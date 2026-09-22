package main

// GET /profile, tested for the things that fail SILENTLY.
//
// This route's answer gates a fetch, so its failure modes are not "wrong
// output" but "a refusal Nora then says about a person, on evidence that does
// not exist". The tests below are chosen for that:
//
//  1. `extra` must carry the unmodelled wire keys. `is_private` is NOT on
//     `slidetypes.User`, so the whole public-or-followed gate depends on
//     `Unrecognized` reaching the caller. A route that dropped it would 200
//     with a plausible body and make the operator's rule unimplementable, with
//     nothing anywhere saying so.
//  2. an error must NOT degrade to `following: false`. That is the one wrong
//     answer that reads as a real one.
//  3. the IGID goes out, not the fbid. Both are decimal strings, so the wrong
//     one validates, reaches Instagram, and names a different account or none.
//  4. `viewer` must not reach the body. It is Nora on every call.

import (
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"

	"go.mau.fi/mautrix-meta/pkg/instameow/slidetypes"
	"go.mau.fi/mautrix-meta/pkg/messagix/httpclient"
)

func getProfile(t *testing.T, b *bridge, query string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(http.MethodGet, "/profile"+query, nil)
	rec := httptest.NewRecorder()
	b.routes().ServeHTTP(rec, req)
	return rec
}

// The gate's two halves, on the wire, under their own names.
//
// `following` is typed and comes off `FriendshipStatus`. `is_private` is not
// modelled anywhere in the library and survives ONLY through `Unrecognized` →
// `extra`. Asserting both in one test is deliberate: they are the two halves
// of one rule, and a change that keeps one and loses the other still leaves
// the gate undecidable.
func TestProfileCarriesTheGateIncludingUnmodelledKeys(t *testing.T) {
	b := newTestBridge(t, &fakeIG{})

	rec := getProfile(t, b, "?igid=221731317")

	if rec.Code != http.StatusOK {
		t.Fatalf("/profile answered %d: %s", rec.Code, rec.Body.String())
	}
	var body profileBody
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatalf("profile body did not decode: %v (%s)", err, rec.Body.String())
	}
	if !body.Following {
		t.Error("following was false; the measured account is one Nora follows, " +
			"and this is the half of the gate that is typed")
	}
	if got, ok := body.Extra["is_private"]; !ok || got != true {
		t.Errorf("extra[is_private] = %v (present=%v); `is_private` is NOT a field "+
			"on slidetypes.User, so dropping Unrecognized makes the public half of "+
			"the operator's gate unreadable — and the body still looks fine",
			got, ok)
	}
	if body.IGID != "221731317" {
		t.Errorf("igid = %q, want the id off the RESPONSE", body.IGID)
	}
	if body.FBID != "110966695387753" {
		t.Errorf("fbid = %q; the messaging id is carried so a caller can join "+
			"the two spaces without a second round trip", body.FBID)
	}
	if !body.HasStory {
		t.Error("has_story was false with latest_reel_media set; a caller uses " +
			"this to SKIP a story fetch, and a story fetch is visible to its author")
	}
}

// Nora's own account rides in `ProfilePageResponse.Viewer` on every call. It is
// not the subject of the read and must not appear in a body that describes
// somebody else.
func TestProfileDoesNotLeakTheViewer(t *testing.T) {
	b := newTestBridge(t, &fakeIG{})

	rec := getProfile(t, b, "?igid=221731317")

	if rec.Code != http.StatusOK {
		t.Fatalf("/profile answered %d: %s", rec.Code, rec.Body.String())
	}
	if got := rec.Body.String(); contains(got, "79597894900") || contains(got, "not_____nora") {
		t.Errorf("the viewer reached the body: %s", got)
	}
}

func contains(haystack, needle string) bool {
	return len(needle) > 0 && len(haystack) >= len(needle) &&
		(func() bool {
			for i := 0; i+len(needle) <= len(haystack); i++ {
				if haystack[i:i+len(needle)] == needle {
					return true
				}
			}
			return false
		})()
}

