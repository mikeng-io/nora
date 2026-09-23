package main

// GET /accounts?query=, wired end to end: the real HTTP surface, the real use
// case, and the real gateway over the measured-wire fake — the same shape
// social_test.go uses for the account and story routes.

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"go.mau.fi/mautrix-meta/pkg/messagix/httpclient"
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
