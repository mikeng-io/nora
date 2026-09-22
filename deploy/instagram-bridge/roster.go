package main

// Roster: who is in one group thread, in BOTH id spaces.
//
// This is the only READ route on the bridge that answers about people rather
// than about the bridge itself, and it exists because three things in Nora are
// broken without it: `group_members` answers «I can't tell who is in this
// room», `where_is` cannot establish the room at all and refuses, and an
// outbound @-mention resolves to nobody and goes out as plain text with no
// ping and nothing logged.
//
// # Both ids, per member, always
//
// `fbid` is `interop_messaging_user_fbid` — the MESSAGING identity. It is what
// an inbound delta's `sender_fbid` carries and what an @-mention carries, so
// it is the only number a mention may be addressed with and the only one that
// can answer "did this person speak here?".
//
// `igid` is `User.ID`, the PUBLIC Instagram numeric id — a different number
// for the same account, and the one `GetProfile`/`FetchReel` take. It is NOT a
// messaging address (02-identity-and-policy.md opens by naming exactly this
// confusion, whose whole cost is that it produces no error anywhere).
//
// Neither is derivable from the other and no shape rule separates them — the
// measured fbids on the live three-person room were 15 and 17 digits, one of
// them sharing Nora's own `1784…` prefix. So the wire carries both, named, and
// nothing downstream has to guess which space a number came from.
//
// # `Users[]` DOES NOT INCLUDE SELF — measured
//
// The live three-person room read back TWO users, not three: Instagram's
// viewer is carried separately (`ThreadInfo.Viewer`/`ViewerID`) and is absent
// from `Users[]`. **Nora missing from this list is correct and must not be
// "fixed".** Nora is not a member of the room for Nora's purposes — she is the
// one asking — and `nora.agent.room` excludes her own account from every
// roster it resolves for precisely this reason: counted as a member she
// resolves to nobody in `identity.toml` and becomes "one account I don't have
// a name for", which is a stranger in the room, who is her, and which alone
// makes `where_is` refuse.
//
// The filter below is therefore BELT AND BRACES, not the mechanism: the
// measurement says self is already absent, and the filter makes the route's
// contract true by construction rather than by a library version's behaviour.
// It logs when it actually drops something, because the day that log line
// appears is the day the measurement above stopped being true.
//
// # Fail closed, never under-report
//
// An under-reported roster is the one dangerous direction here. `Whereis`
// decides disclosure by checking every account this route reports against the
// location circle, so a roster that has quietly FORGOTTEN somebody is a
// disclosure made against a room nobody checked. Every shape this route
// cannot fully account for — no thread, no users, a user with no messaging id
// — is a 500 with a named error, never a short list and never an empty one.
// `Roster(available=false)` on the Python side costs an honest «I couldn't
// look»; a silently short list costs a disclosure.
//
// One correction to the argument above, made 2026-09-22 after the room this
// ships into set `location = true`: that flag waives the `unnamed > 0`
// refusal (DEC-79), so in THAT room an account nobody can name no longer
// stops a disclosure either. The refusal this paragraph leans on is disarmed
// there. Nothing below changes — a smaller margin is a reason to stay strict,
// not a licence to relax — but the claim should not be read as stronger than
// it is.

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"strconv"

	"go.mau.fi/mautrix-meta/pkg/instameow/slidetypes"
)

// rosterMember is one account in the thread. `fbid` is the only field any
// consumer matches on today; the rest are carried because the read already has
// them and an operator debugging «who is this unmapped account?» from a log
// line has nothing else to go on.
//
// `username` is EXPLICITLY not an identity. It is renameable and on Instagram
// the freed name can be taken by somebody else, so matching on it would unmap
// a person on a rename and resolve a stranger AS them — identity.example.toml
// says the same thing at the other end of the wire.
type rosterMember struct {
	FBID     string `json:"fbid"`
	IGID     string `json:"igid"`
	Username string `json:"username"`
	FullName string `json:"full_name"`
}

// rosterBody is the 200 shape. `members` is never null and never empty on a
// 200 — see the fail-closed note in the file header — so a consumer that sees
// `ok: true` has a list it can act on.
type rosterBody struct {
	OK     bool   `json:"ok"`
	RoomID string `json:"room_id"`
	Title  string `json:"title"`
	// IsGroup is passed through rather than enforced. The bridge is transport;
	// whether a 1:1 thread is admitted at all is Nora's policy decision and is
	// already made in `nora.platform.instagram` (deny-by-default this phase).
	IsGroup bool           `json:"is_group"`
	Members []rosterMember `json:"members"`
}

var (
	// errNoThread is a read that came back without the thread. Distinct from
	// an empty member list because they mean different things to an operator:
	// one is a thread id that names nothing, the other is a thread that reads
	// as having nobody in it.
	errNoThread = errors.New("thread read returned no thread")
	// errEmptyRoster is a thread that read back with no users at all. A group
	// Nora is in always has at least one other member, so this is the read
	// failing, not the room emptying — and reporting it as an empty roster is
	// the one answer that would be acted on as «there is nobody here».
	errEmptyRoster = errors.New("thread read returned no members")
)

