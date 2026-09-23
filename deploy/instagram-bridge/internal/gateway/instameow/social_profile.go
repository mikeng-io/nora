package instameow

// The SOCIAL HALF of `GET /accounts/{igid}` — the fields `GetProfile`'s own
// response type throws away.
//
// **Not its own route, and the first draft made it one.** `/profile-social`
// named the BACKEND a field came from, which is not a resource: these are
// more fields of the same account, so they belong in the same read. A path
// per upstream call is how one noun ends up with four spellings.
//
// # The split in `07` is settled: instameow carries the account read alone
//
// `07`'s routing table sent `profile` (social fields) and `posts` to
// OpenCLI, on the recorded reason that instameow has no call for either.
// That reason was true of the client METHODS and false of the API.
// `GetProfile` already reaches an account's profile page through
// `PolarisProfilePageContentQuery` — the same document the real web app
// uses — and this file issues the identical document a second time, raw,
// to reach the 51 fields `slidetypes.ProfilePageResponse` does not model.
//
// Measured 2026-09-22, on this account: **65 keys under `data.user`**, once
// the response was captured whole rather than through the typed struct.
// `is_private`, `followed_by`, the follower/following/media counts and
// `total_clips_count` (reel count) are all on it — the fields `07` had
// recorded as unreachable from instameow. `profile` and `posts`-as-counts
// collapse onto instameow; OpenCLI's remaining job is X and permalinks
// only.
//
// # Keyed on the numeric igid, NOT the username — a corrected mistake
//
// The first working version of this call keyed on `u.Username`, carried
// over from `web_profile_info`'s own `?username=` convention (that REST
// endpoint is throttled — see below — and is not used here at all
// anymore). `PolarisProfilePageContentQuery` does not take a username:
// `slidetypes.GetProfilePageRequest` declares one addressing field, `ID`
// (requests.go), and Instagram answers a username sent through it with
// `field_exception` on `path: ["user"]` — measured 4/4 on this account,
// while the identical call keyed on the numeric igid succeeded 4/4 in the
// same requests. Call ORDER was never the variable; a string was going
// into a field that wanted a number, and every prior failure had been
// silently recorded as success (next paragraph explains why).
//
// # A 200 is not success — the GraphQL envelope must be read for `errors`
//
// A failed GraphQL document still answers HTTP 200: the transport
// succeeded, the QUERY did not, and the failure lives in an `errors` array
// inside the body. The first version of this function checked only
// `resp.StatusCode`, so every username-keyed `field_exception` above
// reported itself as a successful read of an empty profile — no error
// returned, zero fields recovered — and was indistinguishable from a
// throttle from outside this function. That is what made the ordering
// theory look plausible in the first place: the actual failures were
// invisible until the envelope's own `errors` array was read.
//
// # `web_profile_info` (REST) was the WRONG endpoint, not a wrong header
//
// The very first version of this call used `web_profile_info` and was
// wrong twice over. That is a REST endpoint, and it answered 429 to the DM
// session exactly as it does to OpenCLI's browser scrape — measured against
// two real accounts, 2026-09-22, with a properly formed request carrying
// `x-ig-app-id`, `x-csrftoken`, `x-mid` and `x-ig-www-claim`. The throttle
// is on that REST surface specifically, not on the account and not on the
// session: `PolarisProfilePageContentQuery` (GraphQL) answers the same
// session normally.
//
// # Fail closed, same as its siblings
//
// A 429, a `field_exception`, and a malformed body are three different
// failures and are returned as three different errors: an operator reading
// "500" learns nothing about which one happened, and this route exists
// specifically to tell them apart.

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"

	"instagram-bridge/internal/domain"

	"go.mau.fi/mautrix-meta/pkg/instameow/slidetypes"
)

// errProfileGraphQL is a 200 whose BODY says the document did not deliver
// what it was asked for. The `errors`-array case is raised by the graphql
// helper with the same wording; this one is the userless body that carries no
// errors either.
var errProfileGraphQL = domain.Classify(domain.ErrUpstreamRejected,
	errors.New("profile query returned a GraphQL error"))

