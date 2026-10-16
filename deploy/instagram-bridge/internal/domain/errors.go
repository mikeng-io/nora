package domain

// The closed error set (design/instagram-adoption/16 §Domain).
//
// Every failure that crosses a layer is one of these six KINDS. The gateway
// maps what Instagram and the library say onto them; the HTTP layer maps each
// kind onto exactly one status. A kind is a classification, not a message:
// Classify attaches a kind to an existing error WITHOUT changing its text, so
// the operator-facing detail that already reaches a response body or a log
// line stays exactly what it was.

import "errors"

var (
	// ErrInvalidID is a caller-supplied id that does not parse into the value
	// object it names: a username where an igid belongs, a long thread id
	// where the short one belongs. The caller's mistake; no retry fixes it.
	ErrInvalidID = errors.New("invalid id")
	// ErrNotFound is an id that parsed, reached Instagram, and names nothing.
	// "I couldn't find that account", as distinct from "I couldn't look".
	ErrNotFound = errors.New("not found")
	// ErrNeedsHuman is a session Instagram has challenged, checkpointed or
	// logged out. Retrying it is how a captcha becomes a ban.
	ErrNeedsHuman = errors.New("needs a human")
	// ErrUnavailable is the transport being down in a way expected to heal
	// on its own: no session yet, a socket reconnecting.
	ErrUnavailable = errors.New("unavailable")
	// ErrUpstreamRejected is Instagram answering, with an error. A GraphQL
	// `errors` array inside an HTTP 200 is one; so is a read that came back
	// without the thing it was asked for.
	ErrUpstreamRejected = errors.New("upstream rejected")
	// ErrUnsupported is a request no mechanism exists for: a shortcode no
	// known decoding reads, or a read whose GraphQL `doc_id` is not
	// configured (INSTAGRAM_STORY_DOC_ID, INSTAGRAM_TIMELINE_DOC_ID).
	ErrUnsupported = errors.New("unsupported")
)

// ErrStaleQuery REFINES ErrUnsupported, the way ErrTooLarge refines
// ErrUpstreamRejected: Instagram no longer knows a configured GraphQL
// document (its `doc_id` rotated with a new web build). The read has no
// working mechanism until an operator captures the id again, which is
// ErrUnsupported's meaning; it is named because the consequence differs from
// a plain refusal: the LOGIN is fine, so a caller halts that one read and
// must not back the login off (design/cognition-loop/22-social-stream.md §4).
var ErrStaleQuery = refine(ErrUnsupported, "stale query document")

// ErrTooLarge REFINES ErrUpstreamRejected; it is not a seventh kind.
//
// Instagram refusing a message as too long is Instagram answering with an
// error, so it is ErrUpstreamRejected by definition — `errors.Is(err,
// ErrUpstreamRejected)` holds for it. It is named separately because the
// caller's consequence differs: a 50,000-character message can never be
// accepted, so it must not read as a failure worth retrying. The design's own
// status list carries 413 with no kind behind it; this is that kind's
// refinement, and it keeps the closed set closed.
var ErrTooLarge = refine(ErrUpstreamRejected, "payload too large")

// Kinds is the closed set, in the order KindOf tests it.
var Kinds = []error{
	ErrInvalidID, ErrNotFound, ErrNeedsHuman, ErrUnavailable, ErrUpstreamRejected, ErrUnsupported,
}

// KindOf answers which of the closed set err belongs to, or nil when it
// belongs to none. A nil answer is the fail-closed case: whatever consumes it
// treats an unclassified failure as the most conservative one it has.
func KindOf(err error) error {
	for _, kind := range Kinds {
		if errors.Is(err, kind) {
			return kind
		}
	}
	return nil
}

// Classify marks err as belonging to kind, leaving its message untouched.
//
// errors.Is answers true for BOTH the kind and the original error, so a
// caller that already matched on a specific sentinel keeps working.
func Classify(kind, err error) error {
	if err == nil {
		return nil
	}
	return &classified{kind: kind, err: err}
}

type classified struct{ kind, err error }

func (c *classified) Error() string   { return c.err.Error() }
func (c *classified) Unwrap() []error { return []error{c.err, c.kind} }

type refinement struct {
	parent error
	msg    string
}

func refine(parent error, msg string) error { return &refinement{parent: parent, msg: msg} }

func (r *refinement) Error() string { return r.parent.Error() + ": " + r.msg }
func (r *refinement) Unwrap() error { return r.parent }
