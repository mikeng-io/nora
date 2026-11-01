package bridge

// The validated admission projection: `{dir}/instagram.json`, written by the
// app and mounted read-only. The bridge polls mtime and keeps the last good
// document on a parse failure — a half-written file degrades nothing —
// while a bridge that has NEVER seen a valid projection refuses durable
// capture (the upstream gate).
//
// Rule (frozen contract): a denied room means NO body and NO attachment
// capture at all; an admitted event is stamped with the applied revision.

import (
	"context"
	"encoding/json"
	"log/slog"
	"os"
	"path/filepath"
	"sync"
	"sync/atomic"
	"time"
)

// Projection is the file's shape, exactly as the wire contract freezes it.
// revision stays an exact JSON integer — Go decodes straight into the int64
// field, never through float64, so values up to 2^53-1 round-trip.
type Projection struct {
	Revision              int                 `json:"revision"`
	GeneratedMS           int64               `json:"generated_ms"`
	Enabled               bool                `json:"enabled"`
	AccountKey            string              `json:"account_key"`
	Rooms                 []string            `json:"rooms"`
	DMAllowedSenders      []string            `json:"dm_allowed_senders"`
	SelfAliases           []string            `json:"self_aliases"`
	DeterministicTriggers []string            `json:"deterministic_triggers"`
	ReceiptEmoji          *string             `json:"receipt_emoji"`
	RoomReceiptSenders    map[string][]string `json:"room_receipt_senders"`
}

// member reports whether id is in the list — the literal "*" is the
// contract's policy wildcard, only meaningful inside room_receipt_senders.
func member(list []string, id string) bool {
	for _, s := range list {
		if s == id {
			return true
		}
	}
	return false
}

// Admit reports whether this projection lets the conversation in — the
// CAPTURE decision — and, separately, the sender-level receipt grant of an
// admitted event. Capture admission never implies a receipt grant: the two
// returns are independent answers, not one answer reused.
//
// `conversationType` carries the wire's conversation_type; Instagram's
// inbound delta has no group/DM discriminator, so callers pass what the
// event says (usually "group"). dm_allowed_senders binds only on an actual
// "dm" — and never grants a group receipt.
func (p *Projection) Admit(conversationID, conversationType, senderID string) (admitted, receiptGranted bool) {
	if p == nil || !p.Enabled {
		return false, false
	}
	allowed := member(p.Rooms, conversationID)
	if !allowed && conversationType == "dm" {
		allowed = member(p.DMAllowedSenders, senderID)
	}
	if !allowed {
		return false, false
	}
	return true, p.ReceiptGranted(conversationID, conversationType, senderID)
}

// ReceiptGranted is the sender-authorization half of receipt eligibility —
// checked at capture AND re-checked against the CURRENT projection before a
// queued receipt fires, so a later revocation suppresses the pending phase
// before any external effect.
//
// Group receipts require room_receipt_senders[conversationID] membership or
// the literal "*" policy wildcard; a missing field or missing room entry is
// a deny, never an inferred grant. DM receipts require dm_allowed_senders
// membership — the two grants are never interchangeable.
func (p *Projection) ReceiptGranted(conversationID, conversationType, senderID string) bool {
	if p == nil || !p.Enabled {
		return false
	}
	if conversationType == "dm" {
		return member(p.DMAllowedSenders, senderID)
	}
	senders, ok := p.RoomReceiptSenders[conversationID]
	if !ok {
		return false
	}
	for _, s := range senders {
		if s == "*" || s == senderID {
			return true
		}
	}
	return false
}

// ProjectionSource polls the file and serves the last good document.
type ProjectionSource struct {
	path string

	mu      sync.RWMutex
	current *Projection
	mtime   time.Time
	// seenValid is "a valid projection has been loaded at least once" — the
	// gate that refuses capture until the app has published one.
	seenValid atomic.Bool
}

func NewProjectionSource(dir, platform string) *ProjectionSource {
	return &ProjectionSource{path: filepath.Join(dir, platform+".json")}
}

// Poll reloads the file when its mtime changed; on a parse or read failure
// the last good document is kept and the failure is logged (no silent
// projection swap, no dropped admission on a torn write).
func (s *ProjectionSource) Poll(ctx context.Context) {
	st, err := os.Stat(s.path)
	if err != nil {
		if !os.IsNotExist(err) {
			slog.WarnContext(ctx, "projection stat failed", "error", err)
		}
		return
	}
	s.mu.RLock()
	fresh := st.ModTime().Equal(s.mtime)
	s.mu.RUnlock()
	if fresh {
		return
	}
	raw, err := os.ReadFile(s.path)
	if err != nil {
		slog.WarnContext(ctx, "projection read failed; keeping last good", "error", err)
		return
	}
	var p Projection
	if err := json.Unmarshal(raw, &p); err != nil {
		slog.ErrorContext(ctx, "projection invalid; keeping last good", "error", err)
		return
	}
	s.mu.Lock()
	s.current = &p
	s.mtime = st.ModTime()
	s.mu.Unlock()
	s.seenValid.Store(true)
	slog.InfoContext(ctx, "admission projection loaded", "revision", p.Revision, "rooms", len(p.Rooms), "enabled", p.Enabled)
}

// Current is the last good document, or nil until one has been validated.
func (s *ProjectionSource) Current() *Projection {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.current
}

// SeenValid is the startup gate: false until one valid projection exists.
func (s *ProjectionSource) SeenValid() bool { return s.seenValid.Load() }
