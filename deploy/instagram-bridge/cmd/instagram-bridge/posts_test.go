package main

// GET /posts/{shortcode}, wired end to end over the fake, whose FetchMedia
// answers the synthetic posts by MEDIA ID and 404s any other id. So these
// tests go through the real shortcode decode: a wrong decode is a 404 here.
// The response bytes are UNMEASURED (fakeig/posts.go).

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"instagram-bridge/internal/gateway/instameow/fakeig"

	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/sdk/trace/tracetest"
)

func getPost(t *testing.T, b *testBridge, shortcode string) *httptest.ResponseRecorder {
	t.Helper()
	rec := httptest.NewRecorder()
	b.routes().ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/posts/"+shortcode, nil))
	return rec
}

func decodePost(t *testing.T, rec *httptest.ResponseRecorder) postBody {
	t.Helper()
	var out postBody
	if err := json.Unmarshal(rec.Body.Bytes(), &out); err != nil {
		t.Fatalf("body did not decode: %v (%s)", err, rec.Body.String())
	}
	return out
}

func decodeErrorEnvelope(t *testing.T, rec *httptest.ResponseRecorder) errorEnvelope {
	t.Helper()
	var env errorEnvelope
	if err := json.Unmarshal(rec.Body.Bytes(), &env); err != nil {
		t.Fatalf("body is not an error envelope: %v (%s)", err, rec.Body.String())
	}
	return env
}

// The carousel, through the whole path: three items, in order, the video
// with its duration, and the shortcode decoded to the id the fake knows.
func TestPostCarouselEndToEnd(t *testing.T) {
	f := &fakeIG{}
	b := newTestBridge(t, f)
	rec := getPost(t, b, fakeig.CarouselShortcode)
	if rec.Code != http.StatusOK {
		t.Fatalf("answered %d: %s", rec.Code, rec.Body.String())
	}
	if len(f.MediaIDs) != 1 || f.MediaIDs[0] != fakeig.CarouselMediaID {
		t.Errorf("FetchMedia addressed with %v, want [%s]", f.MediaIDs, fakeig.CarouselMediaID)
	}
	if strings.Contains(rec.Body.String(), `"ok"`) {
		t.Errorf("the body carries an ok field: %s", rec.Body.String())
	}
	for _, id := range []string{fakeig.CarouselMediaID, "3000000000000000011", "10000001"} {
		if strings.Contains(rec.Body.String(), id) {
			t.Errorf("the body carries id %s: %s", id, rec.Body.String())
		}
	}
	got := decodePost(t, rec)
	if got.Shortcode != fakeig.CarouselShortcode || got.Author != fakeig.PostAuthor || got.Caption != fakeig.PostCaption {
		t.Errorf("post = %+v", got)
	}
	if len(got.Items) != 3 {
		t.Fatalf("items = %d, want 3", len(got.Items))
	}
	for i, want := range []string{"image", "video", "image"} {
		if got.Items[i].Kind != want {
			t.Errorf("item %d kind = %s, want %s", i, got.Items[i].Kind, want)
		}
	}
	if d := got.Items[1].DurationSeconds; d == nil || *d != 12.5 {
		t.Errorf("video duration = %v, want 12.5", d)
	}
}

func TestPostSingleImageAndVideoEndToEnd(t *testing.T) {
	b := newTestBridge(t, &fakeIG{})
	img := decodePost(t, getPost(t, b, fakeig.ImageShortcode))
	if len(img.Items) != 1 || img.Items[0].Kind != "image" || img.Items[0].Width != 1440 {
		t.Errorf("image post = %+v", img)
	}
	vid := decodePost(t, getPost(t, b, fakeig.VideoShortcode))
	if len(vid.Items) != 1 || vid.Items[0].Kind != "video" || vid.Items[0].DurationSeconds == nil {
		t.Errorf("video post = %+v", vid)
	}
}

// A shortcode that decodes to an id Instagram (here, the fake) 404s is 404.
func TestPostUnknownShortcodeIs404(t *testing.T) {
	b := newTestBridge(t, &fakeIG{})
	rec := getPost(t, b, "C3L9IQOygAE")
	if rec.Code != http.StatusNotFound {
		t.Fatalf("answered %d, want 404: %s", rec.Code, rec.Body.String())
	}
	if env := decodeErrorEnvelope(t, rec); env.Error.Code != "not_found" {
		t.Errorf("code = %q, want not_found", env.Error.Code)
	}
}

func TestPostMalformedShortcodeIs400AndMakesNoCall(t *testing.T) {
	f := &fakeIG{}
	b := newTestBridge(t, f)
	rec := getPost(t, b, "abc.def")
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("answered %d, want 400: %s", rec.Code, rec.Body.String())
	}
	if len(f.MediaIDs) != 0 {
		t.Errorf("a malformed shortcode reached FetchMedia: %v", f.MediaIDs)
	}
}

// A failure inside an HTTP 200 answers 502, end to end.
func TestPostFailureInA200Is502(t *testing.T) {
	b := newTestBridge(t, &fakeIG{MediaSet: true, MediaResp: fakeig.DecodeMedia(fakeig.UnmeasuredFailIn200JSON)})
	rec := getPost(t, b, fakeig.CarouselShortcode)
	if rec.Code != http.StatusBadGateway {
		t.Fatalf("answered %d, want 502: %s", rec.Code, rec.Body.String())
	}
	if env := decodeErrorEnvelope(t, rec); env.Error.Code != "upstream_rejected" {
		t.Errorf("code = %q, want upstream_rejected", env.Error.Code)
	}
}

