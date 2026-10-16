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
//     FetchMedia, the upload, LoadIndex, ...). instameow issues the HTTP
//     itself and returns decoded structs, so the bytes are not ours to check;
//     what the helper owns is the upstream span and the error mapping.
//   - graphql: a RAW GraphQL document, for the fields the typed methods throw
//     away. It checks the transport status AND decodes the GraphQL `errors`
//     array before `data`, because a failed document answers HTTP 200. The
//     absence of that check hid a real bug for a day on 2026-09-22. Its
//     sibling graphqlDoc (docs.go) sends a document the library does not
//     carry, under a doc_id from config, through the same checks.
//
// The design names a third, `rest`, for raw REST endpoints, written as
// `restDo` (a method, a referer, a form body) with its first caller,
// mentions.go's `GET /mentions` read. Before it, the one raw REST call this
// bridge ever made (`web_profile_info`) had been removed when it proved
// throttled. comments.go's comment read and the bridge's ONE public write,
// the comment reply, go through it too (design 19).
package instameow

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
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
	return graphqlAnswer(span, label, resp, raw, err, nil)
}

// graphqlAnswer reads one raw GraphQL answer: the transport status, a
// checkpoint in the body, and the envelope's `errors` array before `data`.
// It is graphql's checks, shared with graphqlDoc (docs.go), whose documents
// carry a CONFIGURED doc_id: for those, `doc` is set and an error naming the
// document is ErrStaleQuery rather than a plain upstream rejection. The span
// is the caller's, and the caller ends it.
func graphqlAnswer(
	span trace.Span, label string, resp *http.Response, raw []byte, err error, doc *webDoc,
) (int, []byte, error) {
	// The library answers every status >= 400 with an error (ErrUnexpectedError,
	// http.go's makeRequestOnce) AND the response and its body, the only place
	// a checkpoint shows up on a raw call (issue #533; restDo reads it the same
	// way). Such an answer goes to the status branch below, which reads the
	// body. A typed sentinel the library already found (a redirect to a
	// challenge or to login) keeps its own classification.
	answered := resp != nil && resp.StatusCode >= 400
	if err != nil && (!answered || needsHuman(err) || errors.Is(err, httpclient.ErrAccountSuspended)) {
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
			// A body naming a checkpoint needs a human, whatever the status
			// (issue #533): the same read restStatusError gives a REST answer.
			if sentinel := restCheckpointSentinel(raw); sentinel != nil {
				return resp.StatusCode, nil, domain.Classify(domain.ErrNeedsHuman,
					fmt.Errorf("%s: %w", label, sentinel))
			}
			if doc != nil {
				if e, ok := doc.staleIn(raw); ok {
					return resp.StatusCode, nil, staleQuery(span, label, *doc, e)
				}
			}
			return resp.StatusCode, nil, domain.Classify(domain.ErrUpstreamRejected,
				fmt.Errorf("%s returned a non-200: %d", label, resp.StatusCode))
		}
	}

	// `errors` BEFORE `data`. A failed document is still an HTTP 200, with an
	// `errors` array where `data` would be.
	var envelope struct {
		Status string         `json:"status"`
		Errors []graphQLError `json:"errors"`
	}
	if err := json.Unmarshal(raw, &envelope); err != nil {
		// A 200 that is not JSON at all (an HTML challenge page, a truncated
		// body) is a failure too, and its span says so.
		span.SetAttributes(attribute.String("error.type", "upstream_rejected"))
		span.SetStatus(codes.Error, "upstream_rejected")
		return http.StatusOK, nil, domain.Classify(domain.ErrUpstreamRejected,
			fmt.Errorf("%s did not return JSON (%d bytes): %w", label, len(raw), err))
	}
	if envelope.Status == "fail" || len(envelope.Errors) > 0 {
		// A checkpoint can also come back as a 200 carrying "status":"fail"
		// and a checkpoint message: it needs a human, not a retry (#533).
		if sentinel := restCheckpointSentinel(raw); sentinel != nil {
			span.SetAttributes(attribute.String("error.type", "needs_human"))
			span.SetStatus(codes.Error, "needs_human")
			return http.StatusOK, nil, domain.Classify(domain.ErrNeedsHuman,
				fmt.Errorf("%s: %w", label, sentinel))
		}
	}
	if envelope.Status == "fail" && len(envelope.Errors) == 0 {
		span.SetAttributes(attribute.String("error.type", "upstream_rejected"))
		span.SetStatus(codes.Error, "upstream_rejected")
		return http.StatusOK, nil, domain.Classify(domain.ErrUpstreamRejected,
			fmt.Errorf("%s returned status fail", label))
	}
	if doc != nil {
		// A configured document reads its errors AND its data root together
		// (docAnswer): which of the two is present decides the answer.
		return docAnswer(span, label, *doc, raw, envelope.Errors)
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

// graphQLError is one entry of a GraphQL envelope's `errors` array, as far
// as this bridge reads it.
type graphQLError struct {
	Message     string `json:"message"`
	Code        int    `json:"code"`
	Subcode     int    `json:"error_subcode"`
	Summary     string `json:"summary"`
	Description string `json:"description"`
}

// RESTIssuer is the one pair of methods the raw REST path needs off
// `*httpclient.HTTPClient`, mirroring GraphQLIssuer above: `*httpclient.HTTPClient`
// satisfies it structurally, so a test can fake it with no real Instagram
// session. mentions.go is its first caller (see the package doc comment).
type RESTIssuer interface {
	BuildHeaders(withCookies, isSecFetchDocument bool) http.Header
	MakeRequest(ctx context.Context, url, method string, headers http.Header, payload []byte, contentType types.ContentType) (*http.Response, []byte, error)
	// MakeRequestOnceNoRedirect is MakeRequest WITHOUT the library's retry
	// loop (httpclient/http.go:425-482: up to MaxHTTPRetries=5 more attempts
	// on a transport error or a 5xx, http.go:302) and without following a
	// redirect (http.go:490-496). Every non-GET and every request whose URL
	// carries ids goes through it:
	//
	//   - a retried POST is a second public comment when the first one
	//     landed and only its answer was lost;
	//   - the retrying form logs the request URL at error level on a failure
	//     (http.go:448-464), and a comment URL carries a media id and a
	//     comment id;
	//   - the redirect-following form runs checkHTTPRedirect
	//     (http.go:365-381), which logs the redirect's url and prev_url at
	//     warn level (the request URL, ids and all), and which does NOT treat
	//     a redirect to /accounts/login/ as a dead token. With no redirect
	//     followed, restDo reads the 3xx's Location itself
	//     (redirectSentinel).
	//
	// makeRequestOnce itself logs nothing (http.go:498-544); UpdateProxy logs
	// only when a proxy is configured, and this bridge configures none.
	MakeRequestOnceNoRedirect(ctx context.Context, url, method string, headers http.Header, payload []byte, contentType types.ContentType) (*http.Response, []byte, error)
	// WebSessionID is the `X-Web-Session-ID` the library's own REST callers
	// send (FetchMedia, FetchReel). It lives on `*instameow.Client`'s
	// unexported configs, and its one exported path is `DumpState()`; see
	// sessionREST in mentions.go.
	WebSessionID() (string, error)
	// WebFormTokens are the per-session values a web-client form POST
	// carries, read off the same dumped configs, each from exactly where the
	// library itself reads it:
	//
	//   - jazoest: `c.configs.Jazoest`, the form field the library's own web
	//     POST sends (pkg/instameow/misc.go:45, pushRegisterQuery
	//     `url:"jazoest"`, misc.go:28), parsed off the page's `__eqmc` ajax
	//     URL (pkg/messagix/httpclient/modules.go:27-38);
	//   - fbDTSG: `c.configs.BrowserConfigTable.DTSGInitData.Token`, the same
	//     POST's `fb_dtsg` (misc.go:47, `url:"fb_dtsg"`, misc.go:29; also the
	//     library's generic web query, httpclient/http.go:283);
	//   - rolloutHash: `BrowserConfigTable.InstagramWebPushInfo.RolloutHash`,
	//     which the library's own web-login POST sends as `x-instagram-ajax`
	//     (pkg/instameow/login_web.go:187-196): the page's numeric client
	//     revision.
	//
	// Plain strings rather than a struct, so fakeig can implement it without
	// importing this package.
	WebFormTokens() (jazoest, fbDTSG, rolloutHash string, err error)
}

// restOrigin is the referer every raw REST call sets, the same base URL
// FetchMedia and FetchReel build theirs from (pkg/instameow/misc.go:
// `c.GetEndpoint("base_url")`). Not read off the library's own endpoint
// table: `restDo`'s callers pass a full URL of their own (mentions.go's
// endpoint has no entry in that table to begin with), so this is a plain
// constant rather than a lookup with one unmatched key.
const restOrigin = "https://www.instagram.com"

// errWebSessionID is left unclassified on purpose: the HTTP layer answers it
// 500 (fail-closed). It is neither retryable nor the caller's fault.
var errWebSessionID = errors.New("web session id unavailable; request not sent")

// errWebFormTokens is errWebSessionID's twin for a web form POST: with no
// jazoest, fb_dtsg or rollout hash, the request would go out different from
// the web client's, so it does not go out. Unclassified for the same reason.
var errWebFormTokens = errors.New("web form tokens unavailable; request not sent")

// restCheckpointBody is the shape Instagram's own private/web APIs use to ask
// for a human inside a failure body — UNMEASURED for this endpoint (no live
// 4xx from news_inbox has been captured), so only the literal `message`
// values and the `checkpoint_url` presence check are trusted, never a guess
// from prose. `login_required` is the shape a dead token answers with
// (distinct from the redirect-based ErrTokenInvalidated checkHTTPRedirect
// already catches elsewhere in the library).
type restCheckpointBody struct {
	Message       string `json:"message"`
	CheckpointURL string `json:"checkpoint_url"`
}

// restCheckpointSentinel reads a REST failure body for that shape and
// returns the matching httpclient sentinel, or nil when the body does not
// look like one (including an empty or non-JSON body, e.g. the 429 path,
// which the library never reads a body for at all).
func restCheckpointSentinel(body []byte) error {
	if len(body) == 0 {
		return nil
	}
	var b restCheckpointBody
	if err := json.Unmarshal(body, &b); err != nil {
		return nil
	}
	switch {
	case b.Message == "checkpoint_required", b.CheckpointURL != "":
		return httpclient.ErrCheckpointRequired
	case b.Message == "challenge_required":
		return httpclient.ErrChallengeRequired
	case b.Message == "consent_required":
		return httpclient.ErrConsentRequired
	case b.Message == "login_required":
		return httpclient.ErrTokenInvalidated
	default:
		return nil
	}
}

// restStatusError classifies a non-2xx REST answer, mirroring
// fetchMediaFailure's own status table (posts.go:111-130): 404 is "no such
// thing", 429 is retryable, and any other status Instagram answered with is
// upstream-rejected — UNLESS the body itself names a checkpoint, a
// challenge, a consent screen or a dead token, which needs a human and takes
// priority over the bare status code.
func restStatusError(label string, status int, body []byte) error {
	if sentinel := restCheckpointSentinel(body); sentinel != nil {
		return domain.Classify(domain.ErrNeedsHuman, fmt.Errorf("%s: %w", label, sentinel))
	}
	switch status {
	case http.StatusNotFound:
		return domain.Classify(domain.ErrNotFound, fmt.Errorf("%s returned 404", label))
	case http.StatusTooManyRequests:
		return domain.Classify(domain.ErrUnavailable, fmt.Errorf("%s was rate limited", label))
	default:
		return domain.Classify(domain.ErrUpstreamRejected, fmt.Errorf("%s returned a non-200: %d", label, status))
	}
}

// restRequest is one raw REST call. The zero value of every optional field is
// a plain GET.
type restRequest struct {
	// method is http.MethodGet when empty.
	method   string
	endpoint string
	// label names the endpoint in error text and in the span
	// (`instagram.rest <label>`). It must never carry an id.
	label string
	// referer is restOrigin when empty.
	referer string
	// form, when non-nil, is sent form-encoded with the library's own
	// `types.FORM` content type (`application/x-www-form-urlencoded`), the
	// way the library's own web POSTs send theirs (misc.go:57-59, route.go:84).
	form url.Values
	// webForm marks a POST the web client sends as one of its own ajax form
	// posts: restDo adds `jazoest` and `fb_dtsg` to the form (misc.go:40-48,
	// then `form.Encode()`, misc.go:57-58), sets `X-Instagram-AJAX` to the
	// rollout hash (login_web.go:196) and, when BuildHeaders set no
	// `x-ig-www-claim` because the session has not been handed one yet, sends
	// "0", as the library's own web-login POST does (login_web.go:198-199).
	// With any token missing, NO request is sent (errWebFormTokens).
	webForm bool
	// idsInURL marks an endpoint whose URL carries identifiers (a media pk,
	// a comment id). Such a request goes out through
	// MakeRequestOnceNoRedirect (see its doc comment for what that avoids),
	// and a transport failure's text, which the library builds from Go's
	// *url.Error and so QUOTES the URL, is replaced with a fixed one before
	// it can reach a span, a log line or an envelope.
	idsInURL bool
}

// restAnswered marks a REST failure where Instagram ANSWERED at the endpoint
// with a refusal: a 4xx (429 included), or a 200 carrying "status":"fail".
// For a write, that is the one outcome where the thing is known NOT to have
// happened; every other failure (no response, a 5xx, a 200 that is not JSON)
// is ambiguous. The text is the wrapped error's, unchanged.
type restAnswered struct {
	err    error
	status int
	// body is the refusal's body, for a caller that reads it (postComment's
	// action-block check). It never enters Error().
	body []byte
}

func (e *restAnswered) Error() string { return e.err.Error() }
func (e *restAnswered) Unwrap() error { return e.err }

// fixedTextError carries a cause for errors.Is while showing only its own,
// fixed text.
type fixedTextError struct {
	msg   string
	cause error
}

func (e *fixedTextError) Error() string { return e.msg }
func (e *fixedTextError) Unwrap() error { return e.cause }

// restDo issues one raw REST request and returns its body only when the
// request succeeded. It is the third helper `16` names and none existed until
// mentions.go needed it (see this file's package doc comment).
//
// Headers are built the SAME header SET the library's own REST callers
// build theirs from — FetchMedia and FetchReel, pkg/instameow/misc.go —
// because a request that looks different from what the web client already
// sends risks a checkpoint on this account: `BuildHeaders(true, false)`
// (cookies, x-csrftoken, x-mid, x-ig-app-id, viewport-width, x-asbd-id, the
// sec-ch-* set, ...) plus `x-requested-with: XMLHttpRequest`, a referer
// (FetchReel's plain base_url by default; a caller reading or writing ONE post
// passes FetchMedia's `/p/{shortcode}/` form, misc.go:85-89), the sec-fetch-*
// triple, and `X-Web-Session-ID`, which FetchMedia reads off
// `*instameow.Client`'s unexported configs. That value's one exported path is
// `(*instameow.Client).DumpState()` (issuer.WebSessionID). When it cannot be
// read, NO request is sent: a request missing a header the real web client
// always sends is the mismatch this helper exists to avoid, so the call fails
// closed instead of going out different.
//
// A POST adds what the library's own web-client POST adds on top of that set:
// `origin` and `priority`, mirrored from its route-definition POST
// (pkg/instameow/route.go:74-84, `BuildHeaders(true, false)` + origin +
// referer + priority + sec-fetch-*, sent with `types.FORM`). Its other web
// POST, RegisterPushNotifications (misc.go:51-59), sets a subset of the same
// (x-requested-with, Referer, types.FORM). A POST never goes through the
// library's retry loop and never follows a redirect
// (RESTIssuer.MakeRequestOnceNoRedirect's doc comment).
//
// `"status":"fail"` inside an HTTP 200 is a failure too — the REST form of
// the check `graphql` above already applies to a GraphQL `errors` array (16
// §Tracing: "Status ERROR on any failure, including a 200 carrying...
// errors").
func restDo(ctx context.Context, issuer RESTIssuer, req restRequest) (int, []byte, error) {
	label := req.label
	ctx, span := startUpstream(ctx, "instagram.rest "+label)
	defer span.End()

	method := req.method
	if method == "" {
		method = http.MethodGet
	}
	referer := req.referer
	if referer == "" {
		referer = restOrigin
	}

	sessionID, err := issuer.WebSessionID()
	if err != nil || sessionID == "" {
		failSpan(span, "web_session_id", errWebSessionID)
		return 0, nil, errWebSessionID
	}
	headers := issuer.BuildHeaders(true, false)
	headers.Set("X-Web-Session-ID", sessionID)
	headers.Set("x-requested-with", "XMLHttpRequest")
	headers.Set("referer", referer)
	headers.Set("sec-fetch-dest", "empty")
	headers.Set("sec-fetch-mode", "cors")
	headers.Set("sec-fetch-site", "same-origin")

	var (
		payload     []byte
		contentType = types.NONE
	)
	if method != http.MethodGet {
		headers.Set("origin", restOrigin)
		headers.Set("priority", "u=1, i")
	}
	form := req.form
	if req.webForm {
		jazoest, fbDTSG, rolloutHash, tokErr := issuer.WebFormTokens()
		if tokErr != nil || jazoest == "" || fbDTSG == "" || rolloutHash == "" {
			failSpan(span, "web_form_tokens", errWebFormTokens)
			return 0, nil, errWebFormTokens
		}
		// A copy: the caller's form is never mutated.
		form = url.Values{}
		for k, v := range req.form {
			form[k] = append([]string(nil), v...)
		}
		form.Set("jazoest", jazoest)
		form.Set("fb_dtsg", fbDTSG)
		headers.Set("X-Instagram-AJAX", rolloutHash)
		if headers.Get("x-ig-www-claim") == "" {
			headers.Set("x-ig-www-claim", "0")
		}
	}
	if form != nil {
		payload = []byte(form.Encode())
		contentType = types.FORM
	}

	var (
		resp *http.Response
		raw  []byte
	)
	noRedirect := method != http.MethodGet || req.idsInURL
	if noRedirect {
		resp, raw, err = issuer.MakeRequestOnceNoRedirect(ctx, req.endpoint, method, headers, payload, contentType)
	} else {
		resp, raw, err = issuer.MakeRequest(ctx, req.endpoint, method, headers, payload, contentType)
	}
	status := 0
	if resp != nil {
		status = resp.StatusCode
		span.SetAttributes(attribute.Int("http.response.status_code", status))
	}
	if err != nil {
		if status >= 400 {
			// The library's own `MakeRequest` wraps every status >= 400 in
			// `ErrUnexpectedError` (http.go's `makeRequestOnce`) but still
			// hands back the response AND its body — the only place a
			// checkpoint/challenge/consent screen shows up on a RAW REST
			// call, since the library's own checkpoint detection
			// (`checkResponseError`, client.go) only fires for its OWN
			// typed and GraphQL calls, never for a request this bridge
			// issues itself. restStatusError reads that body.
			outErr := restStatusError(label, status, raw)
			failSpan(span, errorType(outErr), outErr)
			if status < 500 {
				outErr = &restAnswered{err: outErr, status: status, body: raw}
			}
			return status, nil, outErr
		}
		// A transport-level failure with no usable status: a network error,
		// or a challenge/checkpoint the library already turned into its own
		// typed sentinel during a redirect, with no response body at all.
		// classify() already recognizes those sentinels via needsHuman.
		if req.idsInURL {
			// The library's text for this is Go's *url.Error, which quotes
			// the URL, ids and all (http.go:527). A checkpoint keeps the
			// sentinel's own (fixed) name, which is what the operator reads
			// in /health's detail.
			msg := label + " request failed before an answer"
			if sentinel := humanSentinel(err); sentinel != nil {
				msg = label + ": " + sentinel.Error()
			}
			err = &fixedTextError{msg: msg, cause: err}
		}
		failSpan(span, errorType(err), err)
		return status, nil, classify(err)
	}
	if noRedirect && status >= 300 && status < 400 {
		// Not followed, so read here what checkHTTPRedirect would have read,
		// plus the one thing it misses: a redirect to /accounts/login/ is a
		// dead session. Anything else is refused as an unexpected redirect.
		outErr := redirectError(label, resp)
		failSpan(span, errorType(outErr), outErr)
		return status, nil, &restAnswered{err: outErr, status: status}
	}
	if status != http.StatusOK {
		// Fail-closed fallback. Nothing observed reaches this today — the
		// library only returns a nil error for a plain 200 — but a non-200
		// must never read as a success either way.
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
		if req.idsInURL {
			// A JSON syntax error quotes a byte of the body, and this body
			// can be people's words.
			return status, nil, domain.Classify(domain.ErrUpstreamRejected,
				&fixedTextError{msg: fmt.Sprintf("%s did not return JSON (%d bytes)", label, len(raw)), cause: err})
		}
		return status, nil, domain.Classify(domain.ErrUpstreamRejected,
			fmt.Errorf("%s did not return JSON (%d bytes): %w", label, len(raw), err))
	}
	if envelope.Status == "fail" {
		// A checkpoint can also arrive as a 200 carrying `"status":"fail"`
		// and a checkpoint message: read it the same way a 4xx body is read,
		// so the session supervisor sees it (review of #529).
		if sentinel := restCheckpointSentinel(raw); sentinel != nil {
			span.SetAttributes(attribute.String("error.type", "needs_human"))
			span.SetStatus(codes.Error, "needs_human")
			return status, nil, &restAnswered{status: status, body: raw,
				err: domain.Classify(domain.ErrNeedsHuman, fmt.Errorf("%s: %w", label, sentinel))}
		}
		span.SetAttributes(attribute.String("error.type", "upstream_rejected"))
		span.SetStatus(codes.Error, "upstream_rejected")
		return status, nil, &restAnswered{status: status, body: raw, err: domain.Classify(domain.ErrUpstreamRejected,
			fmt.Errorf("%s returned HTTP 200 with a failure status", label))}
	}
	return status, raw, nil
}

// redirectError classifies a 3xx this bridge chose not to follow, from its
// Location path and its Set-Cookie, the same checks checkHTTPRedirect
// (http.go:383-403) makes, plus /accounts/login/, which it does not. The text
// is fixed: the Location is never quoted (it can carry a `next=` with ids).
func redirectError(label string, resp *http.Response) error {
	if sentinel := redirectSentinel(resp); sentinel != nil {
		return domain.Classify(domain.ErrNeedsHuman, fmt.Errorf("%s: %w", label, sentinel))
	}
	return domain.Classify(domain.ErrUpstreamRejected, fmt.Errorf("%s answered with an unexpected redirect", label))
}

func redirectSentinel(resp *http.Response) error {
	for _, c := range resp.Cookies() {
		if (c.Name == "sessionid" || c.Name == "xs") && c.MaxAge < 0 {
			return httpclient.ErrTokenInvalidated
		}
	}
	loc, err := resp.Location()
	if err != nil {
		return nil
	}
	path := "/" + strings.Trim(loc.Path, "/") + "/"
	switch {
	case strings.Contains(path, "/challenge/"), strings.Contains(path, "/auth_platform/"):
		return httpclient.ErrChallengeRequired
	case strings.Contains(path, "/checkpoint/"):
		return httpclient.ErrCheckpointRequired
	case path == "/accounts/suspended/":
		return httpclient.ErrAccountSuspended
	case path == "/consent/", strings.HasPrefix(path, "/privacy/consent/"):
		return httpclient.ErrConsentRequired
	case strings.HasPrefix(path, "/accounts/login/"), path == "/login.php/":
		return httpclient.ErrTokenInvalidated
	}
	return nil
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

// humanSentinel is whichever needs-a-human sentinel err carries, or nil.
func humanSentinel(err error) error {
	for _, sentinel := range []error{
		httpclient.ErrChallengeRequired, httpclient.ErrCheckpointRequired,
		httpclient.ErrConsentRequired, httpclient.ErrTokenInvalidated,
	} {
		if errors.Is(err, sentinel) {
			return sentinel
		}
	}
	return nil
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
	case errors.Is(err, domain.ErrStaleQuery):
		// graphqlDoc's: a configured document Instagram no longer knows.
		return "stale_query"
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
