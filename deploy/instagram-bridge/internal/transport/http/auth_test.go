package httptransport

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestRequireTokenProtectsHealthToo(t *testing.T) {
	h := RequireToken("s3cret")(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusNoContent)
	}))
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/health", nil))
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("health without token = %d, want 401", rec.Code)
	}
}

func TestRequireTokenRejectsMissingAndWrong(t *testing.T) {
	h := RequireToken("s3cret")(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusNoContent)
	}))
	for _, header := range []string{"", "Bearer wrong", "s3cret"} {
		req := httptest.NewRequest(http.MethodGet, "/events", nil)
		if header != "" {
			req.Header.Set("Authorization", header)
		}
		rec := httptest.NewRecorder()
		h.ServeHTTP(rec, req)
		if rec.Code != http.StatusUnauthorized {
			t.Fatalf("Authorization %q = %d, want 401", header, rec.Code)
		}
	}
}

func TestRequireTokenAcceptsTheBearer(t *testing.T) {
	h := RequireToken("s3cret")(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusNoContent)
	}))
	for _, path := range []string{"/events", "/health"} {
		req := httptest.NewRequest(http.MethodGet, path, nil)
		req.Header.Set("Authorization", "Bearer s3cret")
		rec := httptest.NewRecorder()
		h.ServeHTTP(rec, req)
		if rec.Code != http.StatusNoContent {
			t.Fatalf("authorized %s = %d, want 204", path, rec.Code)
		}
	}
}

func TestRequireTokenFailsClosedWithoutConfiguration(t *testing.T) {
	h := RequireToken("")(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusNoContent)
	}))
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/health", nil))
	if rec.Code != http.StatusServiceUnavailable {
		t.Fatalf("unconfigured = %d, want 503", rec.Code)
	}
}
