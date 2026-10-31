package bridge

// The capture pipeline: the durable path every inbound delta takes before
// the compatibility SSE fan-out sees it.
//
//	admit (projection) → journal append+fsync → enqueue (Redis Lua) →
//	confirm → optional receipt (event_receipt action) → SSE emit
//
// Ordering guarantees the contract needs:
//   - A delta is NEVER emitted to /events before it is fsynced in the
//     journal: a client reading the stream can never see something a crash
//     would lose.
//   - Journal append failure HALTS upstream receive (the gate the
//     supervisor consults): broken durable capture stops the source rather
//     than draining unboundedly.
//   - Account sequencing is ONE critical section: normalize/transition
//     identity, journal append, and the pending-list append all run under
//     captureMu, so journal order IS capture order for every Emit caller.
//   - The drainer walks the pending list head-first and retries the head
//     until Redis takes it: a Redis outage of ANY length loses nothing —
//     accepted events drain in order when Redis recovers, no restart
//     required. The journal is the crash boundary; the list is its in-flight
//     mirror (bounded by the same journal capacity).
//   - One goroutine (the drainer) is the single publisher per account;
//     ordering lives in exactly one place.

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"instagram-bridge/internal/domain"
	"instagram-bridge/internal/observability"

	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/codes"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/trace"
)

// record types the journal holds
const (
	recEvent      = "event"      // {type, envelope}
	recEnqueued   = "enqueued"   // {type, event_id, canonical_event_id, stream_id, queued_ms}
	recReceipted  = "receipted"  // {type, event_id, canonical_event_id, state}
	recTransition = "transition" // {type, key, state, seq}
	recAmbiguous  = "ambiguous"  // {type, reason, raw} — retained, never guessed
)

// Enqueuer is the stream producer the sink needs — *Queue in production, a
// controllable fake in tests. sourceKey is the canonical identity the
// source-alias dedupe resolves; canonicalID is the ORIGINAL event alias the
// entry was written under, which downstream effects must key on.
type Enqueuer interface {
	Enqueue(ctx context.Context, envelopeJSON []byte, sourceKey, eventID string) (streamID, canonicalID string, err error)
	EnsureGroup(ctx context.Context) error
}

// ActionWriter is the bridge_action surface receipts reserve through —
// the same reservation/started/terminal lifecycle outbound actions use,
// owned by kind=event_receipt.
type ActionWriter interface {
	Reserve(ctx context.Context, actionID, ownerKind, ownerID string, ownerGeneration *int, op string, request json.RawMessage, requestHash string) (ReserveOutcome, *ActionRow, error)
	MarkStarted(ctx context.Context, actionID string, messageID *int64) (bool, error)
	Finish(ctx context.Context, actionID, state, platformRef string, platformMS *int64, errKind, errMsg string) error
}

// Sink is the EventSink the gateway emits into; it also fans the confirmed
// event to the compatibility hub after the durable chain completes.
type Sink struct {
	accountKey string
	key        []byte
	keyVersion int

	journal    *Journal
	queue      Enqueuer
	projection *ProjectionSource
	custody    *Custody
	actions    ActionWriter
	httpClient *http.Client
	metrics    *Metrics
	compat     interface {
		Emit(event any)
		HasClients() bool
	}
	receipt func(ctx context.Context, room, messageID, emoji string) error
	onHalt  func(reason string)

	// captureMu serializes admit → normalize → transitions → journal →
	// pending append: account sequencing in exactly one place. Upstream
	// event sources (socket delta goroutine, summons recorder) may call
	// Emit concurrently; ordering must not depend on that.
	captureMu sync.Mutex

	// pending is the in-flight mirror of journaled-but-unconfirmed work.
	// It is NEVER capacity-dropped: the journal is already the durable
	// bound, so a Redis outage backs up here (bounded) instead of stranding
	// accepted events until a restart.
	listMu    sync.Mutex
	pending   []*captureItem
	pendingHd int // dequeue index; items before it are confirmed
	wake      chan struct{}
	done      chan struct{}

	healthy atomic.Bool // journal path intact

	// seenSources is the capture-level dedupe: every journaled source_key.
	// Rebuilt from the journal at boot and set after each committed append,
	// so a redelivered upstream fact is suppressed ONCE — identical
	// content+revision reproduces the identical source key.
	seenSources map[string]bool

	// transitions is the committed-state ledger for clockless reaction
	// deltas: room|target|reactor|emoji → {last state, every committed
	// state, upstream watermark}. Rebuilt from the transitions embedded in
	// journaled event records (same record = same atomicity) so a restart
	// keeps both the event and the state it committed.
	transitions   map[string]*transitionState
	transitionsMu sync.Mutex

	// receipts is the receipt lane: events whose enqueue confirmed and
	// that carry receipt intent. A SECOND lane keeps pending receipts from
	// sitting at the enqueue head — enqueue progress must never wait on a
	// receipt that is retrying or unreservable yet.
	receipts   []*captureItem
	receiptHd  int
	wakeRcpt   chan struct{}
	receiptsMu sync.Mutex
}

// transitionState is one transition key's committed history.
type transitionState struct {
	Last    string          // last committed state
	Seen    map[string]bool // every state this key has committed
	LastSeq int64           // highest committed upstream revision (0 = none)
}

