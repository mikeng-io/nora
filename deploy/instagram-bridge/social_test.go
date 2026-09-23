package main

// GET /profile, tested for the things that fail SILENTLY.
//
// This route's answer gates a fetch, so its failure modes are not "wrong
// output" but "a refusal Nora then says about a person, on evidence that does
// not exist". The tests below are chosen for that:
//
//  1. `extra` must carry the unmodelled wire keys. `is_private` is NOT on
//     `slidetypes.User`, so the whole public-or-followed gate depends on
//     `Unrecognized` reaching the caller. A route that dropped it would 200
//     with a plausible body and make the operator's rule unimplementable, with
//     nothing anywhere saying so.
//  2. an error must NOT degrade to `following: false`. That is the one wrong
//     answer that reads as a real one.
//  3. the IGID goes out, not the fbid. Both are decimal strings, so the wrong
//     one validates, reaches Instagram, and names a different account or none.
//  4. `viewer` must not reach the body. It is Nora on every call.

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"

	"go.mau.fi/mautrix-meta/pkg/instameow/slidetypes"
	"go.mau.fi/mautrix-meta/pkg/messagix/httpclient"
	"go.mau.fi/mautrix-meta/pkg/messagix/responses"
)

func getProfile(t *testing.T, b *bridge, query string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(http.MethodGet, "/accounts/"+query, nil)
	rec := httptest.NewRecorder()
	b.routes().ServeHTTP(rec, req)
	return rec
}

// The gate's two halves, on the wire, under their own names.
//
// `following` is typed and comes off `FriendshipStatus`. `is_private` is not
// modelled anywhere in the library and survives ONLY through `Unrecognized` →
// `extra`. Asserting both in one test is deliberate: they are the two halves
// of one rule, and a change that keeps one and loses the other still leaves
// the gate undecidable.
func TestProfileCarriesTheGateIncludingUnmodelledKeys(t *testing.T) {
	b := newTestBridge(t, &fakeIG{})

	rec := getProfile(t, b, "221731317")

	if rec.Code != http.StatusOK {
		t.Fatalf("/profile answered %d: %s", rec.Code, rec.Body.String())
	}
	var body profileBody
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatalf("profile body did not decode: %v (%s)", err, rec.Body.String())
	}
	if !body.Following {
		t.Error("following was false; the measured account is one Nora follows, " +
			"and this is the half of the gate that is typed")
	}
	if got, ok := body.Extra["is_private"]; !ok || got != true {
		t.Errorf("extra[is_private] = %v (present=%v); `is_private` is NOT a field "+
			"on slidetypes.User, so dropping Unrecognized makes the public half of "+
			"the operator's gate unreadable — and the body still looks fine",
			got, ok)
	}
	if body.IGID != "221731317" {
		t.Errorf("igid = %q, want the id off the RESPONSE", body.IGID)
	}
	if body.FBID != "110966695387753" {
		t.Errorf("fbid = %q; the messaging id is carried so a caller can join "+
			"the two spaces without a second round trip", body.FBID)
	}
	if !body.HasStory {
		t.Error("has_story was false with latest_reel_media set; a caller uses " +
			"this to SKIP a story fetch, and a story fetch is visible to its author")
	}
}

// Nora's own account rides in `ProfilePageResponse.Viewer` on every call. It is
// not the subject of the read and must not appear in a body that describes
// somebody else.
func TestProfileDoesNotLeakTheViewer(t *testing.T) {
	b := newTestBridge(t, &fakeIG{})

	rec := getProfile(t, b, "221731317")

	if rec.Code != http.StatusOK {
		t.Fatalf("/profile answered %d: %s", rec.Code, rec.Body.String())
	}
	if got := rec.Body.String(); contains(got, "79597894900") || contains(got, "not_____nora") {
		t.Errorf("the viewer reached the body: %s", got)
	}
}

func contains(haystack, needle string) bool {
	return len(needle) > 0 && len(haystack) >= len(needle) &&
		(func() bool {
			for i := 0; i+len(needle) <= len(haystack); i++ {
				if haystack[i:i+len(needle)] == needle {
					return true
				}
			}
			return false
		})()
}

