package instameow

// The operations the layers above call. Each takes domain values and returns
// domain values or a classified error; none of them hands back a wire struct.
//
// Which thread id a verb takes is a per-verb fact (outbound.go's table), and
// it is decided HERE, from a ThreadRef that carries both: /unsend is long,
// everything else is short. The long id never leaves this package.

import (
	"context"
	"errors"

	"instagram-bridge/internal/domain"

	"github.com/rs/zerolog"
	"go.mau.fi/mautrix-meta/pkg/messagix/httpclient"
)

// Snapshot is the session at one instant.
func (b *Bridge) Snapshot() domain.Session {
	b.mu.RLock()
	defer b.mu.RUnlock()
	return domain.Session{
		State:     b.state,
		Detail:    b.detail,
		Connected: b.connected,
		Seq:       b.seq,
		Self:      b.self,
		HasClient: b.out != nil,
	}
}

// Supervise owns the Instagram connection for the life of the process.
func (b *Bridge) Supervise(ctx context.Context, libLog zerolog.Logger) { b.supervise(ctx, libLog) }

// UpstreamGate gates upstream receive on durable capture being ready: the
// supervisor consults it before every (re)connect pass and polls while it
// says no. Nil means ungated (the compatibility path with no durable wiring).
func (b *Bridge) UpstreamGate(gate func() bool) {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.upstreamGate = gate
}

// Heartbeat emits the SSE heartbeat while a consumer is attached.
func (b *Bridge) Heartbeat(ctx context.Context) { b.heartbeat(ctx) }

// Shutdown closes the socket and persists the cookie jar.
func (b *Bridge) Shutdown() {
	if cli := b.currentClient(); cli != nil {
		cli.Disconnect()
	}
	b.flushCookies()
}

// client is the request paths' client, or errNotConnected while the
// supervisor is (re)building one.
func (b *Bridge) client() (Client, error) {
	cli := b.sendClient()
	if cli == nil {
		return nil, errNotConnected
	}
	return cli, nil
}

// ResolveThread maps a room key to both thread ids, fetching on a miss. The
// lookup is also what validates that the room exists and what warms the
// reverse cache the typing path reads.
func (b *Bridge) ResolveThread(ctx context.Context, short domain.ThreadFBID) (domain.ThreadRef, error) {
	return b.threads.resolve(ctx, short)
}

// SendText posts a text message (SHORT id).
func (b *Bridge) SendText(
	ctx context.Context, thread domain.ThreadRef, text string, mentions []domain.Mention, replyTo string,
) (domain.Sent, error) {
	cli, err := b.client()
	if err != nil {
		return domain.Sent{}, err
	}
	return sendText(ctx, cli, thread.Short.String(), text, mentions, replyTo)
}

// SendMedia uploads and sends one attachment (SHORT id, both legs).
func (b *Bridge) SendMedia(
	ctx context.Context, thread domain.ThreadRef, media domain.Upload, replyTo string,
) (domain.Sent, error) {
	cli, err := b.client()
	if err != nil {
		return domain.Sent{}, err
	}
	up := b.uploadFn()
	if up == nil {
		return domain.Sent{}, domain.Classify(domain.ErrUnavailable, errSessionRebuilding)
	}
	return sendMedia(ctx, cli, up, thread.Short.Int64(), &httpclient.MercuryUploadMedia{
		Filename:    media.Filename,
		MimeType:    media.MimeType,
		MediaData:   media.Data,
		IsVoiceClip: media.VoiceNote,
	}, replyTo)
}

// React posts or retracts a reaction and reads back whether it landed.
//
// SHORT id — the one verb where that is load-bearing: a reaction addressed
// with the long id is accepted and absent from the thread (measured
// 2026-09-22). `confirmed` false is a degraded success, not a failure: the
// mutation was accepted and only this response's post-state stayed silent.
func (b *Bridge) React(
	ctx context.Context, thread domain.ThreadRef, messageID, emoji string, remove bool,
) (confirmed bool, err error) {
	cli, err := b.client()
	if err != nil {
		return false, err
	}
	err = sendReaction(ctx, cli, thread.Short.String(), messageID, emoji, remove, b.ownFBID())
	if errors.Is(err, errReactionUnconfirmed) {
		return false, nil
	}
	return err == nil, err
}

// Typing publishes the typing indicator (SHORT id).
func (b *Bridge) Typing(ctx context.Context, thread domain.ThreadRef, typing bool) error {
	cli, err := b.client()
	if err != nil {
		return err
	}
	return setTyping(ctx, cli, thread.Short.String(), typing)
}

