package main

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestRejectClientOnwardProxyAndPorts(t *testing.T) {
	h := handler()
	for _, target := range []string{"http://example.invalid:22/", "http://example.invalid/"} {
		r := httptest.NewRequest(http.MethodGet, target, nil)
		if target == "http://example.invalid/" {
			r.Header.Set("X-Upstream-Https-Proxy", "http://user:secret@127.0.0.1:80")
		}
		w := httptest.NewRecorder()
		h.ServeHTTP(w, r)
		if w.Code != http.StatusForbidden {
			t.Fatalf("expected policy refusal, got %d", w.Code)
		}
	}
}
