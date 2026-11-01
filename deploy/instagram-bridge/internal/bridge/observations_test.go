package bridge

// Typed-observation intake tests: the gateway's `kind:"observation"` event
// → v2 envelope, and the durable path it then takes. The gateway builders
// live in internal/gateway/instameow; these tests pin the wire event shape
// they emit and the capture guarantees the envelope gets.

import (
	"context"
	"encoding/json"
	"strconv"
	"testing"
	"time"
)

// pinEvent is the wire shape instameow's pinObservations emits.
func pinEvent(room, messageID, op string, seq int64) map[string]any {
	target := map[string]any{"message_id": messageID, "sent_ts": nil, "mentions_known": true}
	return map[string]any{
		"kind":         "observation",
		"room_id":      room,
		"upstream":     "pin:" + itoa(seq) + ":" + messageID + ":" + op,
		"upstream_seq": seq,
		"observation": map[string]any{
			"contract": 1,
			"kind":     "pin",
			"scope": map[string]any{
				"platform": "instagram", "account_key": "",
				"conversation_id": room, "thread": "",
			},
			"object_ref":       "pins:" + room,
			"target":           target,
			"target_author_id": "",
			"operation":        op,
			"subject_ref":      "",
			"actor_id":         "",
			"actor_status":     "not_supplied",
			"occurred_ms":      nil,
			"received_ms":      0,
			"time_quality":     "unknown",
			"source_revision":  "uq:" + itoa(seq),
			"source_position":  seq,
			"revision_scope":   "uq_seq_id",
			"coverage": map[string]any{
				"complete": true, "covered": "event", "gaps": []any{}, "reason": "",
			},
			"payload": map[string]any{"message": target, "pins": []any{}},
		},
	}
}

func itoa(v int64) string {
	if v == 0 {
		return "na"
	}
	return strconv.FormatInt(v, 10)
}

func roomEvent(room, title, actor string, occurredMS, seq int64) map[string]any {
	return map[string]any{
		"kind":         "observation",
		"room_id":      room,
		"upstream":     "roomrev:" + itoa(seq) + ":h",
		"upstream_seq": seq,
		"sender_fbid":  actor,
		"timestamp_ms": occurredMS,
		"observation": map[string]any{
			"contract": 1,
			"kind":     "room",
			"scope": map[string]any{
				"platform": "instagram", "account_key": "",
				"conversation_id": room, "thread": "",
			},
			"object_ref":       "room:" + room,
			"target":           nil,
			"target_author_id": "",
			"operation":        "revise",
			"subject_ref":      "",
			"actor_id":         actor,
			"actor_status":     "known",
			"occurred_ms":      occurredMS,
			"received_ms":      0,
			"time_quality":     "upstream",
			"source_revision":  "uq:" + itoa(seq),
			"source_position":  seq,
			"revision_scope":   "uq_seq_id",
			"coverage": map[string]any{
				"complete": true, "covered": "fields", "gaps": []any{}, "reason": "",
			},
			"payload": map[string]any{"fields": map[string]any{"title": title}},
		},
	}
}

// An observation event normalizes to a pure v2 envelope: kind = the
// observation's object kind, message null, scope stamped by capture.
func TestNormalizeObservationPin(t *testing.T) {
	n, err := Normalize(pinEvent("room-1", "mid.$a1", "add", 214), "acct", testKey, 1, 1730300001000)
	if err != nil {
		t.Fatalf("normalize failed: %v", err)
	}
	env := n.Env
	if env.V != 2 {
		t.Fatalf("v = %d, want 2", env.V)
	}
	if env.Kind != KindPin {
		t.Fatalf("kind = %q, want pin", env.Kind)
	}
	if env.Message != nil {
		t.Fatal("pure observation must carry message=null")
	}
	if env.SentMS != nil {
		t.Fatalf("clockless observation sent_ms = %v, want null", *env.SentMS)
	}
	if env.TimeQuality != TimeBridgeLocal {
		t.Fatalf("time_quality = %q", env.TimeQuality)
	}
	obs := env.Observation
	if obs == nil || obs["kind"] != "pin" || obs["operation"] != "add" {
		t.Fatalf("observation = %v", obs)
	}
	scope, _ := obs["scope"].(map[string]any)
	if scope["account_key"] != "acct" || scope["conversation_id"] != "room-1" {
		t.Fatalf("scope = %v", scope)
	}
	if obs["received_ms"] != int64(1730300001000) {
		t.Fatalf("received_ms = %v", obs["received_ms"])
	}
	// The pin transition rides the ledger with its upstream seq.
	if len(n.Transitions) != 1 || n.Transitions[0].Key != "room-1|pin|mid.$a1" ||
		n.Transitions[0].State != "add" || n.Transitions[0].UpstreamSeq != 214 {
		t.Fatalf("transitions = %+v", n.Transitions)
	}
	// Source key carries the observation kind and upstream identity.
	want := SourceKey("instagram", "acct", "room-1", "pin", "pin:214:mid.$a1:add")
	if env.SourceKey != want {
		t.Fatalf("source_key = %q, want %q", env.SourceKey, want)
	}
}

