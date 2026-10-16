// Package stream is the SSE fan-out behind GET /events.
package stream

// The SSE fan-out, and the one rule it exists to enforce: a message the bridge
// received and could not deliver is COUNTED, never silently forgotten.
//
// This is inherited from whatsapp-bridge, which inherited the anti-pattern it
// fixes from Hermes: an in-memory queue capped at 100 that called shift() on
// overflow with no counter and no log. For a transport feeding an audit trail,
// silent loss of rows is the failure class Nora's design history treats as a
// bug. Here the oldest event is still the one dropped — the buffer has to be
// bounded or a long disconnection becomes an OOM — but the count survives on
// /health forever and the next client to connect is told once, explicitly.

import (
	"encoding/json"
	"sync"
)

const (
	// bufferCap is the number of events held while NO client is connected.
	// Sized like whatsapp-bridge's: large enough that an adapter restart or a
	// short Nora outage replays completely, small enough that a bridge left
	// running for a week with nothing attached cannot exhaust the container.
	bufferCap = 500
	// clientQueue is the per-client send buffer. A slow reader must never
	// block the Instagram event handler — instameow dispatches deltas on the
	// socket's own goroutine, so blocking here would stall the whole realtime
	// stream and turn one stuck HTTP client into a dead bridge.
	clientQueue = 256
)

type Hub struct {
	mu      sync.Mutex
	clients map[chan []byte]struct{}
	buf     [][]byte
	dropped int
	// notice is set when something was dropped and not yet announced. It is
	// consumed by the NEXT client to connect, exactly once; the cumulative
	// count stays on /health permanently. One-shot because the notice answers
	// "did I miss anything before I got here?", which is only a question at
	// connect time.
	notice bool
}

func NewHub() *Hub {
	return &Hub{clients: map[chan []byte]struct{}{}}
}

// Emit serializes once and fans out. Marshalling here rather than per client
// is not only cheaper — it guarantees every client sees byte-identical text,
// so two adapters attached to the same bridge cannot disagree about what
// arrived.
func (h *Hub) Emit(event any) {
	payload, err := json.Marshal(event)
	if err != nil {
		// An event we cannot serialize is a bug in this process, not a
		// transport failure. Counting it as a drop is the honest record: the
		// adapter did not get it, and the number says so.
		h.mu.Lock()
		h.dropped++
		h.notice = true
		h.mu.Unlock()
		return
	}
	h.mu.Lock()
	defer h.mu.Unlock()
	if len(h.clients) == 0 {
		h.push(payload)
		return
	}
	for ch := range h.clients {
		select {
		case ch <- payload:
		default:
			// This client is not draining. Drop for THIS client only and
			// count it; the alternative is blocking the socket goroutine on a
			// reader that may never come back.
			h.dropped++
			h.notice = true
		}
	}
}

// push appends to the offline buffer, evicting the OLDEST on overflow.
// Oldest-first is deliberate: when Nora reconnects, the recent end of the
// conversation is the part that still has a chance of being worth answering.
func (h *Hub) push(payload []byte) {
	if len(h.buf) >= bufferCap {
		h.buf = h.buf[1:]
		h.dropped++
		h.notice = true
	}
	h.buf = append(h.buf, payload)
}

// Subscribe registers a client and returns its channel plus whatever was
// buffered while nobody was listening.
//
// Registration happens BEFORE the buffer snapshot, under one lock. The reverse
// order has a window in which an event arrives after the snapshot and before
// registration, is buffered, and is then dropped by the caller's clear() — a
// loss that would show up as nothing at all.
func (h *Hub) Subscribe() (chan []byte, [][]byte, int) {
	h.mu.Lock()
	defer h.mu.Unlock()
	ch := make(chan []byte, clientQueue)
	h.clients[ch] = struct{}{}
	replay := h.buf
	h.buf = nil
	notice := 0
	if h.notice && h.dropped > 0 {
		notice = h.dropped
		h.notice = false
	}
	return ch, replay, notice
}

func (h *Hub) Unsubscribe(ch chan []byte) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if _, ok := h.clients[ch]; ok {
		delete(h.clients, ch)
		close(ch)
	}
}

func (h *Hub) Stats() (clients, buffered, dropped int) {
	h.mu.Lock()
	defer h.mu.Unlock()
	return len(h.clients), len(h.buf), h.dropped
}

// HasClients gates the heartbeat. A heartbeat exists to age
// `last_event_age_seconds` on the consumer's side; buffering heartbeats for a
// consumer that is not there would fill the offline buffer with ticks and
// evict the real messages it was reserved for.
func (h *Hub) HasClients() bool {
	h.mu.Lock()
	defer h.mu.Unlock()
	return len(h.clients) > 0
}
