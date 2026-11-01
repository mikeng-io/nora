package instameow

// Native platform-observation fixture check — decode AND emission.
//
// Half one decodes every fixture in internal/bridge/testdata/platform-
// observations/ through the pinned slidetypes.Delta.UnmarshalJSON and
// asserts the decoded event Go type matches the fixture's declared surface.
// Half two routes each delta through emitDelta into a recording sink and
// asserts the event vector the bridge actually sends downstream — decode
// proves the library types the delta; emission proves the data reaches the
// durable pipeline's door.
//
// Fixture placement note: the raw slide deltas live under internal/bridge's
// testdata (the named platform-observations home); the decode harness lives
// here because instameow is the package that owns the native boundary.

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	"instagram-bridge/internal/domain"
	"instagram-bridge/internal/session"

	"go.mau.fi/mautrix-meta/pkg/instameow/slidetypes"
)

const obsFixtureDir = "../../bridge/testdata/platform-observations"

type obsFixture struct {
	Meta struct {
		Name         string `json:"name"`
		NativeEvent  string `json:"native_event"`
		CaptureToday string `json:"capture_today"`
	}
	Payload json.RawMessage `json:"payload"`
}

func loadObsFixture(t *testing.T, name string) obsFixture {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join(obsFixtureDir, name))
	if err != nil {
		t.Fatalf("missing fixture %s: %v", name, err)
	}
	var f struct {
		Fixture json.RawMessage `json:"_fixture"`
		Payload json.RawMessage `json:"payload"`
	}
	if err := json.Unmarshal(raw, &f); err != nil {
		t.Fatalf("%s: invalid fixture json: %v", name, err)
	}
	var meta obsFixture
	if err := json.Unmarshal(f.Fixture, &meta.Meta); err != nil {
		t.Fatalf("%s: invalid _fixture metadata: %v", name, err)
	}
	meta.Payload = f.Payload
	if meta.Meta.Name == "" || meta.Meta.NativeEvent == "" || meta.Meta.CaptureToday == "" {
		t.Fatalf("%s: _fixture metadata incomplete", name)
	}
	return meta
}

func decodePayloads(t *testing.T, f obsFixture) []*slidetypes.Delta {
	t.Helper()
	var one map[string]any
	if err := json.Unmarshal(f.Payload, &one); err == nil {
		var d slidetypes.Delta
		if err := json.Unmarshal(f.Payload, &d); err != nil {
			t.Fatalf("%s: delta decode failed: %v", f.Meta.Name, err)
		}
		return []*slidetypes.Delta{&d}
	}
	var many []json.RawMessage
	if err := json.Unmarshal(f.Payload, &many); err != nil {
		t.Fatalf("%s: payload is neither object nor array: %v", f.Meta.Name, err)
	}
	out := make([]*slidetypes.Delta, 0, len(many))
	for i, raw := range many {
		var d slidetypes.Delta
		if err := json.Unmarshal(raw, &d); err != nil {
			t.Fatalf("%s[%d]: delta decode failed: %v", f.Meta.Name, i, err)
		}
		out = append(out, &d)
	}
	return out
}

