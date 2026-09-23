package instameow

// readPost against LITERAL bytes (fakeig/posts.go), decoded exactly as
// FetchMedia decodes them. The bytes are UNMEASURED: built from the library's
// struct tags, not captured. See posts.go §Unmeasured.

import (
	"errors"
	"strings"
	"testing"

	"instagram-bridge/internal/domain"
	"instagram-bridge/internal/gateway/instameow/fakeig"

	"go.mau.fi/mautrix-meta/pkg/messagix/httpclient"
	"go.mau.fi/mautrix-meta/pkg/messagix/responses"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/codes"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/sdk/trace/tracetest"
)

func shortcode(t *testing.T, s string) domain.Shortcode {
	t.Helper()
	sc, err := domain.ParseShortcode(s)
	if err != nil {
		t.Fatal(err)
	}
	return sc
}

func withBytes(raw string) *fakeIG {
	return &fakeIG{MediaSet: true, MediaResp: fakeig.DecodeMedia(raw)}
}

func recordGatewaySpans(t *testing.T) *tracetest.SpanRecorder {
	t.Helper()
	rec := tracetest.NewSpanRecorder()
	prev := otel.GetTracerProvider()
	otel.SetTracerProvider(sdktrace.NewTracerProvider(sdktrace.WithSpanProcessor(rec)))
	t.Cleanup(func() { otel.SetTracerProvider(prev) })
	return rec
}

// The decode reaches the wire: FetchMedia is addressed with the numeric media
// id the shortcode encodes, and the shortcode rides only as the referer. The
// default fake answers ONLY the synthetic ids and 404s any other, so a wrong
// decode fails here rather than passing on a fake with no opinion.
func TestReadPostAddressesFetchMediaWithTheDecodedID(t *testing.T) {
	f := &fakeIG{}
	if _, err := readPost(t.Context(), f, shortcode(t, fakeig.CarouselShortcode)); err != nil {
		t.Fatal(err)
	}
	if len(f.MediaIDs) != 1 || f.MediaIDs[0] != fakeig.CarouselMediaID {
		t.Fatalf("FetchMedia addressed with %v, want [%s]", f.MediaIDs, fakeig.CarouselMediaID)
	}
	if f.MediaShortcodes[0] != fakeig.CarouselShortcode {
		t.Errorf("referer shortcode = %q, want %q", f.MediaShortcodes[0], fakeig.CarouselShortcode)
	}
}

func TestReadPostSingleImage(t *testing.T) {
	got, err := readPost(t.Context(), withBytes(fakeig.UnmeasuredImageJSON), shortcode(t, fakeig.ImageShortcode))
	if err != nil {
		t.Fatal(err)
	}
	if got.Author != fakeig.PostAuthor || got.Caption != fakeig.PostCaption || got.TakenAt != 1700003600 {
		t.Errorf("post = %+v, want the fixture's author, caption and taken_at", got)
	}
	if got.Shortcode != fakeig.ImageShortcode {
		t.Errorf("shortcode = %q", got.Shortcode)
	}
	if len(got.Items) != 1 {
		t.Fatalf("items = %+v, want one", got.Items)
	}
	it := got.Items[0]
	// The LARGEST candidate, which the fixture lists second.
	if it.Kind != "image" || !strings.Contains(it.URL, "synthetic-i-1440") || it.Width != 1440 || it.Height != 1440 {
		t.Errorf("item = %+v, want the 1440x1440 image", it)
	}
	if it.DurationSeconds != nil {
		t.Errorf("an image carries a duration: %v", *it.DurationSeconds)
	}
}

func TestReadPostSingleVideo(t *testing.T) {
	got, err := readPost(t.Context(), withBytes(fakeig.UnmeasuredVideoJSON), shortcode(t, fakeig.VideoShortcode))
	if err != nil {
		t.Fatal(err)
	}
	if len(got.Items) != 1 {
		t.Fatalf("items = %+v, want one", got.Items)
	}
	it := got.Items[0]
	if it.Kind != "video" || !strings.Contains(it.URL, "synthetic-v-1080.mp4") || it.Width != 1080 || it.Height != 1920 {
		t.Errorf("item = %+v, want the clip, not its cover", it)
	}
	if it.DurationSeconds == nil || *it.DurationSeconds != 30.2 {
		t.Errorf("duration = %v, want 30.2", it.DurationSeconds)
	}
}