// captureItem is one unit of drainer work. receiptOnly marks an event that
// already reached the stream in a previous life (journaled `enqueued`
// record) but whose receipt action never completed: replay must NOT XADD it
// again — only finish the pending receipt. canonicalID is the ORIGINAL
// alias the stream entry was written under; receipt/action identities key
// on it so a rotated replay still resolves the same reservation.
type captureItem struct {
	env         *Envelope
	raw         any
	receiptOnly bool
	canonicalID string
	nextAttempt time.Time // receipt-lane retry backoff (zero = ready now)
}

// SinkConfig wires the pipeline's dependencies.
type SinkConfig struct {
	AccountKey string
	Key        []byte
	KeyVersion int
	Journal    *Journal
	Queue      Enqueuer
	Projection *ProjectionSource
	Custody    *Custody
	// Actions is the bridge_action writer the receipt path reserves through;
	// nil means receipts cannot be recorded honestly and are skipped with a
	// journaled state, never silently fired.
	Actions    ActionWriter
	HTTPClient *http.Client
	Metrics    *Metrics
	// Compat is the SSE hub — served AFTER durable capture, never the
	// correctness boundary.
	Compat interface {
		Emit(event any)
		HasClients() bool
	}
	// Receipt sends the configured receipt reaction post-enqueue; nil
	// disables receipts entirely.
	Receipt func(ctx context.Context, room, messageID, emoji string) error
	// OnHalt is called when durable capture breaks (journal error): the
	// wiring connects it to "disconnect upstream".
	OnHalt func(reason string)
}

// NewSink binds the pipeline. queue may be nil (Redis-less mode: events
// journal but never confirm — reported, not silently lost).
func NewSink(cfg SinkConfig) *Sink {
	s := &Sink{
		accountKey:  cfg.AccountKey,
		key:         cfg.Key,
		keyVersion:  cfg.KeyVersion,
		journal:     cfg.Journal,
		queue:       cfg.Queue,
		projection:  cfg.Projection,
		custody:     cfg.Custody,
		actions:     cfg.Actions,
		httpClient:  cfg.HTTPClient,
		metrics:     cfg.Metrics,
		compat:      cfg.Compat,
		receipt:     cfg.Receipt,
		onHalt:      cfg.OnHalt,
		wake:        make(chan struct{}, 1),
		done:        make(chan struct{}),
		wakeRcpt:    make(chan struct{}, 1),
		seenSources: map[string]bool{},
		transitions: map[string]*transitionState{},
	}
	if s.httpClient == nil {
		// Signed CDN fetches go through the policy client: no redirects to
		// private space, https only, bounded body.
		s.httpClient = NewFetchClient()
	}
	if s.metrics == nil {
		s.metrics = NewMetrics()
	}
	return s
}

// Healthy is the journal half of the upstream gate.
func (s *Sink) Healthy() bool { return s.healthy.Load() }

// UpstreamOK is the supervisor's gate: upstream receive runs only while
// durable capture works AND a valid projection has been seen (a bridge that
// has never loaded one cannot admit anything, so it must not drain the
// source at all).
func (s *Sink) UpstreamOK() bool {
	return s.healthy.Load() && s.projection != nil && s.projection.SeenValid()
}

// Emit implements instameow's EventSink. The hot path, serialized per
// account: normalize → admit → journal (fsync) → in-order pending append.
// The compat hub fires only after the event is durable AND enqueued.
func (s *Sink) Emit(ev any) {
	if !s.healthy.Load() {
		return // halted; nothing drains a broken journal into Redis
	}
	receivedMS := time.Now().UnixMilli()

	s.captureMu.Lock()
	defer s.captureMu.Unlock()

	norm, err := Normalize(ev, s.accountKey, s.key, s.keyVersion, receivedMS)
	if err != nil {
		s.metrics.Count("capture_error")
		slog.Warn("capture normalize failed", "error", err)
		return
	}
	if norm == nil || norm.Env == nil {
		return // dropped kinds (heartbeat, typing, self echo, empty turn)
	}
	env := norm.Env
	// ADMISSION FIRST: a denied room means NO body and NO attachment capture
	// at all. The event is counted, not stored. No projection at all means
	// nothing is admitted — fail closed, same as the upstream gate.
	var proj *Projection
	if s.projection != nil {
		proj = s.projection.Current()
	}
	admitted, granted := proj.Admit(env.ConversationID, env.ConversationType, env.SenderID)
	if !admitted {
		s.metrics.Count("admission_denied")
		return
	}
	env.AdmissionRev = revPtr(proj)
	// Receipt intent rides the envelope only when BOTH halves of the
	// contract's eligibility hold at capture: the sender grant
	// (room_receipt_senders membership/wildcard for groups,
	// dm_allowed_senders for DMs — admitted room alone grants nothing)
	// AND a deterministic addressed signal. `via` + `sender` persist in
	// the intent so the receipt lane can re-check the CURRENT grant and a
	// later phase can suppress a revoked one before the effect fires.
	if env.Kind == KindMessage && granted && proj.ReceiptEmoji != nil && *proj.ReceiptEmoji != "" {
		if via := receiptAddressed(env, proj); via != "" {
			env.Receipt = map[string]any{
				"emoji": *proj.ReceiptEmoji, "via": via,
				"sender": env.SenderID,
			}
		}
	}
	// Transition-identity resolution for clockless reaction deltas BEFORE
	// the source-key dedupe: the ledger decides emit / replay / ambiguous.
	// Ambiguous is journaled and never queued — a clockless transition that
	// cannot be grounded stays a recorded fact, not a guessed event.
	if len(norm.Transitions) > 0 {
		emit, reason := s.resolveTransitions(norm)
		if !emit {
			if reason == "" {
				s.metrics.Count("transition_replay_suppressed")
				return
			}
			s.metrics.Count("ambiguous_retained")
			rec, _ := json.Marshal(map[string]any{
				"type": recAmbiguous, "reason": reason,
				"source_key": env.SourceKey, "envelope": env,
			})
			if err := s.journal.Append(rec); err != nil {
				s.halt(fmt.Sprintf("journal append failed: %v", err))
			}
			return
		}
		env = norm.Env
	}
	// Capture-level dedupe: identical source content (+ upstream revision
	// when carried) reproduces the identical source_key — a redelivered
	// upstream fact is suppressed once, live and across restarts.
	if s.seenSources[env.SourceKey] {
		s.metrics.Count("replay_suppressed")
		return
	}
	// The capture's producer span mints the envelope's trace identity
	// BEFORE the journal append: the durable record and the published
	// payload carry the same traceparent the consumer continues.
	s.injectTrace(env)
	// Journal BEFORE anything else durable: an event not yet fsynced is an
	// event not yet held. The committed transition states ride the SAME
	// record — no crash window between transition and envelope exists.
	rec, _ := json.Marshal(map[string]any{
		"type": recEvent, "envelope": env, "transitions": norm.Transitions,
	})
	if err := s.journal.Append(rec); err != nil {
		s.halt(fmt.Sprintf("journal append failed: %v", err))
		return
	}
	s.seenSources[env.SourceKey] = true
	s.commitTransitions(norm.Transitions)
	s.metrics.Count("captured")
	s.push(&captureItem{env: env, raw: ev})
}

