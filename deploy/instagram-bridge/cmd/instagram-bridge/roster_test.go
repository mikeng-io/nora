package main

// GET /threads/{thread_fbid}/members, tested for the three things that can go wrong silently:
//
//  1. the WRONG ID SPACE goes out, and Instagram answers 200 with nothing —
//     the same failure shape that made the reaction bug invisible for a month;
//  2. the two id spaces get CONFUSED on the way back, so a mention is
//     addressed with a public id that no mention can carry and the tag goes
//     out as plain text with no ping and nothing logged;
//  3. the roster comes back SHORT, which is the one direction that is unsafe:
//     `Whereis` refuses a room holding an account it cannot name, so a roster
//     that has quietly forgotten somebody is one the disclosure gate approves.
//
// The fake refuses the long thread id and refuses to invent members, so each
// of these can actually fail.

import (
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"

	"instagram-bridge/internal/domain"
	gateway "instagram-bridge/internal/gateway/instameow"

	"go.mau.fi/mautrix-meta/pkg/instameow/slidetypes"
	"go.mau.fi/mautrix-meta/pkg/messagix/httpclient"
)

func getRoster(t *testing.T, b *testBridge, query string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(http.MethodGet, "/threads/"+query+"/members", nil)
	rec := httptest.NewRecorder()
	b.routes().ServeHTTP(rec, req)
	return rec
}

func decodeRoster(t *testing.T, rec *httptest.ResponseRecorder) rosterBody {
	t.Helper()
	var body rosterBody
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatalf("roster body did not decode: %v (%s)", err, rec.Body.String())
	}
	return body
}

// BOTH ids, per member, under their own names. This is the assertion the whole
// route exists for: `fbid` is the messaging identity a mention carries and
// `sender_fbid` is, `igid` is the public id the profile and story reads take,
// neither is derivable from the other, and no length or prefix rule separates
// them — 15 digits and 17 digits here, one of them sharing Nora's own `1784…`
// prefix. Naming them on the wire is what stops a consumer guessing.
func TestRosterCarriesBothIDSpacesPerMember(t *testing.T) {
	b := newTestBridge(t, &fakeIG{})

	rec := getRoster(t, b, threadShort)

	if rec.Code != http.StatusOK {
		t.Fatalf("members read answered %d: %s", rec.Code, rec.Body.String())
	}
	body := decodeRoster(t, rec)
	want := []rosterMember{
		{FBID: "110966695387753", IGID: "60739041440", Username: "nka_bpumvg", FullName: "Miles"},
		{FBID: "12116372067347151", IGID: "74340223606", Username: "third_member", FullName: ""},
	}
	if len(body.Members) != len(want) {
		t.Fatalf("roster returned %d members, want %d: %+v", len(body.Members), len(want), body.Members)
	}
	for i, member := range body.Members {
		if member != want[i] {
			t.Errorf("member %d = %+v, want %+v", i, member, want[i])
		}
	}
	if body.RoomID != threadShort {
		t.Errorf("room_id = %q, want the short id %q", body.RoomID, threadShort)
	}
	if !body.IsGroup || body.Title != "the group" {
		t.Errorf("roster envelope = %+v", body)
	}
}

// The room key on the wire is the SHORT id, and the short id is what goes to
// Instagram. The fake answers 200-with-no-thread for the long one — exactly
// what the wire does — so addressing it the wrong way is a failure here rather
// than a shrug.
func TestRosterReadsTheShortThreadID(t *testing.T) {
	f := &fakeIG{}
	b := newTestBridge(t, f)

	if rec := getRoster(t, b, threadShort); rec.Code != http.StatusOK {
		t.Fatalf("members read answered %d", rec.Code)
	}
	if len(f.GetThreadIDs) != 1 || f.GetThreadIDs[0] != threadShort {
		t.Fatalf("GetThread was addressed with %v, want [%s]", f.GetThreadIDs, threadShort)
	}
	// And the long id never crosses the HTTP boundary in either direction: a
	// caller that sends it is refused, not served.
	if rec := getRoster(t, b, threadLong); rec.Code == http.StatusOK {
		t.Fatalf("members read served the LONG thread id: %s", rec.Body.String())
	}
}

