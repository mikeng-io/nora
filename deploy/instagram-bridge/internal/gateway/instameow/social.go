package instameow

// Social reads: the account behind an igid, and the gate that decides whether
// its content may be read at all.
//
// The second READ route that answers about a PERSON rather than about the
// bridge (roster.go is the first), and the only one whose purpose is a
// DECISION rather than a fact. `/roster` answers "who is in this room";
// `/profile` answers "may this account's content be fetched".
//
// # The gate is followed-or-public (operator, 2026-09-22)
//
// The bundle used to carry a four-tier reach ladder keyed on the SUBJECT —
// in the room / in the workspace / named by a human / the open network — and
// Tier 1 of it was blocked outright, because a per-person capability has no
// shape in `policy.toml` and inventing one was a real piece of work
// (07-social-boundary-and-scope.md).
//
// The operator replaced it with one rule: **if the account is public, or this
// account follows it, its content may be read.** That is not a loosening of
// the ladder, it is a different and better-grounded question. The ladder asked
// "what is this subject's relationship to Nora's rooms", which `policy.toml`
// had to answer and could not. This asks "what has this account already
// agreed to show us", which INSTAGRAM answers, on every profile read, for
// free. The gate stops being configuration Nora maintains and becomes a fact
// she reads — and a fact that stays correct when somebody unfollows or goes
// private, which a config file never would.
//
// # `following` is measured; `is_private` is NOT on the struct
//
// `slidetypes.User.FriendshipStatus` declares exactly three fields:
//
//	IsRestricted bool `json:"is_restricted"`
//	Blocking     bool `json:"blocking"`
//	Following    bool `json:"following"`
//
// So the FOLLOWED half of the rule is free and typed. The PUBLIC half is not
// declared anywhere on `User`.
//
// That is not the same as absent from the wire. `User` ends with
// `Unrecognized map[string]any` carrying `json:",unknown"`, so any key the
// library does not model is captured rather than dropped. Whether Instagram
// sends `is_private` on this query is therefore a question about the WIRE,
// and the struct cannot answer it — which is why this route returns
// `extra` verbatim instead of a curated field set. The measurement is the
// point; a route that only forwarded the three typed booleans would have made
// the open question permanently unanswerable from inside Nora.
//
// # Why `viewer` is dropped
//
// `ProfilePageResponse` carries `User` AND `Viewer`, and `Viewer` is Nora's
// own account on every call. It is not the subject of the read, it is the
// same value every time, and it would put her own profile into the body of a
// route whose whole job is describing somebody else. `/health` is where facts
// about Nora belong.
//
// # Fail closed, and a refusal is not a "no"
//
// Same rule as roster.go, for a sharper reason: this route's answer gates a
// fetch. An error that degraded to `{following: false}` would read as "this
// account does not permit it" — a refusal Nora would then SAY, about a person,
// on evidence that does not exist. Every shape this route cannot fully account
// for is a non-200 with a named error, so the caller can tell "may not" from
// "could not look". They are different sentences.

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"strconv"

	"instagram-bridge/internal/domain"

	"go.mau.fi/mautrix-meta/pkg/instameow/slidetypes"
	"go.mau.fi/mautrix-meta/pkg/messagix/responses"
	"go.opentelemetry.io/otel/attribute"
)

// errNoProfile is a read that came back without a user. Distinct from a
// transport failure because it means the igid names nothing — a caller should
// say "I couldn't find that account", not "I couldn't look". So it is
// ErrNotFound, and 404.
//
// A target that is not a decimal Instagram id never reaches here: the id is a
// domain.IGID, and a malformed one was ErrInvalidID, the caller's mistake,
// before anything was sent.
var errNoProfile = domain.Classify(domain.ErrNotFound, errors.New("profile read returned no user"))

