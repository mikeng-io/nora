package httptransport

// The ONE failure shape and the ONE status table, for every route
// (design/instagram-adoption/16 §Resources, 17 §P14).
//
// Before P14 there were two: `statusForError` (reads.go) with the old
// `{"ok": false, "error": "..."}` body, which answered 500 for
// ErrUpstreamRejected, and `statusForDomainError` here with the envelope,
// which the routes built since P8 used. P14 moved every route onto this one
// and deleted the other, so a status and a code can never disagree between
// two routes about the same failure.
//
// The status is what Nora's adapter decides on, and it is NOT a generic
// success/failure split:
//
//	400  the caller's request is malformed: a path id that does not parse, a
//	     body that is not JSON, a required field missing
//	404  the id names nothing (an unmatched PATH is the mux's own plain-text
//	     404, which the Python client tells apart by its body)
//	409  the session is checkpointed or logged out: NOT retryable without a
//	     human. Retrying this is how a captcha becomes a ban.
//	413  a payload over a limit: the body, or a message Instagram refused as
//	     too long. Never retryable.
//	500  a failure nobody has a name for (fail closed: never 503)
//	501  no mechanism exists for this request (`unsupported`), including a
//	     GraphQL read whose doc_id is not configured. `stale_query` is a 501
//	     too: Instagram answered a configured document with an error naming
//	     the DOCUMENT (its doc_id rotated with a web build), so the read has
//	     no working mechanism until an operator captures the id again
//	     (design/cognition-loop/22-social-stream.md §1a). It is NOT a session
//	     problem and NOT a 502: a caller halts that one read and must not
//	     back the login off, which a 502 asks for (§4).
//	502  Instagram answered, and the answer was an error
//	503  no session client yet, or the socket is down: retryable, the bridge
//	     heals itself
//
// The comment write adds its guards' refusals (design 19; app/comments.go),
// each with its OWN code, checked before the table above because two of them
// refine a kind in it:
//
//	403  writes_disabled     INSTAGRAM_COMMENT_WRITES is not `on`
//	403  writes_blocked      Instagram answered a write with an action block
//	                         (feedback_required, spam, sentry_block, or a 429):
//	                         latched off until an operator clears it. NOT a
//	                         429 or 503, which would invite a retry.
//	403  not_a_summons       the reply target is not a summons (no summons card
//	                         for this post in 7 days, or the post's comments
//	                         page does not show it by the card's sender,
//	                         mentioning this account; or it is missing)
//	409  already_replied     this summons already had its one attempt
//	400  invalid_text        empty, over 2200 characters, or not UTF-8
//	429  daily_cap           the rolling 24-hour cap is reached
//	429  too_soon            the minimum interval since the last claim
//	503  ledger_unavailable  comments.json cannot be read or written
//
// Collapsing 409 into 503 would make the adapter hammer a challenged account
// forever; collapsing 503 into 500 would make it give up on a socket that
// reconnects in seconds. They are distinct because the consequences are.

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net/http"

	"instagram-bridge/internal/app"
	"instagram-bridge/internal/domain"
)

// errorBody is the ONE failure shape every route answers: `code` from the
// closed domain set, `message` for an operator.
//
// `stage` is present on exactly one failure: a thread route whose ROOM
// LOOKUP against Instagram failed, as distinct from the verb that would have
// followed it (`thread_lookup`). It was the old body's
// `"error": "thread_lookup_failed"`, kept as a field because `code` is drawn
// from the closed set and a lookup failure has a kind of its own (a
// checkpoint discovered there is still `needs_human`, 409).
type errorBody struct {
	Code    string `json:"code"`
	Message string `json:"message"`
	Stage   string `json:"stage,omitempty"`
}

type errorEnvelope struct {
	Error errorBody `json:"error"`
}

// stageThreadLookup marks a failed room lookup (errorBody.Stage).
const stageThreadLookup = "thread_lookup"

func writeErrorEnvelope(w http.ResponseWriter, status int, code, message string) {
	writeJSON(w, status, errorEnvelope{Error: errorBody{Code: code, Message: message}})
}

// invalidRequest is a 400 with a FIXED message. Every request-shape refusal
// goes through it, so no handler forwards a parser's own error text (which can
// embed the raw input) into the envelope.
//
// The code is `invalid_id`, the closed set's only caller-error kind, which
// `GET /accounts?query=` already answered for a blank query before P14. The
// set has no separate kind for "a malformed body"; see 16 §Migration's P14
// note.
func invalidRequest(w http.ResponseWriter, message string) {
	writeErrorEnvelope(w, http.StatusBadRequest, "invalid_id", message)
}

// statusForDomainError is design 16's status table, over the closed domain
// error set and nothing else. Every kind maps onto exactly one status, and an
// error with no kind is 500, never 200 and never 503: 503 is a claim that
// retrying will work, and making that claim on an unknown failure is how a
// permanent error turns into an infinite retry loop.
// refusals is the comment write's guard table: sentinel, status, code. It
// is consulted FIRST by both functions below, so a refusal that refines a
// closed-set kind (invalid_text, ledger_unavailable) keeps its own code.
var refusals = []struct {
	err    error
	status int
	code   string
}{
	{domain.ErrWritesDisabled, http.StatusForbidden, "writes_disabled"},
	{domain.ErrWritesBlocked, http.StatusForbidden, "writes_blocked"},
	{domain.ErrNotASummons, http.StatusForbidden, "not_a_summons"},
	{domain.ErrAlreadyReplied, http.StatusConflict, "already_replied"},
	{domain.ErrInvalidCommentText, http.StatusBadRequest, "invalid_text"},
	{domain.ErrDailyCap, http.StatusTooManyRequests, "daily_cap"},
	{domain.ErrTooSoon, http.StatusTooManyRequests, "too_soon"},
	{domain.ErrLedgerUnavailable, http.StatusServiceUnavailable, "ledger_unavailable"},
}