// push appends to the pending list and wakes the drainer. Nothing is ever
// dropped here: an accepted (journaled) event MUST eventually publish, so a
// full in-memory window would be the correctness bug the channel's default
// branch used to be.
func (s *Sink) push(item *captureItem) {
	s.listMu.Lock()
	s.pending = append(s.pending, item)
	s.listMu.Unlock()
	select {
	case s.wake <- struct{}{}:
	default:
	}
}

// takeHead returns the next unconfirmed item, or nil when the list is
// drained. Confirmed heads are released so the list cannot grow without
// bound behind completed work.
func (s *Sink) takeHead() *captureItem {
	s.listMu.Lock()
	defer s.listMu.Unlock()
	if s.pendingHd >= len(s.pending) {
		return nil
	}
	item := s.pending[s.pendingHd]
	return item
}

// confirmHead releases the item that just finished enqueueing.
func (s *Sink) confirmHead() {
	s.listMu.Lock()
	s.pendingHd++
	// Reclaim memory once all completed work is past.
	if s.pendingHd == len(s.pending) && s.pendingHd > 0 {
		s.pending = s.pending[:0]
		s.pendingHd = 0
	}
	s.listMu.Unlock()
}

// resolveTransitions decides one clockless delta's verdict against the
// committed ledger. Three outcomes:
//
//   - emit: a genuinely new transition — forward upstream revision, a
//     same-revision new state, or a never-before-committed state.
//   - replay (emit=false, reason=""): an identical redelivery — same
//     committed state, or a state already committed at this revision or
//     later. Suppressed silently; it IS a replay by definition.
//   - ambiguous (emit=false, reason set): a transition whose ordering
//     cannot be grounded — a state change the key already committed under
//     a clockless identity (stale redelivery and genuine re-transition
//     are indistinguishable), or a never-committed state arriving below
//     the upstream watermark. Journaled as `ambiguous`, never queued,
//     and the ledger is NOT updated (no false dedupe of a later real
//     transition).
//
// No sequence numbers are minted: the upstream identity is the content
// itself plus whatever revision the upstream actually stamped.
func (s *Sink) resolveTransitions(norm *Normalized) (emit bool, ambiguousReason string) {
	s.transitionsMu.Lock()
	defer s.transitionsMu.Unlock()
	for _, t := range norm.Transitions {
		st := s.transitions[t.Key]
		if st == nil {
			st = &transitionState{Seen: map[string]bool{}}
		}
		seen := st.Seen[t.State]
		if t.UpstreamSeq > 0 {
			// Revision-grounded deltas: forward progress or a same-batch
			// new state emits; a committed state at/below the watermark
			// is a stale redelivery; an uncommitted state below it is
			// unverifiable — ambiguous.
			switch {
			case t.UpstreamSeq > st.LastSeq:
				continue // forward progress — genuine
			case t.UpstreamSeq < st.LastSeq && !seen:
				return false, "stale upstream revision below watermark"
			case seen && st.LastSeq > 0 && t.UpstreamSeq <= st.LastSeq:
				return false, "" // redelivery of a committed transition
			default:
				continue // same revision, new state — same-batch transition
			}
		}
		// Clockless deltas (no timestamp, no seq): same-as-last is the
		// identical replay; a state the key has ALREADY committed is
		// undecidable — stale redelivery and genuine re-transition are
		// indistinguishable without an upstream clock → ambiguous;
		// a never-committed state is genuine.
		switch {
		case t.State == st.Last:
			return false, ""
		case seen:
			return false, "unresolvable clockless transition: state repeats without upstream revision"
		default:
			continue
		}
	}
	return true, ""
}