// readProfile reads one account through GetProfile.
//
// The IGID, not the fbid — this is the social id space, and it is the one
// `GetProfile` takes. 02-identity-and-policy.md opens on exactly this
// confusion, whose whole cost is that picking the wrong one produces no error
// anywhere: a messaging fbid is also a decimal string, so it validates, and
// then names a different account or none at all.
func readProfile(ctx context.Context, cli Client, igid domain.IGID) (domain.Account, error) {
	// Canonical by construction (domain.ParseIGID): "0197003061" and
	// "286113965" are the same id, so a caller cannot address one account two
	// ways and a cache cannot hold it twice.
	canonical := igid.String()

	resp, err := call(ctx, "instagram.GetProfile",
		[]attribute.KeyValue{attribute.String("instagram.igid", canonical)},
		func(ctx context.Context) (*slidetypes.ProfilePageResponse, error) {
			return cli.GetProfile(ctx, canonical)
		})
	if err != nil {
		return domain.Account{}, err
	}
	if resp == nil || resp.User == nil {
		return domain.Account{}, fmt.Errorf("%w: %s", errNoProfile, canonical)
	}
	u := resp.User

	// The id is taken from the RESPONSE, not echoed from the request. They
	// should be equal, and if they ever are not, the caller must be told about
	// the account it actually got rather than the one it asked for.
	id := u.ID
	if id == "" {
		id = canonical
	}
	fbid := ""
	if u.InteropMessagingUserFBID != 0 {
		fbid = strconv.FormatInt(u.InteropMessagingUserFBID, 10)
	}
	// `Viewer` is dropped here: it is Nora's own account on every call, not
	// the subject of the read. /health is where facts about Nora belong.
	return domain.Account{
		IGID:            id,
		Username:        u.Username,
		FullName:        u.FullName,
		FBID:            fbid,
		Following:       u.FriendshipStatus.Following,
		Blocking:        u.FriendshipStatus.Blocking,
		IsRestricted:    u.FriendshipStatus.IsRestricted,
		IsVerified:      u.IsVerified,
		LatestReelMedia: u.LatestReelMedia,
		Extra:           u.Unrecognized,
	}, nil
}

// readSocial is the SECOND call behind an account read, and it may fail on
// its own: GetProfile has already answered, so a social failure degrades the
// read rather than losing it. Its outcome is carried, never raised.
//
// Keyed on the numeric igid — NOT the username. The first version of this
// keyed on the username, on the theory that `PolarisProfilePageContentQuery`
// is handle-addressed the way `web_profile_info` is. It is not:
// `slidetypes.GetProfilePageRequest.ID` (requests.go) is what the document
// declares, and Instagram answers a username sent through that field with
// `field_exception` on `path: ["user"]` — measured 4/4 on this account,
// 2026-09-22, while the same call keyed on the igid succeeded 4/4. The
// failure was invisible until the envelope's `errors` array was read, which
// is now the graphql helper's job. With the id a domain.IGID the mistake no
// longer compiles.
func readSocial(ctx context.Context, issuer GraphQLIssuer, issuerErr error, igid domain.IGID) domain.SocialRead {
	if issuerErr != nil {
		return domain.SocialRead{Err: issuerErr}
	}
	s, status, err := readSocialProfile(ctx, issuer, igid)
	if err != nil {
		// Logged with the STATUS, because telling a 429 from a challenge
		// page from a parse failure is the entire reason this half exists
		// as a measurement before it exists as a feature. 0 is "no response".
		slog.WarnContext(ctx, "social profile read failed",
			"igid", igid.String(), "status", status, "error", err)
		return domain.SocialRead{Sent: true, Status: status, Err: err}
	}
	return domain.SocialRead{Sent: true, Status: status, Profile: &s}
}

// issuerFor resolves the raw GraphQL issuer off a client: the session's
// HTTP client, which only a real *instameow.Client carries.
func issuerFor(cli Client) (GraphQLIssuer, error) {
	social, ok := cli.(rawSource)
	if !ok {
		return nil, errors.New("this client cannot issue raw requests")
	}
	h := social.GetHTTP()
	if h == nil {
		return nil, fmt.Errorf("%w: no http client", errNotConnected)
	}
	return h, nil
}

// --- /story -------------------------------------------------------------------
//
// Stories exist on NO other path. OpenCLI's `instagram story` verb POSTS one;
// there is no story read anywhere in its surface, verified against the live
// tool. `FetchReel` is the only way to read somebody's story, which is why
// 07-social-boundary-and-scope.md's routing table has `story → instameow
// (only path anywhere)` and why this route is on this bridge rather than the
// social one.
//
// # A story read is a deliberate act, and that changes the rules
//
// Whether the author sees the view is unmeasured (design/instagram-adoption/
// 04, 05 §Reading a story): the `seen` watermark cannot see that property.
// Nora opens only the stories she was asked to, as a privacy choice
// (design/instagram-adoption/18), so two consequences are built into the
// route rather than left to the caller:
//
//  1. it takes exactly ONE igid and never a list, although `FetchReel` accepts
//     several. A batched read would open several people's stories to answer a
//     question about one;
//  2. `has_story` on /profile exists so a caller can decide NOT to call this.
//     Checking first opens nothing; calling and finding nothing opened a
//     story read for no answer.
//
// # Private plus followed is a NORMAL success, not an exception
//
// The operator's rule — public, or followed — is not enforced here. It is
// enforced by INSTAGRAM, against this session's own credentials: the bridge is
// authenticated as Nora, so Instagram serves exactly what Nora may see. A
// private account she follows returns its story in full; one she does not
// follow returns nothing. There is no gate for the bridge to apply, and adding
// one would be a second opinion about a decision that has already been made
// correctly one layer down.
//
// What that does mean is that an EMPTY result is ambiguous on its own — no
// story up, or not permitted to see it — and the route says which by carrying
// `found`. Collapsing the two would let "I'm not allowed" be reported as
// "they haven't posted anything", which is a claim about a person made from a
// permission error.

