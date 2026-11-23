package httptransport

// Focused proofs for the platform contract routes: auth, owner/generation
// fences, honest outcomes and their statuses, request-hash replay/conflict,
// and blob custody. The
// fakes behave like the real seams — including their refusal shapes — so a
// regression in reservation or outcome mapping turns red.

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"sort"
	"strings"
	"sync"
	"testing"
	"time"

	"instagram-bridge/internal/app"
	"instagram-bridge/internal/bridge"
	"instagram-bridge/internal/domain"
	"instagram-bridge/internal/stream"

	sdktrace "go.opentelemetry.io/otel/sdk/trace"
)

// --- fakes -------------------------------------------------------------------
// fakeSession lives in server_test.go (same package); reuse it.

type fakeMessaging struct {
	sent      []domain.Sent
	uploads   []domain.Upload
	sendErr   error
	reactOK   bool
	reactErr  error
	typingErr error
}

func (f *fakeMessaging) ResolveThread(_ context.Context, short domain.ThreadFBID) (domain.ThreadRef, error) {
	return domain.ThreadRef{Short: short}, nil
}
func (f *fakeMessaging) SendText(_ context.Context, _ domain.ThreadRef, _ string, _ []domain.Mention, _ string) (domain.Sent, error) {
	if f.sendErr != nil {
		return domain.Sent{}, f.sendErr
	}
	if len(f.sent) == 0 {
		return domain.Sent{ID: "mid-1", TimestampMS: 100}, nil
	}
	s := f.sent[0]
	f.sent = f.sent[1:]
	return s, nil
}
func (f *fakeMessaging) SendMedia(_ context.Context, _ domain.ThreadRef, upload domain.Upload, _ string) (domain.Sent, error) {
	f.uploads = append(f.uploads, upload)
	return domain.Sent{ID: "mid-2", TimestampMS: 101}, nil
}
func (f *fakeMessaging) React(_ context.Context, _ domain.ThreadRef, _, _ string, _ bool) (bool, error) {
	return f.reactOK, f.reactErr
}
func (f *fakeMessaging) Typing(_ context.Context, _ domain.ThreadRef, _ bool) error {
	return f.typingErr
}
func (f *fakeMessaging) Unsend(_ context.Context, _ domain.ThreadRef, _ string) (bool, error) {
	return true, nil
}
func (f *fakeMessaging) Members(_ context.Context, ref domain.ThreadRef) (domain.Thread, error) {
	return domain.Thread{IsGroup: true, Title: "the group", Members: []domain.Member{{FBID: "fb-2", Username: "two"}}}, nil
}

type fakeSocial struct{}

func (f *fakeSocial) Account(context.Context, domain.IGID) (domain.Account, error) {
	return domain.Account{}, errors.New("not used")
}
func (f *fakeSocial) SocialProfile(context.Context, domain.IGID) domain.SocialRead {
	return domain.SocialRead{}
}
func (f *fakeSocial) Story(context.Context, domain.IGID) (domain.Story, error) {
	return domain.Story{}, errors.New("not used")
}
func (f *fakeSocial) Items(context.Context, domain.IGID, domain.Username, string) (domain.TimelinePage, error) {
	return domain.TimelinePage{}, errors.New("not used")
}
func (f *fakeSocial) SearchAccounts(context.Context, string) ([]domain.AccountSummary, error) {
	return nil, errors.New("not used")
}
func (f *fakeSocial) Post(context.Context, domain.Shortcode) (domain.Post, error) {
	return domain.Post{}, errors.New("not used")
}
func (f *fakeSocial) Mentions(context.Context) ([]domain.CommentMention, map[int]int, error) {
	return nil, nil, errors.New("not used")
}

// fakeActionStore mimics bridge_action's semantics in memory: hash-bound
// reservation, replay/conflict, prepared→started CAS, work generation fence.
type fakeActionStore struct {
	mu   sync.Mutex
	rows map[string]*bridge.ActionRow
	gens map[string]int
	// down makes every call fail, as an unreachable ledger does.
	down error
	// failFinish fails the terminal write; failGet fails every read.
	failFinish, failGet error
}

func newFakeActionStore() *fakeActionStore {
	return &fakeActionStore{rows: map[string]*bridge.ActionRow{}, gens: map[string]int{}}
}

