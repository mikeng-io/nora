package httptransport

// GET and POST /posts/{shortcode}/comments against fake ports: the shapes,
// the edge parsing, and one status and code per guard.

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"instagram-bridge/internal/app"
	"instagram-bridge/internal/domain"
)

const (
	testCode    = "CmiJBr2LAAB"
	testSummons = "17900000000000001"
)

// fakeCommentsPort answers reads with page/err and writes with postErr.
type fakeCommentsPort struct {
	page    domain.CommentPage
	err     error
	postErr error
	parent  *domain.CommentID
	reads   int
	posts   int
}

func (f *fakeCommentsPort) Comments(_ context.Context, _ domain.Shortcode, _ domain.MediaID, parent *domain.CommentID) (domain.CommentPage, error) {
	f.reads++
	f.parent = parent
	return f.page, f.err
}

func (f *fakeCommentsPort) PostComment(_ context.Context, _ domain.Shortcode, _ domain.MediaID, to domain.CommentID, _ string) (domain.PostedComment, error) {
	f.posts++
	if f.postErr != nil {
		return domain.PostedComment{}, f.postErr
	}
	return domain.PostedComment{ID: "18000000000000009", ReplyToCommentID: to.String()}, nil
}

// memLedger is an in-memory ledger. refuse, when set, is every
// transaction's answer (standing in for a ledger that cannot be read).
type memLedger struct {
	led    *domain.CommentLedger
	refuse error
}

func (m *memLedger) Update(now time.Time, fn func(*domain.CommentLedger) error) error {
	if m.refuse != nil {
		return m.refuse
	}
	if m.led == nil {
		m.led = domain.NewCommentLedger()
	}
	return fn(m.led)
}

func commentsHandler(snap domain.Session, port *fakeCommentsPort, ledger app.Ledger, policy app.CommentPolicy) http.Handler {
	social := &mentionsOnlyPorts{}
	svc := app.New(fakeSession{snap}, failingPorts{err: errors.New("unused")}, social).WithComments(app.CommentOptions{
		Port: port, Ledger: ledger, Policy: policy,
	})
	return New(svc, noEvents{}, time.Now()).Routes()
}

var readySnap = domain.Session{State: domain.StateAuthenticated, HasClient: true, Self: domain.Self{Username: "not_____nora"}}

// testSummoner is the igid of the account whose summons card servedLedger
// holds and whose comment summonsPort's page carries.
const testSummoner = "500000000001"

// summonsPort is the port with testSummons on the post's first page, written
// by testSummoner and mentioning this account.
func summonsPort() *fakeCommentsPort {
	return &fakeCommentsPort{page: domain.CommentPage{Items: []domain.Comment{
		{ID: testSummons, AuthorIGID: testSummoner, Text: "@not_____nora is this true"},
	}}}
}

func open() app.CommentPolicy {
	return app.CommentPolicy{WritesEnabled: true, DailyCap: 10, MinInterval: 0}
}

// servedLedger holds a summons card from testSummoner for testCode's media,
// received now.
func servedLedger(t *testing.T) *memLedger {
	t.Helper()
	media, _ := domain.MediaIDOf(3000000000000000001)
	sender, _ := domain.ParseIGID(testSummoner)
	led := domain.NewCommentLedger()
	led.RecordSummons(domain.Summons{MessageID: "mid.$card", Media: media, Sender: sender, ReceivedAt: time.Now()})
	return &memLedger{led: led}
}

func envelopeOf(t *testing.T, rec *httptest.ResponseRecorder) errorBody {
	t.Helper()
	var env errorEnvelope
	if err := json.Unmarshal(rec.Body.Bytes(), &env); err != nil || env.Error.Code == "" {
		t.Fatalf("not an envelope: %s", rec.Body.String())
	}
	return env.Error
}

func TestPostCommentSucceedsWith201AndItsShape(t *testing.T) {
	port := summonsPort()
	h := commentsHandler(readySnap, port, servedLedger(t), open())
	rec := do(h, http.MethodPost, "/posts/"+testCode+"/comments", `{"text":"no.","reply_to_comment_id":"`+testSummons+`"}`)
	if rec.Code != http.StatusCreated {
		t.Fatalf("status %d: %s", rec.Code, rec.Body.String())
	}
	var got map[string]any
	_ = json.Unmarshal(rec.Body.Bytes(), &got)
	if len(got) != 2 || got["id"] != "18000000000000009" || got["reply_to_comment_id"] != testSummons {
		t.Errorf("body = %s, want exactly {id, reply_to_comment_id}", rec.Body.String())
	}
}

