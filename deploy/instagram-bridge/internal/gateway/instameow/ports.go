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
// read instead of losing it.
func (b *Bridge) SocialProfile(ctx context.Context, igid domain.IGID) domain.SocialRead {
	if b.issuer != nil {
		return readSocial(ctx, b.issuer, nil, igid)
	}
	cli, err := b.client()
	if err != nil {
		return domain.SocialRead{Err: err}
	}
	issuer, err := issuerFor(cli)
	return readSocial(ctx, issuer, err, igid)
}

// Story reads one account's current story. ONE igid, never a batch: a batch
// would open stories nobody asked about (whether the author sees the view is
// unmeasured, design 04, 05 §Reading a story).
func (b *Bridge) Story(ctx context.Context, igid domain.IGID) (domain.Story, error) {
	cli, err := b.client()
	if err != nil {
		return domain.Story{}, err
	}
	return readStory(ctx, cli, igid)
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
	self := b.selfSnapshot().Username
	var (
		mentions []domain.CommentMention
		skipped  map[int]int
		err      error
	)
	if b.restIssuer != nil {
		mentions, skipped, err = readMentions(ctx, b.restIssuer, self)
	} else {
		cli, cliErr := b.client()
		if cliErr != nil {
			return nil, nil, cliErr
		}
		issuer, issuerErr := restIssuerFor(cli)
		if issuerErr != nil {
			return nil, nil, issuerErr
		}
		mentions, skipped, err = readMentions(ctx, issuer, self)
	}
	if err != nil && (needsHuman(err) || errors.Is(err, httpclient.ErrAccountSuspended)) {
		b.sessionRejected(ctx, err)
	}
	return mentions, skipped, err
}
