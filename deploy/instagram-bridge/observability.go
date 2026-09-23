package main

// Tracing and logging (design/instagram-adoption/16 §Tracing and §Logging,
// 17 P1).
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
//   - one SERVER span per request, named by its route PATTERN
//     (`GET /accounts/{igid}`), never the raw path, and continuing the caller's
//     trace when Nora sends a W3C `traceparent`;
//   - one CLIENT span per call to Instagram, from `tracedClient` and
//     `tracedIssuer`. A GraphQL document that fails inside an HTTP 200 marks
//     its span ERROR. That shape hid a real bug for a day on 2026-09-22.
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

import (
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"strings"

	"go.mau.fi/mautrix-meta/pkg/instameow"
	"go.mau.fi/mautrix-meta/pkg/instameow/slidetypes"
	"go.mau.fi/mautrix-meta/pkg/messagix/httpclient"
	"go.mau.fi/mautrix-meta/pkg/messagix/responses"
	"go.mau.fi/mautrix-meta/pkg/messagix/types"
	"go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/codes"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/trace"
)

const serviceName = "instagram-bridge"

// tracer is read from the global provider on every call rather than cached,
// so a test that installs a recording provider sees every span.
func tracer() trace.Tracer { return otel.GetTracerProvider().Tracer(serviceName) }

