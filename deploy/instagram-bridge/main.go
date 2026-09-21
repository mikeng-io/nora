// instagram-bridge — Nora's Instagram transport.
//
// A Go daemon that owns the Instagram wire (go.mau.fi/mautrix-meta's
// `instameow`, used standalone: no Matrix homeserver, no appservice, no
// bridgev2 runtime) and exposes a small HTTP + SSE surface to the Nora Python
// process. The wire contract is pinned in
// design/instagram-adoption/03-transport-contract.md — every route, every
// field, the status mapping and the four inbound media shapes are defined
// there, not here.
//
// It is the third instance of a settled pattern: signal-cli, then
// whatsapp-bridge, now this. Same posture as both — private `nora` network,
// publishes NOTHING, holds the full account credential so that the narrower
// its network namespace, the smaller the blast radius.
//
// The two things that differ from whatsapp-bridge:
//
//   - Go rather than Node, because the only usable Instagram client is Go.
//   - A COOKIE session rather than a QR pairing. A password login originating
//     from a server is the highest-risk first contact an account can make, and
//     instameow's native login explicitly cannot drive a CAPTCHA. The operator
//     warms the account in a browser on the normal residential connection and
//     hands the bridge the resulting session.
//
// THE RULE THAT SHAPES THE WHOLE PROGRAM: an Instagram challenge is a normal
// operating state, not a crash. /health answers 200 while the HTTP server
// serves and reports the session as a FIELD. Nothing below exits non-zero
// because Instagram asked for a captcha — if it did, a routine event would
// present as a dead container and the operator would debug the wrong thing.
//
// Usage:
//
//	instagram-bridge --port 18082 --session /session
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"sync"
	"syscall"
	"time"

	"github.com/rs/zerolog"
	"go.mau.fi/mautrix-meta/pkg/instameow"
	"go.mau.fi/mautrix-meta/pkg/instameow/slidetypes"
	"go.mau.fi/mautrix-meta/pkg/messagix/cookies"
)

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

type bridge struct {
	store     store
	hub       *hub
	startedAt time.Time
	threads   *threadIndex

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
	out       igClient
	up        uploader
	state     sessionState
	detail    string
	connected bool
	seq       int64
	self      selfIdentity
	// reindex forces the next supervisor pass to do a full LoadIndex instead
	// of trusting the cached sequence id. Set when Instagram tells us the
	// snapshot is void.
	reindex bool
}

func main() {
	var (
		port       = flag.Int("port", envInt("IG_BRIDGE_PORT", 18082), "HTTP port (private nora network only)")
		sessionDir = flag.String("session", envStr("IG_SESSION_DIR", "/session"), "directory holding cookies + reconnection state")
		verbose    = flag.Bool("v", false, "log the protocol client's own output")
	)
	flag.Parse()

	log.SetFlags(log.LstdFlags | log.LUTC)
	log.SetPrefix("[bridge] ")

	if err := os.MkdirAll(*sessionDir, 0o700); err != nil {
		log.Printf("FATAL cannot create session dir %s: %v", *sessionDir, err)
		os.Exit(2)
	}

	b := &bridge{
		store:     store{dir: *sessionDir},
		hub:       newHub(),
		startedAt: time.Now(),
		state:     stateLoggedOut,
		detail:    "starting",
	}
	// The cache outlives any single client: thread ids do not change, and a
	// reconnect that rebuilt the client should not also re-pay a route lookup
	// per room.
	b.threads = newThreadIndex(func(ctx context.Context, shortID int64) (*instameow.ThreadIGIDs, error) {
		cli := b.currentClient()
		if cli == nil {
			return nil, errNotConnected
		}
		return cli.FetchThreadID(ctx, shortID)
	})
	if self, ok := b.store.loadSelf(); ok {
		b.self = self
	}

	libLog := zerolog.New(zerolog.ConsoleWriter{Out: os.Stderr, TimeFormat: time.TimeOnly}).
		Level(zerolog.ErrorLevel).With().Timestamp().Logger()
	if *verbose {
		libLog = libLog.Level(zerolog.DebugLevel)
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	srv := &http.Server{
		Addr:    fmt.Sprintf(":%d", *port),
		Handler: b.routes(),
		// No write timeout: /events is a long-lived SSE stream and any write
		// deadline would sever it on a quiet room. Read timeouts are set
		// instead, since request bodies are bounded and fast.
		ReadHeaderTimeout: 10 * time.Second,
	}

	var wg sync.WaitGroup
	// The supervisor runs even with no credential on disk. It is what READS
	// the session directory, so starting it unconditionally is what lets an
	// operator drop a `Copy as cURL` paste into the volume and have the bridge
	// pick it up within a backoff cycle, instead of needing a restart to
	// discover a file that is already there.
	wg.Add(1)
	go func() {
		defer wg.Done()
		b.supervise(ctx, libLog)
	}()
	wg.Add(1)
	go func() {
		defer wg.Done()
		b.heartbeat(ctx)
	}()

	go func() {
		log.Printf("listening on :%d (session dir %s)", *port, *sessionDir)
		if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Printf("FATAL http server: %v", err)
			stop()
		}
	}()

	<-ctx.Done()
	log.Printf("shutting down")
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	_ = srv.Shutdown(shutdownCtx)
	if cli := b.currentClient(); cli != nil {
		cli.Disconnect()
	}
	b.flushCookies()
	wg.Wait()
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
func (b *bridge) supervise(ctx context.Context, libLog zerolog.Logger) {
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
		if err := b.ensureSession(ctx, libLog); err != nil {
			wait := backoff
			if errors.Is(err, errNoSession) {
				wait = sessionPoll
			} else {
				backoff = min(backoff*2, reconnectCeiling)
			}
			log.Printf("session setup failed (%s, retrying in %s): %v", b.currentState(), wait, err)
			select {
			case <-ctx.Done():
				return
			case <-time.After(wait):
			}
			continue
		}
		backoff = reconnectFloor

		cli := b.currentClient()
		if cli == nil {
			continue
		}
		cli.Connect(ctx) // blocks until clean close, cancel, or resnapshot
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
		select {
		case <-ctx.Done():
			return
		case <-time.After(reconnectFloor):
		}
	}
}

