package httptransport

// GET /accounts?query= and GET /accounts?username= — the account-search and
// account-by-username resources
// (design/instagram-adoption/16 §Resources, 17 P9, P10, P11).
//
// # Why ?query= exists before P11 does
//
// P9 is a measurement: does instameow's own user search find an arbitrary
// public Instagram account by its EXACT username, or only accounts already in
// Nora's DM contacts? The operator chose to run that probe through THIS real
// route rather than stand up a second client against the live session, which
// could disturb the only session there is. This route is also P11's route, so
// building it once now serves both.
//
// # ?username= is P10, gated on P9's own measurement
//
// P9 measured (17 §P9) that `SearchUsers` finds accounts outside Nora's DM
// contacts, private ones included, and that ONE EXACT MATCH WAS NOT THE FIRST
// RESULT. So `?username=` never takes `items[0]`: it calls the same search
// and keeps only the hits whose username equals the input, case-insensitively
// (app.LookupAccountByUsername — the rule lives there, not here, so it is
// provable independent of this file). Zero matches is a normal empty list;
// two or more cannot happen for a real username and answers 502
// `upstream_rejected`.
//
// # The NEW response conventions, from day one
//
// Every other route in this package still answers the body it had before
// `16`: `{"ok": ..., "error": "..."}`. This route has no old body to keep
// compatible, so it is the first one on `16`'s real conventions:
//
//   - success is `{"items": [...], "next_cursor": null}` — no `ok` field, the
//     SAME shape for both `?query=` and `?username=` (0 or 1 items);
//   - failure is one envelope, `{"error": {"code": "...", "message": "..."}}`,
//     `code` drawn from the closed domain set, at the FULL status table `16`
//     specifies (400/404/409/413/500/501/502/503) — not the older
//     `statusForError` in reads.go, which still answers 500 for
//     ErrUpstreamRejected and has no 501 or 502. That table moves onto the
//     old routes in P14, in the same PR that moves the Python client.
//
// # Validation, before any use case runs — INCLUDING the session gate
//
//   - `query` given more than once (`?query=a&query=b`): 400.
//   - `username` given more than once: 400, the same reason as `query`.
//   - `query` missing, empty, or whitespace-only: 400.
//   - `query` longer than `maxSearchQueryLength`: 400.
//   - both `query` and `username` given: 400 (mutually exclusive).
//   - `username` not `[A-Za-z0-9._]{1,30}` after stripping one leading `@`
//     (domain.ParseUsername, Instagram's own handle alphabet): 400, with a
//     FIXED message — see handleAccountsByUsername.
//
// Every check above runs BEFORE `s.app.SearchAccounts` / `s.app.LookupAccountByUsername`
// is called, which is where the session gate lives (`app.Service.ready`,
// checked first inside every Instagram-facing use case). That is deliberate
// ordering, not an accident of where the code happens to sit: design 16
// §Resources says "a malformed id is 400 before any use case runs", and a
// malformed or missing `query`/`username` is exactly that. The OLD routes
// answer the session gate (409) before a request-shape problem in some
// cases — that is P14's reorder debt on THEM, not a pattern this route
// copies. See TestAccountsSearchValidationRunsBeforeSessionGate.
//
// # The query text and the username
//
// Never a span attribute, span name, status description, event, log line, or
// error envelope message — `16` §Tracing's never-list, applied to a name
// typed about a person. See TestSearchQueryNeverEntersASpan and
// TestSearchQueryNeverLeaksOnFailure (the query) and
// TestUsernameNeverEntersASpan and TestUsernameLookupNeverLeaksOnFailure (the
// username) in cmd/instagram-bridge. The failure path is the harder half: an
// upstream error's own text can echo bad input back (a validation error
// quoting the query), so `searchAccounts` (gateway/instameow/search.go)
// wraps any SearchUsers failure in `upstreamSearchError`, whose `Error()` is
// a FIXED description — never the original message — before it ever reaches
// a span, a log line, or this file's envelope. `domain.ParseUsername`'s OWN
// error text embeds the raw input (`ids.go`'s `invalid("not an instagram
// username: %q", s)`), so this file never forwards it either — see
// invalidUsernameMessage. A RESULT COUNT is fine to record on success (fine
// to log, per the task that specified this route): it is evidence P9's probe
// needs, and it says nothing about who was searched for.

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
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