// The reason this route exists: a carousel is EVERY item, in the author's
// order, each reduced on its own (an image, a video with its duration, an
// image), never the cover alone.
func TestReadPostCarouselIsEveryItemInOrder(t *testing.T) {
	got, err := readPost(t.Context(), withBytes(fakeig.UnmeasuredCarouselJSON), shortcode(t, fakeig.CarouselShortcode))
	if err != nil {
		t.Fatal(err)
	}
	if len(got.Items) != 3 {
		t.Fatalf("carousel read as %d item(s), want 3: %+v", len(got.Items), got.Items)
	}
	want := []struct{ kind, url string }{
		{"image", "synthetic-c1-1080.jpg"},
		{"video", "synthetic-c2-720.mp4"},
		{"image", "synthetic-c3.jpg"},
	}
	for i, w := range want {
		if got.Items[i].Kind != w.kind || !strings.Contains(got.Items[i].URL, w.url) {
			t.Errorf("item %d = %+v, want %s %s", i, got.Items[i], w.kind, w.url)
		}
	}
	if d := got.Items[1].DurationSeconds; d == nil || *d != 12.5 {
		t.Errorf("carousel video duration = %v, want 12.5", d)
	}
	if got.Author != fakeig.PostAuthor || got.Caption != fakeig.PostCaption {
		t.Errorf("post = %+v, want the parent's author and caption", got)
	}
}

// A carousel whose children did not arrive is refused, not degraded to its
// cover: one image out of many is the bug.
func TestReadPostCarouselWithoutChildrenIsRefused(t *testing.T) {
	resp := fakeig.DecodeMedia(fakeig.UnmeasuredCarouselJSON)
	resp.Items[0].CarouselMedia = nil
	resp.Items[0].ImageVersions2.Candidates = []responses.Candidates{{Width: 1, Height: 1, URL: "https://cover.invalid/x.jpg"}}
	_, err := readPost(t.Context(), &fakeIG{MediaSet: true, MediaResp: resp}, shortcode(t, fakeig.CarouselShortcode))
	if !errors.Is(err, domain.ErrUpstreamRejected) {
		t.Errorf("err = %v, want ErrUpstreamRejected", err)
	}
}

// The REST form of an error inside an HTTP 200 (`"status": "fail"`) is a
// failure, and its upstream span says so.
func TestReadPostFailureInA200IsRejectedAndMarksTheSpan(t *testing.T) {
	rec := recordGatewaySpans(t)
	_, err := readPost(t.Context(), withBytes(fakeig.UnmeasuredFailIn200JSON), shortcode(t, fakeig.CarouselShortcode))
	if !errors.Is(err, domain.ErrUpstreamRejected) {
		t.Fatalf("err = %v, want ErrUpstreamRejected", err)
	}
	spans := rec.Ended()
	if len(spans) != 1 || spans[0].Name() != "instagram.FetchMedia" {
		t.Fatalf("spans = %d, want exactly one instagram.FetchMedia", len(spans))
	}
	if spans[0].Status().Code != codes.Error {
		t.Errorf("span status = %v, want Error for a failure inside a 200", spans[0].Status())
	}
}

func TestReadPostEmptyItemsIsRejected(t *testing.T) {
	_, err := readPost(t.Context(), withBytes(`{"items":[],"num_results":0,"status":"ok"}`), shortcode(t, fakeig.CarouselShortcode))
	if !errors.Is(err, domain.ErrUpstreamRejected) || errors.Is(err, domain.ErrNotFound) {
		t.Errorf("err = %v, want ErrUpstreamRejected (an empty answer is not proof of absence)", err)
	}
}

