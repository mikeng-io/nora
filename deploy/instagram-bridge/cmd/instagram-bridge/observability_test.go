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
	"fmt"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"go.mau.fi/mautrix-meta/pkg/instameow/slidetypes"
	"go.mau.fi/mautrix-meta/pkg/messagix/httpclient"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/codes"
	"go.opentelemetry.io/otel/propagation"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/sdk/trace/tracetest"
	"go.opentelemetry.io/otel/trace"
)

// captureLogs installs a buffering slog handler for one test and restores
// the previous default afterwards. Used to prove a value never reaches a log
// line, which a span assertion alone cannot show.
func captureLogs(t *testing.T) *bytes.Buffer {
	t.Helper()
	var buf bytes.Buffer
	prev := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(&buf, nil)))
	t.Cleanup(func() { slog.SetDefault(prev) })
	return &buf
}

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
	b := newTestBridge(t, &fakeIG{ProfileErr: httpclient.ErrCheckpointRequired})

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

// The negative: what people write never enters a span. Checked across every
// place a span carries text, because a span that "looks fine" in Jaeger
// cannot show what it should not contain.
func TestMessageTextNeverEntersASpan(t *testing.T) {
	rec := recordSpans(t)
	b := newTestBridge(t, &fakeIG{})
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

// The negative for GET /accounts?query=: a search query is a name typed
// about a person, the same as message text, and must never land in a span —
// attributes, events, name or status — anywhere, including the
// instagram.search.result_count attribute the handler DOES set deliberately
// (accounts_search.go), which must carry the COUNT and never the query
// itself.
func TestSearchQueryNeverEntersASpan(t *testing.T) {
	rec := recordSpans(t)
	b := newTestBridge(t, &fakeIG{})
	const secret = "the-name-somebody-searched-for-a91c4"

	w := getSearch(t, b, "?query="+secret)
	if w.Code != http.StatusOK {
		t.Fatalf("/accounts?query= answered %d: %s", w.Code, w.Body.String())
	}
	if len(rec.Ended()) < 2 {
		t.Fatalf("expected a server span and an upstream span, got %d", len(rec.Ended()))
	}
	for _, s := range rec.Ended() {
		for _, kv := range s.Attributes() {
			if strings.Contains(kv.Value.String(), secret) {
				t.Errorf("span %q attribute %s carries the search query", s.Name(), kv.Key)
			}
		}
		for _, ev := range s.Events() {
			for _, kv := range ev.Attributes {
				if strings.Contains(kv.Value.String(), secret) {
					t.Errorf("span %q event %q carries the search query", s.Name(), ev.Name)
				}
			}
		}
		if strings.Contains(s.Name(), secret) || strings.Contains(s.Status().Description, secret) {
			t.Errorf("span %q name or status carries the search query", s.Name())
		}
	}
}

// The harder half of the same rule: an UPSTREAM error can echo bad input
// back in its own message (a validation error quoting the query is a normal
// GraphQL shape), so the failure path has to be checked separately from the
// success path above. Covers every surface the query could reach: span
// attributes, span events (span.RecordError writes err.Error() into an
// exception event — the vector TestSearchQueryNeverEntersASpan's
// success-only case cannot see), span name and status, the log line, and the
// error envelope's own message.
func TestSearchQueryNeverLeaksOnFailure(t *testing.T) {
	rec := recordSpans(t)
	logs := captureLogs(t)
	const secret = "the-name-somebody-searched-for-b7f31c"

	// The fake's error TEXT contains the secret, standing in for an upstream
	// validation error that quotes the bad input it is complaining about.
	b := newTestBridge(t, &fakeIG{SearchErr: fmt.Errorf("upstream rejected the query %q", secret)})

	w := getSearch(t, b, "?query="+secret)
	if w.Code == http.StatusOK {
		t.Fatalf("expected a failure status for a synthetic upstream error, got 200: %s", w.Body.String())
	}

	for _, s := range rec.Ended() {
		for _, kv := range s.Attributes() {
			if strings.Contains(kv.Value.String(), secret) {
				t.Errorf("span %q attribute %s carries the search query", s.Name(), kv.Key)
			}
		}
		for _, ev := range s.Events() {
			if strings.Contains(ev.Name, secret) {
				t.Errorf("span %q event name %q carries the search query", s.Name(), ev.Name)
			}
			for _, kv := range ev.Attributes {
				if strings.Contains(kv.Value.String(), secret) {
					t.Errorf("span %q event %q attribute %s carries the search query (RecordError)",
						s.Name(), ev.Name, kv.Key)
				}
			}
		}
		if strings.Contains(s.Name(), secret) || strings.Contains(s.Status().Description, secret) {
			t.Errorf("span %q name or status carries the search query", s.Name())
		}
	}
	if strings.Contains(logs.String(), secret) {
		t.Errorf("a log line carries the search query: %s", logs.String())
	}
	if strings.Contains(w.Body.String(), secret) {
		t.Errorf("the error envelope carries the search query: %s", w.Body.String())
	}
}

// The same rule for GET /accounts?username= (17 P10): a username is a name
// typed about a person, exactly like a search query, and must never land in
// a span — including instagram.accounts_by_username.match_count
// (accounts_search.go), which must carry the COUNT and never the username
// itself.
func TestUsernameNeverEntersASpan(t *testing.T) {
	rec := recordSpans(t)
	const secret = "secret_username_9f2a1"
	b := newTestBridge(t, &fakeIG{SearchResultsSet: true, SearchResults: []*slidetypes.SearchResult{
		{PK: "900000010", Username: secret},
	}})

	w := getSearch(t, b, "?username="+secret)
	if w.Code != http.StatusOK {
		t.Fatalf("/accounts?username= answered %d: %s", w.Code, w.Body.String())
	}
	if len(rec.Ended()) < 2 {
		t.Fatalf("expected a server span and an upstream span, got %d", len(rec.Ended()))
	}
	for _, s := range rec.Ended() {
		for _, kv := range s.Attributes() {
			if strings.Contains(kv.Value.String(), secret) {
				t.Errorf("span %q attribute %s carries the username", s.Name(), kv.Key)
			}
		}
		for _, ev := range s.Events() {
			for _, kv := range ev.Attributes {
				if strings.Contains(kv.Value.String(), secret) {
					t.Errorf("span %q event %q carries the username", s.Name(), ev.Name)
				}
			}
		}
		if strings.Contains(s.Name(), secret) || strings.Contains(s.Status().Description, secret) {
			t.Errorf("span %q name or status carries the username", s.Name())
		}
	}
}

// The harder half, mirroring TestSearchQueryNeverLeaksOnFailure: an upstream
// error can echo bad input back in its own message, so the failure path
// needs its own check across every surface — span attributes, span events
// (RecordError writes err.Error() into an exception event), span name and
// status, the log line, and the error envelope's own message.
func TestUsernameLookupNeverLeaksOnFailure(t *testing.T) {
	rec := recordSpans(t)
	logs := captureLogs(t)
	const secret = "secret_username_b7f31"

	// The fake's error TEXT contains the secret, standing in for an upstream
	// validation error that quotes the bad input it is complaining about.
	b := newTestBridge(t, &fakeIG{SearchErr: fmt.Errorf("upstream rejected the username %q", secret)})

	w := getSearch(t, b, "?username="+secret)
	if w.Code == http.StatusOK {
		t.Fatalf("expected a failure status for a synthetic upstream error, got 200: %s", w.Body.String())
	}

	for _, s := range rec.Ended() {
		for _, kv := range s.Attributes() {
			if strings.Contains(kv.Value.String(), secret) {
				t.Errorf("span %q attribute %s carries the username", s.Name(), kv.Key)
			}
		}
		for _, ev := range s.Events() {
			if strings.Contains(ev.Name, secret) {
				t.Errorf("span %q event name %q carries the username", s.Name(), ev.Name)
			}
			for _, kv := range ev.Attributes {
				if strings.Contains(kv.Value.String(), secret) {
					t.Errorf("span %q event %q attribute %s carries the username (RecordError)",
						s.Name(), ev.Name, kv.Key)
				}
			}
		}
		if strings.Contains(s.Name(), secret) || strings.Contains(s.Status().Description, secret) {
			t.Errorf("span %q name or status carries the username", s.Name())
		}
	}
	if strings.Contains(logs.String(), secret) {
		t.Errorf("a log line carries the username: %s", logs.String())
	}
	if strings.Contains(w.Body.String(), secret) {
		t.Errorf("the error envelope carries the username: %s", w.Body.String())
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
