package main

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
	"net/http"
	"strconv"

	"go.mau.fi/mautrix-meta/pkg/messagix/responses"
)

// profileBody is the 200 shape.
//
// `following`, `blocking` and `is_restricted` are forwarded from
// `FriendshipStatus` rather than collapsed into one `allowed` boolean here.
// The bridge is transport: whether `blocking` should also stop a read is
// Nora's policy call, and it is made in Python where the rest of the boundary
// lives. A bridge that pre-decided it would hide the reason from the layer
// that has to explain itself to a person.
type profileBody struct {
	OK       bool   `json:"ok"`
	IGID     string `json:"igid"`
	Username string `json:"username"`
	FullName string `json:"full_name"`
	// FBID is `interop_messaging_user_fbid` — the MESSAGING id, carried so a
	// caller holding one id space can join to the other without a second
	// round trip. It is 0 for an account the messaging graph has no row for,
	// which is normal for a public account Nora has never had a thread with;
	// that is a missing join key, NOT a failed read, so it does not refuse.
	FBID string `json:"fbid"`
	// Following is the FOLLOWED half of the gate: does NORA follow them.
	// Note the direction — this is not "do they follow Nora", which is a
	// different field the library does not model and which would be the wrong
	// question anyway: being followed BY someone grants no access to them.
	Following    bool `json:"following"`
	Blocking     bool `json:"blocking"`
	IsRestricted bool `json:"is_restricted"`
	IsVerified   bool `json:"is_verified"`
	// HasStory is `latest_reel_media != 0` — a timestamp, not a count, and the
	// only "is there a story up right now" signal on this query. Exposed so a
	// caller can skip a story fetch entirely rather than fetch-then-find-empty,
	// which matters because a story fetch is VISIBLE to its author and an
	// unnecessary one cannot be taken back.
	HasStory bool `json:"has_story"`
	// LatestReelMedia is the raw value behind HasStory, forwarded because it
	// is a timestamp and a caller comparing it against
	// `reel_media_seen_timestamp` can tell "has a story" from "has a story
	// Nora has not seen". Nothing does that yet.
	LatestReelMedia int `json:"latest_reel_media"`
	// Extra is `User.Unrecognized` verbatim: every key on the wire that the
	// library does not model. It exists for `is_private` specifically (see the
	// file header) and stays afterwards, because the alternative is a bridge
	// change every time a gate needs a field Instagram was already sending.
	//
	// It is NOT a stable contract. A consumer reading a key out of here is
	// reading something nothing guarantees will be there next week, and should
	// treat an absent key as unknown rather than as false.
	Extra map[string]any `json:"extra,omitempty"`

	// --- the SOCIAL half ----------------------------------------------------
	//
	// `web_profile_info`'s fields, merged into this account rather than given a
	// path of their own (social_profile.go says why). A SECOND upstream call,
	// so it fails independently — and `SocialAvailable` is what keeps that
	// failure honest: without it, a 429 and a genuinely empty biography are the
	// same empty string, and «this account has no bio» would be asserted from a
	// rate limit. R-27's three states, one field down.
	SocialAvailable bool `json:"social_available"`
	// SocialError is why the social half is missing, for an operator. Present
	// only when `SocialAvailable` is false.
	SocialError string         `json:"social_error,omitempty"`
	Social      *socialProfile `json:"social,omitempty"`
}

// socialProfile is what `web_profile_info` adds over `GetProfile`.
type socialProfile struct {
	// IsPrivate is the half of the operator's public-or-followed rule that
	// `GetProfile` does not carry — measured absent from its wire on three
	// real accounts, 2026-09-22.
	IsPrivate bool `json:"is_private"`
	// FollowedByViewer is the SAME fact `friendship_status.following` carries.
	// Both are returned rather than one trusted: two reads agreeing is
	// evidence, and two disagreeing is worth knowing about.
	FollowedByViewer bool   `json:"followed_by_viewer"`
	FollowsViewer    bool   `json:"follows_viewer"`
	Biography        string `json:"biography"`
	Followers        int    `json:"followers"`
	Following        int    `json:"following"`
	// PostCount and PostsReturned stay SEPARATE: the first is what the account
	// claims, the second is how many rows this page actually held. A timeline
	// reporting 400 and returning 0 is the interesting failure, and collapsing
	// them would hide it.
	PostCount     int `json:"post_count"`
	PostsReturned int `json:"posts_returned"`
}

