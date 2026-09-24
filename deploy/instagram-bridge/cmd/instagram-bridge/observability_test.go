package main

// Tracing and logging, tested for the properties a green dashboard would hide:
//
//  1. a request CONTINUES Nora's trace when she sends a `traceparent`, and its
//     span is named by route pattern, never the raw path;
//  2. a GraphQL document that fails inside an HTTP 200 marks its upstream span
//     ERROR (the shape that hid a real bug for a day on 2026-09-22);
//  3. a message's TEXT never lands in a span, anywhere: attributes, events or
//     status, nor in a log line or an error envelope, on every write route
//     and on every failure path of a send (the negative, which a span that
//     "looks fine" cannot show);
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

	"instagram-bridge/internal/domain"

	"go.mau.fi/mautrix-meta/pkg/instameow/slidetypes"
	"go.mau.fi/mautrix-meta/pkg/messagix/httpclient"
	"go.mau.fi/mautrix-meta/pkg/messagix/types"
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

	w := sendMessage(t, b, threadShort, map[string]any{"text": "hi"})
	if w.Code != http.StatusOK {
		t.Fatalf("send answered %d: %s", w.Code, w.Body.String())
	}
	server := spanNamed(t, rec, "POST /threads/{thread_fbid}/messages")
	lookup := spanNamed(t, rec, "instagram.FetchThreadID")
	if lookup.Parent().SpanID() != server.SpanContext().SpanID() {
		t.Error("the thread lookup is not a child of the request's server span")
	}
}

// writeCase is one write route, called on a COLD room (a fresh bridge), with
// the server span it must produce and the upstream spans beneath it: exactly
// one per instameow call (17 P1), the room's route lookup first.
type writeCase struct {
	name     string
	pattern  string
	call     func(t *testing.T, b *testBridge) *httptest.ResponseRecorder
	upstream []string
}

func writeCases() []writeCase {
	return []writeCase{
		{
			name: "send text", pattern: "POST /threads/{thread_fbid}/messages",
			call: func(t *testing.T, b *testBridge) *httptest.ResponseRecorder {
				return sendMessage(t, b, threadShort, map[string]any{"text": "hi"})
			},
			upstream: []string{"instagram.FetchThreadID", "instagram.SendMessage"},
		},
		{
			name: "send media", pattern: "POST /threads/{thread_fbid}/messages",
			call: func(t *testing.T, b *testBridge) *httptest.ResponseRecorder {
				return sendMessage(t, b, threadShort, mediaOf([]byte("x"), "x.png", "image/png"))
			},
			upstream: []string{"instagram.FetchThreadID", "instagram.Upload", "instagram.SendMedia"},
		},
		{
			name: "unsend", pattern: "DELETE /threads/{thread_fbid}/messages/{message_id}",
			call: func(t *testing.T, b *testBridge) *httptest.ResponseRecorder {
				return unsendMessage(t, b, threadShort, "mid.$x")
			},
			upstream: []string{"instagram.FetchThreadID", "instagram.UnsendMessage"},
		},
		{
			name: "reaction", pattern: "PUT /threads/{thread_fbid}/messages/{message_id}/reaction",
			call: func(t *testing.T, b *testBridge) *httptest.ResponseRecorder {
				return putReaction(t, b, threadShort, "mid.$x", map[string]any{"emoji": "👍"})
			},
			upstream: []string{"instagram.FetchThreadID", "instagram.SendReaction"},
		},
		{
			name: "typing", pattern: "PUT /threads/{thread_fbid}/typing",
			call: func(t *testing.T, b *testBridge) *httptest.ResponseRecorder {
				return putTyping(t, b, threadShort, map[string]any{"typing": true})
			},
			upstream: []string{"instagram.FetchThreadID", "instagram.SetTyping"},
		},
	}
}

