package main

// GET /accounts?query=, wired end to end: the real HTTP surface, the real use
// case, and the real gateway over the measured-wire fake — the same shape
// social_test.go uses for the account and story routes.

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"go.mau.fi/mautrix-meta/pkg/instameow/slidetypes"
	"go.mau.fi/mautrix-meta/pkg/messagix/httpclient"
	"go.mau.fi/mautrix-meta/pkg/messagix/types"
)

func getSearch(t *testing.T, b *testBridge, rawQuery string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(http.MethodGet, "/accounts"+rawQuery, nil)
	rec := httptest.NewRecorder()
	b.routes().ServeHTTP(rec, req)
	return rec
}

// The measured shape reaches the wire response with the library's own order
// kept, and none of GetProfile's fields — this is a different call.
func TestAccountsSearchReturnsTheMeasuredHitsInOrder(t *testing.T) {
	b := newTestBridge(t, &fakeIG{})

	rec := getSearch(t, b, "?query=someone")

	if rec.Code != http.StatusOK {
		t.Fatalf("/accounts?query= answered %d: %s", rec.Code, rec.Body.String())
	}
	var body searchAccountsBody
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatalf("body did not decode: %v (%s)", err, rec.Body.String())
	}
	if body.NextCursor != nil {
		t.Errorf("next_cursor = %v, want null", body.NextCursor)
	}
	if len(body.Items) != len(measuredSearchResults) {
		t.Fatalf("got %d items, want %d (the measured fixture)", len(body.Items), len(measuredSearchResults))
	}
	for i, want := range measuredSearchResults {
		if body.Items[i].IGID != want.PK || body.Items[i].Username != want.Username {
			t.Errorf("item %d = %+v, want pk=%s username=%s (library order preserved)",
				i, body.Items[i], want.PK, want.Username)
		}
	}
}

// A checkpointed session refuses through the SAME 409 the other routes use,
// with the new envelope's shape rather than the old {"ok":false} one.
func TestAccountsSearchRefusesOnACheckpoint(t *testing.T) {
	b := newTestBridge(t, &fakeIG{}, withState("checkpointed", "captcha"))

	rec := getSearch(t, b, "?query=someone")

	if rec.Code != http.StatusConflict {
		t.Fatalf("answered %d, want 409: %s", rec.Code, rec.Body.String())
	}
	var env errorEnvelope
	if err := json.Unmarshal(rec.Body.Bytes(), &env); err != nil {
		t.Fatalf("body is not an error envelope: %v (%s)", err, rec.Body.String())
	}
	if env.Error.Code != "needs_human" {
		t.Errorf("code = %q, want needs_human", env.Error.Code)
	}
}

// An upstream failure — a challenge surfaced through the search call itself
// rather than the session snapshot — classifies exactly like every other
// route's upstream call.
func TestAccountsSearchClassifiesAnUpstreamChallenge(t *testing.T) {
	b := newTestBridge(t, &fakeIG{SearchErr: httpclient.ErrCheckpointRequired})

	rec := getSearch(t, b, "?query=someone")

	if rec.Code != http.StatusConflict {
		t.Fatalf("answered %d, want 409: %s", rec.Code, rec.Body.String())
	}
}

// A GraphQL errors-in-200 failure from SearchUsers — the AsError() shape
// instameow's own makeGraphQLRequest returns — answers 502 with the new
// envelope, end to end: gateway classification (call.go's
// isGraphQLFailure), the app's pass-through, and the transport's status
// table. Before call.go's fix this fell through unclassified to 500.
func TestAccountsSearchUpstreamGraphQLErrorAnswers502(t *testing.T) {
	gqlErr := (&types.ErrorResponse{Errors: []*types.GraphQLError{
		{Message: "synthetic field exception", Summary: "Query error", Code: 1675030},
	}}).AsError()
	b := newTestBridge(t, &fakeIG{SearchErr: gqlErr})

	rec := getSearch(t, b, "?query=someone")

	if rec.Code != http.StatusBadGateway {
		t.Fatalf("answered %d, want 502: %s", rec.Code, rec.Body.String())
	}
	var env errorEnvelope
	if err := json.Unmarshal(rec.Body.Bytes(), &env); err != nil {
		t.Fatalf("body is not an error envelope: %v (%s)", err, rec.Body.String())
	}
	if env.Error.Code != "upstream_rejected" {
		t.Errorf("code = %q, want upstream_rejected", env.Error.Code)
	}
}

