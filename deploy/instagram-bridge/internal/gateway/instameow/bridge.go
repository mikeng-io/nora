package instameow

// The Bridge: the one Instagram session, its supervisor, and the operations
// the layers above call on it.
//
// THE RULE THAT SHAPES IT: an Instagram challenge is a normal operating state,
// not a crash. Nothing below exits or panics because Instagram asked for a
// captcha; it records WHICH kind of rejection it was and keeps serving, so
// /health can describe it and a route can answer 409.

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"strconv"
	"sync"
	"time"

	"instagram-bridge/internal/domain"
	"instagram-bridge/internal/session"

	"github.com/rs/zerolog"
	"go.mau.fi/mautrix-meta/pkg/instameow"
	"go.mau.fi/mautrix-meta/pkg/instameow/slidetypes"
	"go.mau.fi/mautrix-meta/pkg/messagix/cookies"
	"go.mau.fi/mautrix-meta/pkg/messagix/httpclient"
	"go.mau.fi/mautrix-meta/pkg/messagix/types"
)

// EventSink is where inbound events go: the SSE hub. It drops rather than
// blocks, because instameow dispatches deltas on the socket's own goroutine.
type EventSink interface {
	Emit(event any)
	HasClients() bool
}

// errNotConnected is the session being (re)established: expected to heal, so
// it is ErrUnavailable, and retryable.
var errNotConnected = domain.Classify(domain.ErrUnavailable,
	errors.New("instagram session is not connected"))

// errSessionRebuilding is a session whose client is being rebuilt: the upload
// leg has nothing to upload through yet. Retryable.
var errSessionRebuilding = errors.New("session is being re-established")

// Options builds a Bridge.
//
// Production sets Store, Sink and the initial State/Detail/Self, and nothing
// else: the supervisor builds the client from the session directory.
// Client and Upload install one already built, which is how every test above
// the socket runs a whole request path with no live account. The account is
// real and in use; "send one and look" is not an available verification.
type Options struct {
	Store session.Store
	Sink  EventSink

	State     domain.SessionState
	Detail    string
	Connected bool
	Seq       int64
	Self      domain.Self

	Client Client
	Upload Uploader
	// Issuer replaces the client's own raw GraphQL issuer. Nil in production.
	Issuer GraphQLIssuer
	// RESTIssuer replaces the client's own raw REST issuer (mentions.go's
	// `rest` helper). Nil in production.
	RESTIssuer RESTIssuer
	// DocIssuer replaces the client's own issuer for the configured GraphQL
	// documents (docs.go). Nil in production.
	DocIssuer DocIssuer
	// Docs is the configured doc_id of each document (docs.go): read from
	// INSTAGRAM_STORY_DOC_ID and INSTAGRAM_TIMELINE_DOC_ID by main. An empty
	// one makes that read answer 501.
	Docs QueryDocs
}

// NewBridge builds the session with no client; Supervise builds one.
func NewBridge(o Options) *Bridge {
	b := &Bridge{
		store:      o.Store,
		sink:       o.Sink,
		issuer:     o.Issuer,
		restIssuer: o.RESTIssuer,
		docIssuer:  o.DocIssuer,
		docs:       o.Docs,
		state:      o.State,
		detail:     o.Detail,
		connected:  o.Connected,
		seq:        o.Seq,
		self:       o.Self,
		out:        o.Client,
		up:         o.Upload,

		summonsSeen: newSeenSet(summonsSeenCap),
		summonsRate: newWindowLimiter(summonsRefetchMax, summonsRefetchWindow, time.Now),
	}
	// The cache outlives any single client: thread ids do not change, and a
	// reconnect that rebuilt the client should not also re-pay a route lookup
	// per room.
	b.threads = newThreadIndex(b.fetchThreadID)
	return b
}