// setupTracing installs the W3C propagator unconditionally and, when an OTLP
// endpoint is configured, a batching provider that exports to it. The
// returned function flushes on shutdown.
func setupTracing(ctx context.Context) (func(context.Context) error, error) {
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
		resource.WithAttributes(attribute.String("service.name", serviceName)),
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

// traceHTTP wraps the whole mux: extracts the caller's `traceparent` and opens
// the server span. otelhttp renames that span to the mux's matched pattern
// (`GET /accounts/{igid}`) after the handler returns, via Go's
// `Request.Pattern`, so a raw path never becomes a span name.
func traceHTTP(h http.Handler) http.Handler {
	return otelhttp.NewHandler(h, serviceName,
		otelhttp.WithFilter(func(r *http.Request) bool {
			return r.URL.Path != "/health" && r.URL.Path != "/events"
		}),
	)
}

// route adds the `http.route` attribute, which otelhttp does not set on a
// server span. It runs inside the mux because the route is only known once the
// mux has matched. It does NOT rename the span: otelhttp does that after the
// handler returns and would overwrite any name set here.
func route(pattern string, h http.HandlerFunc) http.HandlerFunc {
	path := pattern
	if _, rest, ok := strings.Cut(pattern, " "); ok {
		path = rest
	}
	return func(w http.ResponseWriter, r *http.Request) {
		trace.SpanFromContext(r.Context()).SetAttributes(attribute.String("http.route", path))
		h(w, r)
	}
}

// startUpstream opens the CLIENT span for one call to Instagram.
func startUpstream(ctx context.Context, name string, attrs ...attribute.KeyValue) (context.Context, trace.Span) {
	return tracer().Start(ctx, name,
		trace.WithSpanKind(trace.SpanKindClient),
		trace.WithAttributes(attrs...),
	)
}

// endUpstream closes it, marking it failed with a classified `error.type`.
func endUpstream(span trace.Span, err error) {
	if err != nil {
		kind := errorType(err)
		span.SetAttributes(attribute.String("error.type", kind))
		span.RecordError(err)
		span.SetStatus(codes.Error, kind)
	}
	span.End()
}

// errorType names a failure by what it MEANS, the vocabulary 16's closed
// domain-error set will formalize. Kept to the categories a reader of Jaeger
// acts on differently.
func errorType(err error) string {
	switch {
	case errors.Is(err, httpclient.ErrChallengeRequired),
		errors.Is(err, httpclient.ErrCheckpointRequired),
		errors.Is(err, httpclient.ErrConsentRequired),
		errors.Is(err, httpclient.ErrTokenInvalidated):
		return "needs_human"
	case errors.Is(err, errNotConnected):
		return "unavailable"
	case errors.Is(err, errProfileGraphQL):
		return "upstream_rejected"
	case errors.Is(err, errProfileHTTP):
		return "upstream_http"
	case errors.Is(err, context.DeadlineExceeded):
		return "timeout"
	case errors.Is(err, context.Canceled):
		return "canceled"
	default:
		return "error"
	}
}

// tracedClient decorates igClient so every Instagram call made on behalf of a
// request is a span. One decorator, applied where the session hands the
// handlers their client, rather than a span at each of the call sites.
type tracedClient struct{ inner igClient }

func traceClient(c igClient) igClient {
	if c == nil {
		return nil
	}
	return tracedClient{inner: c}
}

func (t tracedClient) SendMessage(ctx context.Context, req *slidetypes.SendTextRequest) (resp *slidetypes.SendTextResponse, err error) {
	ctx, span := startUpstream(ctx, "instagram.SendMessage")
	defer func() { endUpstream(span, err) }()
	return t.inner.SendMessage(ctx, req)
}

func (t tracedClient) SendMedia(ctx context.Context, req *slidetypes.SendMediaRequest) (resp *slidetypes.SendMediaResponse, err error) {
	ctx, span := startUpstream(ctx, "instagram.SendMedia")
	defer func() { endUpstream(span, err) }()
	return t.inner.SendMedia(ctx, req)
}

func (t tracedClient) SendReaction(ctx context.Context, req *slidetypes.CreateReactionRequest) (resp *slidetypes.SendReactionResponse, err error) {
	ctx, span := startUpstream(ctx, "instagram.SendReaction")
	defer func() { endUpstream(span, err) }()
	return t.inner.SendReaction(ctx, req)
}

func (t tracedClient) UnsendMessage(ctx context.Context, req *slidetypes.UnsendMessageRequest) (resp *slidetypes.UnsendMessageResponse, err error) {
	ctx, span := startUpstream(ctx, "instagram.UnsendMessage")
	defer func() { endUpstream(span, err) }()
	return t.inner.UnsendMessage(ctx, req)
}

func (t tracedClient) SetTyping(ctx context.Context, threadID string, typing bool) (err error) {
	ctx, span := startUpstream(ctx, "instagram.SetTyping",
		attribute.String("instagram.thread_id", threadID))
	defer func() { endUpstream(span, err) }()
	return t.inner.SetTyping(ctx, threadID, typing)
}

func (t tracedClient) FetchThreadID(ctx context.Context, threadFBID int64) (ids *instameow.ThreadIGIDs, err error) {
	ctx, span := startUpstream(ctx, "instagram.FetchThreadID",
		attribute.Int64("instagram.thread_fbid", threadFBID))
	defer func() { endUpstream(span, err) }()
	return t.inner.FetchThreadID(ctx, threadFBID)
}

func (t tracedClient) GetThread(ctx context.Context, req *slidetypes.GetThreadInfoRequest) (resp *slidetypes.ThreadInfoResponse, err error) {
	ctx, span := startUpstream(ctx, "instagram.GetThread")
	defer func() { endUpstream(span, err) }()
	return t.inner.GetThread(ctx, req)
}

func (t tracedClient) GetProfile(ctx context.Context, igid string) (resp *slidetypes.ProfilePageResponse, err error) {
	ctx, span := startUpstream(ctx, "instagram.GetProfile",
		attribute.String("instagram.igid", igid))
	defer func() { endUpstream(span, err) }()
	return t.inner.GetProfile(ctx, igid)
}

func (t tracedClient) FetchReel(ctx context.Context, reelIDs []string, mediaID string) (resp *responses.ReelInfoResponse, err error) {
	ctx, span := startUpstream(ctx, "instagram.FetchReel",
		attribute.StringSlice("instagram.igids", reelIDs))
	defer func() { endUpstream(span, err) }()
	return t.inner.FetchReel(ctx, reelIDs, mediaID)
}

// GetHTTP and GetEndpoint pass through, so the decorated client still
// satisfies socialClient and the raw GraphQL read keeps working through it.
// An inner client without them answers nil and "", which socialIssuerFor
// already reports as "no http client".
func (t tracedClient) GetHTTP() *httpclient.HTTPClient {
	if s, ok := t.inner.(socialClient); ok {
		return s.GetHTTP()
	}
	return nil
}

func (t tracedClient) GetEndpoint(name string) string {
	if s, ok := t.inner.(socialClient); ok {
		return s.GetEndpoint(name)
	}
	return ""
}

// tracedIssuer decorates the raw GraphQL call. It reads the envelope's
// `errors` array itself, because a failed document answers HTTP 200 and a
// span that only looked at the status would be green on exactly the failure
// that matters. 16's gateway helper will own this check once (P7); until then
// parseSocialProfile and this span each read it.
type tracedIssuer struct{ inner graphQLIssuer }

func (t tracedIssuer) MakeGraphQLRequest(ctx context.Context, name string, variables any) (resp *http.Response, raw []byte, err error) {
	ctx, span := startUpstream(ctx, "instagram.graphql "+name,
		attribute.String("instagram.graphql.document", name))
	defer span.End()
	resp, raw, err = t.inner.MakeGraphQLRequest(ctx, name, variables)
	if err != nil {
		kind := errorType(err)
		span.SetAttributes(attribute.String("error.type", kind))
		span.RecordError(err)
		span.SetStatus(codes.Error, kind)
		return resp, raw, err
	}
	if resp != nil {
		span.SetAttributes(attribute.Int("http.response.status_code", resp.StatusCode))
		if resp.StatusCode != http.StatusOK {
			span.SetAttributes(attribute.String("error.type", "upstream_http"))
			span.SetStatus(codes.Error, "upstream_http")
			return resp, raw, err
		}
	}
	var envelope struct {
		Errors []struct {
			Code int `json:"code"`
		} `json:"errors"`
	}
	if json.Unmarshal(raw, &envelope) == nil && len(envelope.Errors) > 0 {
		span.SetAttributes(
			attribute.Int("instagram.graphql.error_code", envelope.Errors[0].Code),
			attribute.String("error.type", "upstream_rejected"),
		)
		span.SetStatus(codes.Error, "upstream_rejected")
	}
	return resp, raw, err
}

// traceUpload decorates the media upload, the one Instagram call that is not
// on igClient.
func traceUpload(up uploader) uploader {
	if up == nil {
		return nil
	}
	return func(ctx context.Context, threadID int64, media *httpclient.MercuryUploadMedia) (resp *types.MercuryUploadResponse, err error) {
		ctx, span := startUpstream(ctx, "instagram.Upload",
			attribute.Int64("instagram.thread_fbid", threadID))
		defer func() { endUpstream(span, err) }()
		return up(ctx, threadID, media)
	}
}

// setupLogging makes every log line JSON on stdout, carrying the trace and
// span ids of the context it was logged in, so a span in Jaeger and its log
// lines join on one id. slog.SetDefault also routes the standard `log`
// package through the same handler.
func setupLogging(verbose bool) {
	level := slog.LevelInfo
	if verbose {
		level = slog.LevelDebug
	}
	slog.SetDefault(slog.New(traceLogHandler{
		slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: level}),
	}))
}

type traceLogHandler struct{ slog.Handler }

func (h traceLogHandler) Handle(ctx context.Context, r slog.Record) error {
	if sc := trace.SpanContextFromContext(ctx); sc.IsValid() {
		r.AddAttrs(
			slog.String("trace_id", sc.TraceID().String()),
			slog.String("span_id", sc.SpanID().String()),
		)
	}
	return h.Handler.Handle(ctx, r)
}

func (h traceLogHandler) WithAttrs(attrs []slog.Attr) slog.Handler {
	return traceLogHandler{h.Handler.WithAttrs(attrs)}
}

func (h traceLogHandler) WithGroup(name string) slog.Handler {
	return traceLogHandler{h.Handler.WithGroup(name)}
}