// commitTransitions applies the emitted event's transitions to the
// ledger. It runs ONLY after the event's journal append succeeded — the
// transitions are journaled in the same record, so the ledger and the
// event it belongs to are durable together.
func (s *Sink) commitTransitions(transitions []Transition) {
	if len(transitions) == 0 {
		return
	}
	s.transitionsMu.Lock()
	defer s.transitionsMu.Unlock()
	for _, t := range transitions {
		s.commitTransition(t)
	}
}

func (s *Sink) commitTransition(t Transition) {
	st := s.transitions[t.Key]
	if st == nil {
		st = &transitionState{Seen: map[string]bool{}}
		s.transitions[t.Key] = st
	}
	st.Seen[t.State] = true
	st.Last = t.State
	if t.UpstreamSeq > st.LastSeq {
		st.LastSeq = t.UpstreamSeq
	}
}

func (s *Sink) halt(reason string) {
	if s.healthy.CompareAndSwap(true, false) {
		s.metrics.Count("capture_halted")
		slog.Error("durable capture halted; upstream receive stops", "reason", reason)
		if s.onHalt != nil {
			s.onHalt(reason)
		}
	}
}

// HasClients mirrors the hub for the gateway's heartbeat check.
func (s *Sink) HasClients() bool { return s.compat.HasClients() }

// SetReceipt installs the post-enqueue receipt sender after the gateway and
// use cases exist (construction order: sink → bridge → service → receipt).
func (s *Sink) SetReceipt(fn func(ctx context.Context, room, messageID, emoji string) error) {
	s.receipt = fn
}

// Start runs preflight and boot: probe → recover → replay pending enqueues
// and pending receipts → drain. It must complete successfully before
// upstream receive starts.
func (s *Sink) Start(ctx context.Context) error {
	if err := s.journal.Probe(); err != nil {
		s.healthy.Store(false)
		return err
	}
	records, repaired, err := s.journal.Recover()
	if err != nil {
		s.healthy.Store(false)
		return err
	}
	if repaired {
		slog.Warn("journal torn tail truncated; replaying intact prefix")
	}
	s.healthy.Store(true)

	enqueued := map[string]string{} // submitted alias → canonical (original) alias
	receipted := map[string]bool{}
	var events []*Envelope
	for _, raw := range records {
		var rec struct {
			Type        string       `json:"type"`
			Envelope    *Envelope    `json:"envelope"`
			Transitions []Transition `json:"transitions"`
			EventID     string       `json:"event_id"`
			CanonicalID string       `json:"canonical_event_id"`
			Key         string       `json:"key"`
			State       string       `json:"state"`
			Seq         int64        `json:"seq"`
		}
		if err := json.Unmarshal(raw, &rec); err != nil {
			continue
		}
		switch rec.Type {
		case recEvent:
			if rec.Envelope != nil {
				events = append(events, rec.Envelope)
				s.seenSources[rec.Envelope.SourceKey] = true
			}
			// The committed transitions ride the event's own record —
			// one atomic unit, so the rebuilt ledger matches what the
			// journal actually committed, never a torn pair.
			for _, t := range rec.Transitions {
				s.commitTransition(t)
			}
		case recEnqueued:
			if rec.CanonicalID == "" {
				rec.CanonicalID = rec.EventID // pre-final records had no canonical field
			}
			enqueued[rec.EventID] = rec.CanonicalID
		case recReceipted:
			receipted[rec.EventID] = true
		case recTransition:
			// Legacy standalone transition records (pre-atomic form).
			s.commitTransition(Transition{Key: rec.Key, State: rec.State, UpstreamSeq: rec.Seq})
		case recAmbiguous:
			s.metrics.Count("ambiguous_retained")
		}
	}
	// Re-derive the drainer's work from the journal truth:
	//   - journaled event, no `enqueued` record → it never reached the
	//     stream (or its confirm record was lost): enqueue it — the Lua
	//     dedupe makes a landed-but-unconfirmed XADD idempotent.
	//   - journaled event + `enqueued` + receipt intent + no `receipted` →
	//     enqueue is DONE; only the pending receipt finishes, as a
	//     receiptOnly item carrying the CANONICAL alias the entry was
	//     written under — the identity the receipt action reserves against,
	//     stable across crash + correlation-key rotation.
	var replay []*captureItem
	for _, env := range events {
		canon, was := enqueued[env.EventID]
		switch {
		case !was:
			replay = append(replay, &captureItem{env: env})
		case env.Receipt != nil && !receipted[env.EventID]:
			// The enqueue already landed in a previous life: only the
			// receipt is pending. It goes to the RECEIPT lane — the
			// enqueue lane stays unblocked, and the canonical alias is
			// the action's identity across rotation.
			s.pushReceipt(&captureItem{env: env, receiptOnly: true, canonicalID: canon})
		default:
			s.metrics.Count("duplicate_suppressed")
		}
	}
	if s.queue != nil {
		if err := s.queue.EnsureGroup(ctx); err != nil {
			slog.Warn("consumer group not ensured; enqueue will retry", "error", errClass(err))
		}
	}
	go s.drain(ctx)
	go s.drainReceipts(ctx)
	for _, item := range replay {
		s.push(item)
	}
	if len(replay) > 0 {
		slog.Info("journal replay restored pending work", "items", len(replay))
	}
	return nil
}

