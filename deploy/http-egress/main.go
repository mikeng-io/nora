// Nora's policy wiring; protocol, resolution and dialing belong to Smokescreen.
package main

import (
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"time"

	"github.com/stripe/smokescreen/pkg/smokescreen"
	"github.com/stripe/smokescreen/pkg/smokescreen/conntrack"
)

func handler() http.Handler {
	c := smokescreen.NewConfig()
	// Upstream logs include signed URLs and client headers. Do not retain them.
	c.Log.SetOutput(io.Discard)
	c.ConnectTimeout = 10 * time.Second
	c.IdleTimeout = 5 * time.Minute
	c.ReadHeaderTimeout = 15 * time.Second
	c.ReadTimeout = 0
	c.WriteTimeout = 0 // active streams are bounded by inactivity, not total length
	if err := c.Validate(); err != nil {
		panic("invalid egress configuration")
	}
	if err := c.InitializeSelfConnectionDetection(); err != nil {
		panic("egress interface discovery failed")
	}
	c.ConnTracker = conntrack.NewTracker(c.IdleTimeout, c.MetricsClient, c.Log, c.ShuttingDown, nil)
	proxy := smokescreen.BuildProxy(c)
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		authority := r.URL.Host
		if r.Method == http.MethodConnect {
			authority = r.Host
		}
		_, port, err := net.SplitHostPort(authority)
		if err != nil {
			port = "80"
		}
		if (port != "80" && port != "443") || r.Header.Get("X-Upstream-Https-Proxy") != "" {
			http.Error(w, "egress request refused", http.StatusForbidden)
			return
		}
		r.Header.Del("X-Upstream-Https-Proxy")
		proxy.ServeHTTP(w, r)
	})
}

func main() {
	// goproxy otherwise inherits onward proxies from the process environment.
	for _, key := range []string{"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
		"http_proxy", "https_proxy", "all_proxy", "NO_PROXY", "no_proxy"} {
		os.Unsetenv(key)
	}
	log.SetOutput(io.Discard)
	server := &http.Server{Addr: ":4750", Handler: handler(), ReadHeaderTimeout: 15 * time.Second, IdleTimeout: 5 * time.Minute, ErrorLog: log.New(io.Discard, "", 0)}
	// Fixed text only: request authorities, credentials and query strings never enter logs.
	if server.ListenAndServe() != nil {
		os.Stderr.WriteString("http-egress stopped\n")
		os.Exit(1)
	}
}