// failEnvelope writes a use case's error in the new envelope, and logs the
// STATUS and CODE only — never the query, and never `err.Error()` raw. By
// the time an error reaches here it is either a NotReadyError (state/detail,
// no query involved) or something that passed through searchAccounts'
// upstreamSearchError wrapper, whose text is already fixed — but this
// function does not rely on that upstream guarantee holding forever; it logs
// the classification, not the message, so a future error source cannot
// reintroduce a leak here by surprise. The same holds for `GET
// /posts/{shortcode}` (posts.go): its gateway errors are fixed texts, and a
// caption never reaches an error at all. `route` names the log line.
func failEnvelope(ctx context.Context, w http.ResponseWriter, route string, err error) {
	var notReady *app.NotReadyError
	if errors.As(err, &notReady) {
		msg := string(notReady.State)
		if notReady.Detail != "" {
			msg = msg + ": " + notReady.Detail
		}
		slog.WarnContext(ctx, route+" failed", "status", http.StatusConflict, "code", "needs_human")
		writeErrorEnvelope(w, http.StatusConflict, "needs_human", msg)
		return
	}
	status, code := statusForDomainError(err), errorCode(err)
	slog.WarnContext(ctx, route+" failed", "status", status, "code", code)
	writeErrorEnvelope(w, status, code, err.Error())
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

// handleAccountsSearch answers GET /accounts?query= and GET /accounts?username=.
func (s *Server) handleAccountsSearch(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	queryValues, usernameValues := q["query"], q["username"]
	hasQuery, hasUsername := len(queryValues) > 0, len(usernameValues) > 0

	switch {
	case len(queryValues) > 1:
		// `?query=a&query=b` silently took the first value before this
		// check existed — a caller could not tell which one the bridge
		// acted on. Refused instead, the same way a malformed id is.
		writeErrorEnvelope(w, http.StatusBadRequest, "invalid_id",
			"query must be given at most once")
		return
	case len(usernameValues) > 1:
		// The same reasoning as `query` above: a repeated `username` must
		// not silently pick one.
		writeErrorEnvelope(w, http.StatusBadRequest, "invalid_id",
			"username must be given at most once")
		return
	case hasQuery && hasUsername:
		writeErrorEnvelope(w, http.StatusBadRequest, "invalid_id",
			"query and username are mutually exclusive; send exactly one")
		return
	case hasUsername:
		s.handleAccountsByUsername(w, r, usernameValues[0])
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
		failEnvelope(r.Context(), w, "accounts_search", err)
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

// --- GET /accounts?username= ----------------------------------------------

// invalidUsernameMessage is the FIXED envelope message for a malformed
// username. `domain.ParseUsername`'s own error text embeds the raw input
// (`ids.go`: `invalid("not an instagram username: %q", s)`), which this file's
// header says never to forward — so a bad username answers this message,
// never `err.Error()`.
const invalidUsernameMessage = "username must be 1-30 characters from [A-Za-z0-9._], with at most one leading @"

// handleAccountsByUsername answers GET /accounts?username= (17 P10), reached
// from handleAccountsSearch once `username` is the only param given.
//
// The username is parsed HERE, before the use case, so a malformed one is
// 400 whatever the session's state — the same "a malformed id is 400 before
// any use case runs" order `handleAccountsSearch`'s query validation and
// `handlePost`'s shortcode parse both already follow.
func (s *Server) handleAccountsByUsername(w http.ResponseWriter, r *http.Request, raw string) {
	username, err := domain.ParseUsername(raw)
	if err != nil {
		writeErrorEnvelope(w, http.StatusBadRequest, "invalid_id", invalidUsernameMessage)
		return
	}
	matches, err := s.app.LookupAccountByUsername(r.Context(), username)
	if err != nil {
		failEnvelope(r.Context(), w, "accounts_by_username", err)
		return
	}
	// A match COUNT, like the search route's result count (file header): it
	// says how many accounts matched (0 or 1 on success; the use case never
	// returns more without erroring), never who was looked up.
	trace.SpanFromContext(r.Context()).SetAttributes(
		attribute.Int("instagram.accounts_by_username.match_count", len(matches)))
	writeJSON(w, http.StatusOK, searchAccountsBody{Items: accountSummariesOf(matches), NextCursor: nil})
}