// The whole table: fixture -> decoded slidetypes event type. Asserts decode,
// not emission.
func TestPlatformObservationFixturesDecode(t *testing.T) {
	cases := []struct {
		file string
		want []string // %T of each decoded Data, in payload order
	}{
		{"new-message-shared-xma.json", []string{"*slidetypes.NewMessageEvent"}},
		{"create-reaction.json", []string{"*slidetypes.CreateReactionEvent"}},
		{"delete-reaction-clockless.json", []string{"*slidetypes.DeleteReactionEvent"}},
		{"reaction-unknown-actor.json", []string{"*slidetypes.CreateReactionEvent"}},
		{"reaction-add-remove-add.json", []string{
			"*slidetypes.CreateReactionEvent", "*slidetypes.DeleteReactionEvent", "*slidetypes.CreateReactionEvent"}},
		{"pin-unpin-message.json", []string{"*slidetypes.PinMessageEvent"}},
		{"update-thread-name.json", []string{"*slidetypes.UpdateThreadNameEvent"}},
		{"update-thread-image.json", []string{"*slidetypes.UpdateThreadImageEvent"}},
		{"participant-join.json", []string{"*slidetypes.ParticipantJoinEvent"}},
		{"participant-join-empty-users.json", []string{"*slidetypes.ParticipantJoinEvent"}},
		{"participant-leave.json", []string{"*slidetypes.ParticipantLeaveEvent"}},
		{"admin-change.json", []string{"*slidetypes.AdminChangeEvent"}},
		{"edit-message-no-sender.json", []string{"*slidetypes.EditMessageEvent"}},
		{"delete-message.json", []string{"*slidetypes.DeleteMessageEvent"}},
		{"unknown-delta-typename.json", []string{"slidetypes.UnknownEvent"}},
		{"read-receipt-and-pinned-thread.json", []string{"*slidetypes.ReadReceiptEvent", "*slidetypes.PinThreadEvent"}},
		{"multimedia-two-attachments.json", []string{"*slidetypes.NewMessageEvent"}},
	}
	seen := map[string]bool{}
	for _, tc := range cases {
		seen[tc.file] = true
		f := loadObsFixture(t, tc.file)
		deltas := decodePayloads(t, f)
		if len(deltas) != len(tc.want) {
			t.Fatalf("%s: decoded %d deltas, want %d", tc.file, len(deltas), len(tc.want))
		}
		for i, d := range deltas {
			got := fmt.Sprintf("%T", d.Data)
			if got != tc.want[i] {
				t.Errorf("%s[%d]: decoded %s, want %s", tc.file, i, got, tc.want[i])
			}
		}
	}
	// thread-info-snapshot is a read-route response, not a delta: it must at
	// least carry the fields its _fixture documents.
	snap := loadObsFixture(t, "thread-info-snapshot.json")
	seen["thread-info-snapshot.json"] = true
	var ti struct {
		ThreadInfo struct {
			Users          []any    `json:"users"`
			AdminUserIDs   []string `json:"admin_user_ids"`
			PinnedMessages []string `json:"pinned_messages"`
			IsGroup        bool     `json:"is_group"`
		} `json:"thread_info"`
	}
	if err := json.Unmarshal(snap.Payload, &ti); err != nil || len(ti.ThreadInfo.Users) == 0 || !ti.ThreadInfo.IsGroup {
		t.Fatalf("thread-info-snapshot: member/admin/pin fields not readable: %v", err)
	}

	entries, err := os.ReadDir(obsFixtureDir)
	if err != nil {
		t.Fatal(err)
	}
	for _, e := range entries {
		name := e.Name()
		if filepath.Ext(name) == ".json" && !seen[name] {
			t.Errorf("fixture %s is not asserted anywhere — every fixture needs a decode verdict", name)
		}
	}
}

// The semantic fields the binding lane needs must actually survive decoding:
// uq_seq_id as int64 revision, sender fbids, actor-vs-subject fields.
func TestPlatformObservationSemanticsSurvive(t *testing.T) {
	leave := decodePayloads(t, loadObsFixture(t, "participant-leave.json"))[0]
	lv, ok := leave.Data.(*slidetypes.ParticipantLeaveEvent)
	if !ok {
		t.Fatalf("leave decoded as %T", leave.Data)
	}
	if lv.ActorFBID == 0 || lv.LeftParticipantFBID == 0 || lv.ActorFBID == lv.LeftParticipantFBID {
		t.Errorf("actor/subject fields lost: %+v", lv)
	}
	del := decodePayloads(t, loadObsFixture(t, "delete-reaction-clockless.json"))[0]
	if del.UQSeqID != 212 {
		t.Errorf("uq_seq_id did not survive as revision: %d", del.UQSeqID)
	}
	dr, ok := del.Data.(*slidetypes.DeleteReactionEvent)
	if !ok || dr.Reaction.SenderFBID == 0 {
		t.Fatalf("reaction reactor lost: %+v", del.Data)
	}
	if !dr.Reaction.ReactionTimestampMS.IsZero() {
		t.Error("fixture must stay clockless — it exercises the seq fallback")
	}
	pin := decodePayloads(t, loadObsFixture(t, "pin-unpin-message.json"))[0]
	pv, ok := pin.Data.(*slidetypes.PinMessageEvent)
	if !ok || len(pv.PinnedMessages) != 1 || len(pv.UnpinnedMessages) != 1 {
		t.Fatalf("pin set semantics lost: %+v", pin.Data)
	}
	// The poll-shaped hypothetical must land on the floor as UnknownEvent.
	unk := decodePayloads(t, loadObsFixture(t, "unknown-delta-typename.json"))[0]
	if _, ok := unk.Data.(slidetypes.UnknownEvent); !ok {
		t.Fatalf("unmapped typename decoded as %T, want UnknownEvent", unk.Data)
	}
}

