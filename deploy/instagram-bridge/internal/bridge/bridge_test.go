package bridge

// Focused contract proofs for the durable-capture half. Every test here
// guards a REAL production seam: identity minting, the fsynced journal, the
// frozen Lua text, admission, and the emit ordering. A break in any of them
// turns a named test red.

import (
	"context"
	"crypto/sha1"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	collectortracepb "go.opentelemetry.io/proto/otlp/collector/trace/v1"
	"google.golang.org/protobuf/proto"

	"github.com/jackc/pgx/v5/pgxpool"
)

// roundTripFunc is an http.RoundTripper for custody-fetch control: counting
// calls proves a replay never re-fetches an already-spooled object.
type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

// --- identity ---------------------------------------------------------------

// The canonical digest is sha256 of the source key and is INDEPENDENT of the
// correlation key/version — that is what makes rotation safe. This vector is
// computed in-test from the same construction the contract freezes, so a
// drift in separator or field order breaks it.
func TestCanonicalDigestIsKeyIndependent(t *testing.T) {
	key := SourceKey("instagram", "acct-1", "conv-42", "message", "1730300000000+1234")
	if got, want := key, "instagram\x1facct-1\x1fconv-42\x1fmessage\x1f1730300000000+1234"; got != want {
		t.Fatalf("source key join drifted: %q", got)
	}
	sum := sha256.Sum256([]byte(key))
	want := hex.EncodeToString(sum[:])
	if got := CanonicalDigest(key); got != want {
		t.Fatalf("canonical digest = %s, want sha256(source_key) = %s", got, want)
	}
}

// Event ids are versioned HMAC aliases: same source, different key or
// version → different id, same canonical digest.
func TestEventIDAliasSemantics(t *testing.T) {
	sk := SourceKey("instagram", "a", "c", "message", "mid:1")
	k1, _ := CorrelationKey("0011223344556677889900112233445566778899001122334455667788990011")
	k2, _ := CorrelationKey("ffeeffeeffeeffeeffeeffeeffeeffeeffeeffeeffeeffeeffeeffeeffeeffee")
	id1 := EventID(sk, k1, 1)
	if !strings.HasPrefix(id1, "v1:") || len(id1) != 3+64 {
		t.Fatalf("event id shape wrong: %q", id1)
	}
	if EventID(sk, k1, 2) == id1 {
		t.Fatal("version bump must mint a new alias")
	}
	if EventID(sk, k2, 1) == id1 {
		t.Fatal("key rotation must mint a new alias")
	}
	if EventID(sk, k1, 1) != id1 {
		t.Fatal("event id must be deterministic")
	}
}

// Every required wire field is present in the marshaled envelope — the
// contract names them all, nulls included.
func TestEnvelopeRequiredFields(t *testing.T) {
	env := &Envelope{
		V: 1, Platform: "instagram", AccountKey: "a", Kind: "message",
		SourceKey: "s", EventID: "v1:x", ConversationID: "c",
		ConversationType: "dm", SenderID: "s1",
		SentMS: 1, ReceivedMS: 2, QueuedMS: 3, TimeQuality: TimeUpstream,
		Message: map[string]any{}, Attachments: []Attachment{},
		Trace: map[string]any{}, Receipt: nil, AdmissionRev: nil,
	}
	raw, err := env.Marshal()
	if err != nil {
		t.Fatal(err)
	}
	var m map[string]any
	if err := json.Unmarshal(raw, &m); err != nil {
		t.Fatal(err)
	}
	for _, f := range []string{"v", "platform", "account_key", "kind", "source_key",
		"event_id", "conversation_id", "conversation_type", "sender_id",
		"sender_name", "sent_ms", "received_ms", "queued_ms", "time_quality",
		"message", "attachments", "trace", "receipt", "admission_rev"} {
		if _, ok := m[f]; !ok {
			t.Fatalf("envelope missing required field %q", f)
		}
	}
	if _, ok := m["receipt"]; !ok {
		t.Fatal("receipt must serialize even when null")
	}
	if _, ok := m["admission_rev"]; !ok {
		t.Fatal("admission_rev must serialize even when null")
	}
}

// --- queue ------------------------------------------------------------------

// The frozen Lua text's sha1 is the contract's cross-language pin.
func TestEnqueueScriptSHA1(t *testing.T) {
	sum := sha1.Sum([]byte(EnqueueScript))
	if got := hex.EncodeToString(sum[:]); got != "1f85b82d28739797b95784c0b7c3a118007ecadd" {
		t.Fatalf("enqueue script sha1 = %s, contract pins 1f85b82d28739797b95784c0b7c3a118007ecadd", got)
	}
	if !strings.Contains(EnqueueScript, `'e', ARGV[1]`) {
		t.Fatal("script must write the single field 'e'")
	}
	if !strings.Contains(EnqueueScript, `return {prior, orig}`) {
		t.Fatal("script must return {stream_id, canonical_event_id}")
	}
}

// Key namespaces are the contract's, verified against a constructed queue.
func TestQueueNamespaces(t *testing.T) {
	q, err := NewQueue("redis://localhost:6379/0", "instagram", "acct-9", "nora-v1")
	if err != nil {
		t.Fatal(err)
	}
	defer q.Close()
	if q.stream != "nq:in:instagram:acct-9" {
		t.Fatalf("stream = %q, want nq:in:instagram:acct-9", q.stream)
	}
	if q.dedupe != "nq:dedupe:" {
		t.Fatalf("dedupe prefix = %q", q.dedupe)
	}
	if q.src != "nq:src:" {
		t.Fatalf("src prefix = %q", q.src)
	}
	if q.group != "nora-v1" {
		t.Fatalf("group = %q", q.group)
	}
}

// --- journal ----------------------------------------------------------------

func appendRec(t *testing.T, j *Journal, payload string) {
	t.Helper()
	if err := j.Append(json.RawMessage(payload)); err != nil {
		t.Fatalf("append %q: %v", payload, err)
	}
}

func TestJournalRoundTrip(t *testing.T) {
	j, err := OpenJournal(filepath.Join(t.TempDir(), "j.log"), 0)
	if err != nil {
		t.Fatal(err)
	}
	if err := j.Probe(); err != nil {
		t.Fatalf("probe: %v", err)
	}
	appendRec(t, j, `{"type":"event","n":1}`)
	appendRec(t, j, `{"type":"event","n":2}`)
	recs, repaired, err := j.Recover()
	if err != nil {
		t.Fatal(err)
	}
	if repaired || len(recs) != 2 {
		t.Fatalf("recover = %d records repaired=%v, want 2/false", len(recs), repaired)
	}
}

// A torn tail truncates and replays the intact prefix.
func TestJournalTornTail(t *testing.T) {
	path := filepath.Join(t.TempDir(), "j.log")
	j, err := OpenJournal(path, 0)
	if err != nil {
		t.Fatal(err)
	}
	appendRec(t, j, `{"n":1}`)
	appendRec(t, j, `{"n":2}`)
	f, _ := os.OpenFile(path, os.O_WRONLY|os.O_APPEND, 0o600)
	f.WriteString("999 deadbeef {\"n\":3") // no newline, bad checksum, partial json
	f.Close()
	j2, _ := OpenJournal(path, 0)
	recs, repaired, err := j2.Recover()
	if err != nil {
		t.Fatal(err)
	}
	if !repaired || len(recs) != 2 {
		t.Fatalf("torn tail recover = %d records repaired=%v, want 2/true", len(recs), repaired)
	}
	// And the file must actually be truncated — a second recover is clean.
	recs2, repaired2, err := j2.Recover()
	if err != nil || repaired2 || len(recs2) != 2 {
		t.Fatalf("post-truncate recover = %d/%v/%v", len(recs2), repaired2, err)
	}
}

// Interior corruption REFUSES — later records are never silently dropped.
func TestJournalInteriorCorruption(t *testing.T) {
	path := filepath.Join(t.TempDir(), "j.log")
	j, err := OpenJournal(path, 0)
	if err != nil {
		t.Fatal(err)
	}
	appendRec(t, j, `{"n":1}`)
	f, _ := os.OpenFile(path, os.O_WRONLY|os.O_APPEND, 0o600)
	f.WriteString("999 deadbeef {garbage\n")
	f.Close()
	appendRec(t, j, `{"n":2}`)
	j.size = 0 // size is stale after the manual append; Recover must not rely on it for this check
	j2, _ := OpenJournal(path, 0)
	if _, _, err := j2.Recover(); err == nil {
		t.Fatal("interior corruption must refuse recovery")
	} else {
		var je *JournalError
		if !errors.As(err, &je) {
			t.Fatalf("want JournalError, got %T", err)
		}
	}
}

