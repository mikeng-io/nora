package main

// Ids are parsed into their domain value objects at the HTTP edge. Two
// properties of that, both about WHEN the parse happens:
//
//  1. a malformed id never reaches Instagram, not even the route lookup;
//  2. it is parsed BEFORE the session gate (P14, 16 §Resources: "a malformed
//     id is 400 before any use case runs"), so a malformed id is 400 whatever
//     the session's state. Before P14 the gate ran first and a checkpointed
//     session answered 409 to a malformed id; that order moved with the
//     resource paths, as this file said it would.

import (
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"

	"instagram-bridge/internal/domain"
)

// badRoomIDs are room keys that must be refused. The last is the LONG thread
// id: all digits, so a naive "is it numeric" check would wave it through, and
// a room key in the wrong id space is precisely the mistake the whole
// translation layer exists to make impossible. It overflows int64.
var badRoomIDs = []string{"abc", "-1", "0", "12a4", threadLong}

// roomRequest sends one well-formed request to one thread route, so the room
// key is the only thing wrong.
func roomRequest(t *testing.T, b *testBridge, route, room string) *httptest.ResponseRecorder {
	t.Helper()
	switch route {
	case "members":
		rec := httptest.NewRecorder()
		b.routes().ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/threads/"+room+"/members", nil))
		return rec
	case "send text":
		return sendMessage(t, b, room, map[string]any{"text": "hi"})
	case "send media":
		return sendMessage(t, b, room, mediaOf([]byte("x"), "", ""))
	case "reaction":
		return putReaction(t, b, room, "mid.$x", map[string]any{"emoji": "👍"})
	case "typing":
		return putTyping(t, b, room, map[string]any{"typing": true})
	case "unsend":
		return unsendMessage(t, b, room, "mid.$x")
	}
	t.Fatalf("unknown route %q", route)
	return nil
}

var roomRoutes = []string{"send text", "send media", "reaction", "typing", "unsend", "members"}

// A non-numeric room key is the CALLER's error, not Instagram's. Letting it
// through would produce a route lookup for /direct/t/0/ and a 404 that reads
// like a deleted thread. Driven through EVERY route that takes a room key.
func TestNonNumericRoomIDIsRejectedWithoutALookup(t *testing.T) {
	f := &fakeIG{}
	b := newTestBridge(t, f)
	for _, bad := range append([]string{""}, badRoomIDs...) {
		if _, err := domain.ParseThreadFBID(bad); !errors.Is(err, domain.ErrInvalidID) {
			t.Errorf("room key %q parsed as %v, want ErrInvalidID", bad, err)
		}
	}
	for _, bad := range badRoomIDs {
		for _, route := range roomRoutes {
			rec := roomRequest(t, b, route, bad)
			if rec.Code != http.StatusBadRequest {
				t.Errorf("%s with room %q answered %d, want 400", route, bad, rec.Code)
				continue
			}
			if env := decodeEnvelope(t, rec); env.Error.Code != "invalid_id" {
				t.Errorf("%s with room %q: envelope = %+v", route, bad, env)
			}
		}
	}
	if f.Fetches != 0 || len(f.GetThreadIDs) != 0 {
		t.Fatalf("a malformed room key reached the network: %d lookup(s), %d read(s)",
			f.Fetches, len(f.GetThreadIDs))
	}
}

// A checkpointed session with a MALFORMED id answers 400, not 409: the id is
// parsed at the edge, before any use case runs and so before the session
// gate. What a caller must fix first is its own request; a well-formed one
// then learns the session needs a human.
func TestAMalformedIDIs400EvenWhileCheckpointed(t *testing.T) {
	for _, state := range []domain.SessionState{domain.StateCheckpointed, domain.StateLoggedOut} {
		f := &fakeIG{}
		b := newTestBridge(t, f, withState(state, "challenge required"))
		for _, route := range roomRoutes {
			if rec := roomRequest(t, b, route, "not-a-thread"); rec.Code != http.StatusBadRequest {
				t.Errorf("session %q: %s with a malformed room answered %d, want 400",
					state, route, rec.Code)
			}
			// ...and the same route with a WELL-FORMED room is the gate's 409.
			if rec := roomRequest(t, b, route, threadShort); rec.Code != http.StatusConflict {
				t.Errorf("session %q: %s with a good room answered %d, want 409",
					state, route, rec.Code)
			}
		}
		for _, path := range []string{"/accounts/not_a_number", "/accounts/not_a_number/story", "/accounts/not_a_number/items"} {
			rec := httptest.NewRecorder()
			b.routes().ServeHTTP(rec, httptest.NewRequest(http.MethodGet, path, nil))
			if rec.Code != http.StatusBadRequest {
				t.Errorf("session %q: GET %s answered %d, want 400", state, path, rec.Code)
			}
		}
		if f.Fetches != 0 || len(f.ProfileIGIDs) != 0 || len(f.DocRequests) != 0 {
			t.Errorf("session %q: a request reached Instagram while checkpointed", state)
		}
	}
}
