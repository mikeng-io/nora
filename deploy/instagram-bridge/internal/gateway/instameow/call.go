// Package instameow is the gateway: the ONLY code in the bridge that talks to
// Instagram (design/instagram-adoption/16 §Layers, 17 P7).
//
// It owns the instameow client and its session supervisor, Instagram's wire
// structs, and the mapping from those structs to domain entities. Nothing
// above it sees a slidetypes value, and nothing above it sees an error that
// is not either a domain kind or deliberately left unclassified (which the
// HTTP layer answers as 500, fail-closed).
//
// # One way to call Instagram
//
// Every upstream call passes through one of two helpers in this file:
//
//   - call: every TYPED instameow method (SendMessage, GetThread, GetProfile,
//     FetchReel, the upload, LoadIndex, ...). instameow issues the HTTP
//     itself and returns decoded structs, so the bytes are not ours to check;
//     what the helper owns is the upstream span and the error mapping.
//   - graphql: a RAW GraphQL document, for the fields the typed methods throw
//     away. It checks the transport status AND decodes the GraphQL `errors`
//     array before `data`, because a failed document answers HTTP 200. The
//     absence of that check hid a real bug for a day on 2026-09-22.
//
// The design names a third, `rest`, for raw REST endpoints. Written with its
// first caller: mentions.go's `GET /mentions` probe. Before it, the one raw
// REST call this bridge ever made (`web_profile_info`) had been removed when
// it proved throttled.
package instameow

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"strings"

	"instagram-bridge/internal/domain"
	"instagram-bridge/internal/observability"

	"go.mau.fi/mautrix-meta/pkg/messagix/httpclient"
	"go.mau.fi/mautrix-meta/pkg/messagix/types"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/codes"
	"go.opentelemetry.io/otel/trace"
)

// call runs one typed instameow method under its CLIENT span and maps its
// failure onto the closed error set.
//
// The span is ended with the RAW error, so its `error.type` is exactly what
// P1 recorded; the classification is applied to what the caller receives.
func call[T any](
	ctx context.Context, span string, attrs []attribute.KeyValue,
	fn func(context.Context) (T, error),
) (T, error) {
	ctx, s := startUpstream(ctx, span, attrs...)
	out, err := fn(ctx)
	endUpstream(s, err)
	return out, classify(err)
}

// graphQLIssuer is the one method the raw path needs off
// `*httpclient.HTTPClient`, which has no interface of its own to fake.
// `*httpclient.HTTPClient` satisfies it structurally.
type GraphQLIssuer interface {
	MakeGraphQLRequest(ctx context.Context, name string, variables any) (*http.Response, []byte, error)
}