// classifyAuthError maps an instameow error onto the contract's session
// vocabulary.
//
// The distinction is not cosmetic. `checkpointed` means Instagram wants a
// human in a browser and the SAME cookies will work again afterwards;
// `logged_out` means the cookies are dead and a new session must be handed
// over. Collapsing them would tell the operator to do the wrong thing, and
// collapsing either into "error" would make /send retry forever against a
// wall — which is why the contract gives checkpointed its own non-retryable
// 409 rather than a 503.
func classifyAuthError(err error) domain.SessionState {
	switch {
	case err == nil:
		return domain.StateAuthenticated
	case errors.Is(err, httpclient.ErrChallengeRequired),
		errors.Is(err, httpclient.ErrCheckpointRequired),
		errors.Is(err, httpclient.ErrConsentRequired):
		return domain.StateCheckpointed
	case errors.Is(err, httpclient.ErrTokenInvalidated):
		return domain.StateLoggedOut
	default:
		// An unclassified failure is NOT reported as authenticated. Reaching
		// here means the session did something we do not have a name for, and
		// the fail-closed reading is "a human should look", not "carry on".
		return domain.StateCheckpointed
	}
}

const (
	// heartbeatInterval feeds the consumer's `last_event_age_seconds`, which
	// is the REAL socket-death detector. A container healthcheck can only
	// prove the HTTP layer serves; it cannot prove the Instagram socket is
	// alive. Same lesson the signal-cli and whatsapp-bridge compose comments
	// both record.
	heartbeatInterval = 30 * time.Second
	// cookieFlushInterval persists the jar while the process runs, because
	// Instagram rotates values inside a live session.
	cookieFlushInterval = 5 * time.Minute
	// reconnectFloor / reconnectCeiling bound the supervisor's retry after a
	// failed session setup. The ceiling is minutes, not seconds, because the
	// commonest reason setup fails is a checkpoint — which needs a human, and
	// hammering the login endpoint while waiting for one is the obvious way to
	// turn a checkpoint into a ban.
	reconnectFloor   = 5 * time.Second
	reconnectCeiling = 5 * time.Minute
	// stableConnection is how long Connect() must block before the session
	// counts as having actually worked. Connect() has NO return value and
	// returns instantly on a clean close, so without a duration test a socket
	// that closes the moment it opens is indistinguishable from one that ran
	// for a week — and the supervisor would treat both as a success, reset the
	// backoff to five seconds, and re-LoadIndex against Instagram every five
	// seconds forever with the anti-ban ceiling never once engaging. A healthy
	// session sits inside one Connect() call for days, so a minute is a
	// generous floor for "that was real".
	stableConnection = 60 * time.Second
	// sessionPoll is the retry when there is no credential on disk at all.
	// That failure never touches Instagram, so the anti-ban ceiling above does
	// not apply to it — and the documented first-run flow is an operator
	// dropping a file into the volume, which should be picked up in seconds
	// rather than in five minutes.
	sessionPoll = 10 * time.Second
)

// errNoSession distinguishes "we have no credential to try" from "Instagram
// refused the credential we tried". The first is a bootstrap state and is
// polled; the second is an account state and is backed off.
var errNoSession = errors.New("no usable session on disk")

type Bridge struct {
	store   session.Store
	sink    EventSink
	threads *threadIndex
	// issuer, when set, replaces the session client's own raw GraphQL
	// issuer. Production leaves it nil.
	issuer GraphQLIssuer
	// restIssuer, when set, replaces the session client's own raw REST
	// issuer (mentions.go). Production leaves it nil.
	restIssuer RESTIssuer
	// docIssuer, when set, replaces the session client's own issuer for the
	// configured GraphQL documents (docs.go). Production leaves it nil.
	docIssuer DocIssuer
	// docs is set once at construction and never written after.
	docs QueryDocs

	mu  sync.RWMutex
	cli *instameow.Client
	// jar is re-read from disk on every client rebuild, so it is guarded like
	// everything else the supervisor touches: the periodic cookie flush runs
	// on its own goroutine.
	jar *cookies.Cookies
	// out and up are the SEND-facing view of the same session, narrowed to
	// interfaces. They exist so the HTTP layer can be exercised end to end
	// without a live Instagram session: the account is real and in use, so
	// "send one and look" is not an available verification and every path that
	// does not strictly need the network is written to be reachable without it.
	// setClient keeps them in step; nothing else writes them outside tests.
	out       Client
	up        Uploader
	state     domain.SessionState
	detail    string
	connected bool
	seq       int64
	self      domain.Self
	// reindex forces the next supervisor pass to do a full LoadIndex instead
	// of trusting the cached sequence id. Set when Instagram tells us the
	// snapshot is void.
	reindex bool

	// The summons intake (summons.go): where a resolved summons is recorded,
	// the card ids already taken in, and the refetches still running.
	summonsRecorder SummonsRecorder
	summonsSeen     *seenSet
	summonsRate     *windowLimiter
	summonsInflight sync.WaitGroup
	// upstreamGate is the durable-capture readiness gate (UpstreamGate).
	upstreamGate func() bool
}

