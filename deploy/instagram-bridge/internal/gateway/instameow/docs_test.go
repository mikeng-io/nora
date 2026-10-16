package instameow

// The configured GraphQL documents (docs.go): what graphqlDoc refuses to send,
// how it tells a rotated doc_id from an ordinary GraphQL error, and that a
// session being rebuilt sends nothing. The whole-route behaviour is in
// cmd/instagram-bridge/items_test.go and social_test.go.

import (
	"errors"
	"io"
	"net/http"
	"net/url"
	"strings"
	"testing"

	"instagram-bridge/internal/domain"
	"instagram-bridge/internal/gateway/instameow/fakeig"
	"instagram-bridge/internal/session"

	"github.com/rs/zerolog"
	"go.mau.fi/mautrix-meta/pkg/messagix/cookies"
	"go.mau.fi/mautrix-meta/pkg/messagix/httpclient"
	"go.mau.fi/mautrix-meta/pkg/messagix/types"
	"go.mau.fi/util/exhttp"
	"go.opentelemetry.io/otel/codes"
)

// A read sends a read. Whatever a later edit names a document, one whose name
// says it is a mutation or a seen-mark is refused before a byte goes out.
func TestGraphqlDocRefusesAMutationOrASeenMark(t *testing.T) {
	for _, name := range []string{"PolarisStoriesV3SeenMutation", "SomethingSeenQuery", "AnyMutation"} {
		f := &fakeIG{}
		d := webDoc{friendlyName: name, endpoint: graphqlAPIEndpoint, docID: "1234", env: EnvStoryDocID, label: "x"}
		if _, _, err := graphqlDoc(t.Context(), f, d, restOrigin+"/", storyVars{}); !errors.Is(err, errNotARead) {
			t.Errorf("%s: err = %v, want errNotARead", name, err)
		}
		if len(f.DocRequests) != 0 {
			t.Errorf("%s: %d requests sent", name, len(f.DocRequests))
		}
	}
}

// An error that names the document (its name, its id, or the words a GraphQL
// server uses for an unknown persisted query) is ErrStaleQuery, and its span
// says so. An error about the DATA is not.
func TestAnErrorNamingTheDocumentIsStaleQuery(t *testing.T) {
	d := storyDoc("1234")
	for name, tc := range map[string]struct {
		body  string
		stale bool
	}{
		"persisted query": {`{"errors":[{"message":"PersistedQueryNotFound","code":1}]}`, true},
		"doc_id":          {`{"errors":[{"message":"x","summary":"Unknown doc_id","code":1}]}`, true},
		"the id itself":   {`{"errors":[{"message":"no such query 1234","code":1}]}`, true},
		"the name":        {`{"errors":[{"description":"PolarisStoriesV3ReelPageStandaloneQuery is gone","code":1}]}`, true},
		"field exception": {`{"errors":[{"message":"A server error field_exception occured. Check server logs for details.","summary":"Query Error","description":"Error performing query.","code":1675030}]}`, false},
	} {
		t.Run(name, func(t *testing.T) {
			rec := recordSpans(t)
			f := &fakeIG{StoryBody: []byte(tc.body)}
			_, _, err := graphqlDoc(t.Context(), f, d, restOrigin+"/", storyVars{})
			if got := errors.Is(err, domain.ErrStaleQuery); got != tc.stale {
				t.Fatalf("stale = %v (err %v), want %v", got, err, tc.stale)
			}
			if !tc.stale && !errors.Is(err, domain.ErrUpstreamRejected) {
				t.Errorf("err = %v, want ErrUpstreamRejected", err)
			}
			s := spanNamed(t, rec, "instagram.graphql PolarisStoriesV3ReelPageStandaloneQuery")
			want := "upstream_rejected"
			if tc.stale {
				want = "stale_query"
			}
			if v, _ := attrOf(s, "error.type"); s.Status().Code != codes.Error || v.AsString() != want {
				t.Errorf("span status=%v error.type=%q, want %s", s.Status().Code, v.AsString(), want)
			}
		})
	}
}

