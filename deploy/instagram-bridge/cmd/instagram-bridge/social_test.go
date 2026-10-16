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
	"strings"
	"testing"

	"instagram-bridge/internal/gateway/instameow/fakeig"

	"go.mau.fi/mautrix-meta/pkg/instameow/slidetypes"
	"go.mau.fi/mautrix-meta/pkg/messagix/httpclient"
)

func getProfile(t *testing.T, b *testBridge, query string) *httptest.ResponseRecorder {
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

	rec := getProfile(t, b, "246967358")

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
	if body.IGID != "246967358" {
		t.Errorf("igid = %q, want the id off the RESPONSE", body.IGID)
	}
	if body.FBID != "186217604552756" {
		t.Errorf("fbid = %q; the messaging id is carried so a caller can join "+
			"the two spaces without a second round trip", body.FBID)
	}
	if !body.HasStory {
		t.Error("has_story was false with latest_reel_media set; a caller uses " +
			"this to SKIP a story fetch, and the social stream reads a story only when it moved")
	}
}

// Nora's own account rides in `ProfilePageResponse.Viewer` on every call. It is
// not the subject of the read and must not appear in a body that describes
// somebody else.
func TestProfileDoesNotLeakTheViewer(t *testing.T) {
	b := newTestBridge(t, &fakeIG{})

	rec := getProfile(t, b, "246967358")

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
			fake:  &fakeIG{ProfileUserSet: true, ProfileUser: nil},
			want:  http.StatusNotFound,
			query: "246967358",
		},
		{
			name:  "transport error",
			fake:  &fakeIG{ProfileErr: errors.New("connection reset")},
			want:  http.StatusInternalServerError,
			query: "246967358",
		},
		{
			// A human has to clear this one, and a retry hammers a challenged
			// account — the same mapping every other route uses.
			name:  "checkpointed session",
			fake:  &fakeIG{ProfileErr: httpclient.ErrCheckpointRequired},
			want:  http.StatusConflict,
			query: "246967358",
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
			if tc.query == "" {
				// The router's own 404, which no handler wrote.
				return
			}
			// A failed read is the envelope and nothing else: never a
			// decodable profile whose gate reads as a measured refusal.
			env := decodeEnvelope(t, rec)
			var raw map[string]any
			_ = json.Unmarshal(rec.Body.Bytes(), &raw)
			if len(raw) != 1 || env.Error.Message == "" {
				t.Errorf("a failed read carried more than the envelope: %s", rec.Body.String())
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
	if rec := getProfile(t, b, "0246967358"); rec.Code != http.StatusOK {
		t.Fatalf("/profile answered %d: %s", rec.Code, rec.Body.String())
	}
	if len(f.ProfileIGIDs) != 1 || f.ProfileIGIDs[0] != "246967358" {
		t.Fatalf("GetProfile was addressed with %v, want [246967358] canonicalized",
			f.ProfileIGIDs)
	}
}

// A public account Nora has never had a thread with has no messaging row, so
// `interop_messaging_user_fbid` is 0. That is a missing JOIN KEY, not a failed
// read: refusing here would make every not-yet-messaged account unreadable,
// which is exactly the set the operator's public-or-followed rule is for.
func TestProfileWithNoMessagingIDStillAnswers(t *testing.T) {
	f := &fakeIG{ProfileUserSet: true, ProfileUser: &slidetypes.User{
		ID: "286113965", Username: "_qnpdwdmqvgho",
	}}
	b := newTestBridge(t, f)

	rec := getProfile(t, b, "286113965")

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
	if body.IGID != "286113965" || body.Username != "_qnpdwdmqvgho" {
		t.Errorf("a missing join key lost the account: %+v", body)
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
// is about what goes INTO the request that produces them. Only an injected
// issuer can watch the value as it leaves the account read.
func TestProfileKeysTheSocialCallOnTheIGIDNotTheUsername(t *testing.T) {
	issuer := &fakeGraphQLIssuer{body: []byte(`{"data":{"user":{"is_private":true}}}`)}

	// measuredProfile carries ID "246967358" and Username "nka_bpumvg" — two
	// different strings, so a test that only checked "some string was sent"
	// could not tell a regression to the old, wrong behaviour from a pass.
	f := &fakeIG{}
	b := newTestBridge(t, f, withIssuer(issuer))

	rec := getProfile(t, b, "246967358")

	if rec.Code != http.StatusOK {
		t.Fatalf("/profile answered %d: %s", rec.Code, rec.Body.String())
	}
	if len(issuer.sawIGID) != 1 || issuer.sawIGID[0] != "246967358" {
		t.Fatalf("social call keyed on %v, want [246967358] — the numeric igid, "+
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

func getStory(t *testing.T, b *testBridge, query string) *httptest.ResponseRecorder {
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

// ONE igid per call, never a list, although `reel_ids_arr` accepts several.
// Each extra id in a batch is another person's story opened to answer a
// question about somebody else. Asserted on the variables that went OUT.
func TestStoryNeverBatchesTheRead(t *testing.T) {
	f := &fakeIG{}
	b := newTestBridge(t, f)

	if rec := getStory(t, b, "246967358"); rec.Code != http.StatusOK {
		t.Fatalf("/story answered %d: %s", rec.Code, rec.Body.String())
	}
	if len(f.DocRequests) != 1 {
		t.Fatalf("%d documents sent, want 1", len(f.DocRequests))
	}
	req := f.DocRequests[0]
	if req.FriendlyName != fakeig.StoryDocument || req.DocID != testStoryDocID ||
		req.Endpoint != "https://www.instagram.com/api/graphql" {
		t.Errorf("sent %s (doc %s) to %s, want the story document, its configured "+
			"doc_id, at /api/graphql", req.FriendlyName, req.DocID, req.Endpoint)
	}
	var vars map[string]any
	if err := json.Unmarshal([]byte(req.Variables), &vars); err != nil {
		t.Fatalf("variables are not JSON: %v (%s)", err, req.Variables)
	}
	ids, _ := vars["reel_ids_arr"].([]any)
	if len(ids) != 1 || ids[0] != "246967358" {
		t.Errorf("reel_ids_arr = %v, want exactly the one igid asked about", vars["reel_ids_arr"])
	}
	if vars["__relay_internal__pv__PolarisCommunityNoteStoriesLabelEnabledrelayprovider"] != true {
		t.Errorf("the relay flag the web client sends is missing: %s", req.Variables)
	}
}

// A video frame must be called a video AND keep its still. A caller that only
// wants to know what a story looks like fetches the thumbnail and never
// downloads the clip; one that reported the still as the media would have Nora
// describe a single frame as if she had watched it.
func TestStoryFramesKeepBothTheClipAndTheStill(t *testing.T) {
	b := newTestBridge(t, &fakeIG{})

	rec := getStory(t, b, "246967358")

	if rec.Code != http.StatusOK {
		t.Fatalf("/story answered %d: %s", rec.Code, rec.Body.String())
	}
	body := decodeStory(t, rec)
	if !body.Found || body.Username != "nka_bpumvg" {
		t.Fatalf("found=%v username=%q for a reel that is present", body.Found, body.Username)
	}
	if len(body.Items) != 2 {
		t.Fatalf("got %d frames, want 2", len(body.Items))
	}

	image := body.Items[0]
	if image.ID != "1001" || image.Kind != "image" || image.TakenAt != 1790000432 {
		t.Errorf("frame 1 = %+v, want pk 1001, an image, its own taken_at", image)
	}
	if image.URL != "https://cdn/large.jpg" {
		t.Errorf("frame 1 url = %q, want the LARGEST candidate", image.URL)
	}
	if image.Caption != "synthetic story caption" || image.AltText != "a photo of a plate" {
		t.Errorf("frame 1 caption=%q alt_text=%q; both are free and often answer "+
			"the question with no fetch at all", image.Caption, image.AltText)
	}

	video := body.Items[1]
	if video.Kind != "video" {
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
	if video.Caption != "" {
		t.Errorf("frame 2 caption = %q from a null caption", video.Caption)
	}
}

// The retention bound, per frame. S0 measured the REEL's `expiring_at` as null
// and each frame's as its own taken_at + 86400, so each frame keeps its own,
// and the story's is the latest of them (the set ends when its newest frame
// does). A reel that does send its own keeps it.
func TestStoryExpiryIsPerFrameAndTheReelTakesTheLatest(t *testing.T) {
	body := decodeStory(t, getStory(t, newTestBridge(t, &fakeIG{}), "246967358"))
	if len(body.Items) != 2 {
		t.Fatalf("got %d frames, want 2", len(body.Items))
	}
	if body.Items[0].ExpiringAt != 1790086832 || body.Items[1].ExpiringAt != 1790098244 {
		t.Errorf("frame expiries = %d, %d, want each frame's own",
			body.Items[0].ExpiringAt, body.Items[1].ExpiringAt)
	}
	if body.ExpiringAt != 1790098244 {
		t.Errorf("story expiring_at = %d, want the latest frame's (the reel's was null)", body.ExpiringAt)
	}

	withOwn := strings.Replace(fakeig.MeasuredStoryJSON, `"expiring_at": null`, `"expiring_at": 1790099999`, 1)
	body = decodeStory(t, getStory(t, newTestBridge(t, &fakeIG{StoryBody: []byte(withOwn)}), "246967358"))
	if body.ExpiringAt != 1790099999 {
		t.Errorf("story expiring_at = %d, want the reel's own when it sends one", body.ExpiringAt)
	}
}

// "media_type says video" and "there is a clip" are different facts. A frame
// claiming video with no video url must degrade to its cover frame rather
// than promise bytes that are not there.
func TestStoryVideoWithNoClipDegradesToItsCover(t *testing.T) {
	f := &fakeIG{StoryBody: []byte(`{"data":{"xdt_api__v1__feed__reels_media":{"reels_media":[{
		"id": "246967358", "user": {"pk": "246967358"},
		"items": [{"pk": "1", "media_type": 2,
			"image_versions2": {"candidates": [{"width": 1080, "url": "https://cdn/cover.jpg"}]}}]}]}}}`)}
	b := newTestBridge(t, f)

	rec := getStory(t, b, "246967358")

	if rec.Code != http.StatusOK {
		t.Fatalf("/story answered %d: %s", rec.Code, rec.Body.String())
	}
	body := decodeStory(t, rec)
	if len(body.Items) != 1 {
		t.Fatalf("got %d frames, want 1", len(body.Items))
	}
	if body.Items[0].Kind != "image" {
		t.Errorf("kind = %q; media_type alone is not evidence a clip exists",
			body.Items[0].Kind)
	}
	if body.Items[0].URL != "https://cdn/cover.jpg" {
		t.Errorf("url = %q, want the cover frame", body.Items[0].URL)
	}
}

// The distinction the whole route turns on. "No story up" and "Nora may not
// see it" BOTH come back as an empty `reels_media`, and reporting the second
// as the first states something about a person on the strength of a
// permission error.
//
// `found` is what separates them, and a caller has to be able to read it.
func TestStoryEmptyIsNotAClaimThatNothingWasPosted(t *testing.T) {
	f := &fakeIG{StoryBody: []byte(fakeig.NoStoryJSON)}
	b := newTestBridge(t, f)

	rec := getStory(t, b, "246967358")

	if rec.Code != http.StatusOK {
		t.Fatalf("/story answered %d: %s", rec.Code, rec.Body.String())
	}
	body := decodeStory(t, rec)
	if body.Found {
		t.Error("found was true for an empty reels_media")
	}
	if len(body.Items) != 0 {
		t.Errorf("items = %v, want none", body.Items)
	}
	if body.IGID == "" {
		t.Error("an account with no visible story answered no account; it is not an error")
	}
}

// A 200 with no reel LIST, or with a reel for somebody else, is not "no story"
// either: neither is ever read as `found: false` about the person asked
// about. No list at all (and no error) is an answer to some OTHER document,
// so it is `stale_query`, halting the read (items_test.go has the seen-mark
// case); a stranger's reel is a 502.
func TestStoryWithoutTheAskedReelIsRefusedNotEmpty(t *testing.T) {
	for name, tc := range map[string]struct {
		body   string
		status int
		code   string
	}{
		"no reel list":   {`{"data":{"xdt_api__v1__feed__reels_media":null}}`, 501, "stale_query"},
		"another's reel": {`{"data":{"xdt_api__v1__feed__reels_media":{"reels_media":[{"id":"999","user":{"pk":"999"},"items":[]}]}}}`, 502, "upstream_rejected"},
	} {
		t.Run(name, func(t *testing.T) {
			rec := getStory(t, newTestBridge(t, &fakeIG{StoryBody: []byte(tc.body)}), "246967358")
			if rec.Code != tc.status || decodeEnvelope(t, rec).Error.Code != tc.code {
				t.Errorf("answered %d %s, want %d %s", rec.Code, rec.Body.String(), tc.status, tc.code)
			}
		})
	}
}

// A GraphQL error BESIDE the reel does not lose the story: the reel is read
// (and the errors logged). Without the reel, the same error is a 502, and a
// checkpoint keeps its precedence over both.
func TestStoryErrorsBesideTheReelStillReadTheReel(t *testing.T) {
	withErr := strings.Replace(fakeig.MeasuredStoryJSON, `"extensions":{"is_final":true}`,
		`"errors":[{"message":"A server error field_exception occured.","code":1675030,"summary":"Query Error","path":["x"]}]`, 1)
	body := decodeStory(t, getStory(t, newTestBridge(t, &fakeIG{StoryBody: []byte(withErr)}), "246967358"))
	if !body.Found || len(body.Items) != 2 {
		t.Errorf("found=%v items=%d; a partial error must not drop the reel", body.Found, len(body.Items))
	}

	noReel := `{"data":{"xdt_api__v1__feed__reels_media":null},"errors":[{"message":"A server error field_exception occured.","code":1675030,"summary":"Query Error"}]}`
	rec := getStory(t, newTestBridge(t, &fakeIG{StoryBody: []byte(noReel)}), "246967358")
	if rec.Code != http.StatusBadGateway || decodeEnvelope(t, rec).Error.Code != "upstream_rejected" {
		t.Errorf("errors with no reel answered %d %s, want 502", rec.Code, rec.Body.String())
	}

	checkpoint := `{"status":"fail","message":"checkpoint_required","checkpoint_url":"https://i.instagram.com/challenge/x","errors":[{"message":"x"}],"data":{"xdt_api__v1__feed__reels_media":{"reels_media":[]}}}`
	rec = getStory(t, newTestBridge(t, &fakeIG{StoryBody: []byte(checkpoint)}), "246967358")
	if rec.Code != http.StatusConflict {
		t.Errorf("a checkpoint beside data answered %d, want 409", rec.Code)
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