// --- emission: what emitDelta actually sends downstream ---------------------
//
// Decode proved the library types the delta; this half proves the bridge
// emits the typed observation — the two are different claims and only the
// second one means the data reaches the durable pipeline.

// recordingSink is an EventSink that keeps everything emitted.
type recordingSink struct {
	mu      sync.Mutex
	emitted []any
}

func (r *recordingSink) Emit(ev any) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.emitted = append(r.emitted, ev)
}

func (r *recordingSink) HasClients() bool { return true }

// obsTestBridge is a Bridge wired to a recording sink, self-bound like
// eventBridge but with capture instead of the hub.
func obsTestBridge(t *testing.T) (*Bridge, *recordingSink) {
	t.Helper()
	sink := &recordingSink{}
	b := NewBridge(Options{
		Store: session.Store{Dir: t.TempDir()},
		Sink:  sink,
		State: domain.StateAuthenticated,
		Self:  domain.Self{FBID: "60987654321000000", IGID: "1", Username: "self"},
	})
	return b, sink
}

// emitFixture routes one fixture's deltas through emitDelta and returns the
// emitted events in order.
func emitFixture(t *testing.T, name string) []any {
	t.Helper()
	b, sink := obsTestBridge(t)
	for _, d := range decodePayloads(t, loadObsFixture(t, name)) {
		b.emitDelta(d)
	}
	sink.mu.Lock()
	defer sink.mu.Unlock()
	return append([]any(nil), sink.emitted...)
}

// observationsOf filters the emitted set to observation events.
func observationsOf(emitted []any) []*observationEvent {
	var out []*observationEvent
	for _, ev := range emitted {
		if o, ok := ev.(*observationEvent); ok {
			out = append(out, o)
		}
	}
	return out
}

func messagesOf(emitted []any) []*messageEvent {
	var out []*messageEvent
	for _, ev := range emitted {
		if m, ok := ev.(*messageEvent); ok {
			out = append(out, m)
		}
	}
	return out
}

