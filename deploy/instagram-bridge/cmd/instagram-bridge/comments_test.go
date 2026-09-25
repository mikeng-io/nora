package main

// The summons write end to end: GET /mentions serves a mention and records it,
// POST /posts/{shortcode}/comments replies to it once, over the wired bridge,
// its real ledger in the session directory, and a raw-REST fake that records
// every request (fakeig.REST). Nothing here touches the network.

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"instagram-bridge/internal/app"
	"instagram-bridge/internal/domain"
	gateway "instagram-bridge/internal/gateway/instameow"
	"instagram-bridge/internal/gateway/instameow/fakeig"
	"instagram-bridge/internal/session"

	"go.mau.fi/mautrix-meta/pkg/messagix/httpclient"
)

func withREST(r gateway.RESTIssuer) bridgeOpt { return func(o *gateway.Options) { o.RESTIssuer = r } }

func withStoreDir(dir string) bridgeOpt {
	return func(o *gateway.Options) { o.Store = session.Store{Dir: dir} }
}

func writesOn() app.CommentPolicy {
	return app.CommentPolicy{WritesEnabled: true, DailyCap: 10, MinInterval: 0}
}

func commentsPath(code string) string { return "/posts/" + code + "/comments" }

func replyBody(to, text string) map[string]any {
	return map[string]any{"text": text, "reply_to_comment_id": to}
}

// The whole flow: the mention GET /mentions serves is the one comment the
// bridge may answer, it answers it once (201), and a second attempt is 409
// with no second request to Instagram.
func TestASummonsIsAnsweredOnceEndToEnd(t *testing.T) {
	rest := fakeig.NewREST()
	b := newTestBridgeWithComments(t, &fakeIG{}, writesOn(), withREST(rest))

	if w := request(t, b, http.MethodGet, "/mentions", nil); w.Code != http.StatusOK {
		t.Fatalf("/mentions answered %d: %s", w.Code, w.Body.String())
	}
	w := request(t, b, http.MethodPost, commentsPath(fakeig.CarouselShortcode), replyBody(fakeig.MentionCommentID, "  no, it isn't.  "))
	if w.Code != http.StatusCreated {
		t.Fatalf("reply answered %d: %s", w.Code, w.Body.String())
	}
	var got map[string]string
	_ = json.Unmarshal(w.Body.Bytes(), &got)
	if got["id"] != fakeig.NewCommentID || got["reply_to_comment_id"] != fakeig.MentionCommentID {
		t.Errorf("201 body = %s", w.Body.String())
	}

	var post fakeig.RESTCall
	for _, c := range rest.Calls() {
		if c.Method == http.MethodPost {
			post = c
		}
	}
	if post.URL != "https://www.instagram.com/api/v1/web/comments/"+fakeig.CarouselMediaID+"/add/" || !post.Once {
		t.Errorf("write went to %q (once=%v)", post.URL, post.Once)
	}
	form, _ := url.ParseQuery(string(post.Payload))
	if form.Get("comment_text") != "no, it isn't." || form.Get("replied_to_comment_id") != fakeig.MentionCommentID {
		t.Errorf("form = %v", form)
	}
	if post.ContentType != "application/x-www-form-urlencoded" {
		t.Errorf("content type = %q", post.ContentType)
	}

	w = request(t, b, http.MethodPost, commentsPath(fakeig.CarouselShortcode), replyBody(fakeig.MentionCommentID, "again"))
	if w.Code != http.StatusConflict || decodeEnvelope(t, w).Error.Code != "already_replied" {
		t.Fatalf("second reply answered %d: %s", w.Code, w.Body.String())
	}
	if n := rest.Writes(); n != 1 {
		t.Errorf("%d writes reached Instagram, want 1", n)
	}
}

