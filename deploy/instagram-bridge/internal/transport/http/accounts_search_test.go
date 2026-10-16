package httptransport

// GET /accounts?query=, tested against fake ports — the FIRST route on the
// new response conventions (accounts_search.go's file header): {"items":...,
// "next_cursor":null} on success, {"error":{"code","message"}} on failure,
// at 16's full status table (400/404/409/413/500/501/502/503).

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"instagram-bridge/internal/app"
	"instagram-bridge/internal/domain"
)

// searchOnlyPorts answers SearchAccounts from its own fields and fails any
// other Social method it is asked for — nothing in these tests should reach
// Account, SocialProfile or Story.
type searchOnlyPorts struct {
	hits []domain.AccountSummary
	err  error
}

func (searchOnlyPorts) Account(context.Context, domain.IGID) (domain.Account, error) {
	return domain.Account{}, errors.New("unused in these tests")
}
func (searchOnlyPorts) SocialProfile(context.Context, domain.IGID) domain.SocialRead {
	return domain.SocialRead{Err: errors.New("unused in these tests")}
}
func (searchOnlyPorts) Story(context.Context, domain.IGID) (domain.Story, error) {
	return domain.Story{}, errors.New("unused in these tests")
}
func (searchOnlyPorts) Items(context.Context, domain.IGID, domain.Username, string) (domain.TimelinePage, error) {
	return domain.TimelinePage{}, errors.New("unused in these tests")
}
func (searchOnlyPorts) Post(context.Context, domain.Shortcode) (domain.Post, error) {
	return domain.Post{}, errors.New("unused in these tests")
}
func (searchOnlyPorts) Mentions(context.Context) ([]domain.CommentMention, map[int]int, error) {
	return nil, nil, errors.New("unused in these tests")
}
func (f searchOnlyPorts) SearchAccounts(_ context.Context, query string) ([]domain.AccountSummary, error) {
	if f.err != nil {
		return nil, f.err
	}
	return f.hits, nil
}

func searchHandler(snap domain.Session, social searchOnlyPorts) http.Handler {
	messaging := failingPorts{err: errors.New("unused in these tests")}
	return New(app.New(fakeSession{snap}, messaging, social), noEvents{}, time.Now()).Routes()
}

func getSearch(h http.Handler, rawQuery string) *httptest.ResponseRecorder {
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/accounts"+rawQuery, nil))
	return rec
}

func decodeEnvelope(t *testing.T, rec *httptest.ResponseRecorder) errorEnvelope {
	t.Helper()
	var out errorEnvelope
	if err := json.Unmarshal(rec.Body.Bytes(), &out); err != nil {
		t.Fatalf("body is not an error envelope: %v (%s)", err, rec.Body.String())
	}
	return out
}

func decodeSearchBody(t *testing.T, rec *httptest.ResponseRecorder) searchAccountsBody {
	t.Helper()
	var out searchAccountsBody
	if err := json.Unmarshal(rec.Body.Bytes(), &out); err != nil {
		t.Fatalf("body did not decode: %v (%s)", err, rec.Body.String())
	}
	return out
}

// A successful search answers the item list, in the port's own order, with
// no "ok" field and next_cursor explicitly null.
func TestAccountsSearchSucceeds(t *testing.T) {
	h := searchHandler(authenticated, searchOnlyPorts{hits: []domain.AccountSummary{
		{IGID: "1", Username: "first", FullName: "First Person", FBID: "10", IsVerified: false},
		{IGID: "2", Username: "second", FullName: "", IsVerified: true},
	}})

	rec := getSearch(h, "?query=someone")
	if rec.Code != http.StatusOK {
		t.Fatalf("answered %d: %s", rec.Code, rec.Body.String())
	}
	if raw := rec.Body.String(); containsToken(raw, `"ok"`) {
		t.Errorf("body carries an ok field, which the new conventions drop: %s", raw)
	}
	body := decodeSearchBody(t, rec)
	if body.NextCursor != nil {
		t.Errorf("next_cursor = %v, want null — the library has no cursor to offer", body.NextCursor)
	}
	if len(body.Items) != 2 || body.Items[0].Username != "first" || body.Items[1].Username != "second" {
		t.Fatalf("items = %+v, want the port's two hits in order", body.Items)
	}
	if body.Items[0].FBID != "10" {
		t.Errorf("items[0].fbid = %q, want the port's value forwarded", body.Items[0].FBID)
	}
	if !body.Items[1].IsVerified {
		t.Error("items[1].is_verified was false; the port set it true")
	}
}