// A pin-set delta emits one pin observation per named message id, add and
// remove, on the room's pin-set object — seq-grounded, actorless.
func TestPinDeltaEmitsObservations(t *testing.T) {
	emitted := emitFixture(t, "pin-unpin-message.json")
	if len(emitted) != 2 {
		t.Fatalf("pin delta emitted %d events, want 2", len(emitted))
	}
	obs := observationsOf(emitted)
	if len(obs) != 2 {
		t.Fatalf("emitted %d observations, want 2", len(obs))
	}
	var add, remove *observationEvent
	for _, o := range obs {
		if o.Observation["operation"] == "add" {
			add = o
		} else {
			remove = o
		}
		if o.Observation["kind"] != "pin" {
			t.Errorf("observation kind = %v, want pin", o.Observation["kind"])
		}
		if o.Observation["object_ref"] != "pins:1234567890123456" {
			t.Errorf("object_ref = %v", o.Observation["object_ref"])
		}
		if o.Observation["actor_status"] != "not_supplied" {
			t.Errorf("actor_status = %v, want not_supplied", o.Observation["actor_status"])
		}
		if o.UpstreamSeq != 214 {
			t.Errorf("upstream_seq = %d, want 214", o.UpstreamSeq)
		}
		if o.Observation["source_position"] != int64(214) {
			t.Errorf("source_position = %v, want 214", o.Observation["source_position"])
		}
		if o.Observation["revision_scope"] != revisionScopeUQSeqID {
			t.Errorf("revision_scope = %v", o.Observation["revision_scope"])
		}
	}
	if add == nil || remove == nil {
		t.Fatal("expected one add and one remove observation")
	}
	tgt, _ := add.Observation["target"].(map[string]any)
	if tgt["message_id"] != "mid.$a1" {
		t.Errorf("add target = %v", tgt)
	}
	tgt, _ = remove.Observation["target"].(map[string]any)
	if tgt["message_id"] != "mid.$b9" {
		t.Errorf("remove target = %v", tgt)
	}
	if add.Upstream != "pin:214:mid.$a1:add" {
		t.Errorf("add upstream = %q", add.Upstream)
	}
}

// A thread-name delta emits the embedded admin message AND the typed room
// revise carrying the new title — the actor comes off the embedded message.
func TestThreadNameEmitsMessageAndRoomObservation(t *testing.T) {
	emitted := emitFixture(t, "update-thread-name.json")
	if len(emitted) != 2 {
		t.Fatalf("thread-name delta emitted %d events, want 2", len(emitted))
	}
	msgs := messagesOf(emitted)
	if len(msgs) != 1 || msgs[0].MessageID != "mid.$adm1" {
		t.Fatalf("expected the embedded admin message, got %+v", msgs)
	}
	obs := observationsOf(emitted)
	if len(obs) != 1 {
		t.Fatalf("expected one room observation, got %d", len(obs))
	}
	o := obs[0].Observation
	if o["kind"] != "room" || o["operation"] != "revise" {
		t.Fatalf("kind/op = %v/%v", o["kind"], o["operation"])
	}
	fields, _ := o["payload"].(map[string]any)["fields"].(map[string]any)
	if fields["title"] != "Saturday planning" {
		t.Fatalf("title field = %v", fields)
	}
	if o["actor_id"] != "63123456700000011" || o["actor_status"] != "known" {
		t.Errorf("actor = %v/%v", o["actor_id"], o["actor_status"])
	}
	if obs[0].TimestampMS != 1790002200000 {
		t.Errorf("timestamp_ms = %d", obs[0].TimestampMS)
	}
}

// A thread-image delta is the parallel room revise — fields{image} carries
// the new avatar URI, nothing else is claimed.
func TestThreadImageEmitsRoomObservation(t *testing.T) {
	emitted := emitFixture(t, "update-thread-image.json")
	if len(emitted) != 2 {
		t.Fatalf("thread-image delta emitted %d events, want 2", len(emitted))
	}
	obs := observationsOf(emitted)
	if len(obs) != 1 {
		t.Fatalf("expected one room observation, got %d", len(obs))
	}
	o := obs[0].Observation
	if o["kind"] != "room" || o["operation"] != "revise" {
		t.Fatalf("kind/op = %v/%v", o["kind"], o["operation"])
	}
	fields, _ := o["payload"].(map[string]any)["fields"].(map[string]any)
	uri, _ := fields["image"].(string)
	if !strings.HasPrefix(uri, "https://") || len(fields) != 1 {
		t.Fatalf("image field = %v", fields)
	}
}

