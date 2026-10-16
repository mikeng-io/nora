package main

// GET /accounts/{igid}/items, and what the two configured GraphQL reads (the
// story and the timeline) share: a doc_id from config, the `stale_query`
// code, and never a seen-mark (design/cognition-loop/22-social-stream.md §1,
// §1a, §4).

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"regexp"
	"strings"
	"testing"

	"instagram-bridge/internal/domain"
	gateway "instagram-bridge/internal/gateway/instameow"
	"instagram-bridge/internal/gateway/instameow/fakeig"
)

type itemsBody struct {
	Items []struct {
		Code          string `json:"code"`
		PK            string `json:"pk"`
		TakenAt       int64  `json:"taken_at"`
		Kind          string `json:"kind"`
		MediaType     int    `json:"media_type"`
		Caption       string `json:"caption"`
		CarouselCount int    `json:"carousel_count"`
	} `json:"items"`
	NextCursor *string `json:"next_cursor"`
}

func getItems(t *testing.T, b *testBridge, igid, query string) *httptest.ResponseRecorder {
	t.Helper()
	return request(t, b, http.MethodGet, "/accounts/"+igid+"/items"+query, nil)
}

func decodeItems(t *testing.T, rec *httptest.ResponseRecorder) itemsBody {
	t.Helper()
	if rec.Code != http.StatusOK {
		t.Fatalf("/items answered %d: %s", rec.Code, rec.Body.String())
	}
	var body itemsBody
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatalf("items body did not decode: %v (%s)", err, rec.Body.String())
	}
	return body
}

// The timeline document is keyed on the USERNAME, which the route resolves
// through the profile read: one GetProfile on the igid, then one timeline
// page for the username it answered, at /graphql/query, with the captured
// variables.
func TestItemsResolvesTheUsernameThenReadsOnePage(t *testing.T) {
	f := &fakeIG{TimelineBody: []byte(fakeig.TimelineFirstPageJSON)}
	b := newTestBridge(t, f)

	decodeItems(t, getItems(t, b, fakeig.TimelineOwnerIGID, ""))

	if len(f.ProfileIGIDs) != 1 || f.ProfileIGIDs[0] != fakeig.TimelineOwnerIGID {
		t.Errorf("GetProfile addressed with %v, want the one igid", f.ProfileIGIDs)
	}
	if len(f.DocRequests) != 1 {
		t.Fatalf("%d documents sent, want 1", len(f.DocRequests))
	}
	req := f.DocRequests[0]
	if req.FriendlyName != fakeig.TimelineDocument || req.DocID != testTimelineDocID ||
		req.Endpoint != "https://www.instagram.com/graphql/query" {
		t.Errorf("sent %s (doc %s) to %s, want the timeline document at /graphql/query",
			req.FriendlyName, req.DocID, req.Endpoint)
	}
	var vars map[string]any
	if err := json.Unmarshal([]byte(req.Variables), &vars); err != nil {
		t.Fatalf("variables are not JSON: %v", err)
	}
	// The measured profile's username (fakeig.MeasuredProfile).
	if vars["username"] != fakeig.MeasuredProfile.Username {
		t.Errorf("username = %v, want the one the profile read answered", vars["username"])
	}
	if v, has := vars["after"]; !has || v != nil {
		t.Errorf("after = %v (present %v), want an explicit null on the first page", v, has)
	}
	if vars["first"] != float64(12) {
		t.Errorf("first = %v, want the web client's 12", vars["first"])
	}
	data, _ := vars["data"].(map[string]any)
	if data["count"] != float64(12) {
		t.Errorf("data.count = %v, want 12", data["count"])
	}
}

// A reel is `product_type: clips`; a carousel keeps its count; a null caption
// is "". `next_cursor` carries the page's end_cursor while there is more.
func TestItemsMapsReelsPostsAndTheCursor(t *testing.T) {
	f := &fakeIG{TimelineBody: []byte(fakeig.TimelineFirstPageJSON)}
	body := decodeItems(t, getItems(t, newTestBridge(t, f), fakeig.TimelineOwnerIGID, ""))

	if len(body.Items) != 3 {
		t.Fatalf("got %d items, want 3", len(body.Items))
	}
	reel, carousel, plain := body.Items[0], body.Items[1], body.Items[2]
	if reel.Code != "SynthReel01" || reel.PK != "2001" || reel.Kind != "reel" ||
		reel.MediaType != 2 || reel.TakenAt != 1790000300 || reel.Caption != "synthetic reel caption" {
		t.Errorf("reel = %+v", reel)
	}
	if carousel.Kind != "post" || carousel.CarouselCount != 3 || carousel.MediaType != 8 {
		t.Errorf("carousel = %+v, want a post with carousel_count 3", carousel)
	}
	if plain.Kind != "post" || plain.Caption != "" {
		t.Errorf("captionless post = %+v", plain)
	}
	if body.NextCursor == nil || *body.NextCursor != "synthetic-cursor-1" {
		t.Errorf("next_cursor = %v, want the page's end_cursor", body.NextCursor)
	}
}

