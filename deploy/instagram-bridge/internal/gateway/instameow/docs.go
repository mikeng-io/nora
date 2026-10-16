package instameow

// The GraphQL documents the library does NOT carry: the web app's story read
// and its profile timeline (design/cognition-loop/22-social-stream.md §1a).
//
// `graphql` (call.go) sends a document by its friendly name, and the library
// looks the doc_id up in its own table (`graphql.GraphQLDocs`) and always
// posts to `/api/graphql`. Neither holds for these two:
//
//   - their doc_ids are not in that table, and are not constants anywhere in
//     this repo either: a doc_id ROTATES when Instagram ships a new web build,
//     so each is configuration (INSTAGRAM_STORY_DOC_ID,
//     INSTAGRAM_TIMELINE_DOC_ID), captured from the logged-in web client the
//     way §1a describes;
//   - the timeline document goes to `/graphql/query`, not `/api/graphql`.
//     Captured that way from the web client (§1a); the library has no
//     endpoint for it.
//
// So graphqlDoc builds the SAME request `MakeGraphQLRequest` builds
// (httpclient/graphql.go: `NewHTTPQuery()`, `fb_api_caller_class=RelayModern`,
// `fb_api_req_friendly_name`, `variables`, `server_timestamps`, `doc_id`; the
// header set `BuildHeaders(true, false)` plus `x-fb-friendly-name`, the
// sec-fetch-* triple and `origin`), with the endpoint and doc_id as data, and
// reads the answer through graphql's own checks (graphqlAnswer). Two
// differences from the library's request, both deliberate:
//
//   - ONE attempt (`MakeRequestOnce`), not the library's retry loop, which
//     re-sends a 5xx up to five more times. The story REST read this replaces
//     answered 500 on every call (§1, 2026-10-16); retried, each poll would
//     spend six calls of the login's budget (§4) on one answer;
//   - the `referer` is the page the web client sends the document from (a
//     profile for the timeline), not the library's `/direct/inbox/`.
//
// # A read sends the read, and nothing that marks it seen
//
// The web viewer marks a story seen with a SEPARATE mutation
// (`PolarisStoriesV3SeenMutation`, §1). graphqlDoc refuses any document whose
// name says it is a mutation or a seen-mark, before anything is sent, so no
// later edit to a friendly name can turn a read into a view receipt.

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"strings"

	"instagram-bridge/internal/domain"

	"github.com/google/go-querystring/query"
	"go.mau.fi/mautrix-meta/pkg/messagix/httpclient"
	"go.mau.fi/mautrix-meta/pkg/messagix/types"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/codes"
	"go.opentelemetry.io/otel/trace"
)

// The variables that configure the two doc_ids. Named here, beside the
// documents, because the 501 a missing one answers names its variable.
const (
	EnvStoryDocID    = "INSTAGRAM_STORY_DOC_ID"
	EnvTimelineDocID = "INSTAGRAM_TIMELINE_DOC_ID"
)

// QueryDocs is the configured doc_id of each document. An empty one makes
// that read answer 501 `unsupported`, naming its variable.
type QueryDocs struct {
	Story    string
	Timeline string
}

// Problem is why this configuration must not be sent at all, or nil. Two
// documents with ONE doc_id means at least one variable holds the wrong
// document, and Instagram executes the doc_id, not the friendly name this
// bridge labels it with: a pasted seen-mutation id would be sent under the
// read's name. So both reads answer 501 until an operator fixes it.
func (q QueryDocs) Problem() error {
	if q.Story != "" && q.Story == q.Timeline {
		return domain.Classify(domain.ErrUnsupported, fmt.Errorf(
			"%s and %s hold the same doc_id; one of them names the wrong document, so neither is sent",
			EnvStoryDocID, EnvTimelineDocID))
	}
	return nil
}

// The two endpoints, as the web client posts to them (§1a).
const (
	graphqlAPIEndpoint   = restOrigin + "/api/graphql"
	graphqlQueryEndpoint = restOrigin + "/graphql/query"
)

// DocIssuer sends one configured GraphQL document. The live one is
// sessionDocs over the session's own HTTP client; a test hands in a fake that
// records every request. Plain types only, so fakeig can implement it without
// importing this package.
type DocIssuer interface {
	MakeDocRequest(
		ctx context.Context, endpoint, referer, friendlyName, docID string, variables []byte,
	) (*http.Response, []byte, error)
}

