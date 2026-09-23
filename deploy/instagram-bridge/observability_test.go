package main

// Tracing and logging, tested for the properties a green dashboard would hide:
//
//  1. a request CONTINUES Nora's trace when she sends a `traceparent`, and its
//     span is named by route pattern, never the raw path;
//  2. a GraphQL document that fails inside an HTTP 200 marks its upstream span
//     ERROR (the shape that hid a real bug for a day on 2026-09-22);
//  3. a message's TEXT never lands in a span, anywhere: attributes, events or
//     status (the negative, which a span that "looks fine" cannot show);
//  4. the healthcheck and the SSE stream are not traced;
//  5. a log line carries the trace id of the context it was logged in.

import (
	"bytes"
	"context"
	"encoding/json"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"go.mau.fi/mautrix-meta/pkg/messagix/httpclient"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/codes"
	"go.opentelemetry.io/otel/propagation"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/sdk/trace/tracetest"
	"go.opentelemetry.io/otel/trace"
)

// recordSpans installs a recording provider and the W3C propagator for one
// test and restores the globals afterwards.
func recordSpans(t *testing.T) *tracetest.SpanRecorder {
	t.Helper()
	rec := tracetest.NewSpanRecorder()
	prevTP, prevProp := otel.GetTracerProvider(), otel.GetTextMapPropagator()
	otel.SetTracerProvider(sdktrace.NewTracerProvider(sdktrace.WithSpanProcessor(rec)))
	otel.SetTextMapPropagator(propagation.TraceContext{})
	t.Cleanup(func() {
		otel.SetTracerProvider(prevTP)
		otel.SetTextMapPropagator(prevProp)
	})
	return rec
}

func spanNamed(t *testing.T, rec *tracetest.SpanRecorder, name string) sdktrace.ReadOnlySpan {
	t.Helper()
	var names []string
	for _, s := range rec.Ended() {
		if s.Name() == name {
			return s
		}
		names = append(names, s.Name())
	}
	t.Fatalf("no span named %q; got %v", name, names)
	return nil
}

func attrOf(s sdktrace.ReadOnlySpan, key string) (attribute.Value, bool) {
	for _, kv := range s.Attributes() {
		if string(kv.Key) == key {
			return kv.Value, true
		}
	}
	return attribute.Value{}, false
}

const (
	incomingTraceID = "4bf92f3577b34da6a3ce929d0e0e4736"
	incomingParent  = "00f067aa0ba902b7"
)

// A request carrying Nora's traceparent is part of Nora's trace, and its span
// is named by pattern. A raw-path name would put a person's igid into the span
// NAME, where Jaeger indexes it, and split one route into a series per person.
func TestServerSpanContinuesTheCallersTraceAndIsNamedByPattern(t *testing.T) {
	rec := recordSpans(t)
	b := newTestBridge(t, &fakeIG{})

	req := httptest.NewRequest(http.MethodGet, "/accounts/221731317", nil)
	req.Header.Set("traceparent", "00-"+incomingTraceID+"-"+incomingParent+"-01")
	w := httptest.NewRecorder()
	b.routes().ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("/accounts answered %d: %s", w.Code, w.Body.String())
	}
	s := spanNamed(t, rec, "GET /accounts/{igid}")
	if got := s.SpanContext().TraceID().String(); got != incomingTraceID {
		t.Errorf("server span trace id = %s, want the caller's %s: the bridge "+
			"started a new trace instead of continuing Nora's", got, incomingTraceID)
	}
	if got := s.Parent().SpanID().String(); got != incomingParent {
		t.Errorf("server span parent = %s, want the caller's span %s", got, incomingParent)
	}
	if s.SpanKind() != trace.SpanKindServer {
		t.Errorf("span kind = %v, want server", s.SpanKind())
	}
	if v, ok := attrOf(s, "http.route"); !ok || v.AsString() != "/accounts/{igid}" {
		t.Errorf("http.route = %v (present=%v), want /accounts/{igid}", v.AsString(), ok)
	}
}