func (f *fakeActionStore) Reserve(_ context.Context, id, ok, oid string, og *int, op string, req json.RawMessage, hash string) (bridge.ReserveOutcome, *bridge.ActionRow, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.down != nil {
		return 0, nil, f.down
	}
	if row, seen := f.rows[id]; seen {
		if row.RequestHash != hash {
			return bridge.ReserveConflict, row, nil
		}
		if row.State == bridge.ActionPrepared {
			return bridge.ReserveExecute, row, nil
		}
		return bridge.ReserveReplay, row, nil
	}
	row := &bridge.ActionRow{ActionID: id, OwnerKind: ok, OwnerID: oid,
		OwnerGeneration: og, Op: op, Request: req, RequestHash: hash, State: bridge.ActionPrepared}
	f.rows[id] = row
	return bridge.ReserveExecute, row, nil
}
func (f *fakeActionStore) Get(_ context.Context, id string) (*bridge.ActionRow, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.down != nil {
		return nil, f.down
	}
	if f.failGet != nil {
		return nil, f.failGet
	}
	return f.rows[id], nil
}
func (f *fakeActionStore) MarkStarted(_ context.Context, id string, messageID *int64) (bool, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	row := f.rows[id]
	if row == nil || row.State != bridge.ActionPrepared {
		return false, nil
	}
	row.State = bridge.ActionStarted
	row.MessageID = messageID
	at := time.Now().UnixMilli()
	row.StartedAtMS = &at
	return true, nil
}
func (f *fakeActionStore) Finish(_ context.Context, id, state, ref string, refs []string, ms *int64, ek, em string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.failFinish != nil {
		return f.failFinish
	}
	row := f.rows[id]
	row.State = state
	at := time.Now().UnixMilli()
	row.SettledAtMS = &at
	row.PlatformRef = nil
	if ref != "" {
		row.PlatformRef = &ref
	}
	row.PlatformMS = ms
	row.PlatformRefs = nil
	if len(refs) > 0 {
		row.PlatformRefs = append([]string{}, refs...)
	}
	if ek != "" {
		row.Error, _ = json.Marshal(map[string]string{"kind": ek, "message": em})
	}
	return nil
}

// ValidateOwner mirrors the scoped authority check: work needs a known live
// generation match; event_receipt needs a known open event; the rest deny.
func (f *fakeActionStore) ValidateOwner(_ context.Context, kind, id string, gen *int, _ string) (*bridge.OwnerVerdict, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	switch kind {
	case "work":
		if gen == nil {
			return &bridge.OwnerVerdict{Reason: "work owner must declare a generation"}, nil
		}
		g, ok := f.gens[id]
		if !ok || g != *gen {
			return &bridge.OwnerVerdict{Reason: "stale owner generation"}, nil
		}
		return &bridge.OwnerVerdict{OK: true}, nil
	case "event_receipt":
		if _, ok := f.gens["evt:"+id]; !ok {
			return &bridge.OwnerVerdict{Reason: "no journaled receipt intent for owner"}, nil
		}
		return &bridge.OwnerVerdict{OK: true}, nil
	}
	return &bridge.OwnerVerdict{Reason: "unknown owner kind"}, nil
}

// --- harness ------------------------------------------------------------------

var v1Session = domain.Session{State: domain.StateAuthenticated, Connected: true, HasClient: true,
	Self: domain.Self{FBID: "fb-self", IGID: "ig-self"}}

func v1Server(t *testing.T, store ActionStore, msg *fakeMessaging, custody *bridge.Custody) *httptest.Server {
	t.Helper()
	return v1ServerAs(t, v1Session, store, msg, custody)
}

func v1ServerAs(t *testing.T, snap domain.Session, store ActionStore, msg *fakeMessaging, custody *bridge.Custody) *httptest.Server {
	t.Helper()
	svc := app.New(fakeSession{snap: snap}, msg, &fakeSocial{})
	srv := New(svc, stream.NewHub(), time.Now())
	v1 := &V1{
		App: svc, Custody: custody,
		Metrics: bridge.NewMetrics(), AccountKey: "acct",
		UpstreamOK: func() bool { return true },
	}
	if store != nil {
		v1.Store = store
	}
	srv = srv.WithV1(v1)
	ts := httptest.NewServer(RequireToken("tok")(srv.Routes()))
	t.Cleanup(ts.Close)
	return ts
}