// The server span is named by pattern, and there is exactly one upstream
// span under it.
func TestPostSpanIsNamedByPatternWithOneUpstreamCall(t *testing.T) {
	rec := recordSpans(t)
	b := newTestBridge(t, &fakeIG{})
	if w := getPost(t, b, fakeig.CarouselShortcode); w.Code != http.StatusOK {
		t.Fatalf("answered %d", w.Code)
	}
	s := spanNamed(t, rec, "GET /posts/{shortcode}")
	if v, ok := attrOf(s, "http.route"); !ok || v.AsString() != "/posts/{shortcode}" {
		t.Errorf("http.route = %q (present=%v)", v.AsString(), ok)
	}
	if v, ok := attrOf(s, "url.path"); ok && v.AsString() != "/posts/{shortcode}" {
		t.Errorf("url.path = %q, want the pattern", v.AsString())
	}
	var upstream []string
	for _, sp := range rec.Ended() {
		if strings.HasPrefix(sp.Name(), "instagram.") {
			upstream = append(upstream, sp.Name())
			if sp.Parent().SpanID() != s.SpanContext().SpanID() {
				t.Errorf("upstream span %q is not a child of the request", sp.Name())
			}
		}
	}
	if len(upstream) != 1 || upstream[0] != "instagram.FetchMedia" {
		t.Errorf("upstream spans = %v, want exactly [instagram.FetchMedia]", upstream)
	}
}

// secretsIn reports every span surface a value reached: attribute values,
// event names and attributes (RecordError writes err.Error() there), the
// name and the status. The span NAME is allowed to hold the route pattern,
// which contains no value.
func secretsIn(spans []sdktrace.ReadOnlySpan, secrets ...string) []string {
	var hits []string
	for _, s := range spans {
		check := func(where, v string) {
			for _, sec := range secrets {
				if strings.Contains(v, sec) {
					hits = append(hits, s.Name()+" "+where+" carries "+sec)
				}
			}
		}
		for _, kv := range s.Attributes() {
			check("attribute "+string(kv.Key), kv.Value.Emit())
		}
		for _, ev := range s.Events() {
			check("event name", ev.Name)
			for _, kv := range ev.Attributes {
				check("event "+ev.Name+" attribute "+string(kv.Key), kv.Value.Emit())
			}
		}
		check("name", s.Name())
		check("status", s.Status().Description)
	}
	return hits
}

func endedSpans(rec *tracetest.SpanRecorder) []sdktrace.ReadOnlySpan { return rec.Ended() }

// The negative: the CAPTION reaches the 200 body and nothing else, and the
// SHORTCODE and the media id it decodes to never become a span value.
func TestPostCaptionAndShortcodeNeverEnterASpanOrLog(t *testing.T) {
	rec := recordSpans(t)
	logs := captureLogs(t)
	b := newTestBridge(t, &fakeIG{})
	w := getPost(t, b, fakeig.CarouselShortcode)
	if w.Code != http.StatusOK || !strings.Contains(w.Body.String(), fakeig.PostCaption) {
		t.Fatalf("answered %d without the caption: %s", w.Code, w.Body.String())
	}
	if len(rec.Ended()) < 2 {
		t.Fatalf("expected a server span and an upstream span, got %d", len(rec.Ended()))
	}
	for _, hit := range secretsIn(endedSpans(rec), fakeig.PostCaption, fakeig.CarouselShortcode, fakeig.CarouselMediaID) {
		t.Error(hit)
	}
	for _, sec := range []string{fakeig.PostCaption, fakeig.CarouselShortcode} {
		if strings.Contains(logs.String(), sec) {
			t.Errorf("a log line carries %q: %s", sec, logs.String())
		}
	}
}

// The failure half, where a leak is likelier: the refused answer below
// CARRIES the caption (UnmeasuredFailIn200JSON), and the library's own error
// text for a status failure carries the media id. Neither may reach a span,
// a log line or the envelope.
func TestPostNothingLeaksOnFailure(t *testing.T) {
	for name, f := range map[string]*fakeIG{
		"fail in 200": {MediaSet: true, MediaResp: fakeig.DecodeMedia(fakeig.UnmeasuredFailIn200JSON)},
		"status 400":  {MediaErr: fakeig.LibraryStatusError(fakeig.CarouselMediaID, 400)},
		"status 404":  {MediaErr: fakeig.LibraryStatusError(fakeig.CarouselMediaID, 404)},
	} {
		t.Run(name, func(t *testing.T) {
			rec := recordSpans(t)
			logs := captureLogs(t)
			b := newTestBridge(t, f)
			w := getPost(t, b, fakeig.CarouselShortcode)
			if w.Code == http.StatusOK {
				t.Fatalf("a refused read answered 200: %s", w.Body.String())
			}
			for _, hit := range secretsIn(endedSpans(rec), fakeig.PostCaption, fakeig.CarouselShortcode, fakeig.CarouselMediaID) {
				t.Error(hit)
			}
			for _, sec := range []string{fakeig.PostCaption, fakeig.CarouselShortcode, fakeig.CarouselMediaID} {
				if strings.Contains(logs.String(), sec) {
					t.Errorf("a log line carries %q", sec)
				}
				if strings.Contains(w.Body.String(), sec) {
					t.Errorf("the envelope carries %q: %s", sec, w.Body.String())
				}
			}
		})
	}
}