// The short id is canonicalized before it is used, the same way every other
// route's is — "012…" and "12…" are one thread, and two spellings would be two
// cache entries and two different `room_id` echoes for one room.
func TestRosterCanonicalizesTheRoomID(t *testing.T) {
	f := &fakeIG{}
	b := newTestBridge(t, f)

	rec := getRoster(t, b, "0"+threadShort)

	if rec.Code != http.StatusOK {
		t.Fatalf("members read answered %d: %s", rec.Code, rec.Body.String())
	}
	if body := decodeRoster(t, rec); body.RoomID != threadShort {
		t.Errorf("room_id echoed as %q, want the canonical %q", body.RoomID, threadShort)
	}
}

// MEASURED: `Users[]` does not include the viewer. Nora absent from her own
// room's roster is CORRECT — she is the one asking, and counted as a member
// she resolves to nobody in identity.toml and becomes a stranger in the room,
// which alone makes `where_is` refuse.
//
// This pins that the route does not synthesize her back in. It is the test a
// future "the roster is missing Nora" bug report would break.
func TestRosterDoesNotInventSelfAsAMember(t *testing.T) {
	b := newTestBridge(t, &fakeIG{})

	body := decodeRoster(t, getRoster(t, b, threadShort))

	for _, member := range body.Members {
		if member.FBID == b.gw.Snapshot().Self.FBID {
			t.Fatalf("the roster carries Nora's own account: %+v", member)
		}
	}
}

// ...and if the library ever DOES start putting the viewer in `Users[]`, the
// route drops it rather than handing Nora back an account she cannot name.
// Belt and braces: the measurement says this never fires today.
func TestRosterDropsSelfIfTheWireEverIncludesIt(t *testing.T) {
	f := &fakeIG{ThreadUsersSet: true, ThreadUsers: append(
		[]*slidetypes.User{{
			InteropMessagingUserFBID: 14963523149149302,
			ID:                       "79597894900",
			Username:                 "not_____nora",
		}},
		measuredUsers...,
	)}
	b := newTestBridge(t, f)

	body := decodeRoster(t, getRoster(t, b, threadShort))

	if len(body.Members) != len(measuredUsers) {
		t.Fatalf("roster returned %d members, want %d: %+v",
			len(body.Members), len(measuredUsers), body.Members)
	}
	for _, member := range body.Members {
		if member.FBID == "14963523149149302" {
			t.Fatalf("self survived the filter: %+v", member)
		}
	}
}

// A thread that reads back with NO members is the read failing, not the room
// emptying — a group Nora is in always holds at least one other account. Served
// as a 200 with `members: []` it would be acted on as «there is nobody here»,
// which is the sentence `Roster.available` exists to keep separate from «I
// could not look».
func TestRosterRefusesAnEmptyMemberList(t *testing.T) {
	f := &fakeIG{ThreadUsersSet: true, ThreadUsers: nil}
	b := newTestBridge(t, f)

	rec := getRoster(t, b, threadShort)

	// 502 since P14: Instagram answered, and the answer was not a room. It
	// was 500 under the legacy status table, which had no 502.
	if rec.Code != http.StatusBadGateway {
		t.Fatalf("an empty roster answered %d, want 502: %s", rec.Code, rec.Body.String())
	}
}

// A thread the read does not return is 502, not an empty 200. It is also not
// an error from the library: GraphQL answers with the thread simply absent, so
// nothing raises and only an explicit check catches it.
func TestRosterRefusesAThreadThatReadsBackAbsent(t *testing.T) {
	b := newTestBridge(t, &fakeIG{NoThread: true})

	rec := getRoster(t, b, threadShort)

	if rec.Code != http.StatusBadGateway {
		t.Fatalf("an absent thread answered %d, want 502: %s", rec.Code, rec.Body.String())
	}
	if env := decodeEnvelope(t, rec); env.Error.Code != "upstream_rejected" {
		t.Fatalf("a refused roster answered envelope %+v, want code upstream_rejected", env)
	}
}

// A member with no messaging id fails the WHOLE read rather than being skipped.
//
// Skipping is the tempting choice and it is the unsafe one: the other rows
// still look like a complete room, so the caller gets a roster that is short by
// one with nothing to tell it so — and `Whereis`'s refusal is keyed on
// unnamed accounts it CAN see. An account it cannot see at all is one the
// disclosure gate never gets to refuse over.
func TestRosterRefusesRatherThanUnderReportTheRoom(t *testing.T) {
	f := &fakeIG{ThreadUsersSet: true, ThreadUsers: []*slidetypes.User{
		measuredUsers[0],
		{InteropMessagingUserFBID: 0, ID: "74340223606", Username: "third_member"},
	}}
	b := newTestBridge(t, f)

	rec := getRoster(t, b, threadShort)

	if rec.Code != http.StatusBadGateway {
		t.Fatalf("an unaccountable member answered %d, want 502: %s", rec.Code, rec.Body.String())
	}
	if body := decodeRoster(t, rec); len(body.Members) != 0 {
		t.Fatalf("a refused roster still carried members: %+v", body.Members)
	}
	decodeEnvelope(t, rec)
}