var (
	// errNoProfile is a read that came back without a user. Distinct from a
	// transport failure because it means the igid names nothing — a caller
	// should say "I couldn't find that account", not "I couldn't look".
	errNoProfile = errors.New("profile read returned no user")
	// errBadIGID is a target that is not a decimal Instagram id. Its own error
	// so it maps to 400 rather than joining the 500 bucket: it is the caller's
	// mistake and no retry will fix it.
	errBadIGID = errors.New("igid is not a decimal instagram id")
)

// handleProfile answers GET /accounts/{igid}.
//
// The IGID, not the fbid — this is the social id space, and it is the one
// `GetProfile` takes. 02-identity-and-policy.md opens on exactly this
// confusion, whose whole cost is that picking the wrong one produces no error
// anywhere: a messaging fbid is also a decimal string, so it validates, and
// then names a different account or none at all.
func (b *bridge) handleProfile(w http.ResponseWriter, r *http.Request) {
	igid := r.PathValue("igid")
	if igid == "" {
		badRequest(w, "igid is required")
		return
	}
	cli, ok := b.ready(w)
	if !ok {
		return
	}
	body, err := readProfile(r.Context(), cli, igid)
	if err != nil {
		status := statusForError(err)
		if errors.Is(err, errBadIGID) {
			status = http.StatusBadRequest
		}
		if errors.Is(err, errNoProfile) {
			status = http.StatusNotFound
		}
		// The igid is logged and NOT echoed into the body: it is a person's
		// identifier and the error text crosses a boundary, while the log
		// stays on the host an operator already has.
		logf("profile failed (igid=%s status=%d): %v", igid, status, err)
		writeJSON(w, status, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, body)
}

// readProfile reads one account.
func readProfile(ctx context.Context, cli igClient, igid string) (profileBody, error) {
	// Canonicalized through ParseInt/FormatInt for the reason threadRef.Short
	// is: it makes "0903771081" and "276321323" the same id, so a caller
	// cannot address one account two ways and a cache cannot hold it twice.
	n, err := strconv.ParseInt(igid, 10, 64)
	if err != nil || n <= 0 {
		return profileBody{}, fmt.Errorf("%w: %q", errBadIGID, igid)
	}
	canonical := strconv.FormatInt(n, 10)

	resp, err := cli.GetProfile(ctx, canonical)
	if err != nil {
		return profileBody{}, err
	}
	if resp == nil || resp.User == nil {
		return profileBody{}, fmt.Errorf("%w: %s", errNoProfile, canonical)
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
	out := profileBody{
		OK:              true,
		IGID:            id,
		Username:        u.Username,
		FullName:        u.FullName,
		FBID:            fbid,
		Following:       u.FriendshipStatus.Following,
		Blocking:        u.FriendshipStatus.Blocking,
		IsRestricted:    u.FriendshipStatus.IsRestricted,
		IsVerified:      u.IsVerified,
		HasStory:        u.LatestReelMedia != 0,
		LatestReelMedia: u.LatestReelMedia,
		Extra:           u.Unrecognized,
	}

	// THE SECOND CALL, and it may fail on its own. `GetProfile` has already
	// answered by here, so a social failure degrades this read rather than
	// losing it: the caller still gets the username, the follow state and
	// `has_story`, with `social_available: false` saying what is missing and
	// why. Keyed on the USERNAME the first call just returned, so a caller
	// only ever supplies an igid.
	social, ok := cli.(socialClient)
	switch {
	case !ok:
		out.SocialError = "this client cannot issue raw requests"
	case u.Username == "":
		out.SocialError = "no username on the messaging profile to key the social read with"
	default:
		s, status, err := readSocialProfile(ctx, social, u.Username)
		if err != nil {
			// Logged with the STATUS, because telling a 429 from a challenge
			// page from a parse failure is the entire reason this half exists
			// as a measurement before it exists as a feature.
			logf("social profile read failed (username=%s status=%d): %v",
				u.Username, status, err)
			out.SocialError = fmt.Sprintf("%d: %v", status, err)
		} else {
			out.SocialAvailable, out.Social = true, &s
		}
	}
	return out, nil
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
// # A story read is VISIBLE to its author, and that changes the rules
//
// Every other read in this system is invisible to the person being read.
// Fetching a story puts Nora in the author's viewer list, and it cannot be
// taken back. Two consequences are built into the route rather than left to
// the caller:
//
//  1. it takes exactly ONE igid and never a list, although `FetchReel` accepts
//     several. A batched read would view several people's stories to answer a
//     question about one, and each of them would see it;
//  2. `has_story` on /profile exists so a caller can decide NOT to call this.
//     Checking first is free and invisible; calling and finding nothing is
//     neither.
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

// storyItem is one frame of a story.
type storyItem struct {
	// ID is Instagram's media id (`pk`), carried for provenance. Nothing
	// fetches by it — the url does that.
	ID string `json:"id"`
	// Kind is derived from `media_type` (1 image, 2 video), and defaults to
	// image, because `image_versions2` is populated for BOTH: a video story
	// carries its own cover frame. A frame described as a video would have
	// Nora talk about a clip she only saw one still of.
	Kind string `json:"kind"`
	// URL is the best candidate: the video when there is one and the caller
	// asked for depth, otherwise the largest still. Directly fetchable — the
	// CDN url carries its own signature and needs no cookies, which is what
	// lets `media_fetch` take it with no special case.
	URL string `json:"url"`
	// ThumbnailURL is always the still, even for a video. A caller that only
	// wants to know what a story LOOKS like fetches this and never the clip.
	ThumbnailURL string `json:"thumbnail_url,omitempty"`
	TakenAt      int    `json:"taken_at,omitempty"`
	// ExpiringAt is the natural retention bound and the reason 06's "social
	// reads are not kept" rule has a number behind it: a story is gone in 24
	// hours, so anything Nora kept would outlive its source.
	ExpiringAt int `json:"expiring_at,omitempty"`
	// Caption is the author's own words on the frame, when there are any.
	Caption string `json:"caption,omitempty"`
	// AltText is `accessibility_caption` — Instagram's own description of the
	// image. Free, already on the delta, and often enough to answer a question
	// without any fetch or vision call at all.
	AltText string `json:"alt_text,omitempty"`
}

// storyBody is the 200 shape.
type storyBody struct {
	OK   bool   `json:"ok"`
	IGID string `json:"igid"`
	// Found distinguishes "this account has no story up" from "this account
	// has one and Nora may not see it". Both come back as zero items, and
	// reporting the second as the first would state something about a person
	// on the strength of a permission error.
	Found bool `json:"found"`
	// Username is off the reel's own user row, not echoed from the request.
	Username string `json:"username,omitempty"`
	// ExpiringAt is the reel's, which bounds the whole set.
	ExpiringAt int `json:"expiring_at,omitempty"`
	// Seen is Instagram's own cursor: the timestamp of the last frame this
	// account viewed. It is NOT Nora's unread cursor — 10 §"Unread must be
	// Nora's own cursor" is explicit that hers has to be hers — but it is the
	// evidence that a view was recorded, which an operator auditing "what did
	// she look at" needs.
	Seen  int         `json:"seen,omitempty"`
	Items []storyItem `json:"items"`
}

var errNoReel = errors.New("story read returned no reel")

// handleStory answers GET /accounts/{igid}/story.
func (b *bridge) handleStory(w http.ResponseWriter, r *http.Request) {
	igid := r.PathValue("igid")
	if igid == "" {
		badRequest(w, "igid is required")
		return
	}
	cli, ok := b.ready(w)
	if !ok {
		return
	}
	body, err := readStory(r.Context(), cli, igid)
	if err != nil {
		status := statusForError(err)
		if errors.Is(err, errBadIGID) {
			status = http.StatusBadRequest
		}
		logf("story failed (igid=%s status=%d): %v", igid, status, err)
		writeJSON(w, status, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, body)
}

// readStory reads one account's current story.
func readStory(ctx context.Context, cli igClient, igid string) (storyBody, error) {
	n, err := strconv.ParseInt(igid, 10, 64)
	if err != nil || n <= 0 {
		return storyBody{}, fmt.Errorf("%w: %q", errBadIGID, igid)
	}
	canonical := strconv.FormatInt(n, 10)

	// ONE id, never a list. See the file header: each extra id is another
	// person who sees that Nora watched.
	resp, err := cli.FetchReel(ctx, []string{canonical}, "")
	if err != nil {
		return storyBody{}, err
	}
	if resp == nil {
		return storyBody{}, fmt.Errorf("%w: %s", errNoReel, canonical)
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

	out := storyBody{OK: true, IGID: canonical, Found: found, Items: []storyItem{}}
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
			logf("story (igid=%s) frame %s had no url; skipped", canonical, item.ID)
			continue
		}
		out.Items = append(out.Items, frame)
	}
	return out, nil
}

// storyFrame reduces one reel item to what a caller can act on.
func storyFrame(item *responses.ReelItem) storyItem {
	out := storyItem{
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