func containsToken(s, tok string) bool {
	for i := 0; i+len(tok) <= len(s); i++ {
		if s[i:i+len(tok)] == tok {
			return true
		}
	}
	return false
}

// An empty result set is a normal 200, not an error: nobody matched.
func TestAccountsSearchEmptyResultIsStill200(t *testing.T) {
	h := searchHandler(authenticated, searchOnlyPorts{hits: []domain.AccountSummary{}})
	rec := getSearch(h, "?query=nobody-matches-this")
	if rec.Code != http.StatusOK {
		t.Fatalf("answered %d, want 200 for a search that found nobody", rec.Code)
	}
	body := decodeSearchBody(t, rec)
	if len(body.Items) != 0 {
		t.Errorf("items = %v, want none", body.Items)
	}
}

// The four ways a request is refused before any use case runs.
func TestAccountsSearchValidationIsTheHandler(t *testing.T) {
	// A port that fails loudly if reached: every case below must refuse
	// before SearchAccounts is ever called.
	h := searchHandler(authenticated, searchOnlyPorts{err: errors.New("no use case should run")})

	for name, rawQuery := range map[string]string{
		"missing":        "",
		"empty":          "?query=",
		"whitespace":     "?query=%20%20%20",
		"too long":       "?query=" + repeat("a", maxSearchQueryLength+1),
		"query+username": "?query=someone&username=someone",
		"repeated query": "?query=a&query=b",
	} {
		t.Run(name, func(t *testing.T) {
			rec := getSearch(h, rawQuery)
			if rec.Code != http.StatusBadRequest {
				t.Fatalf("%s: answered %d, want 400: %s", name, rec.Code, rec.Body.String())
			}
			env := decodeEnvelope(t, rec)
			if env.Error.Code != "invalid_id" {
				t.Errorf("%s: code = %q, want invalid_id", name, env.Error.Code)
			}
		})
	}

	// At the boundary: exactly the max length is accepted (falls through to
	// the use case, which this test's port refuses loudly).
	rec := getSearch(h, "?query="+repeat("a", maxSearchQueryLength))
	if rec.Code == http.StatusBadRequest {
		t.Errorf("a query of exactly %d characters was refused as too long", maxSearchQueryLength)
	}
}

func repeat(s string, n int) string {
	out := make([]byte, 0, n)
	for len(out) < n {
		out = append(out, s...)
	}
	return string(out[:n])
}

// --- GET /accounts?username= (17 P10) --------------------------------------
//
// The exact-match rule itself lives in app.LookupAccountByUsername and is
// proven there (internal/app/app_test.go); these tests prove the ROUTE:
// which status and shape reach the caller, and that validation and the
// session gate order the same way ?query='s already do.

// The P9 case: the exact match is not the first hit, and this route must
// still return it rather than items[0].
func TestAccountsByUsernameExactMatchNotAtRankZero(t *testing.T) {
	h := searchHandler(authenticated, searchOnlyPorts{hits: []domain.AccountSummary{
		{IGID: "1", Username: "decoy_one", FullName: "Not The One"},
		{IGID: "2", Username: "decoy_two", FullName: "Still Not It"},
		{IGID: "3", Username: "target_user", FullName: "The Target"},
	}})
	rec := getSearch(h, "?username=target_user")
	if rec.Code != http.StatusOK {
		t.Fatalf("answered %d: %s", rec.Code, rec.Body.String())
	}
	body := decodeSearchBody(t, rec)
	if len(body.Items) != 1 || body.Items[0].Username != "target_user" || body.Items[0].IGID != "3" {
		t.Fatalf("items = %+v, want exactly the exact match at rank 2", body.Items)
	}
}

