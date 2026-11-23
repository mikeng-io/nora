package httptransport

// How a delivery's result is reported when a dependency is missing, a kind is
// malformed, or the ledger fails around the terminal write.

import (
	"errors"
	"net/http"
	"testing"

	"instagram-bridge/internal/domain"
)

func TestReadyNeedsEveryDeliveryDependency(t *testing.T) {
	noClient := v1Session
	noClient.HasClient = false
	noAccount := v1Session
	noAccount.Self = domain.Self{}
	for name, snap := range map[string]domain.Session{"no messaging client": noClient, "no account id": noAccount} {
		ts := v1ServerAs(t, snap, newFakeActionStore(), &fakeMessaging{}, newCustody(t))
		if code, out := callJSON(t, ts, http.MethodGet, "/ready", nil); code != http.StatusServiceUnavailable {
			t.Errorf("/ready with %s = %d, want 503 (%v)", name, code, out)
		}
		_, out := callJSON(t, ts, http.MethodGet, "/v1/operations/status", nil)
		if part(out, "payload", "ok") != false || part(out, "payload", "detail") == nil {
			t.Errorf("status with %s = %v, want ok false with a detail", name, out["payload"])
		}
	}
	ts := v1Server(t, newFakeActionStore(), &fakeMessaging{}, newCustody(t))
	if code, _ := callJSON(t, ts, http.MethodGet, "/ready", nil); code != http.StatusOK {
		t.Errorf("/ready with every dependency = %d, want 200", code)
	}
}

func TestAMalformedKindIs422(t *testing.T) {
	gen := 7
	store := newFakeActionStore()
	store.gens["w-1"] = gen
	ts := v1Server(t, store, &fakeMessaging{}, newCustody(t))
	for _, kind := range []any{[]any{}, map[string]any{}, 1} {
		body := sendOp("w-1:send:m", &gen)
		body["kind"] = kind
		if code, out := postOp(t, ts, body); code != http.StatusUnprocessableEntity || out["outcome"] != nil {
			t.Errorf("operation kind %v = %d %v, want a bare 422", kind, code, out)
		}
		code, out := callJSON(t, ts, http.MethodPost, "/v1/platform/queries", map[string]any{"kind": kind, "params": map[string]any{}})
		if code != http.StatusUnprocessableEntity || out["outcome"] != nil {
			t.Errorf("query kind %v = %d %v, want a bare 422", kind, code, out)
		}
	}
	if len(store.rows) != 0 {
		t.Fatal("a malformed kind reserves no row")
	}
}

func TestARecordedSendIsCompleteWhenItsReadBackFails(t *testing.T) {
	status := contractStatus(t)
	gen := 7
	store := newFakeActionStore()
	store.gens["w-1"] = gen
	store.failGet = errors.New("generated read failure")
	ts := v1Server(t, store, &fakeMessaging{}, newCustody(t))
	code, out := postOp(t, ts, sendOp("w-1:send:f", &gen))
	assertEnvelope(t, status, code, out, "complete")
	if part(out, "from", "platform_ref") != "mid-1" || part(out, "payload", "platform_ms") != float64(100) {
		t.Fatalf("the envelope carries what the terminal write persisted: %v", out)
	}
	if refs, _ := part(out, "payload", "platform_refs").([]any); len(refs) != 1 || refs[0] != "mid-1" {
		t.Fatalf("platform_refs = %v", part(out, "payload", "platform_refs"))
	}
}

func TestAFailedTerminalWriteIsUncertain(t *testing.T) {
	status := contractStatus(t)
	gen := 7
	store := newFakeActionStore()
	store.gens["w-1"] = gen
	store.failFinish = errors.New("generated write failure")
	ts := v1Server(t, store, &fakeMessaging{}, newCustody(t))
	code, out := postOp(t, ts, sendOp("w-1:send:w", &gen))
	assertEnvelope(t, status, code, out, "uncertain")
}