// Every Instagram call made for a request is a child span of that request,
// and a session Instagram has challenged names its failure.
func TestUpstreamSpanIsAChildAndClassifiesAChallenge(t *testing.T) {
	rec := recordSpans(t)
	b := newTestBridge(t, &fakeIG{profileErr: httpclient.ErrCheckpointRequired})
	b.out = traceClient(b.out)

	w := getProfile(t, b, "221731317")
	if w.Code != http.StatusConflict {
		t.Fatalf("/accounts answered %d, want 409 for a checkpoint", w.Code)
	}
	server := spanNamed(t, rec, "GET /accounts/{igid}")
	up := spanNamed(t, rec, "instagram.GetProfile")
	if up.Parent().SpanID() != server.SpanContext().SpanID() {
		t.Error("the upstream span is not a child of the request's server span")
	}
	if up.SpanKind() != trace.SpanKindClient {
		t.Errorf("upstream span kind = %v, want client", up.SpanKind())
	}
	if up.Status().Code != codes.Error {
		t.Errorf("upstream span status = %v, want Error", up.Status().Code)
	}
	if v, _ := attrOf(up, "error.type"); v.AsString() != "needs_human" {
		t.Errorf("error.type = %q, want needs_human", v.AsString())
	}
}

// A room the bridge has not resolved yet costs an Instagram round trip inside
// the request, and that round trip must be a span. It is wired here the way
// `main` wires it (`newThreadIndex(b.fetchThreadID)`) rather than the way
// newTestBridge does (straight to the fake), because the bug this pins lived
// exactly in that difference: production resolved through the raw session
// client, bypassing the tracing decorator, and every test passed.
func TestAColdRoomsThreadLookupIsASpanUnderTheRequest(t *testing.T) {
	rec := recordSpans(t)
	b := newTestBridge(t, &fakeIG{})
	b.out = traceClient(b.out)
	b.threads = newThreadIndex(b.fetchThreadID)

	w := post(t, b, "/send", map[string]any{"room_id": threadShort, "text": "hi"})
	if w.Code != http.StatusOK {
		t.Fatalf("/send answered %d: %s", w.Code, w.Body.String())
	}
	server := spanNamed(t, rec, "POST /send")
	lookup := spanNamed(t, rec, "instagram.FetchThreadID")
	if lookup.Parent().SpanID() != server.SpanContext().SpanID() {
		t.Error("the thread lookup is not a child of the request's server span")
	}
}

// The regression this whole file exists for: a GraphQL failure that answers
// HTTP 200. A span that looked only at the status would be green here.
func TestGraphQLErrorsInsideA200MarkTheSpanFailed(t *testing.T) {
	rec := recordSpans(t)
	issuer := tracedIssuer{inner: &fakeGraphQLIssuer{body: []byte(fieldExceptionEnvelope)}}

	_, _, err := issuer.MakeGraphQLRequest(context.Background(), "PolarisProfilePageContentQuery", nil)
	if err != nil {
		t.Fatalf("the transport call itself should succeed; got %v", err)
	}
	s := spanNamed(t, rec, "instagram.graphql PolarisProfilePageContentQuery")
	if s.Status().Code != codes.Error {
		t.Fatalf("span status = %v for a field_exception envelope; a failed "+
			"document inside a 200 must read as a failure", s.Status().Code)
	}
	if v, _ := attrOf(s, "instagram.graphql.error_code"); v.AsInt64() != 1675030 {
		t.Errorf("instagram.graphql.error_code = %d, want 1675030", v.AsInt64())
	}
	if v, _ := attrOf(s, "error.type"); v.AsString() != "upstream_rejected" {
		t.Errorf("error.type = %q, want upstream_rejected", v.AsString())
	}
}