// graphql issues one raw GraphQL document and returns its body only when the
// document succeeded.
//
// `label` names the document in error text ("profile query"), which reaches
// an operator through `social_error`, so its wording is kept stable.
//
// The status it returns is Instagram's HTTP status: the real one for a
// non-200, 200 for a failure inside the body, and 0 when there was no
// response at all.
func graphql(
	ctx context.Context, issuer GraphQLIssuer, doc, label string, vars any,
) (int, []byte, error) {
	ctx, span := startUpstream(ctx, "instagram.graphql "+doc,
		attribute.String("instagram.graphql.document", doc))
	defer span.End()

	resp, raw, err := issuer.MakeGraphQLRequest(ctx, doc, vars)
	if err != nil {
		failSpan(span, errorType(err), err)
		return 0, nil, classify(err)
	}
	if resp != nil {
		span.SetAttributes(attribute.Int("http.response.status_code", resp.StatusCode))
		if resp.StatusCode != http.StatusOK {
			// FORWARDED, not flattened: telling a 429 from a challenge from a
			// parse failure is why this is a named error rather than one bucket.
			span.SetAttributes(attribute.String("error.type", "upstream_http"))
			span.SetStatus(codes.Error, "upstream_http")
			return resp.StatusCode, nil, domain.Classify(domain.ErrUpstreamRejected,
				fmt.Errorf("%s returned a non-200: %d", label, resp.StatusCode))
		}
	}

	// `errors` BEFORE `data`. A failed document is still an HTTP 200, with an
	// `errors` array where `data` would be.
	var envelope struct {
		Errors []struct {
			Message string `json:"message"`
			Code    int    `json:"code"`
			Summary string `json:"summary"`
		} `json:"errors"`
	}
	if err := json.Unmarshal(raw, &envelope); err != nil {
		// A 200 that is not JSON at all (an HTML challenge page, a truncated
		// body) is a failure too, and its span says so.
		span.SetAttributes(attribute.String("error.type", "upstream_rejected"))
		span.SetStatus(codes.Error, "upstream_rejected")
		return http.StatusOK, nil, domain.Classify(domain.ErrUpstreamRejected,
			fmt.Errorf("%s did not return JSON (%d bytes): %w", label, len(raw), err))
	}
	if len(envelope.Errors) > 0 {
		e := envelope.Errors[0]
		span.SetAttributes(
			attribute.Int("instagram.graphql.error_code", e.Code),
			attribute.String("error.type", "upstream_rejected"),
		)
		span.SetStatus(codes.Error, "upstream_rejected")
		return http.StatusOK, nil, domain.Classify(domain.ErrUpstreamRejected,
			fmt.Errorf("%s returned a GraphQL error: %s (code=%d): %s", label, e.Summary, e.Code, e.Message))
	}
	return http.StatusOK, raw, nil
}

// RESTIssuer is the one pair of methods the raw REST path needs off
// `*httpclient.HTTPClient`, mirroring GraphQLIssuer above: `*httpclient.HTTPClient`
// satisfies it structurally, so a test can fake it with no real Instagram
// session. mentions.go is its first caller (see the package doc comment's
// note on `rest`).
type RESTIssuer interface {
	BuildHeaders(withCookies, isSecFetchDocument bool) http.Header
	MakeRequest(ctx context.Context, url, method string, headers http.Header, payload []byte, contentType types.ContentType) (*http.Response, []byte, error)
	// WebSessionID is the `X-Web-Session-ID` the library's own REST callers
	// send (FetchMedia, FetchReel). It lives on `*instameow.Client`'s
	// unexported configs, and its one exported path is `DumpState()`; see
	// sessionREST in mentions.go.
	WebSessionID() (string, error)
}

// restOrigin is the referer every raw REST call sets, the same base URL
// FetchMedia and FetchReel build theirs from (pkg/instameow/misc.go:
// `c.GetEndpoint("base_url")`). Not read off the library's own endpoint
// table: `rest`'s callers pass a full URL of their own (mentions.go's
// endpoint has no entry in that table to begin with), so this is a plain
// constant rather than a lookup with one unmatched key.
const restOrigin = "https://www.instagram.com"

// errWebSessionID is left unclassified on purpose: the HTTP layer answers it
// 500 (fail-closed). It is neither retryable nor the caller's fault.
var errWebSessionID = errors.New("web session id unavailable; request not sent")