// The served-mention requirement end to end: an id never served, a served id
// on another post, and a reply with writes off all refuse with zero writes.
func TestOnlyAServedSummonsOnItsOwnPostCanBeAnswered(t *testing.T) {
	for name, tc := range map[string]struct {
		policy app.CommentPolicy
		code   string
		to     string
		status int
		want   string
	}{
		"writes off (the default)": {app.DefaultCommentPolicy(), fakeig.CarouselShortcode, fakeig.MentionCommentID, 403, "writes_disabled"},
		"never served":             {writesOn(), fakeig.CarouselShortcode, "17900000000000999", 403, "not_a_summons"},
		"another post":             {writesOn(), fakeig.ImageShortcode, fakeig.MentionCommentID, 403, "not_a_summons"},
		"top-level (no target)":    {writesOn(), fakeig.CarouselShortcode, "", 403, "not_a_summons"},
	} {
		t.Run(name, func(t *testing.T) {
			rest := fakeig.NewREST()
			b := newTestBridgeWithComments(t, &fakeIG{}, tc.policy, withREST(rest))
			request(t, b, http.MethodGet, "/mentions", nil)
			w := request(t, b, http.MethodPost, commentsPath(tc.code), replyBody(tc.to, "hi"))
			if w.Code != tc.status || decodeEnvelope(t, w).Error.Code != tc.want {
				t.Fatalf("answered %d %s, want %d %s", w.Code, w.Body.String(), tc.status, tc.want)
			}
			if n := rest.Writes(); n != 0 {
				t.Errorf("%d writes reached Instagram, want 0", n)
			}
		})
	}
}

// A restart is a fresh bridge over the same session directory: the answered
// summons stays answered.
func TestAnsweredStaysAnsweredAcrossARestart(t *testing.T) {
	dir := t.TempDir()
	rest := fakeig.NewREST()
	b := newTestBridgeWithComments(t, &fakeIG{}, writesOn(), withREST(rest), withStoreDir(dir))
	request(t, b, http.MethodGet, "/mentions", nil)
	if w := request(t, b, http.MethodPost, commentsPath(fakeig.CarouselShortcode), replyBody(fakeig.MentionCommentID, "hi")); w.Code != http.StatusCreated {
		t.Fatalf("answered %d: %s", w.Code, w.Body.String())
	}

	rest2 := fakeig.NewREST()
	b2 := newTestBridgeWithComments(t, &fakeIG{}, writesOn(), withREST(rest2), withStoreDir(dir))
	request(t, b2, http.MethodGet, "/mentions", nil)
	w := request(t, b2, http.MethodPost, commentsPath(fakeig.CarouselShortcode), replyBody(fakeig.MentionCommentID, "hi"))
	if w.Code != http.StatusConflict || decodeEnvelope(t, w).Error.Code != "already_replied" {
		t.Fatalf("after a restart answered %d: %s", w.Code, w.Body.String())
	}
	if rest2.Writes() != 0 {
		t.Errorf("the restarted bridge wrote %d time(s), want 0", rest2.Writes())
	}
}

// A checkpoint answered to the write flips the session (409 on the write,
// `checkpointed` on /health), and every later write is refused at the session
// gate with no request.
func TestACheckpointOnTheWriteStopsTheSession(t *testing.T) {
	rest := fakeig.NewREST()
	rest.Answer("POST https://www.instagram.com/api/v1/web/comments/", fakeig.RESTAnswer{
		Status: 400, Body: `{"message":"checkpoint_required","checkpoint_url":"https://i.instagram.com/challenge/x","status":"fail"}`,
	})
	b := newTestBridgeWithComments(t, &fakeIG{}, writesOn(), withREST(rest))
	request(t, b, http.MethodGet, "/mentions", nil)
	w := request(t, b, http.MethodPost, commentsPath(fakeig.CarouselShortcode), replyBody(fakeig.MentionCommentID, "hi"))
	if w.Code != http.StatusConflict || decodeEnvelope(t, w).Error.Code != "needs_human" {
		t.Fatalf("answered %d: %s", w.Code, w.Body.String())
	}
	h := request(t, b, http.MethodGet, "/health", nil)
	var health map[string]any
	_ = json.Unmarshal(h.Body.Bytes(), &health)
	if health["session"] != string(domain.StateCheckpointed) {
		t.Errorf("/health session = %v, want checkpointed", health["session"])
	}
	before := rest.Writes()
	w = request(t, b, http.MethodPost, commentsPath(fakeig.CarouselShortcode), replyBody(fakeig.MentionCommentID, "hi"))
	if w.Code != http.StatusConflict || rest.Writes() != before {
		t.Errorf("after the checkpoint: %d, %d more write(s)", w.Code, rest.Writes()-before)
	}
}

