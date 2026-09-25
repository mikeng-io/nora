package httptransport

// GET /mentions against a fake port: the 200 shape (items, next_cursor,
// skipped_story_types), the status per domain kind, and the session gate.

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

// mentionsOnlyPorts answers Mentions from its own fields and fails any other
// Social method it is asked for.
type mentionsOnlyPorts struct {
	searchOnlyPorts
	mentions []domain.CommentMention
	skipped  map[int]int
	err      error
	calls    int
}

func (f *mentionsOnlyPorts) Mentions(context.Context) ([]domain.CommentMention, map[int]int, error) {
	f.calls++
	if f.err != nil {
		return nil, nil, f.err
	}
	return f.mentions, f.skipped, nil
}

func mentionsHandler(snap domain.Session, social *mentionsOnlyPorts) http.Handler {
	messaging := failingPorts{err: errors.New("unused in these tests")}
	return New(app.New(fakeSession{snap}, messaging, social), noEvents{}, time.Now()).Routes()
}

var oneMention = domain.CommentMention{
	StoryType:      3,
	At:             1700000123,
	AuthorIGID:     "500000000001",
	AuthorUsername: "commenter_alpha",
	MediaID:        "3000000000000000001",
	MediaShortcode: "CmiJBr2LAAB",
	CommentID:      "17900000000000001",
	CommentText:    "nice shot",
}

func TestMentionsSucceedsWithTheFullShape(t *testing.T) {
	p := &mentionsOnlyPorts{mentions: []domain.CommentMention{oneMention}, skipped: map[int]int{1: 2, 4: 1}}
	h := mentionsHandler(authenticated, p)
	rec := get(h, "/mentions")
	if rec.Code != http.StatusOK {
		t.Fatalf("answered %d: %s", rec.Code, rec.Body.String())
	}
	if p.calls != 1 {
		t.Fatalf("the port was called %d time(s), want 1", p.calls)
	}

	var got mentionsBody
	if err := json.Unmarshal(rec.Body.Bytes(), &got); err != nil {
		t.Fatalf("body did not decode: %v (%s)", err, rec.Body.String())
	}
	if got.NextCursor != nil {
		t.Errorf("next_cursor = %v, want null", got.NextCursor)
	}
	if len(got.Items) != 1 {
		t.Fatalf("items = %d, want 1", len(got.Items))
	}
	item := got.Items[0]
	if item.StoryType != 3 {
		t.Errorf("story_type = %d, want 3", item.StoryType)
	}
	if item.At != "2023-11-14T22:15:23Z" {
		t.Errorf("at = %q, want the RFC3339 form of 1700000123", item.At)
	}
	if item.Author.IGID != "500000000001" || item.Author.Username != "commenter_alpha" {
		t.Errorf("author = %+v", item.Author)
	}
	if item.Media.ID != "3000000000000000001" || item.Media.Shortcode != "CmiJBr2LAAB" {
		t.Errorf("media = %+v", item.Media)
	}
	if item.Comment.ID != "17900000000000001" || item.Comment.Text != "nice shot" {
		t.Errorf("comment = %+v", item.Comment)
	}
	if got.SkippedStoryTypes["1"] != 2 || got.SkippedStoryTypes["4"] != 1 || len(got.SkippedStoryTypes) != 2 {
		t.Errorf("skipped_story_types = %+v, want {1:2, 4:1}", got.SkippedStoryTypes)
	}
}

// No `ok` field, matching every other route since P14.
func TestMentionsBodyHasNoOk(t *testing.T) {
	p := &mentionsOnlyPorts{}
	rec := get(mentionsHandler(authenticated, p), "/mentions")
	var raw map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &raw); err != nil {
		t.Fatal(err)
	}
	if _, has := raw["ok"]; has {
		t.Errorf("body carries `ok`: %v", raw)
	}
	for key := range raw {
		switch key {
		case "items", "next_cursor", "skipped_story_types":
		default:
			t.Errorf("the body carries %q; want exactly items, next_cursor, skipped_story_types", key)
		}
	}
}