// webDoc is one document as this bridge sends it.
type webDoc struct {
	friendlyName string
	endpoint     string
	docID        string
	// env is the variable docID comes from, for the 501 and the stale error.
	env string
	// label names the read in error text, as graphql's label does.
	label string
	// root is the field under `data` the document answers with. A 200 with
	// no error and without it is ErrStaleQuery: the doc_id names some OTHER
	// document (docAnswer).
	root string
	// partialOK reads the root even beside GraphQL errors, logging them: a
	// story whose reel arrived is read, whatever else failed beside it.
	partialOK bool
}

// errNotARead refuses a document whose name says it writes or marks seen.
// Unclassified: it is a bug in this bridge, answered 500, never retried.
var errNotARead = errors.New("refused: a read may not send a mutation or a seen-mark")

// unconfigured is the 501 for a read with no doc_id.
func (d webDoc) unconfigured() error {
	return domain.Classify(domain.ErrUnsupported,
		fmt.Errorf("%s has no doc_id configured: set %s", d.label, d.env))
}

// graphqlDoc sends one configured document and returns its body only when
// the document succeeded. The status is Instagram's, as graphql's is.
func graphqlDoc(
	ctx context.Context, issuer DocIssuer, d webDoc, referer string, vars any,
) (int, []byte, error) {
	if d.docID == "" {
		return 0, nil, d.unconfigured()
	}
	name := strings.ToLower(d.friendlyName)
	if strings.Contains(name, "mutation") || strings.Contains(name, "seen") {
		return 0, nil, errNotARead
	}
	payload, err := json.Marshal(vars)
	if err != nil {
		return 0, nil, fmt.Errorf("%s variables did not encode: %w", d.label, err)
	}
	ctx, span := startUpstream(ctx, "instagram.graphql "+d.friendlyName,
		attribute.String("instagram.graphql.document", d.friendlyName))
	defer span.End()
	resp, raw, err := issuer.MakeDocRequest(ctx, d.endpoint, referer, d.friendlyName, d.docID, payload)
	return graphqlAnswer(span, d.label, resp, raw, err, &d)
}

// staleMarkers are what an error naming the DOCUMENT, rather than the data,
// says. UNMEASURED: no rotated doc_id has been sent yet (§1a: "what a rotated
// id answers is not measured"). The words are the ones GraphQL servers use
// for an unknown persisted query, plus the document's own name and id, so an
// error that names this document is caught whatever its wording.
var staleMarkers = []string{
	"persisted query", "persistedquery", "persisted_query",
	"doc_id", "docid", "document", "query not found",
}

// stale answers whether any error names this document, and which.
func (d webDoc) stale(errs []graphQLError) (graphQLError, bool) {
	for _, e := range errs {
		text := strings.ToLower(e.Message + " " + e.Summary + " " + e.Description)
		if strings.Contains(text, strings.ToLower(d.friendlyName)) || strings.Contains(text, d.docID) {
			return e, true
		}
		for _, m := range staleMarkers {
			if strings.Contains(text, m) {
				return e, true
			}
		}
	}
	return graphQLError{}, false
}

// staleIn reads a non-200 body for the same thing.
func (d webDoc) staleIn(raw []byte) (graphQLError, bool) {
	return d.stale(answerErrors(raw, nil))
}

// answerErrors is every error a body carries: the GraphQL `errors` array,
// plus the singular `error` object Meta's endpoints sometimes answer with
// instead (`message`, `code`, `error_subcode`).
func answerErrors(raw []byte, errs []graphQLError) []graphQLError {
	var envelope struct {
		Errors []graphQLError `json:"errors"`
		Error  *graphQLError  `json:"error"`
	}
	if json.Unmarshal(raw, &envelope) != nil {
		return errs
	}
	if errs == nil {
		errs = envelope.Errors
	}
	if envelope.Error != nil {
		errs = append(errs, *envelope.Error)
	}
	return errs
}

