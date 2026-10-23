package httptransport

// The HTTP surface against the use cases over FAKE ports: one status and one
// envelope per domain error kind on EVERY route, the two stage errors, the
// request validation that runs before any use case (and so before the session
// gate), the success bodies with no `ok`, and the deleted verb paths. The
// whole wired path (real gateway over a measured-wire fake) is exercised in
// cmd/instagram-bridge.

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"instagram-bridge/internal/app"
	"instagram-bridge/internal/domain"
)

type fakeSession struct{ snap domain.Session }

func (f fakeSession) Snapshot() domain.Session { return f.snap }

// failingPorts answers every call with one error.
type failingPorts struct{ err error }

func (f failingPorts) ResolveThread(_ context.Context, short domain.ThreadFBID) (domain.ThreadRef, error) {
	return domain.ThreadRef{}, f.err
}
func (f failingPorts) SendText(context.Context, domain.ThreadRef, string, []domain.Mention, string) (domain.Sent, error) {
	return domain.Sent{}, f.err
}
func (f failingPorts) SendMedia(context.Context, domain.ThreadRef, domain.Upload, string) (domain.Sent, error) {
	return domain.Sent{}, f.err
}
func (f failingPorts) React(context.Context, domain.ThreadRef, string, string, bool) (bool, error) {
	return false, f.err
}
func (f failingPorts) Typing(context.Context, domain.ThreadRef, bool) error { return f.err }
func (f failingPorts) Unsend(context.Context, domain.ThreadRef, string) (bool, error) {
	return false, f.err
}
func (f failingPorts) Members(context.Context, domain.ThreadRef) (domain.Thread, error) {
	return domain.Thread{}, f.err
}
func (f failingPorts) Account(context.Context, domain.IGID) (domain.Account, error) {
	return domain.Account{}, f.err
}
func (f failingPorts) SocialProfile(context.Context, domain.IGID) domain.SocialRead {
	return domain.SocialRead{Err: f.err}
}
func (f failingPorts) Story(context.Context, domain.IGID) (domain.Story, error) {
	return domain.Story{}, f.err
}
func (f failingPorts) Items(context.Context, domain.IGID, domain.Username, string) (domain.TimelinePage, error) {
	return domain.TimelinePage{}, f.err
}
func (f failingPorts) SearchAccounts(context.Context, string) ([]domain.AccountSummary, error) {
	return nil, f.err
}
func (f failingPorts) Post(context.Context, domain.Shortcode) (domain.Post, error) {
	return domain.Post{}, f.err
}
func (f failingPorts) Mentions(context.Context) ([]domain.CommentMention, map[int]int, error) {
	return nil, nil, f.err
}

// verbFailingPorts resolves every thread and fails only the VERB, so the
// error reaches the handler as the verb's and not as a room lookup's.
type verbFailingPorts struct{ failingPorts }

func (verbFailingPorts) ResolveThread(_ context.Context, short domain.ThreadFBID) (domain.ThreadRef, error) {
	long, _ := domain.NewThreadIGID("300000000000000000000000000000000000042")
	return domain.ThreadRef{Short: short, Long: long}, nil
}

// okPorts answers every write and read successfully and COUNTS the calls, so
// a test can prove a request was refused before any use case reached a port.
type okPorts struct {
	mu        sync.Mutex
	calls     int
	confirmed bool
	sent      domain.Sent
	gotText   string
	gotMedia  domain.Upload
	gotReply  string
	gotEmoji  string
	gotRemove bool
	gotMsg    string
	gotTyping *bool
	mentions  []domain.Mention
}

func (p *okPorts) hit() { p.mu.Lock(); p.calls++; p.mu.Unlock() }