// ensureSession brings the client to the point where Connect() can be called:
// authenticated, with a socket, and with a sequence id to resume from.
func (b *bridge) ensureSession(ctx context.Context, libLog zerolog.Logger) error {
	cli := b.currentClient()
	if cli == nil {
		// Re-read the credential from disk on every rebuild. The jar in memory
		// is the one that just failed; the one on disk may be a session the
		// operator dropped in thirty seconds ago. Reusing the in-memory copy
		// would mean a cleared checkpoint is only noticed at the next restart.
		jar, err := loadCookieJar(b.store)
		if err != nil {
			b.setState(stateLoggedOut, fmt.Sprintf("no usable session: %v", err))
			return fmt.Errorf("%w: %w", errNoSession, err)
		}
		if missing := jar.GetMissingCookieNames(); len(missing) > 0 {
			// sessionid / csrftoken / ds_user_id. A partial jar fails later
			// with an opaque auth error, so it is refused here with the names.
			b.setState(stateLoggedOut, fmt.Sprintf("missing cookies: %v", missing))
			return fmt.Errorf("%w: missing cookies %v", errNoSession, missing)
		}
		b.setJar(jar)

		seq, seqTS := b.store.loadSeq()
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
		if state, ok := b.store.loadState(); ok {
			if err := cli.LoadState(state); err != nil {
				// instameow refuses state older than 24h (ErrCachedStateTooOld).
				// That is not an error condition — it is the documented signal
				// to do a full LoadIndex — so it is logged and stepped over.
				log.Printf("cached state not usable, will re-read index: %v", err)
			} else {
				log.Printf("loaded reconnection state (seq=%d)", seq)
			}
		}
		b.setClient(cli)
	}

	if !b.needsReindex() && cli.HasSeqID() && cli.IsAuthenticated() {
		b.setState(stateAuthenticated, "resumed from cached state")
		return nil
	}

	loadCtx, cancel := context.WithTimeout(ctx, 60*time.Second)
	defer cancel()
	viewer, mailbox, err := cli.LoadIndex(loadCtx)
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
	// (measured: 18781497603028340 vs 14963523149149302) and matches no
	// mention, anywhere, ever — and choosing it produces no error at all,
	// just an account that never notices it was tagged. viewer.GetUserId()
	// is the public igid, which the Phase 2 social calls take.
	self := selfIdentity{
		FBID:     strconv.FormatInt(cli.GetOwnFBID(), 10),
		IGID:     viewer.GetUserId(),
		Username: viewer.GetUsername(),
	}
	if self.FBID == "0" {
		self.FBID = ""
	}
	b.setSelf(self)
	if err := b.store.saveSelf(self); err != nil {
		log.Printf("could not cache own identity: %v", err)
	}
	if mailbox != nil {
		b.setSeq(mailbox.UQSeqID)
		if err := b.store.saveSeq(mailbox.UQSeqID, time.Now()); err != nil {
			log.Printf("could not persist seq id: %v", err)
		}
	}
	b.clearReindex()
	b.setState(stateAuthenticated, "")
	b.flushCookies()
	log.Printf("authenticated as @%s (fbid=%s igid=%s)", self.Username, self.FBID, self.IGID)
	return nil
}