// The end of the timeline is `next_cursor: null`, present, not omitted; and
// the cursor a caller sends back goes out as `after`.
func TestItemsLastPageIsANullCursorAndAfterIsForwarded(t *testing.T) {
	f := &fakeIG{TimelineBody: []byte(fakeig.TimelineLastPageJSON)}
	rec := getItems(t, newTestBridge(t, f), fakeig.TimelineOwnerIGID, "?after=synthetic-cursor-1")

	var raw map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &raw); err != nil {
		t.Fatal(err)
	}
	if v, has := raw["next_cursor"]; !has || v != nil {
		t.Errorf("next_cursor = %v (present %v), want an explicit null at the end", v, has)
	}
	if body := decodeItems(t, rec); len(body.Items) != 1 || body.Items[0].Code != "SynthPost04" {
		t.Errorf("items = %+v", body.Items)
	}
	var vars map[string]any
	_ = json.Unmarshal([]byte(f.DocRequests[0].Variables), &vars)
	if vars["after"] != "synthetic-cursor-1" {
		t.Errorf("after = %v, want the caller's cursor", vars["after"])
	}
}

// `has_next_page: true` with no cursor would read, as null, as the END of a
// timeline that has more. Refused instead.
func TestItemsMoreWithNoCursorIsRefused(t *testing.T) {
	body := `{"data":{"xdt_api__v1__feed__user_timeline_graphql_connection":{"edges":[],"page_info":{"end_cursor":null,"has_next_page":true}}}}`
	rec := getItems(t, newTestBridge(t, &fakeIG{TimelineBody: []byte(body)}), fakeig.TimelineOwnerIGID, "")
	if rec.Code != http.StatusBadGateway {
		t.Errorf("answered %d, want 502: %s", rec.Code, rec.Body.String())
	}
}

// A username can change hands between the profile lookup and the page. An
// item whose id names another owner refuses the page rather than recording
// somebody else's post under this person.
func TestItemsFromAnotherOwnerAreRefused(t *testing.T) {
	body := `{"data":{"xdt_api__v1__feed__user_timeline_graphql_connection":{"edges":[
		{"node":{"code":"SynthPost09","pk":"2009","id":"2009_999","taken_at":1,"media_type":1,"product_type":"feed"}}],
		"page_info":{"end_cursor":null,"has_next_page":false}}}}`
	rec := getItems(t, newTestBridge(t, &fakeIG{TimelineBody: []byte(body)}), fakeig.TimelineOwnerIGID, "")
	if rec.Code != http.StatusBadGateway || decodeEnvelope(t, rec).Error.Code != "upstream_rejected" {
		t.Errorf("answered %d %s, want 502 upstream_rejected", rec.Code, rec.Body.String())
	}
}

// A cursor that cannot be one is the caller's mistake, 400 before anything
// is sent.
func TestItemsRefusesAMalformedCursor(t *testing.T) {
	f := &fakeIG{}
	b := newTestBridge(t, f)
	for _, q := range []string{"?after=%00", "?after=" + longCursor()} {
		if rec := getItems(t, b, fakeig.TimelineOwnerIGID, q); rec.Code != http.StatusBadRequest {
			t.Errorf("%.20s… answered %d, want 400", q, rec.Code)
		}
	}
	if len(f.DocRequests) != 0 || len(f.ProfileIGIDs) != 0 {
		t.Error("a malformed cursor reached Instagram")
	}
}

func longCursor() string { return strings.Repeat("a", 1025) }