func (p *okPorts) ResolveThread(_ context.Context, short domain.ThreadFBID) (domain.ThreadRef, error) {
	p.hit()
	long, _ := domain.NewThreadIGID("300000000000000000000000000000000000042")
	return domain.ThreadRef{Short: short, Long: long}, nil
}
func (p *okPorts) SendText(_ context.Context, _ domain.ThreadRef, text string, mentions []domain.Mention, replyTo string) (domain.Sent, error) {
	p.hit()
	p.gotText, p.mentions, p.gotReply = text, mentions, replyTo
	return p.sent, nil
}
func (p *okPorts) SendMedia(_ context.Context, _ domain.ThreadRef, media domain.Upload, replyTo string) (domain.Sent, error) {
	p.hit()
	p.gotMedia, p.gotReply = media, replyTo
	return p.sent, nil
}
func (p *okPorts) React(_ context.Context, _ domain.ThreadRef, msg, emoji string, remove bool) (bool, error) {
	p.hit()
	p.gotMsg, p.gotEmoji, p.gotRemove = msg, emoji, remove
	return p.confirmed, nil
}
func (p *okPorts) Typing(_ context.Context, _ domain.ThreadRef, typing bool) error {
	p.hit()
	p.gotTyping = &typing
	return nil
}
func (p *okPorts) Unsend(_ context.Context, _ domain.ThreadRef, msg string) (bool, error) {
	p.hit()
	p.gotMsg = msg
	return p.confirmed, nil
}
func (p *okPorts) Members(context.Context, domain.ThreadRef) (domain.Thread, error) {
	p.hit()
	return domain.Thread{Title: "g", IsGroup: true, Members: []domain.Member{{FBID: "100000000000001"}}}, nil
}
func (p *okPorts) Account(_ context.Context, igid domain.IGID) (domain.Account, error) {
	p.hit()
	return domain.Account{IGID: igid.String(), Username: "example_friend"}, nil
}
func (p *okPorts) SocialProfile(context.Context, domain.IGID) domain.SocialRead {
	return domain.SocialRead{Sent: true, Status: 200, Profile: &domain.SocialProfile{}}
}
func (p *okPorts) Story(_ context.Context, igid domain.IGID) (domain.Story, error) {
	p.hit()
	return domain.Story{IGID: igid.String()}, nil
}
func (p *okPorts) Items(context.Context, domain.IGID, domain.Username, string) (domain.TimelinePage, error) {
	p.hit()
	return domain.TimelinePage{}, nil
}
func (p *okPorts) SearchAccounts(context.Context, string) ([]domain.AccountSummary, error) {
	p.hit()
	return nil, nil
}
func (p *okPorts) Post(context.Context, domain.Shortcode) (domain.Post, error) {
	p.hit()
	return domain.Post{}, nil
}
func (p *okPorts) Mentions(context.Context) ([]domain.CommentMention, map[int]int, error) {
	p.hit()
	return nil, nil, nil
}

type noEvents struct{}

func (noEvents) Subscribe() (chan []byte, [][]byte, int, error) {
	return make(chan []byte), nil, 0, nil
}
func (noEvents) Unsubscribe(chan []byte)     {}
func (noEvents) Stats() (int, int, int, int) { return 0, 0, 0, 0 }

func handler(snap domain.Session, err error) http.Handler {
	p := failingPorts{err: err}
	return New(app.New(fakeSession{snap}, p, p), noEvents{}, time.Now()).Routes()
}

func handlerOver(snap domain.Session, messaging app.Messaging, social app.Social) http.Handler {
	return New(app.New(fakeSession{snap}, messaging, social), noEvents{}, time.Now()).Routes()
}

var authenticated = domain.Session{State: domain.StateAuthenticated, Connected: true, HasClient: true}

func get(h http.Handler, path string) *httptest.ResponseRecorder {
	return do(h, http.MethodGet, path, "")
}

func do(h http.Handler, method, path, payload string) *httptest.ResponseRecorder {
	rec := httptest.NewRecorder()
	var body *strings.Reader
	if payload != "" {
		body = strings.NewReader(payload)
	} else {
		body = strings.NewReader("")
	}
	h.ServeHTTP(rec, httptest.NewRequest(method, path, body))
	return rec
}