// rest issues one raw REST GET and returns its body only when the request
// succeeded. It is the third helper `16` names and none existed until now
// (see this file's package doc comment): mentions.go is its first caller.
//
// Headers are built the SAME way the library's own REST callers build
// theirs — FetchMedia and FetchReel, pkg/instameow/misc.go — because a
// request that looks different from what the web client already sends risks
// a checkpoint on this account: `BuildHeaders(true, false)` (cookies,
// x-csrftoken, x-mid, x-ig-app-id, viewport-width, x-asbd-id, the sec-ch-*
// set, ...) plus `x-requested-with: XMLHttpRequest`, a same-origin referer
// and the sec-fetch-* triple, exactly what those two callers add on top of
// BuildHeaders, and `X-Web-Session-ID`, which FetchMedia reads off
// `*instameow.Client`'s unexported configs. That value's one exported path
// is `(*instameow.Client).DumpState()` (issuer.WebSessionID). When it cannot
// be read, NO request is sent: a request missing a header the real web
// client always sends is the mismatch this helper exists to avoid, so the
// call fails closed instead of going out different.
//
// `label` names the endpoint in error text ("news_inbox") and the span
// (`instagram.rest news_inbox`), the same convention `graphql`'s `label`
// uses.
//
// `"status":"fail"` inside an HTTP 200 is a failure too — the REST form of
// the check `graphql` above already applies to a GraphQL `errors` array (16
// §Tracing: "Status ERROR on any failure, including a 200 carrying...
// errors").
func rest(ctx context.Context, issuer RESTIssuer, endpoint, label string) (int, []byte, error) {
	ctx, span := startUpstream(ctx, "instagram.rest "+label)
	defer span.End()

	sessionID, err := issuer.WebSessionID()
	if err != nil || sessionID == "" {
		failSpan(span, "web_session_id", errWebSessionID)
		return 0, nil, errWebSessionID
	}
	headers := issuer.BuildHeaders(true, false)
	headers.Set("X-Web-Session-ID", sessionID)
	headers.Set("x-requested-with", "XMLHttpRequest")
	headers.Set("referer", restOrigin+"/")
	headers.Set("sec-fetch-dest", "empty")
	headers.Set("sec-fetch-mode", "cors")
	headers.Set("sec-fetch-site", "same-origin")

	resp, raw, err := issuer.MakeRequest(ctx, endpoint, http.MethodGet, headers, nil, types.NONE)
	if err != nil {
		failSpan(span, errorType(err), err)
		return 0, nil, classify(err)
	}
	status := 0
	if resp != nil {
		status = resp.StatusCode
		span.SetAttributes(attribute.Int("http.response.status_code", status))
	}
	if status != http.StatusOK {
		span.SetAttributes(attribute.String("error.type", "upstream_http"))
		span.SetStatus(codes.Error, "upstream_http")
		return status, nil, domain.Classify(domain.ErrUpstreamRejected,
			fmt.Errorf("%s returned a non-200: %d", label, status))
	}

	// The REST form of the same "errors before data" rule graphql() applies:
	// a REST failure can still be an HTTP 200, with `"status":"fail"` where a
	// real answer would be (posts.go's rule for FetchMedia).
	var envelope struct {
		Status string `json:"status"`
	}
	if err := json.Unmarshal(raw, &envelope); err != nil {
		// A 200 that is not JSON at all (an HTML challenge page, a truncated
		// body) is a failure too, and its span says so.
		span.SetAttributes(attribute.String("error.type", "upstream_rejected"))
		span.SetStatus(codes.Error, "upstream_rejected")
		return status, nil, domain.Classify(domain.ErrUpstreamRejected,
			fmt.Errorf("%s did not return JSON (%d bytes): %w", label, len(raw), err))
	}
	if envelope.Status == "fail" {
		span.SetAttributes(attribute.String("error.type", "upstream_rejected"))
		span.SetStatus(codes.Error, "upstream_rejected")
		return status, nil, domain.Classify(domain.ErrUpstreamRejected,
			fmt.Errorf("%s returned HTTP 200 with a failure status", label))
	}
	return status, raw, nil
}

// classify maps what Instagram and the library say onto the closed set.
//
// What it cannot name it leaves UNCLASSIFIED, and the HTTP layer answers that
// as 500: an unknown failure is not claimed to be retryable (503) or the
// caller's fault (400). What it does name, the HTTP layer maps through its one
// status table (transport/http/errors.go, statusForDomainError, the only one
// since P14): ErrNeedsHuman is 409, ErrTooLarge 413, and ErrUpstreamRejected,
// which the isGraphQLFailure case below produces, 502.
func classify(err error) error {
	switch {
	case err == nil:
		return nil
	case domain.KindOf(err) != nil:
		return err
	case needsHuman(err):
		return domain.Classify(domain.ErrNeedsHuman, err)
	case isTooLarge(err):
		return domain.Classify(domain.ErrTooLarge, err)
	case isGraphQLFailure(err):
		return domain.Classify(domain.ErrUpstreamRejected, err)
	default:
		return err
	}
}