// profileUserWire is `data.user`, decoded only as far as the fields
// `socialProfile` keeps. Unnamed fields on the wire (65 measured, ~50 not
// named here) are left unparsed rather than carried as more
// `Unrecognized`-style dead weight — see the file header for what was
// measured and why the rest was left out.
type profileUserWire struct {
	IsPrivate            bool   `json:"is_private"`
	Biography            string `json:"biography"`
	ExternalURL          string `json:"external_url"`
	FollowerCount        int    `json:"follower_count"`
	FollowingCount       int    `json:"following_count"`
	MutualFollowersCount int    `json:"mutual_followers_count"`
	MediaCount           int    `json:"media_count"`
	TotalClipsCount      int    `json:"total_clips_count"`
	IsBusiness           bool   `json:"is_business"`
	// IsProfessionalAccount is a POINTER, unlike every other bool on this
	// struct. Measured `null` on a real account (2026-09-22) — a personal
	// account, where the field means "not applicable" — and `null` into a
	// bare `bool` unmarshals to `false` with no error, which would make
	// "measured not-professional" and "Instagram did not say" the same
	// value. `Category` two fields below already drew this line for the
	// same reason; this field just did not follow it the first time.
	IsProfessionalAccount *bool   `json:"is_professional_account"`
	Category              *string `json:"category"`
	ProfilePicURL         string  `json:"profile_pic_url"`
	FriendshipStatus      struct {
		FollowedBy bool `json:"followed_by"`
	} `json:"friendship_status"`
}

// readSocialProfile issues the SAME GraphQL document `GetProfile` uses,
// through the graphql helper, and returns the named fields the library's own
// response type discards. The helper owns the transport status and the
// envelope's `errors` array; this function owns only `data.user`.
//
// The status is Instagram's (see graphql), for the operator-facing
// `social_error`.
func readSocialProfile(
	ctx context.Context, issuer GraphQLIssuer, igid domain.IGID,
) (domain.SocialProfile, int, error) {
	status, raw, err := graphql(ctx, issuer,
		"PolarisProfilePageContentQuery", "profile query",
		slidetypes.MakeProfilePageRequest(igid.String()))
	if err != nil {
		return domain.SocialProfile{}, status, err
	}
	out, err := parseSocialProfile(raw)
	if err != nil {
		return domain.SocialProfile{}, status, err
	}
	return out, status, nil
}

// parseSocialProfile reads `data.user` out of a document the graphql helper
// has already accepted, with no network underneath it.
//
// A 200 with neither `errors` nor a `user` should not exist but, per this
// bundle's own history of wrong field-set guesses, gets a named refusal rather
// than a silent zero-value success if it ever does.
func parseSocialProfile(raw []byte) (domain.SocialProfile, error) {
	var envelope struct {
		Data struct {
			User *profileUserWire `json:"user"`
		} `json:"data"`
	}
	if err := json.Unmarshal(raw, &envelope); err != nil {
		return domain.SocialProfile{}, domain.Classify(domain.ErrUpstreamRejected,
			fmt.Errorf("profile query did not return JSON (%d bytes): %w", len(raw), err))
	}
	if envelope.Data.User == nil {
		return domain.SocialProfile{}, fmt.Errorf("%w: no user in a 200 with no errors either", errProfileGraphQL)
	}

	u := envelope.Data.User
	return domain.SocialProfile{
		IsPrivate:             u.IsPrivate,
		FollowedBy:            u.FriendshipStatus.FollowedBy,
		Biography:             u.Biography,
		ExternalURL:           u.ExternalURL,
		FollowerCount:         u.FollowerCount,
		FollowingCount:        u.FollowingCount,
		MutualFollowersCount:  u.MutualFollowersCount,
		MediaCount:            u.MediaCount,
		TotalClipsCount:       u.TotalClipsCount,
		IsBusiness:            u.IsBusiness,
		IsProfessionalAccount: u.IsProfessionalAccount,
		Category:              u.Category,
		ProfilePicURL:         u.ProfilePicURL,
	}, nil
}