// An empty answer is an empty list, not null, and an empty object for
// skipped_story_types, not null — an absent field would read as "we don't
// know" instead of "zero".
func TestMentionsEmptyAnswerIsEmptyNotNull(t *testing.T) {
	p := &mentionsOnlyPorts{}
	rec := get(mentionsHandler(authenticated, p), "/mentions")
	var raw map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &raw); err != nil {
		t.Fatal(err)
	}
	if raw["items"] == nil {
		t.Error("items is null, want an empty array")
	}
	if raw["skipped_story_types"] == nil {
		t.Error("skipped_story_types is null, want an empty object")
	}
}

func TestMentionsStatusPerDomainKind(t *testing.T) {
	cause := errors.New("what the gateway said")
	for _, tc := range []struct {
		err      error
		wantCode int
		wantKind string
	}{
		{domain.Classify(domain.ErrNeedsHuman, cause), http.StatusConflict, "needs_human"},
		{domain.Classify(domain.ErrUpstreamRejected, cause), http.StatusBadGateway, "upstream_rejected"},
		{domain.Classify(domain.ErrUnavailable, cause), http.StatusServiceUnavailable, "unavailable"},
		{cause, http.StatusInternalServerError, "internal"},
	} {
		p := &mentionsOnlyPorts{err: tc.err}
		rec := get(mentionsHandler(authenticated, p), "/mentions")
		if rec.Code != tc.wantCode {
			t.Errorf("kind %v: status = %d, want %d: %s", domain.KindOf(tc.err), rec.Code, tc.wantCode, rec.Body.String())
		}
		if env := decodeEnvelope(t, rec); env.Error.Code != tc.wantKind || env.Error.Message != "what the gateway said" {
			t.Errorf("kind %v: envelope = %+v, want {%s, %q}", domain.KindOf(tc.err), env, tc.wantKind, "what the gateway said")
		}
	}
}

// A checkpointed session answers 409 and never reaches the port.
func TestMentionsSessionGate(t *testing.T) {
	p := &mentionsOnlyPorts{mentions: []domain.CommentMention{oneMention}}
	rec := get(mentionsHandler(domain.Session{State: domain.StateCheckpointed, Detail: "captcha"}, p), "/mentions")
	if rec.Code != http.StatusConflict {
		t.Fatalf("answered %d, want 409", rec.Code)
	}
	if p.calls != 0 {
		t.Errorf("a checkpointed session reached the port %d time(s)", p.calls)
	}
	if env := decodeEnvelope(t, rec); env.Error.Code != "needs_human" {
		t.Errorf("code = %q, want needs_human", env.Error.Code)
	}
}

// The comment's text and the commenter's identity reach the 200 body — that
// is the whole point of the route — but never an error path, and the route
// is registered as GET, never as anything else.
func TestMentionsIsGETOnly(t *testing.T) {
	p := &mentionsOnlyPorts{}
	h := mentionsHandler(authenticated, p)
	for _, method := range []string{http.MethodPost, http.MethodPut, http.MethodDelete} {
		rec := do(h, method, "/mentions", "")
		if rec.Code != http.StatusMethodNotAllowed && rec.Code != http.StatusNotFound {
			t.Errorf("%s /mentions answered %d, want 404 or 405", method, rec.Code)
		}
	}
	if p.calls != 0 {
		t.Errorf("a non-GET request reached the port %d time(s)", p.calls)
	}
}

// A gateway error's text must never carry the query-string style artifacts
// this route has none of, but IS still checked the way every other
// content-bearing route is: the envelope forwards the gateway's message
// verbatim (failEnvelope's job), so this test pins that the fixed text the
// gateway is required to produce reaches the caller unmodified.
func TestMentionsForwardsTheGatewaysFixedMessage(t *testing.T) {
	p := &mentionsOnlyPorts{err: domain.Classify(domain.ErrUpstreamRejected,
		errors.New("news_inbox returned a non-200: 429"))}
	rec := get(mentionsHandler(authenticated, p), "/mentions")
	env := decodeEnvelope(t, rec)
	if strings.Contains(env.Error.Message, "@") {
		t.Errorf("message = %q looks like it carries a mention token", env.Error.Message)
	}
	if env.Error.Message != "news_inbox returned a non-200: 429" {
		t.Errorf("message = %q, want the gateway's fixed text unchanged", env.Error.Message)
	}
}
