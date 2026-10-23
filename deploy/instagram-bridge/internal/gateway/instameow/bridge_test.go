package instameow

// The supervisor and the event callback: the two places with no HTTP surface,
// which is why neither had a test and why both of the "silent" defects lived
// here. Nothing below touches the network — the account is real and in use —
// so what is measured is the state machine: what the bridge decides, what it
// writes to disk, and how long it waits before touching Instagram again.

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"testing"
	"time"

	"instagram-bridge/internal/domain"
	"instagram-bridge/internal/session"
	"instagram-bridge/internal/stream"

	"github.com/rs/zerolog"
	"go.mau.fi/mautrix-meta/pkg/instameow/slidetypes"
	"go.mau.fi/mautrix-meta/pkg/messagix/httpclient"
)

func eventBridge(t *testing.T) *Bridge {
	t.Helper()
	b := NewBridge(Options{
		Store: session.Store{Dir: t.TempDir()},
		Sink:  stream.NewHub(),
		State: domain.StateAuthenticated,
		Self:  domain.Self{FBID: "18247361241432477", IGID: "79597894900", Username: "not_____nora"},
	})
	b.threads = newThreadIndex((&fakeIG{}).FetchThreadID)
	return b
}

// --- the backoff -------------------------------------------------------------

// Connect() has NO return value and returns instantly on a clean close, so a
// socket that dies the moment it opens is indistinguishable from one that ran
// for a week UNLESS the duration is measured. Without this the supervisor
// treated every return as a healthy session, reset to the five-second floor,
// and re-ran LoadIndex against Instagram every five seconds forever — with the
// anti-ban ceiling, which exists for exactly this, never once engaging.
func TestAFlappingSocketBacksOffToTheAntiBanCeiling(t *testing.T) {
	wait := reconnectFloor
	var waits []time.Duration
	for range 12 {
		wait = nextBackoff(wait, time.Millisecond) // Connect returned at once
		waits = append(waits, wait)
	}
	if waits[0] <= reconnectFloor {
		t.Fatalf("the first flap did not back off at all: %s", waits[0])
	}
	for i := 1; i < len(waits); i++ {
		if waits[i] < waits[i-1] {
			t.Fatalf("backoff went backwards: %v", waits)
		}
	}
	if got := waits[len(waits)-1]; got != reconnectCeiling {
		t.Fatalf("after 12 flaps the wait is %s, want the ceiling %s", got, reconnectCeiling)
	}
	// And it must never exceed it: the ceiling is the promise, not a trend.
	if nextBackoff(reconnectCeiling, time.Millisecond) != reconnectCeiling {
		t.Fatal("the backoff climbed past the anti-ban ceiling")
	}
}

// The other half. A connection that actually ran is a success and must reset,
// or one bad night would leave the bridge waiting five minutes between every
// ordinary reconnect for the rest of the process's life.
func TestASessionThatLastedResetsTheBackoff(t *testing.T) {
	if got := nextBackoff(reconnectCeiling, stableConnection); got != reconnectFloor {
		t.Fatalf("a %s session left the backoff at %s", stableConnection, got)
	}
	if got := nextBackoff(reconnectCeiling, 26*time.Hour); got != reconnectFloor {
		t.Fatalf("a day-long session left the backoff at %s", got)
	}
	// The boundary belongs to "stable": >= not >.
	if got := nextBackoff(reconnectFloor, stableConnection-time.Nanosecond); got == reconnectFloor {
		t.Fatal("a session one nanosecond under the bar counted as stable")
	}
}

func TestSleepOrDoneReturnsFalseOnShutdown(t *testing.T) {
	ctx, cancel := context.WithCancel(t.Context())
	cancel()
	if sleepOrDone(ctx, time.Hour) {
		t.Fatal("a cancelled context waited an hour")
	}
	if !sleepOrDone(t.Context(), time.Millisecond) {
		t.Fatal("a completed wait reported shutdown")
	}
}

// supervise runs with no credential on disk — the documented first-run state,
// where an operator has not yet dropped a file into the volume. It must poll,
// not spin, and it must stop when the process does.
func TestSuperviseWithNoCredentialPollsAndStopsOnShutdown(t *testing.T) {
	b := eventBridge(t)
	ctx, cancel := context.WithCancel(t.Context())
	done := make(chan struct{})
	go func() {
		defer close(done)
		b.supervise(ctx, zerolog.Nop())
	}()
	// One pass is enough to reach the poll: sessionPoll is 10s, so if the loop
	// spun instead of waiting this would be thousands of passes by now.
	deadline := time.After(3 * time.Second)
	for b.currentState() != domain.StateLoggedOut {
		select {
		case <-deadline:
			t.Fatal("supervise never reported logged_out")
		case <-time.After(5 * time.Millisecond):
		}
	}
	cancel()
	select {
	case <-done:
	case <-time.After(3 * time.Second):
		t.Fatal("supervise ignored shutdown and kept polling")
	}
}