// Unsend retracts a message (LONG id), reporting rather than deciding what
// `direct_unsend_message` said.
func (b *Bridge) Unsend(ctx context.Context, thread domain.ThreadRef, messageID string) (confirmed bool, err error) {
	cli, err := b.client()
	if err != nil {
		return false, err
	}
	return unsendMessage(ctx, cli, thread.Long.String(), messageID)
}

// Members reads one thread's membership, never short and never empty, with
// the bridge's own account dropped if the wire ever includes it.
func (b *Bridge) Members(ctx context.Context, thread domain.ThreadRef) (domain.Thread, error) {
	cli, err := b.client()
	if err != nil {
		return domain.Thread{}, err
	}
	return readRoster(ctx, cli, thread.Short.String(), b.ownFBID())
}

// Account reads one account through GetProfile.
func (b *Bridge) Account(ctx context.Context, igid domain.IGID) (domain.Account, error) {
	cli, err := b.client()
	if err != nil {
		return domain.Account{}, err
	}
	return readProfile(ctx, cli, igid)
}

// SocialProfile is the second, independent read behind an account. It never
// returns an error: its outcome is carried, so a failure degrades the account
// read instead of losing it. A raw GraphQL call goes through none of the
// library's own checkpoint detection, so a needs-human answer goes through
// sessionRejected here, as on Mentions (issue #533).
func (b *Bridge) SocialProfile(ctx context.Context, igid domain.IGID) domain.SocialRead {
	issuer, issuerErr := b.rawGraphQL()
	read := readSocial(ctx, issuer, issuerErr, igid)
	if read.Sent {
		b.checkRejected(ctx, read.Err)
	}
	return read
}

// rawGraphQL is the GraphQL issuer a raw document goes through: the injected
// one in a test, otherwise the live session client's.
func (b *Bridge) rawGraphQL() (GraphQLIssuer, error) {
	if b.issuer != nil {
		return b.issuer, nil
	}
	cli, err := b.client()
	if err != nil {
		return nil, err
	}
	return issuerFor(cli)
}

// Story reads one account's current story over the web app's story
// document (social.go). ONE igid, never a batch: a batch would open stories
// nobody asked about. A checkpoint goes through sessionRejected, as on
// SocialProfile; a stale document does not (the login is fine).
func (b *Bridge) Story(ctx context.Context, igid domain.IGID) (domain.Story, error) {
	if err := b.docs.Problem(); err != nil {
		return domain.Story{}, err
	}
	if b.docs.Story == "" {
		return domain.Story{}, storyDoc("").unconfigured()
	}
	issuer, err := b.rawDocs()
	if err != nil {
		return domain.Story{}, err
	}
	story, err := readStory(ctx, issuer, b.docs.Story, igid)
	b.checkRejected(ctx, err)
	return story, err
}

// Items reads one page of an account's posts and reels over the web app's
// timeline document (timeline.go). The document takes a USERNAME. A caller
// that already holds it (a back-fill paging on) passes it, and the page costs
// one upstream call; otherwise the account is read first through
// readProfile, the lookup `GET /accounts/{igid}` makes, and its igid must be
// the one asked about. Either way every item's owner is checked against the
// igid, which is what makes a caller-supplied username safe to trust. No
// call at all when the document is not configured. `after` is the previous
// page's cursor, "" for the newest page.
func (b *Bridge) Items(
	ctx context.Context, igid domain.IGID, username domain.Username, after string,
) (domain.TimelinePage, error) {
	if err := b.docs.Problem(); err != nil {
		return domain.TimelinePage{}, err
	}
	if b.docs.Timeline == "" {
		return domain.TimelinePage{}, timelineDoc("").unconfigured()
	}
	issuer, err := b.rawDocs()
	if err != nil {
		return domain.TimelinePage{}, err
	}
	if username.String() == "" {
		cli, err := b.client()
		if err != nil {
			return domain.TimelinePage{}, err
		}
		acct, err := readProfile(ctx, cli, igid)
		if err != nil {
			return domain.TimelinePage{}, err
		}
		if acct.IGID != igid.String() {
			// The lookup answered about another account: its username would
			// page somebody else's timeline.
			return domain.TimelinePage{}, errOtherAccount
		}
		username, err = domain.ParseUsername(acct.Username)
		if err != nil {
			// Fixed text: the parser's own quotes the value.
			return domain.TimelinePage{}, errNoUsername
		}
	}
	page, err := readTimeline(ctx, issuer, b.docs.Timeline, igid, username, after)
	b.checkRejected(ctx, err)
	return page, err
}

// errOtherAccount is a profile read that answered about a different igid
// than the one the timeline was asked for.
var errOtherAccount = domain.Classify(domain.ErrUpstreamRejected,
	errors.New("profile read answered about a different account"))