// A doc_id is configuration. Missing, the read answers 501 `unsupported`
// naming its variable, and sends nothing.
func TestAnUnconfiguredDocumentIs501NamingItsVariable(t *testing.T) {
	for _, tc := range []struct {
		name, path, env string
		docs            gateway.QueryDocs
	}{
		{"story", "/accounts/246967358/story", "INSTAGRAM_STORY_DOC_ID", gateway.QueryDocs{Timeline: testTimelineDocID}},
		{"items", "/accounts/246967358/items", "INSTAGRAM_TIMELINE_DOC_ID", gateway.QueryDocs{Story: testStoryDocID}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			f := &fakeIG{}
			rec := request(t, newTestBridge(t, f, withDocs(tc.docs)), http.MethodGet, tc.path, nil)
			if rec.Code != http.StatusNotImplemented {
				t.Fatalf("answered %d, want 501: %s", rec.Code, rec.Body.String())
			}
			env := decodeEnvelope(t, rec)
			if env.Error.Code != "unsupported" || !regexp.MustCompile(tc.env).MatchString(env.Error.Message) {
				t.Errorf("envelope = %+v, want unsupported naming %s", env.Error, tc.env)
			}
			if len(f.DocRequests) != 0 || len(f.ProfileIGIDs) != 0 {
				t.Errorf("an unconfigured read reached Instagram: %d documents, %d profile reads",
					len(f.DocRequests), len(f.ProfileIGIDs))
			}
		})
	}
}

// A GraphQL error naming the document is a rotated doc_id: 501 `stale_query`,
// and the SESSION is untouched (the login is fine; §4 halts only the read).
// An error that does not name it stays a 502 upstream rejection.
func TestAStaleDocumentIsStaleQueryAndLeavesTheSessionAlone(t *testing.T) {
	stale := []byte(`{"errors":[{"message":"PersistedQueryNotFound","code":1675002,"summary":"Query not found"}]}`)
	ordinary := []byte(`{"errors":[{"message":"A server error field_exception occured.","code":1675030,"summary":"Query Error"}]}`)
	for _, tc := range []struct {
		name string
		f    *fakeIG
		path string
		want int
		code string
	}{
		{"story stale", &fakeIG{StoryBody: stale}, "/accounts/246967358/story", 501, "stale_query"},
		{"items stale", &fakeIG{TimelineBody: stale}, "/accounts/246967358/items", 501, "stale_query"},
		{"story stale in a 400", &fakeIG{StoryBody: stale, DocStatus: 400}, "/accounts/246967358/story", 501, "stale_query"},
		{"story ordinary", &fakeIG{StoryBody: ordinary}, "/accounts/246967358/story", 502, "upstream_rejected"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			b := newTestBridge(t, tc.f)
			rec := request(t, b, http.MethodGet, tc.path, nil)
			if rec.Code != tc.want || decodeEnvelope(t, rec).Error.Code != tc.code {
				t.Errorf("answered %d %s, want %d %s", rec.Code, rec.Body.String(), tc.want, tc.code)
			}
			if s := b.gw.Snapshot().State; s != domain.StateAuthenticated {
				t.Errorf("session = %q after a document error, want authenticated", s)
			}
		})
	}
}

// A checkpoint met by a configured document needs a human (409) AND stops
// the session, as on the profile's raw read: no library checkpoint detection
// runs on a raw call, so the bridge has to (issue #533).
func TestACheckpointOnADocumentIs409AndStopsTheSession(t *testing.T) {
	checkpoint := []byte(`{"message":"checkpoint_required","checkpoint_url":"https://i.instagram.com/challenge/x","status":"fail"}`)
	for _, path := range []string{"/accounts/246967358/story", "/accounts/246967358/items"} {
		t.Run(path, func(t *testing.T) {
			b := newTestBridge(t, &fakeIG{StoryBody: checkpoint, TimelineBody: checkpoint, DocStatus: 400})
			rec := request(t, b, http.MethodGet, path, nil)
			if rec.Code != http.StatusConflict || decodeEnvelope(t, rec).Error.Code != "needs_human" {
				t.Errorf("answered %d %s, want 409 needs_human", rec.Code, rec.Body.String())
			}
			if s := b.gw.Snapshot().State; s == domain.StateAuthenticated {
				t.Error("the session stayed authenticated after a checkpoint")
			}
		})
	}
}

