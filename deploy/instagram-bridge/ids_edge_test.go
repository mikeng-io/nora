package main

// Ids are parsed into their domain value objects at the HTTP edge. Two
// properties of that, both about WHEN the parse happens:
//
//  1. a malformed id never reaches Instagram — not even the route lookup;
//  2. it is parsed AFTER the session gate, so a checkpointed session answers
//     409 ("a human must act") ahead of a malformed id's 400. That is the
//     order these routes had before the ids were types; moving the parse
//     first is a status change, and it belongs with the resource paths (P14).

import (
	"encoding/base64"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"

	"instagram-bridge/internal/domain"
)

// badRoomIDs are room keys that must be refused. The last is the LONG thread
// id: all digits, so a naive "is it numeric" check would wave it through —
// and a room_id in the wrong id space is precisely the mistake the whole
// translation layer exists to make impossible. It overflows int64.
var badRoomIDs = []string{"abc", "-1", "0", "12a4", threadLong}

// roomRequest sends one room-keyed request to one route. Every body carries
// everything every route needs, so the room key is the only thing wrong.
func roomRequest(t *testing.T, b *server, route, room string) *httptest.ResponseRecorder {
	t.Helper()
	if route == "members" {
		req := httptest.NewRequest(http.MethodGet, "/threads/"+room+"/members", nil)
		rec := httptest.NewRecorder()
		b.routes().ServeHTTP(rec, req)
		return rec
	}
	return post(t, b, route, map[string]any{
		"room_id": room, "text": "hi", "message_id": "mid.$x", "emoji": "👍",
		"data_base64": base64.StdEncoding.EncodeToString([]byte("x")),
	})
}

var roomRoutes = []string{"/send", "/send-media", "/react", "/typing", "/unsend", "members"}

// A non-numeric room_id is the CALLER's error, not Instagram's. Letting it
// through would produce a route lookup for /direct/t/0/ and a 404 that reads
// like a deleted thread. Driven through EVERY route that takes a room key.
func TestNonNumericRoomIDIsRejectedWithoutALookup(t *testing.T) {
	f := &fakeIG{}
	b := newTestBridge(t, f)
	for _, bad := range append([]string{""}, badRoomIDs...) {
		if _, err := domain.ParseThreadFBID(bad); !errors.Is(err, domain.ErrInvalidID) {
			t.Errorf("room_id %q parsed as %v, want ErrInvalidID", bad, err)
		}
	}
	for _, bad := range badRoomIDs {
		for _, route := range roomRoutes {
			if rec := roomRequest(t, b, route, bad); rec.Code != http.StatusBadRequest {
				t.Errorf("%s with room_id %q answered %d, want 400", route, bad, rec.Code)
			}
		}
	}
	// An EMPTY room key on the POST routes is the handler's 400 too (the
	// members route refuses an empty segment in the router instead; see
	// TestRosterStatusMapping).
	for _, route := range roomRoutes[:5] {
		if rec := roomRequest(t, b, route, ""); rec.Code != http.StatusBadRequest {
			t.Errorf("%s with an empty room_id answered %d, want 400", route, rec.Code)
		}
	}
	if f.Fetches != 0 || len(f.GetThreadIDs) != 0 {
		t.Fatalf("a malformed room_id reached the network: %d lookup(s), %d read(s)",
			f.Fetches, len(f.GetThreadIDs))
	}
}

// A checkpointed session with a MALFORMED id answers 409, not 400: the
// session gate runs first. Retrying a challenged account is how a captcha
// becomes a ban, so "a human must act" is the answer that must win.
func TestACheckpointedSessionAnswers409EvenForAMalformedID(t *testing.T) {
	for _, state := range []domain.SessionState{domain.StateCheckpointed, domain.StateLoggedOut} {
		f := &fakeIG{}
		b := newTestBridge(t, f, withState(state, "challenge required"))
		for _, route := range roomRoutes {
			if rec := roomRequest(t, b, route, "not-a-thread"); rec.Code != http.StatusConflict {
				t.Errorf("session %q: %s with a malformed room_id answered %d, want 409",
					state, route, rec.Code)
			}
		}
		for _, path := range []string{"/accounts/nka_bpumvg", "/accounts/nka_bpumvg/story"} {
			rec := httptest.NewRecorder()
			b.routes().ServeHTTP(rec, httptest.NewRequest(http.MethodGet, path, nil))
			if rec.Code != http.StatusConflict {
				t.Errorf("session %q: GET %s answered %d, want 409", state, path, rec.Code)
			}
		}
		if f.Fetches != 0 || len(f.ProfileIGIDs) != 0 || len(f.ReelIDs) != 0 {
			t.Errorf("session %q: a request reached Instagram while checkpointed", state)
		}
	}
}