// A join delta emits the admin message AND a membership snapshot of the
// POST-ACTION member set — the joiner has no typed field, so the covered
// member collection is what the source supplies.
func TestJoinEmitsMessageAndMemberSnapshot(t *testing.T) {
	emitted := emitFixture(t, "participant-join.json")
	if len(emitted) != 2 {
		t.Fatalf("join delta emitted %d events, want 2", len(emitted))
	}
	if len(messagesOf(emitted)) != 1 {
		t.Fatal("expected the embedded admin message")
	}
	obs := observationsOf(emitted)
	if len(obs) != 1 {
		t.Fatalf("expected one membership observation, got %d", len(obs))
	}
	o := obs[0].Observation
	if o["kind"] != "membership" || o["operation"] != "snapshot" {
		t.Fatalf("kind/op = %v/%v", o["kind"], o["operation"])
	}
	if o["object_ref"] != "members:1234567890123456" {
		t.Errorf("object_ref = %v", o["object_ref"])
	}
	payload, _ := o["payload"].(map[string]any)
	members, _ := payload["members"].([]any)
	if len(members) != 4 {
		t.Fatalf("snapshot members = %d, want 4", len(members))
	}
	if payload["covered"] != "members" {
		t.Errorf("covered = %v", payload["covered"])
	}
	cov, _ := o["coverage"].(map[string]any)
	if cov["complete"] != true {
		t.Errorf("coverage.complete = %v", cov["complete"])
	}
}

// An empty users[] still emits a snapshot — covered-set semantics mean the
// source reported an empty member set, not a missing one.
func TestJoinEmptyUsersEmitsEmptySnapshot(t *testing.T) {
	emitted := emitFixture(t, "participant-join-empty-users.json")
	obs := observationsOf(emitted)
	if len(obs) != 1 {
		t.Fatalf("expected one membership observation, got %d", len(obs))
	}
	payload, _ := obs[0].Observation["payload"].(map[string]any)
	members, _ := payload["members"].([]any)
	if len(members) != 0 {
		t.Fatalf("expected empty covered set, got %d", len(members))
	}
}

// A leave delta emits the admin message AND a typed remove: actor and
// subject are separate fields here — state 'left' only when actor==subject.
func TestLeaveEmitsTypedActorAndSubject(t *testing.T) {
	emitted := emitFixture(t, "participant-leave.json")
	obs := observationsOf(emitted)
	if len(obs) != 1 {
		t.Fatalf("expected one membership observation, got %d", len(obs))
	}
	o := obs[0].Observation
	if o["kind"] != "membership" || o["operation"] != "remove" {
		t.Fatalf("kind/op = %v/%v", o["kind"], o["operation"])
	}
	if o["subject_ref"] != "63987654300000022" {
		t.Errorf("subject_ref = %v", o["subject_ref"])
	}
	if o["actor_id"] != "63123456700000011" {
		t.Errorf("actor_id = %v", o["actor_id"])
	}
	// actor != subject: a removal, never asserted as voluntary leave.
	payload, _ := o["payload"].(map[string]any)
	if payload["state"] != "" {
		t.Errorf("state = %v, want empty for an admin removal", payload["state"])
	}
}

// actor==subject on a leave is a self-departure the source proves.
func TestSelfLeaveReadsLeft(t *testing.T) {
	d := decodePayloads(t, loadObsFixture(t, "participant-leave.json"))[0]
	lv := d.Data.(*slidetypes.ParticipantLeaveEvent)
	lv.ActorFBID = lv.LeftParticipantFBID // self-departure
	b, sink := obsTestBridge(t)
	b.emitDelta(d)
	obs := observationsOf(sink.emitted)
	if len(obs) != 1 {
		t.Fatalf("expected one observation, got %d", len(obs))
	}
	payload, _ := obs[0].Observation["payload"].(map[string]any)
	if payload["state"] != "left" {
		t.Errorf("state = %v, want left", payload["state"])
	}
}

