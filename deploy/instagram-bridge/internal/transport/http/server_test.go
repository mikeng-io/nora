package httptransport

// The HTTP surface against the use cases over FAKE ports: one status per
// domain error kind, and the two stage bodies. The whole wired path (real
// gateway over a measured-wire fake) is exercised in cmd/instagram-bridge.

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
func (f failingPorts) SearchAccounts(context.Context, string) ([]domain.AccountSummary, error) {
	return nil, f.err
}
func (f failingPorts) Post(context.Context, domain.Shortcode) (domain.Post, error) {
	return domain.Post{}, f.err
}

type noEvents struct{}

func (noEvents) Subscribe() (cortez []byte, [][]byte, int) { return make(cortez []byte), nil, 0 }
func (noEvents) Unsubscribe(cortez []byte)                 {}
func (noEvents) Stats() (int, int, int)                  { return 0, 0, 0 }

func handler(snap domain.Session, err error) http.Handler {
	p := failingPorts{err: err}
	return New(app.New(fakeSession{snap}, p, p), noEvents{}, time.Now()).Routes()
}

var authenticated = domain.Session{State: domain.StateAuthenticated, Connected: true, HasClient: true}

func get(h http.Handler, path string) *httptest.ResponseRecorder {
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, path, nil))
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

// ONE status per kind. This is what the adapter's retry decision reads:
// 409 must never collapse into 503, and 503 must never be claimed for a
// failure nobody has a name for.
func TestEveryDomainKindMapsToItsOneStatus(t *testing.T) {
	cause := errors.New("what the gateway said")
	for _, tc := range []struct {
		err  error
		want int
	}{
		{domain.Classify(domain.ErrInvalidID, cause), http.StatusBadRequest},
		{domain.Classify(domain.ErrNotFound, cause), http.StatusNotFound},
		{domain.Classify(domain.ErrNeedsHuman, cause), http.StatusConflict},
		{domain.Classify(domain.ErrTooLarge, cause), http.StatusRequestEntityTooLarge},
		{domain.Classify(domain.ErrUnavailable, cause), http.StatusServiceUnavailable},
		{domain.Classify(domain.ErrUpstreamRejected, cause), http.StatusInternalServerError},
		{domain.Classify(domain.ErrUnsupported, cause), http.StatusNotImplemented},
		// Fail closed: no kind is 500, never 503.
		{cause, http.StatusInternalServerError},
	} {
		rec := get(handler(authenticated, tc.err), "/accounts/221731317")
		if rec.Code != tc.want {
			t.Errorf("kind %v answered %d, want %d", domain.KindOf(tc.err), rec.Code, tc.want)
		}
		b := body(t, rec)
		if b["ok"] != false || b["error"] != "what the gateway said" {
			t.Errorf("kind %v: body = %v, want {ok:false, error:<the gateway's text>}", domain.KindOf(tc.err), b)
		}
	}
}

// The session gate answers with the session state and its detail, not an
// error string: what the caller needs is which human action is due.
func TestTheSessionGateAnswersWithTheState(t *testing.T) {
	h := handler(domain.Session{State: domain.StateCheckpointed, Detail: "challenge required"}, nil)
	rec := get(h, "/accounts/nka_bpumvg")
	if rec.Code != http.StatusConflict {
		t.Fatalf("answered %d, want 409", rec.Code)
	}
	b := body(t, rec)
	if b["error"] != "checkpointed" || b["detail"] != "challenge required" || b["ok"] != false {
		t.Errorf("body = %v", b)
	}
}

// A room's route lookup failing against Instagram has its own body, at the
// status its kind maps to — except "not connected", which is a plain 503.
func TestAFailedThreadLookupHasItsOwnBody(t *testing.T) {
	checkpoint := domain.Classify(domain.ErrNeedsHuman, errors.New("checkpoint_required"))
	rec := get(handler(authenticated, checkpoint), "/threads/1009543911792316/members")
	if rec.Code != http.StatusConflict {
		t.Fatalf("answered %d, want 409", rec.Code)
	}
	if b := body(t, rec); b["error"] != "thread_lookup_failed" || b["detail"] != "checkpoint_required" {
		t.Errorf("body = %v", b)
	}

	down := domain.Classify(domain.ErrUnavailable, errors.New("instagram session is not connected"))
	rec = get(handler(authenticated, down), "/threads/1009543911792316/members")
	if rec.Code != http.StatusServiceUnavailable {
		t.Fatalf("answered %d, want 503", rec.Code)
	}
	if b := body(t, rec); b["error"] != "instagram session is not connected" {
		t.Errorf("body = %v", b)
	}
}

// The handlers are thin, but they still own request validation: a missing
// required field is 400 before any use case runs.
func TestRequestValidationIsTheHandlers(t *testing.T) {
	h := handler(authenticated, errors.New("no use case should run"))
	for route, payload := range map[string]string{
		"/send":       `{"room_id":"1009543911792316"}`,
		"/send-media": `{"room_id":"1009543911792316","data_base64":"!!!!"}`,
		"/react":      `{"room_id":"1009543911792316","emoji":"x"}`,
		"/unsend":     `{"room_id":"1009543911792316"}`,
	} {
		rec := httptest.NewRecorder()
		h.ServeHTTP(rec, httptest.NewRequest(http.MethodPost, route, strings.NewReader(payload)))
		if rec.Code != http.StatusBadRequest {
			t.Errorf("%s answered %d, want 400", route, rec.Code)
		}
	}
}