func body(t *testing.T, rec *httptest.ResponseRecorder) map[string]any {
	t.Helper()
	var out map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &out); err != nil {
		t.Fatalf("body is not JSON: %v (%s)", err, rec.Body.String())
	}
	return out
}

const (
	room = "100000000000042"
	mid  = "mid.$synthetic0001"
)

// routeCall is one request to one route, well formed in every way, so the
// only thing that can make it fail is what the test arranges.
type routeCall struct {
	name, method, path, payload string
}

// everyRoute is each route that reaches Instagram, with a well-formed
// request. The two operational paths (/health, /events) are not here: they
// never answer with the envelope.
var everyRoute = []routeCall{
	{"members", http.MethodGet, "/threads/" + room + "/members", ""},
	{"send text", http.MethodPost, "/threads/" + room + "/messages", `{"text":"hi"}`},
	{"send media", http.MethodPost, "/threads/" + room + "/messages", `{"media":{"data_base64":"eA=="}}`},
	{"unsend", http.MethodDelete, "/threads/" + room + "/messages/" + mid, ""},
	{"reaction", http.MethodPut, "/threads/" + room + "/messages/" + mid + "/reaction", `{"emoji":"x"}`},
	{"typing", http.MethodPut, "/threads/" + room + "/typing", `{"typing":true}`},
	{"account", http.MethodGet, "/accounts/10000001", ""},
	{"story", http.MethodGet, "/accounts/10000001/story", ""},
	{"items", http.MethodGet, "/accounts/10000001/items", ""},
}

var threadRoutes = everyRoute[:6]

// ONE status and ONE envelope per kind, on EVERY route. This is what the
// adapter's retry decision reads: 409 must never collapse into 503, 503 must
// never be claimed for a failure nobody has a name for, and ErrUpstreamRejected
// is 502 on the old routes too now (it was 500 under the legacy table).
func TestEveryDomainKindMapsToItsOneStatusOnEveryRoute(t *testing.T) {
	cause := errors.New("what the gateway said")
	for _, tc := range []struct {
		err  error
		want int
		code string
	}{
		{domain.Classify(domain.ErrInvalidID, cause), http.StatusBadRequest, "invalid_id"},
		{domain.Classify(domain.ErrNotFound, cause), http.StatusNotFound, "not_found"},
		{domain.Classify(domain.ErrNeedsHuman, cause), http.StatusConflict, "needs_human"},
		{domain.Classify(domain.ErrTooLarge, cause), http.StatusRequestEntityTooLarge, "too_large"},
		{domain.Classify(domain.ErrUnavailable, cause), http.StatusServiceUnavailable, "unavailable"},
		{domain.Classify(domain.ErrUpstreamRejected, cause), http.StatusBadGateway, "upstream_rejected"},
		{domain.Classify(domain.ErrUnsupported, cause), http.StatusNotImplemented, "unsupported"},
		// A refinement keeps its parent's status and its own code.
		{domain.Classify(domain.ErrStaleQuery, cause), http.StatusNotImplemented, "stale_query"},
		// Fail closed: no kind is 500, never 503.
		{cause, http.StatusInternalServerError, "internal"},
	} {
		// The VERB fails (the room resolves), so the thread routes answer
		// the kind's own envelope with no lookup stage.
		p := verbFailingPorts{failingPorts{err: tc.err}}
		h := handlerOver(authenticated, p, p)
		for _, rc := range everyRoute {
			rec := do(h, rc.method, rc.path, rc.payload)
			if rec.Code != tc.want {
				t.Errorf("%s, kind %v: answered %d, want %d", rc.name, domain.KindOf(tc.err), rec.Code, tc.want)
				continue
			}
			b := body(t, rec)
			if _, has := b["ok"]; has {
				t.Errorf("%s: a failure still carries the old `ok` field: %v", rc.name, b)
			}
			env, _ := b["error"].(map[string]any)
			if env["code"] != tc.code || env["message"] != "what the gateway said" {
				t.Errorf("%s, kind %v: envelope = %v, want {code:%s, message:<the gateway's text>}",
					rc.name, domain.KindOf(tc.err), b, tc.code)
			}
			if _, has := env["stage"]; has {
				t.Errorf("%s: a VERB failure carries a lookup stage: %v", rc.name, b)
			}
		}
	}
}