// Capacity is a hard bound: a full journal is a JournalError, never an
// oldest-drop.
func TestJournalCapacityBound(t *testing.T) {
	j, err := OpenJournal(filepath.Join(t.TempDir(), "j.log"), 100)
	if err != nil {
		t.Fatal(err)
	}
	var capErr error
	for i := 0; i < 100; i++ {
		if err := j.Append(json.RawMessage(fmt.Sprintf(`{"n":%d}`, i))); err != nil {
			capErr = err
			break
		}
	}
	if capErr == nil {
		t.Fatal("capacity bound never engaged")
	}
	var je *JournalError
	if !errors.As(capErr, &je) {
		t.Fatalf("capacity error must be JournalError, got %T", capErr)
	}
}

// --- projection --------------------------------------------------------------

func TestProjectionAdmit(t *testing.T) {
	emoji := "👍"
	p := &Projection{Revision: 3, Enabled: true, Rooms: []string{"room-a"},
		DMAllowedSenders: []string{"person-b"}, ReceiptEmoji: &emoji,
		RoomReceiptSenders: map[string][]string{"room-a": {"member-1", "member-2"}}}
	if ok, _ := p.Admit("room-a", "group", "anyone"); !ok {
		t.Fatal("allowed room must admit for capture")
	}
	// Admission is NOT a receipt grant: a room member is captured but the
	// sender grant still has to come from room_receipt_senders.
	if _, rc := p.Admit("room-a", "group", "anyone"); rc {
		t.Fatal("admitted room alone must not grant a receipt")
	}
	if _, rc := p.Admit("room-a", "group", "member-1"); !rc {
		t.Fatal("room_receipt_senders membership grants")
	}
	if ok, _ := p.Admit("room-b", "group", "anyone"); ok {
		t.Fatal("unlisted room must deny")
	}
	if ok, _ := p.Admit("room-b", "dm", "person-b"); !ok {
		t.Fatal("dm_allowed_senders must admit a DM sender")
	}
	if ok, _ := p.Admit("room-b", "group", "person-b"); ok {
		t.Fatal("dm sender must not admit a group room")
	}
	off := &Projection{Enabled: false, Rooms: []string{"room-a"}}
	if ok, _ := off.Admit("room-a", "group", "x"); ok {
		t.Fatal("disabled projection denies everything")
	}
	var nilP *Projection
	if ok, _ := nilP.Admit("room-a", "group", "x"); ok {
		t.Fatal("no projection denies everything")
	}
}

// The grant halves, straight from the contract's rule text.
func TestProjectionReceiptGranted(t *testing.T) {
	p := &Projection{Enabled: true, Rooms: []string{"room-a"},
		DMAllowedSenders:   []string{"dm-person"},
		RoomReceiptSenders: map[string][]string{"room-a": {"s-1"}, "wild": {"*"}}}
	cases := []struct {
		conv, typ, sender string
		want              bool
	}{
		{"room-a", "group", "s-1", true},     // listed member
		{"room-a", "group", "s-2", false},    // unlisted member
		{"wild", "group", "anyone", true},    // literal-* policy wildcard
		{"room-b", "group", "s-1", false},    // missing room entry = deny
		{"dm1", "dm", "dm-person", true},     // DM grant binds on dm type
		{"dm1", "dm", "other", false},        // unlisted DM sender
		{"room-a", "dm", "s-1", false},       // group list is not a DM grant
		{"dm1", "group", "dm-person", false}, // DM list is not a group grant
	}
	for _, c := range cases {
		if got := p.ReceiptGranted(c.conv, c.typ, c.sender); got != c.want {
			t.Errorf("ReceiptGranted(%q,%q,%q) = %v, want %v", c.conv, c.typ, c.sender, got, c.want)
		}
	}
	// No room_receipt_senders field at all → every group grant denies.
	bare := &Projection{Enabled: true, Rooms: []string{"room-a"}}
	if bare.ReceiptGranted("room-a", "group", "s-1") {
		t.Fatal("missing room_receipt_senders must deny group receipts")
	}
}

// revision survives a JSON round-trip exactly — the contract's bound is
// 2^53-1; a float64 anywhere in the path would round this off.
func TestProjectionRevisionExact(t *testing.T) {
	var p Projection
	if err := json.Unmarshal([]byte(`{"revision":9007199254740991,"enabled":true}`), &p); err != nil {
		t.Fatal(err)
	}
	if p.Revision != 9007199254740991 {
		t.Fatalf("revision decoded as %d, want exact 9007199254740991", p.Revision)
	}
}

// --- normalize ---------------------------------------------------------------

var testKey = []byte("0123456789abcdef0123456789abcdef")

func msgEvent() map[string]any {
	return map[string]any{
		"kind": "message", "room_id": "room-1", "is_group": true,
		"message_id": "mid-1", "text": "hello @alice",
		"timestamp_ms": float64(1730300000000),
		"sender":       map[string]any{"fbid": "fb-9", "username": "bob"},
		"mentions": []any{
			map[string]any{"fbid": "fb-1", "offset": 6, "length": 6},
			map[string]any{"fbid": "fb-2", "range_type": "THREAD", "offset": 0, "length": 0},
		},
	}
}

func TestNormalizeMessage(t *testing.T) {
	n, err := Normalize(msgEvent(), "acct", testKey, 1, 1730300001000)
	if err != nil || n == nil {
		t.Fatalf("normalize = %v/%v", n, err)
	}
	env := n.Env
	if env.Kind != KindMessage || env.ConversationID != "room-1" || env.SenderID != "fb-9" {
		t.Fatalf("envelope core wrong: %+v", env)
	}
	if env.TimeQuality != TimeUpstream || env.SentMS != 1730300000000 {
		t.Fatal("upstream timestamp must be used")
	}
	// Person mentions only: the THREAD-range entry carries the chat id, not
	// a person's, and must not land in mentions.
	menRaw, _ := json.Marshal(env.Message["mentions"])
	if string(menRaw) != `["fb-1"]` {
		t.Fatalf("mentions = %s, want [fb-1]", menRaw)
	}
	if !strings.HasPrefix(env.SourceKey, "instagram\x1facct\x1froom-1\x1fmessage\x1fmid:mid-1") &&
		env.SourceKey != SourceKey("instagram", "acct", "room-1", "message", "mid:mid-1") {
		t.Fatalf("source key wrong: %q", env.SourceKey)
	}
}

func TestNormalizeMessageDrops(t *testing.T) {
	ev := msgEvent()
	ev["from_me"] = true
	if n, _ := Normalize(ev, "a", testKey, 1, 1); n != nil {
		t.Fatal("from_me echo must drop")
	}
	ev = msgEvent()
	ev["text"] = ""
	if n, _ := Normalize(ev, "a", testKey, 1, 1); n != nil {
		t.Fatal("empty turn must drop")
	}
	ev = msgEvent()
	delete(ev, "room_id")
	if n, _ := Normalize(ev, "a", testKey, 1, 1); n != nil {
		t.Fatal("missing room must drop")
	}
	for _, k := range []string{"heartbeat", "typing", "dropped", "unknown"} {
		if n, _ := Normalize(map[string]any{"kind": k}, "a", testKey, 1, 1); n != nil {
			t.Fatalf("kind %q must drop", k)
		}
	}
}

func TestNormalizeUnsendIsDelete(t *testing.T) {
	n, err := Normalize(map[string]any{
		"kind": "unsend", "room_id": "r", "message_id": "mid-9",
		"sender_fbid": "fb-1", "timestamp_ms": float64(5),
	}, "a", testKey, 1, 9)
	if err != nil || n == nil {
		t.Fatal("unsend must normalize")
	}
	if n.Env.Kind != KindDelete {
		t.Fatalf("unsend must map to delete, got %q", n.Env.Kind)
	}
	touch, _ := n.Env.Message["touch"].(map[string]any)
	if touch["kind"] != "delete" {
		t.Fatalf("touch.kind = %v", touch["kind"])
	}
}

func TestNormalizeReactionIdentity(t *testing.T) {
	add := func(removed bool) *Normalized {
		n, _ := Normalize(map[string]any{
			"kind": "reaction", "room_id": "r", "message_id": "mid-1",
			"sender_fbid": "fb-1", "emoji": "❤️", "removed": removed,
			"timestamp_ms": float64(10),
		}, "a", testKey, 1, 9)
		return n
	}
	a, r := add(false), add(true)
	if a.Env.Kind != KindReaction || r.Env.Kind != KindReaction {
		t.Fatal("reaction events must stay kind=reaction")
	}
	if a.Env.SourceKey == r.Env.SourceKey {
		t.Fatal("add and remove must have distinct upstream identities")
	}
	// Clockless reactions go through the transition ledger.
	n, _ := Normalize(map[string]any{
		"kind": "reaction", "room_id": "r", "message_id": "mid-1",
		"sender_fbid": "fb-1", "emoji": "❤️", "removed": false,
	}, "a", testKey, 1, 9)
	if len(n.Transitions) != 1 {
		t.Fatal("ts==0 reaction must produce a transition")
	}
	// from_me reaction drops.
	if n, _ := Normalize(map[string]any{
		"kind": "reaction", "room_id": "r", "message_id": "m", "from_me": true,
	}, "a", testKey, 1, 9); n != nil {
		t.Fatal("from_me reaction must drop")
	}
}

