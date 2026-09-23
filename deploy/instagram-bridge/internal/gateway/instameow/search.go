package instameow

// Account search: the raw hit list behind `GET /accounts?query=`
// (design/instagram-adoption/16 §Resources, 17 P9, P11).
//
// # Why this exists now, ahead of P11
//
// P9 is a measurement, not a feature: does `SearchUsers` find an arbitrary
// public Instagram account by its EXACT username, or only accounts already in
// Nora's DM contact list? The operator chose to run that probe through this
// real route rather than a second client against the live session, because a
// second client could disturb the one session that exists. So this file is
// P11's route, built early, to be P9's instrument.
//
// # No re-ranking, no filtering, no invented paging
//
// The probe needs the LIBRARY's raw answer. Reordering or dropping hits here
// would make the probe measure this function instead of Instagram. The
// library returns its whole result set in one answer with no cursor, so
// `next_cursor` stays null rather than invent a paging Instagram never
// offered (transport/http owns that shape; this file returns a plain slice).
//
// # A typed library call, like GetProfile — not the gateway's raw graphql()
//
// `SearchUsers` decodes through instameow's own `makeGraphQLRequest`
// (go.mau.fi/mautrix-meta/pkg/instameow/graphql.go), which already reads the
// GraphQL envelope's `errors` array before `Data` and turns a non-empty one
// into a Go error (`graphql.Response[T].AsError()`, built on
// `types.ErrorResponse`). So a GraphQL failure inside an HTTP 200 already
// fails here, exactly as `16` requires — the SAME guarantee `readProfile`
// relies on for `GetProfile`, and for the same reason: this goes through the
// `call` helper, not the raw `graphql` helper, because the bytes are not ours
// to check.
//
// # is_private is not on this wire
//
// `slidetypes.SearchResult` (user.go) declares
// `interop_messaging_user_fbid`, `username`, `full_name`, `pk`,
// `profile_pic_url` and `is_verified` — no `is_private`. That field is on
// `PolarisProfilePageContentQuery`'s `data.user` (social_profile.go), a
// different document `GET /accounts?query=` never calls. See
// `domain.AccountSummary`'s doc comment for what that means for the shape
// this route returns.

import (
	"context"
	"strconv"

	"instagram-bridge/internal/domain"

	"go.mau.fi/mautrix-meta/pkg/instameow/slidetypes"
	"go.opentelemetry.io/otel/attribute"
)

// searchAccounts issues one search and returns every hit the library gave
// back, in its own order.
//
// The QUERY TEXT is never an attribute on the upstream span — it is a name
// typed about a person, the same never-list `16` §Tracing puts message text
// on. Only its length crosses into the span, which is bounded and carries
// nothing about who was searched for.
func searchAccounts(ctx context.Context, cli Client, query string) ([]domain.AccountSummary, error) {
	resp, err := call(ctx, "instagram.SearchUsers",
		[]attribute.KeyValue{attribute.Int("instagram.search.query_length", len(query))},
		func(ctx context.Context) (*slidetypes.SearchResponse, error) {
			return cli.SearchUsers(ctx, query)
		})
	if err != nil {
		return nil, err
	}
	if resp == nil {
		// A 200 with no envelope at all, per the same fail-closed reasoning
		// `readProfile` uses for `errNoProfile`: not an error the library
		// raised, but nothing to report either. An empty result set, not a
		// refusal — a search finding nobody is a normal, common answer.
		return []domain.AccountSummary{}, nil
	}
	results := resp.Data.Results
	out := make([]domain.AccountSummary, 0, len(results))
	for _, r := range results {
		if r == nil {
			// Not a hit this route can name. Skipped, not refused: unlike
			// roster.go's membership read, an under-counted search result set
			// gates no disclosure, and the caller can rerun the query.
			continue
		}
		fbid := ""
		if r.InteropMessagingUserFBID != 0 {
			fbid = strconv.FormatInt(r.InteropMessagingUserFBID, 10)
		}
		out = append(out, domain.AccountSummary{
			IGID:       r.PK,
			Username:   r.Username,
			FullName:   r.FullName,
			FBID:       fbid,
			IsVerified: r.IsVerified,
		})
	}
	return out, nil
}