// The session gate answers 409 `needs_human`, the message naming the state
// and its detail: what the caller needs is which human action is due.
func TestTheSessionGateAnswersWithTheStateOnEveryRoute(t *testing.T) {
	h := handler(domain.Session{State: domain.StateCheckpointed, Detail: "challenge required"}, nil)
	for _, rc := range everyRoute {
		rec := do(h, rc.method, rc.path, rc.payload)
		if rec.Code != http.StatusConflict {
			t.Errorf("%s: answered %d, want 409", rc.name, rec.Code)
			continue
		}
		env, _ := body(t, rec)["error"].(map[string]any)
		if env["code"] != "needs_human" || env["message"] != "checkpointed: challenge required" {
			t.Errorf("%s: envelope = %v", rc.name, env)
		}
	}
}

// A room's route lookup failing against Instagram keeps its own distinction
// (the old body's `thread_lookup_failed`), now as `"stage": "thread_lookup"`
// beside its kind's code, at the status its kind maps to. A checkpoint found
// there is still 409. "Not connected" is a plain 503: nothing about the room
// was learned.
func TestAFailedThreadLookupIsMarkedOnEveryThreadRoute(t *testing.T) {
	checkpoint := domain.Classify(domain.ErrNeedsHuman, errors.New("checkpoint_required"))
	down := domain.Classify(domain.ErrUnavailable, errors.New("instagram session is not connected"))
	unnamed := errors.New("route lookup answered nothing usable")
	for _, rc := range threadRoutes {
		rec := do(handler(authenticated, checkpoint), rc.method, rc.path, rc.payload)
		if rec.Code != http.StatusConflict {
			t.Errorf("%s: a checkpoint in the lookup answered %d, want 409", rc.name, rec.Code)
		}
		env, _ := body(t, rec)["error"].(map[string]any)
		if env["code"] != "needs_human" || env["stage"] != "thread_lookup" || env["message"] != "checkpoint_required" {
			t.Errorf("%s: envelope = %v, want needs_human at stage thread_lookup", rc.name, env)
		}

		rec = do(handler(authenticated, unnamed), rc.method, rc.path, rc.payload)
		env, _ = body(t, rec)["error"].(map[string]any)
		if rec.Code != http.StatusInternalServerError || env["stage"] != "thread_lookup" {
			t.Errorf("%s: an unnamed lookup failure = %d %v, want 500 at stage thread_lookup", rc.name, rec.Code, env)
		}

		rec = do(handler(authenticated, down), rc.method, rc.path, rc.payload)
		if rec.Code != http.StatusServiceUnavailable {
			t.Errorf("%s: a down socket in the lookup answered %d, want 503", rc.name, rec.Code)
		}
		env, _ = body(t, rec)["error"].(map[string]any)
		if _, has := env["stage"]; has || env["code"] != "unavailable" {
			t.Errorf("%s: envelope = %v, want a plain unavailable", rc.name, env)
		}
	}
}