// Instagram's action block on the write, end to end: 403 writes_blocked, the
// latch on disk, and every later write (any summons, after a restart)
// refused with no request.
func TestAnActionBlockLatchesWritesOffEndToEnd(t *testing.T) {
	dir := t.TempDir()
	rest := fakeig.NewREST()
	rest.Answer("POST https://www.instagram.com/api/v1/web/comments/", fakeig.RESTAnswer{
		Status: 400, Body: `{"message":"feedback_required","spam":true,"feedback_title":"Action Blocked","status":"fail"}`,
	})
	b := newTestBridgeWithComments(t, &fakeIG{}, writesOn(), withREST(rest), withStoreDir(dir))
	request(t, b, http.MethodGet, "/mentions", nil)
	w := request(t, b, http.MethodPost, commentsPath(fakeig.CarouselShortcode), replyBody(fakeig.MentionCommentID, "hi"))
	if w.Code != http.StatusForbidden || decodeEnvelope(t, w).Error.Code != "writes_blocked" {
		t.Fatalf("answered %d: %s", w.Code, w.Body.String())
	}
	blob, _ := os.ReadFile(filepath.Join(dir, "comments.json"))
	if !strings.Contains(string(blob), `"writes_blocked"`) || !strings.Contains(string(blob), `"feedback_required"`) {
		t.Fatalf("the latch is not on disk: %s", blob)
	}
	b2 := newTestBridgeWithComments(t, &fakeIG{}, writesOn(), withREST(rest), withStoreDir(dir))
	w = request(t, b2, http.MethodPost, commentsPath(fakeig.CarouselShortcode), replyBody("17900000000000999", "hi"))
	if w.Code != http.StatusForbidden || decodeEnvelope(t, w).Error.Code != "writes_blocked" {
		t.Errorf("after a restart answered %d: %s", w.Code, w.Body.String())
	}
	if rest.Writes() != 1 {
		t.Errorf("%d writes reached Instagram, want 1", rest.Writes())
	}
}

// The read, end to end: both shapes, and reply_to validated at the edge.
func TestCommentsReadEndToEnd(t *testing.T) {
	rest := fakeig.NewREST()
	b := newTestBridge(t, &fakeIG{}, withREST(rest))
	w := request(t, b, http.MethodGet, commentsPath(fakeig.CarouselShortcode), nil)
	if w.Code != http.StatusOK {
		t.Fatalf("answered %d: %s", w.Code, w.Body.String())
	}
	var page struct {
		Items []struct {
			ID     string `json:"id"`
			Author struct {
				IGID string `json:"igid"`
			} `json:"author"`
		} `json:"items"`
		NextCursor   *string `json:"next_cursor"`
		SkippedCount int     `json:"skipped_count"`
	}
	_ = json.Unmarshal(w.Body.Bytes(), &page)
	if len(page.Items) != 2 || page.Items[0].ID != fakeig.CommentTopID || page.NextCursor != nil || page.SkippedCount != 2 {
		t.Errorf("page = %s", w.Body.String())
	}
	w = request(t, b, http.MethodGet, commentsPath(fakeig.CarouselShortcode)+"?reply_to="+fakeig.CommentTopID, nil)
	if w.Code != http.StatusOK || !strings.Contains(w.Body.String(), `"parent_id":"`+fakeig.CommentTopID+`"`) {
		t.Errorf("replies answered %d: %s", w.Code, w.Body.String())
	}
	before := len(rest.Calls())
	if w := request(t, b, http.MethodGet, commentsPath(fakeig.CarouselShortcode)+"?reply_to=x1", nil); w.Code != http.StatusBadRequest {
		t.Errorf("a malformed reply_to answered %d", w.Code)
	}
	if len(rest.Calls()) != before {
		t.Error("a malformed reply_to reached Instagram")
	}
}