// --- the checkpoint latch ----------------------------------------------------

// An Instagram challenge is a normal operating state, so the bridge keeps
// serving and reports it as a field. The latch is what lets it come BACK: the
// reindex flag is set on the rejection and cleared only by a LoadIndex that
// succeeded, which is what discovers that the operator cleared the challenge.
// Without the release the bridge would report `checkpointed` forever, long
// after the account was fine.
func TestAuthErrorLatchesReindexAndOnlyASuccessfulLoadReleasesIt(t *testing.T) {
	b := eventBridge(t)
	if b.needsReindex() {
		t.Fatal("a fresh bridge already wants a reindex")
	}
	if err := b.onEvent(t.Context(), &slidetypes.AuthError{Error: httpclient.ErrCheckpointRequired}); err != nil {
		t.Fatal(err)
	}
	if got := b.currentState(); got != domain.StateCheckpointed {
		t.Fatalf("state = %q, want checkpointed", got)
	}
	if !b.needsReindex() {
		t.Fatal("a rejected session did not latch the reindex")
	}
	// Still serving: the whole point of reporting it as a field.
	if _, _, connected, _ := b.snapshot(); connected {
		t.Error("the bridge still claims a live socket after a rejection")
	}
	b.clearReindex()
	if b.needsReindex() {
		t.Fatal("the latch did not release")
	}
}

// --- the invalidated sequence id ---------------------------------------------

// THE DURABLE HALF. ResnapshotRequired means Instagram threw the snapshot
// away, which voids the sequence id with it. An in-memory flag only binds THIS
// process: a restart in the window before the next LoadIndex reads seq.json,
// finds a number, takes ensureSession's resume shortcut, skips LoadIndex and
// subscribes from a sequence id that refers to nothing. Nothing errors. The
// room just goes quiet.
func TestAVoidedSequenceIDDoesNotSurviveARestart(t *testing.T) {
	for name, evt := range map[string]slidetypes.ClientEvent{
		"resnapshot": &slidetypes.ResnapshotRequired{},
		"auth error": &slidetypes.AuthError{Error: httpclient.ErrCheckpointRequired},
	} {
		b := eventBridge(t)
		if err := b.store.SaveSeq(4242, time.Now()); err != nil {
			t.Fatal(err)
		}
		if err := b.onEvent(t.Context(), evt); err != nil {
			t.Fatal(err)
		}
		if seq, _ := b.store.LoadSeq(); seq != 0 {
			t.Errorf("%s: seq.json still holds %d — a restart would resume from it", name, seq)
		}
		if _, _, _, seq := b.snapshot(); seq != 0 {
			t.Errorf("%s: /health still reports seq %d", name, seq)
		}
	}
}

// The negative half, and it matters as much: an ORDINARY disconnection does
// not void anything. Clearing seq.json on every socket blip would force a full
// LoadIndex on every reconnect and lose every delta between the two — which is
// the failure the sequence id exists to prevent.
func TestAnOrdinaryDisconnectKeepsTheSequenceID(t *testing.T) {
	b := eventBridge(t)
	if err := b.store.SaveSeq(4242, time.Now()); err != nil {
		t.Fatal(err)
	}
	if err := b.onEvent(t.Context(), &slidetypes.Disconnected{FailureCount: 1, Error: errors.New("eof")}); err != nil {
		t.Fatal(err)
	}
	if seq, _ := b.store.LoadSeq(); seq != 4242 {
		t.Fatalf("an ordinary disconnect discarded the sequence id (seq=%d)", seq)
	}
}

// --- every onEvent branch ----------------------------------------------------

func TestConnectedRecordsTheSocketAndTheLatestSeq(t *testing.T) {
	b := eventBridge(t)
	if err := b.onEvent(t.Context(), &slidetypes.Connected{SubscribedSeqID: 10, LatestSeqID: 4242}); err != nil {
		t.Fatal(err)
	}
	state, _, connected, seq := b.snapshot()
	if !connected || seq != 4242 {
		t.Fatalf("connected=%v seq=%d state=%q", connected, seq, state)
	}
}