// Somebody else's post is never the answer: a code or pk that disagrees with
// the request, or an item with neither, is refused.
func TestReadPostRefusesADifferentPost(t *testing.T) {
	for name, mutate := range map[string]func(*responses.Items){
		"other code":  func(it *responses.Items) { it.Code = "CsFaZNTtgAC" },
		"other pk":    func(it *responses.Items) { it.Pk = "3100000000000000002" },
		"no identity": func(it *responses.Items) { it.Code, it.Pk, it.ID = "", "", "" },
	} {
		t.Run(name, func(t *testing.T) {
			resp := fakeig.DecodeMedia(fakeig.UnmeasuredCarouselJSON)
			mutate(resp.Items[0])
			_, err := readPost(t.Context(), &fakeIG{MediaSet: true, MediaResp: resp}, shortcode(t, fakeig.CarouselShortcode))
			if !errors.Is(err, domain.ErrUpstreamRejected) {
				t.Errorf("err = %v, want ErrUpstreamRejected", err)
			}
		})
	}
}

// Upstream statuses: 404 is Instagram saying there is no such post; every
// other status (the 400 a story id got on this endpoint included) is a
// refusal this code cannot interpret further; a challenge needs a human.
func TestReadPostClassifiesUpstreamFailures(t *testing.T) {
	for _, tc := range []struct {
		name string
		err  error
		kind error
	}{
		{"404", fakeig.LibraryStatusError(fakeig.CarouselMediaID, 404), domain.ErrNotFound},
		{"400", fakeig.LibraryStatusError(fakeig.CarouselMediaID, 400), domain.ErrUpstreamRejected},
		{"429", httpclient.ErrRateLimited, domain.ErrUpstreamRejected},
		{"checkpoint", httpclient.ErrCheckpointRequired, domain.ErrNeedsHuman},
	} {
		t.Run(tc.name, func(t *testing.T) {
			_, err := readPost(t.Context(), &fakeIG{MediaErr: tc.err}, shortcode(t, fakeig.CarouselShortcode))
			if domain.KindOf(err) != tc.kind {
				t.Errorf("err = %v (kind %v), want %v", err, domain.KindOf(err), tc.kind)
			}
		})
	}
}

// The library's error text names the media id, which is the shortcode
// re-encoded. What call() records and what the caller receives is a fixed
// text, never that id.
func TestReadPostErrorTextCarriesNoID(t *testing.T) {
	rec := recordGatewaySpans(t)
	_, err := readPost(t.Context(), &fakeIG{MediaErr: fakeig.LibraryStatusError(fakeig.CarouselMediaID, 400)},
		shortcode(t, fakeig.CarouselShortcode))
	if err == nil {
		t.Fatal("want an error")
	}
	if strings.Contains(err.Error(), fakeig.CarouselMediaID) || strings.Contains(err.Error(), fakeig.CarouselShortcode) {
		t.Errorf("err.Error() = %q carries the post's id", err.Error())
	}
	for _, s := range rec.Ended() {
		for _, ev := range s.Events() {
			for _, kv := range ev.Attributes {
				if v := kv.Value.Emit(); strings.Contains(v, fakeig.CarouselMediaID) {
					t.Errorf("span event %q attribute %s carries the media id", ev.Name, kv.Key)
				}
			}
		}
	}
}

// A shortcode too long to decode never reaches Instagram and opens no span.
func TestReadPostUndecodableShortcodeMakesNoCall(t *testing.T) {
	rec := recordGatewaySpans(t)
	f := &fakeIG{}
	_, err := readPost(t.Context(), f, shortcode(t, "CmiJBr2LAABxyz"))
	if !errors.Is(err, domain.ErrUnsupported) {
		t.Errorf("err = %v, want ErrUnsupported", err)
	}
	if len(f.MediaIDs) != 0 || len(rec.Ended()) != 0 {
		t.Errorf("an undecodable shortcode reached Instagram: calls %v, spans %d", f.MediaIDs, len(rec.Ended()))
	}
}

// A carousel child with no url is skipped and the rest are kept.
func TestReadPostSkipsAChildWithNoURL(t *testing.T) {
	resp := fakeig.DecodeMedia(fakeig.UnmeasuredCarouselJSON)
	resp.Items[0].CarouselMedia[2].ImageVersions2.Candidates = nil
	got, err := readPost(t.Context(), &fakeIG{MediaSet: true, MediaResp: resp}, shortcode(t, fakeig.CarouselShortcode))
	if err != nil {
		t.Fatal(err)
	}
	if len(got.Items) != 2 {
		t.Errorf("items = %d, want the two with urls", len(got.Items))
	}
}