// Request validation belongs to the handler and runs BEFORE any use case, so
// a malformed request is 400 whatever the session's state: checkpointed here,
// on purpose. Before P14 the session gate answered 409 first on these routes.
func TestMalformedRequestsAre400BeforeTheSessionGate(t *testing.T) {
	longRoom := "340282366841710301281152937372201004392"
	for _, rc := range []routeCall{
		{"members, not a thread id", http.MethodGet, "/threads/not-a-thread/members", ""},
		{"members, the LONG id", http.MethodGet, "/threads/" + longRoom + "/members", ""},
		{"send, not a thread id", http.MethodPost, "/threads/abc/messages", `{"text":"hi"}`},
		{"send, zero", http.MethodPost, "/threads/0/messages", `{"text":"hi"}`},
		{"send, no text and no media", http.MethodPost, "/threads/" + room + "/messages", `{}`},
		{"send, empty text", http.MethodPost, "/threads/" + room + "/messages", `{"text":""}`},
		{"send, text AND media", http.MethodPost, "/threads/" + room + "/messages", `{"text":"hi","media":{"data_base64":"eA=="}}`},
		{"send, mentions on media", http.MethodPost, "/threads/" + room + "/messages", `{"media":{"data_base64":"eA=="},"mentions":[{"fbid":"1","offset":0,"length":1}]}`},
		{"send, media with no bytes", http.MethodPost, "/threads/" + room + "/messages", `{"media":{}}`},
		{"send, media not base64", http.MethodPost, "/threads/" + room + "/messages", `{"media":{"data_base64":"!!!!"}}`},
		{"send, a non-numeric mention", http.MethodPost, "/threads/" + room + "/messages", `{"text":"hi","mentions":[{"fbid":"someone","offset":0,"length":2}]}`},
		{"send, a negative mention offset", http.MethodPost, "/threads/" + room + "/messages", `{"text":"hi","mentions":[{"fbid":"1","offset":-1,"length":2}]}`},
		{"send, reply_to with a space", http.MethodPost, "/threads/" + room + "/messages", `{"text":"hi","reply_to":"mid.$a b"}`},
		{"send, not JSON", http.MethodPost, "/threads/" + room + "/messages", `{not json`},
		{"unsend, not a thread id", http.MethodDelete, "/threads/abc/messages/" + mid, ""},
		{"unsend, a message id with a space", http.MethodDelete, "/threads/" + room + "/messages/mid.$a%20b", ""},
		{"reaction, no emoji", http.MethodPut, "/threads/" + room + "/messages/" + mid + "/reaction", `{}`},
		{"reaction, no emoji on a removal", http.MethodPut, "/threads/" + room + "/messages/" + mid + "/reaction", `{"remove":true}`},
		{"reaction, an oversize emoji", http.MethodPut, "/threads/" + room + "/messages/" + mid + "/reaction", `{"emoji":"` + strings.Repeat("x", 65) + `"}`},
		{"reaction, not a thread id", http.MethodPut, "/threads/abc/messages/" + mid + "/reaction", `{"emoji":"x"}`},
		{"typing, no field", http.MethodPut, "/threads/" + room + "/typing", `{}`},
		{"typing, not a thread id", http.MethodPut, "/threads/abc/typing", `{"typing":true}`},
		{"account, a username", http.MethodGet, "/accounts/not_a_number", ""},
		{"account, zero", http.MethodGet, "/accounts/0", ""},
		{"story, a username", http.MethodGet, "/accounts/not_a_number/story", ""},
	} {
		for _, state := range []domain.SessionState{domain.StateAuthenticated, domain.StateCheckpointed} {
			p := &okPorts{}
			h := handlerOver(domain.Session{State: state, Detail: "captcha", Connected: true, HasClient: true}, p, p)
			rec := do(h, rc.method, rc.path, rc.payload)
			if rec.Code != http.StatusBadRequest {
				t.Errorf("%s (session %s): answered %d, want 400: %s", rc.name, state, rec.Code, rec.Body.String())
				continue
			}
			env, _ := body(t, rec)["error"].(map[string]any)
			if env["code"] != "invalid_id" || env["message"] == "" {
				t.Errorf("%s: envelope = %v", rc.name, env)
			}
			if p.calls != 0 {
				t.Errorf("%s: a malformed request reached a port %d time(s)", rc.name, p.calls)
			}
		}
	}
}

