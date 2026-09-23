// Package observability is the bridge's tracer provider, W3C propagation,
// server-span middleware and trace-joined JSON logging
// (design/instagram-adoption/16 §Tracing and §Logging, 17 P1).
//
// # Where spans go
//
// OTLP/HTTP to the collector Nora already exports to. The collector sends
// traces carrying a model call to Langfuse and everything else to Jaeger, so
// this process needs no collector change and makes no routing decision of its
// own. With no OTLP endpoint in the environment the provider stays the global
// no-op, which is what the unit tests and a bare `go run` get.
//
// # What a trace contains
//
//   - one SERVER span per request (TraceHTTP), named by its route PATTERN
//     (`GET /accounts/{igid}`), never the raw path, and continuing the caller's
//     trace when Nora sends a W3C `traceparent`;
//   - one CLIENT span per call to Instagram, opened by the gateway's two call
//     helpers (internal/gateway/instameow). A GraphQL document that fails
//     inside an HTTP 200 marks its span ERROR. That shape hid a real bug for a
//     day on 2026-09-22.
//
// `/health` (polled by the container healthcheck) and `/events` (an SSE stream
// that stays open for hours) are not traced: one is noise, the other would be
// a single span the length of a session.
//
// # What never goes into a span or a log line
//
// Message text, captions, cookies, tokens, and signed CDN URLs, which grant
// access to whoever holds one. Ids (igid, thread id, message id), routes,
// statuses and error codes are allowed: this is operator-only infrastructure
// and they are what debugging needs.
//
// Nothing here propagates OUR trace context to Instagram. instameow issues
// its requests through its own HTTP client, which carries no OpenTelemetry
// transport, and that is deliberate: a `traceparent` header on a request to
// instagram.com would be an identifier of Nora's infrastructure handed to a
// third party.
package observability

import (
	"context"
	"log/slog"
	"net/http"
	"os"
	"strings"

	"go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/trace"
)

// ServiceName is `service.name` on every span, and the tracer's name.
const ServiceName = "instagram-bridge"

// Tracer is read from the global provider on every call rather than cached,
// so a test that installs a recording provider sees every span.
func Tracer() trace.Tracer { return otel.GetTracerProvider().Tracer(ServiceName) }

// SetupTracing installs the W3C propagator unconditionally and, when an OTLP
// endpoint is configured, a batching provider that exports to it. The
// returned function flushes on shutdown.
func SetupTracing(ctx context.Context) (func(context.Context) error, error) {
	otel.SetTextMapPropagator(propagation.NewCompositeTextMapPropagator(
		propagation.TraceContext{}, propagation.Baggage{},
	))
	if os.Getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT") == "" &&
		os.Getenv("OTEL_EXPORTER_OTLP_ENDPOINT") == "" {
		slog.InfoContext(ctx, "tracing disabled: no OTLP endpoint configured")
		return func(context.Context) error { return nil }, nil
	}
	exporter, err := otlptracehttp.New(ctx)
	if err != nil {
		return nil, err
	}
	// The default name first, so OTEL_SERVICE_NAME can override it.
	res, err := resource.New(ctx,
		resource.WithAttributes(attribute.String("service.name", ServiceName)),
		resource.WithFromEnv(),
		resource.WithTelemetrySDK(),
	)
	if err != nil {
		return nil, err
	}
	provider := sdktrace.NewTracerProvider(
		sdktrace.WithBatcher(exporter),
		sdktrace.WithResource(res),
	)
	otel.SetTracerProvider(provider)
	slog.InfoContext(ctx, "tracing enabled")
	return provider.Shutdown, nil
}

// TraceHTTP wraps the whole mux: extracts the caller's `traceparent` and opens
// the server span. otelhttp renames that span to the mux's matched pattern
// (`GET /accounts/{igid}`) after the handler returns, via Go's
// `Request.Pattern`, so a raw path never becomes a span name.
func TraceHTTP(h http.Handler) http.Handler {
	return otelhttp.NewHandler(h, ServiceName,
		otelhttp.WithFilter(func(r *http.Request) bool {
			return r.URL.Path != "/health" && r.URL.Path != "/events"
		}),
	)
}

// Route adds the `http.route` attribute, which otelhttp does not set on a
// server span. It runs inside the mux because the route is only known once the
// mux has matched. It does NOT rename the span: otelhttp does that after the
// handler returns and would overwrite any name set here.
func Route(pattern string, h http.HandlerFunc) http.HandlerFunc {
	path := pattern
	if _, rest, ok := strings.Cut(pattern, " "); ok {
		path = rest
	}
	return func(w http.ResponseWriter, r *http.Request) {
		trace.SpanFromContext(r.Context()).SetAttributes(attribute.String("http.route", path))
		h(w, r)
	}
}

// PatternOnlyRoute is Route for a path whose variable segment must not be
// recorded. otelhttp puts the RAW path into `url.path` when it opens the
// server span, and sets nothing path-shaped afterwards, so overwriting it here
// with the pattern leaves the span carrying `/posts/{shortcode}` and never
// the shortcode. (An igid in `url.path` is allowed, 16 §Tracing; a post's
// shortcode is not, which is why this is not simply Route.)
func PatternOnlyRoute(pattern string, h http.HandlerFunc) http.HandlerFunc {
	path := pattern
	if _, rest, ok := strings.Cut(pattern, " "); ok {
		path = rest
	}
	return func(w http.ResponseWriter, r *http.Request) {
		trace.SpanFromContext(r.Context()).SetAttributes(
			attribute.String("http.route", path),
			attribute.String("url.path", path),
		)
		h(w, r)
	}
}

// SetupLogging makes every log line JSON on stdout, carrying the trace and
// span ids of the context it was logged in, so a span in Jaeger and its log
// lines join on one id. slog.SetDefault also routes the standard `log`
// package through the same handler.
func SetupLogging(verbose bool) {
	level := slog.LevelInfo
	if verbose {
		level = slog.LevelDebug
	}
	slog.SetDefault(slog.New(TraceLogHandler{
		slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: level}),
	}))
}

// TraceLogHandler adds `trace_id` and `span_id` from the record's context.
type TraceLogHandler struct{ slog.Handler }

func (h TraceLogHandler) Handle(ctx context.Context, r slog.Record) error {
	if sc := trace.SpanContextFromContext(ctx); sc.IsValid() {
		r.AddAttrs(
			slog.String("trace_id", sc.TraceID().String()),
			slog.String("span_id", sc.SpanID().String()),
		)
	}
	return h.Handler.Handle(ctx, r)
}

func (h TraceLogHandler) WithAttrs(attrs []slog.Attr) slog.Handler {
	return TraceLogHandler{h.Handler.WithAttrs(attrs)}
}

func (h TraceLogHandler) WithGroup(name string) slog.Handler {
	return TraceLogHandler{h.Handler.WithGroup(name)}
}
