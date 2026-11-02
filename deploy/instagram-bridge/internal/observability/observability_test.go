package observability

import (
	"bytes"
	"context"
	"encoding/json"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"testing"

	"go.opentelemetry.io/otel"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/sdk/trace/tracetest"
)

func TestPollingDoesNotHideBusinessTraces(t *testing.T) {
	previous := otel.GetTracerProvider()
	recorder := tracetest.NewSpanRecorder()
	otel.SetTracerProvider(sdktrace.NewTracerProvider(sdktrace.WithSpanProcessor(recorder)))
	t.Cleanup(func() { otel.SetTracerProvider(previous) })
	handler := TraceHTTP(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	for _, path := range []string{"/health", "/v1/health", "/v1/self", "/metrics", "/events"} {
		handler.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest(http.MethodGet, path, nil))
	}
	if got := len(recorder.Ended()); got != 0 {
		t.Fatalf("polling exported %d spans", got)
	}
	handler.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest(http.MethodPost, "/v1/actions", nil))
	if got := len(recorder.Ended()); got != 1 {
		t.Fatalf("business request exported %d spans, want one", got)
	}
}

// A log line inside a span carries that span's ids, which is the join between
// a trace in Jaeger and the container's log.
func TestALogLineCarriesItsTraceID(t *testing.T) {
	prev := otel.GetTracerProvider()
	otel.SetTracerProvider(sdktrace.NewTracerProvider(sdktrace.WithSpanProcessor(tracetest.NewSpanRecorder())))
	t.Cleanup(func() { otel.SetTracerProvider(prev) })

	var buf bytes.Buffer
	logger := slog.New(TraceLogHandler{slog.NewJSONHandler(&buf, nil)})

	ctx, span := Tracer().Start(context.Background(), "unit")
	logger.WarnContext(ctx, "something happened", "room", "1183780976338188")
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