func refusalOf(err error) (int, string, bool) {
	for _, r := range refusals {
		if errors.Is(err, r.err) {
			return r.status, r.code, true
		}
	}
	return 0, "", false
}

func statusForDomainError(err error) int {
	if status, _, ok := refusalOf(err); ok {
		return status
	}
	switch {
	case err == nil:
		return http.StatusOK
	case errors.Is(err, domain.ErrInvalidID):
		return http.StatusBadRequest
	case errors.Is(err, domain.ErrNotFound):
		return http.StatusNotFound
	case errors.Is(err, domain.ErrNeedsHuman):
		return http.StatusConflict
	case errors.Is(err, domain.ErrTooLarge):
		// Checked BEFORE ErrUpstreamRejected: ErrTooLarge refines it
		// (domain/errors.go), so errors.Is against the parent would also
		// match here and the route would lose the 413.
		return http.StatusRequestEntityTooLarge
	case errors.Is(err, domain.ErrUnsupported):
		// ErrStaleQuery refines it, and shares its status.
		return http.StatusNotImplemented
	case errors.Is(err, domain.ErrUpstreamRejected):
		return http.StatusBadGateway
	case errors.Is(err, domain.ErrUnavailable):
		return http.StatusServiceUnavailable
	default:
		return http.StatusInternalServerError
	}
}

// errorCode names the closed set's member for the envelope's `code`. It
// mirrors statusForDomainError's classification exactly, so a status and a
// code can never disagree about what happened.
func errorCode(err error) string {
	if _, code, ok := refusalOf(err); ok {
		return code
	}
	switch {
	case errors.Is(err, domain.ErrInvalidID):
		return "invalid_id"
	case errors.Is(err, domain.ErrNotFound):
		return "not_found"
	case errors.Is(err, domain.ErrNeedsHuman):
		return "needs_human"
	case errors.Is(err, domain.ErrTooLarge):
		return "too_large"
	case errors.Is(err, domain.ErrStaleQuery):
		// Before ErrUnsupported, which it refines (ErrTooLarge's reason).
		return "stale_query"
	case errors.Is(err, domain.ErrUnsupported):
		return "unsupported"
	case errors.Is(err, domain.ErrUpstreamRejected):
		return "upstream_rejected"
	case errors.Is(err, domain.ErrUnavailable):
		return "unavailable"
	default:
		return "internal"
	}
}

// failEnvelope writes a use case's error in the envelope, and logs the
// STATUS and CODE (plus whatever ids the caller passes in `logAttrs`), never
// `err.Error()`. By the time an error reaches here its text is either fixed
// (the gateway replaces an upstream message that could echo a query, a
// caption or message text before it reaches a span), a session state, or
// an id; but this function does not rely on every error source keeping that
// promise forever. It logs the classification, not the message, so a future
// error source cannot put message text into a log line by surprise. The
// envelope's `message` is the error's text, which is what an operator reads
// on a curl; the gateway is what keeps that text free of people's words.
//
// Two stage errors keep their meaning:
//
//   - the session gate (app.NotReadyError): 409 `needs_human`, the message
//     naming the session state and its detail, because what the caller needs
//     is which human action is due;
//   - a room's route lookup failing against Instagram
//     (app.ThreadLookupError): the kind's own status and code, plus
//     `"stage": "thread_lookup"`, so a checkpoint discovered there reads 409
//     and not a 500 the adapter would retry. A lookup that failed only
//     because the socket is down is a plain 503, with no stage: nothing
//     about the room was learned.
//
// `route` names the log line.
func failEnvelope(ctx context.Context, w http.ResponseWriter, route string, err error, logAttrs ...any) {
	var notReady *app.NotReadyError
	if errors.As(err, &notReady) {
		msg := string(notReady.State)
		if notReady.Detail != "" {
			msg = msg + ": " + notReady.Detail
		}
		slog.WarnContext(ctx, route+" failed",
			append([]any{"status", http.StatusConflict, "code", "needs_human"}, logAttrs...)...)
		writeErrorEnvelope(w, http.StatusConflict, "needs_human", msg)
		return
	}
	status, code := statusForDomainError(err), errorCode(err)
	body := errorBody{Code: code, Message: err.Error()}
	var lookup *app.ThreadLookupError
	if errors.As(err, &lookup) && !errors.Is(err, domain.ErrUnavailable) {
		body.Stage = stageThreadLookup
	}
	slog.WarnContext(ctx, route+" failed",
		append([]any{"status", status, "code", code, "stage", body.Stage}, logAttrs...)...)
	writeJSON(w, status, errorEnvelope{Error: body})
}

// readJSON decodes a request body of at most `limit` bytes into `into`,
// answering 413 `too_large` over the limit and 400 for anything that is not
// one JSON value. It returns the raw bytes too, for the one route whose limit
// depends on what the body turned out to be (a text message is bounded
// tighter than a media one).
func readJSON(w http.ResponseWriter, r *http.Request, limit int64, into any) ([]byte, bool) {
	raw, err := io.ReadAll(http.MaxBytesReader(w, r.Body, limit))
	if err != nil {
		var tooBig *http.MaxBytesError
		if errors.As(err, &tooBig) {
			writeErrorEnvelope(w, http.StatusRequestEntityTooLarge, "too_large", "payload too large")
			return nil, false
		}
		invalidRequest(w, "the request body could not be read")
		return nil, false
	}
	if err := json.Unmarshal(raw, into); err != nil {
		invalidRequest(w, "invalid json body")
		return nil, false
	}
	return raw, true
}

func writeJSON(w http.ResponseWriter, status int, body any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(body)
}
