package instameow

// Native platform-observation fixture check — CURRENT library behavior only.
//
// Decodes every fixture in internal/bridge/testdata/platform-observations/
// through the pinned slidetypes.Delta.UnmarshalJSON and asserts the decoded
// event Go type matches the fixture's declared surface. This pins what the
// SDK decodes today; it says nothing about whether emitDelta forwards the
// event — the fixture README records that half per file.
//
// Fixture placement note: the raw slide deltas live under internal/bridge's
// testdata (the named platform-observations home); the decode harness lives
// here because instameow is the package that owns the native boundary.

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"testing"

	"go.mau.fi/mautrix-meta/pkg/instameow/slidetypes"
)

const obsFixtureDir = "../../bridge/testdata/platform-observations"

type obsFixture struct {
	Meta    struct {
		Name          string `json:"name"`
		NativeEvent   string `json:"native_event"`
		CaptureToday  string `json:"capture_today"`
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