// currentGate reads the upstream gate under the bridge mutex.
func (b *Bridge) currentGate() func() bool {
	b.mu.RLock()
	defer b.mu.RUnlock()
	return b.upstreamGate
}

// HaltUpstream disconnects the socket without touching the session files:
// durable capture broke mid-flight, so receive stops while HTTP keeps
// serving. The gate keeps the supervisor from rebuilding it.
func (b *Bridge) HaltUpstream() {
	if cli := b.currentClient(); cli != nil {
		go cli.Disconnect()
	}
}

// --- session supervisor -------------------------------------------------------

// supervise owns the Instagram connection for the life of the process.
//
// instameow's Connect() blocks and reconnects internally, so a healthy session
// sits inside one call for days. It RETURNS in three cases and the loop has to
// tell them apart: the context was cancelled (we are shutting down), the
// socket closed cleanly, or Instagram demanded a resnapshot — which voids the
// sequence id and means the next pass must re-read the index rather than
// resume from a number that no longer refers to anything.
func (b *Bridge) supervise(ctx context.Context, libLog zerolog.Logger) {
	backoff := reconnectFloor
	cookieTick := time.NewTicker(cookieFlushInterval)
	defer cookieTick.Stop()
	go func() {
		for {
			select {
			case <-ctx.Done():
				return
			case <-cookieTick.C:
				b.flushCookies()
			}
		}
	}()

	for ctx.Err() == nil {
		if gate := b.currentGate(); gate != nil && !gate() {
			// Durable capture is broken or has never seen a valid projection:
			// upstream receive must not drain events into a void. Poll on the
			// session cadence; capture's own preflight/journal recovery is
			// what flips this back.
			if !sleepOrDone(ctx, sessionPoll) {
				return
			}
			continue
		}
		if err := b.ensureSession(ctx, libLog); err != nil {
			wait := backoff
			if errors.Is(err, errNoSession) {
				wait = sessionPoll
			} else {
				backoff = min(backoff*2, reconnectCeiling)
			}
			slog.WarnContext(ctx, "session setup failed", "state", b.currentState(), "retry_in", wait.String(), "error", err)
			if !sleepOrDone(ctx, wait) {
				return
			}
			continue
		}

		cli := b.currentClient()
		if cli == nil {
			// ensureSession succeeded without leaving a client behind. There
			// is no writer that does this today, but `continue` with no sleep
			// would be a hot loop, so it is backed off like any other failed
			// pass rather than trusted not to happen.
			backoff = min(backoff*2, reconnectCeiling)
			slog.WarnContext(ctx, "session setup reported success with no client", "retry_in", backoff.String())
			if !sleepOrDone(ctx, backoff) {
				return
			}
			continue
		}

		start := time.Now()
		cli.Connect(ctx) // blocks until clean close, cancel, or resnapshot
		lasted := time.Since(start)
		b.setConnected(false)
		if ctx.Err() != nil {
			return
		}
		// Whatever brought us back, the cheap assumption (resume from the
		// cached seq) is no longer safe. Force the next pass to re-read the
		// index; a redundant LoadIndex costs one request, a resumed stream
		// against a void sequence id costs every message until someone
		// notices the room went quiet.
		b.forceReindex()
		// THE BACKOFF IS ON THE DURATION, not on an error, because Connect()
		// does not report one. A socket that ran and then closed resets to the
		// floor; a socket that returned immediately is flapping, and the next
		// pass does a full LoadIndex against Instagram, so the retry rate IS
		// the request rate against a service that bans for exactly this.
		next := nextBackoff(backoff, lasted)
		if next != reconnectFloor || backoff != reconnectFloor {
			slog.InfoContext(ctx, "realtime socket ended", "lasted", lasted.Truncate(time.Millisecond).String(), "retry_in", next.String())
		}
		backoff = next
		if !sleepOrDone(ctx, backoff) {
			return
		}
	}
}

