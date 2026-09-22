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

// handleProfile answers GET /profile?igid=<igid>.
//
// The IGID, not the fbid — this is the social id space, and it is the one
// `GetProfile` takes. 02-identity-and-policy.md opens on exactly this
// confusion, whose whole cost is that picking the wrong one produces no error
// anywhere: a messaging fbid is also a decimal string, so it validates, and
// then names a different account or none at all.
func (b *bridge) handleProfile(w http.ResponseWriter, r *http.Request) {
	igid := r.URL.Query().Get("igid")
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
	return profileBody{
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
	}, nil
}