// A stale document leaves the session alone; Bridge.Story still runs the
// checkpoint check on every other failure (checkRejected).
func TestBridgeStoryStaleDoesNotStopTheSession(t *testing.T) {
	f := &fakeIG{StoryBody: []byte(`{"errors":[{"message":"PersistedQueryNotFound","code":1}]}`)}
	b := docsTestBridge(t, f, f)
	if _, err := b.Story(t.Context(), mustIGID(t, fakeig.StoryIGID)); !errors.Is(err, domain.ErrStaleQuery) {
		t.Fatalf("err = %v, want ErrStaleQuery", err)
	}
	if s := b.Snapshot().State; s != domain.StateAuthenticated {
		t.Errorf("state = %q, want authenticated", s)
	}
}

// With no client (the supervisor rebuilding one) nothing is sent, even with
// a document issuer injected: 503, the bridge heals itself.
func TestBridgeDocumentsSendNothingWithoutAClient(t *testing.T) {
	f := &fakeIG{}
	b := docsTestBridge(t, nil, f)
	if _, err := b.Story(t.Context(), mustIGID(t, fakeig.StoryIGID)); !errors.Is(err, domain.ErrUnavailable) {
		t.Errorf("story err = %v, want ErrUnavailable", err)
	}
	if _, err := b.Items(t.Context(), mustIGID(t, fakeig.StoryIGID), domain.Username{}, ""); !errors.Is(err, domain.ErrUnavailable) {
		t.Errorf("items err = %v, want ErrUnavailable", err)
	}
	if len(f.DocRequests) != 0 {
		t.Errorf("%d requests sent with no client", len(f.DocRequests))
	}
}

// A non-200 body naming the document is stale too; one that names a
// checkpoint is a human's, whatever else it says.
func TestANon200ReadsTheBodyCheckpointFirst(t *testing.T) {
	f := &fakeIG{DocStatus: http.StatusBadRequest,
		StoryBody: []byte(`{"message":"checkpoint_required","checkpoint_url":"https://i.instagram.com/challenge/x","errors":[{"message":"doc_id"}]}`)}
	if _, _, err := graphqlDoc(t.Context(), f, storyDoc("1234"), restOrigin+"/", storyVars{}); !errors.Is(err, domain.ErrNeedsHuman) {
		t.Errorf("err = %v, want ErrNeedsHuman", err)
	}
}

func docsTestBridge(t *testing.T, cli Client, docs DocIssuer) *Bridge {
	t.Helper()
	return NewBridge(Options{
		Store:     session.Store{Dir: t.TempDir()},
		State:     domain.StateAuthenticated,
		Connected: true,
		Self:      domain.Self{FBID: "1", IGID: "79597894900", Username: fakeig.SelfUsername},
		Client:    cli,
		DocIssuer: docs,
		Docs:      QueryDocs{Story: "1234", Timeline: "5678"},
	})
}

// Meta's endpoints sometimes answer with a singular `error` object instead
// of an `errors` array. One naming the document is stale too, in a 200 and
// in a non-200.
func TestASingularErrorObjectNamingTheDocumentIsStale(t *testing.T) {
	body := []byte(`{"error":{"message":"Invalid doc_id","code":100,"error_subcode":2018001}}`)
	for _, status := range []int{0, http.StatusBadRequest} {
		f := &fakeIG{StoryBody: body, DocStatus: status}
		if _, _, err := graphqlDoc(t.Context(), f, storyDoc("1234"), restOrigin+"/", storyVars{}); !errors.Is(err, domain.ErrStaleQuery) {
			t.Errorf("status %d: err = %v, want ErrStaleQuery", status, err)
		}
	}
}

// --- the live issuer, over a real *httpclient.HTTPClient ---------------------
//
// sessionDocs is the one piece above that no fake stands in for in
// production. It runs here on the library's own HTTP client, with the
// transport replaced by a recorder, so what is asserted is the request the
// library actually builds and puts on the wire.