// One status and one code per guard, and no upstream call behind any of them.
func TestPostCommentAnswersEachGuardWithItsOwnCode(t *testing.T) {
	body := `{"text":"hi","reply_to_comment_id":"` + testSummons + `"}`
	for _, tc := range []struct {
		name   string
		snap   domain.Session
		ledger app.Ledger
		policy app.CommentPolicy
		body   string
		status int
		code   string
	}{
		{"writes off", readySnap, servedLedger(t), app.DefaultCommentPolicy(), body, 403, "writes_disabled"},
		{"session", domain.Session{State: domain.StateCheckpointed}, servedLedger(t), open(), body, 409, "needs_human"},
		{"not a summons", readySnap, &memLedger{}, open(), body, 403, "not_a_summons"},
		{"a card but not its sender's comment", readySnap, servedLedger(t), open(), `{"text":"hi","reply_to_comment_id":"17900000000000999"}`, 403, "not_a_summons"},
		{"no reply target", readySnap, servedLedger(t), open(), `{"text":"hi"}`, 403, "not_a_summons"},
		{"already replied", readySnap, func() app.Ledger {
			l := servedLedger(t)
			id, _ := domain.ParseCommentID(testSummons)
			l.led.Claim(id, "3000000000000000001", time.Now().Add(-time.Hour))
			return l
		}(), open(), body, 409, "already_replied"},
		{"text", readySnap, servedLedger(t), open(), `{"text":"  ","reply_to_comment_id":"` + testSummons + `"}`, 400, "invalid_text"},
		{"cap", readySnap, servedLedger(t), app.CommentPolicy{WritesEnabled: true, DailyCap: 0}, body, 429, "daily_cap"},
		{"too soon", readySnap, &memLedger{refuse: domain.ErrTooSoon}, open(), body, 429, "too_soon"},
		{"latched off", readySnap, func() app.Ledger {
			l := servedLedger(t)
			l.led.Block(domain.BlockFeedbackRequired, time.Now())
			return l
		}(), open(), body, 403, "writes_blocked"},
		{"ledger", readySnap, &memLedger{refuse: domain.Classify(domain.ErrLedgerUnavailable, errors.New("comments.json does not parse"))}, open(), body, 503, "ledger_unavailable"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			port := summonsPort()
			rec := do(commentsHandler(tc.snap, port, tc.ledger, tc.policy), http.MethodPost, "/posts/"+testCode+"/comments", tc.body)
			if rec.Code != tc.status {
				t.Fatalf("status %d, want %d: %s", rec.Code, tc.status, rec.Body.String())
			}
			if env := envelopeOf(t, rec); env.Code != tc.code {
				t.Errorf("code %q, want %q", env.Code, tc.code)
			}
			if port.posts != 0 {
				t.Errorf("%d upstream write(s), want 0", port.posts)
			}
		})
	}
}

// The edge: a bad shortcode, a non-JSON body and an oversized body are
// refused before any use case, with fixed messages.
func TestPostCommentEdgeRefusals(t *testing.T) {
	port := &fakeCommentsPort{}
	h := commentsHandler(readySnap, port, servedLedger(t), open())
	for name, tc := range map[string]struct {
		path, body string
		status     int
		code       string
	}{
		"bad shortcode": {"/posts/bad!code/comments", `{"text":"hi"}`, 400, "invalid_id"},
		"not json":      {"/posts/" + testCode + "/comments", `text=hi`, 400, "invalid_id"},
		"too large":     {"/posts/" + testCode + "/comments", `{"text":"` + strings.Repeat("a", commentBodyLimit) + `"}`, 413, "too_large"},
	} {
		t.Run(name, func(t *testing.T) {
			rec := do(h, http.MethodPost, tc.path, tc.body)
			if rec.Code != tc.status || envelopeOf(t, rec).Code != tc.code {
				t.Errorf("%d %s, want %d %s", rec.Code, rec.Body.String(), tc.status, tc.code)
			}
		})
	}
	if port.posts != 0 {
		t.Errorf("%d upstream writes, want 0", port.posts)
	}
}