// nextBackoff is the supervisor's retry interval after Connect() returns.
//
// It is a function so that it can be measured without a socket. The rule it
// encodes: a connection that LASTED counts as a success and resets to the
// floor; one that returned quickly is flapping and doubles, up to the anti-ban
// ceiling. Connect() reports no error and returns instantly on a clean close,
// so duration is the only signal there is — and without it a flapping socket
// re-LoadIndexes against Instagram every five seconds indefinitely.
func nextBackoff(current, lasted time.Duration) time.Duration {
	if lasted >= stableConnection {
		return reconnectFloor
	}
	return min(max(current, reconnectFloor)*2, reconnectCeiling)
}

// sleepOrDone waits, and reports false when the process is shutting down
// instead. Every wait in the supervisor goes through it so that none of them
// can be the one that ignores a SIGTERM.
func sleepOrDone(ctx context.Context, d time.Duration) bool {
	timer := time.NewTimer(d)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-timer.C:
		return true
	}
}

// ensureSession brings the client to the point where Connect() can be called:
// authenticated, with a socket, and with a sequence id to resume from.
func (b *Bridge) ensureSession(ctx context.Context, libLog zerolog.Logger) error {
	cli := b.currentClient()
	if cli == nil {
		// Re-read the credential from disk on every rebuild. The jar in memory
		// is the one that just failed; the one on disk may be a session the
		// operator dropped in thirty seconds ago. Reusing the in-memory copy
		// would mean a cleared checkpoint is only noticed at the next restart.
		jar, err := session.LoadCookieJar(b.store)
		if err != nil {
			b.setState(domain.StateLoggedOut, fmt.Sprintf("no usable session: %v", err))
			return fmt.Errorf("%w: %w", errNoSession, err)
		}
		if missing := jar.GetMissingCookieNames(); len(missing) > 0 {
			// sessionid / csrftoken / ds_user_id. A partial jar fails later
			// with an opaque auth error, so it is refused here with the names.
			b.setState(domain.StateLoggedOut, fmt.Sprintf("missing cookies: %v", missing))
			return fmt.Errorf("%w: missing cookies %v", errNoSession, missing)
		}
		b.setJar(jar)

		seq, seqTS := b.store.LoadSeq()
		cli = instameow.NewClient(instameow.ClientParams{
			Cookies: jar,
			Log:     libLog,
			// The sequence id is passed HERE and not restored by LoadState:
			// instameow's DumpState carries the HTTP configs and nothing else,
			// so a bridge that persisted only state.json would report
			// HasSeqID() == false on every boot and re-snapshot every time.
			SeqID:        seq,
			SeqIDTS:      seqTS,
			EventHandler: b.onEvent,
			// Typing stays ENABLED: /typing is part of the contract, and the
			// same flag also gates the stream-controller socket that delivers
			// other people's typing notifications inbound.
		})
		if state, ok := b.store.LoadState(); ok {
			if err := cli.LoadState(state); err != nil {
				// instameow refuses state older than 24h (ErrCachedStateTooOld).
				// That is not an error condition — it is the documented signal
				// to do a full LoadIndex — so it is logged and stepped over.
				slog.WarnContext(ctx, "cached state not usable, will re-read index", "error", err)
			} else {
				slog.InfoContext(ctx, "loaded reconnection state", "seq", seq)
			}
		}
		b.setClient(cli)
	}

	if !b.needsReindex() && cli.HasSeqID() && cli.IsAuthenticated() {
		b.setState(domain.StateAuthenticated, "resumed from cached state")
		return nil
	}

	loadCtx, cancel := context.WithTimeout(ctx, 60*time.Second)
	defer cancel()
	type index struct {
		viewer  *types.PolarisViewer
		mailbox *slidetypes.Mailbox
	}
	loaded, err := call(loadCtx, "instagram.LoadIndex", nil, func(ctx context.Context) (index, error) {
		v, m, err := cli.LoadIndex(ctx)
		return index{v, m}, err
	})
	viewer, mailbox := loaded.viewer, loaded.mailbox
	if err != nil {
		state := classifyAuthError(err)
		b.setState(state, err.Error())
		// A checkpoint needs a human. Tear the client down and throw it away so
		// the next attempt rebuilds from whatever the operator may have dropped
		// into the session dir in the meantime, rather than retrying forever
		// against an object that already knows it is rejected. The supervisor's
		// backoff climbs to five minutes, because hammering the login endpoint
		// while waiting for a human is the obvious way to turn a checkpoint
		// into a ban.
		go cli.Disconnect()
		b.setClient(nil)
		return err
	}

	// THE THREE ID SPACES. GetOwnFBID() is `interop_messaging_user_fbid` —
	// the id a mention carries, the only one "was I tagged?" may compare
	// against. viewer.GetFBID() is a DIFFERENT number for this same account
	// (measured: 15553308125534842 vs 18247361241432477) and matches no
	// mention, anywhere, ever — and choosing it produces no error at all,
	// just an account that never notices it was tagged. viewer.GetUserId()
	// is the public igid, which the Phase 2 social calls take.
	b.recordSelf(cli.GetOwnFBID(), viewer.GetUserId(), viewer.GetUsername())
	if mailbox != nil {
		b.setSeq(mailbox.UQSeqID)
		if err := b.store.SaveSeq(mailbox.UQSeqID, time.Now()); err != nil {
			slog.WarnContext(ctx, "could not persist seq id", "error", err)
		}
	}
	b.clearReindex()
	b.setState(domain.StateAuthenticated, "")
	b.flushCookies()
	self := b.selfSnapshot()
	slog.InfoContext(ctx, "authenticated", "username", self.Username, "fbid", self.FBID, "igid", self.IGID)
	return nil
}