// isGraphQLFailure recognizes the AsError() shape instameow's own
// makeGraphQLRequest returns for a TYPED call (graphql.Response[T], built on
// types.ErrorResponse): either a single error with a top-level `error` code,
// or one or more *types.GraphQLError from the `errors` array (possibly
// joined via errors.Join — errors.As traverses that). Without this, a
// GraphQL errors-in-200 failure from a typed call (SearchUsers, GetProfile,
// ...) was UNCLASSIFIED and fell through to 500 on every route; the raw
// graphql() helper already treats the same shape as ErrUpstreamRejected for
// the RAW path (§One way to call Instagram above), so this closes the same
// gap for the typed one.
func isGraphQLFailure(err error) bool {
	var gqlErr *types.GraphQLError
	if errors.As(err, &gqlErr) {
		return true
	}
	var resp *types.ErrorResponse
	return errors.As(err, &resp)
}

// needsHuman is Instagram asking for a person: a challenge, a checkpoint, a
// consent screen, or a dead token.
func needsHuman(err error) bool {
	return errors.Is(err, httpclient.ErrChallengeRequired) ||
		errors.Is(err, httpclient.ErrCheckpointRequired) ||
		errors.Is(err, httpclient.ErrConsentRequired) ||
		errors.Is(err, httpclient.ErrTokenInvalidated)
}

// isTooLarge matches Instagram's own refusal text, because the library
// surfaces it as an opaque error rather than a typed one. Matching on a string
// is unpleasant and is confined to exactly this, HERE, where the wire is
// interpreted: the alternative is reporting a 20,001-character message as a
// server fault the adapter would retry forever.
func isTooLarge(err error) bool {
	msg := strings.ToLower(err.Error())
	return strings.Contains(msg, "message too long") ||
		strings.Contains(msg, "too large") ||
		strings.Contains(msg, "entity too large")
}

// startUpstream opens the CLIENT span for one call to Instagram.
func startUpstream(ctx context.Context, name string, attrs ...attribute.KeyValue) (context.Context, trace.Span) {
	return observability.Tracer().Start(ctx, name,
		trace.WithSpanKind(trace.SpanKindClient),
		trace.WithAttributes(attrs...),
	)
}

// endUpstream closes it, marking it failed with a classified `error.type`.
func endUpstream(span trace.Span, err error) {
	if err != nil {
		failSpan(span, errorType(err), err)
	}
	span.End()
}

func failSpan(span trace.Span, kind string, err error) {
	span.SetAttributes(attribute.String("error.type", kind))
	span.RecordError(err)
	span.SetStatus(codes.Error, kind)
}

// errorType names a failure by what it MEANS, in the vocabulary a reader of
// Jaeger acts on differently. It reads the RAW error, as P1 did.
func errorType(err error) string {
	switch {
	case needsHuman(err):
		return "needs_human"
	case errors.Is(err, domain.ErrNotFound):
		// Reachable only from a wrapped function that classifies its own
		// failure before returning it (posts.go does, so that a REST answer
		// inside an HTTP 200 marks the span). No earlier call does.
		return "not_found"
	case errors.Is(err, domain.ErrUpstreamRejected):
		// The same vocabulary the raw graphql() helper already writes.
		return "upstream_rejected"
	case errors.Is(err, domain.ErrUnavailable):
		// Only reachable if a wrapped function returns an already-classified
		// "not connected"; no library call does today. Kept so that one would
		// not read as a bare "error". (P1 carried the same case.)
		return "unavailable"
	case errors.Is(err, context.DeadlineExceeded):
		return "timeout"
	case errors.Is(err, context.Canceled):
		return "canceled"
	default:
		return "error"
	}
}
