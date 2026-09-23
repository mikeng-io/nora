package instameow

// searchAccounts, tested against the TYPED struct path: SearchUsers is a
// typed library call (like GetProfile), not the gateway's raw graphql()
// helper, so these tests build slidetypes.SearchResponse/SearchResult values
// directly rather than literal wire bytes — the same split search.go's file
// header explains.

import (
	"errors"
	"testing"

	"instagram-bridge/internal/domain"

	"go.mau.fi/mautrix-meta/pkg/instameow/slidetypes"
	"go.mau.fi/mautrix-meta/pkg/messagix/httpclient"
	"go.mau.fi/mautrix-meta/pkg/messagix/types"
)

// The measured shape maps field for field: `pk` becomes the igid, the
// messaging fbid is formatted from the int64 the library decoded, and
// `is_verified` carries straight through. No `is_private` anywhere — it does
// not exist on this wire (see the file header and domain.AccountSummary).
func TestSearchAccountsMapsTheMeasuredWire(t *testing.T) {
	f := &fakeIG{}

	got, err := searchAccounts(t.Context(), f, "someone")
	if err != nil {
		t.Fatal(err)
	}
	if len(f.SearchQueries) != 1 || f.SearchQueries[0] != "someone" {
		t.Fatalf("SearchUsers was addressed with %v, want [someone]", f.SearchQueries)
	}
	if len(got) != 2 {
		t.Fatalf("got %d results, want 2", len(got))
	}
	first := got[0]
	if first.IGID != "900000001" || first.Username != "search_result_alpha" || first.FullName != "Search Result Alpha" {
		t.Errorf("first result = %+v, want the measured pk/username/full_name", first)
	}
	if first.FBID != "900000000000001" {
		t.Errorf("first result fbid = %q, want the formatted messaging id", first.FBID)
	}
	if first.IsVerified {
		t.Error("first result is_verified was true; the measured fixture has it false")
	}
	second := got[1]
	if second.FBID != "" {
		t.Errorf("second result fbid = %q, want empty — the fixture has no messaging id for it", second.FBID)
	}
	if !second.IsVerified {
		t.Error("second result is_verified was false; the measured fixture has it true")
	}
}

// The order is the library's. Reordering or filtering here would make the
// P9 probe measure this function instead of Instagram.
func TestSearchAccountsPreservesLibraryOrder(t *testing.T) {
	f := &fakeIG{}
	got, err := searchAccounts(t.Context(), f, "someone")
	if err != nil {
		t.Fatal(err)
	}
	for i, want := range []string{"900000001", "900000002"} {
		if got[i].IGID != want {
			t.Errorf("result %d igid = %q, want %q (library order)", i, got[i].IGID, want)
		}
	}
}

// A nil entry in the result slice is skipped, not carried as a zero-value
// hit that would read as a real, nameless account.
func TestSearchAccountsSkipsANilEntry(t *testing.T) {
	f := &fakeIG{SearchResultsSet: true, SearchResults: []*slidetypes.SearchResult{
		nil, measuredSearchResults[0],
	}}
	got, err := searchAccounts(t.Context(), f, "someone")
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 || got[0].IGID != "900000001" {
		t.Fatalf("got %+v, want only the non-nil measured hit", got)
	}
}

// A result set the library measured empty is a normal success: nobody
// matched, not a failure.
func TestSearchAccountsEmptyResultIsNotAnError(t *testing.T) {
	f := &fakeIG{SearchResultsSet: true, SearchResults: nil}
	got, err := searchAccounts(t.Context(), f, "nobody-matches-this")
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 0 {
		t.Fatalf("got %d results, want 0", len(got))
	}
}

// A GraphQL failure inside an HTTP 200 — the shape that hid the profile-query
// bug for a day — must fail here too. instameow's own makeGraphQLRequest
// decodes the `errors` array and turns it into the error SearchUsers returns;
// this proves the gateway does not swallow it.
func TestSearchAccountsFailsOnAnUpstreamError(t *testing.T) {
	f := &fakeIG{SearchErr: errors.New("synthetic graphql error: field_exception")}
	_, err := searchAccounts(t.Context(), f, "someone")
	if err == nil {
		t.Fatal("want an error, got nil")
	}
}

// A challenged session is ErrNeedsHuman, exactly as every other route on this
// gateway classifies it — search is not a special case.
func TestSearchAccountsClassifiesAChallenge(t *testing.T) {
	f := &fakeIG{SearchErr: httpclient.ErrCheckpointRequired}
	_, err := searchAccounts(t.Context(), f, "someone")
	if !errors.Is(err, domain.ErrNeedsHuman) {
		t.Errorf("err = %v, want ErrNeedsHuman", err)
	}
}

// A GraphQL errors-in-200 shape from a TYPED call — the SAME AsError() shape
// instameow's own makeGraphQLRequest returns — classifies as
// ErrUpstreamRejected via call.go's isGraphQLFailure, so the new-convention
// route answers 502 instead of falling through unclassified to 500.
func TestSearchAccountsClassifiesGraphQLErrorsAsUpstreamRejected(t *testing.T) {
	gqlErr := (&types.ErrorResponse{Errors: []*types.GraphQLError{
		{Message: "synthetic field exception", Summary: "Query error", Code: 1675030},
	}}).AsError()
	f := &fakeIG{SearchErr: gqlErr}
	_, err := searchAccounts(t.Context(), f, "someone")
	if !errors.Is(err, domain.ErrUpstreamRejected) {
		t.Errorf("err = %v, want ErrUpstreamRejected", err)
	}
}

// The error TEXT that reaches the caller (and, before it, the span) is
// FIXED, never the upstream message — which can echo bad input back, and the
// upstream message here deliberately contains a value that must not survive.
func TestSearchAccountsErrorTextIsSanitized(t *testing.T) {
	const secret = "a-value-that-must-not-survive-3ee81"
	f := &fakeIG{SearchErr: errors.New("upstream said: " + secret)}
	_, err := searchAccounts(t.Context(), f, "someone")
	if err == nil {
		t.Fatal("want an error, got nil")
	}
	if got := err.Error(); got == "" || containsSubstring(got, secret) {
		t.Errorf("err.Error() = %q, want a fixed description without the upstream text", got)
	}
}

func containsSubstring(s, sub string) bool {
	for i := 0; i+len(sub) <= len(s); i++ {
		if s[i:i+len(sub)] == sub {
			return true
		}
	}
	return false
}
