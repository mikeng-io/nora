// instagram-bridge — Nora's Instagram transport.
//
// A Go daemon that owns the Instagram wire (go.mau.fi/mautrix-meta's
// `instameow`, used standalone: no Matrix homeserver, no appservice, no
// bridgev2 runtime) and exposes a small HTTP + SSE surface to the Nora Python
// process. The wire contract is pinned in
// design/instagram-adoption/03-transport-contract.md — every route, every
// field, the status mapping and the four inbound media shapes are defined
// there, not here.
//
// It is the third instance of a settled pattern: signal-cli, then
// whatsapp-bridge, now this. Same posture as both — private `nora` network,
// publishes NOTHING, holds the full account credential so that the narrower
// its network namespace, the smaller the blast radius.
//
// The two things that differ from whatsapp-bridge:
//
//   - Go rather than Node, because the only usable Instagram client is Go.
//   - A COOKIE session rather than a QR pairing. A password login originating
//     from a server is the highest-risk first contact an account can make, and
//     instameow's native login explicitly cannot drive a CAPTCHA. The operator
//     warms the account in a browser on the normal residential connection and
//     hands the bridge the resulting session.
//
// THE RULE THAT SHAPES THE WHOLE PROGRAM: an Instagram challenge is a normal
// operating state, not a crash. /health answers 200 while the HTTP server
// serves and reports the session as a FIELD. Nothing below exits non-zero
// because Instagram asked for a captcha — if it did, a routine event would
// present as a dead container and the operator would debug the wrong thing.
//
// Usage:
//
//	instagram-bridge --port 18082 --session /session
package main

// The composition root: flags, logging, tracing, and wiring. Every behaviour
// lives in internal/ (design/instagram-adoption/16 §Layers).

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"log"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"sync"
	"syscall"
	"time"

	"instagram-bridge/internal/domain"
	gateway "instagram-bridge/internal/gateway/instameow"
	"instagram-bridge/internal/observability"
	"instagram-bridge/internal/session"
	"instagram-bridge/internal/stream"
	httptransport "instagram-bridge/internal/transport/http"

	"github.com/rs/zerolog"
)

func main() {
	var (
		port       = flag.Int("port", envInt("IG_BRIDGE_PORT", 18082), "HTTP port (private nora network only)")
		sessionDir = flag.String("session", envStr("IG_SESSION_DIR", "/session"), "directory holding cookies + reconnection state")
		verbose    = flag.Bool("v", false, "log the protocol client's own output")
		token      = envStr("NORA_BRIDGE_TOKEN", "")
	)
	flag.Parse()
	observability.SetupLogging(*verbose)

	log.SetFlags(log.LstdFlags | log.LUTC)
	log.SetPrefix("[bridge] ")

	if err := os.MkdirAll(*sessionDir, 0o700); err != nil {
		slog.Error("cannot create session dir", "dir", *sessionDir, "error", err)
		os.Exit(2)
	}

	if token == "" {
		slog.Error("NORA_BRIDGE_TOKEN is not set; refusing to start an unauthenticated bridge")
		os.Exit(2)
	}
	store := session.Store{Dir: *sessionDir}
	self, _ := store.LoadSelf()
	commentPolicy, problems := commentPolicyFromEnv(os.Getenv)
	for _, p := range problems {
		slog.Error("comment writes disabled: invalid configuration", "problem", p)
	}
	slog.Info("comment writes", "enabled", commentPolicy.WritesEnabled,
		"daily_cap", commentPolicy.DailyCap, "min_interval", commentPolicy.MinInterval.String())
	docs, docProblems := queryDocsFromEnv(os.Getenv)
	for _, p := range docProblems {
		slog.Error("graphql read disabled: invalid configuration", "problem", p)
	}
	// Whether each is set, never the value: an operator reading this line
	// learns which read will answer 501.
	slog.Info("graphql documents", "story_configured", docs.Story != "",
		"timeline_configured", docs.Timeline != "")
	gw, handler := wire(gateway.Options{
		Store:  store,
		State:  domain.StateLoggedOut,
		Detail: "starting",
		Self:   self,
		Docs:   docs,
	}, stream.NewHub(), time.Now(), commentPolicy)

	// instameow's own log output, as JSON on the same stream and with slog's
	// field names, so an operator reads one format.
	zerolog.MessageFieldName = "msg"
	zerolog.TimeFieldFormat = time.RFC3339Nano
	libLog := zerolog.New(os.Stdout).
		Level(zerolog.ErrorLevel).With().Timestamp().Str("logger", "instameow").Logger()
	if *verbose {
		libLog = libLog.Level(zerolog.DebugLevel)
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	shutdownTracing, err := observability.SetupTracing(ctx)
	if err != nil {
		// Tracing is observability, not function: a bridge that cannot export
		// spans still serves messages. Logged loudly and carried on without it.
		slog.Error("tracing setup failed; continuing untraced", "error", err)
		shutdownTracing = func(context.Context) error { return nil }
	}

	srv := &http.Server{
		Addr:    fmt.Sprintf(":%d", *port),
		Handler: httptransport.RequireToken(token)(handler),
		// No write timeout: /events is a long-lived SSE stream and any write
		// deadline would sever it on a quiet room. Writes are bounded per frame
		// (transport/http's streamWriteTimeout), and each request body has its own
		// read deadline (readJSON's bodyReadTimeout).
		ReadHeaderTimeout: 10 * time.Second,
	}

	var wg sync.WaitGroup
	// The supervisor runs even with no credential on disk. It is what READS
	// the session directory, so starting it unconditionally is what lets an
	// operator drop a `Copy as cURL` paste into the volume and have the bridge
	// pick it up within a backoff cycle, instead of needing a restart to
	// discover a file that is already there.
	wg.Add(1)
	go func() {
		defer wg.Done()
		gw.Supervise(ctx, libLog)
	}()
	wg.Add(1)
	go func() {
		defer wg.Done()
		gw.Heartbeat(ctx)
	}()

	go func() {
		slog.Info("listening", "port", *port, "session_dir", *sessionDir)
		if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			slog.Error("http server failed", "error", err)
			stop()
		}
	}()

	<-ctx.Done()
	slog.Info("shutting down")
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	_ = srv.Shutdown(shutdownCtx)
	// Its OWN budget. srv.Shutdown waits for open connections and does not
	// close them, and Nora keeps /events open, so it can spend its whole
	// deadline; sharing it would leave the span flush nothing, dropping the
	// traces from the moments before a restart.
	flushCtx, cancelFlush := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancelFlush()
	_ = shutdownTracing(flushCtx)
	gw.Shutdown()
	wg.Wait()
}

func envStr(name, fallback string) string {
	if v := os.Getenv(name); v != "" {
		return v
	}
	return fallback
}

func envInt(name string, fallback int) int {
	if v := os.Getenv(name); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			return n
		}
	}
	return fallback
}