// onEvent is instameow's single callback for everything the session does. It
// runs on the socket's goroutine, so nothing here may block: every branch
// either mutates a mutex-guarded field or hands a serialized event to the hub,
// which drops rather than blocks.
func (b *bridge) onEvent(ctx context.Context, evt slidetypes.ClientEvent) error {
	switch e := evt.(type) {
	case *slidetypes.Connected:
		b.setConnected(true)
		b.setSeq(e.LatestSeqID)
		log.Printf("realtime socket connected (subscribed_seq=%d latest_seq=%d)", e.SubscribedSeqID, e.LatestSeqID)

	case *slidetypes.Disconnected:
		b.setConnected(false)
		detail := "socket disconnected"
		if e.Error != nil {
			detail = e.Error.Error()
		}
		log.Printf("socket disconnected (failures=%d): %s", e.FailureCount, detail)

	case *slidetypes.AuthError:
		// The single most important branch in the file. Instagram rejected the
		// session mid-stream: record WHICH kind of rejection, keep serving, and
		// hand control back to the supervisor.
		//
		// The handback is not optional. Connect() blocks and reconnects on its
		// own, so without it the socket would sit there retrying while the
		// bridge reported `checkpointed` FOREVER — including long after the
		// operator cleared the challenge, because nothing would ever call
		// LoadIndex again to discover that it had been cleared. A state that
		// cannot recover on its own is a state somebody has to notice, and the
		// whole point of reporting it as a field is that nobody has to.
		//
		// Disconnect must run on another goroutine: it waits for the socket
		// loop to stop, and this handler IS the socket loop.
		state := classifyAuthError(e.Error)
		detail := ""
		if e.Error != nil {
			detail = e.Error.Error()
		}
		b.setState(state, detail)
		b.forceReindex()
		log.Printf("session %s: %s", state, detail)
		if cli := b.currentClient(); cli != nil {
			go cli.Disconnect()
		}

	case *slidetypes.SeqIDUpdate:
		b.setSeq(e.SeqID)
		if err := b.store.saveSeq(e.SeqID, e.Timestamp); err != nil {
			log.Printf("could not persist seq id: %v", err)
		}

	case *slidetypes.ReconnectionStateUpdate:
		// ~6 KB, written atomically on every change. This is what makes a
		// restart a resume instead of a fresh login, and a fresh login is
		// exactly the event that provokes a trust check.
		if err := b.store.saveState(e.State); err != nil {
			log.Printf("could not persist reconnection state: %v", err)
		}

	case *slidetypes.ResnapshotRequired:
		// Connect() returns right after dispatching this, so the supervisor
		// regains control on its own; all this branch owes is the flag that
		// stops the next pass from trusting the cached sequence id.
		log.Printf("resnapshot required, will re-read index")
		b.forceReindex()

	case *slidetypes.TypingNotification:
		b.emitTyping(e)

	case *slidetypes.Delta:
		b.emitDelta(e)
	}
	return nil
}

// emitDelta maps one delta onto the SSE stream. Deltas whose type carries no
// meaning for Nora (read receipts, folder moves, mute changes, thread pins)
// fall through and emit nothing: forwarding them would age
// `last_event_age_seconds` on traffic that is not conversation, which is the
// one thing that detector must not be fooled by.
func (b *bridge) emitDelta(d *slidetypes.Delta) {
	room := d.ThreadIGID // JSON tag `thread_fbid`: the SHORT id, despite the name
	own := b.ownFBID()
	switch data := d.Data.(type) {
	case *slidetypes.NewMessageEvent:
		if ev := normalizeMessage(room, data.Message, own); ev != nil {
			b.hub.emit(ev)
		}
	case *slidetypes.AdminMessageEvent:
		// GROUP_THREAD_CREATED_XMAT, ADD_PARTICIPANT_XMAT and friends arrive
		// as ordinary messages attributed to whoever acted. They are emitted
		// as messages with their content_type intact, which is how a roster
		// change reaches the adapter at all.
		if ev := normalizeMessage(room, data.Message, own); ev != nil {
			b.hub.emit(ev)
		}
	case *slidetypes.ParticipantJoinEvent:
		if ev := normalizeMessage(room, data.Message, own); ev != nil {
			b.hub.emit(ev)
		}
	case *slidetypes.ParticipantLeaveEvent:
		if ev := normalizeMessage(room, data.Message, own); ev != nil {
			b.hub.emit(ev)
		}
	case *slidetypes.CreateReactionEvent:
		b.hub.emit(normalizeReaction(room, data.Reaction, data.MessageID, false))
	case *slidetypes.DeleteReactionEvent:
		b.hub.emit(normalizeReaction(room, data.Reaction, data.MessageID, true))
	case *slidetypes.EditMessageEvent:
		ev := &editEvent{Kind: kindEdit, RoomID: room, MessageID: data.MessageID, Text: data.TextBody}
		if !data.SlideEditHistoryEntry.TimestampMS.IsZero() {
			ev.TimestampMS = data.SlideEditHistoryEntry.TimestampMS.UnixMilli()
		}
		b.hub.emit(ev)
	case *slidetypes.DeleteMessageEvent:
		b.hub.emit(&unsendEvent{Kind: kindUnsend, RoomID: room, MessageID: data.MessageID})
	}
}