// A failed read must not answer 200. `following: false` on a Go zero value is
// indistinguishable from a measured "Nora does not follow them" — and the
// caller would turn that into a sentence refusing to read someone's content
// because of a network error.
func TestProfileFailureIsNeverAZeroValueGate(t *testing.T) {
	for _, tc := range []struct {
		name  string
		fake  *fakeIG
		want  int
		query string
	}{
		{
			// 200 with no user: what a GraphQL read does when it finds
			// nothing. NOT an error, which is why the route has to check.
			name:  "no user in the response",
			fake:  &fakeIG{profileUserSet: true, profileUser: nil},
			want:  http.StatusNotFound,
			query: "221731317",
		},
		{
			name:  "transport error",
			fake:  &fakeIG{profileErr: errors.New("connection reset")},
			want:  http.StatusInternalServerError,
			query: "221731317",
		},
		{
			// A human has to clear this one, and a retry hammers a challenged
			// account — the same mapping every other route uses.
			name:  "checkpointed session",
			fake:  &fakeIG{profileErr: httpclient.ErrCheckpointRequired},
			want:  http.StatusConflict,
			query: "221731317",
		},
		{
			name:  "not a decimal id",
			fake:  &fakeIG{},
			want:  http.StatusBadRequest,
			query: "nka_bpumvg",
		},
		{
			// An empty id is now refused by the ROUTER, not the handler:
			// `/accounts/` matches no route, so it is a 404 rather than the
			// old 400. Stronger, because the id can no longer arrive
			// empty-but-present at code that might read it.
			name:  "no igid at all",
			fake:  &fakeIG{},
			want:  http.StatusNotFound,
			query: "",
		},
	} {
		t.Run(tc.name, func(t *testing.T) {
			b := newTestBridge(t, tc.fake)

			rec := getProfile(t, b, tc.query)

			if rec.Code != tc.want {
				t.Fatalf("/profile answered %d, want %d: %s",
					rec.Code, tc.want, rec.Body.String())
			}
			var body profileBody
			// The body may or may not decode into profileBody (an error body
			// has a different shape); what must NOT happen is a decodable
			// profile whose gate reads as a measured refusal.
			if err := json.Unmarshal(rec.Body.Bytes(), &body); err == nil && body.OK {
				t.Errorf("a failed read produced ok=true: %s", rec.Body.String())
			}
		})
	}
}

// The IGID, not the fbid, and canonicalized.
//
// Both id spaces are decimal strings, so passing the wrong one produces no
// error anywhere — it names a different account, or none, and the caller gets
// a confident wrong answer. Asserting what went OUT is the only way this can
// fail; asserting the response cannot, because the fake would answer either.
func TestProfileAddressesTheIGIDSpaceAndCanonicalizes(t *testing.T) {
	f := &fakeIG{}
	b := newTestBridge(t, f)

	// Leading zeros: the same account, spelled two ways. Left alone it would
	// be two cache keys and two identities for one person.
	if rec := getProfile(t, b, "0221731317"); rec.Code != http.StatusOK {
		t.Fatalf("/profile answered %d: %s", rec.Code, rec.Body.String())
	}
	if len(f.profileIGIDs) != 1 || f.profileIGIDs[0] != "221731317" {
		t.Fatalf("GetProfile was addressed with %v, want [221731317] canonicalized",
			f.profileIGIDs)
	}
}

// A public account Nora has never had a thread with has no messaging row, so
// `interop_messaging_user_fbid` is 0. That is a missing JOIN KEY, not a failed
// read: refusing here would make every not-yet-messaged account unreadable,
// which is exactly the set the operator's public-or-followed rule is for.
func TestProfileWithNoMessagingIDStillAnswers(t *testing.T) {
	f := &fakeIG{profileUserSet: true, profileUser: &slidetypes.User{
		ID: "276321323", Username: "_qnpdwdmqvgho",
	}}
	b := newTestBridge(t, f)

	rec := getProfile(t, b, "276321323")

	if rec.Code != http.StatusOK {
		t.Fatalf("/profile answered %d: %s", rec.Code, rec.Body.String())
	}
	var body profileBody
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatalf("profile body did not decode: %v", err)
	}
	if body.FBID != "" {
		t.Errorf("fbid = %q, want empty — 0 means no messaging row, and "+
			"formatting it as \"0\" would be an id that names nobody", body.FBID)
	}
	if !body.OK {
		t.Error("ok was false; a missing join key is not a failed read")
	}
}

// fakeGraphQLIssuer records what it was asked for and answers with canned
// bytes — the seam that stands in for `*httpclient.HTTPClient`, which has
// no interface of its own to fake against directly (see `socialIssuerFor`'s
// doc comment in social.go).
type fakeGraphQLIssuer struct {
	sawIGID   []string
	sawFields any
	body      []byte
}

func (f *fakeGraphQLIssuer) MakeGraphQLRequest(
	_ context.Context, _ string, variables any,
) (*http.Response, []byte, error) {
	f.sawFields = variables
	if req, ok := variables.(*slidetypes.GetProfilePageRequest); ok {
		f.sawIGID = append(f.sawIGID, req.ID)
	}
	return &http.Response{StatusCode: http.StatusOK}, f.body, nil
}