// --- GET /accounts?username= (17 P10), wired end to end --------------------

// The exact match, over the real gateway, real use case and real HTTP
// surface: the fixture's fuzzy hit is dropped and the exact one kept, in
// the same list shape as ?query=.
func TestAccountsByUsernameReturnsTheExactMatch(t *testing.T) {
	b := newTestBridge(t, &fakeIG{SearchResultsSet: true, SearchResults: []*slidetypes.SearchResult{
		{PK: "900000010", Username: "decoy_result", FullName: "Not The One"},
		{PK: "900000011", Username: "example_target", FullName: "The Target"},
	}})

	rec := getSearch(t, b, "?username=example_target")

	if rec.Code != http.StatusOK {
		t.Fatalf("/accounts?username= answered %d: %s", rec.Code, rec.Body.String())
	}
	var body searchAccountsBody
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatalf("body did not decode: %v (%s)", err, rec.Body.String())
	}
	if body.NextCursor != nil {
		t.Errorf("next_cursor = %v, want null", body.NextCursor)
	}
	if len(body.Items) != 1 || body.Items[0].Username != "example_target" || body.Items[0].IGID != "900000011" {
		t.Fatalf("items = %+v, want exactly the exact match", body.Items)
	}
}

// A checkpointed session refuses through the same 409 ?query= uses.
func TestAccountsByUsernameRefusesOnACheckpoint(t *testing.T) {
	b := newTestBridge(t, &fakeIG{}, withState("checkpointed", "captcha"))

	rec := getSearch(t, b, "?username=example_target")

	if rec.Code != http.StatusConflict {
		t.Fatalf("answered %d, want 409: %s", rec.Code, rec.Body.String())
	}
	var env errorEnvelope
	if err := json.Unmarshal(rec.Body.Bytes(), &env); err != nil {
		t.Fatalf("body is not an error envelope: %v (%s)", err, rec.Body.String())
	}
	if env.Error.Code != "needs_human" {
		t.Errorf("code = %q, want needs_human", env.Error.Code)
	}
}

// An upstream challenge surfaced through the search call itself classifies
// exactly like every other route's upstream call.
func TestAccountsByUsernameClassifiesAnUpstreamChallenge(t *testing.T) {
	b := newTestBridge(t, &fakeIG{SearchErr: httpclient.ErrCheckpointRequired})

	rec := getSearch(t, b, "?username=example_target")

	if rec.Code != http.StatusConflict {
		t.Fatalf("answered %d, want 409: %s", rec.Code, rec.Body.String())
	}
}

// A GraphQL errors-in-200 failure from SearchUsers answers 502 with the new
// envelope, end to end — the same shape TestAccountsSearchUpstreamGraphQLErrorAnswers502
// proves for ?query=.
func TestAccountsByUsernameUpstreamGraphQLErrorAnswers502(t *testing.T) {
	gqlErr := (&types.ErrorResponse{Errors: []*types.GraphQLError{
		{Message: "synthetic field exception", Summary: "Query error", Code: 1675030},
	}}).AsError()
	b := newTestBridge(t, &fakeIG{SearchErr: gqlErr})

	rec := getSearch(t, b, "?username=example_target")

	if rec.Code != http.StatusBadGateway {
		t.Fatalf("answered %d, want 502: %s", rec.Code, rec.Body.String())
	}
	var env errorEnvelope
	if err := json.Unmarshal(rec.Body.Bytes(), &env); err != nil {
		t.Fatalf("body is not an error envelope: %v (%s)", err, rec.Body.String())
	}
	if env.Error.Code != "upstream_rejected" {
		t.Errorf("code = %q, want upstream_rejected", env.Error.Code)
	}
}