func newCustody(t *testing.T) *bridge.Custody {
	t.Helper()
	c, err := bridge.NewCustody(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	return c
}

// call sends one v1Session request and returns its status and body.
func call(t *testing.T, ts *httptest.Server, method, path string, body io.Reader, headers ...string) (int, []byte) {
	t.Helper()
	req, err := http.NewRequest(method, ts.URL+path, body)
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Authorization", "Bearer tok")
	for i := 0; i+1 < len(headers); i += 2 {
		req.Header.Set(headers[i], headers[i+1])
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(resp.Body)
	if err != nil {
		t.Fatal(err)
	}
	return resp.StatusCode, raw
}

func callJSON(t *testing.T, ts *httptest.Server, method, path string, body any, headers ...string) (int, map[string]any) {
	t.Helper()
	var r io.Reader
	if body != nil {
		raw, _ := json.Marshal(body)
		r = bytes.NewReader(raw)
	}
	code, raw := call(t, ts, method, path, r, append([]string{"Content-Type", "application/json"}, headers...)...)
	var out map[string]any
	_ = json.Unmarshal(raw, &out)
	return code, out
}

func postOp(t *testing.T, ts *httptest.Server, body map[string]any) (int, map[string]any) {
	t.Helper()
	return callJSON(t, ts, http.MethodPost, "/v1/platform/operations", body)
}

func sendOp(operationID string, gen *int) map[string]any {
	owner := map[string]any{"kind": "work", "id": "w-1"}
	if gen != nil {
		owner["generation"] = *gen
	}
	return map[string]any{
		"operation_id": operationID, "owner": owner, "kind": "send",
		"effect": map[string]any{
			"target": map[string]any{"kind": "dm", "id": "100000000000042"},
			"text":   "hi there",
		},
	}
}

// part reads one nested field of a decoded body: part(out, "payload", "error").
func part(out map[string]any, keys ...string) any {
	var v any = out
	for _, k := range keys {
		m, _ := v.(map[string]any)
		v = m[k]
	}
	return v
}

// --- the contract document ------------------------------------------------------

// contractPath is docs/service-api/openapi.json from this package's directory.
const contractPath = "../../../../../docs/service-api/openapi.json"

type contractDoc struct {
	Paths      map[string]map[string]json.RawMessage `json:"paths"`
	Components struct {
		Schemas map[string]struct {
			Status map[string]int `json:"x-http-status"`
		} `json:"schemas"`
	} `json:"components"`
}

func readContract(t *testing.T) contractDoc {
	t.Helper()
	raw, err := os.ReadFile(contractPath)
	if err != nil {
		t.Fatalf("the contract document must be readable: %v", err)
	}
	var doc contractDoc
	if err := json.Unmarshal(raw, &doc); err != nil {
		t.Fatal(err)
	}
	return doc
}

// contractStatus is the kernel's status for every outcome, as the document
// states it.
func contractStatus(t *testing.T) map[string]int {
	t.Helper()
	doc := readContract(t)
	out := map[string]int{}
	for _, name := range []string{"OperationOutcome", "ReadOutcome"} {
		table := doc.Components.Schemas[name].Status
		if len(table) == 0 {
			t.Fatalf("the document has no x-http-status for %s", name)
		}
		for outcome, status := range table {
			out[outcome] = status
		}
	}
	return out
}

// assertEnvelope checks a body is exactly the six parts, carries want, and
// was answered under the document's status for it.
func assertEnvelope(t *testing.T, status map[string]int, code int, out map[string]any, want string) {
	t.Helper()
	keys := make([]string, 0, len(out))
	for k := range out {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	if got := strings.Join(keys, ","); got != "from,outcome,payload,scope,trace,when" {
		t.Fatalf("envelope parts = %s, want the six (body %v)", got, out)
	}
	if out["outcome"] != want {
		t.Fatalf("outcome = %v, want %s (body %v)", out["outcome"], want, out)
	}
	if code != status[want] {
		t.Fatalf("%s answered %d, the contract says %d", want, code, status[want])
	}
	if want == "pending" {
		if s := part(out, "when", "state"); s == "settled" {
			t.Fatalf("a pending answer is not settled: %v", out["when"])
		}
	} else if _, isOp := out["when"].(map[string]any)["state"]; isOp && part(out, "when", "state") != "settled" {
		t.Fatalf("%s must be settled, got %v", want, out["when"])
	}
}

func assertNoEnvelope(t *testing.T, code int, out map[string]any, want int) {
	t.Helper()
	if code != want {
		t.Fatalf("status = %d, want %d (body %v)", code, want, out)
	}
	if _, ok := out["outcome"]; ok {
		t.Fatalf("a %d carries no envelope, got %v", want, out)
	}
}

// --- item 2: the routes ---------------------------------------------------------

// oldRoutes are the retired wire's collections under /v1/, spelled in parts
// so the repository-wide scan for an old route name finds none here.
var oldRoutes = []string{"actions", "self", "reads", "attachments", "health",
	"groups", "guilds", "channels", "presence"}

func TestRoutesMatchThePlatformContract(t *testing.T) {
	doc := readContract(t)
	documented := map[string]bool{}
	for path, ops := range doc.Paths {
		for method := range ops {
			documented[strings.ToUpper(method)+" "+path] = true
		}
	}
	if len(documented) == 0 {
		t.Fatal("the document lists no routes")
	}
	svc := app.New(fakeSession{snap: v1Session}, &fakeMessaging{}, &fakeSocial{})
	srv := New(svc, stream.NewHub(), time.Now()).WithV1(&V1{App: svc, Metrics: bridge.NewMetrics()})
	_, patterns := srv.routes()
	served := map[string]bool{}
	for _, p := range patterns {
		served[p] = true
		path := p[strings.Index(p, " ")+1:]
		if strings.HasPrefix(path, "/v1/platform/") || strings.HasPrefix(path, "/v1/operations/") {
			if !documented[p] {
				t.Errorf("serves %s, which the contract does not document", p)
			}
		}
		for _, old := range oldRoutes {
			if strings.HasPrefix(path, "/v1/"+old) {
				t.Errorf("still serves the old route %s", p)
			}
		}
	}
	for route := range documented {
		if !served[route] {
			t.Errorf("the contract documents %s, which is not served", route)
		}
	}
	for _, probe := range []string{"GET /health", "GET /ready"} {
		if !served[probe] {
			t.Errorf("%s must be registered", probe)
		}
	}

	// Each documented route answers through its handler, not the mux's 404 or
	// 405, and each old path is the mux's 404.
	ts := v1Server(t, newFakeActionStore(), &fakeMessaging{}, newCustody(t))
	for route := range documented {
		method, path, _ := strings.Cut(route, " ")
		path = strings.NewReplacer("{operation_id}", "op-x", "{account_id}", "self", "{key}", "key-x").Replace(path)
		code, _ := call(t, ts, method, path, strings.NewReader("{}"))
		if code == http.StatusNotFound || code == http.StatusMethodNotAllowed {
			t.Errorf("%s %s answered %d from the mux", method, path, code)
		}
	}
	for _, old := range []string{"actions", "actions/x", "self", "reads", "attachments/k", "health",
		"groups", "guilds/g/members", "channels/c", "presence/g/u"} {
		old = "/v1/" + old
		for _, method := range []string{http.MethodGet, http.MethodPost, http.MethodPut} {
			if code, _ := call(t, ts, method, old, strings.NewReader("{}")); code != http.StatusNotFound {
				t.Errorf("%s %s = %d, want 404", method, old, code)
			}
		}
	}

	// /ready: 200 only when the bridge can serve the contract.
	for _, tc := range []struct {
		name    string
		snap    domain.Session
		store   ActionStore
		custody *bridge.Custody
		want    int
	}{
		{"serving", v1Session, newFakeActionStore(), newCustody(t), http.StatusOK},
		{"no action store", v1Session, nil, newCustody(t), http.StatusServiceUnavailable},
		{"no custody", v1Session, newFakeActionStore(), nil, http.StatusServiceUnavailable},
		{"checkpointed", domain.Session{State: domain.StateCheckpointed}, newFakeActionStore(), newCustody(t), http.StatusServiceUnavailable},
	} {
		ts := v1ServerAs(t, tc.snap, tc.store, &fakeMessaging{}, tc.custody)
		code, out := callJSON(t, ts, http.MethodGet, "/ready", nil)
		if code != tc.want {
			t.Errorf("/ready %s = %d, want %d (%v)", tc.name, code, tc.want, out)
		}
		if _, ok := out["outcome"]; ok {
			t.Errorf("/ready is a probe, not an envelope: %v", out)
		}
	}
	unmounted := httptest.NewServer(New(svc, stream.NewHub(), time.Now()).Routes())
	defer unmounted.Close()
	resp, err := http.Get(unmounted.URL + "/ready")
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusServiceUnavailable {
		t.Errorf("/ready without the contract mounted = %d, want 503", resp.StatusCode)
	}
}

func TestV1AuthRequired(t *testing.T) {
	ts := v1Server(t, newFakeActionStore(), &fakeMessaging{}, nil)
	for _, route := range []string{"POST /v1/platform/operations", "GET /v1/platform/operations/x",
		"POST /v1/platform/queries", "GET /v1/platform/accounts/self", "PUT /v1/platform/blobs/k",
		"GET /v1/platform/blobs/k", "GET /v1/operations/status", "GET /health", "GET /ready"} {
		method, path, _ := strings.Cut(route, " ")
		req, _ := http.NewRequest(method, ts.URL+path, strings.NewReader("{}"))
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		resp.Body.Close()
		if resp.StatusCode != http.StatusUnauthorized {
			t.Fatalf("%s without token = %d, want 401", route, resp.StatusCode)
		}
	}
}

// --- item 3: outcomes and statuses ----------------------------------------------

func TestOperationOutcomesFollowTheContract(t *testing.T) {
	status := contractStatus(t)
	gen := 7
	tp := "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
	fresh := func(msg *fakeMessaging) (*fakeActionStore, *httptest.Server) {
		store := newFakeActionStore()
		store.gens["w-1"] = gen
		return store, v1Server(t, store, msg, newCustody(t))
	}

	t.Run("complete", func(t *testing.T) {
		_, ts := fresh(&fakeMessaging{})
		raw, _ := json.Marshal(sendOp("w-1:send:0", &gen))
		code, body := call(t, ts, http.MethodPost, "/v1/platform/operations", bytes.NewReader(raw), "traceparent", tp)
		var out map[string]any
		_ = json.Unmarshal(body, &out)
		assertEnvelope(t, status, code, out, "complete")
		if part(out, "from", "platform_ref") != "mid-1" || part(out, "from", "target") != "100000000000042" {
			t.Fatalf("from = %v", out["from"])
		}
		if part(out, "scope", "level") != "room" || part(out, "scope", "id") != "100000000000042" {
			t.Fatalf("scope = %v", out["scope"])
		}
		if out["trace"] != "4bf92f3577b34da6a3ce929d0e0e4736" {
			t.Fatalf("trace = %v", out["trace"])
		}
		if part(out, "when", "started_at_ms") == nil || part(out, "when", "settled_at_ms") == nil {
			t.Fatalf("a settled row carries its times: %v", out["when"])
		}
	})
	t.Run("pending", func(t *testing.T) {
		store, ts := fresh(&fakeMessaging{})
		body := sendOp("w-1:send:p", &gen)
		var effect any
		raw, _ := json.Marshal(body["effect"])
		_ = json.Unmarshal(raw, &effect)
		hash, canon, _ := bridge.RequestHash(effect)
		store.rows["w-1:send:p"] = &bridge.ActionRow{ActionID: "w-1:send:p", OwnerKind: "work", OwnerID: "w-1",
			Op: "send", Request: canon, RequestHash: hash, State: bridge.ActionStarted}
		code, out := postOp(t, ts, body)
		assertEnvelope(t, status, code, out, "pending")
		if part(out, "when", "state") != "started" {
			t.Fatalf("when.state = %v, want started", part(out, "when", "state"))
		}
	})
	t.Run("rejected", func(t *testing.T) {
		_, ts := fresh(&fakeMessaging{sendErr: domain.ErrUpstreamRejected})
		code, out := postOp(t, ts, sendOp("w-1:send:r", &gen))
		assertEnvelope(t, status, code, out, "rejected")
		if part(out, "payload", "error", "kind") != "upstream" {
			t.Fatalf("payload.error = %v", part(out, "payload", "error"))
		}
	})
	t.Run("unsupported", func(t *testing.T) {
		_, ts := fresh(&fakeMessaging{})
		code, out := postOp(t, ts, map[string]any{
			"operation_id": "w-1:edit:0", "owner": map[string]any{"kind": "work", "id": "w-1", "generation": gen},
			"kind":   "edit",
			"effect": map[string]any{"target": map[string]any{"kind": "dm", "id": "100000000000042"}, "message_ref": "1802", "text": "x"},
		})
		assertEnvelope(t, status, code, out, "unsupported")
	})
	t.Run("uncertain", func(t *testing.T) {
		_, ts := fresh(&fakeMessaging{reactOK: false})
		code, out := postOp(t, ts, map[string]any{
			"operation_id": "w-1:react:0", "owner": map[string]any{"kind": "work", "id": "w-1", "generation": gen},
			"kind": "react",
			"effect": map[string]any{
				"target": map[string]any{"kind": "dm", "id": "100000000000042"},
				"emoji":  "❤️", "to_message_id": "18025123456789012",
			},
		})
		assertEnvelope(t, status, code, out, "uncertain")
	})
	t.Run("denied by a stale owner", func(t *testing.T) {
		store, ts := fresh(&fakeMessaging{})
		stale := gen - 1
		code, out := postOp(t, ts, sendOp("w-1:send:d", &stale))
		assertEnvelope(t, status, code, out, "denied")
		if row, _ := store.Get(context.Background(), "w-1:send:d"); row == nil || row.PlatformRef != nil {
			t.Fatal("a fenced operation is stored rejected and never reaches the platform")
		}
	})
	t.Run("denied before any row", func(t *testing.T) {
		store, ts := fresh(&fakeMessaging{})
		code, out := postOp(t, ts, map[string]any{
			"operation_id": "x:send:0", "owner": map[string]any{"kind": "stranger", "id": "x"},
			"kind": "send", "effect": map[string]any{"text": "hi"},
		})
		assertEnvelope(t, status, code, out, "denied")
		if len(store.rows) != 0 {
			t.Fatal("an owner refused before reservation writes no row")
		}
	})
	t.Run("unavailable without a ledger", func(t *testing.T) {
		ts := v1Server(t, nil, &fakeMessaging{}, nil)
		code, out := postOp(t, ts, sendOp("w-1:send:u", &gen))
		assertEnvelope(t, status, code, out, "unavailable")
	})
	t.Run("unavailable with the ledger down", func(t *testing.T) {
		store, ts := fresh(&fakeMessaging{})
		store.down = errors.New("connection refused")
		code, out := postOp(t, ts, sendOp("w-1:send:u", &gen))
		assertEnvelope(t, status, code, out, "unavailable")
		code, out = callJSON(t, ts, http.MethodGet, "/v1/platform/operations/w-1:send:u", nil)
		assertEnvelope(t, status, code, out, "unavailable")
	})
	t.Run("read-back ok and nothing_found", func(t *testing.T) {
		_, ts := fresh(&fakeMessaging{})
		postOp(t, ts, sendOp("w-1:send:0", &gen))
		code, out := callJSON(t, ts, http.MethodGet, "/v1/platform/operations/w-1:send:0", nil)
		assertEnvelope(t, status, code, out, "ok")
		inner, _ := out["payload"].(map[string]any)
		assertEnvelope(t, status, http.StatusOK, inner, "complete")
		if part(inner, "from", "platform_ref") != "mid-1" {
			t.Fatalf("read-back payload = %v", inner)
		}
		code, out = callJSON(t, ts, http.MethodGet, "/v1/platform/operations/unknown", nil)
		assertEnvelope(t, status, code, out, "nothing_found")
		if out["payload"] != nil {
			t.Fatalf("nothing_found carries no payload: %v", out["payload"])
		}
	})
	t.Run("a kind outside the closed set is 422 and reserves nothing", func(t *testing.T) {
		store, ts := fresh(&fakeMessaging{})
		body := sendOp("w-1:poke:0", &gen)
		body["kind"] = "poke"
		code, out := postOp(t, ts, body)
		assertNoEnvelope(t, code, out, http.StatusUnprocessableEntity)
		if len(store.rows) != 0 {
			t.Fatal("an unknown kind reserves no row")
		}
	})
	t.Run("an operation_id reused for another effect is 409", func(t *testing.T) {
		_, ts := fresh(&fakeMessaging{})
		postOp(t, ts, sendOp("w-1:send:0", &gen))
		body := sendOp("w-1:send:0", &gen)
		body["effect"] = map[string]any{"target": map[string]any{"kind": "dm", "id": "100000000000042"}, "text": "different"}
		code, out := postOp(t, ts, body)
		assertNoEnvelope(t, code, out, http.StatusConflict)
	})
	t.Run("queries", func(t *testing.T) {
		_, ts := fresh(&fakeMessaging{})
		code, out := callJSON(t, ts, http.MethodPost, "/v1/platform/queries",
			map[string]any{"kind": "roster", "params": map[string]any{"conversation_id": "100000000000042"}})
		assertEnvelope(t, status, code, out, "ok")
		if part(out, "payload", "conversation_id") != "100000000000042" || part(out, "payload", "is_group") != true {
			t.Fatalf("roster payload = %v", out["payload"])
		}
		if part(out, "from", "source") != "instagram" || part(out, "when", "known_by") == nil {
			t.Fatalf("read parts = %v %v", out["from"], out["when"])
		}
		code, out = callJSON(t, ts, http.MethodPost, "/v1/platform/queries",
			map[string]any{"kind": "channels", "params": map[string]any{}})
		assertEnvelope(t, status, code, out, "unavailable")
		code, out = callJSON(t, ts, http.MethodPost, "/v1/platform/queries",
			map[string]any{"kind": "weather", "params": map[string]any{}})
		assertNoEnvelope(t, code, out, http.StatusUnprocessableEntity)
	})
	t.Run("account", func(t *testing.T) {
		_, ts := fresh(&fakeMessaging{})
		code, out := callJSON(t, ts, http.MethodGet, "/v1/platform/accounts/self", nil)
		assertEnvelope(t, status, code, out, "ok")
		if part(out, "payload", "platform") != "instagram" || part(out, "payload", "primary") != "fb-self" {
			t.Fatalf("account payload = %v", out["payload"])
		}
		code, out = callJSON(t, ts, http.MethodGet, "/v1/platform/accounts/someone-else", nil)
		assertEnvelope(t, status, code, out, "nothing_found")
		before := v1ServerAs(t, domain.Session{State: domain.StateLoggedOut}, newFakeActionStore(), &fakeMessaging{}, nil)
		code, out = callJSON(t, before, http.MethodGet, "/v1/platform/accounts/self", nil)
		assertEnvelope(t, status, code, out, "unavailable")
	})
	t.Run("status", func(t *testing.T) {
		_, ts := fresh(&fakeMessaging{})
		code, out := callJSON(t, ts, http.MethodGet, "/v1/operations/status", nil)
		assertEnvelope(t, status, code, out, "ok")
		if part(out, "payload", "ok") != true || part(out, "payload", "capture") != "capturing" {
			t.Fatalf("status payload = %v", out["payload"])
		}
	})
	t.Run("blobs", func(t *testing.T) {
		_, ts := fresh(&fakeMessaging{})
		put := func(data []byte) (int, map[string]any) {
			code, raw := call(t, ts, http.MethodPut, "/v1/platform/blobs/blob-1?pin=w-1", bytes.NewReader(data))
			var out map[string]any
			_ = json.Unmarshal(raw, &out)
			return code, out
		}
		code, out := put([]byte("blob bytes"))
		assertEnvelope(t, status, code, out, "complete")
		if part(out, "payload", "key") != "blob-1" {
			t.Fatalf("complete payload = %v", out["payload"])
		}
		code, out = put([]byte("blob bytes"))
		assertEnvelope(t, status, code, out, "complete")
		code, out = put([]byte("other bytes"))
		assertEnvelope(t, status, code, out, "rejected")
		if part(out, "payload", "error", "kind") != "conflict" {
			t.Fatalf("conflict payload = %v", out["payload"])
		}
		code, raw := call(t, ts, http.MethodPut, "/v1/platform/blobs/blob-big",
			bytes.NewReader(make([]byte, bridge.MaxAttachmentBytes+1)))
		_ = json.Unmarshal(raw, &out)
		assertEnvelope(t, status, code, out, "rejected")
		if part(out, "payload", "error", "kind") != "too_large" {
			t.Fatalf("too_large payload = %v", out["payload"])
		}
		code, raw = call(t, ts, http.MethodGet, "/v1/platform/blobs/blob-1", nil)
		if code != http.StatusOK || string(raw) != "blob bytes" {
			t.Fatalf("GET blob = %d %q", code, raw)
		}
		code, out = callJSON(t, ts, http.MethodGet, "/v1/platform/blobs/nope", nil)
		assertEnvelope(t, status, code, out, "nothing_found")
		if out["payload"] != nil {
			t.Fatalf("a blob GET envelope carries no payload: %v", out["payload"])
		}
		off := v1Server(t, newFakeActionStore(), &fakeMessaging{}, nil)
		code, raw = call(t, off, http.MethodPut, "/v1/platform/blobs/blob-1", strings.NewReader("x"))
		_ = json.Unmarshal(raw, &out)
		assertEnvelope(t, status, code, out, "unavailable")
		code, out = callJSON(t, off, http.MethodGet, "/v1/platform/blobs/blob-1", nil)
		assertEnvelope(t, status, code, out, "unavailable")
	})
}

// --- item 5: the request hash -----------------------------------------------------

// The digest of the effect today's BridgeAdapter.send builds, computed by the
// Python request_hash. A send reserved before the switch replays only if Go
// hashes the same effect to the same digest.
const pinnedEffect = `{"attachments":[],"mentions":[],"preview":null,"quote":null,"styles":[],"target":{"id":"conv-1","kind":"group","thread":""},"text":"hello"}`
const pinnedDigest = "2769bc2bbbf2d02f1cf2a8d29db8261987258a0d6873e5b1e5c81aeb54d54035"

func TestRequestHashCoversTheEffectOnly(t *testing.T) {
	var effect any
	if err := json.Unmarshal([]byte(pinnedEffect), &effect); err != nil {
		t.Fatal(err)
	}
	if got, _, _ := bridge.RequestHash(effect); got != pinnedDigest {
		t.Fatalf("RequestHash(effect) = %s, want %s", got, pinnedDigest)
	}
	// The stored hash is the effect's alone: the same effect under another
	// operation id, owner and kind stores the same digest.
	store := newFakeActionStore()
	store.gens["w-1"] = 7
	store.gens["w-2"] = 3
	ts := v1Server(t, store, &fakeMessaging{}, nil)
	for _, body := range []map[string]any{
		{"operation_id": "w-1:send:0", "owner": map[string]any{"kind": "work", "id": "w-1", "generation": 7},
			"kind": "send", "effect": json.RawMessage(pinnedEffect)},
		{"operation_id": "w-2:typing:5", "owner": map[string]any{"kind": "work", "id": "w-2", "generation": 3},
			"kind": "typing", "effect": json.RawMessage(pinnedEffect)},
	} {
		postOp(t, ts, body)
		row, _ := store.Get(context.Background(), body["operation_id"].(string))
		if row == nil {
			t.Fatalf("%s reserved no row", body["operation_id"])
		}
		if row.RequestHash != pinnedDigest {
			t.Fatalf("%s stored hash %s, want %s", body["operation_id"], row.RequestHash, pinnedDigest)
		}
	}
}

// --- behaviour carried over from the old wire ---------------------------------

// Text plus an attachment is TWO platform items — the row keeps every
// confirmed native id, and replay and read-back answer the same list.
func TestV1SendMultiPostKeepsEveryRef(t *testing.T) {
	store := newFakeActionStore()
	store.gens["w-1"] = 7
	custody := newCustody(t)
	if _, err := custody.Put("k1", bytes.NewReader([]byte("generated-bytes")),
		"image/png", "a.png", "w-1"); err != nil {
		t.Fatal(err)
	}
	ts := v1Server(t, store, &fakeMessaging{}, custody)
	gen := 7
	body := sendOp("w-1:send:0", &gen)
	body["effect"].(map[string]any)["attachments"] = []any{
		map[string]any{"key": "k1", "file_name": "a.png", "media_type": "image/png"},
	}
	wantRefs := func(where string, refs any) {
		t.Helper()
		list, _ := refs.([]any)
		if len(list) != 2 || list[0] != "mid-1" || list[1] != "mid-2" {
			t.Fatalf("%s platform_refs = %v, want [mid-1 mid-2]", where, refs)
		}
	}
	code, out := postOp(t, ts, body)
	if code != 200 || out["outcome"] != "complete" {
		t.Fatalf("multi-post send = %d %v", code, out)
	}
	wantRefs("send", part(out, "payload", "platform_refs"))
	if part(out, "from", "platform_ref") != "mid-2" {
		t.Fatalf("platform_ref = %v, want the last piece's id mid-2", part(out, "from", "platform_ref"))
	}
	code, out = postOp(t, ts, body)
	if code != 200 || out["outcome"] != "complete" {
		t.Fatalf("replay = %d %v", code, out)
	}
	wantRefs("replay", part(out, "payload", "platform_refs"))
	_, out = callJSON(t, ts, http.MethodGet, "/v1/platform/operations/w-1:send:0", nil)
	wantRefs("read-back", part(out, "payload", "payload", "platform_refs"))
}

// Replay by the same effect returns the stored row — no second send.
func TestV1ReplaySameRequest(t *testing.T) {
	store := newFakeActionStore()
	store.gens["w-1"] = 7
	msg := &fakeMessaging{sent: []domain.Sent{{ID: "mid-1", TimestampMS: 1}}}
	ts := v1Server(t, store, msg, nil)
	gen := 7
	postOp(t, ts, sendOp("w-1:send:0", &gen))
	code, out := postOp(t, ts, sendOp("w-1:send:0", &gen))
	if code != 200 || part(out, "from", "platform_ref") != "mid-1" {
		t.Fatalf("replay must return the stored row: %d %v", code, out)
	}
	if len(msg.sent) != 0 {
		t.Fatal("replay must not execute the op again")
	}
}

func TestV1LargeAttachmentReachesSendUnchanged(t *testing.T) {
	for _, media := range []struct{ mime, filename string }{
		{"image/png", "generated.png"},
		{"video/mp4", "generated.mp4"},
		{"audio/mpeg", "generated.mp3"},
		{"audio/ogg", "generated.ogg"},
		{"application/pdf", "generated.pdf"},
		{"application/octet-stream", "generated.bin"},
	} {
		t.Run(media.mime, func(t *testing.T) {
			store := newFakeActionStore()
			store.gens["w-1"] = 7
			msg := &fakeMessaging{}
			ts := v1Server(t, store, msg, newCustody(t))
			data := bytes.Repeat([]byte{0x5a}, 1319325)
			for _, tc := range []struct {
				method  string
				data    []byte
				outcome string
			}{
				{http.MethodPut, data, "complete"},
				{http.MethodPut, data, "complete"},
				{http.MethodPut, []byte("different bytes"), "rejected"},
				{http.MethodGet, nil, ""},
			} {
				code, body := call(t, ts, tc.method, "/v1/platform/blobs/generated-large", bytes.NewReader(tc.data))
				if code != http.StatusOK {
					t.Fatalf("%s blob = %d", tc.method, code)
				}
				if tc.method == http.MethodGet {
					if !bytes.Equal(body, data) {
						t.Fatal("GET must return the original bytes after a rejected overwrite")
					}
					continue
				}
				var out map[string]any
				_ = json.Unmarshal(body, &out)
				if out["outcome"] != tc.outcome {
					t.Fatalf("PUT blob outcome = %v, want %s", out["outcome"], tc.outcome)
				}
			}
			gen := 7
			body := sendOp("w-1:send:large", &gen)
			effect := body["effect"].(map[string]any)
			effect["text"] = ""
			effect["attachments"] = []any{
				map[string]any{"key": "generated-large", "media_type": media.mime, "file_name": media.filename},
			}
			for range 2 {
				code, out := postOp(t, ts, body)
				if code != http.StatusOK || out["outcome"] != "complete" {
					t.Fatalf("send = %d %v", code, out)
				}
			}
			if len(msg.uploads) != 1 || !bytes.Equal(msg.uploads[0].Data, data) ||
				msg.uploads[0].MimeType != media.mime || msg.uploads[0].Filename != media.filename {
				t.Fatal("send must hand the original bytes and metadata to the media gateway exactly once")
			}
		})
	}
}

// A valid caller traceparent becomes a span LINK on the operation's bounded
// deliver root — never a parent — and an absent or invalid header simply
// carries no link (design/instagram-adoption/bridge.md «Messaging contract»).
func TestV1ActionDeliverSpanLinksCallerContext(t *testing.T) {
	rec := recordServerSpans(t)
	store := newFakeActionStore()
	store.gens["w-1"] = 7
	ts := v1Server(t, store, &fakeMessaging{}, nil)
	gen := 7
	tp := "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"

	raw, _ := json.Marshal(sendOp("w-1:send:0", &gen))
	code, _ := call(t, ts, http.MethodPost, "/v1/platform/operations", bytes.NewReader(raw),
		"Content-Type", "application/json", "traceparent", tp)
	if code != 200 {
		t.Fatalf("send = %d", code)
	}

	var deliver sdktrace.ReadOnlySpan
	for _, s := range rec.Ended() {
		if s.Name() == "queue.deliver" {
			deliver = s
		}
	}
	if deliver == nil {
		t.Fatal("no queue.deliver span recorded")
	}
	if deliver.Parent().IsValid() {
		t.Fatal("deliver span must be a root — a parent reopens the caller's tree")
	}
	links := deliver.Links()
	if len(links) != 1 {
		t.Fatalf("deliver span links = %d, want 1", len(links))
	}
	if got := links[0].SpanContext.TraceID().String(); got != "4bf92f3577b34da6a3ce929d0e0e4736" {
		t.Fatalf("link trace id = %s", got)
	}
	if got := links[0].SpanContext.SpanID().String(); got != "00f067aa0ba902b7" {
		t.Fatalf("link span id = %s", got)
	}
}

func TestV1ActionInvalidTraceparentIsNoLinkNotAFailure(t *testing.T) {
	rec := recordServerSpans(t)
	store := newFakeActionStore()
	store.gens["w-1"] = 7
	ts := v1Server(t, store, &fakeMessaging{}, nil)
	gen := 7

	for i, tp := range []string{"", "garbage", "00-zz-00-01",
		"00-00000000000000000000000000000000-0000000000000000-01"} {
		raw, _ := json.Marshal(sendOp(fmt.Sprintf("w-1:send:x%d", i), &gen))
		headers := []string{"Content-Type", "application/json"}
		if tp != "" {
			headers = append(headers, "traceparent", tp)
		}
		_, body := call(t, ts, http.MethodPost, "/v1/platform/operations", bytes.NewReader(raw), headers...)
		var out map[string]any
		_ = json.Unmarshal(body, &out)
		if out["trace"] != "" {
			t.Fatalf("an absent or invalid traceparent is trace \"\", got %v", out["trace"])
		}
	}
	for _, s := range rec.Ended() {
		if s.Name() == "queue.deliver" && len(s.Links()) != 0 {
			t.Fatalf("invalid/absent traceparent must carry no link, got %v", s.Links())
		}
	}
}