func TestSeqAndStateUpdatesReachTheDisk(t *testing.T) {
	b := eventBridge(t)
	ts := time.Now().UTC().Truncate(time.Second)
	if err := b.onEvent(t.Context(), &slidetypes.SeqIDUpdate{SeqID: 99, Timestamp: ts}); err != nil {
		t.Fatal(err)
	}
	seq, got := b.store.LoadSeq()
	if seq != 99 || !got.Equal(ts) {
		t.Fatalf("seq persisted as %d at %s", seq, got)
	}
	state := json.RawMessage(`{"Configs":{}}`)
	if err := b.onEvent(t.Context(), &slidetypes.ReconnectionStateUpdate{State: state}); err != nil {
		t.Fatal(err)
	}
	// This is what makes a restart a resume rather than a fresh login, and a
	// fresh login is the event that provokes a trust check.
	if blob, ok := b.store.LoadState(); !ok || string(blob) != string(state) {
		t.Fatalf("reconnection state = %q, %v", blob, ok)
	}
}

// A typing notification arrives on the stream-controller socket carrying an id
// whose space was never established. room_id is filled only when the cache can
// vouch for it — guessing would put a long id in a field the contract defines
// as the short one, and every consumer keying on room_id would miss silently.
func TestTypingClaimsARoomOnlyWhenTheCacheCanVouchForIt(t *testing.T) {
	b := eventBridge(t)
	ch, _, _, _ := hubOf(b).Subscribe()
	defer hubOf(b).Unsubscribe(ch)

	if err := b.onEvent(t.Context(), &slidetypes.TypingNotification{
		ThreadID: threadLong, SenderID: 186217604552756, ActivityStatus: 1,
	}); err != nil {
		t.Fatal(err)
	}
	ev := nextTyping(t, ch)
	if ev.RoomID != "" {
		t.Fatalf("room_id = %q for an id the cache has never seen", ev.RoomID)
	}
	if ev.ThreadID != threadLong || !ev.Typing {
		t.Fatalf("typing event = %+v", ev)
	}
	if ev.FromMe == nil || *ev.FromMe {
		t.Fatalf("from_me = %v for another member's typing", ev.FromMe)
	}

	// Now teach the cache the pair, and the same notification resolves.
	if _, err := b.threads.resolve(t.Context(), shortThread(t, threadShort)); err != nil {
		t.Fatal(err)
	}
	if err := b.onEvent(t.Context(), &slidetypes.TypingNotification{
		ThreadID: threadLong, SenderID: 18247361241432477, ActivityStatus: 1,
	}); err != nil {
		t.Fatal(err)
	}
	ev = nextTyping(t, ch)
	if ev.RoomID != threadShort {
		t.Fatalf("room_id = %q, want the short id", ev.RoomID)
	}
	if ev.FromMe == nil || !*ev.FromMe {
		t.Fatalf("from_me = %v for our own typing", ev.FromMe)
	}
}