// An upstream clock on the event lands as the envelope's sent_ms.
func TestNormalizeObservationClocked(t *testing.T) {
	n, err := Normalize(roomEvent("room-1", "Saturday", "fb-9", 1790002200000, 215), "acct", testKey, 1, 1730300001000)
	if err != nil {
		t.Fatalf("normalize failed: %v", err)
	}
	env := n.Env
	if env.Kind != KindRoom || env.SenderID != "fb-9" {
		t.Fatalf("envelope = %+v", env)
	}
	if env.SentMS == nil || *env.SentMS != 1790002200000 {
		t.Fatalf("sent_ms = %v", env.SentMS)
	}
	if env.TimeQuality != TimeUpstream {
		t.Fatalf("time_quality = %q", env.TimeQuality)
	}
}

// Scope binding: an observation for a different room or a different platform
// is a retained failure at the boundary, never a record.
func TestNormalizeObservationRejectsWrongScope(t *testing.T) {
	ev := pinEvent("room-1", "mid.$a1", "add", 214)
	ev["room_id"] = "room-2" // envelope says room-2, observation says room-1
	if _, err := Normalize(ev, "acct", testKey, 1, 1); err == nil {
		t.Fatal("wrong-room observation must fail")
	}
	ev = pinEvent("room-1", "mid.$a1", "add", 214)
	scope := ev["observation"].(map[string]any)["scope"].(map[string]any)
	scope["platform"] = "whatsapp"
	if _, err := Normalize(ev, "acct", testKey, 1, 1); err == nil {
		t.Fatal("wrong-platform observation must fail")
	}
}

// Malformed dicts fail closed — a bad contract, kind, op or missing ref is
// an error, never a partially-typed record.
func TestNormalizeObservationRejectsMalformed(t *testing.T) {
	cases := map[string]func(map[string]any){
		"bad contract":  func(m map[string]any) { m["contract"] = 2 },
		"bad kind":      func(m map[string]any) { m["kind"] = "fortune" },
		"bad operation": func(m map[string]any) { m["operation"] = "explode" },
		"missing ref":   func(m map[string]any) { m["object_ref"] = "" },
		"known w/o actor": func(m map[string]any) {
			m["actor_status"] = "known"
			m["actor_id"] = ""
		},
		"upstream time w/o clock": func(m map[string]any) {
			m["time_quality"] = "upstream"
		},
		"position w/o scope": func(m map[string]any) {
			m["revision_scope"] = ""
		},
	}
	for name, mutate := range cases {
		ev := pinEvent("room-1", "mid.$a1", "add", 214)
		mutate(ev["observation"].(map[string]any))
		if _, err := Normalize(ev, "acct", testKey, 1, 1); err == nil {
			t.Errorf("%s: malformed observation must fail", name)
		}
	}
	// And an observation event missing room or upstream is dropped outright.
	ev := pinEvent("", "mid.$a1", "add", 214)
	if n, err := Normalize(ev, "acct", testKey, 1, 1); n != nil || err != nil {
		t.Errorf("roomless observation must drop, got %v/%v", n, err)
	}
}

// The shared-XMA message carries the artifact sidecar on the SAME v2
// envelope — kind stays message, observation is set, one wake.
func TestSharedXMAMessageCarriesArtifactSidecar(t *testing.T) {
	ev := msgEvent()
	ev["media"] = map[string]any{
		"kind": "image", "url": "https://cdn.example/thumb.jpg",
	}
	ev["shared"] = map[string]any{
		"author":     "@creator_example",
		"target_url": "https://www.instagram.com/reel/ABC123def/",
		"target_id":  "3401111111111111111",
		"typename":   "XMATemplate",
		"layout":     "DEFAULT",
		"subtitle":   "12k plays",
	}
	n, err := Normalize(ev, "acct", testKey, 1, 1730300001000)
	if err != nil || n == nil {
		t.Fatalf("normalize = %v/%v", n, err)
	}
	env := n.Env
	if env.V != 2 {
		t.Fatalf("v = %d, want 2 (sidecar)", env.V)
	}
	if env.Kind != KindMessage {
		t.Fatalf("kind = %q, want message", env.Kind)
	}
	if env.Message == nil {
		t.Fatal("sidecar envelope keeps its message")
	}
	obs := env.Observation
	if obs == nil {
		t.Fatal("artifact sidecar missing")
	}
	if obs["kind"] != KindArtifact || obs["operation"] != "create" {
		t.Fatalf("artifact obs = %v", obs)
	}
	if obs["object_ref"] != "xma:3401111111111111111" {
		t.Fatalf("object_ref = %v", obs["object_ref"])
	}
	payload, _ := obs["payload"].(map[string]any)
	if payload["artifact_kind"] != "reel" {
		t.Fatalf("artifact_kind = %v, want validated reel", payload["artifact_kind"])
	}
	if payload["custody"] != "declared" {
		t.Fatalf("custody = %v", payload["custody"])
	}
	if payload["preview"] != true {
		t.Fatalf("preview = %v (thumbnail rode the delta)", payload["preview"])
	}
	meta, _ := payload["metadata"].(map[string]any)
	if meta["typename"] != "XMATemplate" || meta["author"] != "@creator_example" {
		t.Fatalf("metadata = %v", meta)
	}
	if obs["actor_id"] != "fb-9" {
		t.Fatalf("actor_id = %v (sharer is the sender)", obs["actor_id"])
	}
	// The message's own upstream identity is unchanged — same source, one key.
	if env.SourceKey != SourceKey("instagram", "acct", "room-1", "message", "mid:mid-1") {
		t.Fatalf("source_key = %q", env.SourceKey)
	}
}