// drain is the single publisher: pending items → custody → enqueue →
// confirm → compat emit, strictly in journal order. A Redis outage blocks
// the head (retry inside enqueue) — later events wait in the list rather
// than overtaking or being dropped. Receipts are a SECOND lane: a pending
// or retrying receipt never holds the enqueue head.
func (s *Sink) drain(ctx context.Context) {
	defer close(s.done)
	for {
		item := s.takeHead()
		if item == nil {
			select {
			case <-ctx.Done():
				return
			case <-s.wake:
				continue
			}
		}
		s.enqueue(ctx, item)
		s.confirmHead()
	}
}

// pushReceipt queues receipt work on the receipt lane.
func (s *Sink) pushReceipt(item *captureItem) {
	s.receiptsMu.Lock()
	s.receipts = append(s.receipts, item)
	s.receiptsMu.Unlock()
	select {
	case s.wakeRcpt <- struct{}{}:
	default:
	}
}

// takeReceipt returns the next receipt item that is ready to run (its
// backoff, if any, has elapsed), and the wait until the earliest not-yet
// ready item — the lane sleeps on that when nothing is due.
func (s *Sink) takeReceipt(now time.Time) (*captureItem, time.Duration) {
	s.receiptsMu.Lock()
	defer s.receiptsMu.Unlock()
	var ready *captureItem
	var wait time.Duration
	for i := s.receiptHd; i < len(s.receipts); i++ {
		it := s.receipts[i]
		if it == nil {
			continue
		}
		if !it.nextAttempt.After(now) {
			if ready == nil {
				ready = it
				s.receipts[i] = nil // taken; compacted below
			}
		} else if d := it.nextAttempt.Sub(now); wait == 0 || d < wait {
			wait = d
		}
	}
	// Compact completed slots off the head.
	for s.receiptHd < len(s.receipts) && s.receipts[s.receiptHd] == nil {
		s.receiptHd++
	}
	if s.receiptHd == len(s.receipts) && s.receiptHd > 0 {
		s.receipts = s.receipts[:0]
		s.receiptHd = 0
	}
	return ready, wait
}

// drainReceipts is the receipt lane's worker: each confirmed event's
// receipt intent → reserved action → platform reaction → journaled phase,
// retried on infrastructure failure until a durable phase records it.
// Backoff keeps a failing head from pinning the lane; enqueue progress is
// never gated on it.
func (s *Sink) drainReceipts(ctx context.Context) {
	backoff := 500 * time.Millisecond
	for {
		item, wait := s.takeReceipt(time.Now())
		if item == nil {
			delay := 2 * time.Second
			if wait > 0 && wait < delay {
				delay = wait
			}
			select {
			case <-ctx.Done():
				return
			case <-s.wakeRcpt:
				continue
			case <-time.After(delay):
				continue
			}
		}
		retry := s.doReceipt(ctx, item.env, item.canonicalID)
		if retry {
			item.nextAttempt = time.Now().Add(backoff)
			s.receiptsMu.Lock()
			s.receipts = append(s.receipts, item)
			s.receiptsMu.Unlock()
			if backoff < 15*time.Second {
				backoff *= 2
			}
			continue
		}
		backoff = 500 * time.Millisecond
	}
}

// mediaFetch is one custody copy to perform: which message.media entry and
// the signed upstream URL that carries its bytes.
type mediaFetch struct {
	index int
	url   string
}

// mediaFetches derives the fetch list from the event's raw provenance — the
// same derivation live events and journal-replayed ones share. `raw.media`
// may be a single object (today's shape) or a list (multi-attachment
// variants); message.media entries already holding a custody handle are
// skipped, so a replay never re-fetches.
func mediaFetches(env *Envelope) []mediaFetch {
	raw, _ := env.Message["raw"].(map[string]any)
	var out []mediaFetch
	switch m := raw["media"].(type) {
	case map[string]any:
		if u := str(m, "url"); u != "" {
			out = append(out, mediaFetch{index: 0, url: u})
		}
	case []any:
		for i, it := range m {
			d, ok := it.(map[string]any)
			if !ok {
				continue
			}
			if u := str(d, "url"); u != "" {
				out = append(out, mediaFetch{index: i, url: u})
			}
		}
	}
	// Skip entries already spooled (replay after a custody write).
	media, _ := env.Message["media"].([]any)
	var keep []mediaFetch
	for _, f := range out {
		if f.index < len(media) {
			if ref, ok := media[f.index].(map[string]any); ok {
				if str(ref, "fetch_token") != "" || str(ref, "attachment_key") != "" {
					continue
				}
			}
		}
		keep = append(keep, f)
	}
	return keep
}