// The regression this bundle actually fixed: the social call must be keyed
// on the account's numeric igid, not its username. `parseSocialProfile`'s
// own tests cannot see this — they start from response bytes, and this bug
// is about what goes INTO the request that produces them. Only
// `socialIssuerFor`'s injected seam can watch the value as it leaves
// `readProfile`.
func TestProfileKeysTheSocialCallOnTheIGIDNotTheUsername(t *testing.T) {
	issuer := &fakeGraphQLIssuer{body: []byte(`{"data":{"user":{"is_private":true}}}`)}
	restore := socialIssuerFor
	socialIssuerFor = func(igClient) (graphQLIssuer, error) { return issuer, nil }
	t.Cleanup(func() { socialIssuerFor = restore })

	// measuredProfile carries ID "221731317" and Username "nka_bpumvg" — two
	// different strings, so a test that only checked "some string was sent"
	// could not tell a regression to the old, wrong behaviour from a pass.
	f := &fakeIG{}
	b := newTestBridge(t, f)

	rec := getProfile(t, b, "221731317")

	if rec.Code != http.StatusOK {
		t.Fatalf("/profile answered %d: %s", rec.Code, rec.Body.String())
	}
	if len(issuer.sawIGID) != 1 || issuer.sawIGID[0] != "221731317" {
		t.Fatalf("social call keyed on %v, want [221731317] — the numeric igid, "+
			"not measuredProfile's username %q", issuer.sawIGID, "nka_bpumvg")
	}
	var body profileBody
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatalf("profile body did not decode: %v", err)
	}
	if !body.SocialAvailable {
		t.Errorf("social_available was false with SocialError %q; the fake issuer "+
			"answered 200 with a user, so this can only mean the wrong value was "+
			"sent and the fake refused to recognize it", body.SocialError)
	}
}

// --- GET /story ---------------------------------------------------------------
//
// A story read is the ONLY read in this system that the person being read can
// see. That makes two things testable that are not testable anywhere else: the
// route must never batch, and it must never report "not permitted" as "nothing
// posted".

func getStory(t *testing.T, b *bridge, query string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(http.MethodGet, "/accounts/"+query+"/story", nil)
	rec := httptest.NewRecorder()
	b.routes().ServeHTTP(rec, req)
	return rec
}

func decodeStory(t *testing.T, rec *httptest.ResponseRecorder) storyBody {
	t.Helper()
	var body storyBody
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatalf("story body did not decode: %v (%s)", err, rec.Body.String())
	}
	return body
}

// ONE igid per call, never a list, although FetchReel accepts several. Each
// extra id in a batch is another person who sees that Nora watched their story
// — paid to answer a question about somebody else.
func TestStoryNeverBatchesTheRead(t *testing.T) {
	f := &fakeIG{}
	b := newTestBridge(t, f)

	if rec := getStory(t, b, "221731317"); rec.Code != http.StatusOK {
		t.Fatalf("/story answered %d: %s", rec.Code, rec.Body.String())
	}
	if len(f.reelIDs) != 1 {
		t.Fatalf("FetchReel called %d times, want 1", len(f.reelIDs))
	}
	if len(f.reelIDs[0]) != 1 || f.reelIDs[0][0] != "221731317" {
		t.Errorf("FetchReel was addressed with %v, want exactly one igid — a "+
			"batched read is visible to everybody in the batch", f.reelIDs[0])
	}
}

// A video frame must be called a video AND keep its still. A caller that only
// wants to know what a story looks like fetches the thumbnail and never
// downloads the clip; one that reported the still as the media would have Nora
// describe a single frame as if she had watched it.
func TestStoryFramesKeepBothTheClipAndTheStill(t *testing.T) {
	b := newTestBridge(t, &fakeIG{})

	rec := getStory(t, b, "221731317")

	if rec.Code != http.StatusOK {
		t.Fatalf("/story answered %d: %s", rec.Code, rec.Body.String())
	}
	body := decodeStory(t, rec)
	if !body.Found {
		t.Fatal("found was false for a reel that is present")
	}
	if len(body.Items) != 2 {
		t.Fatalf("got %d frames, want 2", len(body.Items))
	}

	image := body.Items[0]
	if image.Kind != mediaImage {
		t.Errorf("frame 1 kind = %q, want image", image.Kind)
	}
	if image.URL != "https://cdn/large.jpg" {
		t.Errorf("frame 1 url = %q, want the LARGEST candidate", image.URL)
	}
	if image.AltText != "a photo of a plate" {
		t.Errorf("frame 1 alt_text = %q; Instagram's own description is free "+
			"and often answers the question with no fetch at all", image.AltText)
	}

	video := body.Items[1]
	if video.Kind != mediaVideo {
		t.Errorf("frame 2 kind = %q, want video", video.Kind)
	}
	if video.URL != "https://cdn/large.mp4" {
		t.Errorf("frame 2 url = %q, want the largest VIDEO", video.URL)
	}
	if video.ThumbnailURL != "https://cdn/cover.jpg" {
		t.Errorf("frame 2 thumbnail = %q; image_versions2 is populated for a "+
			"video story too and that cover frame is what makes a cheap look "+
			"possible", video.ThumbnailURL)
	}
	// The retention bound. A story is gone in 24 hours, so anything kept
	// outlives its source — 06's rule with a number behind it.
	if video.ExpiringAt == 0 || body.ExpiringAt == 0 {
		t.Error("expiring_at was dropped; it is the natural retention bound")
	}
}

