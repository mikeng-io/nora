package main

// The SOCIAL HALF of `GET /accounts/{igid}` — `web_profile_info`, issued
// with the DM session's own headers.
//
// **Not its own route, and the first draft made it one.** `/profile-social`
// named the BACKEND a field came from, which is not a resource: these are
// more fields of the same account, so they belong in the same read. A path
// per upstream call is how one noun ends up with four spellings.
//
// # What it is trying to settle
//
// It is still a PROBE in the sense that matters: `07`'s routing table sends
// `profile` (social fields) and `posts` to OpenCLI,
// on the recorded reason that instameow has no call for either. That reason is
// true of the client METHODS and false of the API: `web_profile_info` is
// declared in `messagix/endpoints/instagram.go` and its response is fully
// typed in `messagix/responses/profile.go`, carrying `is_private`,
// `followed_by_viewer`, `follows_viewer`, `id`, `fbid`, `biography`, the
// follower/following counts AND `edge_owner_to_timeline_media` — the posts
// list. No client method calls it; `GetHTTP()` and `GetEndpoint()` are
// exported, so this file does.
//
// The OpenCLI half of those two rows does not currently work. `profile` and
// `following` have returned HTTP 429 on that same endpoint all day, from a
// browser-scraped request, while the operator reports the account answering
// normally in a real browser and `search` answering from the same CLI. Same
// endpoint, same account, different REQUEST SHAPE.
//
// So the question is exactly one thing: **does Instagram answer the DM
// session's own headers where it refuses the scrape?** If yes, `profile` and
// `posts` collapse onto instameow and OpenCLI keeps only X and permalinks. If
// no, the split in `07` stands and its OpenCLI half is simply degraded.
//
// # It is keyed on a USERNAME, and that is a wrinkle worth naming
//
// `web_profile_info` takes `?username=`. Everything else instameow serves is
// keyed on an id Nora holds, which is the whole basis of "id-keyed →
// instameow, handle-keyed → OpenCLI". This one call is handle-keyed and served
// by instameow, so that rule is a heuristic about WHOSE SESSION CAN SEE IT
// rather than a law about key spaces. Recorded here so the exception is
// visible rather than quietly eroding the rule.
//
// Nora holds the handles — `identity.toml` gained `username` for all eleven
// mapped accounts on 2026-09-22, and `/roster` returns it per member — so
// being handle-keyed costs nothing at the call site. What it DOES mean is that
// a rename breaks this read and the account is still perfectly identified by
// its igid, which is the asymmetry `roster.go` already warns about.
//
// # Fail closed, same as its siblings
//
// A 429 is reported AS a 429 rather than folded into a 500: this route exists
// to tell one failure from another, and an operator reading "500" learns
// nothing about whether the request shape was the problem.

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"

	"go.mau.fi/mautrix-meta/pkg/messagix/httpclient"
	"go.mau.fi/mautrix-meta/pkg/messagix/responses"
	"go.mau.fi/mautrix-meta/pkg/messagix/types"
)

// socialClient is the slice of *instameow.Client this route needs, kept
// separate from `igClient` because nothing else uses it and widening the fake
// every other test builds for one probe is a poor trade.
type socialClient interface {
	GetHTTP() *httpclient.HTTPClient
	GetEndpoint(name string) string
}

var errProfileHTTP = errors.New("web_profile_info returned a non-200")

func readSocialProfile(
	ctx context.Context, cli socialClient, username string,
) (socialProfile, int, error) {
	h := cli.GetHTTP()
	if h == nil {
		return socialProfile{}, http.StatusServiceUnavailable,
			fmt.Errorf("%w: no http client", errNotConnected)
	}
	// The SAME header set `FetchReel` builds — that call works against this
	// session today, so it is the known-good shape to copy. `X-Web-Session-ID`
	// is NOT set: it lives on the client's unexported config and the probe is
	// about whether the session's ordinary headers suffice.
	headers := h.BuildHeaders(true, false)
	headers.Set("x-requested-with", "XMLHttpRequest")
	headers.Set("referer", cli.GetEndpoint("base_url"))
	headers.Set("sec-fetch-dest", "empty")
	headers.Set("sec-fetch-mode", "cors")
	headers.Set("sec-fetch-site", "same-origin")

	query := url.Values{}
	query.Add("username", username)
	reqURL := cli.GetEndpoint("web_profile_info") + query.Encode()

	resp, raw, err := h.MakeRequest(ctx, reqURL, http.MethodGet, headers, nil, types.NONE)
	if err != nil {
		return socialProfile{}, statusForError(err), err
	}
	if resp != nil && resp.StatusCode != http.StatusOK {
		// The status is FORWARDED, not flattened. This route's whole purpose
		// is telling a 429 from a challenge from a parse failure; a caller
		// handed 500 for all three learns nothing.
		return socialProfile{}, resp.StatusCode,
			fmt.Errorf("%w: %d", errProfileHTTP, resp.StatusCode)
	}

	var parsed responses.ProfileInfoResponse
	if err := json.Unmarshal(raw, &parsed); err != nil {
		// An HTML challenge page unmarshals as a JSON error, which is exactly
		// how OpenCLI's `user` verb fails today. Named so the two are
		// distinguishable in a log.
		return socialProfile{}, http.StatusBadGateway,
			fmt.Errorf("web_profile_info did not return JSON (%d bytes): %w", len(raw), err)
	}
	u := parsed.Data.User
	// ONLY what this call ADDS. `username`, `igid`, `fbid` and `full_name` are
	// already on the account from the messaging half, and returning a second
	// copy here would be two sources for one fact with nothing reconciling
	// them — the shape that put a wrong `interop_messaging_user_fbid` into
	// `05`'s field list in the first place.
	return socialProfile{
		IsPrivate:        u.IsPrivate,
		FollowedByViewer: u.FollowedByViewer,
		FollowsViewer:    u.FollowsViewer,
		Biography:        u.Biography,
		Followers:        u.EdgeFollowedBy.Count,
		Following:        u.EdgeFollow.Count,
		PostCount:        u.EdgeOwnerToTimelineMedia.Count,
		PostsReturned:    len(u.EdgeOwnerToTimelineMedia.Edges),
	}, http.StatusOK, nil
}