// The positive half, so the failure above is not simply "every GraphQL span
// is red".
func TestASuccessfulGraphQLDocumentLeavesTheSpanUnset(t *testing.T) {
	rec := recordSpans(t)
	issuer := tracedIssuer{inner: &fakeGraphQLIssuer{body: []byte(realProfileEnvelope)}}

	if _, _, err := issuer.MakeGraphQLRequest(context.Background(), "PolarisProfilePageContentQuery", nil); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	s := spanNamed(t, rec, "instagram.graphql PolarisProfilePageContentQuery")
	if s.Status().Code == codes.Error {
		t.Errorf("a successful document marked its span Error: %q", s.Status().Description)
	}
}

// The negative: what people write never enters a span. Checked across every
// place a span carries text, because a span that "looks fine" in Jaeger
// cannot show what it should not contain.
func TestMessageTextNeverEntersASpan(t *testing.T) {
	rec := recordSpans(t)
	b := newTestBridge(t, &fakeIG{})
	b.out = traceClient(b.out)
	const secret = "the-words-somebody-wrote-3f9c1"

	w := post(t, b, "/send", map[string]any{"room_id": threadShort, "text": secret})
	if w.Code != http.StatusOK {
		t.Fatalf("/send answered %d: %s", w.Code, w.Body.String())
	}
	if len(rec.Ended()) < 2 {
		t.Fatalf("expected a server span and an upstream span, got %d", len(rec.Ended()))
	}
	for _, s := range rec.Ended() {
		for _, kv := range s.Attributes() {
			if strings.Contains(kv.Value.String(), secret) {
				t.Errorf("span %q attribute %s carries the message text", s.Name(), kv.Key)
			}
		}
		for _, ev := range s.Events() {
			for _, kv := range ev.Attributes {
				if strings.Contains(kv.Value.String(), secret) {
					t.Errorf("span %q event %q carries the message text", s.Name(), ev.Name)
				}
			}
		}
		if strings.Contains(s.Name(), secret) || strings.Contains(s.Status().Description, secret) {
			t.Errorf("span %q name or status carries the message text", s.Name())
		}
	}
}

// The healthcheck is polled continuously and /events is a stream that stays
// open for hours; neither is a unit of work worth a span.
func TestHealthAndEventsAreNotTraced(t *testing.T) {
	rec := recordSpans(t)
	b := newTestBridge(t, &fakeIG{})

	w := httptest.NewRecorder()
	b.routes().ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/health", nil))
	if w.Code != http.StatusOK {
		t.Fatalf("/health answered %d", w.Code)
	}
	if n := len(rec.Ended()); n != 0 {
		t.Errorf("/health produced %d span(s), want none", n)
	}

	// An already-cancelled context makes the SSE handler return at once.
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	w = httptest.NewRecorder()
	b.routes().ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/events", nil).WithContext(ctx))
	if n := len(rec.Ended()); n != 0 {
		t.Errorf("/events produced %d span(s), want none", n)
	}
}

// A log line inside a span carries that span's ids, which is the join between
// a trace in Jaeger and the container's log.
func TestALogLineCarriesItsTraceID(t *testing.T) {
	recordSpans(t)
	var buf bytes.Buffer
	logger := slog.New(traceLogHandler{slog.NewJSONHandler(&buf, nil)})

	ctx, span := tracer().Start(context.Background(), "unit")
	logger.WarnContext(ctx, "something happened", "room", "1009543911792316")
	span.End()

	var line map[string]any
	if err := json.Unmarshal(buf.Bytes(), &line); err != nil {
		t.Fatalf("log line is not JSON: %v (%s)", err, buf.String())
	}
	if line["trace_id"] != span.SpanContext().TraceID().String() {
		t.Errorf("trace_id = %v, want %s", line["trace_id"], span.SpanContext().TraceID())
	}
	if line["span_id"] != span.SpanContext().SpanID().String() {
		t.Errorf("span_id = %v, want %s", line["span_id"], span.SpanContext().SpanID())
	}
}
