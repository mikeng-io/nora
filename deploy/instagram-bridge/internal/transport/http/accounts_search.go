package httptransport

// GET /accounts?query= — the account-search resource
// (design/instagram-adoption/16 §Resources, 17 P9, P11).
//
// # Why this route exists before P11 does
//
// P9 is a measurement: does instameow's own user search find an arbitrary
// public Instagram account by its EXACT username, or only accounts already in
// Nora's DM contacts? The operator chose to run that probe through THIS real
// route rather than stand up a second client against the live session, which
// could disturb the only session there is. This route is also P11's route, so
// building it once now serves both.
//
// # The NEW response conventions, from day one
//
// Every other route in this package still answers the body it had before
// `16`: `{"ok": ..., "error": "..."}`. This route has no old body to keep
// compatible, so it is the first one on `16`'s real conventions:
//
//   - success is `{"items": [...], "next_cursor": null}` — no `ok` field;
//   - failure is one envelope, `{"error": {"code": "...", "message": "..."}}`,
//     `code` drawn from the closed domain set, at the FULL status table `16`
//     specifies (400/404/409/413/500/501/502/503) — not the older
//     `statusForError` in reads.go, which still answers 500 for
//     ErrUpstreamRejected and has no 501 or 502. That table moves onto the
//     old routes in P14, in the same PR that moves the Python client.
//
// # Validation, before any use case runs
//
//   - `query` missing, empty, or whitespace-only: 400.
//   - `query` longer than `maxSearchQueryLength`: 400.
//   - both `query` and `username` given: 400 (mutually exclusive).
//   - `username` alone: 501. `GET /accounts?username=` is P10's route
//     (design 16 §Resources) and its mechanism depends on P9's own result, so
//     it is not built yet. 501 over 404, because R-27 already has a name for
//     "a verb that exists and fails closed for a reason": `unavailable`, with
//     the reason attached — not a 404 that reads as "this API has no such
//     thing", which is a different and false claim.
//
// # The query text
//
// Never a span attribute, span name, status description, event, or log line
// — `16` §Tracing's never-list, applied to a name typed about a person. See
// TestSearchQueryNeverEntersASpan in cmd/instagram-bridge. A RESULT COUNT is
// fine to record (fine to log, per the task that specified this route): it is
// evidence P9's probe needs, and it says nothing about who was searched for.

import (
	"errors"
	"fmt"
	"net/http"
	"strings"

	"instagram-bridge/internal/app"
	"instagram-bridge/internal/domain"

	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/trace"
)

// maxSearchQueryLength bounds `query`. Instagram's own handle alphabet caps a
// USERNAME at 30 characters (domain.ParseUsername's usernamePattern), but
// `query` also matches full names and partial text, which a strict
// username-length cap would wrongly refuse. 64 is double that with margin,
// chosen as the sane maximum the design calls for rather than a limit
// instameow itself documents — nothing has measured the library's own
// ceiling, so this is deliberately conservative and may move if that changes.
const maxSearchQueryLength = 64

// errorBody is the ONE failure shape every new-convention route answers:
// `code` from the closed domain set, `message` for an operator.
type errorBody struct {
	Code    string `json:"code"`
	Message string `json:"message"`
}

type errorEnvelope struct {
	Error errorBody `json:"error"`
}

func writeErrorEnvelope(w http.ResponseWriter, status int, code, message string) {
	writeJSON(w, status, errorEnvelope{Error: errorBody{Code: code, Message: message}})
}

// statusForDomainError is design 16's FULL status table for the new
// conventions — 400/404/409/413/500/501/502/503 — which differs from the
// older statusForError in reads.go (500 for ErrUpstreamRejected, no 501, no
// 502) only because the old routes have not migrated onto it yet (P14).
func statusForDomainError(err error) int {
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
		// match here and this route would lose the 413.
		return http.StatusRequestEntityTooLarge
	case errors.Is(err, domain.ErrUnsupported):
		return http.StatusNotImplemented
	case errors.Is(err, domain.ErrUpstreamRejected):
		return http.StatusBadGateway
	case errors.Is(err, domain.ErrUnavailable):
		return http.StatusServiceUnavailable
	default:
		// Fail closed: an unclassified failure is 500, never a status that
		// claims to know whether retrying would help.
		return http.StatusInternalServerError
	}
}