// An XMA with no parseable target_url stays honestly "xma" — the typename
// is never classified.
func TestSharedXMAUnlinkedStaysXMA(t *testing.T) {
	ev := msgEvent()
	ev["shared"] = map[string]any{
		"author": "@creator_example", "typename": "XMATemplate",
	}
	n, err := Normalize(ev, "acct", testKey, 1, 1730300001000)
	if err != nil || n == nil {
		t.Fatalf("normalize = %v/%v", n, err)
	}
	payload, _ := n.Env.Observation["payload"].(map[string]any)
	if payload["artifact_kind"] != "xma" {
		t.Fatalf("artifact_kind = %v, want xma", payload["artifact_kind"])
	}
}

// The durable path: an observation event journals, enqueues, dedupes on
// replay — the same guarantees a message gets.
func TestObservationDurablePath(t *testing.T) {
	dir := t.TempDir()
	writeProjection(t, dir, []string{"room-1"}, nil)
	q, compat := &fakeQueue{}, &fakeCompat{}
	s := newTestSink(t, q, compat, dir)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	if err := s.Start(ctx); err != nil {
		t.Fatal(err)
	}
	s.Emit(pinEvent("room-1", "mid.$a1", "add", 214))
	deadline := time.Now().Add(3 * time.Second)
	for q.count() == 0 && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	if q.count() != 1 {
		t.Fatal("observation never enqueued")
	}
	var env map[string]any
	if err := json.Unmarshal(q.envs[0], &env); err != nil {
		t.Fatal(err)
	}
	if env["v"] != float64(2) || env["kind"] != "pin" {
		t.Fatalf("enqueued envelope = %v/%v", env["v"], env["kind"])
	}
	if env["message"] != nil {
		t.Fatal("pure observation must not carry a message")
	}
	if _, ok := env["observation"].(map[string]any); !ok {
		t.Fatal("observation missing from enqueued envelope")
	}
	// The journaled records: exactly one `event` record carrying the same
	// v2 envelope (the `enqueued` confirmation record sits beside it).
	recs, _, err := s.journal.Recover()
	if err != nil {
		t.Fatal(err)
	}
	var eventRecs int
	for _, raw := range recs {
		var rec struct {
			Type     string         `json:"type"`
			Envelope map[string]any `json:"envelope"`
		}
		if json.Unmarshal(raw, &rec) != nil || rec.Type != "event" {
			continue
		}
		eventRecs++
		if rec.Envelope["v"] != float64(2) || rec.Envelope["kind"] != "pin" {
			t.Fatalf("journaled envelope = %v/%v", rec.Envelope["v"], rec.Envelope["kind"])
		}
	}
	if eventRecs != 1 {
		t.Fatalf("journal event records = %d, want 1 (records: %d)", eventRecs, len(recs))
	}
	// Replay: identical source → suppressed, nothing re-enqueued.
	s.Emit(pinEvent("room-1", "mid.$a1", "add", 214))
	time.Sleep(50 * time.Millisecond)
	if q.count() != 1 {
		t.Fatal("replayed observation must dedupe")
	}
}

// A denied room's observation is never journaled or queued — the same
// fail-closed admission a message gets.
func TestObservationDeniedRoom(t *testing.T) {
	dir := t.TempDir()
	writeProjection(t, dir, []string{"room-1"}, nil)
	q, compat := &fakeQueue{}, &fakeCompat{}
	s := newTestSink(t, q, compat, dir)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	if err := s.Start(ctx); err != nil {
		t.Fatal(err)
	}
	s.Emit(pinEvent("room-secret", "mid.$a1", "add", 214))
	time.Sleep(50 * time.Millisecond)
	if q.count() != 0 || compat.count() != 0 {
		t.Fatal("denied-room observation must not journal or enqueue")
	}
	// The journal holds nothing for it — the probe truncated itself.
	recs, _, err := s.journal.Recover()
	if err != nil || len(recs) != 0 {
		t.Fatalf("journal should be empty for a denied observation: %d recs, %v", len(recs), err)
	}
}