// A malformed path id's own text never reaches the envelope: the parsers
// quote their input, and a path segment is whatever a caller typed.
func TestAMalformedPathIDIsNotEchoed(t *testing.T) {
	const typed = "someone-typed-this-7c21"
	h := handler(authenticated, errors.New("no use case should run"))
	for _, path := range []string{
		"/threads/" + typed + "/members",
		"/accounts/" + typed,
		"/accounts/" + typed + "/story",
	} {
		if rec := get(h, path); strings.Contains(rec.Body.String(), typed) {
			t.Errorf("GET %s echoed the id: %s", path, rec.Body.String())
		}
	}
}

// A text message over 1 MiB is 413, though the route reads under the media
// limit: a text message is bounded tighter. A body over the media limit is
// 413 whatever it is.
func TestOversizeBodiesAre413(t *testing.T) {
	p := &okPorts{}
	h := handlerOver(authenticated, p, p)
	text := `{"text":"` + strings.Repeat("x", maxJSONBody+1024) + `"}`
	if rec := do(h, http.MethodPost, "/threads/"+room+"/messages", text); rec.Code != http.StatusRequestEntityTooLarge {
		t.Errorf("an oversize text message answered %d, want 413", rec.Code)
	} else if env, _ := body(t, rec)["error"].(map[string]any); env["code"] != "too_large" {
		t.Errorf("envelope = %v", env)
	}
	reaction := `{"emoji":"x","pad":"` + strings.Repeat("x", maxJSONBody) + `"}`
	if rec := do(h, http.MethodPut, "/threads/"+room+"/messages/"+mid+"/reaction", reaction); rec.Code != http.StatusRequestEntityTooLarge {
		t.Errorf("an oversize reaction body answered %d, want 413", rec.Code)
	}
	if p.calls != 0 {
		t.Errorf("an oversize body reached a port %d time(s)", p.calls)
	}
}

// The success bodies: the resource, with no `ok`, and the fields Nora reads.
func TestWriteSuccessBodies(t *testing.T) {
	p := &okPorts{sent: domain.Sent{ID: "mid.$sent", TimestampMS: 1700000000123}, confirmed: true}
	h := handlerOver(authenticated, p, p)

	rec := do(h, http.MethodPost, "/threads/"+room+"/messages",
		`{"text":"hello","mentions":[{"fbid":"0100000000000001","offset":0,"length":5}],"reply_to":"mid.$parent"}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("send answered %d: %s", rec.Code, rec.Body.String())
	}
	b := body(t, rec)
	if len(b) != 2 || b["id"] != "mid.$sent" || b["timestamp_ms"] != float64(1700000000123) {
		t.Errorf("send body = %v, want exactly {id, timestamp_ms}", b)
	}
	if p.gotText != "hello" || p.gotReply != "mid.$parent" {
		t.Errorf("the use case got text=%q reply=%q", p.gotText, p.gotReply)
	}
	// Canonicalized at the edge, like every other id.
	if len(p.mentions) != 1 || p.mentions[0].FBID != "100000000000001" || p.mentions[0].Length != 5 {
		t.Errorf("mentions reached the use case as %+v", p.mentions)
	}

	// No timestamp from Instagram: the field is OMITTED, never 0. Nora
	// records a 0 as a real timestamp and an absent one as unknown.
	p.sent = domain.Sent{ID: "mid.$sent"}
	b = body(t, do(h, http.MethodPost, "/threads/"+room+"/messages", `{"text":"hello"}`))
	if _, has := b["timestamp_ms"]; has {
		t.Errorf("a send with no platform timestamp carries timestamp_ms: %v", b)
	}

	rec = do(h, http.MethodPost, "/threads/"+room+"/messages",
		`{"media":{"data_base64":"iVBORw0K","filename":"a.png","mimetype":"image/png"},"caption":"not a field"}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("send media answered %d: %s", rec.Code, rec.Body.String())
	}
	if p.gotMedia.Filename != "a.png" || p.gotMedia.MimeType != "image/png" || len(p.gotMedia.Data) == 0 {
		t.Errorf("the upload reached the use case as %+v", p.gotMedia)
	}

	for _, tc := range []struct{ name, method, path, payload string }{
		{"unsend", http.MethodDelete, "/threads/" + room + "/messages/" + mid, ""},
		{"reaction", http.MethodPut, "/threads/" + room + "/messages/" + mid + "/reaction", `{"emoji":"x","remove":true}`},
	} {
		for _, confirmed := range []bool{true, false} {
			p.confirmed = confirmed
			rec := do(h, tc.method, tc.path, tc.payload)
			b := body(t, rec)
			if rec.Code != http.StatusOK || len(b) != 1 || b["confirmed"] != confirmed {
				t.Errorf("%s (confirmed=%v): %d %v, want 200 {confirmed:%v}", tc.name, confirmed, rec.Code, b, confirmed)
			}
		}
	}
	if p.gotMsg != mid || p.gotEmoji != "x" || !p.gotRemove {
		t.Errorf("reaction reached the use case as msg=%q emoji=%q remove=%v", p.gotMsg, p.gotEmoji, p.gotRemove)
	}

	rec = do(h, http.MethodPut, "/threads/"+room+"/typing", `{"typing":false}`)
	if b := body(t, rec); rec.Code != http.StatusOK || len(b) != 1 || b["typing"] != false {
		t.Errorf("typing: %d %v", rec.Code, b)
	}
	if p.gotTyping == nil || *p.gotTyping {
		t.Errorf("typing reached the use case as %v", p.gotTyping)
	}
}