// onEvent is instameow's single callback for everything the session does.
// Nothing here may block: every branch either mutates a mutex-guarded field or
// hands a serialized event to the hub, which drops rather than blocks.
//
// It is NOT single-goroutine. Deltas and Connected/Disconnected arrive on the
// realtime socket's loop, TypingNotification on the stream-controller socket's
// — a second connection with a second loop — and AuthError is dispatched from
// whichever HTTP request goroutine got the rejection. Two branches can
// therefore run at once, which is why every write below goes through the
// bridge mutex and why writeAtomic may not use a shared temp name.
func (b *Bridge) onEvent(ctx context.Context, evt slidetypes.ClientEvent) error {
	switch e := evt.(type) {
	case *slidetypes.Connected:
		b.setConnected(true)
		b.setSeq(e.LatestSeqID)
		slog.InfoContext(ctx, "realtime socket connected", "subscribed_seq", e.SubscribedSeqID, "latest_seq", e.LatestSeqID)

	case *slidetypes.Disconnected:
		b.setConnected(false)
		detail := "socket disconnected"
		if e.Error != nil {
			detail = e.Error.Error()
		}
		slog.WarnContext(ctx, "socket disconnected", "failures", e.FailureCount, "detail", detail)

	case *slidetypes.AuthError:
		// The single most important branch in the file. See sessionRejected's
		// own doc comment for why this exists and why the handback is not
		// optional.
		b.sessionRejected(ctx, e.Error)

	case *slidetypes.SeqIDUpdate:
		b.setSeq(e.SeqID)
		if err := b.store.SaveSeq(e.SeqID, e.Timestamp); err != nil {
			slog.WarnContext(ctx, "could not persist seq id", "error", err)
		}

	case *slidetypes.ReconnectionStateUpdate:
		// ~6 KB, written atomically on every change. This is what makes a
		// restart a resume instead of a fresh login, and a fresh login is
		// exactly the event that provokes a trust check.
		if err := b.store.SaveState(e.State); err != nil {
			slog.WarnContext(ctx, "could not persist reconnection state", "error", err)
		}

	case *slidetypes.ResnapshotRequired:
		// Connect() returns right after dispatching this, so the supervisor
		// regains control on its own; all this branch owes is the invalidation
		// that stops the NEXT pass from trusting the cached sequence id — and
		// that has to reach the disk, not just the in-memory flag. See
		// invalidateSeq.
		slog.InfoContext(ctx, "resnapshot required, will re-read index")
		b.invalidateSeq()

	case *slidetypes.TypingNotification:
		b.emitTyping(e)

	case *slidetypes.Delta:
		b.emitDelta(e)
	}
	return nil
}

