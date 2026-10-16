package instameow

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
// for the same account, and the one `GetProfile` and the story read take. It is NOT a
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
	"log/slog"
	"strconv"

	"instagram-bridge/internal/domain"

	"go.mau.fi/mautrix-meta/pkg/instameow/slidetypes"
)

var (
	// errNoThread is a read that came back without the thread. Distinct from
	// an empty member list because they mean different things to an operator:
	// one is a thread id that names nothing, the other is a thread that reads
	// as having nobody in it.
	errNoThread = domain.Classify(domain.ErrUpstreamRejected, errors.New("thread read returned no thread"))
	// errEmptyRoster is a thread that read back with no users at all. A group
	// Nora is in always has at least one other member, so this is the read
	// failing, not the room emptying — and reporting it as an empty roster is
	// the one answer that would be acted on as «there is nobody here».
	errEmptyRoster = domain.Classify(domain.ErrUpstreamRejected, errors.New("thread read returned no members"))
)

// readRoster reads one thread's membership.
//
// `selfFBID` is the viewer's own `interop_messaging_user_fbid`, or 0 when the
// bridge does not know it. It is used only to drop self if the library ever
// starts including the viewer in `Users[]` — see the file header for why that
// is a guard rather than the mechanism.
func readRoster(
	ctx context.Context, cli Client, shortThreadID string, selfFBID int64,
) (domain.Thread, error) {
	// The SHORT id, like every other read and like four of the five sends.
	// `GetThreadInfoRequest.ThreadFBID` says so on the field itself, and this
	// is the id the three-person roster was measured with.
	req := slidetypes.MakeGetThreadInfoRequest(shortThreadID)
	resp, err := call(ctx, "instagram.GetThread", nil, func(ctx context.Context) (*slidetypes.ThreadInfoResponse, error) {
		return cli.GetThread(ctx, req)
	})
	if err != nil {
		return domain.Thread{}, err
	}
	if resp == nil || resp.ThreadInfo.AsIGDirectThread == nil {
		return domain.Thread{}, fmt.Errorf("%w: %s", errNoThread, shortThreadID)
	}
	info := resp.ThreadInfo.AsIGDirectThread
	members := make([]domain.Member, 0, len(info.Users))
	dropped := 0
	for _, user := range info.Users {
		if user == nil {
			// Not a member we can drop quietly: a nil row is a row whose
			// account we cannot name, and the whole read is refused rather
			// than silently shortened.
			return domain.Thread{}, fmt.Errorf(
				"%w: thread %s returned a null user row", errNoThread, shortThreadID)
		}
		if user.InteropMessagingUserFBID == 0 {
			// Refused, not skipped. A member with no messaging id cannot be
			// mentioned, cannot be matched against a sender, and cannot be
			// mapped in identity.toml — so keeping the other rows would hand
			// back a room that is short by one, which is the direction that
			// lets a disclosure through.
			return domain.Thread{}, domain.Classify(domain.ErrUpstreamRejected, fmt.Errorf(
				"thread %s returned a member with no interop_messaging_user_fbid "+
					"(igid=%q username=%q); refusing the whole roster rather than "+
					"under-report the room",
				shortThreadID, user.ID, user.Username))
		}
		fbid := strconv.FormatInt(user.InteropMessagingUserFBID, 10)
		if selfFBID != 0 && user.InteropMessagingUserFBID == selfFBID {
			dropped++
			continue
		}
		members = append(members, domain.Member{
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
		slog.WarnContext(ctx, "roster dropped self rows from Users[]: the measured "+
			"shape does NOT include the viewer, so this is a library change worth reading",
			"room", shortThreadID, "dropped", dropped)
	}
	if len(members) == 0 {
		return domain.Thread{}, fmt.Errorf("%w: %s", errEmptyRoster, shortThreadID)
	}
	return domain.Thread{
		Title:   info.ThreadTitle,
		IsGroup: info.IsGroup,
		Members: members,
	}, nil
}