// The leak test, extended to the comment routes: no igid, username, media
// id, shortcode, comment id, comment text or reply text in any span (name,
// attribute, event, status) or log line, on the success paths and the
// failure paths — including a transport failure whose raw text quotes the
// URL, ids and all. Error envelopes are checked too; 200/201 bodies carry
// these values by design and are not.
func TestCommentRoutesNeverLeakIntoTelemetry(t *testing.T) {
	rec := recordSpans(t)
	logs := captureLogs(t)
	const replyText = "the-reply-nora-wrote-7d21"
	secrets := []string{
		fakeig.CarouselMediaID, fakeig.CarouselShortcode, fakeig.MentionCommentID,
		fakeig.MentionCommentAuthorIGID, fakeig.MentionCommentAuthorUsername, fakeig.MentionNotificationText,
		fakeig.CommentTopID, fakeig.CommentAuthorIGID, fakeig.CommentAuthorName, fakeig.CommentText,
		fakeig.NewCommentID, replyText,
	}
	var envelopes strings.Builder
	expect := func(w *httptest.ResponseRecorder, status int) {
		t.Helper()
		if w.Code != status {
			t.Fatalf("answered %d, want %d: %s", w.Code, status, w.Body.String())
		}
		if status >= 400 {
			envelopes.WriteString(w.Body.String())
		}
	}

	rest := fakeig.NewREST()
	b := withTraceparent(newTestBridgeWithComments(t, &fakeIG{}, writesOn(), withREST(rest)))
	expect(request(t, b, http.MethodGet, "/mentions", nil), 200)
	expect(request(t, b, http.MethodGet, commentsPath(fakeig.CarouselShortcode), nil), 200)
	expect(request(t, b, http.MethodGet, commentsPath(fakeig.CarouselShortcode)+"?reply_to="+fakeig.CommentTopID, nil), 200)
	expect(request(t, b, http.MethodPost, commentsPath(fakeig.CarouselShortcode), replyBody(fakeig.MentionCommentID, replyText)), 201)
	expect(request(t, b, http.MethodPost, commentsPath(fakeig.CarouselShortcode), replyBody(fakeig.MentionCommentID, replyText)), 409)
	expect(request(t, b, http.MethodPost, commentsPath(fakeig.CarouselShortcode), replyBody(fakeig.CommentTopID, replyText)), 403)

	// Failure paths: a transport error quoting the URL (the write and the
	// read), and a refusal Instagram answers.
	urlErr := fmt.Errorf("%w: %w", httpclient.ErrRequestFailed, &url.Error{
		Op: "Post", URL: "https://www.instagram.com/api/v1/web/comments/" + fakeig.CarouselMediaID + "/add/?c=" + fakeig.MentionCommentID,
		Err: context.DeadlineExceeded,
	})
	for _, answer := range []fakeig.RESTAnswer{{Err: urlErr}, {Status: 400, Body: `{"message":"feedback_required","status":"fail"}`}} {
		rest := fakeig.NewREST()
		rest.Answer("POST https://www.instagram.com/api/v1/web/comments/", answer)
		rest.Answer("GET https://www.instagram.com/api/v1/media/", answer)
		b := withTraceparent(newTestBridgeWithComments(t, &fakeIG{}, writesOn(), withREST(rest)))
		expect(request(t, b, http.MethodGet, "/mentions", nil), 200)
		w := request(t, b, http.MethodPost, commentsPath(fakeig.CarouselShortcode), replyBody(fakeig.MentionCommentID, replyText))
		if w.Code < 400 {
			t.Fatalf("a failing write answered %d", w.Code)
		}
		envelopes.WriteString(w.Body.String())
		w = request(t, b, http.MethodGet, commentsPath(fakeig.CarouselShortcode), nil)
		if w.Code < 400 {
			t.Fatalf("a failing read answered %d", w.Code)
		}
		envelopes.WriteString(w.Body.String())
	}

	if len(rec.Ended()) < 12 {
		t.Fatalf("only %d spans recorded; the routes were not traced", len(rec.Ended()))
	}
	for _, secret := range secrets {
		assertNothingCarries(t, rec, logs, envelopes.String(), secret, "identifier or text "+secret)
	}
	// The server spans name the pattern, never the path.
	for _, s := range rec.Ended() {
		if !strings.HasPrefix(s.Name(), "GET /posts/") && !strings.HasPrefix(s.Name(), "POST /posts/") {
			continue
		}
		if v, _ := attrOf(s, "url.path"); v.AsString() != "/posts/{shortcode}/comments" {
			t.Errorf("span %q url.path = %q, want the pattern", s.Name(), v.AsString())
		}
	}
}