// sessionRejected records Instagram asking for a human (or suspending the
// account) and hands control back to the supervisor.
//
// It is the socket's AuthError branch, extracted: record WHICH kind of
// rejection, keep serving, and let the supervisor rebuild. The handback is
// not optional. Connect() blocks and reconnects on its own, so without it the
// socket would sit there retrying while the bridge reported `checkpointed`
// FOREVER — including long after the operator cleared the challenge, because
// nothing would ever call LoadIndex again to discover that it had been
// cleared. A state that cannot recover on its own is a state somebody has to
// notice, and the whole point of reporting it as a field is that nobody has
// to.
//
// It is not only the socket's. The library's own checkpoint detection
// (`(*instameow.Client).checkResponseError`, client.go) fires ONLY inside the
// typed methods that call it (FetchMedia, FetchReel, ...) — never for a raw
// GraphQL document (social_profile.go goes through
// `httpclient.MakeGraphQLRequest`, which does not) and never for a raw REST
// request this bridge
// issues itself (mentions.go's `rest` helper), because a raw `MakeRequest`
// failure comes back as a bare `ErrUnexpectedError` with no idea what the
// body said. `rest` reads that body itself (call.go's `restStatusError`) and
// classifies the result `ErrNeedsHuman`, but classifying an error is not the
// same as recording that the SESSION was rejected — nothing upstream of a
// raw REST call ever saw a `slidetypes.AuthError` to react to. Any caller
// that gets `needsHuman` or `ErrAccountSuspended` off a call outside the
// socket must run this too (Bridge.Mentions does).
//
// Disconnect must run on another goroutine: called from the socket loop it
// would wait for itself to stop; called from a request handler it must not
// block the response on a teardown that can take a moment.
func (b *Bridge) sessionRejected(ctx context.Context, err error) {
	state := classifyAuthError(err)
	detail := ""
	if err != nil {
		detail = err.Error()
	}
	b.setState(state, detail)
	b.invalidateSeq()
	slog.ErrorContext(ctx, "session rejected by instagram", "state", state, "detail", detail)
	if cli := b.currentClient(); cli != nil {
		go cli.Disconnect()
	}
}

// emitDelta maps one delta onto the SSE stream. Deltas whose type carries no
// meaning for Nora (read receipts, folder moves, mute changes, thread pins)
// fall through and emit nothing: forwarding them would age
// `last_event_age_seconds` on traffic that is not conversation, which is the
// one thing that detector must not be fooled by.
func (b *Bridge) emitDelta(d *slidetypes.Delta) {
	room := d.ThreadIGID // JSON tag `thread_fbid`: the SHORT id, despite the name
	own := b.ownFBID()
	switch data := d.Data.(type) {
	case *slidetypes.NewMessageEvent:
		if ev := normalizeMessage(room, data.Message, own); ev != nil {
			b.sink.Emit(ev)
		}
		// A summons card is ALSO a summons: the ordinary message event above
		// goes out as it always did, and the intake (summons.go) adds its
		// own event once the card is resolved to a post.
		b.detectSummons(room, data.Message)
	case *slidetypes.AdminMessageEvent:
		// GROUP_THREAD_CREATED_XMAT, ADD_PARTICIPANT_XMAT and friends arrive
		// as ordinary messages attributed to whoever acted. They are emitted
		// as messages with their content_type intact, which is how a roster
		// change reaches the adapter at all.
		if ev := normalizeMessage(room, data.Message, own); ev != nil {
			b.sink.Emit(ev)
		}
	case *slidetypes.ParticipantJoinEvent:
		if ev := normalizeMessage(room, data.Message, own); ev != nil {
			b.sink.Emit(ev)
		}
	case *slidetypes.ParticipantLeaveEvent:
		if ev := normalizeMessage(room, data.Message, own); ev != nil {
			b.sink.Emit(ev)
		}
	case *slidetypes.CreateReactionEvent:
		b.sink.Emit(normalizeReaction(room, data.Reaction, data.MessageID, false, own, d.UQSeqID))
	case *slidetypes.DeleteReactionEvent:
		b.sink.Emit(normalizeReaction(room, data.Reaction, data.MessageID, true, own, d.UQSeqID))
	case *slidetypes.EditMessageEvent:
		// from_me is left nil: the delta carries no sender. See editEvent.
		ev := &editEvent{Kind: kindEdit, RoomID: room, MessageID: data.MessageID, Text: data.TextBody}
		if !data.SlideEditHistoryEntry.TimestampMS.IsZero() {
			ev.TimestampMS = data.SlideEditHistoryEntry.TimestampMS.UnixMilli()
		}
		b.sink.Emit(ev)
	case *slidetypes.DeleteMessageEvent:
		b.sink.Emit(&unsendEvent{Kind: kindUnsend, RoomID: room, MessageID: data.MessageID})
	}
}

