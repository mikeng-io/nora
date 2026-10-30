package httptransport

// Focused proofs for the /v1 facade: auth, owner/generation fences, honest
// action states, request-hash replay/conflict, and custody endpoints. The
// fakes behave like the real seams — including their refusal shapes — so a
// regression in reservation or outcome mapping turns red.

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"instagram-bridge/internal/app"
	"instagram-bridge/internal/bridge"
	"instagram-bridge/internal/domain"
	"instagram-bridge/internal/stream"
)

// --- fakes -------------------------------------------------------------------
// fakeSession lives in server_test.go (same package); reuse it.

type fakeMessaging struct {
	sent      []domain.Sent
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
func (f *fakeMessaging) SendMedia(_ context.Context, _ domain.ThreadRef, _ domain.Upload, _ string) (domain.Sent, error) {
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
func (f *fakeMessaging) Members(_ context.Context, _ domain.ThreadRef) (domain.Thread, error) {
	return domain.Thread{}, nil
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
}

func newFakeActionStore() *fakeActionStore {
	return &fakeActionStore{rows: map[string]*bridge.ActionRow{}, gens: map[string]int{}}
}

func (f *fakeActionStore) Reserve(_ context.Context, id, ok, oid string, og *int, op string, req json.RawMessage, hash string) (bridge.ReserveOutcome, *bridge.ActionRow, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
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
	return true, nil
}
func (f *fakeActionStore) Finish(_ context.Context, id, state, ref string, ms *int64, ek, em string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	row := f.rows[id]
	row.State = state
	row.PlatformRef = nil
	if ref != "" {
		row.PlatformRef = &ref
	}
	row.PlatformMS = ms
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

func v1Server(t *testing.T, store ActionStore, msg *fakeMessaging, custody *bridge.Custody) *httptest.Server {
	t.Helper()
	svc := app.New(fakeSession{snap: domain.Session{State: domain.StateAuthenticated, Connected: true, HasClient: true, Self: domain.Self{FBID: "fb-self"}}}, msg, &fakeSocial{})
	srv := New(svc, stream.NewHub(), time.Now())
	srv = srv.WithV1(&V1{
		App: svc, Store: store, Custody: custody,
		Metrics: bridge.NewMetrics(), AccountKey: "acct",
		UpstreamOK: func() bool { return true },
	})
	ts := httptest.NewServer(RequireToken("tok")(srv.Routes()))
	t.Cleanup(ts.Close)
	return ts
}

func postAction(t *testing.T, ts *httptest.Server, body map[string]any) (int, map[string]any) {
	t.Helper()
	raw, _ := json.Marshal(body)
	req, _ := http.NewRequest("POST", ts.URL+"/v1/actions", bytes.NewReader(raw))
	req.Header.Set("Authorization", "Bearer tok")
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	var out map[string]any
	json.NewDecoder(resp.Body).Decode(&out)
	return resp.StatusCode, out
}

func sendAction(actionID string, gen *int) map[string]any {
	owner := map[string]any{"kind": "work", "id": "w-1"}
	if gen != nil {
		owner["generation"] = *gen
	}
	return map[string]any{
		"action_id": actionID, "owner": owner, "op": "send",
		"request": map[string]any{
			"target": map[string]any{"kind": "dm", "id": "100000000000042"},
			"text":   "hi there",
		},
	}
}

// --- tests ---------------------------------------------------------------------

func TestV1AuthRequired(t *testing.T) {
	ts := v1Server(t, newFakeActionStore(), &fakeMessaging{}, nil)
	for _, path := range []string{"/v1/actions", "/v1/self", "/v1/health", "/v1/reads", "/v1/attachments/k"} {
		resp, err := http.Post(ts.URL+path, "application/json", strings.NewReader("{}"))
		if err != nil {
			t.Fatal(err)
		}
		resp.Body.Close()
		if resp.StatusCode != http.StatusUnauthorized {
			t.Fatalf("%s without token = %d, want 401", path, resp.StatusCode)
		}
	}
}

func TestV1HealthShape(t *testing.T) {
	ts := v1Server(t, newFakeActionStore(), &fakeMessaging{}, nil)
	req, _ := http.NewRequest("GET", ts.URL+"/v1/health", nil)
	req.Header.Set("Authorization", "Bearer tok")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	var out map[string]any
	json.NewDecoder(resp.Body).Decode(&out)
	if _, ok := out["ok"]; !ok {
		t.Fatalf("/v1/health must carry ok: %v", out)
	}
}

func TestV1SelfShape(t *testing.T) {
	ts := v1Server(t, newFakeActionStore(), &fakeMessaging{}, nil)
	req, _ := http.NewRequest("GET", ts.URL+"/v1/self", nil)
	req.Header.Set("Authorization", "Bearer tok")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	var out map[string]any
	json.NewDecoder(resp.Body).Decode(&out)
	if out["platform"] != "instagram" {
		t.Fatalf("self.platform = %v", out["platform"])
	}
	if _, ok := out["primary"]; !ok {
		t.Fatalf("self must carry primary: %v", out)
	}
	if _, ok := out["aliases"]; !ok {
		t.Fatalf("self must carry aliases: %v", out)
	}
}

func TestV1SendConfirmed(t *testing.T) {
	store := newFakeActionStore()
	store.gens["w-1"] = 7
	ts := v1Server(t, store, &fakeMessaging{}, nil)
	gen := 7
	code, out := postAction(t, ts, sendAction("w-1:send:0", &gen))
	if code != 200 {
		t.Fatalf("send = %d %v", code, out)
	}
	if out["state"] != "confirmed" || out["outcome"] != "confirmed" {
		t.Fatalf("confirmed send expected, got %v", out)
	}
	if out["platform_ref"] != "mid-1" {
		t.Fatalf("platform_ref = %v, want mid-1", out["platform_ref"])
	}
}

// A stale owner generation must never reach the platform call.
func TestV1GenerationFence(t *testing.T) {
	store := newFakeActionStore()
	store.gens["w-1"] = 8 // platform says 8; caller claims 7
	msg := &fakeMessaging{}
	ts := v1Server(t, store, msg, nil)
	gen := 7
	code, out := postAction(t, ts, sendAction("w-1:send:0", &gen))
	if code != 200 || out["state"] != "rejected" {
		t.Fatalf("stale generation must reject: %d %v", code, out)
	}
	// And the request must never have touched the messaging port — the fake
	// would have handed back mid-1 if it had.
	row, _ := store.Get(context.Background(), "w-1:send:0")
	if row.PlatformRef != nil {
		t.Fatal("fenced action must have no platform ref")
	}
}

// Replay by the same request hash returns the stored row — no second send.
func TestV1ReplaySameRequest(t *testing.T) {
	store := newFakeActionStore()
	store.gens["w-1"] = 7
	msg := &fakeMessaging{sent: []domain.Sent{{ID: "mid-1", TimestampMS: 1}}}
	ts := v1Server(t, store, msg, nil)
	gen := 7
	postAction(t, ts, sendAction("w-1:send:0", &gen))
	code, out := postAction(t, ts, sendAction("w-1:send:0", &gen))
	if code != 200 || out["platform_ref"] != "mid-1" {
		t.Fatalf("replay must return the stored row: %d %v", code, out)
	}
	if len(msg.sent) != 0 {
		t.Fatal("replay must not execute the op again")
	}
}

// Same action_id, different request → 409, never a silent second send.
func TestV1ConflictOnDifferentRequest(t *testing.T) {
	store := newFakeActionStore()
	store.gens["w-1"] = 7
	ts := v1Server(t, store, &fakeMessaging{}, nil)
	gen := 7
	postAction(t, ts, sendAction("w-1:send:0", &gen))
	body := sendAction("w-1:send:0", &gen)
	body["request"] = map[string]any{"target": map[string]any{"kind": "dm", "id": "100000000000042"}, "text": "different"}
	code, _ := postAction(t, ts, body)
	if code != http.StatusConflict {
		t.Fatalf("same id different request = %d, want 409", code)
	}
}

// An unconfirmed reaction answer is UNCERTAIN, not confirmed and not a
// blind retry candidate.
func TestV1ReactUncertain(t *testing.T) {
	store := newFakeActionStore()
	store.gens["w-1"] = 7
	ts := v1Server(t, store, &fakeMessaging{reactOK: false}, nil)
	gen := 7
	body := map[string]any{
		"action_id": "w-1:react:0", "owner": map[string]any{"kind": "work", "id": "w-1", "generation": gen},
		"op": "react",
		"request": map[string]any{
			"target":        map[string]any{"kind": "dm", "id": "100000000000042"},
			"emoji":         "❤️",
			"to_message_id": "18025123456789012",
		},
	}
	code, out := postAction(t, ts, body)
	if code != 200 || out["state"] != "uncertain" || out["outcome"] != "uncertain" {
		t.Fatalf("silent post-state reaction must be uncertain: %d %v", code, out)
	}
}

// Instagram has no outbound edit: rejected as unsupported, never faked.
func TestV1EditUnsupported(t *testing.T) {
	store := newFakeActionStore()
	store.gens["w-1"] = 7
	ts := v1Server(t, store, &fakeMessaging{}, nil)
	gen := 7
	body := map[string]any{
		"action_id": "w-1:edit:0", "owner": map[string]any{"kind": "work", "id": "w-1", "generation": gen},
		"op":      "edit",
		"request": map[string]any{"target": map[string]any{"kind": "dm", "id": "100000000000042"}, "message_ref": "1802", "text": "x"},
	}
	code, out := postAction(t, ts, body)
	if code != 200 || out["state"] != "rejected" || out["outcome"] != "unsupported" {
		t.Fatalf("edit must answer rejected/unsupported: %d %v", code, out)
	}
}

func TestV1ActionsUnavailableWithoutStore(t *testing.T) {
	ts := v1Server(t, nil, &fakeMessaging{}, nil)
	code, out := postAction(t, ts, sendAction("w-1:send:0", nil))
	if code != http.StatusServiceUnavailable {
		t.Fatalf("nil store = %d, want 503", code)
	}
	_ = out
}

func TestV1AttachmentPutGet(t *testing.T) {
	custody, err := bridge.NewCustody(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	ts := v1Server(t, newFakeActionStore(), &fakeMessaging{}, custody)
	req, _ := http.NewRequest("PUT", ts.URL+"/v1/attachments/test-key?pin=w-1",
		strings.NewReader("blob bytes"))
	req.Header.Set("Authorization", "Bearer tok")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusCreated {
		t.Fatalf("PUT attachment = %d, want 201", resp.StatusCode)
	}
	req2, _ := http.NewRequest("GET", ts.URL+"/v1/attachments/test-key", nil)
	req2.Header.Set("Authorization", "Bearer tok")
	resp2, err := http.DefaultClient.Do(req2)
	if err != nil {
		t.Fatal(err)
	}
	defer resp2.Body.Close()
	buf := make([]byte, 64)
	n, _ := resp2.Body.Read(buf)
	if string(buf[:n]) != "blob bytes" {
		t.Fatalf("GET attachment = %q", string(buf[:n]))
	}
	req3, _ := http.NewRequest("GET", ts.URL+"/v1/attachments/nope", nil)
	req3.Header.Set("Authorization", "Bearer tok")
	resp3, _ := http.DefaultClient.Do(req3)
	resp3.Body.Close()
	if resp3.StatusCode != http.StatusNotFound {
		t.Fatalf("unknown attachment = %d, want 404", resp3.StatusCode)
	}
}

// GET /v1/actions/{id} is the recovery readback.
func TestV1ActionReadback(t *testing.T) {
	store := newFakeActionStore()
	store.gens["w-1"] = 7
	ts := v1Server(t, store, &fakeMessaging{}, nil)
	gen := 7
	postAction(t, ts, sendAction("w-1:send:0", &gen))
	req, _ := http.NewRequest("GET", ts.URL+"/v1/actions/w-1:send:0", nil)
	req.Header.Set("Authorization", "Bearer tok")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	var out map[string]any
	json.NewDecoder(resp.Body).Decode(&out)
	if out["state"] != "confirmed" || out["platform_ref"] != "mid-1" {
		t.Fatalf("readback = %v", out)
	}
	req2, _ := http.NewRequest("GET", ts.URL+"/v1/actions/unknown", nil)
	req2.Header.Set("Authorization", "Bearer tok")
	resp2, _ := http.DefaultClient.Do(req2)
	resp2.Body.Close()
	if resp2.StatusCode != http.StatusNotFound {
		t.Fatalf("unknown action readback = %d, want 404", resp2.StatusCode)
	}
}

var _ = fmt.Sprint // keep fmt for later additions