// The web viewer marks a story seen with a SEPARATE mutation
// (`PolarisStoriesV3SeenMutation`, §1). No request these reads send may carry
// a friendly name, an endpoint or a referer that says "seen", on any outcome:
// found, empty, paged, stale or failed. Every request the fake received is
// checked, not the ones the bridge meant to send.
//
// What this does NOT check is the doc_id: Instagram executes the id, not the
// name, and an id is opaque. A wrong id (a seen-mutation's pasted into a
// read's variable) is caught by what it ANSWERS: no read root, so
// `stale_query`, which halts the read instead of resending it
// (TestAnAnswerWithoutTheReadRootHaltsTheRead), and two variables holding one
// id are refused outright (TestOneDocIDInBothVariablesSendsNeither).
func TestNoReadSendsASeenMarkByNameOrURL(t *testing.T) {
	seen := regexp.MustCompile(`(?i)seen`)
	stale := []byte(`{"errors":[{"message":"PersistedQueryNotFound","code":1}]}`)
	var all []fakeig.DocRequest
	for _, f := range []*fakeIG{
		{},
		{StoryBody: []byte(fakeig.NoStoryJSON)},
		{TimelineBody: []byte(fakeig.TimelineFirstPageJSON)},
		{StoryBody: stale, TimelineBody: stale},
		{DocStatus: 500},
	} {
		b := newTestBridge(t, f)
		request(t, b, http.MethodGet, "/accounts/246967358/story", nil)
		request(t, b, http.MethodGet, "/accounts/246967358/items", nil)
		request(t, b, http.MethodGet, "/accounts/246967358/items?after=synthetic-cursor-1", nil)
		all = append(all, f.DocRequests...)
	}
	if len(all) != 15 {
		t.Fatalf("recorded %d requests, want 15 (3 reads x 5 outcomes); the check must see them all", len(all))
	}
	for _, r := range all {
		if seen.MatchString(r.FriendlyName) || seen.MatchString(r.Endpoint) || seen.MatchString(r.Referer) {
			t.Errorf("a read sent %s to %s (referer %s): a seen-mark", r.FriendlyName, r.Endpoint, r.Referer)
		}
		if r.FriendlyName != fakeig.StoryDocument && r.FriendlyName != fakeig.TimelineDocument {
			t.Errorf("a read sent an unexpected document %q", r.FriendlyName)
		}
	}
}

// Instagram executes the doc_id. A wrong id in a read's variable (say, the
// seen mutation's) is sent under the read's name and answers with ANOTHER
// document's root. That answer is `stale_query`, halting the read, never a
// 502 that backs the login off and resends the same id next poll; and the
// session is left alone.
func TestAnAnswerWithoutTheReadRootHaltsTheRead(t *testing.T) {
	mutationShaped := []byte(`{"data":{"xdt_mark_story_seen":{"success":true}},"extensions":{"is_final":true}}`)
	for _, path := range []string{"/accounts/246967358/story", "/accounts/246967358/items"} {
		t.Run(path, func(t *testing.T) {
			b := newTestBridge(t, &fakeIG{StoryBody: mutationShaped, TimelineBody: mutationShaped})
			rec := request(t, b, http.MethodGet, path, nil)
			if rec.Code != http.StatusNotImplemented || decodeEnvelope(t, rec).Error.Code != "stale_query" {
				t.Errorf("answered %d %s, want 501 stale_query", rec.Code, rec.Body.String())
			}
			if s := b.gw.Snapshot().State; s != domain.StateAuthenticated {
				t.Errorf("session = %q, want authenticated", s)
			}
		})
	}
}

// Two variables holding ONE doc_id means one names the wrong document. Both
// reads answer 501 `unsupported` saying so, and nothing is sent; main reports
// it at start (queryDocsFromEnv).
func TestOneDocIDInBothVariablesSendsNeither(t *testing.T) {
	f := &fakeIG{}
	b := newTestBridge(t, f, withDocs(gateway.QueryDocs{Story: "1234", Timeline: "1234"}))
	for _, path := range []string{"/accounts/246967358/story", "/accounts/246967358/items"} {
		rec := request(t, b, http.MethodGet, path, nil)
		env := decodeEnvelope(t, rec)
		if rec.Code != http.StatusNotImplemented || env.Error.Code != "unsupported" ||
			!strings.Contains(env.Error.Message, "same doc_id") {
			t.Errorf("%s answered %d %+v, want 501 unsupported naming the shared id", path, rec.Code, env.Error)
		}
	}
	if len(f.DocRequests)+len(f.ProfileIGIDs) != 0 {
		t.Errorf("sent %d documents and %d profile reads with a shared doc_id", len(f.DocRequests), len(f.ProfileIGIDs))
	}
	docs, problems := queryDocsFromEnv(func(k string) string { return "1234" })
	if len(problems) != 1 || !strings.Contains(problems[0], "same doc_id") || docs.Problem() == nil {
		t.Errorf("queryDocsFromEnv problems = %v, want the shared id reported at start", problems)
	}
}