// enqueue runs one event through custody → Redis → confirm → receipt →
// compat emit. Redis-down retries head-first (bounded by ctx): a later
// event never overtakes the head of the journal.
func (s *Sink) enqueue(ctx context.Context, item *captureItem) {
	env := item.env
	if s.queue == nil {
		return // journal holds it; nothing confirms — reported, not lost
	}
	// Custody: copy EVERY fetchable media ref into the spool BEFORE the
	// envelope is marshaled, so the stream never names bytes we do not
	// hold. A fetch failure marks the ref `unavailable` — honest
	// degradation the app can read — never a silent "no media arrived".
	if s.custody != nil {
		for _, f := range mediaFetches(env) {
			s.spoolMedia(ctx, env, f)
		}
	}
	var streamID, canonicalID string
	backoff := 250 * time.Millisecond
	for {
		env.QueuedMS = time.Now().UnixMilli()
		payload, err := env.Marshal()
		if err != nil {
			slog.Error("envelope marshal failed", "error", errClass(err))
			return
		}
		// Every attempt's span links back to the capture's producer span —
		// retries stay bounded siblings of the same lineage, never
		// children of a different trace. Queue-kind attribute rides at
		// creation for the collector's span-head routing.
		spanCtx, span := s.queueSpan(ctx, "bridge.enqueue", env,
			attribute.String("bridge.platform", env.Platform),
			attribute.String("bridge.kind", env.Kind))
		streamID, canonicalID, err = s.queue.Enqueue(spanCtx, payload, env.SourceKey, env.EventID)
		if err == nil {
			span.End()
			break
		}
		span.RecordError(errors.New(errClass(err)))
		span.SetStatus(codes.Error, errClass(err))
		span.End()
		if ctx.Err() != nil {
			return // shutdown; the journal still holds it for the next boot
		}
		s.metrics.Count("enqueue_retry")
		t := time.NewTimer(backoff)
		select {
		case <-ctx.Done():
			t.Stop()
			return
		case <-t.C:
		}
		if backoff < 5*time.Second {
			backoff *= 2
		}
	}
	// Both aliases ride the confirmation record: event_id is THIS envelope's
	// (possibly rotated) identity; canonical_event_id is the original alias
	// the entry was written under — the one receipts/actions key on. Keeping
	// both is what lets a crash + rotation still resolve the same receipt
	// reservation on replay.
	rec, _ := json.Marshal(map[string]any{
		"type": recEnqueued, "event_id": env.EventID,
		"canonical_event_id": canonicalID,
		"stream_id":          streamID, "queued_ms": env.QueuedMS,
	})
	if err := s.journal.Append(rec); err != nil {
		// The event IS on the stream but its confirmation failed: replay
		// re-enqueues, the Lua returns the same id — consistent, not a dup.
		s.halt(fmt.Sprintf("journal confirm failed: %v", err))
		return
	}
	s.metrics.Count("enqueued")
	// Replay suppression at the enqueue seam: the canonical alias differs
	// from this envelope's submitted id only when the source already
	// reached the stream under an earlier alias (a rotated-key replay).
	// That fact already fanned out — do not emit it to compat again.
	replayed := canonicalID != "" && canonicalID != env.EventID
	if replayed {
		s.metrics.Count("replay_suppressed")
	}
	if env.Receipt != nil && s.receipt != nil {
		s.pushReceipt(&captureItem{env: env, receiptOnly: true, canonicalID: canonicalID})
	}
	if item.raw != nil && s.compat != nil && !replayed {
		s.compat.Emit(item.raw)
	}
}

// spoolMedia copies one signed upstream object into custody and rewrites
// its media ref to the durable handle, or marks it unavailable.
func (s *Sink) spoolMedia(ctx context.Context, env *Envelope, f mediaFetch) {
	media, _ := env.Message["media"].([]any)
	if f.index >= len(media) {
		return
	}
	ref, ok := media[f.index].(map[string]any)
	if !ok {
		return
	}
	key := AttachmentKey(env.EventID, f.index)
	// Durable reuse FIRST: a crash after the custody write but before the
	// XADD replays the original (expired) URL. The deterministic key names
	// the same object — verify the spool's hash and rewire the ref rather
	// than re-fetching or falsely degrading bytes we already hold.
	if rec, ok := s.custody.Stat(key); ok {
		wireRef(ref, rec, env)
		s.metrics.Count("custody_reused")
		return
	}
	spanCtx, span := s.queueSpan(ctx, "bridge.custody_fetch", env,
		attribute.String("bridge.platform", env.Platform))
	rec, err := s.custody.FetchURL(spanCtx, s.httpClient, f.url, key, env.EventID)
	if err != nil {
		span.SetStatus(codes.Error, errClass(err))
		span.End()
		s.metrics.Count("custody_fetch_failed")
		// Explicit degradation the app can interpret: the ref stays, named
		// unavailable, rather than the media pretending it never existed.
		ref["unavailable"] = true
		ref["unavailable_reason"] = errClass(err)
		slog.Warn("media custody fetch failed; ref marked unavailable", "kind", errClass(err))
		return
	}
	span.End()
	wireRef(ref, rec, env)
	s.metrics.Count("custody_spooled")
}