// handleRoster answers GET /roster?room_id=<short thread_fbid>.
//
// A GET with the room key in the query string rather than a POST body, because
// it is a read with no side effect — the same shape /health and /events have,
// and the opposite of the six mutating routes. The room key is the SHORT
// thread id, exactly as every other route takes it; the long id never crosses
// this boundary in either direction.
//
// The status mapping is the same one the send routes use, and for the same
// reasons (see the table at the top of server.go): 409 for a checkpointed or
// logged-out session because a human has to act and a retry would hammer a
// challenged account, 503 for a session being re-established because that one
// does heal itself, 400 for a room_id that is not a decimal thread_fbid, and
// 500 for anything the bridge cannot classify — never a 200 with a partial
// answer.
func (b *bridge) handleRoster(w http.ResponseWriter, r *http.Request) {
	roomID := r.URL.Query().Get("room_id")
	if roomID == "" {
		badRequest(w, "room_id is required")
		return
	}
	cli, ok := b.ready(w)
	if !ok {
		return
	}
	// resolveRoom is doing two jobs here and only one of them is the long id,
	// which this route never uses: it is also what validates that `room_id`
	// parses (400, not a 5xx from Instagram) and what warms the short<->long
	// cache the typing path reads. The lookup is cached after the first call,
	// so this costs a round trip once per thread per process.
	thread, ok := b.resolveRoom(w, r, roomID)
	if !ok {
		return
	}
	info, err := readRoster(r.Context(), cli, thread.Short, b.ownFBID())
	if err != nil {
		status := statusForError(err)
		logf("roster failed (room=%s status=%d): %v", thread.Short, status, err)
		writeJSON(w, status, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	info.RoomID = thread.Short
	writeJSON(w, http.StatusOK, info)
}

// readRoster reads one thread's membership.
//
// `selfFBID` is the viewer's own `interop_messaging_user_fbid`, or 0 when the
// bridge does not know it. It is used only to drop self if the library ever
// starts including the viewer in `Users[]` — see the file header for why that
// is a guard rather than the mechanism.
func readRoster(
	ctx context.Context, cli igClient, shortThreadID string, selfFBID int64,
) (rosterBody, error) {
	// The SHORT id, like every other read and like four of the five sends.
	// `GetThreadInfoRequest.ThreadFBID` says so on the field itself, and this
	// is the id the three-person roster was measured with.
	resp, err := cli.GetThread(ctx, slidetypes.MakeGetThreadInfoRequest(shortThreadID))
	if err != nil {
		return rosterBody{}, err
	}
	if resp == nil || resp.ThreadInfo.AsIGDirectThread == nil {
		return rosterBody{}, fmt.Errorf("%w: %s", errNoThread, shortThreadID)
	}
	info := resp.ThreadInfo.AsIGDirectThread
	members := make([]rosterMember, 0, len(info.Users))
	dropped := 0
	for _, user := range info.Users {
		if user == nil {
			// Not a member we can drop quietly: a nil row is a row whose
			// account we cannot name, and the whole read is refused rather
			// than silently shortened.
			return rosterBody{}, fmt.Errorf(
				"%w: thread %s returned a null user row", errNoThread, shortThreadID)
		}
		if user.InteropMessagingUserFBID == 0 {
			// Refused, not skipped. A member with no messaging id cannot be
			// mentioned, cannot be matched against a sender, and cannot be
			// mapped in identity.toml — so keeping the other rows would hand
			// back a room that is short by one, which is the direction that
			// lets a disclosure through.
			return rosterBody{}, fmt.Errorf(
				"thread %s returned a member with no interop_messaging_user_fbid "+
					"(igid=%q username=%q); refusing the whole roster rather than "+
					"under-report the room",
				shortThreadID, user.ID, user.Username)
		}
		fbid := strconv.FormatInt(user.InteropMessagingUserFBID, 10)
		if selfFBID != 0 && user.InteropMessagingUserFBID == selfFBID {
			dropped++
			continue
		}
		members = append(members, rosterMember{
			FBID:     fbid,
			IGID:     user.ID,
			Username: user.Username,
			FullName: user.FullName,
		})
	}
	if dropped > 0 {
		// Measured 2026-09-22: this never fires, because `Users[]` carries the
		// other participants and the viewer rides in `ThreadInfo.Viewer`. The
		// day it does fire, the measurement the Python side's "Nora is
		// excluded by the wire" comment rests on has changed.
		logf("roster (room=%s) dropped %d self row(s) from Users[] — the measured "+
			"shape does NOT include the viewer, so this is a library change worth "+
			"reading", shortThreadID, dropped)
	}
	if len(members) == 0 {
		return rosterBody{}, fmt.Errorf("%w: %s", errEmptyRoster, shortThreadID)
	}
	return rosterBody{
		OK:      true,
		Title:   info.ThreadTitle,
		IsGroup: info.IsGroup,
		Members: members,
	}, nil
}