func TestNormalizeEditAndSummons(t *testing.T) {
	e, _ := Normalize(map[string]any{
		"kind": "edit", "room_id": "r", "message_id": "mid-1",
		"text": "edited words", "timestamp_ms": float64(7),
	}, "a", testKey, 1, 9)
	if e == nil || e.Env.Kind != KindEdit {
		t.Fatal("edit must normalize to kind=edit")
	}
	eo, _ := e.Env.Message["edit_of"].(map[string]any)
	if eo["message_id"] != "mid-1" {
		t.Fatal("edit_of must name the target message")
	}
	s, _ := Normalize(map[string]any{
		"kind": "summons", "room_id": "r", "message_id": "cid-1",
		"text": "hey nora", "received_at_ms": float64(11),
		"sender": map[string]any{"fbid": "fb-7", "username": "x"},
	}, "a", testKey, 1, 9)
	if s == nil || s.Env.Kind != KindSummons {
		t.Fatal("summons must stay kind=summons")
	}
}

// Shared post renders the [shared …] line; stranger `]` is sanitized.
func TestNormalizeSharedPost(t *testing.T) {
	ev := msgEvent()
	ev["shared"] = map[string]any{
		"author":     "@poster",
		"target_url": "https://www.instagram.com/reel/ABC123/",
		"caption":    "look ]at[ this",
	}
	n, err := Normalize(ev, "a", testKey, 1, 1730300001000)
	if err != nil || n == nil {
		t.Fatal(err)
	}
	body, _ := n.Env.Message["body"].(string)
	if !strings.Contains(body, "[shared reel by @poster https://www.instagram.com/reel/ABC123/") {
		t.Fatalf("shared line missing/wrong: %q", body)
	}
	if strings.Contains(body, "]at[") {
		t.Fatalf("stranger brackets must be sanitized: %q", body)
	}
}

// --- custody -----------------------------------------------------------------