// Every delta shape the bridge claims to forward, forwarded once each — and
// the ones it deliberately drops, dropped. A read receipt that aged
// `last_event_age_seconds` would make the socket-death detector unable to tell
// a live room from a dead socket, which is the one thing it exists for.
func TestEveryMeaningfulDeltaIsForwardedAndTheRestAreNot(t *testing.T) {
	msg := &slidetypes.Message{MessageID: "mid.$m", SenderFBID: 186217604552756, TextBody: "hi"}
	cases := []struct {
		name string
		data slidetypes.DeltaEvent
		kind string
	}{
		{"message", &slidetypes.NewMessageEvent{Message: msg}, kindMessage},
		{"admin message", &slidetypes.AdminMessageEvent{Message: msg}, kindMessage},
		{"join", &slidetypes.ParticipantJoinEvent{Message: msg}, kindMessage},
		{"leave", &slidetypes.ParticipantLeaveEvent{Message: msg}, kindMessage},
		{"reaction", &slidetypes.CreateReactionEvent{MessageID: "mid.$m",
			Reaction: slidetypes.Reaction{Reaction: "👍", SenderFBID: 186217604552756}}, kindReaction},
		{"reaction removed", &slidetypes.DeleteReactionEvent{MessageID: "mid.$m",
			Reaction: slidetypes.Reaction{Reaction: "👍", SenderFBID: 186217604552756}}, kindReaction},
		{"edit", &slidetypes.EditMessageEvent{MessageID: "mid.$m", TextBody: "fixed"}, kindEdit},
		{"unsend", &slidetypes.DeleteMessageEvent{MessageID: "mid.$m"}, kindUnsend},
		// Not conversation. Forwarding these would age the socket-death
		// detector on traffic nobody said.
		{"read receipt", &slidetypes.MarkReadEvent{}, ""},
		{"thread pinned", &slidetypes.PinThreadEvent{}, ""},
		{"muted", &slidetypes.MuteThreadEvent{}, ""},
	}
	for _, c := range cases {
		b := eventBridge(t)
		if err := b.onEvent(t.Context(), &slidetypes.Delta{ThreadIGID: threadShort, Data: c.data}); err != nil {
			t.Fatalf("%s: %v", c.name, err)
		}
		_, buffered, dropped, _ := hubOf(b).Stats()
		if dropped != 0 {
			t.Errorf("%s: %d events were dropped", c.name, dropped)
		}
		if c.kind == "" {
			if buffered != 0 {
				t.Errorf("%s: emitted %d events, want none", c.name, buffered)
			}
			continue
		}
		if buffered != 1 {
			t.Fatalf("%s: emitted %d events, want 1", c.name, buffered)
		}
		_, replay, _, _ := hubOf(b).Subscribe()
		var got map[string]any
		if err := json.Unmarshal(replay[0], &got); err != nil {
			t.Fatal(err)
		}
		if got["kind"] != c.kind {
			t.Errorf("%s: kind = %v, want %q", c.name, got["kind"], c.kind)
		}
		if got["room_id"] != threadShort {
			t.Errorf("%s: room_id = %v", c.name, got["room_id"])
		}
		// The contract says every event carries from_me. On edit and unsend
		// the delta names no sender, so the value is an explicit null: "the
		// bridge does not know" is a thing an adapter can branch on, a
		// confident `false` is not.
		if _, ok := got["from_me"]; !ok {
			t.Errorf("%s: from_me is missing from the event", c.name)
		}
	}
}

// --- the identity that everything else binds on -------------------------------

// GetOwnFBID() returning 0 used to be persisted like any other value, which
// OVERWROTE a good self.json with one loadSelf refuses. From then on from_me
// was false for every echo, self-tag detection matched nothing and /react
// could not verify its own read-back — with no error, no log and no field on
// /health that looked wrong. The only symptom is an account that stops
// noticing it was spoken to.
func TestAZeroOwnFBIDDoesNotDestroyTheCachedIdentity(t *testing.T) {
	good := domain.Self{FBID: "18247361241432477", IGID: "79597894900", Username: "not_____nora"}
	b := eventBridge(t)
	b.setSelf(good)
	if err := b.store.SaveSelf(good); err != nil {
		t.Fatal(err)
	}

	// The pathological LoadIndex: a viewer, and no interop fbid.
	b.recordSelf(0, "79597894900", "not_____nora")

	if got := b.selfSnapshot(); got != good {
		t.Fatalf("the live identity was replaced by %+v", got)
	}
	if got, ok := b.store.LoadSelf(); !ok || got != good {
		t.Fatalf("self.json was overwritten: %+v, %v", got, ok)
	}
	// And the thing that actually breaks when it is lost.
	if b.ownFBID() == 0 {
		t.Fatal("ownFBID() is 0 — from_me and self-tag detection are now dead")
	}
}

// First boot with a zero fbid: there is nothing cached to protect, so what
// LoadIndex DID return is worth reporting on /health — but it must not be
// written to disk, because an identity with no fbid is not an identity and
// loadSelf refuses it anyway. Writing it would only make the next boot slower
// to notice the same problem.
func TestAZeroOwnFBIDOnAFirstBootIsReportedButNotPersisted(t *testing.T) {
	b := eventBridge(t)
	b.setSelf(domain.Self{})
	b.recordSelf(0, "79597894900", "not_____nora")

	if got := b.selfSnapshot(); got.Username != "not_____nora" || got.FBID != "" {
		t.Fatalf("self = %+v", got)
	}
	if _, err := os.Stat(b.store.Path("self.json")); !os.IsNotExist(err) {
		t.Fatal("an fbid-less identity was written to self.json")
	}
}