// Every write route: its server span is named by its PATTERN, continues the
// caller's trace (Nora sends `traceparent` on every write), carries
// `http.route`, and has exactly one upstream span per instameow call as its
// children. A raw-path span name would put a thread and message id into the
// NAME, where Jaeger indexes it, and split one route into a series per room.
func TestEveryWriteRouteIsOneTracedRequestWithOneSpanPerUpstreamCall(t *testing.T) {
	for _, wc := range writeCases() {
		t.Run(wc.name, func(t *testing.T) {
			rec := recordSpans(t)
			b := newTestBridge(t, &fakeIG{})

			w := wc.call(t, withTraceparent(b))
			if w.Code != http.StatusOK {
				t.Fatalf("answered %d: %s", w.Code, w.Body.String())
			}
			server := spanNamed(t, rec, wc.pattern)
			if got := server.SpanContext().TraceID().String(); got != incomingTraceID {
				t.Errorf("server span trace id = %s, want the caller's %s", got, incomingTraceID)
			}
			if server.SpanKind() != trace.SpanKindServer {
				t.Errorf("span kind = %v, want server", server.SpanKind())
			}
			_, path, _ := strings.Cut(wc.pattern, " ")
			if v, ok := attrOf(server, "http.route"); !ok || v.AsString() != path {
				t.Errorf("http.route = %q (present=%v), want %q", v.AsString(), ok, path)
			}
			var children []string
			for _, s := range rec.Ended() {
				if s.Parent().SpanID() == server.SpanContext().SpanID() {
					children = append(children, s.Name())
					if s.SpanKind() != trace.SpanKindClient {
						t.Errorf("upstream span %q kind = %v, want client", s.Name(), s.SpanKind())
					}
				}
			}
			if strings.Join(children, ",") != strings.Join(wc.upstream, ",") {
				t.Errorf("upstream spans = %v, want exactly %v", children, wc.upstream)
			}
			if n := len(rec.Ended()); n != 1+len(wc.upstream) {
				t.Errorf("%d spans in all, want %d: one server span and one per upstream call", n, 1+len(wc.upstream))
			}
		})
	}
}

// tracedBridge wraps a test bridge so every request it serves carries Nora's
// `traceparent`, the header her adapter sends on every write.
type tracedBridge struct{ inner http.Handler }

func (h tracedBridge) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	r.Header.Set("traceparent", "00-"+incomingTraceID+"-"+incomingParent+"-01")
	h.inner.ServeHTTP(w, r)
}

func withTraceparent(b *testBridge) *testBridge {
	return &testBridge{gw: b.gw, hub: b.hub, h: tracedBridge{inner: b.h}}
}

// assertNothingCarries fails if `secret` is in any span (attribute, event name
// or attribute, name, status), any log line, or the response body.
func assertNothingCarries(t *testing.T, rec *tracetest.SpanRecorder, logs *bytes.Buffer, body, secret, what string) {
	t.Helper()
	for _, s := range rec.Ended() {
		for _, kv := range s.Attributes() {
			if strings.Contains(kv.Value.Emit(), secret) {
				t.Errorf("span %q attribute %s carries the %s", s.Name(), kv.Key, what)
			}
		}
		for _, ev := range s.Events() {
			if strings.Contains(ev.Name, secret) {
				t.Errorf("span %q event name %q carries the %s", s.Name(), ev.Name, what)
			}
			for _, kv := range ev.Attributes {
				if strings.Contains(kv.Value.Emit(), secret) {
					t.Errorf("span %q event %q attribute %s carries the %s (RecordError)",
						s.Name(), ev.Name, kv.Key, what)
				}
			}
		}
		if strings.Contains(s.Name(), secret) || strings.Contains(s.Status().Description, secret) {
			t.Errorf("span %q name or status carries the %s", s.Name(), what)
		}
	}
	if logs != nil && strings.Contains(logs.String(), secret) {
		t.Errorf("a log line carries the %s: %s", what, logs.String())
	}
	if strings.Contains(body, secret) {
		t.Errorf("the response body carries the %s: %s", what, body)
	}
}