// errorCode names the closed set's member for the envelope's `code`. It
// mirrors statusForDomainError's classification exactly, so a status and a
// code can never disagree about what happened.
func errorCode(err error) string {
	switch {
	case errors.Is(err, domain.ErrInvalidID):
		return "invalid_id"
	case errors.Is(err, domain.ErrNotFound):
		return "not_found"
	case errors.Is(err, domain.ErrNeedsHuman):
		return "needs_human"
	case errors.Is(err, domain.ErrTooLarge):
		return "too_large"
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

// failEnvelope writes a use case's error in the new envelope. It never
// includes the query — the handler already refused to pass it to anything an
// error could echo back — and never logs it either.
func failEnvelope(w http.ResponseWriter, err error) {
	var notReady *app.NotReadyError
	if errors.As(err, &notReady) {
		msg := string(notReady.State)
		if notReady.Detail != "" {
			msg = msg + ": " + notReady.Detail
		}
		writeErrorEnvelope(w, http.StatusConflict, "needs_human", msg)
		return
	}
	writeErrorEnvelope(w, statusForDomainError(err), errorCode(err), err.Error())
}

// --- GET /accounts?query= ------------------------------------------------

// accountSummary is one search hit: the library's own shape, forwarded with
// no re-ranking and no filtering — P9's probe needs the raw answer.
//
// No `is_private`: the search this route calls does not carry that field
// (domain.AccountSummary's doc comment says why). Adding a field the wire
// never sends would be inventing one.
type accountSummary struct {
	IGID     string `json:"igid"`
	Username string `json:"username"`
	FullName string `json:"full_name"`
	// FBID is the messaging id, or "" when the search has no row for it — a
	// missing join key, not a failed read, the same as GET /accounts/{igid}.
	FBID       string `json:"fbid"`
	IsVerified bool   `json:"is_verified"`
}

// searchAccountsBody is the 200 shape: `16`'s list convention. `next_cursor`
// stays null — the library answers with its whole result set in one call, so
// inventing a cursor would promise paging Instagram never offered.
type searchAccountsBody struct {
	Items      []accountSummary `json:"items"`
	NextCursor *string          `json:"next_cursor"`
}

func accountSummaryOf(a domain.AccountSummary) accountSummary {
	return accountSummary{
		IGID: a.IGID, Username: a.Username, FullName: a.FullName,
		FBID: a.FBID, IsVerified: a.IsVerified,
	}
}

func accountSummariesOf(in []domain.AccountSummary) []accountSummary {
	out := make([]accountSummary, 0, len(in))
	for _, a := range in {
		out = append(out, accountSummaryOf(a))
	}
	return out
}

// handleAccountsSearch answers GET /accounts?query=.
func (s *Server) handleAccountsSearch(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	hasQuery, hasUsername := q.Has("query"), q.Has("username")

	switch {
	case hasQuery && hasUsername:
		writeErrorEnvelope(w, http.StatusBadRequest, "invalid_id",
			"query and username are mutually exclusive; send exactly one")
		return
	case hasUsername:
		// GET /accounts?username= is P10's route; its mechanism depends on
		// P9's own probe result and is not built yet.
		writeErrorEnvelope(w, http.StatusNotImplemented, "unsupported",
			"GET /accounts?username= is not built yet (design/instagram-adoption/17 P10)")
		return
	}

	raw := q.Get("query")
	trimmed := strings.TrimSpace(raw)
	if trimmed == "" {
		writeErrorEnvelope(w, http.StatusBadRequest, "invalid_id",
			"query is required and must not be blank")
		return
	}
	if len(raw) > maxSearchQueryLength {
		writeErrorEnvelope(w, http.StatusBadRequest, "invalid_id",
			fmt.Sprintf("query must be at most %d characters", maxSearchQueryLength))
		return
	}

	results, err := s.app.SearchAccounts(r.Context(), trimmed)
	if err != nil {
		failEnvelope(w, err)
		return
	}
	// A result COUNT is allowed where the query text is not (file header).
	// It rides the request's own server span rather than a log line, which
	// keeps this route on "per-request success is not logged"
	// (internal/observability) while still giving P9's probe something to
	// read in Jaeger.
	trace.SpanFromContext(r.Context()).SetAttributes(
		attribute.Int("instagram.search.result_count", len(results)))
	writeJSON(w, http.StatusOK, searchAccountsBody{Items: accountSummariesOf(results), NextCursor: nil})
}