// The ordinary path still works, or "protect the cached value" would have been
// bought by never updating it.
func TestARealOwnFBIDIsRecordedAndPersisted(t *testing.T) {
	b := eventBridge(t)
	b.setSelf(domain.Self{})
	b.recordSelf(18247361241432477, "79597894900", "not_____nora")

	want := domain.Self{FBID: "18247361241432477", IGID: "79597894900", Username: "not_____nora"}
	if got := b.selfSnapshot(); got != want {
		t.Fatalf("self = %+v", got)
	}
	if got, ok := b.store.LoadSelf(); !ok || got != want {
		t.Fatalf("self.json = %+v, %v", got, ok)
	}
}

// --- shutdown -----------------------------------------------------------------

// flushCookies runs on the shutdown path with whatever jar is in memory, and
// on a logged-out session that jar is empty. It must not write, and it must
// not panic on a nil one either.
func TestFlushingANilOrEmptyJarIsSafe(t *testing.T) {
	b := eventBridge(t)
	b.flushCookies()
	if _, err := os.Stat(b.store.Path("cookies.json")); !os.IsNotExist(err) {
		t.Fatal("a nil jar was persisted")
	}
}

func nextTyping(t *testing.T, ch chan []byte) typingEvent {
	t.Helper()
	select {
	case payload := <-ch:
		var ev typingEvent
		if err := json.Unmarshal(payload, &ev); err != nil {
			t.Fatal(err)
		}
		return ev
	case <-time.After(2 * time.Second):
		t.Fatal("no typing event arrived")
		return typingEvent{}
	}
}

// The three states are not interchangeable and the difference decides what the
// operator is told to do. `checkpointed` means the SAME cookies work again
// after a human clears a challenge; `logged_out` means they are dead and a new
// browser session must be handed over. Collapsing them sends the operator to
// the wrong remedy; collapsing either into "authenticated" makes /send retry
// against a wall.
func TestAuthErrorsAreClassifiedNotCollapsed(t *testing.T) {
	cases := []struct {
		err  error
		want domain.SessionState
	}{
		{nil, domain.StateAuthenticated},
		{httpclient.ErrChallengeRequired, domain.StateCheckpointed},
		{httpclient.ErrCheckpointRequired, domain.StateCheckpointed},
		{httpclient.ErrConsentRequired, domain.StateCheckpointed},
		{httpclient.ErrTokenInvalidated, domain.StateLoggedOut},
		// Wrapped, because that is how they actually arrive.
		{fmt.Errorf("failed to load inbox: %w", httpclient.ErrCheckpointRequired), domain.StateCheckpointed},
		{fmt.Errorf("mercury upload: %w", httpclient.ErrTokenInvalidated), domain.StateLoggedOut},
		// Unclassified. FAIL CLOSED: an error we have no name for is reported
		// as needing a human, never as a healthy session.
		{errors.New("something nobody has seen before"), domain.StateCheckpointed},
	}
	for _, c := range cases {
		if got := classifyAuthError(c.err); got != c.want {
			t.Errorf("classifyAuthError(%v) = %q, want %q", c.err, got, c.want)
		}
	}
}

// "No credential to try" and "Instagram refused the credential we tried" are
// different states with different remedies and different retry rates. The
// first is a bootstrap step an operator is in the middle of, polled every few
// seconds; the second is an account state, backed off to five minutes because
// hammering a login endpoint while waiting for a human is how a checkpoint
// becomes a ban. Collapsing them gives one of the two the wrong rate.
func TestMissingOrPartialSessionIsABootstrapStateNotAnAuthFailure(t *testing.T) {
	cases := map[string]string{
		"nothing on disk": "",
		"partial paste":   `-H 'cookie: csrftoken=CSRF123'`,
	}
	for name, curl := range cases {
		b := NewBridge(Options{Store: session.Store{Dir: t.TempDir()}, Sink: stream.NewHub(), State: domain.StateAuthenticated})
		if curl != "" {
			if err := os.WriteFile(b.store.Path("cookies.curl"), []byte(curl), 0o600); err != nil {
				t.Fatal(err)
			}
		}
		err := b.ensureSession(t.Context(), zerolog.Nop())
		if !errors.Is(err, errNoSession) {
			t.Errorf("%s: ensureSession gave %v, want errNoSession", name, err)
		}
		if got := b.currentState(); got != domain.StateLoggedOut {
			t.Errorf("%s: session reported as %q, want logged_out", name, got)
		}
		if b.currentClient() != nil {
			t.Errorf("%s: a client was built from an unusable credential", name)
		}
	}
}

func hubOf(b *Bridge) *stream.Hub { return b.sink.(*stream.Hub) }