// The status mapping, which is what the Python side's retry and degradation
// decisions read. 409 means a human must act; answering 503 here would make a
// caller retry a challenged account, which is how a captcha becomes a ban.
func TestRosterStatusMapping(t *testing.T) {
	for _, tc := range []struct {
		name  string
		setup func(*gateway.Options, *fakeIG)
		query string
		want  int
	}{
		{
			// 404, not 400, and the change is deliberate. `/threads//members`
			// is not a path this router serves, so the refusal now happens
			// BEFORE the handler — which is the stronger place for it: a
			// missing resource id cannot reach code that might read it as
			// empty-but-present. The old `?room_id=` shape could carry an
			// empty value all the way in, and the handler had to catch it.
			name:  "no thread id at all is not a resource this server has",
			query: "",
			// 307, and it is worth knowing WHY rather than just pinning it:
			// `/threads//members` has an empty segment, and `http.ServeMux`
			// CLEANS the path and redirects to `/threads/members` — which this
			// server does not serve either. So a caller that builds a URL with
			// a missing id gets a redirect to a 404 rather than a 400.
			//
			// Pinned as-is rather than "fixed" to 400: the refusal now happens
			// in the ROUTER, before any handler can read the id as
			// empty-but-present, and that is the stronger place for it. The
			// handler's own empty check is belt-and-braces now, not the
			// mechanism.
			want: http.StatusTemporaryRedirect,
		},
		{
			name:  "a room_id that is not a decimal thread_fbid",
			query: "not-a-thread",
			want:  http.StatusBadRequest,
		},
		{
			name:  "checkpointed: a human must clear it, never retry",
			setup: func(o *gateway.Options, _ *fakeIG) { withState(domain.StateCheckpointed, "captcha")(o) },
			query: threadShort,
			want:  http.StatusConflict,
		},
		{
			name:  "logged out: same — new cookies, not a retry",
			setup: func(o *gateway.Options, _ *fakeIG) { withState(domain.StateLoggedOut, "gone")(o) },
			query: threadShort,
			want:  http.StatusConflict,
		},
		{
			name: "a checkpoint discovered during the read is still 409",
			setup: func(_ *gateway.Options, f *fakeIG) {
				f.ThreadErr = httpclient.ErrCheckpointRequired
			},
			query: threadShort,
			want:  http.StatusConflict,
		},
		{
			name:  "the session is being re-established: retryable",
			setup: func(o *gateway.Options, _ *fakeIG) { withNoClient()(o) },
			query: threadShort,
			want:  http.StatusServiceUnavailable,
		},
		{
			name: "an unclassified failure is 500, never a 503 that invites a retry loop",
			setup: func(_ *gateway.Options, f *fakeIG) {
				f.ThreadErr = errors.New("something nobody has a name for")
			},
			query: threadShort,
			want:  http.StatusInternalServerError,
		},
	} {
		t.Run(tc.name, func(t *testing.T) {
			f := &fakeIG{}
			b := newTestBridge(t, f, func(o *gateway.Options) {
				if tc.setup != nil {
					tc.setup(o, f)
				}
			})
			if rec := getRoster(t, b, tc.query); rec.Code != tc.want {
				t.Fatalf("answered %d, want %d: %s", rec.Code, tc.want, rec.Body.String())
			}
		})
	}
}

// A checkpointed session is refused BEFORE the read, not after: a challenged
// account must not be touched at all.
func TestRosterDoesNotReachInstagramWhileCheckpointed(t *testing.T) {
	f := &fakeIG{}
	b := newTestBridge(t, f, withState(domain.StateCheckpointed, "captcha"))

	getRoster(t, b, threadShort)

	if len(f.GetThreadIDs) != 0 {
		t.Fatalf("a checkpointed session still read the thread: %v", f.GetThreadIDs)
	}
}