func TestCustodyPutGetHash(t *testing.T) {
	c, err := NewCustody(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	rec, err := c.Put("k1", strings.NewReader("hello bytes"), "application/octet-stream", "", "event-1")
	if err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256([]byte("hello bytes"))
	if rec.SHA256 != hex.EncodeToString(sum[:]) || rec.Size != 11 {
		t.Fatalf("custody meta wrong: %+v", rec)
	}
	got, data, ok := c.Get("k1")
	if !ok || string(data) != "hello bytes" || got.SHA256 != rec.SHA256 {
		t.Fatal("custody get mismatch")
	}
	if _, _, ok := c.Get("nope"); ok {
		t.Fatal("unknown key must 404")
	}
	if _, err := c.Put("../escape", strings.NewReader("x"), "application/octet-stream", "", "e"); err == nil {
		t.Fatal("invalid key must refuse")
	}
}

// --- capture pipeline ---------------------------------------------------------

// fakeQueue models the frozen Lua's dedupe faithfully — a fake that answers
// every call the same way proves nothing, so this one resolves the
// source_key to the ORIGINAL alias first (rotation-safe), then the
// submitted alias, exactly like the script.
type fakeQueue struct {
	mu     sync.Mutex
	ids    []string
	envs   [][]byte
	failN  int
	down   atomic.Bool // simulated outage: every call fails while set
	src    map[string]string
	dedupe map[string]string
}

func (f *fakeQueue) EnsureGroup(context.Context) error { return nil }
func (f *fakeQueue) Enqueue(_ context.Context, payload []byte, sourceKey, eventID string) (string, string, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.down.Load() || f.failN > 0 {
		f.failN--
		return "", "", errors.New("redis down")
	}
	if f.src == nil {
		f.src = map[string]string{}
		f.dedupe = map[string]string{}
	}
	// The Lua's three cases, verbatim semantics:
	if orig, ok := f.src[sourceKey]; ok {
		if prior, ok := f.dedupe[orig]; ok {
			return prior, orig, nil
		}
	}
	if prior, ok := f.dedupe[eventID]; ok {
		f.src[sourceKey] = eventID
		return prior, eventID, nil
	}
	id := fmt.Sprintf("1-%d", len(f.ids)+1)
	f.ids = append(f.ids, id)
	f.envs = append(f.envs, append([]byte(nil), payload...))
	f.dedupe[eventID] = id
	f.src[sourceKey] = eventID
	return id, eventID, nil
}
func (f *fakeQueue) count() int { f.mu.Lock(); defer f.mu.Unlock(); return len(f.ids) }

// fakeActions mimics bridge_action for the receipt path: hash+owner-bound
// reservation, prepared→started CAS, terminal states replay. failReserve /
// failFinish inject N transient infrastructure failures, letting the
// receipt-lane retry semantics be exercised for real.
type fakeActions struct {
	mu          sync.Mutex
	rows        map[string]*ActionRow
	failReserve int
	failFinish  int
}

func newFakeActions() *fakeActions { return &fakeActions{rows: map[string]*ActionRow{}} }

func (f *fakeActions) Reserve(_ context.Context, id, ok, oid string, og *int, op string, req json.RawMessage, hash string) (ReserveOutcome, *ActionRow, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failReserve > 0 {
		f.failReserve--
		return ReserveConflict, nil, errors.New("pg down")
	}
	if row, seen := f.rows[id]; seen {
		if row.RequestHash != hash || row.OwnerKind != ok || row.OwnerID != oid || row.Op != op {
			return ReserveConflict, row, nil
		}
		if row.State == ActionPrepared {
			return ReserveExecute, row, nil
		}
		return ReserveReplay, row, nil
	}
	row := &ActionRow{ActionID: id, OwnerKind: ok, OwnerID: oid, OwnerGeneration: og,
		Op: op, Request: req, RequestHash: hash, State: ActionPrepared}
	f.rows[id] = row
	return ReserveExecute, row, nil
}
func (f *fakeActions) MarkStarted(_ context.Context, id string, _ *int64) (bool, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	r := f.rows[id]
	if r == nil || r.State != ActionPrepared {
		return false, nil
	}
	r.State = ActionStarted
	return true, nil
}
func (f *fakeActions) Finish(_ context.Context, id, state, ref string, ms *int64, ek, em string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failFinish > 0 {
		f.failFinish--
		return errors.New("pg down")
	}
	r := f.rows[id]
	r.State = state
	if ref != "" {
		r.PlatformRef = &ref
	}
	return nil
}

type fakeCompat struct {
	mu      sync.Mutex
	emitted []any
}

func (f *fakeCompat) Emit(e any)       { f.mu.Lock(); f.emitted = append(f.emitted, e); f.mu.Unlock() }
func (f *fakeCompat) HasClients() bool { return true }
func (f *fakeCompat) count() int       { f.mu.Lock(); defer f.mu.Unlock(); return len(f.emitted) }

func newTestSink(t *testing.T, q *fakeQueue, compat *fakeCompat, projDir string) *Sink {
	t.Helper()
	j, err := OpenJournal(filepath.Join(t.TempDir(), "j.log"), 0)
	if err != nil {
		t.Fatal(err)
	}
	proj := NewProjectionSource(projDir, "instagram")
	proj.Poll(context.Background())
	return NewSink(SinkConfig{
		AccountKey: "acct", Key: testKey, KeyVersion: 1,
		Journal: j, Queue: q, Projection: proj, Compat: compat,
		Actions: newFakeActions(),
	})
}

// writeProjection publishes a projection whose listed rooms ALSO wildcard-
// grant receipts (room_receipt_senders {"*"} per room), with fb-1 as the
// self alias — the fixture legacy receipt tests need: msgEvent's mention of
// fb-1 is the deterministic addressed signal. Denied/narrow-grant cases
// write their own document via writeProjectionDoc.
func writeProjection(t *testing.T, dir string, rooms []string, emoji *string) {
	t.Helper()
	senders := map[string]any{}
	for _, r := range rooms {
		senders[r] = []any{"*"}
	}
	writeProjectionDoc(t, dir, map[string]any{
		"revision": 1, "generated_ms": 1, "enabled": true, "account_key": "acct",
		"rooms": rooms, "receipt_emoji": emoji,
		"self_aliases":           []any{"fb-1"},
		"deterministic_triggers": []any{"nora"},
		"room_receipt_senders":   senders,
	})
}

func writeProjectionDoc(t *testing.T, dir string, doc map[string]any) {
	t.Helper()
	raw, _ := json.Marshal(doc)
	if err := os.WriteFile(filepath.Join(dir, "instagram.json"), raw, 0o600); err != nil {
		t.Fatal(err)
	}
	// mtime tick so Poll sees it as fresh
	os.Chtimes(filepath.Join(dir, "instagram.json"), time.Now(), time.Now().Add(time.Second))
}

// The end-to-end order: emit → journal → enqueue → compat, and the stream
// payload is the exact v1 envelope.
func TestSinkEmitEnqueuesThenCompat(t *testing.T) {
	dir := t.TempDir()
	emoji := "👀"
	writeProjection(t, dir, []string{"room-1"}, &emoji)
	q, compat := &fakeQueue{}, &fakeCompat{}
	s := newTestSink(t, q, compat, dir)
	var receiptCalls []string
	var mu sync.Mutex
	s.SetReceipt(func(_ context.Context, room, mid, em string) error {
		mu.Lock()
		receiptCalls = append(receiptCalls, room+"|"+mid+"|"+em)
		mu.Unlock()
		return nil
	})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	if err := s.Start(ctx); err != nil {
		t.Fatal(err)
	}
	if !s.UpstreamOK() {
		t.Fatal("gate must open with journal + valid projection")
	}
	s.Emit(msgEvent())
	deadline := time.Now().Add(3 * time.Second)
	for q.count() == 0 && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	if q.count() != 1 {
		t.Fatal("event never enqueued")
	}
	// compat fires after durable enqueue.
	for compat.count() == 0 && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	if compat.count() != 1 {
		t.Fatal("compat hub never emitted")
	}
	// The stream payload is the exact v1 envelope with admission_rev.
	var env map[string]any
	if err := json.Unmarshal(q.envs[0], &env); err != nil {
		t.Fatal(err)
	}
	if env["admission_rev"].(float64) != 1 {
		t.Fatalf("admission_rev = %v", env["admission_rev"])
	}
	// Receipt after enqueue, once.
	for i := 0; i < 50 && len(receiptCalls) == 0; i++ {
		time.Sleep(10 * time.Millisecond)
	}
	mu.Lock()
	defer mu.Unlock()
	if len(receiptCalls) != 1 || receiptCalls[0] != "room-1|mid-1|👀" {
		t.Fatalf("receipt = %v, want one call room-1|mid-1|👀", receiptCalls)
	}
}

// A denied room is never journaled and never enqueued — no body capture.
func TestSinkAdmissionDenied(t *testing.T) {
	dir := t.TempDir()
	writeProjection(t, dir, []string{"other-room"}, nil)
	q, compat := &fakeQueue{}, &fakeCompat{}
	s := newTestSink(t, q, compat, dir)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	if err := s.Start(ctx); err != nil {
		t.Fatal(err)
	}
	s.Emit(msgEvent())
	time.Sleep(200 * time.Millisecond)
	if q.count() != 0 || compat.count() != 0 {
		t.Fatal("denied event must not enqueue or fan out")
	}
	recs, _, _ := s.journal.Recover()
	if len(recs) != 0 {
		t.Fatal("denied event must not be journaled")
	}
}

// Restart replay: an event journaled but never confirmed enqueues on the
// next boot; a confirmed one does NOT re-enqueue (no blind duplicate).
func TestSinkRestartReplay(t *testing.T) {
	dir := t.TempDir()
	writeProjection(t, dir, []string{"room-1"}, nil)
	jpath := filepath.Join(t.TempDir(), "j.log")

	// Boot 1: journal one event and its enqueue confirmation, then one
	// event with NO confirmation (crash between XADD and record — or Redis
	// down before the attempt).
	j1, _ := OpenJournal(jpath, 0)
	env1 := &Envelope{V: 1, Platform: "instagram", AccountKey: "acct", Kind: "message",
		SourceKey: "k1", EventID: "v1:done", ConversationID: "room-1",
		Message: map[string]any{}, Attachments: []Attachment{}, Trace: map[string]any{}}
	env2 := &Envelope{V: 1, Platform: "instagram", AccountKey: "acct", Kind: "message",
		SourceKey: "k2", EventID: "v1:pending", ConversationID: "room-1",
		Message: map[string]any{}, Attachments: []Attachment{}, Trace: map[string]any{}}
	r1, _ := json.Marshal(map[string]any{"type": recEvent, "envelope": env1})
	r2, _ := json.Marshal(map[string]any{"type": recEnqueued, "event_id": "v1:done",
		"canonical_event_id": "v1:done", "stream_id": "1-1"})
	r3, _ := json.Marshal(map[string]any{"type": recEvent, "envelope": env2})
	j1.Append(r1)
	j1.Append(r2)
	j1.Append(r3)

	// Boot 2: replay must enqueue exactly the unconfirmed one.
	j2, _ := OpenJournal(jpath, 0)
	q := &fakeQueue{}
	s := NewSink(SinkConfig{AccountKey: "acct", Key: testKey, KeyVersion: 1,
		Journal: j2, Queue: q, Projection: NewProjectionSource(dir, "instagram"),
		Compat: &fakeCompat{}})
	s.projection.Poll(context.Background())
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	if err := s.Start(ctx); err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(3 * time.Second)
	for q.count() == 0 && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	if q.count() != 1 {
		t.Fatalf("replay enqueued %d events, want exactly 1 (the unconfirmed)", q.count())
	}
	var env map[string]any
	json.Unmarshal(q.envs[0], &env)
	if env["event_id"] != "v1:pending" {
		t.Fatalf("replayed wrong event: %v", env["event_id"])
	}
}

// Journal failure halts: the gate closes and no further events are held.
func TestSinkJournalFailureHalts(t *testing.T) {
	dir := t.TempDir()
	writeProjection(t, dir, []string{"room-1"}, nil)
	q, compat := &fakeQueue{}, &fakeCompat{}
	s := newTestSink(t, q, compat, dir)
	halted := make(chan string, 1)
	s.onHalt = func(r string) { halted <- r }
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	if err := s.Start(ctx); err != nil {
		t.Fatal(err)
	}
	// Break the journal: unlink the file and put a DIRECTORY at its path —
	// open(O_CREATE|O_APPEND) fails with EISDIR for any uid. A mode-bit
	// trick would be bypassed by CAP_DAC_OVERRIDE when the image build
	// runs this suite as root.
	os.Remove(s.journal.path)
	if err := os.Mkdir(s.journal.path, 0o700); err != nil {
		t.Fatal(err)
	}
	s.Emit(msgEvent())
	select {
	case <-halted:
	case <-time.After(2 * time.Second):
		t.Fatal("journal failure must invoke OnHalt")
	}
	if s.Healthy() || s.UpstreamOK() {
		t.Fatal("gate must close on journal failure")
	}
}

// Seq-grounded reaction transitions: a redelivery keeps its iris seq and
// dedupes, a stale delta below the watermark is suppressed, and a real
// re-add at a LATER seq is a new transition. The ledger never mints
// sequence numbers — ordering comes from the upstream revision itself.
func TestTransitionSeqGrounded(t *testing.T) {
	dir := t.TempDir()
	writeProjection(t, dir, []string{"r"}, nil)
	q, compat := &fakeQueue{}, &fakeCompat{}
	s := newTestSink(t, q, compat, dir)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	s.Start(ctx)
	react := func(removed bool, seq int64) {
		s.Emit(map[string]any{
			"kind": "reaction", "room_id": "r", "message_id": "mid-1",
			"sender_fbid": "fb-1", "emoji": "❤️", "removed": removed,
			"upstream_seq": float64(seq),
		})
	}
	react(false, 5) // add @5 — genuine
	react(false, 5) // identical redelivery — replay
	react(true, 6)  // remove @6 — genuine
	react(false, 5) // DELAYED redelivery of the original add — stale replay
	react(false, 7) // real re-add @7 — genuine
	time.Sleep(400 * time.Millisecond)
	if q.count() != 3 {
		t.Fatalf("enqueued %d transitions, want 3 (add@5, remove@6, re-add@7)", q.count())
	}
	// The three identities are distinct and seq-grounded.
	var kinds []string
	for _, raw := range q.envs {
		var env map[string]any
		json.Unmarshal(raw, &env)
		kinds = append(kinds, env["source_key"].(string))
	}
	if !strings.Contains(kinds[0], ":add:s5") || !strings.Contains(kinds[1], ":remove:s6") ||
		!strings.Contains(kinds[2], ":add:s7") {
		t.Fatalf("source identities not seq-grounded: %v", kinds)
	}
}

// Restart across add/remove/add: the ledger is rebuilt from the transitions
// embedded in each event's own journal record — a delayed redelivery after
// restart still suppresses, a genuine later transition still emits.
func TestTransitionLedgerSurvivesRestart(t *testing.T) {
	dir := t.TempDir()
	writeProjection(t, dir, []string{"r"}, nil)
	jpath := filepath.Join(t.TempDir(), "j.log")
	react := func(s *Sink, removed bool, seq int64) {
		s.Emit(map[string]any{
			"kind": "reaction", "room_id": "r", "message_id": "mid-1",
			"sender_fbid": "fb-1", "emoji": "❤️", "removed": removed,
			"upstream_seq": float64(seq),
		})
	}
	// Boot 1: add@5 + remove@6 captured AND enqueued (the enqueued records
	// must be durable before the "crash", or the replay set differs run to
	// run — waiting makes the crash boundary deterministic).
	j1, _ := OpenJournal(jpath, 0)
	q1 := &fakeQueue{}
	s1 := NewSink(SinkConfig{AccountKey: "acct", Key: testKey, KeyVersion: 1,
		Journal: j1, Queue: q1, Projection: NewProjectionSource(dir, "instagram"),
		Compat: &fakeCompat{}, Actions: newFakeActions()})
	s1.projection.Poll(context.Background())
	ctx, cancel := context.WithCancel(context.Background())
	s1.Start(ctx)
	react(s1, false, 5)
	react(s1, true, 6)
	deadline := time.Now().Add(3 * time.Second)
	for q1.count() < 2 && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	if q1.count() != 2 {
		cancel()
		t.Fatalf("boot-1 enqueued %d, want 2", q1.count())
	}
	cancel()

	// Boot 2: same journal. The two events carry `enqueued` records, so no
	// enqueue replay is owed. A delayed add@5 must STILL be suppressed —
	// the transition ledger was rebuilt from the journaled records — and a
	// genuine re-add@7 must emit.
	j2, _ := OpenJournal(jpath, 0)
	q2 := &fakeQueue{}
	s2 := NewSink(SinkConfig{AccountKey: "acct", Key: testKey, KeyVersion: 1,
		Journal: j2, Queue: q2, Projection: NewProjectionSource(dir, "instagram"),
		Compat: &fakeCompat{}, Actions: newFakeActions()})
	s2.projection.Poll(context.Background())
	ctx2, cancel2 := context.WithCancel(context.Background())
	defer cancel2()
	s2.Start(ctx2)
	react(s2, false, 5) // delayed redelivery — suppressed by rebuilt ledger
	react(s2, false, 7) // genuine re-add — emitted
	deadline = time.Now().Add(3 * time.Second)
	for q2.count() < 1 && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	time.Sleep(200 * time.Millisecond)
	if q2.count() != 1 {
		t.Fatalf("enqueued %d after restart, want 1 (only the re-add)", q2.count())
	}
}

// Truly clockless deltas (no timestamp, no seq): identical repeats are
// replays; a state the key has already committed is UNRESOLVABLE — stale
// redelivery and genuine re-transition are indistinguishable — so it is
// journaled `ambiguous`, never queued, and never consumes the ledger.
func TestClocklessAmbiguousTransition(t *testing.T) {
	dir := t.TempDir()
	writeProjection(t, dir, []string{"r"}, nil)
	q, compat := &fakeQueue{}, &fakeCompat{}
	s := newTestSink(t, q, compat, dir)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	s.Start(ctx)
	react := func(removed bool) {
		s.Emit(map[string]any{
			"kind": "reaction", "room_id": "r", "message_id": "mid-1",
			"sender_fbid": "fb-1", "emoji": "❤️", "removed": removed,
		})
	}
	react(false) // first add — genuine
	react(false) // identical repeat — replay
	react(true)  // first remove — genuine
	react(false) // re-add with no clock: unresolvable → ambiguous
	time.Sleep(400 * time.Millisecond)
	if q.count() != 2 {
		t.Fatalf("enqueued %d, want 2 (add + remove); ambiguous never queues", q.count())
	}
	recs, _, _ := s.journal.Recover()
	var ambiguous int
	for _, raw := range recs {
		var rec map[string]any
		json.Unmarshal(raw, &rec)
		if rec["type"] == recAmbiguous {
			ambiguous++
		}
	}
	if ambiguous != 1 {
		t.Fatalf("ambiguous records = %d, want 1", ambiguous)
	}
	// No false dedupe: the ambiguous delta must NOT have committed — the
	// ledger's last state is still remove.
	if st := s.transitions["r|mid-1|fb-1|❤️"]; st == nil || st.Last != "remove" {
		t.Fatalf("ledger consumed an ambiguous delta: %+v", st)
	}
}

// >buffer-capacity outage: the OLD channel dropped the in-memory handoff at
// 1024 and stranded journaled events until a restart. The pending list
// mirrors the journal and drains ALL accepted work, in order, on recovery.
func TestSinkDrainsAllAfterOutage(t *testing.T) {
	dir := t.TempDir()
	writeProjection(t, dir, []string{"room-1"}, nil)
	q, compat := &fakeQueue{}, &fakeCompat{}
	const total = 1200 // deliberately > the old 1024 channel capacity
	q.down.Store(true) // redis down for the whole emit burst
	s := newTestSink(t, q, compat, dir)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	if err := s.Start(ctx); err != nil {
		t.Fatal(err)
	}
	for i := 0; i < total; i++ {
		ev := msgEvent()
		ev["message_id"] = fmt.Sprintf("mid-%d", i)
		s.Emit(ev)
	}
	if q.count() != 0 {
		t.Fatal("outage must not have leaked an enqueue")
	}
	// Redis "recovers": the drainer's retry loop must now drain EVERY
	// accepted event in order — no restart, no loss, no reordering.
	q.down.Store(false)
	deadline := time.Now().Add(20 * time.Second)
	for q.count() < total && time.Now().Before(deadline) {
		time.Sleep(20 * time.Millisecond)
	}
	if got := q.count(); got != total {
		t.Fatalf("enqueued %d of %d events after outage", got, total)
	}
	// Order check: stream ids are sequential and each envelope's event_id
	// order matches emit order (the drainer is the single sequencer).
	for i, raw := range q.envs {
		var env map[string]any
		json.Unmarshal(raw, &env)
		wantMid := fmt.Sprintf("mid-%d", i)
		if msg, _ := env["message"].(map[string]any); msg["message_id"] != wantMid {
			t.Fatalf("position %d = %v, want %s", i, msg["message_id"], wantMid)
		}
	}
}

// Kill after enqueue, before the receipt: restart must finish the pending
// receipt WITHOUT another XADD (receiptOnly) and fire exactly once.
func TestSinkReceiptRecoveryAfterKill(t *testing.T) {
	dir := t.TempDir()
	emoji := "👀"
	writeProjection(t, dir, []string{"room-1"}, &emoji)
	jpath := filepath.Join(t.TempDir(), "j.log")

	// Prior life: event enqueued (confirmed in journal) but receipted never
	// landed — the crash window between XADD-confirm and receipt record.
	j1, _ := OpenJournal(jpath, 0)
	env := &Envelope{V: 1, Platform: "instagram", AccountKey: "acct", Kind: "message",
		SourceKey: SourceKey("instagram", "acct", "room-1", "message", "mid:m1"),
		EventID:   "v1:old", ConversationID: "room-1", ConversationType: "group",
		Message: map[string]any{"message_id": "m1"}, Attachments: []Attachment{},
		Trace: map[string]any{}, Receipt: map[string]any{"emoji": emoji}}
	r1, _ := json.Marshal(map[string]any{"type": recEvent, "envelope": env})
	r2, _ := json.Marshal(map[string]any{"type": recEnqueued, "event_id": "v1:old",
		"canonical_event_id": "v1:old", "stream_id": "1-1"})
	j1.Append(r1)
	j1.Append(r2)

	j2, _ := OpenJournal(jpath, 0)
	q := &fakeQueue{}
	s := NewSink(SinkConfig{AccountKey: "acct", Key: testKey, KeyVersion: 1,
		Journal: j2, Queue: q, Projection: NewProjectionSource(dir, "instagram"),
		Compat: &fakeCompat{}, Actions: newFakeActions()})
	s.projection.Poll(context.Background())
	var calls int
	var mu sync.Mutex
	s.SetReceipt(func(_ context.Context, room, mid, em string) error {
		mu.Lock()
		calls++
		mu.Unlock()
		return nil
	})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	if err := s.Start(ctx); err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		mu.Lock()
		if calls > 0 {
			mu.Unlock()
			break
		}
		mu.Unlock()
		time.Sleep(10 * time.Millisecond)
	}
	mu.Lock()
	defer mu.Unlock()
	if calls != 1 {
		t.Fatalf("receipt fired %d times, want 1", calls)
	}
	if q.count() != 0 {
		t.Fatalf("receipt recovery XADD'd %d times, want 0", q.count())
	}
	// And the journal now holds a receipted record suppressing future fires.
	recs, _, _ := j2.Recover()
	var sawReceipted bool
	for _, raw := range recs {
		var rec map[string]any
		json.Unmarshal(raw, &rec)
		if rec["type"] == recReceipted {
			sawReceipted = true
		}
	}
	if !sawReceipted {
		t.Fatal("receipted record must be journaled")
	}
}