// The admin-set mutation emits a snapshot on admins:{room} — IGID space,
// a different object from the fbid member set.
func TestAdminChangeEmitsIgidSnapshot(t *testing.T) {
	emitted := emitFixture(t, "admin-change.json")
	obs := observationsOf(emitted)
	if len(obs) != 1 {
		t.Fatalf("expected one membership observation, got %d", len(obs))
	}
	o := obs[0].Observation
	if o["object_ref"] != "admins:1234567890123456" {
		t.Errorf("object_ref = %v", o["object_ref"])
	}
	payload, _ := o["payload"].(map[string]any)
	if payload["covered"] != "admins" {
		t.Errorf("covered = %v", payload["covered"])
	}
	members, _ := payload["members"].([]any)
	if len(members) != 2 {
		t.Fatalf("admin members = %d", len(members))
	}
	first, _ := members[0].(map[string]any)
	if first["role"] != "admin" || first["subject"] != "901234567" {
		t.Errorf("admin member = %+v", first)
	}
	if o["actor_status"] != "not_applicable" {
		t.Errorf("actor_status = %v, want not_applicable", o["actor_status"])
	}
}

// A shared XMA emits the ordinary message event with the decoded `shared`
// metadata intact — the artifact sidecar is derived downstream at
// Normalize, but only because these fields actually reach the sink.
func TestSharedXMAEmitsSharedMetadata(t *testing.T) {
	emitted := emitFixture(t, "new-message-shared-xma.json")
	msgs := messagesOf(emitted)
	if len(msgs) != 1 {
		t.Fatalf("expected one message event, got %d", len(msgs))
	}
	sh := msgs[0].Shared
	if sh == nil {
		t.Fatal("shared metadata did not reach the sink")
	}
	if sh.Author != "@creator_example" || sh.TargetID != "3401111111111111111" {
		t.Errorf("shared = %+v", sh)
	}
	if sh.TargetURL != "https://www.instagram.com/reel/ABC123def/" {
		t.Errorf("target_url = %q", sh.TargetURL)
	}
}

// Inbox-level and receipt surfaces stay dropped; an unmapped typename emits
// nothing (explicit unavailability, never a guessed route).
func TestUnmappedAndInboxDeltasEmitNothing(t *testing.T) {
	for _, f := range []string{
		"unknown-delta-typename.json", "read-receipt-and-pinned-thread.json",
	} {
		if n := len(emitFixture(t, f)); n != 0 {
			t.Errorf("%s emitted %d events, want 0", f, n)
		}
	}
}

// A MULTI_MEDIA delta carries ordered_photo_video_attachments — the fifth
// attachment nesting. EVERY entry must reach the sink: `media` keeps the
// singular first-attachment contract for pre-multiplicity readers, and
// `media_list` carries the ordered set — image first, video second, in the
// source's order, with kinds inferred from the dash manifest. Dropping the
// second attachment is exactly the silent-multiplicity bug this guards.
func TestMultiMediaEmitsEveryAttachmentInOrder(t *testing.T) {
	emitted := emitFixture(t, "multimedia-two-attachments.json")
	msgs := messagesOf(emitted)
	if len(msgs) != 1 {
		t.Fatalf("expected one message event, got %d", len(msgs))
	}
	m := msgs[0]
	if m.Media == nil {
		t.Fatal("singular media contract broke — first attachment missing")
	}
	if m.Media.Kind != mediaImage || m.Media.AttachmentFBID != "970000000000001" {
		t.Fatalf("media[0] = %+v, want the image attachment 970000000000001", m.Media)
	}
	if len(m.MediaList) != 2 {
		t.Fatalf("media_list = %d entries, want 2 — multiplicity was dropped", len(m.MediaList))
	}
	img, vid := m.MediaList[0], m.MediaList[1]
	if img.Kind != mediaImage || img.AttachmentFBID != "970000000000001" {
		t.Fatalf("media_list[0] = %+v, want image 970000000000001", img)
	}
	if vid.Kind != mediaVideo || vid.AttachmentFBID != "970000000000002" {
		t.Fatalf("media_list[1] = %+v, want video (dash manifest) 970000000000002", vid)
	}
	if img.URL == nil || vid.URL == nil {
		t.Fatalf("cdn urls lost: %+v / %+v", img, vid)
	}
	// The singular contract IS the first list entry — not a different read.
	if *m.Media.URL != *img.URL || m.Media.AttachmentFBID != img.AttachmentFBID {
		t.Fatalf("media and media_list[0] diverged: %+v vs %+v", m.Media, img)
	}
}