// docAnswer reads a configured document's 200, after graphqlAnswer's
// transport, checkpoint and status checks, which keep their precedence:
//
//   - an error naming the document is ErrStaleQuery;
//   - errors beside the data root: read the data when the document allows it
//     (partialOK, the story), logging the errors; otherwise the first error
//     is ErrUpstreamRejected, as graphql answers;
//   - no error and NO data root is ErrStaleQuery too. The answer is some
//     other document's: Instagram executes the doc_id, so a wrong id in the
//     variable answers with a different root (a seen-mutation's id answers
//     with a mutation's). Halting that read is right; a 502 would back the
//     login off and resend the same wrong id on the next poll.
func docAnswer(span trace.Span, label string, d webDoc, raw []byte, errs []graphQLError) (int, []byte, error) {
	all := answerErrors(raw, errs)
	if e, ok := d.stale(all); ok {
		return http.StatusOK, nil, staleQuery(span, label, d, e)
	}
	var body struct {
		Data map[string]json.RawMessage `json:"data"`
	}
	// A `data` that is not an object has no root either.
	_ = json.Unmarshal(raw, &body)
	root := body.Data[d.root]
	present := len(root) > 0 && string(root) != "null"
	if len(all) > 0 {
		e := all[0]
		if present && d.partialOK {
			slog.Warn("graphql document answered with its data and errors; the data is read",
				"document", d.friendlyName, "errors", len(all), "code", e.Code, "summary", e.Summary)
			return http.StatusOK, raw, nil
		}
		span.SetAttributes(
			attribute.Int("instagram.graphql.error_code", e.Code),
			attribute.String("error.type", "upstream_rejected"),
		)
		span.SetStatus(codes.Error, "upstream_rejected")
		return http.StatusOK, nil, domain.Classify(domain.ErrUpstreamRejected,
			fmt.Errorf("%s returned a GraphQL error: %s (code=%d): %s", label, e.Summary, e.Code, e.Message))
	}
	if !present {
		span.SetAttributes(attribute.String("error.type", "stale_query"))
		span.SetStatus(codes.Error, "stale_query")
		return http.StatusOK, nil, domain.Classify(domain.ErrStaleQuery, fmt.Errorf(
			"%s: the answer has no %s, so %s names another document; capture it again",
			label, d.root, d.env))
	}
	return http.StatusOK, raw, nil
}

// staleQuery is ErrStaleQuery, with the span marked. NOT a session problem:
// the caller never runs sessionRejected on it, and the poller halts only this
// read (§4).
func staleQuery(span trace.Span, label string, d webDoc, e graphQLError) error {
	span.SetAttributes(
		attribute.Int("instagram.graphql.error_code", e.Code),
		attribute.String("error.type", "stale_query"),
	)
	span.SetStatus(codes.Error, "stale_query")
	return domain.Classify(domain.ErrStaleQuery, fmt.Errorf(
		"%s: Instagram does not know the configured document; capture %s again (%s, code=%d)",
		label, d.env, e.Summary, e.Code))
}

// docIssuerFor resolves the live DocIssuer off a client, as issuerFor does
// for the library's own documents.
func docIssuerFor(cli Client) (DocIssuer, error) {
	source, ok := cli.(rawSource)
	if !ok {
		return nil, errors.New("this client cannot issue raw requests")
	}
	h := source.GetHTTP()
	if h == nil {
		return nil, fmt.Errorf("%w: no http client", errNotConnected)
	}
	return sessionDocs{h: h}, nil
}

// sessionDocs is the live DocIssuer: `MakeGraphQLRequest`'s request, built
// here with the endpoint and doc_id as data (this file's header).
type sessionDocs struct{ h *httpclient.HTTPClient }

func (s sessionDocs) MakeDocRequest(
	ctx context.Context, endpoint, referer, friendlyName, docID string, variables []byte,
) (*http.Response, []byte, error) {
	payload := s.h.NewHTTPQuery()
	payload.FbAPICallerClass = "RelayModern"
	payload.FbAPIReqFriendlyName = friendlyName
	payload.Variables = string(variables)
	payload.ServerTimestamps = "true"
	payload.DocID = docID
	// The library sets this per document from its table, and leaves it empty
	// for the profile document graphql sends.
	payload.Jssesw = ""
	form, err := query.Values(payload)
	if err != nil {
		return nil, nil, err
	}

	headers := s.h.BuildHeaders(true, false)
	headers.Set("x-fb-friendly-name", friendlyName)
	headers.Set("sec-fetch-dest", "empty")
	headers.Set("sec-fetch-mode", "cors")
	headers.Set("sec-fetch-site", "same-origin")
	headers.Set("origin", restOrigin)
	headers.Set("referer", referer)

	resp, raw, err := s.h.MakeRequestOnce(ctx, endpoint, http.MethodPost, headers, []byte(form.Encode()), types.FORM)
	// What MakeGraphQLRequest does after a successful request: the session's
	// jar takes the answer's Set-Cookie (a rotated csrftoken, the www claim),
	// so the next request carries what Instagram last handed out.
	if err == nil && resp != nil {
		s.h.GetCookies().UpdateFromResponse(resp)
	}
	return resp, bytes.TrimPrefix(raw, httpclient.AntiJSPrefix), err
}