// wireRef rewrites one media ref to its durable custody handle and records
// the object on the envelope's attachments list — shared by a fresh fetch
// and a crash-recovery reuse so the queued envelope looks identical either
// way.
func wireRef(ref map[string]any, rec *CustodyRecord, env *Envelope) {
	ref["attachment_key"] = rec.Key
	ref["fetch_token"] = "attachment:" + rec.Key
	ref["size"] = rec.Size
	ref["media_type"] = rec.MediaType
	ref["sha256"] = rec.SHA256
	if rec.FileName != "" {
		ref["filename"] = rec.FileName
	}
	env.Attachments = append(env.Attachments, Attachment{
		Key: rec.Key, MediaType: rec.MediaType,
		FileName: rec.FileName, Size: rec.Size,
	})
}

// doReceipt runs one pending receipt to a durable phase and reports
// whether the lane must schedule it again. The reaction is an ACTION like
// any other: reserved under owner kind=event_receipt (owner_id = the
// canonical event id the enqueue resolved to — the bridge's own journal
// authority; bridge_event may not exist yet on the app side and is never
// the binding for a bridge-initiated owner), prepared→started→terminal
// around the external call, and its terminal state is journaled so
// restart and replay never re-fire a landed or exhausted receipt.
//
// Return contract: true = infrastructure failure (Reserve/Finish error);
// the lane retries with backoff. false = a durable phase is on the
// journal (terminal, skip, conflict, uncertain) or none was ever owed.
// In particular a Finish failure records NOTHING — a receipted record
// written despite a failed store write would falsely close the phase.
func (s *Sink) doReceipt(ctx context.Context, env *Envelope, canonicalID string) (retry bool) {
	if env.Receipt == nil {
		return false
	}
	emoji, _ := env.Receipt["emoji"].(string)
	if emoji == "" {
		return false
	}
	if canonicalID == "" {
		canonicalID = env.EventID
	}
	record := func(state string) {
		rec, _ := json.Marshal(map[string]any{
			"type": recReceipted, "event_id": env.EventID,
			"canonical_event_id": canonicalID, "state": state,
		})
		if err := s.journal.Append(rec); err != nil {
			s.halt(fmt.Sprintf("journal receipt record failed: %v", err))
		}
	}
	if s.receipt == nil {
		// No reaction sender is installed yet — the intent stays pending.
		// Retrying keeps a durable phase from lying (a terminal `skipped`
		// here would close a receipt that was never actually declined).
		return true
	}
	// Current-policy re-check BEFORE any reservation or effect: the grant
	// held at capture, but the CURRENT projection rules — a revoked room
	// list, a removed sender, a dropped field or a disabled policy
	// suppresses the pending receipt now, journaled as a terminal phase.
	var proj *Projection
	if s.projection != nil {
		proj = s.projection.Current()
	}
	sender, _ := env.Receipt["sender"].(string)
	if sender == "" {
		sender = env.SenderID
	}
	if !proj.ReceiptGranted(env.ConversationID, env.ConversationType, sender) {
		s.metrics.Count("receipt_skipped")
		record("skipped_revoked")
		return false
	}
	if s.actions == nil {
		s.metrics.Count("receipt_skipped")
		slog.Warn("receipt skipped: action store not configured; journaled as skipped")
		record("skipped_no_store")
		return false
	}
	ownerID := canonicalID
	actionID := ownerID + ":react:0"
	convType := env.ConversationType
	if convType == "" {
		convType = "group"
	}
	request := map[string]any{
		"target":        map[string]any{"kind": convType, "id": env.ConversationID},
		"emoji":         emoji,
		"to_message_id": messageIDOf(env),
	}
	hash, canon, err := RequestHash(request)
	if err != nil {
		slog.Error("receipt request canonicalization failed", "error", errClass(err))
		return true
	}
	outcome, row, err := s.actions.Reserve(ctx, actionID, OwnerEventReceipt, ownerID,
		nil, "react", canon, hash)
	if err != nil {
		s.metrics.Count("receipt_failed")
		slog.Warn("receipt reservation failed; lane retries", "kind", errClass(err))
		return true
	}
	switch outcome {
	case ReserveReplay:
		// Later progress suppresses the queued phase: terminal states
		// record as themselves; a `started` row means the platform call
		// may already have fired in a previous life — uncertain, never
		// re-fired.
		state := ActionUncertain
		if row != nil && row.State != ActionStarted {
			state = row.State
		}
		record(state)
		return false
	case ReserveConflict:
		slog.Warn("receipt reservation conflicted; journaled terminal")
		record("conflict")
		return false
	}
	// Fresh (or resuming a `prepared`) reservation: take the start fence.
	started, err := s.actions.MarkStarted(ctx, actionID, nil)
	if err != nil {
		return true
	}
	if !started {
		// Someone else holds the start — for a bridge-owned deterministic
		// id that only means a prior life already started; the answer may
		// have landed. Uncertain, not a re-fire.
		record(ActionUncertain)
		return false
	}
	_, span := s.queueSpan(ctx, "bridge.receipt", env,
		attribute.String("bridge.platform", env.Platform))
	err = s.receipt(ctx, env.ConversationID, messageIDOf(env), emoji)
	// A lost answer is uncertain, not a retry candidate; a definite refusal
	// (checkpoint) is rejected. Both are terminal for the queued receipt.
	state, errKind := ActionUncertain, "upstream"
	if errors.Is(err, domain.ErrNeedsHuman) {
		state, errKind = ActionRejected, "checkpoint"
	}
	if err == nil {
		state, errKind = ActionConfirmed, ""
	}
	errMsg := ""
	if err != nil {
		errMsg = "receipt failed"
	}
	if ferr := s.actions.Finish(ctx, actionID, state, messageIDOf(env), nil, errKind, errMsg); ferr != nil {
		// The action row is still `started` and the journal has no phase:
		// retry — the replay reads `started` and lands on uncertain, which
		// is the honest state for a possibly-fired reaction.
		span.SetStatus(codes.Error, errClass(ferr))
		span.End()
		return true
	}
	if err != nil {
		span.SetStatus(codes.Error, errKind)
	}
	span.End()
	record(state)
	if err == nil {
		s.metrics.Count("receipt_sent")
	} else {
		s.metrics.Count("receipt_failed")
	}
	return false
}