// Crash + rotation: the same canonical source replayed under a rotated key
// resolves to the ORIGINAL alias (what the Lua returns as canonical), and
// the receipt reservation keyed on it dedupes — one receipt total.
func TestReceiptSuppressedAcrossRotation(t *testing.T) {
	dir := t.TempDir()
	emoji := "👀"
	writeProjection(t, dir, []string{"room-1"}, &emoji)
	q, compat := &fakeQueue{}, &fakeCompat{}
	s := newTestSink(t, q, compat, dir)
	var calls int
	var mu sync.Mutex
	s.SetReceipt(func(_ context.Context, _, _, _ string) error {
		mu.Lock()
		calls++
		mu.Unlock()
		return nil
	})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	s.Start(ctx)

	src := SourceKey("instagram", "acct", "room-1", "message", "mid:m1")
	env1 := &Envelope{V: 1, Platform: "instagram", AccountKey: "acct", Kind: KindMessage,
		SourceKey: src, EventID: EventID(src, testKey, 1), ConversationID: "room-1",
		ConversationType: "group", Message: map[string]any{"message_id": "m1"},
		Receipt: map[string]any{"emoji": emoji}}
	env2 := &Envelope{V: 1, Platform: "instagram", AccountKey: "acct", Kind: KindMessage,
		SourceKey: src, EventID: EventID(src, []byte("rotated-rotated-rotated-rotated"), 2),
		ConversationID: "room-1", ConversationType: "group",
		Message: map[string]any{"message_id": "m1"}, Receipt: map[string]any{"emoji": emoji}}
	// The first enqueue writes under env1's alias; the rotated replay's Lua
	// result resolves canonical back to that original alias — NOT env2's.
	s.doReceipt(ctx, env1, env1.EventID)
	s.doReceipt(ctx, env2, env1.EventID)
	mu.Lock()
	defer mu.Unlock()
	if calls != 1 {
		t.Fatalf("receipt fired %d times across rotation, want 1", calls)
	}
}