func TestGetCommentsShape(t *testing.T) {
	port := &fakeCommentsPort{page: domain.CommentPage{Skipped: 3, Items: []domain.Comment{
		{ID: "1", AuthorIGID: "2", AuthorUsername: "u", Text: "t", At: 1700000500},
		{ID: "4", ParentID: "1", Text: "r"},
	}}}
	rec := do(commentsHandler(readySnap, port, &memLedger{}, open()), http.MethodGet, "/posts/"+testCode+"/comments", "")
	if rec.Code != http.StatusOK {
		t.Fatalf("status %d: %s", rec.Code, rec.Body.String())
	}
	want := `{"items":[{"id":"1","author":{"igid":"2","username":"u"},"text":"t","at":"2023-11-14T22:21:40Z"},` +
		`{"id":"4","parent_id":"1","author":{"igid":"","username":""},"text":"r"}],"next_cursor":null,"skipped_count":3}`
	if got := strings.TrimSpace(rec.Body.String()); got != want {
		t.Errorf("body\n got %s\nwant %s", got, want)
	}
	if port.parent != nil {
		t.Error("a top-level read passed a parent")
	}
}

func TestGetCommentsReplyTo(t *testing.T) {
	port := &fakeCommentsPort{}
	h := commentsHandler(readySnap, port, &memLedger{}, open())
	rec := do(h, http.MethodGet, "/posts/"+testCode+"/comments?reply_to=0"+testSummons, "")
	if rec.Code != http.StatusOK || port.parent == nil || port.parent.String() != testSummons {
		t.Fatalf("status %d, parent %v", rec.Code, port.parent)
	}
	for _, bad := range []string{"", "abc", "-1", "1.5", "0", "99999999999999999999999"} {
		port.reads = 0
		rec := do(h, http.MethodGet, "/posts/"+testCode+"/comments?reply_to="+bad, "")
		if rec.Code != http.StatusBadRequest || envelopeOf(t, rec).Code != "invalid_id" {
			t.Errorf("reply_to=%q: %d %s, want 400 invalid_id", bad, rec.Code, rec.Body.String())
		}
		if strings.Contains(rec.Body.String(), bad) && bad != "" && bad != "0" && bad != "-1" {
			t.Errorf("reply_to=%q is quoted back in the envelope", bad)
		}
		if port.reads != 0 {
			t.Errorf("reply_to=%q reached the port", bad)
		}
	}
}

func TestGetCommentsStatusPerKind(t *testing.T) {
	for kind, status := range map[error]int{
		domain.ErrNeedsHuman:       409,
		domain.ErrUpstreamRejected: 502,
		domain.ErrUnavailable:      503,
		domain.ErrNotFound:         404,
	} {
		port := &fakeCommentsPort{err: domain.Classify(kind, errors.New("media_comments failed"))}
		rec := do(commentsHandler(readySnap, port, &memLedger{}, open()), http.MethodGet, "/posts/"+testCode+"/comments", "")
		if rec.Code != status {
			t.Errorf("%v: status %d, want %d", kind, rec.Code, status)
		}
	}
}

// Only GET and POST exist on the collection.
func TestCommentsRouteMethods(t *testing.T) {
	h := commentsHandler(readySnap, &fakeCommentsPort{}, &memLedger{}, open())
	for _, m := range []string{http.MethodPut, http.MethodDelete, http.MethodPatch} {
		if rec := do(h, m, "/posts/"+testCode+"/comments", ""); rec.Code != http.StatusMethodNotAllowed {
			t.Errorf("%s answered %d, want 405", m, rec.Code)
		}
	}
}

// An action block drawn BY the write answers 403 writes_blocked, not the 429
// or 503 its upstream status would map to, and the next write is refused
// before the port.
func TestAnActionBlockOnTheWriteIsNotRetryInviting(t *testing.T) {
	port := summonsPort()
	port.postErr = &domain.ActionBlocked{Reason: domain.BlockRateLimited,
		Err: domain.Classify(domain.ErrUnavailable, errors.New("comment_add was rate limited"))}
	h := commentsHandler(readySnap, port, servedLedger(t), open())
	rec := do(h, http.MethodPost, "/posts/"+testCode+"/comments", `{"text":"hi","reply_to_comment_id":"`+testSummons+`"}`)
	if rec.Code != http.StatusForbidden || envelopeOf(t, rec).Code != "writes_blocked" {
		t.Fatalf("answered %d %s, want 403 writes_blocked", rec.Code, rec.Body.String())
	}
	rec = do(h, http.MethodPost, "/posts/"+testCode+"/comments", `{"text":"hi","reply_to_comment_id":"`+testSummons+`"}`)
	if rec.Code != http.StatusForbidden || port.posts != 1 {
		t.Errorf("the next write answered %d after %d upstream write(s), want 403 after 1", rec.Code, port.posts)
	}
}