// The negative: what people write never enters a span, a log line or an
// envelope. Checked across every place a span carries text, on the success
// path of EVERY write route in one conversation (the text is sent, then the
// message is reacted to, unsent, and the room shown typing), because a span
// that "looks fine" in Jaeger cannot show what it should not contain.
func TestMessageTextNeverEntersASpan(t *testing.T) {
	rec := recordSpans(t)
	logs := captureLogs(t)
	b := newTestBridge(t, &fakeIG{})
	const secret = "the-words-somebody-wrote-3f9c1"

	var bodies strings.Builder
	for name, w := range map[string]*httptest.ResponseRecorder{
		"send text":  sendMessage(t, b, threadShort, map[string]any{"text": secret}),
		"send media": sendMessage(t, b, threadShort, mediaOf([]byte(secret), secret+".png", "image/png")),
		"reaction":   putReaction(t, b, threadShort, "mid.$sent", map[string]any{"emoji": "👍"}),
		"unsend":     unsendMessage(t, b, threadShort, "mid.$sent"),
		"typing":     putTyping(t, b, threadShort, map[string]any{"typing": false}),
	} {
		if w.Code != http.StatusOK {
			t.Fatalf("%s answered %d: %s", name, w.Code, w.Body.String())
		}
		bodies.WriteString(w.Body.String())
	}
	if len(rec.Ended()) < 10 {
		t.Fatalf("expected server and upstream spans for five routes, got %d", len(rec.Ended()))
	}
	assertNothingCarries(t, rec, logs, bodies.String(), secret, "message text")
}

// The harder half: every FAILURE path of a text send. An upstream refusal can
// quote what it refused (the fake's error text carries the words, standing in
// for that), and span.RecordError writes err.Error() into the span, the
// envelope's message is the error's text, and the failure is logged. Each
// failure here is a different path through the gateway's classification: too
// long (413), a checkpoint found during the send (409), a GraphQL refusal
// (502), an unnamed failure (500), a checkpoint in the room lookup (409 at the
// lookup stage), and a checkpointed session (409 at the gate).
func TestMessageTextNeverLeaksOnFailure(t *testing.T) {
	const secret = "the-words-somebody-wrote-8a2d4"
	for _, tc := range []struct {
		name string
		fake *fakeIG
		opts []bridgeOpt
		want int
	}{
		{"too long", &fakeIG{SendErr: fmt.Errorf("Message Too Long: %s", secret)}, nil, http.StatusRequestEntityTooLarge},
		{"checkpoint during the send", &fakeIG{SendErr: fmt.Errorf("%w: %s", httpclient.ErrCheckpointRequired, secret)}, nil, http.StatusConflict},
		{"graphql refusal", &fakeIG{SendErr: (&types.ErrorResponse{Errors: []*types.GraphQLError{
			{Message: "refused " + secret, Summary: "Query error", Code: 1675030},
		}}).AsError()}, nil, http.StatusBadGateway},
		{"unnamed", &fakeIG{SendErr: fmt.Errorf("upstream said %q", secret)}, nil, http.StatusInternalServerError},
		{"no message id", &fakeIG{EmptySend: true}, nil, http.StatusBadGateway},
		{"room lookup", &fakeIG{FetchErr: httpclient.ErrCheckpointRequired}, nil, http.StatusConflict},
		{"checkpointed session", &fakeIG{}, []bridgeOpt{withState(domain.StateCheckpointed, "captcha")}, http.StatusConflict},
	} {
		t.Run(tc.name, func(t *testing.T) {
			rec := recordSpans(t)
			logs := captureLogs(t)
			b := newTestBridge(t, tc.fake, tc.opts...)

			w := sendMessage(t, b, threadShort, map[string]any{"text": secret})
			if w.Code != tc.want {
				t.Fatalf("answered %d, want %d: %s", w.Code, tc.want, w.Body.String())
			}
			decodeEnvelope(t, w)
			assertNothingCarries(t, rec, logs, w.Body.String(), secret, "message text")
		})
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
