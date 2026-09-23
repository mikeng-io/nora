package main

// parseSocialProfile, tested against literal bytes rather than a live
// account — see its doc comment for why it is split out of
// readSocialProfile at all: `*httpclient.HTTPClient` has no test seam.
//
// Both tests below are regression tests for mistakes that were live in
// production, not hypothetical ones:
//
//  1. A `field_exception` envelope is a 200 with an `errors` array instead of
//     `data`. The first version of this parser checked only the transport
//     status and returned `socialProfile{}, nil` for this exact shape — a
//     silent "success" with every field zero. Measured 4/4 on a real account
//     before the fix (2026-09-22): a username sent through
//     `PolarisProfilePageContentQuery`'s numeric `id` field fails this way
//     every time, and was being recorded as `social_available: true`.
//  2. A real success envelope must populate every named field, keyed off the
//     actual shape Instagram returned for this account on 2026-09-22 — not a
//     shape guessed ahead of the measurement, which is what put a wrong
//     `interop_messaging_user_fbid` into 05 twice already.

import (
	"strings"
	"testing"
)

// fieldExceptionEnvelope is the exact shape measured live: a 200 whose body
// is an `errors` array, no `data` key at all.
const fieldExceptionEnvelope = `{"errors":[{"message":"A server error field_exception occured. Check server logs for details.","severity":"CRITICAL","mids":["f0136f328befa77fe031ff7c53d932b4"],"debug_link":"https://www.meta.com/debug/?mid=f0136f328befa77fe031ff7c53d932b4","code":1675030,"api_error_code":-1,"summary":"Query Error","description":"Error performing query.","description_html":null,"description_raw":"Error performing query.","is_silent":false,"is_transient":false,"is_not_critical":false,"requires_reauth":false,"allow_user_retry":false,"debug_info":null,"query_path":null,"fbtrace_id":"F1CUAXL+Uuc","www_request_id":"A2b1faVSaoWmzADV29iJri-","path":["user"]}],"extensions":{"dtsg_token":"NAfxAATXtBq4-vYscgjOE4S6d2E0"}}`

func TestParseSocialProfileRefusesAFieldExceptionRatherThanReadingItAsEmpty(t *testing.T) {
	out, err := parseSocialProfile([]byte(fieldExceptionEnvelope))
	if err == nil {
		t.Fatalf("a field_exception envelope parsed with no error; got %+v", out)
	}
	if !strings.Contains(err.Error(), "Query Error") {
		t.Errorf("error %q lost the GraphQL summary that tells an operator what failed", err)
	}
	if out != (socialProfile{}) {
		t.Errorf("a failed parse returned a non-zero profile: %+v", out)
	}
}

// realProfileEnvelope is a trimmed version of a real `data.user` response
// (measured 2026-09-22, account igid 221731317): the named fields plus one
// unmodelled key (`account_type`) to prove extras are ignored rather than
// erroring the parse.
const realProfileEnvelope = `{"data":{"user":{
	"account_type": 1,
	"is_private": true,
	"biography": "From complexity to clarity",
	"external_url": "",
	"follower_count": 116,
	"following_count": 285,
	"mutual_followers_count": 5,
	"media_count": 268,
	"total_clips_count": 1,
	"is_business": false,
	"is_professional_account": null,
	"category": null,
	"profile_pic_url": "https://scontent.cdninstagram.com/pic.jpg",
	"friendship_status": {"following": true, "followed_by": true, "blocking": false}
}}}`

func TestParseSocialProfileNamesEveryMeasuredField(t *testing.T) {
	out, err := parseSocialProfile([]byte(realProfileEnvelope))
	if err != nil {
		t.Fatalf("a real profile envelope failed to parse: %v", err)
	}
	want := socialProfile{
		IsPrivate:            true,
		FollowedBy:           true,
		Biography:            "From complexity to clarity",
		FollowerCount:        116,
		FollowingCount:       285,
		MutualFollowersCount: 5,
		MediaCount:           268,
		TotalClipsCount:      1,
		ProfilePicURL:        "https://scontent.cdninstagram.com/pic.jpg",
	}
	if out != want {
		t.Errorf("parsed profile = %+v, want %+v", out, want)
	}
}

func TestParseSocialProfileRefusesAUserlessBodyWithNoErrors(t *testing.T) {
	// Neither `errors` nor a `user` — a shape that should not exist but, per
	// this bundle's own history of wrong field-set guesses, gets a named
	// refusal rather than a silent zero-value success if it ever does.
	out, err := parseSocialProfile([]byte(`{"data":{}}`))
	if err == nil {
		t.Fatalf("a userless body with no errors parsed with no error; got %+v", out)
	}
}