// --- defect proofs ------------------------------------------------------------

// Custody reuse after kill: a crash between the custody write and the XADD
// replays the ORIGINAL (expired) URL — the deterministic key + verified
// hash lets the replay rewire the durable handle with ZERO upstream calls.
func TestCustodyReusedAcrossKill(t *testing.T) {
	dir := t.TempDir()
	writeProjection(t, dir, []string{"room-1"}, nil)
	cust, err := NewCustody(filepath.Join(t.TempDir(), "custody"))
	if err != nil {
		t.Fatal(err)
	}
	// The bytes landed durably BEFORE the crash.
	key := AttachmentKey("v1:media", 0)
	payload := []byte("png-bytes-that-would-need-refetch")
	rec, err := cust.Put(key, strings.NewReader(string(payload)), "image/png", "x.png", "v1:media")
	if err != nil {
		t.Fatal(err)
	}
	var httpCalls int32
	trip := roundTripFunc(func(*http.Request) (*http.Response, error) {
		atomic.AddInt32(&httpCalls, 1)
		return nil, errors.New("must not fetch: url expired")
	})
	j, _ := OpenJournal(filepath.Join(t.TempDir(), "j.log"), 0)
	s := NewSink(SinkConfig{AccountKey: "acct", Key: testKey, KeyVersion: 1,
		Journal: j, Queue: &fakeQueue{}, Projection: NewProjectionSource(dir, "instagram"),
		Compat: &fakeCompat{}, Custody: cust,
		HTTPClient: &http.Client{Transport: trip}})
	env := &Envelope{V: 1, Platform: "instagram", AccountKey: "acct", Kind: KindMessage,
		SourceKey: "sk", EventID: "v1:media", ConversationID: "room-1",
		ConversationType: "group", Attachments: []Attachment{}, Trace: map[string]any{},
		Message: map[string]any{
			"raw": map[string]any{"media": map[string]any{
				"url": "https://cdn.invalid/expired.png", "media_type": "image/png"}},
			"media": []any{map[string]any{"url": "https://cdn.invalid/expired.png"}},
		}}
	s.enqueue(context.Background(), &captureItem{env: env})
	if got := atomic.LoadInt32(&httpCalls); got != 0 {
		t.Fatalf("replay re-fetched an already-spooled object (%d calls)", got)
	}
	ref := env.Message["media"].([]any)[0].(map[string]any)
	if ref["attachment_key"] != key || ref["sha256"] != rec.SHA256 || ref["unavailable"] == true {
		t.Fatalf("ref not rewired to durable handle: %v", ref)
	}
	if len(env.Attachments) != 1 || env.Attachments[0].Key != key {
		t.Fatalf("attachments = %v, want the durable key", env.Attachments)
	}
}

// Receipt lane retry: a transient Reserve failure re-queues the receipt in
// the lane (no journal phase, no loss, no restart) — it fires once PG
// answers. Meanwhile the ENQUEUE lane keeps draining: later events are not
// held behind a failing receipt.
func TestReceiptLaneRetriesReserveFailure(t *testing.T) {
	dir := t.TempDir()
	emoji := "👀"
	writeProjection(t, dir, []string{"room-1"}, &emoji)
	q, compat := &fakeQueue{}, &fakeCompat{}
	s := newTestSink(t, q, compat, dir)
	acts := s.actions.(*fakeActions)
	acts.failReserve = 1
	var mu sync.Mutex
	fired := map[string]bool{}
	s.SetReceipt(func(_ context.Context, _, mid, _ string) error {
		mu.Lock()
		fired[mid] = true
		mu.Unlock()
		return nil
	})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	s.Start(ctx)
	s.Emit(msgEvent()) // message_id mid-1; its receipt hits the reserve failure
	// Later traffic enqueues despite the receipt still retrying.
	for i := 0; i < 3; i++ {
		ev := msgEvent()
		ev["message_id"] = fmt.Sprintf("mid-extra-%d", i)
		s.Emit(ev)
	}
	deadline := time.Now().Add(4 * time.Second)
	for q.count() < 4 && time.Now().Before(deadline) {
		time.Sleep(15 * time.Millisecond)
	}
	if q.count() != 4 {
		t.Fatalf("enqueue lane blocked behind a retrying receipt: %d of 4", q.count())
	}
	for time.Now().Before(deadline) {
		mu.Lock()
		done := len(fired) == 4 && fired["mid-1"]
		mu.Unlock()
		if done {
			break
		}
		time.Sleep(20 * time.Millisecond)
	}
	mu.Lock()
	defer mu.Unlock()
	if !fired["mid-1"] {
		t.Fatal("receipt whose Reserve failed never fired on retry")
	}
	if len(fired) != 4 {
		t.Fatalf("receipts fired for %d messages, want 4", len(fired))
	}
}

// A Finish failure must record NO phase: the action row stays `started`,
// the journal stays silent, and the retry resolves the receipt to
// `uncertain` WITHOUT re-firing the platform call.
func TestReceiptFinishFailureResolvesUncertain(t *testing.T) {
	dir := t.TempDir()
	writeProjection(t, dir, []string{"room-1"}, nil)
	j, _ := OpenJournal(filepath.Join(t.TempDir(), "j.log"), 0)
	acts := newFakeActions()
	acts.failFinish = 1
	s := NewSink(SinkConfig{AccountKey: "acct", Key: testKey, KeyVersion: 1,
		Journal: j, Queue: &fakeQueue{}, Projection: NewProjectionSource(dir, "instagram"),
		Compat: &fakeCompat{}, Actions: acts})
	s.projection.Poll(context.Background())
	var calls int
	s.SetReceipt(func(_ context.Context, _, _, _ string) error { calls++; return nil })
	env := &Envelope{V: 1, Platform: "instagram", AccountKey: "acct", Kind: KindMessage,
		SourceKey: "sk", EventID: "v1:r", ConversationID: "room-1", ConversationType: "group",
		Message: map[string]any{"message_id": "m1"}, Attachments: []Attachment{},
		Trace: map[string]any{}, Receipt: map[string]any{"emoji": "👀"}}
	ctx := context.Background()
	if !s.doReceipt(ctx, env, env.EventID) {
		t.Fatal("Finish failure must request a retry, not close the phase")
	}
	recs, _, _ := j.Recover()
	for _, raw := range recs {
		var rec map[string]any
		json.Unmarshal(raw, &rec)
		if rec["type"] == recReceipted {
			t.Fatalf("a receipted record was written despite the failed Finish: %v", rec)
		}
	}
	// Retry: replay sees `started`, journals `uncertain`, never re-fires.
	if s.doReceipt(ctx, env, env.EventID) {
		t.Fatal("a `started` prior phase must resolve to uncertain, not retry")
	}
	if calls != 1 {
		t.Fatalf("platform call fired %d times across Finish failure, want 1", calls)
	}
	recs, _, _ = j.Recover()
	var sawUncertain bool
	for _, raw := range recs {
		var rec map[string]any
		json.Unmarshal(raw, &rec)
		if rec["type"] == recReceipted && rec["state"] == ActionUncertain {
			sawUncertain = true
		}
	}
	if !sawUncertain {
		t.Fatal("receipted record must land as uncertain after a started prior phase")
	}
}