// The easy case: the exact match is also the first hit.
func TestAccountsByUsernameExactMatchAtRankZero(t *testing.T) {
	h := searchHandler(authenticated, searchOnlyPorts{hits: []domain.AccountSummary{
		{IGID: "1", Username: "target_user", FullName: "The Target"},
		{IGID: "2", Username: "decoy", FullName: "Not It"},
	}})
	rec := getSearch(h, "?username=target_user")
	if rec.Code != http.StatusOK {
		t.Fatalf("answered %d: %s", rec.Code, rec.Body.String())
	}
	body := decodeSearchBody(t, rec)
	if len(body.Items) != 1 || body.Items[0].Username != "target_user" {
		t.Fatalf("items = %+v, want exactly the exact match", body.Items)
	}
}

// A case difference between the request and the hit still matches.
func TestAccountsByUsernameIsCaseInsensitive(t *testing.T) {
	h := searchHandler(authenticated, searchOnlyPorts{hits: []domain.AccountSummary{
		{IGID: "1", Username: "Target_User", FullName: "The Target"},
	}})
	rec := getSearch(h, "?username=target_user")
	if rec.Code != http.StatusOK {
		t.Fatalf("answered %d: %s", rec.Code, rec.Body.String())
	}
	body := decodeSearchBody(t, rec)
	if len(body.Items) != 1 || body.Items[0].Username != "Target_User" {
		t.Fatalf("items = %+v, want the case-different exact match", body.Items)
	}
}

// No hit equals the input exactly: an empty list, the same "search found
// nobody" convention ?query= already uses, never a 404.
func TestAccountsByUsernameNoExactMatchIsEmptyList(t *testing.T) {
	h := searchHandler(authenticated, searchOnlyPorts{hits: []domain.AccountSummary{
		{IGID: "1", Username: "target_userx"},
		{IGID: "2", Username: "xtarget_user"},
	}})
	rec := getSearch(h, "?username=target_user")
	if rec.Code != http.StatusOK {
		t.Fatalf("answered %d, want 200 with an empty list: %s", rec.Code, rec.Body.String())
	}
	body := decodeSearchBody(t, rec)
	if len(body.Items) != 0 {
		t.Errorf("items = %v, want none", body.Items)
	}
}

// Two exact, case-insensitive matches cannot happen for a real username
// (they are unique), so it means the upstream data disagreed with itself:
// 502 upstream_rejected, never items[0] or a silent pick.
func TestAccountsByUsernameTwoExactMatchesIs502(t *testing.T) {
	h := searchHandler(authenticated, searchOnlyPorts{hits: []domain.AccountSummary{
		{IGID: "1", Username: "target_user"},
		{IGID: "2", Username: "Target_User"},
	}})
	rec := getSearch(h, "?username=target_user")
	if rec.Code != http.StatusBadGateway {
		t.Fatalf("answered %d, want 502: %s", rec.Code, rec.Body.String())
	}
	env := decodeEnvelope(t, rec)
	if env.Error.Code != "upstream_rejected" {
		t.Errorf("code = %q, want upstream_rejected", env.Error.Code)
	}
}

// The empty list and the single-account cases both ride the SAME shape
// ?query= uses: {"items":[...],"next_cursor":null}, no "ok" field.
func TestAccountsByUsernameSucceedsInTheListShape(t *testing.T) {
	h := searchHandler(authenticated, searchOnlyPorts{hits: []domain.AccountSummary{
		{IGID: "1", Username: "target_user", FullName: "The Target", FBID: "10", IsVerified: true},
	}})
	rec := getSearch(h, "?username=target_user")
	if rec.Code != http.StatusOK {
		t.Fatalf("answered %d: %s", rec.Code, rec.Body.String())
	}
	if raw := rec.Body.String(); containsToken(raw, `"ok"`) {
		t.Errorf("body carries an ok field, which the new conventions drop: %s", raw)
	}
	body := decodeSearchBody(t, rec)
	if body.NextCursor != nil {
		t.Errorf("next_cursor = %v, want null", body.NextCursor)
	}
	if len(body.Items) != 1 || body.Items[0].FBID != "10" || !body.Items[0].IsVerified {
		t.Errorf("items = %+v, want the port's one match forwarded", body.Items)
	}
}