// Every item must NAME its owner, and every owner it names must be the igid:
// an id with no `_<owner>` and no user/owner field cannot be shown to be this
// account's; a `user.pk` that differs from the id's owner is somebody else's.
// One that names it only through `owner.pk` or `user.pk` is fine.
func TestItemsOwnerMustBeNamedAndMustBeTheIGID(t *testing.T) {
	page := func(node string) []byte {
		return []byte(`{"data":{"xdt_api__v1__feed__user_timeline_graphql_connection":{"edges":[{"node":` +
			node + `}],"page_info":{"end_cursor":null,"has_next_page":false}}}}`)
	}
	for name, tc := range map[string]struct {
		node string
		want int
	}{
		"no owner anywhere":    {`{"code":"SynthPost09","pk":"2009","id":"2009","taken_at":1,"media_type":1}`, 502},
		"user.pk differs":      {`{"code":"SynthPost09","pk":"2009","id":"2009_246967358","user":{"pk":"999"},"taken_at":1,"media_type":1}`, 502},
		"owner.pk differs":     {`{"code":"SynthPost09","pk":"2009","owner":{"pk":"999"},"taken_at":1,"media_type":1}`, 502},
		"only user.pk, right":  {`{"code":"SynthPost09","pk":"2009","id":"2009","user":{"pk":"246967358"},"taken_at":1,"media_type":1}`, 200},
		"only owner.id, right": {`{"code":"SynthPost09","pk":"2009","owner":{"id":"246967358"},"taken_at":1,"media_type":1}`, 200},
	} {
		t.Run(name, func(t *testing.T) {
			rec := getItems(t, newTestBridge(t, &fakeIG{TimelineBody: page(tc.node)}), fakeig.TimelineOwnerIGID, "")
			if rec.Code != tc.want {
				t.Errorf("answered %d %s, want %d", rec.Code, rec.Body.String(), tc.want)
			}
		})
	}
}

// The profile lookup must answer about the igid asked for; its username
// would otherwise page somebody else's timeline.
func TestItemsRefusesAProfileReadAboutAnotherAccount(t *testing.T) {
	other := *fakeig.MeasuredProfile
	other.ID = "999"
	f := &fakeIG{ProfileUserSet: true, ProfileUser: &other}
	rec := getItems(t, newTestBridge(t, f), fakeig.TimelineOwnerIGID, "")
	if rec.Code != http.StatusBadGateway || len(f.DocRequests) != 0 {
		t.Errorf("answered %d with %d documents sent, want 502 and none", rec.Code, len(f.DocRequests))
	}
}

// A caller that holds the username (a back-fill paging on) passes it: no
// profile read, one upstream call a page, and that username goes out. The
// owner check is what keeps it honest (the test above).
func TestItemsWithAUsernameSkipsTheProfileRead(t *testing.T) {
	f := &fakeIG{TimelineBody: []byte(fakeig.TimelineFirstPageJSON)}
	decodeItems(t, getItems(t, newTestBridge(t, f), fakeig.TimelineOwnerIGID, "?username=synthetic_handle&after=c1"))
	if len(f.ProfileIGIDs) != 0 {
		t.Errorf("GetProfile called %d times with a username given, want 0", len(f.ProfileIGIDs))
	}
	if len(f.DocRequests) != 1 {
		t.Fatalf("%d documents sent, want 1", len(f.DocRequests))
	}
	var vars map[string]any
	_ = json.Unmarshal([]byte(f.DocRequests[0].Variables), &vars)
	if vars["username"] != "synthetic_handle" || vars["after"] != "c1" {
		t.Errorf("username=%v after=%v, want the caller's", vars["username"], vars["after"])
	}
}

// The username is validated strictly at the edge: a bare handle only.
func TestItemsRefusesAMalformedUsername(t *testing.T) {
	f := &fakeIG{}
	b := newTestBridge(t, f)
	for _, q := range []string{"?username=%40synthetic_handle", "?username=bad-name", "?username=" + strings.Repeat("a", 31)} {
		if rec := getItems(t, b, fakeig.TimelineOwnerIGID, q); rec.Code != http.StatusBadRequest {
			t.Errorf("%s answered %d, want 400", q, rec.Code)
		}
	}
	if len(f.DocRequests)+len(f.ProfileIGIDs) != 0 {
		t.Error("a malformed username reached Instagram")
	}
}