// errNoUsername is a profile read whose username is not one: the timeline
// document is keyed on it, so there is nothing to send.
var errNoUsername = domain.Classify(domain.ErrUpstreamRejected,
	errors.New("profile read returned no usable username"))

// rawDocs is the issuer the configured documents go through: the injected
// one in a test, otherwise the live session client's. The client is checked
// FIRST either way, so a session being rebuilt answers 503 and sends nothing,
// in a test as in production.
func (b *Bridge) rawDocs() (DocIssuer, error) {
	cli, err := b.client()
	if err != nil {
		return nil, err
	}
	if b.docIssuer != nil {
		return b.docIssuer, nil
	}
	return docIssuerFor(cli)
}

// SearchAccounts searches Instagram accounts by free text, in the library's
// own order (16 §Resources, 17 P9, P11).
func (b *Bridge) SearchAccounts(ctx context.Context, query string) ([]domain.AccountSummary, error) {
	cli, err := b.client()
	if err != nil {
		return nil, err
	}
	return searchAccounts(ctx, cli, query)
}

// Post reads one post by its shortcode, every carousel item in order
// (17 P12). See posts.go.
func (b *Bridge) Post(ctx context.Context, code domain.Shortcode) (domain.Post, error) {
	cli, err := b.client()
	if err != nil {
		return domain.Post{}, err
	}
	return readPost(ctx, cli, code)
}

// Mentions reads comment-mentions from Instagram's activity/notification
// feed (`GET /mentions`, UNMEASURED; see mentions.go). skipped counts every
// story this call did NOT classify as a mention, keyed by its story_type, so
// the live probe can pin the numeric type a real comment-mention arrives as.
//
// A raw REST call goes through none of the library's own checkpoint
// detection (sessionRejected's doc comment explains why), so this is the one
// place that has to run it itself: a needsHuman or account-suspended answer
// flips the session state the same way the socket's own AuthError branch
// does, so /health and the next request both see it.
func (b *Bridge) Mentions(ctx context.Context) ([]domain.CommentMention, map[int]int, error) {
	self := b.selfSnapshot()
	issuer, err := b.rawREST()
	if err != nil {
		return nil, nil, err
	}
	mentions, skipped, err := readMentions(ctx, issuer, self)
	b.checkRejected(ctx, err)
	return mentions, skipped, err
}

// Comments reads one page of a post's comments, or one comment's replies
// (design 19 phase 2; comments.go). A checkpoint goes through
// sessionRejected, exactly as on Mentions (design 19 §Account safety, rule
// 4: "by every route this design adds").
func (b *Bridge) Comments(
	ctx context.Context, code domain.Shortcode, media domain.MediaID, parent *domain.CommentID,
) (domain.CommentPage, error) {
	issuer, err := b.rawREST()
	if err != nil {
		return domain.CommentPage{}, err
	}
	page, err := readComments(ctx, issuer, code, media, parent)
	b.checkRejected(ctx, err)
	return page, err
}

// PostComment writes one reply under a summons comment: the bridge's only
// public write (design 19 phase 4; comments.go). Every guard runs BEFORE this,
// in the app layer; this is the call itself. It runs on a context detached
// from the request's cancellation, bounded by commentWriteTimeout, so a caller
// hanging up cannot turn a clean answer into an ambiguous one.
func (b *Bridge) PostComment(
	ctx context.Context, code domain.Shortcode, media domain.MediaID, replyTo domain.CommentID, text string,
) (domain.PostedComment, error) {
	issuer, err := b.rawREST()
	if err != nil {
		return domain.PostedComment{}, err
	}
	ctx, cancel := context.WithTimeout(context.WithoutCancel(ctx), commentWriteTimeout)
	defer cancel()
	posted, err := postComment(ctx, issuer, code, media, replyTo, text)
	b.checkRejected(ctx, err)
	return posted, err
}

// rawREST is the REST issuer a raw call goes through: the injected one in a
// test, otherwise the live session client's.
func (b *Bridge) rawREST() (RESTIssuer, error) {
	if b.restIssuer != nil {
		return b.restIssuer, nil
	}
	cli, err := b.client()
	if err != nil {
		return nil, err
	}
	return restIssuerFor(cli)
}

// checkRejected runs sessionRejected for a needs-human or account-suspended
// answer off a raw call (see sessionRejected's doc comment for why a raw
// call has to do this itself).
func (b *Bridge) checkRejected(ctx context.Context, err error) {
	if err != nil && (needsHuman(err) || errors.Is(err, httpclient.ErrAccountSuspended)) {
		b.sessionRejected(ctx, err)
	}
}