// The ways a `username` request is refused before any use case runs: bad
// characters, over length, repeated, and combined with `query`. A leading
// `@` is accepted (ParseUsername strips it) and covered separately below.
func TestAccountsByUsernameValidation(t *testing.T) {
	h := searchHandler(authenticated, searchOnlyPorts{err: errors.New("no use case should run")})

	for name, rawQuery := range map[string]string{
		"bad characters": "?username=not-a-username!",
		"31 characters":  "?username=" + repeat("a", 31),
		"repeated":       "?username=a&username=b",
		"query+username": "?query=someone&username=someone",
	} {
		t.Run(name, func(t *testing.T) {
			rec := getSearch(h, rawQuery)
			if rec.Code != http.StatusBadRequest {
				t.Fatalf("%s: answered %d, want 400: %s", name, rec.Code, rec.Body.String())
			}
			env := decodeEnvelope(t, rec)
			if env.Error.Code != "invalid_id" {
				t.Errorf("%s: code = %q, want invalid_id", name, env.Error.Code)
			}
		})
	}

	// At the boundary: exactly 30 characters is accepted (falls through to
	// the use case, which this test's port refuses loudly).
	rec := getSearch(h, "?username="+repeat("a", 30))
	if rec.Code == http.StatusBadRequest {
		t.Errorf("a username of exactly 30 characters was refused as too long")
	}
}

// A malformed username's envelope message is the FIXED string, never the
// raw input — domain.ParseUsername's own error text embeds it, and this
// route must not forward that text (file header's never-list).
func TestAccountsByUsernameInvalidMessageIsFixedNeverTheInput(t *testing.T) {
	h := searchHandler(authenticated, searchOnlyPorts{err: errors.New("no use case should run")})
	const secret = "not-a-valid-handle!!!"
	rec := getSearch(h, "?username="+secret)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("answered %d, want 400: %s", rec.Code, rec.Body.String())
	}
	if containsToken(rec.Body.String(), secret) {
		t.Errorf("the envelope carries the raw username: %s", rec.Body.String())
	}
	env := decodeEnvelope(t, rec)
	if env.Error.Message != invalidUsernameMessage {
		t.Errorf("message = %q, want the fixed message %q", env.Error.Message, invalidUsernameMessage)
	}
}

// A leading `@` is stripped, the same as domain.ParseUsername does for every
// other caller of it.
func TestAccountsByUsernameStripsALeadingAt(t *testing.T) {
	h := searchHandler(authenticated, searchOnlyPorts{hits: []domain.AccountSummary{
		{IGID: "1", Username: "target_user"},
	}})
	rec := getSearch(h, "?username=%40target_user")
	if rec.Code != http.StatusOK {
		t.Fatalf("answered %d: %s", rec.Code, rec.Body.String())
	}
	body := decodeSearchBody(t, rec)
	if len(body.Items) != 1 || body.Items[0].Username != "target_user" {
		t.Fatalf("items = %+v, want the @-stripped match", body.Items)
	}
}

// The session gate answers 409 the same way ?query='s does.
func TestAccountsByUsernameSessionGate(t *testing.T) {
	h := searchHandler(domain.Session{State: domain.StateCheckpointed, Detail: "challenge required"},
		searchOnlyPorts{err: errors.New("no use case should run")})
	rec := getSearch(h, "?username=target_user")
	if rec.Code != http.StatusConflict {
		t.Fatalf("answered %d, want 409: %s", rec.Code, rec.Body.String())
	}
	env := decodeEnvelope(t, rec)
	if env.Error.Code != "needs_human" {
		t.Errorf("code = %q, want needs_human", env.Error.Code)
	}
}

// Validation runs BEFORE the session gate for `username` too: a checkpointed
// session must not change a malformed username's answer.
func TestAccountsByUsernameValidationRunsBeforeSessionGate(t *testing.T) {
	h := searchHandler(domain.Session{State: domain.StateCheckpointed, Detail: "captcha"},
		searchOnlyPorts{err: errors.New("no use case should run")})
	rec := getSearch(h, "?username=not-valid!")
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("a checkpointed session with a malformed username answered %d, want 400", rec.Code)
	}
	env := decodeEnvelope(t, rec)
	if env.Error.Code != "invalid_id" {
		t.Errorf("code = %q, want invalid_id", env.Error.Code)
	}
}