// A failed read must not answer 200. `following: false` on a Go zero value is
// indistinguishable from a measured "Nora does not follow them" — and the
// caller would turn that into a sentence refusing to read someone's content
// because of a network error.
func TestProfileFailureIsNeverAZeroValueGate(t *testing.T) {
	for _, tc := range []struct {
		name  string
		fake  *fakeIG
		want  int
		query string
	}{
		{
			// 200 with no user: what a GraphQL read does when it finds
			// nothing. NOT an error, which is why the route has to check.
			name:  "no user in the response",
			fake:  &fakeIG{profileUserSet: true, profileUser: nil},
			want:  http.StatusNotFound,
			query: "?igid=221731317",
		},
		{
			name:  "transport error",
			fake:  &fakeIG{profileErr: errors.New("connection reset")},
			want:  http.StatusInternalServerError,
			query: "?igid=221731317",
		},
		{
			// A human has to clear this one, and a retry hammers a challenged
			// account — the same mapping every other route uses.
			name:  "checkpointed session",
			fake:  &fakeIG{profileErr: httpclient.ErrCheckpointRequired},
			want:  http.StatusConflict,
			query: "?igid=221731317",
		},
		{
			name:  "not a decimal id",
			fake:  &fakeIG{},
			want:  http.StatusBadRequest,
			query: "?igid=nka_bpumvg",
		},
		{
			// A username is the obvious wrong thing to pass; so is an empty
			// one, and it must not reach the network as a 400 from Instagram.
			name:  "no igid at all",
			fake:  &fakeIG{},
			want:  http.StatusBadRequest,
			query: "",
		},
	} {
		t.Run(tc.name, func(t *testing.T) {
			b := newTestBridge(t, tc.fake)

			rec := getProfile(t, b, tc.query)

			if rec.Code != tc.want {
				t.Fatalf("/profile answered %d, want %d: %s",
					rec.Code, tc.want, rec.Body.String())
			}
			var body profileBody
			// The body may or may not decode into profileBody (an error body
			// has a different shape); what must NOT happen is a decodable
			// profile whose gate reads as a measured refusal.
			if err := json.Unmarshal(rec.Body.Bytes(), &body); err == nil && body.OK {
				t.Errorf("a failed read produced ok=true: %s", rec.Body.String())
			}
		})
	}
}

// The IGID, not the fbid, and canonicalized.
//
// Both id spaces are decimal strings, so passing the wrong one produces no
// error anywhere — it names a different account, or none, and the caller gets
// a confident wrong answer. Asserting what went OUT is the only way this can
// fail; asserting the response cannot, because the fake would answer either.
func TestProfileAddressesTheIGIDSpaceAndCanonicalizes(t *testing.T) {
	f := &fakeIG{}
	b := newTestBridge(t, f)

	// Leading zeros: the same account, spelled two ways. Left alone it would
	// be two cache keys and two identities for one person.
	if rec := getProfile(t, b, "?igid=0221731317"); rec.Code != http.StatusOK {
		t.Fatalf("/profile answered %d: %s", rec.Code, rec.Body.String())
	}
	if len(f.profileIGIDs) != 1 || f.profileIGIDs[0] != "221731317" {
		t.Fatalf("GetProfile was addressed with %v, want [221731317] canonicalized",
			f.profileIGIDs)
	}
}

// A public account Nora has never had a thread with has no messaging row, so
// `interop_messaging_user_fbid` is 0. That is a missing JOIN KEY, not a failed
// read: refusing here would make every not-yet-messaged account unreadable,
// which is exactly the set the operator's public-or-followed rule is for.
func TestProfileWithNoMessagingIDStillAnswers(t *testing.T) {
	f := &fakeIG{profileUserSet: true, profileUser: &slidetypes.User{
		ID: "276321323", Username: "_qnpdwdmqvgho",
	}}
	b := newTestBridge(t, f)

	rec := getProfile(t, b, "?igid=276321323")

	if rec.Code != http.StatusOK {
		t.Fatalf("/profile answered %d: %s", rec.Code, rec.Body.String())
	}
	var body profileBody
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatalf("profile body did not decode: %v", err)
	}
	if body.FBID != "" {
		t.Errorf("fbid = %q, want empty — 0 means no messaging row, and "+
			"formatting it as \"0\" would be an id that names nobody", body.FBID)
	}
	if !body.OK {
		t.Error("ok was false; a missing join key is not a failed read")
	}
}