// The capture span mints the envelope's trace identity, and the exported
// OTLP span proves it: a real otlptracehttp exporter posting real protobuf
// to an actual OTLP sink. Assert the exported `bridge.capture` span's
// trace_id equals the traceparent inside the queued envelope and that it
// carries nora.trace_kind=queue; the `bridge.enqueue` span must LINK back
// to that capture context, not sit under a different lineage.
func TestTraceExportedMatchesEnvelope(t *testing.T) {
	var mu sync.Mutex
	var captured []*collectortracepb.ExportTraceServiceRequest
	sink := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		var req collectortracepb.ExportTraceServiceRequest
		if err := proto.Unmarshal(body, &req); err != nil {
			t.Errorf("OTLP sink could not decode protobuf: %v", err)
			w.WriteHeader(400)
			return
		}
		mu.Lock()
		captured = append(captured, &req)
		mu.Unlock()
		w.WriteHeader(200)
	}))
	defer sink.Close()

	exp, err := otlptracehttp.New(context.Background(),
		otlptracehttp.WithEndpoint(strings.TrimPrefix(sink.URL, "http://")),
		otlptracehttp.WithInsecure())
	if err != nil {
		t.Fatal(err)
	}
	tp := sdktrace.NewTracerProvider(sdktrace.WithSyncer(exp))
	prev := otel.GetTracerProvider()
	otel.SetTracerProvider(tp)
	defer func() { otel.SetTracerProvider(prev); tp.Shutdown(context.Background()) }()

	dir := t.TempDir()
	emoji := "👀"
	writeProjection(t, dir, []string{"room-1"}, &emoji) // grants receipts (wildcard + fb-1 alias)
	cust, err := NewCustody(filepath.Join(t.TempDir(), "custody"))
	if err != nil {
		t.Fatal(err)
	}
	q, compat := &fakeQueue{}, &fakeCompat{}
	s := newTestSink(t, q, compat, dir)
	s.custody = cust
	s.httpClient = &http.Client{Transport: roundTripFunc(func(*http.Request) (*http.Response, error) {
		return &http.Response{StatusCode: 200,
			Body:          io.NopCloser(strings.NewReader("img-bytes")),
			ContentLength: 9,
			Header:        http.Header{"Content-Type": []string{"image/png"}}}, nil
	})}
	s.SetReceipt(func(_ context.Context, _, _, _ string) error { return nil })
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	if err := s.Start(ctx); err != nil {
		t.Fatal(err)
	}
	s.Emit(msgEvent()) // receipt intent (granted + addressed) → bridge.receipt root
	mev := msgEvent()
	mev["message_id"] = "mid-media"
	mev["media"] = map[string]any{"kind": "image", "url": "http://cdn.example/x.png",
		"attachment_fbid": "att-1"}
	s.Emit(mev) // media fetch → bridge.custody_fetch root

	deadline := time.Now().Add(4 * time.Second)
	wantRoots := map[string]bool{"bridge.capture": true, "bridge.enqueue": true,
		"bridge.custody_fetch": true, "bridge.receipt": true}
	for time.Now().Before(deadline) {
		mu.Lock()
		found := map[string]bool{}
		for _, req := range captured {
			for _, rs := range req.ResourceSpans {
				for _, ss := range rs.ScopeSpans {
					for _, sp := range ss.Spans {
						if wantRoots[sp.Name] {
							found[sp.Name] = true
						}
					}
				}
			}
		}
		mu.Unlock()
		if len(found) == 4 {
			break
		}
		time.Sleep(20 * time.Millisecond)
	}

	if q.count() == 0 {
		t.Fatal("event never enqueued")
	}
	var env map[string]any
	if err := json.Unmarshal(q.envs[0], &env); err != nil {
		t.Fatal(err)
	}
	tr, _ := env["trace"].(map[string]any)
	tpStr, _ := tr["traceparent"].(string)
	parts := strings.Split(tpStr, "-")
	if len(parts) != 4 || parts[0] != "00" {
		t.Fatalf("envelope traceparent malformed: %q", tpStr)
	}
	envTraceID := parts[1]

	mu.Lock()
	var names []string
	kindOK := map[string]bool{}
	producerOK := map[string]bool{}
	linkOK := map[string]bool{}
	captureTraces := map[string]bool{}
	for _, req := range captured {
		for _, rs := range req.ResourceSpans {
			for _, ss := range rs.ScopeSpans {
				for _, sp := range ss.Spans {
					names = append(names, sp.Name)
					for _, a := range sp.Attributes {
						if a.Key == "nora.trace_kind" && a.Value.GetStringValue() == "queue" {
							kindOK[sp.Name] = true
						}
					}
					if sp.Kind == 4 /* SPAN_KIND_PRODUCER */ {
						producerOK[sp.Name] = true
					}
					if sp.Name == "bridge.capture" {
						captureTraces[hex.EncodeToString(sp.TraceId)] = true
					}
				}
			}
		}
	}
	// Second pass: downstream links must name one of the CAPTURE traces —
	// not just any context.
	for _, req := range captured {
		for _, rs := range req.ResourceSpans {
			for _, ss := range rs.ScopeSpans {
				for _, sp := range ss.Spans {
					if sp.Name == "bridge.enqueue" || sp.Name == "bridge.custody_fetch" || sp.Name == "bridge.receipt" {
						for _, l := range sp.Links {
							if captureTraces[hex.EncodeToString(l.TraceId)] {
								linkOK[sp.Name] = true
							}
						}
					}
				}
			}
		}
	}
	mu.Unlock()
	if !captureTraces[envTraceID] {
		t.Fatalf("no bridge.capture span exported with envelope trace; saw %v", names)
	}
	// Every queue-side root must carry nora.trace_kind=queue AT CREATION —
	// the collector drops anything else — and be an explicit producer root
	// linked back to a capture lineage.
	for _, name := range []string{"bridge.capture", "bridge.enqueue", "bridge.custody_fetch", "bridge.receipt"} {
		if !kindOK[name] {
			t.Fatalf("%s exported without nora.trace_kind=queue (saw %v)", name, names)
		}
		if !producerOK[name] {
			t.Fatalf("%s is not a producer root (saw %v)", name, names)
		}
	}
	for _, name := range []string{"bridge.enqueue", "bridge.custody_fetch", "bridge.receipt"} {
		if !linkOK[name] {
			t.Fatalf("%s carries no span link to a capture context", name)
		}
	}
}

// --- receipt eligibility (room_receipt_senders + addressed) --------------------

// A denied sender — not in room_receipt_senders — mentioning Nora is
// CAPTURED (the room admits them) but carries no receipt intent.
func TestReceiptDeniedSenderMentioningNora(t *testing.T) {
	dir := t.TempDir()
	emoji := "👀"
	writeProjectionDoc(t, dir, map[string]any{
		"revision": 2, "generated_ms": 1, "enabled": true, "account_key": "acct",
		"rooms": []any{"room-1"}, "receipt_emoji": emoji,
		"self_aliases":         []any{"fb-1"},
		"room_receipt_senders": map[string]any{"room-1": []any{"other-9"}},
	})
	q, compat := &fakeQueue{}, &fakeCompat{}
	s := newTestSink(t, q, compat, dir)
	var calls int
	s.SetReceipt(func(_ context.Context, _, _, _ string) error { calls++; return nil })
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	s.Start(ctx)
	s.Emit(msgEvent()) // sender fb-9, mentions fb-1 — addressed, NOT granted
	deadline := time.Now().Add(3 * time.Second)
	for q.count() == 0 && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	if q.count() != 1 {
		t.Fatal("denied sender must still be captured")
	}
	var env map[string]any
	json.Unmarshal(q.envs[0], &env)
	if env["receipt"] != nil {
		t.Fatalf("denied sender's envelope carries a receipt intent: %v", env["receipt"])
	}
	time.Sleep(300 * time.Millisecond)
	if calls != 0 {
		t.Fatalf("receipt fired %d times for a denied sender", calls)
	}
}

