package httptransport

// The platform contract's envelope (design/service-api/conventions.md «The
// envelope»): every answer on /v1/platform/ and /v1/operations/ is one of
// these six parts, and its HTTP status is the kernel's status for its outcome
// (nora/domain/service_api.py). writeEnvelope is the only writer.

import (
	"context"
	"fmt"
	"net/http"
	"time"

	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/trace"
)

// outcomeStatus is the kernel's status table, both families.
var outcomeStatus = map[string]int{
	// read outcomes
	"ok": http.StatusOK, "nothing_found": http.StatusOK, "unavailable": http.StatusServiceUnavailable,
	// operation outcomes
	"complete": http.StatusOK, "pending": http.StatusAccepted, "denied": http.StatusForbidden,
	"rejected": http.StatusOK, "unsupported": http.StatusOK, "incomplete": http.StatusOK,
	"uncertain": http.StatusOK,
}

// platformSource is `from.source` on every read this bridge answers.
const platformSource = "instagram"

// knownByBridgeClock is `when.known_by` for a time this bridge read off its
// own clock while answering.
const knownByBridgeClock = "bridge_clock"

type envScope struct {
	Level string  `json:"level"`
	ID    *string `json:"id"`
}

type readWhen struct {
	ObservedAtMS int64  `json:"observed_at_ms"`
	KnownBy      string `json:"known_by"`
}

type readFrom struct {
	Source        string   `json:"source"`
	Healthy       bool     `json:"healthy"`
	FailedSources []string `json:"failed_sources"`
}

type opWhen struct {
	State       string `json:"state"`
	StartedAtMS *int64 `json:"started_at_ms"`
	SettledAtMS *int64 `json:"settled_at_ms"`
}

type opFrom struct {
	Target      string  `json:"target"`
	PlatformRef *string `json:"platform_ref"`
}

// envelope is the six parts. When and From hold a read's or an operation's
// shape, per family.
type envelope struct {
	Outcome string   `json:"outcome"`
	Payload any      `json:"payload"`
	When    any      `json:"when"`
	From    any      `json:"from"`
	Scope   envScope `json:"scope"`
	Trace   string   `json:"trace"`
}

// writeEnvelope answers env under its outcome's status. An outcome outside
// the table is a programming error, not an answer.
func writeEnvelope(w http.ResponseWriter, env envelope) {
	status, ok := outcomeStatus[env.Outcome]
	if !ok {
		panic(fmt.Sprintf("envelope outcome %q has no status", env.Outcome))
	}
	writeJSON(w, status, env)
}

// readEnvelope is a read answered now, by this bridge's clock.
func readEnvelope(r *http.Request, outcome string, payload any, healthy bool, scope envScope) envelope {
	return envelope{
		Outcome: outcome, Payload: payload,
		When:  readWhen{ObservedAtMS: time.Now().UnixMilli(), KnownBy: knownByBridgeClock},
		From:  readFrom{Source: platformSource, Healthy: healthy, FailedSources: []string{}},
		Scope: scope, Trace: traceOf(r),
	}
}

// settledEnvelope is an operation answer with no row behind it.
func settledEnvelope(r *http.Request, outcome string, payload any, target string, scope envScope) envelope {
	return envelope{
		Outcome: outcome, Payload: payload,
		When:  opWhen{State: "settled"},
		From:  opFrom{Target: target},
		Scope: scope, Trace: traceOf(r),
	}
}

func roomScope(conversationID string) envScope {
	if conversationID == "" {
		return installationScope()
	}
	return envScope{Level: "room", ID: &conversationID}
}

func installationScope() envScope { return envScope{Level: "installation"} }

// traceOf is the trace id the request's traceparent carries, or "".
func traceOf(r *http.Request) string {
	sc := trace.SpanContextFromContext(propagation.TraceContext{}.Extract(
		context.Background(), propagation.HeaderCarrier(r.Header)))
	if !sc.IsValid() {
		return ""
	}
	return sc.TraceID().String()
}