type docsParent struct{ jar *cookies.Cookies }

func (docsParent) GetPlatform() types.Platform { return types.Instagram }
func (docsParent) GetLogger() *zerolog.Logger  { l := zerolog.Nop(); return &l }
func (p docsParent) GetCookies() *cookies.Cookies {
	return p.jar
}
func (docsParent) GetEndpoint(name string) string { return restOrigin }
func (docsParent) IsAuthenticated() bool          { return true }

type recordingTransport struct {
	reqs   []*http.Request
	bodies []string
	answer string
}

func (r *recordingTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	body, _ := io.ReadAll(req.Body)
	r.reqs, r.bodies = append(r.reqs, req), append(r.bodies, string(body))
	h := http.Header{}
	h.Set("Content-Type", "application/json")
	h.Add("Set-Cookie", "csrftoken=rotated-synthetic; Path=/; Secure")
	return &http.Response{
		StatusCode: http.StatusOK, Header: h, Request: req,
		Body: io.NopCloser(strings.NewReader(r.answer)),
	}, nil
}

func TestSessionDocsSendsTheReadAndTakesTheCookies(t *testing.T) {
	parent := docsParent{jar: &cookies.Cookies{Platform: types.Instagram}}
	h := httpclient.NewHTTPClient(parent, httpclient.NewConfigs(parent), exhttp.ClientSettings{})
	rt := &recordingTransport{answer: fakeig.MeasuredStoryJSON}
	h.HTTP.Transport = rt

	story, err := readStory(t.Context(), sessionDocs{h: h}, "1234", mustIGID(t, fakeig.StoryIGID))
	if err != nil || !story.Found {
		t.Fatalf("story = %+v, err = %v", story, err)
	}
	if len(rt.reqs) != 1 {
		t.Fatalf("%d requests on the wire, want ONE (no retry loop)", len(rt.reqs))
	}
	req := rt.reqs[0]
	if req.Method != http.MethodPost || req.URL.String() != "https://www.instagram.com/api/graphql" {
		t.Errorf("sent %s %s, want POST https://www.instagram.com/api/graphql", req.Method, req.URL)
	}
	form, err := url.ParseQuery(rt.bodies[0])
	if err != nil {
		t.Fatal(err)
	}
	if form.Get("doc_id") != "1234" ||
		form.Get("fb_api_req_friendly_name") != "PolarisStoriesV3ReelPageStandaloneQuery" ||
		form.Get("fb_api_caller_class") != "RelayModern" ||
		form.Get("variables") != `{"reel_ids_arr":["246967358"],"__relay_internal__pv__PolarisCommunityNoteStoriesLabelEnabledrelayprovider":true}` {
		t.Errorf("form = %v", form)
	}
	if req.Header.Get("x-fb-friendly-name") != "PolarisStoriesV3ReelPageStandaloneQuery" ||
		req.Header.Get("origin") != restOrigin {
		t.Errorf("headers = %v", req.Header)
	}
	for k, vs := range form {
		for _, v := range vs {
			if l := strings.ToLower(k + "=" + v); strings.Contains(l, "seen") || strings.Contains(l, "mutation") {
				t.Errorf("form field %s=%s says seen/mutation", k, v)
			}
		}
	}
	for k, vs := range req.Header {
		for _, v := range vs {
			if l := strings.ToLower(k + ": " + v); strings.Contains(l, "seen") || strings.Contains(l, "mutation") {
				t.Errorf("header %s: %s says seen/mutation", k, v)
			}
		}
	}
	// MakeGraphQLRequest's cookie update: the answer's Set-Cookie reaches
	// the session's jar.
	if got := parent.jar.Get(cookies.IGCookieCSRFToken); got != "rotated-synthetic" {
		t.Errorf("csrftoken in the jar = %q, want the answer's", got)
	}
}
