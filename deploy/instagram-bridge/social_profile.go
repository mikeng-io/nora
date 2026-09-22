package main

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
	"net/http"

	"go.mau.fi/mautrix-meta/pkg/instameow/slidetypes"
	"go.mau.fi/mautrix-meta/pkg/messagix/httpclient"
)

// socialClient is the slice of *instameow.Client this read needs.
// `MakeGraphQLRequest` is exported on the HTTP client and `GetHTTP()` hands
// one over, which is the whole reason this can be done from outside the
// library.
type socialClient interface {
	GetHTTP() *httpclient.HTTPClient
	GetEndpoint(name string) string
}

var errProfileHTTP = errors.New("profile query returned a non-200")

// errProfileGraphQL is a 200 whose BODY says the query failed — a
// `field_exception` and its relatives. Distinct from errProfileHTTP because
// the failure is in the document, not the transport, and an operator needs
// to tell them apart the same way a 429 needs telling from a 500.
var errProfileGraphQL = errors.New("profile query returned a GraphQL error")

// profileUserWire is `data.user`, decoded only as far as the fields
// `socialProfile` keeps. Unnamed fields on the wire (65 measured, ~50 not
// named here) are left unparsed rather than carried as more
// `Unrecognized`-style dead weight — see the file header for what was
// measured and why the rest was left out.
type profileUserWire struct {
	IsPrivate             bool    `json:"is_private"`
	Biography             string  `json:"biography"`
	ExternalURL           string  `json:"external_url"`
	FollowerCount         int     `json:"follower_count"`
	FollowingCount        int     `json:"following_count"`
	MutualFollowersCount  int     `json:"mutual_followers_count"`
	MediaCount            int     `json:"media_count"`
	TotalClipsCount       int     `json:"total_clips_count"`
	IsBusiness            bool    `json:"is_business"`
	IsProfessionalAccount bool    `json:"is_professional_account"`
	Category              *string `json:"category"`
	ProfilePicURL         string  `json:"profile_pic_url"`
	FriendshipStatus      struct {
		FollowedBy bool `json:"followed_by"`
	} `json:"friendship_status"`
}

// readSocialProfile issues the SAME GraphQL document `GetProfile` uses and
// returns the named fields the library's own response type discards. See
// the file header for the two corrected mistakes behind this shape:
// username-keyed (wrong field) and status-only error detection (missed the
// GraphQL envelope's own `errors`).
func readSocialProfile(
	ctx context.Context, cli socialClient, igid string,
) (socialProfile, int, error) {
	h := cli.GetHTTP()
	if h == nil {
		return socialProfile{}, http.StatusServiceUnavailable,
			fmt.Errorf("%w: no http client", errNotConnected)
	}

	resp, raw, err := h.MakeGraphQLRequest(
		ctx, "PolarisProfilePageContentQuery", slidetypes.MakeProfilePageRequest(igid),
	)
	if err != nil {
		return socialProfile{}, statusForError(err), err
	}
	if resp != nil && resp.StatusCode != http.StatusOK {
		// FORWARDED, not flattened: telling a 429 from a challenge from a
		// parse failure is why this exists as a named error rather than one
		// bucket.
		return socialProfile{}, resp.StatusCode,
			fmt.Errorf("%w: %d", errProfileHTTP, resp.StatusCode)
	}

	out, err := parseSocialProfile(raw)
	if err != nil {
		return socialProfile{}, http.StatusOK, err
	}
	return out, http.StatusOK, nil
}

// parseSocialProfile is the pure half of readSocialProfile — the GraphQL
// envelope in, a curated profile or a named error out, with no network
// underneath it. Split out on purpose: `*httpclient.HTTPClient` is a
// concrete vendor type with no test seam, so this function is the only place
// the two mistakes below can actually be asserted against a byte string
// instead of a live account.
//
// `Errors` is checked before `Data`: a failed GraphQL document is still an
// HTTP 200, with an `errors` array where `data` would be. The first version
// of this call checked only the HTTP status and treated that shape as a
// successful read of an empty profile — no error, zero fields — which is
// what let a `field_exception` masquerade as success on every call keyed by
// username (see the file header).
func parseSocialProfile(raw []byte) (socialProfile, error) {
	var envelope struct {
		Data struct {
			User *profileUserWire `json:"user"`
		} `json:"data"`
		Errors []struct {
			Message string `json:"message"`
			Code    int    `json:"code"`
			Summary string `json:"summary"`
		} `json:"errors"`
	}
	if err := json.Unmarshal(raw, &envelope); err != nil {
		return socialProfile{}, fmt.Errorf("profile query did not return JSON (%d bytes): %w", len(raw), err)
	}
	if len(envelope.Errors) > 0 {
		e := envelope.Errors[0]
		return socialProfile{}, fmt.Errorf("%w: %s (code=%d): %s", errProfileGraphQL, e.Summary, e.Code, e.Message)
	}
	if envelope.Data.User == nil {
		return socialProfile{}, fmt.Errorf("%w: no user in a 200 with no errors either", errProfileGraphQL)
	}

	u := envelope.Data.User
	return socialProfile{
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