// The read bodies lost `ok` and nothing else.
func TestReadBodiesCarryNoOk(t *testing.T) {
	p := &okPorts{}
	h := handlerOver(authenticated, p, p)
	for _, path := range []string{"/threads/" + room + "/members", "/accounts/10000001", "/accounts/10000001/story"} {
		rec := get(h, path)
		if rec.Code != http.StatusOK {
			t.Fatalf("GET %s answered %d: %s", path, rec.Code, rec.Body.String())
		}
		if _, has := body(t, rec)["ok"]; has {
			t.Errorf("GET %s still carries `ok`: %s", path, rec.Body.String())
		}
	}
}

// The verb paths are DELETED (17 §P14), not aliased: each is the mux's own
// 404, which no handler wrote, so no use case can run behind one.
func TestTheOldVerbPathsAreGone(t *testing.T) {
	p := &okPorts{sent: domain.Sent{ID: "mid.$sent"}, confirmed: true}
	h := handlerOver(authenticated, p, p)
	for _, path := range []string{"/send", "/send-media", "/react", "/typing", "/unsend", "/roster", "/profile", "/story"} {
		for _, method := range []string{http.MethodPost, http.MethodGet} {
			rec := do(h, method, path, `{"room_id":"`+room+`","text":"hi","message_id":"`+mid+`","emoji":"x","typing":true,"data_base64":"eA=="}`)
			if rec.Code != http.StatusNotFound {
				t.Errorf("%s %s answered %d, want 404", method, path, rec.Code)
			}
		}
	}
	if p.calls != 0 {
		t.Errorf("an old path reached a port %d time(s)", p.calls)
	}
}

// /health keeps its operational body, `ok` included: it is not a resource,
// and the container healthcheck and Nora's identity read both depend on it.
func TestHealthIsUnchanged(t *testing.T) {
	rec := get(handler(authenticated, nil), "/health")
	if rec.Code != http.StatusOK {
		t.Fatalf("/health answered %d", rec.Code)
	}
	b := body(t, rec)
	for _, key := range []string{"ok", "session", "connected", "seq", "self"} {
		if _, has := b[key]; !has {
			t.Errorf("/health lost %q: %v", key, b)
		}
	}
}