func (b *Bridge) emitTyping(t *slidetypes.TypingNotification) {
	ev := &typingEvent{
		Kind:     kindTyping,
		ThreadID: t.ThreadID,
		Typing:   t.ActivityStatus != 0,
	}
	if t.SenderID != 0 {
		ev.SenderFBID = strconv.FormatInt(t.SenderID, 10)
	}
	ev.FromMe = fromMe(t.SenderID, b.ownFBID())
	// room_id is filled ONLY when the cache can vouch for the id space. See
	// typingEvent's doc comment: guessing here would put a long id in a field
	// the contract defines as the short one, and every consumer keying on
	// room_id would miss without ever erroring.
	if room, ok := b.threads.roomFor(t.ThreadID); ok {
		ev.RoomID = room
	}
	b.sink.Emit(ev)
}

func (b *Bridge) heartbeat(ctx context.Context) {
	ticker := time.NewTicker(heartbeatInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			if !b.sink.HasClients() {
				continue
			}
			state, _, connected, _ := b.snapshot()
			b.sink.Emit(&heartbeatEvent{
				Kind: kindHeartbeat, TSMS: time.Now().UnixMilli(),
				Connected: connected, Session: state,
			})
		}
	}
}

func (b *Bridge) setJar(jar *cookies.Cookies) {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.jar = jar
}

func (b *Bridge) currentJar() *cookies.Cookies {
	b.mu.RLock()
	defer b.mu.RUnlock()
	return b.jar
}

func (b *Bridge) flushCookies() {
	jar := b.currentJar()
	if jar == nil {
		return
	}
	if err := b.store.SaveCookies(jar); err != nil {
		// Expected and harmless while logged out; noisy only if it persists.
		slog.Warn("cookie flush skipped", "error", err)
	}
}

// --- guarded state ------------------------------------------------------------

func (b *Bridge) setClient(cli *instameow.Client) {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.cli = cli
	if cli == nil {
		// Assigning a typed nil pointer to an interface yields a NON-nil
		// interface, so this branch is not redundant: without it, `out != nil`
		// stays true after the client is torn down and every send panics
		// instead of answering 503.
		b.out, b.up = nil, nil
		return
	}
	b.out = cli
	b.up = cli.GetHTTP().SendMercuryUploadRequest
}

// fetchThreadID resolves a short thread id through the HANDLERS' client, the
// traced one, so a cache miss during a request is a span under that request.
// It used to call the raw session client directly, which left every cold
// room's first send, reaction or roster read with an untraced Instagram round
// trip inside it.
func (b *Bridge) fetchThreadID(ctx context.Context, shortID int64) (*instameow.ThreadIGIDs, error) {
	cli := b.sendClient()
	if cli == nil {
		return nil, errNotConnected
	}
	return lookupThread(ctx, cli, shortID)
}

func (b *Bridge) currentClient() *instameow.Client {
	b.mu.RLock()
	defer b.mu.RUnlock()
	return b.cli
}

// sendClient and uploadFn are what the HTTP handlers reach for. They are
// narrow on purpose: a handler that could see *instameow.Client could also
// call LoadIndex or Disconnect from inside a request.
func (b *Bridge) sendClient() Client {
	b.mu.RLock()
	defer b.mu.RUnlock()
	return b.out
}