// errNoReel is a story read that came back with no response body at all:
// Instagram answered, without the thing it was asked for.
var errNoReel = domain.Classify(domain.ErrUpstreamRejected, errors.New("story read returned no reel"))

// readStory reads one account's current story.
func readStory(ctx context.Context, cli Client, igid domain.IGID) (domain.Story, error) {
	canonical := igid.String()

	// ONE id, never a list. See the file header: each extra id is another
	// person who sees that Nora watched.
	ids := []string{canonical}
	resp, err := call(ctx, "instagram.FetchReel",
		[]attribute.KeyValue{attribute.StringSlice("instagram.igids", ids)},
		func(ctx context.Context) (*responses.ReelInfoResponse, error) {
			return cli.FetchReel(ctx, ids, "")
		})
	if err != nil {
		return domain.Story{}, err
	}
	if resp == nil {
		return domain.Story{}, fmt.Errorf("%w: %s", errNoReel, canonical)
	}

	reel, found := resp.Reels[canonical]
	if !found {
		// Also try the flat form: the response declares BOTH `reels` (a map)
		// and `reels_media` (a list), and which one arrives has not been
		// measured for this call. Reading only one would report "no story" for
		// a shape that carried one.
		for _, candidate := range resp.ReelsMedia {
			if candidate.ID == canonical || candidate.StrongID == canonical {
				reel, found = candidate, true
				break
			}
		}
	}

	out := domain.Story{IGID: canonical, Found: found, Items: []domain.StoryItem{}}
	if !found {
		// Not an error. Either they have no story up, or Nora may not see it,
		// and `found: false` says exactly that much and no more.
		return out, nil
	}
	out.ExpiringAt = reel.ExpiringAt
	out.Seen = reel.Seen
	if reel.User != nil {
		out.Username = reel.User.Username
	}
	for _, item := range reel.Items {
		if item == nil {
			continue
		}
		frame := storyFrame(item)
		if frame.URL == "" {
			// A frame with no fetchable url is a handle naming nothing. Skipped
			// rather than refused: the OTHER frames are still readable, and a
			// story short by one frame is not the safety problem a roster short
			// by one member is — nothing gates a disclosure on it.
			slog.WarnContext(ctx, "story frame had no url; skipped",
				"igid", canonical, "frame", item.ID)
			continue
		}
		out.Items = append(out.Items, frame)
	}
	return out, nil
}

// storyFrame reduces one reel item to what a caller can act on.
func storyFrame(item *responses.ReelItem) domain.StoryItem {
	out := domain.StoryItem{
		ID:         item.Pk,
		Kind:       mediaImage,
		TakenAt:    item.TakenAt,
		ExpiringAt: item.ExpiringAt,
		Caption:    item.Caption.Text,
		AltText:    item.AccessibilityCaption,
	}
	if out.ID == "" {
		out.ID = item.ID
	}
	// The largest still. `image_versions2.candidates` is populated for a VIDEO
	// story too — it is the cover frame — which is why the thumbnail is set
	// before the kind is decided.
	best := 0
	for _, c := range item.ImageVersions2.Candidates {
		if c.URL != "" && c.Width >= best {
			best, out.ThumbnailURL = c.Width, c.URL
		}
	}
	out.URL = out.ThumbnailURL

	// media_type 2 is a video. Checked against the presence of an actual video
	// url rather than the integer alone, so a delta that says "video" and
	// carries no clip degrades to its cover frame instead of promising bytes
	// that are not there.
	bestVideo := 0
	videoURL := ""
	for _, v := range item.VideoVersions {
		if v.URL != "" && v.Width >= bestVideo {
			bestVideo, videoURL = v.Width, v.URL
		}
	}
	if videoURL != "" {
		out.Kind, out.URL = mediaVideo, videoURL
	}
	return out
}
