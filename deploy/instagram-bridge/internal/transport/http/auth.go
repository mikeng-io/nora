package httptransport

import (
	"crypto/subtle"
	"net/http"
)

// RequireToken authenticates every route, including /health: that response
// carries the bridge's own account (fbid, igid, username), which is still an
// identifier a co-networked container has no business reading. The compose
// healthcheck and Nora's identity probe send the same bearer.
//
// The bridge is on a private Docker network, but membership in that network is
// not caller authentication: any co-networked container can reach the account
// routes. An empty token is a configuration error and fails CLOSED: a bridge
// with no token must not become an unauthenticated bridge.
func RequireToken(token string) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		if token == "" {
			return http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				http.Error(w, "bridge token is not configured", http.StatusServiceUnavailable)
			})
		}
		want := []byte("Bearer " + token)
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			got := []byte(r.Header.Get("Authorization"))
			if len(got) != len(want) || subtle.ConstantTimeCompare(got, want) != 1 {
				w.Header().Set("WWW-Authenticate", "Bearer")
				http.Error(w, "unauthorized", http.StatusUnauthorized)
				return
			}
			next.ServeHTTP(w, r)
		})
	}
}
