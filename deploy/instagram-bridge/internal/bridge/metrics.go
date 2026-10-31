package bridge

// Low-cardinality counters for the durable path, matching the contract's
// vocabulary (captured / enqueued / duplicate_suppressed /
// delivery_confirmed / receipt outcomes / capture_halted / custody). No
// labels on event or room ids — a platform id in a metric label is the same
// disclosure as in a log line, so there are none.

import (
	"fmt"
	"sort"
	"strings"
	"sync/atomic"
)

// Metrics is a tiny counter registry exposed at GET /metrics.
type Metrics struct {
	counters map[string]*atomic.Int64
}

func NewMetrics() *Metrics {
	m := &Metrics{counters: map[string]*atomic.Int64{}}
	for _, name := range []string{
		"captured", "capture_error", "capture_halted",
		"admission_denied", "transition_replay_suppressed",
		"duplicate_suppressed", "ambiguous_retained",
		"enqueued", "enqueue_retry", "drainer_backlog_drop",
		"custody_spooled", "custody_fetch_failed",
		"receipt_sent", "receipt_failed",
		"action_executed", "action_replayed", "action_conflicted",
		"action_confirmed", "action_rejected", "action_uncertain", "action_unsupported",
	} {
		m.counters[name] = &atomic.Int64{}
	}
	return m
}

// Count adds one to a counter (unknown names are created so a new metric
// can never panic on first use).
func (m *Metrics) Count(name string) {
	c, ok := m.counters[name]
	if !ok {
		c = &atomic.Int64{}
		m.counters[name] = c
	}
	c.Add(1)
}

// Value is the counter's current value (for /v1/health and tests).
func (m *Metrics) Value(name string) int64 {
	if c, ok := m.counters[name]; ok {
		return c.Load()
	}
	return 0
}

// Render is the Prometheus text exposition (prefixed nora_bridge_).
func (m *Metrics) Render() string {
	names := make([]string, 0, len(m.counters))
	for n := range m.counters {
		names = append(names, n)
	}
	sort.Strings(names)
	var b strings.Builder
	for _, n := range names {
		fmt.Fprintf(&b, "nora_bridge_%s %d\n", n, m.counters[n].Load())
	}
	return b.String()
}