func (b *Bridge) uploadFn() Uploader {
	b.mu.RLock()
	defer b.mu.RUnlock()
	return b.up
}

func (b *Bridge) setState(state domain.SessionState, detail string) {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.state = state
	b.detail = detail
}

func (b *Bridge) currentState() domain.SessionState {
	b.mu.RLock()
	defer b.mu.RUnlock()
	return b.state
}

func (b *Bridge) setConnected(v bool) {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.connected = v
}

func (b *Bridge) setSeq(seq int64) {
	if seq == 0 {
		return
	}
	b.mu.Lock()
	defer b.mu.Unlock()
	b.seq = seq
}

// recordSelf is the identity half of a successful LoadIndex, and it FAILS
// CLOSED on a zero fbid.
//
// GetOwnFBID() returns 0 when the browser config table did not carry
// `interop_messaging_user_fbid`, or carried one whose viewer fbid disagrees
// with it — a real outcome of a partially-served page, not a hypothetical.
// Persisting the fbid-less identity anyway OVERWRITES a good self.json with
// one loadSelf will refuse, and from then on `from_me` is false for every
// echo, R-12's self-tag detection matches nothing, and /react cannot verify
// its own read-back. There is no error, no counter and no field on /health
// that looks wrong: the only symptom is an account that stops noticing it was
// spoken to. So the cached identity is kept, the file is not rewritten, and
// the log says so loudly enough to be found.
//
// It is a method rather than four lines inside ensureSession because
// ensureSession needs a live LoadIndex and cannot be exercised without the
// account, and a branch nothing can reach is a branch nothing measures.
func (b *Bridge) recordSelf(fbid int64, igid, username string) {
	self := domain.Self{IGID: igid, Username: username}
	if fbid != 0 {
		self.FBID = strconv.FormatInt(fbid, 10)
		b.setSelf(self)
		if err := b.store.SaveSelf(self); err != nil {
			slog.Warn("could not cache own identity", "error", err)
		}
		return
	}
	cached := b.selfSnapshot()
	slog.Error("GetOwnFBID() returned 0: KEEPING the cached identity and NOT overwriting "+
		"self.json. from_me, self-tag detection and the /react read-back all bind on this value.",
		"username", username, "cached_fbid", cached.FBID)
	if cached.FBID == "" {
		// Nothing to protect. Record what LoadIndex did give us so /health can
		// report the username, but leave the file alone: an identity with no
		// fbid is not an identity, and loadSelf refuses one anyway.
		b.setSelf(self)
	}
}

func (b *Bridge) setSelf(self domain.Self) {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.self = self
}

func (b *Bridge) ownFBID() int64 {
	b.mu.RLock()
	defer b.mu.RUnlock()
	fbid, _ := strconv.ParseInt(b.self.FBID, 10, 64)
	return fbid
}

func (b *Bridge) forceReindex() {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.reindex = true
}

// invalidateSeq is forceReindex plus the durable half.
//
// The in-memory flag only binds THIS process. Instagram voiding the snapshot
// also voids the number in seq.json, and ensureSession's resume shortcut reads
// that file: a restart in the window between the invalidation and the next
// successful LoadIndex would find HasSeqID() true, skip LoadIndex, and
// subscribe from a sequence id that no longer refers to anything. Nothing
// errors; messages simply stop arriving. So the file goes too.
func (b *Bridge) invalidateSeq() {
	b.forceReindex()
	if err := b.store.ClearSeq(); err != nil {
		slog.Error("could not clear the stale seq id (a restart may resume from it)", "error", err)
	}
	b.mu.Lock()
	b.seq = 0
	b.mu.Unlock()
}

func (b *Bridge) clearReindex() {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.reindex = false
}

func (b *Bridge) needsReindex() bool {
	b.mu.RLock()
	defer b.mu.RUnlock()
	return b.reindex
}

func (b *Bridge) snapshot() (domain.SessionState, string, bool, int64) {
	b.mu.RLock()
	defer b.mu.RUnlock()
	return b.state, b.detail, b.connected, b.seq
}

func (b *Bridge) selfSnapshot() domain.Self {
	b.mu.RLock()
	defer b.mu.RUnlock()
	return b.self
}
