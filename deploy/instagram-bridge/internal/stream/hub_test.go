package stream

import (
	"encoding/json"
	"testing"
)

type probe struct {
	Kind string `json:"kind"`
	N    int    `json:"n"`
}

func drain(ch chan []byte) [][]byte {
	var out [][]byte
	for {
		select {
		case payload := <-ch:
			out = append(out, payload)
		default:
			return out
		}
	}
}

// With nobody listening, events wait. This is the whole reason the buffer
// exists: Nora restarting must not cost the messages sent while she was down.
func TestBufferedEventsReplayOnConnect(t *testing.T) {
	h := NewHub()
	for i := range 3 {
		h.Emit(&probe{Kind: "message", N: i})
	}
	_, buffered, dropped := h.Stats()
	if buffered != 3 || dropped != 0 {
		t.Fatalf("buffered=%d dropped=%d, want 3/0", buffered, dropped)
	}

	ch, replay, notice := h.Subscribe()
	defer h.Unsubscribe(ch)
	if notice != 0 {
		t.Errorf("drop notice %d raised with nothing dropped", notice)
	}
	if len(replay) != 3 {
		t.Fatalf("replayed %d events, want 3", len(replay))
	}
	// Order matters: the replay is a conversation, not a set.
	for i, payload := range replay {
		var got probe
		if err := json.Unmarshal(payload, &got); err != nil {
			t.Fatal(err)
		}
		if got.N != i {
			t.Errorf("replay[%d].n = %d", i, got.N)
		}
	}
	if _, buffered, _ := h.Stats(); buffered != 0 {
		t.Errorf("buffer still holds %d after replay", buffered)
	}
}

// Overflow drops the OLDEST and COUNTS it. Hermes' bridge did the first half
// and not the second — a silent shift() with no counter, no log and no spill —
// and for a transport feeding an audit trail that is the failure class Nora's
// design history treats as a bug rather than a feature.
func TestOverflowDropsOldestAndCounts(t *testing.T) {
	h := NewHub()
	for i := range bufferCap + 10 {
		h.Emit(&probe{Kind: "message", N: i})
	}
	_, buffered, dropped := h.Stats()
	if buffered != bufferCap {
		t.Errorf("buffered=%d, want the cap %d", buffered, bufferCap)
	}
	if dropped != 10 {
		t.Fatalf("dropped=%d, want 10 — a loss that is not counted is a loss nobody can see", dropped)
	}

	ch, replay, notice := h.Subscribe()
	defer h.Unsubscribe(ch)
	if notice != 10 {
		t.Errorf("connecting client was told %d drops, want 10", notice)
	}
	var first probe
	if err := json.Unmarshal(replay[0], &first); err != nil {
		t.Fatal(err)
	}
	if first.N != 10 {
		t.Errorf("oldest surviving event is n=%d, want 10 (the newest end must survive)", first.N)
	}
}

// The notice is one-shot; the counter is permanent. The notice answers "did I
// miss anything before I got here?", which is only a question at connect time,
// while the number on /health has to stay readable long afterwards.
func TestDropNoticeIsOneShotButTheCounterPersists(t *testing.T) {
	h := NewHub()
	for i := range bufferCap + 1 {
		h.Emit(&probe{Kind: "message", N: i})
	}
	ch1, _, notice1 := h.Subscribe()
	if notice1 != 1 {
		t.Fatalf("first client notice = %d", notice1)
	}
	h.Unsubscribe(ch1)

	ch2, _, notice2 := h.Subscribe()
	defer h.Unsubscribe(ch2)
	if notice2 != 0 {
		t.Errorf("second client was told about the same drop again (%d)", notice2)
	}
	if _, _, dropped := h.Stats(); dropped != 1 {
		t.Errorf("counter reset to %d — /health would report a clean run", dropped)
	}
}

// A client that stops draining must not stall the Instagram event handler:
// instameow dispatches deltas on the socket's own goroutine, so blocking here
// would turn one stuck HTTP reader into a dead bridge.
func TestSlowClientIsDroppedNotBlockedOn(t *testing.T) {
	h := NewHub()
	ch, _, _ := h.Subscribe()
	defer h.Unsubscribe(ch)
	for i := range clientQueue + 5 {
		h.Emit(&probe{Kind: "message", N: i})
	}
	if _, _, dropped := h.Stats(); dropped != 5 {
		t.Fatalf("dropped=%d, want 5 overflow drops for the slow client", dropped)
	}
	if got := len(drain(ch)); got != clientQueue {
		t.Errorf("client queue held %d, want %d", got, clientQueue)
	}
}

// With a client attached, events go live and NOT into the offline buffer —
// otherwise a long-lived connection would slowly fill the buffer reserved for
// the next disconnection.
func TestLiveClientBypassesTheBuffer(t *testing.T) {
	h := NewHub()
	ch, _, _ := h.Subscribe()
	defer h.Unsubscribe(ch)
	h.Emit(&probe{Kind: "message", N: 1})
	if _, buffered, _ := h.Stats(); buffered != 0 {
		t.Errorf("buffered %d while a client was attached", buffered)
	}
	if got := len(drain(ch)); got != 1 {
		t.Errorf("client received %d events, want 1", got)
	}
}

// An event that cannot be serialized is a bug in this process, not a transport
// failure — but the adapter still did not get it, so it is counted as a drop
// rather than discarded.
func TestUnserializableEventIsCountedAsADrop(t *testing.T) {
	h := NewHub()
	h.Emit(make(chan int)) // channels have no JSON representation
	_, buffered, dropped := h.Stats()
	if dropped != 1 || buffered != 0 {
		t.Fatalf("dropped=%d buffered=%d, want 1/0", dropped, buffered)
	}
}

func TestUnsubscribeIsIdempotent(t *testing.T) {
	h := NewHub()
	ch, _, _ := h.Subscribe()
	h.Unsubscribe(ch)
	// A second call must not close an already-closed channel: the HTTP handler
	// defers this and an early return would otherwise panic the whole process
	// over one disconnecting client.
	h.Unsubscribe(ch)
	if clients, _, _ := h.Stats(); clients != 0 {
		t.Errorf("clients=%d after unsubscribe", clients)
	}
}