// Every domain kind maps to its OWN status under the one table every route
// shares (errors.go), including 502 for ErrUpstreamRejected and 501 for
// ErrUnsupported, which the legacy table P14 deleted did not have.
func TestAccountsSearchStatusPerDomainKind(t *testing.T) {
	cause := errors.New("what the gateway said")
	for _, tc := range []struct {
		err      error
		wantCode int
		wantKind string
	}{
		{domain.Classify(domain.ErrInvalidID, cause), http.StatusBadRequest, "invalid_id"},
		{domain.Classify(domain.ErrNotFound, cause), http.StatusNotFound, "not_found"},
		{domain.Classify(domain.ErrNeedsHuman, cause), http.StatusConflict, "needs_human"},
		{domain.Classify(domain.ErrTooLarge, cause), http.StatusRequestEntityTooLarge, "too_large"},
		{domain.Classify(domain.ErrUnsupported, cause), http.StatusNotImplemented, "unsupported"},
		{domain.Classify(domain.ErrUpstreamRejected, cause), http.StatusBadGateway, "upstream_rejected"},
		{domain.Classify(domain.ErrUnavailable, cause), http.StatusServiceUnavailable, "unavailable"},
		// Fail closed: an unclassified error is 500, never a status that
		// implies a name for what went wrong.
		{cause, http.StatusInternalServerError, "internal"},
	} {
		h := searchHandler(authenticated, searchOnlyPorts{err: tc.err})
		rec := getSearch(h, "?query=someone")
		if rec.Code != tc.wantCode {
			t.Errorf("kind %v: status = %d, want %d", domain.KindOf(tc.err), rec.Code, tc.wantCode)
		}
		env := decodeEnvelope(t, rec)
		if env.Error.Code != tc.wantKind {
			t.Errorf("kind %v: code = %q, want %q", domain.KindOf(tc.err), env.Error.Code, tc.wantKind)
		}
		if env.Error.Message == "" {
			t.Errorf("kind %v: message was empty", domain.KindOf(tc.err))
		}
	}
}

// The session gate answers 409 with the needs_human code and the session's
// own state/detail in the message, same information the old routes give in
// their own shape.
func TestAccountsSearchSessionGate(t *testing.T) {
	h := searchHandler(domain.Session{State: domain.StateCheckpointed, Detail: "challenge required"},
		searchOnlyPorts{err: errors.New("no use case should run")})
	rec := getSearch(h, "?query=someone")
	if rec.Code != http.StatusConflict {
		t.Fatalf("answered %d, want 409: %s", rec.Code, rec.Body.String())
	}
	env := decodeEnvelope(t, rec)
	if env.Error.Code != "needs_human" {
		t.Errorf("code = %q, want needs_human", env.Error.Code)
	}
	if env.Error.Message != "checkpointed: challenge required" {
		t.Errorf("message = %q, want the state and detail", env.Error.Message)
	}
}

// Request-shape validation runs BEFORE the session gate: design 16 says "a
// malformed id is 400 before any use case runs", and a blank query is that.
// A checkpointed session must not change the answer — proof that the 400
// check is not reachable only when the session happens to be healthy.
func TestAccountsSearchValidationRunsBeforeSessionGate(t *testing.T) {
	h := searchHandler(domain.Session{State: domain.StateCheckpointed, Detail: "captcha"},
		searchOnlyPorts{err: errors.New("no use case should run")})

	rec := getSearch(h, "?query=%20%20%20")

	if rec.Code != http.StatusBadRequest {
		t.Fatalf("a checkpointed session with a blank query answered %d, want 400 "+
			"(validation must run before the session gate)", rec.Code)
	}
	env := decodeEnvelope(t, rec)
	if env.Error.Code != "invalid_id" {
		t.Errorf("code = %q, want invalid_id", env.Error.Code)
	}
}