// injectTrace mints the capture's producer span and writes its W3C context
// into the envelope BEFORE the journal append — the durable record and the
// published payload carry the same trace identity downstream continues
// from. An invalid extraction clears the field rather than publishing a
// malformed context.
func (s *Sink) injectTrace(env *Envelope) {
	spanCtx, span := observability.Tracer().Start(context.Background(), "bridge.capture",
		trace.WithSpanKind(trace.SpanKindProducer),
		trace.WithAttributes(
			attribute.String("bridge.platform", env.Platform),
			attribute.String("bridge.kind", env.Kind),
			attribute.String("nora.trace_kind", "queue"),
		))
	carrier := propagation.MapCarrier{}
	propagation.TraceContext{}.Inject(spanCtx, carrier)
	if env.Trace == nil {
		env.Trace = map[string]any{}
	}
	for k, v := range carrier {
		env.Trace[k] = v
	}
	// Validate what was written: a context that cannot round-trip is
	// dropped, not published.
	check := propagation.TraceContext{}.Extract(context.Background(), carrier)
	if !trace.SpanContextFromContext(check).IsValid() {
		delete(env.Trace, "traceparent")
		delete(env.Trace, "tracestate")
	}
	span.End()
}

// queueSpan opens a NEW bounded producer root for one queue-side
// operation (enqueue attempt, custody fetch, receipt), linked back to the
// event's capture context. `nora.trace_kind=queue` is set AT CREATION —
// the collector's queue-kind routing reads span-head attributes, so a
// post-Start SetAttributes lands too late. A legacy envelope without a
// trace context still gets a root; the link is simply absent — a nil
// SpanStartOption must never reach Start (it panics on apply).
func (s *Sink) queueSpan(ctx context.Context, name string, env *Envelope, attrs ...attribute.KeyValue) (context.Context, trace.Span) {
	opts := []trace.SpanStartOption{
		trace.WithNewRoot(),
		trace.WithSpanKind(trace.SpanKindProducer),
		trace.WithAttributes(append(
			[]attribute.KeyValue{attribute.String("nora.trace_kind", "queue")},
			attrs...)...),
	}
	if l := s.traceLink(env); l != nil {
		opts = append(opts, l)
	}
	return observability.Tracer().Start(ctx, name, opts...)
}

// traceLink resolves the envelope's capture-time context into a span link
// for bounded downstream spans (enqueue attempts, custody fetches,
// receipts): every operation for one event names the same lineage without
// pretending the capture span is still open.
func (s *Sink) traceLink(env *Envelope) trace.SpanStartOption {
	if env == nil || env.Trace == nil {
		return nil
	}
	carrier := propagation.MapCarrier{}
	for k, v := range env.Trace {
		if sv, ok := v.(string); ok {
			carrier[k] = sv
		}
	}
	ext := propagation.TraceContext{}.Extract(context.Background(), carrier)
	sc := trace.SpanContextFromContext(ext)
	if !sc.IsValid() {
		return nil
	}
	return trace.WithLinks(trace.Link{SpanContext: sc})
}

func messageIDOf(env *Envelope) string {
	if v, ok := env.Message["message_id"].(string); ok {
		return v
	}
	return ""
}

// stringList reads a normalized message field that arrives as []string
// live and []any after a journal round-trip.
func stringList(v any) []string {
	switch l := v.(type) {
	case []string:
		return l
	case []any:
		out := make([]string, 0, len(l))
		for _, e := range l {
			if s, ok := e.(string); ok {
				out = append(out, s)
			}
		}
		return out
	}
	return nil
}

// receiptAddressed is the deterministic half of receipt eligibility: the
// normalized message must name Nora — a person-mention of a self alias, a
// reply quoting a self alias, or a deterministic trigger string in the
// body. Returns the channel ("mention"|"reply"|"trigger") or "" — no
// inference, no model judgement: a miss is a deny.
func receiptAddressed(env *Envelope, p *Projection) string {
	if p == nil {
		return ""
	}
	for _, m := range stringList(env.Message["mentions"]) {
		if member(p.SelfAliases, m) {
			return "mention"
		}
	}
	if q, ok := env.Message["quote"].(map[string]any); ok {
		if member(p.SelfAliases, str(q, "author_id")) {
			return "reply"
		}
	}
	body, _ := env.Message["body"].(string)
	for _, tg := range p.DeterministicTriggers {
		if tg != "" && strings.Contains(body, tg) {
			return "trigger"
		}
	}
	return ""
}

func revPtr(p *Projection) *int {
	if p == nil {
		return nil
	}
	return &p.Revision
}