func (b *bridge) emitTyping(t *slidetypes.TypingNotification) {
	ev := &typingEvent{
		Kind:     kindTyping,
		ThreadID: t.ThreadID,
		Typing:   t.ActivityStatus != 0,
	}
	if t.SenderID != 0 {
		ev.SenderFBID = strconv.FormatInt(t.SenderID, 10)
	}
	// room_id is filled ONLY when the cache can vouch for the id space. See
	// typingEvent's doc comment: guessing here would put a long id in a field
	// the contract defines as the short one, and every consumer keying on
	// room_id would miss without ever erroring.
	if room, ok := b.threads.roomFor(t.ThreadID); ok {
		ev.RoomID = room
	}
	b.hub.emit(ev)
}

func (b *bridge) heartbeat(ctx context.Context) {
	ticker := time.NewTicker(heartbeatInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			if !b.hub.hasClients() {
				continue
			}
			state, _, connected, _ := b.snapshot()
			b.hub.emit(&heartbeatEvent{
				Kind: kindHeartbeat, TSMS: time.Now().UnixMilli(),
				Connected: connected, Session: state,
			})
		}
	}
}

func (b *bridge) setJar(jar *cookies.Cookies) {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.jar = jar
}

func (b *bridge) currentJar() *cookies.Cookies {
	b.mu.RLock()
	defer b.mu.RUnlock()
	return b.jar
}

func (b *bridge) flushCookies() {
	jar := b.currentJar()
	if jar == nil {
		return
	}
	if err := b.store.saveCookies(jar); err != nil {
		// Expected and harmless while logged out; noisy only if it persists.
		log.Printf("cookie flush skipped: %v", err)
	}
}

// --- guarded state ------------------------------------------------------------

func (b *bridge) setClient(cli *instameow.Client) {
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

func (b *bridge) currentClient() *instameow.Client {
	b.mu.RLock()
	defer b.mu.RUnlock()
	return b.cli
}

// sendClient and uploadFn are what the HTTP handlers reach for. They are
// narrow on purpose: a handler that could see *instameow.Client could also
// call LoadIndex or Disconnect from inside a request.
func (b *bridge) sendClient() igClient {
	b.mu.RLock()
	defer b.mu.RUnlock()
	return b.out
}

func (b *bridge) uploadFn() uploader {
	b.mu.RLock()
	defer b.mu.RUnlock()
	return b.up
}

func (b *bridge) setState(state sessionState, detail string) {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.state = state
	b.detail = detail
}

func (b *bridge) currentState() sessionState {
	b.mu.RLock()
	defer b.mu.RUnlock()
	return b.state
}

func (b *bridge) setConnected(v bool) {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.connected = v
}

func (b *bridge) setSeq(seq int64) {
	if seq == 0 {
		return
	}
	b.mu.Lock()
	defer b.mu.Unlock()
	b.seq = seq
}

func (b *bridge) setSelf(self selfIdentity) {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.self = self
}

func (b *bridge) ownFBID() int64 {
	b.mu.RLock()
	defer b.mu.RUnlock()
	fbid, _ := strconv.ParseInt(b.self.FBID, 10, 64)
	return fbid
}

func (b *bridge) forceReindex() {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.reindex = true
}

func (b *bridge) clearReindex() {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.reindex = false
}

func (b *bridge) needsReindex() bool {
	b.mu.RLock()
	defer b.mu.RUnlock()
	return b.reindex
}

func (b *bridge) snapshot() (sessionState, string, bool, int64) {
	b.mu.RLock()
	defer b.mu.RUnlock()
	return b.state, b.detail, b.connected, b.seq
}

func (b *bridge) selfSnapshot() selfIdentity {
	b.mu.RLock()
	defer b.mu.RUnlock()
	return b.self
}

func envStr(name, fallback string) string {
	if v := os.Getenv(name); v != "" {
		return v
	}
	return fallback
}

func envInt(name string, fallback int) int {
	if v := os.Getenv(name); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			return n
		}
	}
	return fallback
}