// "media_type says video" and "there is a clip" are different facts. A delta
// claiming video with no video url must degrade to its cover frame rather than
// promise bytes that are not there.
func TestStoryVideoWithNoClipDegradesToItsCover(t *testing.T) {
	item := &responses.ReelItem{}
	item.Pk = "1"
	item.MediaType = 2
	item.ImageVersions2.Candidates = []responses.Candidates{
		{Width: 1080, URL: "https://cdn/cover.jpg"},
	}
	f := &fakeIG{reelSet: true, reelResp: &responses.ReelInfoResponse{
		Reels: map[string]responses.ReelInfo{
			"221731317": {ID: "221731317", Items: []*responses.ReelItem{item}},
		},
	}}
	b := newTestBridge(t, f)

	rec := getStory(t, b, "221731317")

	if rec.Code != http.StatusOK {
		t.Fatalf("/story answered %d: %s", rec.Code, rec.Body.String())
	}
	body := decodeStory(t, rec)
	if len(body.Items) != 1 {
		t.Fatalf("got %d frames, want 1", len(body.Items))
	}
	if body.Items[0].Kind != mediaImage {
		t.Errorf("kind = %q; media_type alone is not evidence a clip exists",
			body.Items[0].Kind)
	}
	if body.Items[0].URL != "https://cdn/cover.jpg" {
		t.Errorf("url = %q, want the cover frame", body.Items[0].URL)
	}
}

// The distinction the whole route turns on. "No story up" and "Nora may not
// see it" BOTH come back as zero frames, and reporting the second as the first
// states something about a person on the strength of a permission error.
//
// `found` is what separates them, and a caller has to be able to read it.
func TestStoryEmptyIsNotAClaimThatNothingWasPosted(t *testing.T) {
	f := &fakeIG{reelSet: true, reelResp: &responses.ReelInfoResponse{
		Reels: map[string]responses.ReelInfo{},
	}}
	b := newTestBridge(t, f)

	rec := getStory(t, b, "221731317")

	if rec.Code != http.StatusOK {
		t.Fatalf("/story answered %d: %s", rec.Code, rec.Body.String())
	}
	body := decodeStory(t, rec)
	if body.Found {
		t.Error("found was true for a reel that is not in the response")
	}
	if len(body.Items) != 0 {
		t.Errorf("items = %v, want none", body.Items)
	}
	if !body.OK {
		t.Error("ok was false; an account with no visible story is not an error")
	}
}

// The response declares BOTH `reels` (a map) and `reels_media` (a list) and
// which one arrives has not been measured for this call. Reading only the map
// would report "no story" for a shape that carried one — a silent false
// negative about a person.
func TestStoryReadsTheFlatReelsMediaShapeToo(t *testing.T) {
	item := &responses.ReelItem{}
	item.Pk = "1"
	item.ImageVersions2.Candidates = []responses.Candidates{{Width: 1080, URL: "https://cdn/a.jpg"}}
	f := &fakeIG{reelSet: true, reelResp: &responses.ReelInfoResponse{
		ReelsMedia: []responses.ReelInfo{
			{ID: "221731317", Items: []*responses.ReelItem{item}},
		},
	}}
	b := newTestBridge(t, f)

	rec := getStory(t, b, "221731317")

	if rec.Code != http.StatusOK {
		t.Fatalf("/story answered %d: %s", rec.Code, rec.Body.String())
	}
	body := decodeStory(t, rec)
	if !body.Found || len(body.Items) != 1 {
		t.Errorf("the flat shape was not read: found=%v items=%d",
			body.Found, len(body.Items))
	}
}

func TestStoryRefusesABadIGID(t *testing.T) {
	b := newTestBridge(t, &fakeIG{})
	// A MALFORMED id is the handler's 400. An ABSENT one is the router's
	// refusal — `/accounts//story` has an empty segment, which `http.ServeMux`
	// cleans and redirects (307) to a path this server does not serve. Both
	// are refusals; only one reaches this code.
	for _, q := range []string{"nka_bpumvg", "0"} {
		if rec := getStory(t, b, q); rec.Code != http.StatusBadRequest {
			t.Errorf("/accounts/%s/story answered %d, want 400", q, rec.Code)
		}
	}
	if rec := getStory(t, b, ""); rec.Code != http.StatusTemporaryRedirect {
		t.Errorf("an absent igid answered %d, want the router's 307", rec.Code)
	}
}
