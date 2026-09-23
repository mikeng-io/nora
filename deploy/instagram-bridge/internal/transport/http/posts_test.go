package httptransport

// GET /posts/{shortcode} against a fake port: the edge parse, the status per
// domain kind, and the 200 shape (no ok, no ids, items in the port's order).

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"strings"
	"testing"
	"time"

	"instagram-bridge/internal/app"
	"instagram-bridge/internal/domain"
)

// postOnlyPorts answers Post from its fields and records what reached it.
type postOnlyPorts struct {
	searchOnlyPorts
	post  domain.Post
	err   error
	calls *[]string
}

func (f postOnlyPorts) Post(_ context.Context, code domain.Shortcode) (domain.Post, error) {
	if f.calls != nil {
		*f.calls = append(*f.calls, code.String())
	}
	if f.err != nil {
		return domain.Post{}, f.err
	}
	return f.post, nil
}

func postHandler(snap domain.Session, social postOnlyPorts) http.Handler {
	messaging := failingPorts{err: errors.New("unused in these tests")}
	return New(app.New(fakeSession{snap}, messaging, social), noEvents{}, time.Now()).Routes()
}

func seconds(f float64) *float64 { return &f }

// A carousel of three, mixed, as the port hands it over.
var threeItems = domain.Post{
	Shortcode: "CmiJBr2LAAB",
	Author:    "example_author",
	Caption:   "a caption",
	TakenAt:   1700000000,
	Items: []domain.PostItem{
		{Kind: "image", URL: "https://cdn.invalid/1.jpg", Width: 1080, Height: 1350},
		{Kind: "video", URL: "https://cdn.invalid/2.mp4", Width: 720, Height: 1280, DurationSeconds: seconds(12.5)},
		{Kind: "image", URL: "https://cdn.invalid/3.jpg", Width: 1080, Height: 1080},
	},
}

func TestPostSucceedsWithEveryItemInOrder(t *testing.T) {
	var calls []string
	h := postHandler(authenticated, postOnlyPorts{post: threeItems, calls: &calls})
	rec := get(h, "/posts/CmiJBr2LAAB")
	if rec.Code != http.StatusOK {
		t.Fatalf("answered %d: %s", rec.Code, rec.Body.String())
	}
	if len(calls) != 1 || calls[0] != "CmiJBr2LAAB" {
		t.Errorf("the port was addressed with %v", calls)
	}
	var got postBody
	if err := json.Unmarshal(rec.Body.Bytes(), &got); err != nil {
		t.Fatal(err)
	}
	if got.Shortcode != "CmiJBr2LAAB" || got.Author != "example_author" || got.Caption != "a caption" || got.TakenAt != 1700000000 {
		t.Errorf("post = %+v", got)
	}
	if len(got.Items) != 3 {
		t.Fatalf("items = %d, want 3", len(got.Items))
	}
	for i, want := range []string{"1.jpg", "2.mp4", "3.jpg"} {
		if !strings.HasSuffix(got.Items[i].URL, want) {
			t.Errorf("item %d = %s, want %s (the port's order)", i, got.Items[i].URL, want)
		}
	}
	if got.Items[1].Kind != "video" || got.Items[1].DurationSeconds == nil || *got.Items[1].DurationSeconds != 12.5 {
		t.Errorf("video item = %+v", got.Items[1])
	}
}

// The new conventions: no ok field, no ids, and no duration on an image.
func TestPostBodyHasNoOkNoIDsAndNoImageDuration(t *testing.T) {
	h := postHandler(authenticated, postOnlyPorts{post: threeItems})
	rec := get(h, "/posts/CmiJBr2LAAB")
	var raw map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &raw); err != nil {
		t.Fatal(err)
	}
	for key := range raw {
		switch key {
		case "shortcode", "author", "caption", "taken_at", "items":
		default:
			t.Errorf("the body carries %q; want exactly shortcode, author, caption, taken_at, items", key)
		}
	}
	items := raw["items"].([]any)
	if _, ok := items[0].(map[string]any)["duration_seconds"]; ok {
		t.Error("an image item carries duration_seconds")
	}
	if _, ok := items[1].(map[string]any)["duration_seconds"]; !ok {
		t.Error("the video item lost duration_seconds")
	}
}

// A malformed shortcode is 400 at the edge, before the session gate and
// before any port, whatever the session's state.
func TestPostMalformedShortcodeIs400BeforeTheSessionGate(t *testing.T) {
	for _, snap := range []domain.Session{authenticated, {State: domain.StateCheckpointed, Detail: "captcha"}} {
		var calls []string
		h := postHandler(snap, postOnlyPorts{post: threeItems, calls: &calls})
		for _, path := range []string{"/posts/abc.def", "/posts/abc%20def", "/posts/abc+def",
			"/posts/" + strings.Repeat("a", 65), "/posts/" + strings.Repeat("a", 20) + "."} {
			rec := get(h, path)
			if rec.Code != http.StatusBadRequest {
				t.Errorf("%s (%s): answered %d, want 400: %s", path, snap.State, rec.Code, rec.Body.String())
				continue
			}
			env := decodeEnvelope(t, rec)
			if env.Error.Code != "invalid_id" {
				t.Errorf("%s: code = %q, want invalid_id", path, env.Error.Code)
			}
		}
		if len(calls) != 0 {
			t.Errorf("a malformed shortcode reached the port: %v", calls)
		}
	}
}

func TestPostStatusPerDomainKind(t *testing.T) {
	cause := errors.New("what the gateway said")
	for _, tc := range []struct {
		err      error
		wantCode int
		wantKind string
	}{
		{domain.Classify(domain.ErrNotFound, cause), http.StatusNotFound, "not_found"},
		{domain.Classify(domain.ErrNeedsHuman, cause), http.StatusConflict, "needs_human"},
		{domain.ErrShortcodeNotDecodable, http.StatusNotImplemented, "unsupported"},
		{domain.Classify(domain.ErrUpstreamRejected, cause), http.StatusBadGateway, "upstream_rejected"},
		{domain.Classify(domain.ErrUnavailable, cause), http.StatusServiceUnavailable, "unavailable"},
		{cause, http.StatusInternalServerError, "internal"},
	} {
		h := postHandler(authenticated, postOnlyPorts{err: tc.err})
		rec := get(h, "/posts/CmiJBr2LAAB")
		if rec.Code != tc.wantCode {
			t.Errorf("kind %v: status = %d, want %d", domain.KindOf(tc.err), rec.Code, tc.wantCode)
		}
		if env := decodeEnvelope(t, rec); env.Error.Code != tc.wantKind {
			t.Errorf("kind %v: code = %q, want %q", domain.KindOf(tc.err), env.Error.Code, tc.wantKind)
		}
	}
}

// A well-formed shortcode on a checkpointed session is the session's 409,
// and nothing reaches the port.
func TestPostSessionGate(t *testing.T) {
	var calls []string
	h := postHandler(domain.Session{State: domain.StateCheckpointed, Detail: "captcha"},
		postOnlyPorts{post: threeItems, calls: &calls})
	rec := get(h, "/posts/CmiJBr2LAAB")
	if rec.Code != http.StatusConflict {
		t.Fatalf("answered %d, want 409", rec.Code)
	}
	if len(calls) != 0 {
		t.Errorf("a checkpointed session reached the port: %v", calls)
	}
}