// A room member in room_receipt_senders — with NO dm_allowed_senders at
// all — who addresses Nora gets the receipt. The DM list is not consulted.
func TestReceiptRoomMemberNoDMGrant(t *testing.T) {
	dir := t.TempDir()
	emoji := "👀"
	writeProjectionDoc(t, dir, map[string]any{
		"revision": 3, "generated_ms": 1, "enabled": true, "account_key": "acct",
		"rooms": []any{"room-1"}, "receipt_emoji": emoji,
		"self_aliases":         []any{"fb-1"},
		"room_receipt_senders": map[string]any{"room-1": []any{"fb-9"}},
	})
	q, compat := &fakeQueue{}, &fakeCompat{}
	s := newTestSink(t, q, compat, dir)
	var mu sync.Mutex
	var calls int
	s.SetReceipt(func(_ context.Context, _, mid, _ string) error {
		mu.Lock()
		calls++
		mu.Unlock()
		return nil
	})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	s.Start(ctx)
	s.Emit(msgEvent()) // fb-9, mentions fb-1 — granted AND addressed
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		mu.Lock()
		if calls > 0 {
			mu.Unlock()
			break
		}
		mu.Unlock()
		time.Sleep(10 * time.Millisecond)
	}
	mu.Lock()
	defer mu.Unlock()
	if calls != 1 {
		t.Fatalf("granted+addressed receipt fired %d times, want 1", calls)
	}
}

// Literal "*" in room_receipt_senders grants an otherwise unmapped sender —
// the addressed check still has to pass.
func TestReceiptWildcardUnmappedAddressed(t *testing.T) {
	dir := t.TempDir()
	emoji := "👀"
	writeProjectionDoc(t, dir, map[string]any{
		"revision": 4, "generated_ms": 1, "enabled": true, "account_key": "acct",
		"rooms": []any{"room-1"}, "receipt_emoji": emoji,
		"self_aliases":         []any{"fb-1"},
		"room_receipt_senders": map[string]any{"room-1": []any{"*"}},
	})
	q, compat := &fakeQueue{}, &fakeCompat{}
	s := newTestSink(t, q, compat, dir)
	var mu sync.Mutex
	var calls int
	s.SetReceipt(func(_ context.Context, _, _, _ string) error { mu.Lock(); calls++; mu.Unlock(); return nil })
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	s.Start(ctx)
	ev := msgEvent()
	ev["sender"] = map[string]any{"fbid": "stranger-77", "username": "x"}
	s.Emit(ev)
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		mu.Lock()
		if calls > 0 {
			mu.Unlock()
			break
		}
		mu.Unlock()
		time.Sleep(10 * time.Millisecond)
	}
	mu.Lock()
	defer mu.Unlock()
	if calls != 1 {
		t.Fatalf("wildcard-granted addressed receipt fired %d times, want 1", calls)
	}
}

// A granted sender whose message does NOT address Nora — no self mention,
// no self reply, no trigger — is captured but owed no receipt. No model or
// unknown-sender judgement fills the gap.
func TestReceiptGrantedButUnaddressed(t *testing.T) {
	dir := t.TempDir()
	emoji := "👀"
	writeProjection(t, dir, []string{"room-1"}, &emoji) // wildcard grant, fb-1 self alias
	q, compat := &fakeQueue{}, &fakeCompat{}
	s := newTestSink(t, q, compat, dir)
	var calls int
	s.SetReceipt(func(_ context.Context, _, _, _ string) error { calls++; return nil })
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	s.Start(ctx)
	ev := msgEvent()
	delete(ev, "mentions") // nobody mentioned; body is "hello @alice" — no trigger
	s.Emit(ev)
	deadline := time.Now().Add(3 * time.Second)
	for q.count() == 0 && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	if q.count() != 1 {
		t.Fatal("unaddressed granted sender is still captured")
	}
	var env map[string]any
	json.Unmarshal(q.envs[0], &env)
	if env["receipt"] != nil {
		t.Fatalf("unaddressed message carries a receipt intent: %v", env["receipt"])
	}
	time.Sleep(300 * time.Millisecond)
	if calls != 0 {
		t.Fatalf("receipt fired %d times for an unaddressed message", calls)
	}
}

// Revocation: the intent persisted at capture, then the projection dropped
// the room's sender list while the receipt waited on a retry. The pending
// phase is suppressed BEFORE the effect — journaled `skipped_revoked`,
// zero platform calls, the event itself still enqueued.
func TestReceiptRevokedBeforeEffect(t *testing.T) {
	dir := t.TempDir()
	emoji := "👀"
	writeProjection(t, dir, []string{"room-1"}, &emoji)
	q, compat := &fakeQueue{}, &fakeCompat{}
	s := newTestSink(t, q, compat, dir)
	// One transient Reserve failure forces the receipt into lane backoff —
	// the revocation lands deterministically inside that window.
	s.actions.(*fakeActions).failReserve = 1
	var calls int
	s.SetReceipt(func(_ context.Context, _, _, _ string) error { calls++; return nil })
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	if err := s.Start(ctx); err != nil {
		t.Fatal(err)
	}
	s.Emit(msgEvent()) // granted + addressed at capture → intent persisted
	deadline := time.Now().Add(3 * time.Second)
	for q.count() == 0 && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	if q.count() != 1 {
		t.Fatal("event must still enqueue")
	}
	// Revoke mid-retry: same rooms (capture unchanged), grant gone.
	writeProjectionDoc(t, dir, map[string]any{
		"revision": 2, "generated_ms": 2, "enabled": true, "account_key": "acct",
		"rooms": []any{"room-1"}, "receipt_emoji": emoji,
		"self_aliases": []any{"fb-1"},
	})
	s.projection.Poll(context.Background())
	deadline = time.Now().Add(3 * time.Second)
	var sawRevoked bool
	for time.Now().Before(deadline) {
		recs, _, _ := s.journal.Recover()
		for _, raw := range recs {
			var rec map[string]any
			json.Unmarshal(raw, &rec)
			if rec["type"] == recReceipted && rec["state"] == "skipped_revoked" {
				sawRevoked = true
			}
		}
		if sawRevoked {
			break
		}
		time.Sleep(20 * time.Millisecond)
	}
	if !sawRevoked {
		t.Fatal("revoked receipt must be journaled skipped_revoked")
	}
	if calls != 0 {
		t.Fatalf("platform call fired %d times after revocation", calls)
	}
}

// A legacy journal record — an envelope written before trace injection
// existed — must still drive every queue root without panicking: the link
// is absent, the root is still a producer with the queue kind.
func TestLegacyEnvelopeNoTraceNoPanic(t *testing.T) {
	dir := t.TempDir()
	writeProjection(t, dir, []string{"room-1"}, nil)
	q := &fakeQueue{}
	s := newTestSink(t, q, &fakeCompat{}, dir)
	env := &Envelope{V: 1, Platform: "instagram", AccountKey: "acct", Kind: KindMessage,
		SourceKey: "sk", EventID: "v1:legacy", ConversationID: "room-1",
		ConversationType: "group", Message: map[string]any{"message_id": "m1"},
		Attachments: []Attachment{}, Trace: nil}
	s.enqueue(context.Background(), &captureItem{env: env})
	if q.count() != 1 {
		t.Fatal("legacy envelope must still enqueue")
	}
}

// The canonical DATABASE_URL is the app's SQLAlchemy string
// (postgresql+asyncpg://…); pgx speaks the wire protocol and must see the
// bare scheme or it refuses to parse at all.
func TestCanonicalDSNStripsDialect(t *testing.T) {
	cases := map[string]string{
		"postgresql+asyncpg://u:p@h:5432/db": "postgresql://u:p@h:5432/db",
		"postgres+asyncpg://u:p@h/db":       "postgres://u:p@h/db",
		"postgresql://u:p@h/db":             "postgresql://u:p@h/db",
		"host=h port=5432 user=u":           "host=h port=5432 user=u",
	}
	for in, want := range cases {
		if got := canonicalDSN(in); got != want {
			t.Fatalf("canonicalDSN(%q) = %q, want %q", in, got, want)
		}
	}
	if _, err := pgxpool.ParseConfig(canonicalDSN("postgresql+asyncpg://u:p@localhost:5432/db")); err != nil {
		t.Fatalf("dialect URL must parse after normalisation: %v", err)
	}
}
