package instameow

// The raw GraphQL path, tested against LITERAL wire bytes rather than a live
// account: `*httpclient.HTTPClient` has no test seam, so the graphql helper
// takes an issuer and these tests hand it the bytes Instagram sent.
//
// Every failure shape seen live is here, because each one was a real mistake,
// not a hypothetical:
//
//  1. A `field_exception` envelope is a 200 with an `errors` array instead of
//     `data`. The first version of this read checked only the transport
//     status and returned an all-zero profile for this exact shape — a silent
//     "success". Measured 4/4 on a real account before the fix (2026-09-22):
//     a username sent through `PolarisProfilePageContentQuery`'s numeric `id`
//     field fails this way every time, and was being recorded as
//     `social_available: true`.
//  2. A non-200 (the 429 `web_profile_info` answered this session with) is
//     forwarded with its status, so an operator can tell a throttle from a
//     challenge from a parse failure.
//  3. A 200 whose body is not JSON (a challenge page) is a failure, not an
//     empty profile.
//  4. A transport error that is a checkpoint is ErrNeedsHuman.
//  5. A real success envelope populates every named field, keyed off the
//     shape Instagram returned for this account on 2026-09-22.

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"strings"
	"testing"

	"instagram-bridge/internal/domain"
	"instagram-bridge/internal/gateway/instameow/fakeig"
	"instagram-bridge/internal/session"

	"go.mau.fi/mautrix-meta/pkg/instameow/slidetypes"
	"go.mau.fi/mautrix-meta/pkg/messagix/httpclient"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/codes"
	"go.opentelemetry.io/otel/propagation"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/sdk/trace/tracetest"
)

// fieldExceptionEnvelope is the exact shape measured live: a 200 whose body
// is an `errors` array, no `data` key at all.
const fieldExceptionEnvelope = `{"errors":[{"message":"A server error field_exception occured. Check server logs for details.","severity":"CRITICAL","mids":["f0136f328befa77fe031ff7c53d932b4"],"debug_link":"https://www.meta.com/debug/?mid=f0136f328befa77fe031ff7c53d932b4","code":1675030,"api_error_code":-1,"summary":"Query Error","description":"Error performing query.","description_html":null,"description_raw":"Error performing query.","is_silent":false,"is_transient":false,"is_not_critical":false,"requires_reauth":false,"allow_user_retry":false,"debug_info":null,"query_path":null,"fbtrace_id":"F1CUAXL+Uuc","www_request_id":"A2b1faVSaoWmzADV29iJri-","path":["user"]}],"extensions":{"dtsg_token":"NAfxAATXtBq4-vYscgjOE4S6d2E0"}}`

// realProfileEnvelope is a trimmed version of a real `data.user` response
// (measured 2026-09-22, account igid 246967358): the named fields plus one
// unmodelled key (`account_type`) to prove extras are ignored rather than
// erroring the parse.
const realProfileEnvelope = `{"data":{"user":{
	"account_type": 1,
	"is_private": true,
	"biography": "From complexity to clarity",
	"external_url": "",
	"follower_count": 116,
	"following_count": 285,
	"mutual_followers_count": 5,
	"media_count": 268,
	"total_clips_count": 1,
	"is_business": false,
	"is_professional_account": null,
	"category": null,
	"profile_pic_url": "https://scontent.cdninstagram.com/pic.jpg",
	"friendship_status": {"following": true, "followed_by": true, "blocking": false}
}}}`

// challengePage is what a 200 carrying a login/challenge page instead of a
// document looks like: HTML where JSON belongs.
const challengePage = `<!DOCTYPE html><html><head><title>Instagram</title></head><body>challenge</body></html>`

// fakeGraphQLIssuer records what it was asked for and answers with canned
// bytes — the seam that stands in for `*httpclient.HTTPClient`.
type fakeGraphQLIssuer struct {
	sawIGID []string
	status  int
	body    []byte
	err     error
}

func (f *fakeGraphQLIssuer) MakeGraphQLRequest(
	_ context.Context, _ string, variables any,
) (*http.Response, []byte, error) {
	if req, ok := variables.(*slidetypes.GetProfilePageRequest); ok {
		f.sawIGID = append(f.sawIGID, req.ID)
	}
	if f.err != nil {
		return nil, nil, f.err
	}
	status := f.status
	if status == 0 {
		status = http.StatusOK
	}
	if status >= 400 {
		// The live library's shape (httpclient/http.go makeRequestOnce): a
		// status >= 400 comes back as an error AND the response and body.
		return &http.Response{StatusCode: status}, f.body,
			fmt.Errorf("%w %d", httpclient.ErrUnexpectedError, status)
	}
	return &http.Response{StatusCode: status}, f.body, nil
}

func mustIGID(t *testing.T, s string) domain.IGID {
	t.Helper()
	id, err := domain.ParseIGID(s)
	if err != nil {
		t.Fatal(err)
	}
	return id
}

// recordSpans installs a recording provider for one test and restores the
// globals afterwards.
func recordSpans(t *testing.T) *tracetest.SpanRecorder {
	t.Helper()
	rec := tracetest.NewSpanRecorder()
	prevTP, prevProp := otel.GetTracerProvider(), otel.GetTextMapPropagator()
	otel.SetTracerProvider(sdktrace.NewTracerProvider(sdktrace.WithSpanProcessor(rec)))
	otel.SetTextMapPropagator(propagation.TraceContext{})
	t.Cleanup(func() {
		otel.SetTracerProvider(prevTP)
		otel.SetTextMapPropagator(prevProp)
	})
	return rec
}

func spanNamed(t *testing.T, rec *tracetest.SpanRecorder, name string) sdktrace.ReadOnlySpan {
	t.Helper()
	var names []string
	for _, s := range rec.Ended() {
		if s.Name() == name {
			return s
		}
		names = append(names, s.Name())
	}
	t.Fatalf("no span named %q; got %v", name, names)
	return nil
}

func attrOf(s sdktrace.ReadOnlySpan, key string) (attribute.Value, bool) {
	for _, kv := range s.Attributes() {
		if string(kv.Key) == key {
			return kv.Value, true
		}
	}
	return attribute.Value{}, false
}

func TestAFieldExceptionIsRefusedRatherThanReadAsEmpty(t *testing.T) {
	issuer := &fakeGraphQLIssuer{body: []byte(fieldExceptionEnvelope)}
	out, status, err := readSocialProfile(t.Context(), issuer, mustIGID(t, "246967358"))
	if err == nil {
		t.Fatalf("a field_exception envelope read with no error; got %+v", out)
	}
	if !strings.Contains(err.Error(), "Query Error") {
		t.Errorf("error %q lost the GraphQL summary that tells an operator what failed", err)
	}
	if !errors.Is(err, domain.ErrUpstreamRejected) {
		t.Errorf("error %v is not ErrUpstreamRejected", err)
	}
	if status != http.StatusOK {
		t.Errorf("status = %d; the failure was inside a 200", status)
	}
	if out != (domain.SocialProfile{}) {
		t.Errorf("a failed read returned a non-zero profile: %+v", out)
	}
}

func TestANon200IsForwardedWithItsStatus(t *testing.T) {
	issuer := &fakeGraphQLIssuer{status: http.StatusTooManyRequests, body: []byte(`{"message":"Please wait a few minutes before you try again.","status":"fail"}`)}
	_, status, err := readSocialProfile(t.Context(), issuer, mustIGID(t, "246967358"))
	if status != http.StatusTooManyRequests {
		t.Errorf("status = %d, want the upstream 429", status)
	}
	if !errors.Is(err, domain.ErrUpstreamRejected) || !strings.Contains(err.Error(), "429") {
		t.Errorf("err = %v, want an upstream rejection naming 429", err)
	}
}

func TestAChallengePageInsideA200IsAFailure(t *testing.T) {
	rec := recordSpans(t)
	issuer := &fakeGraphQLIssuer{body: []byte(challengePage)}
	out, _, err := readSocialProfile(t.Context(), issuer, mustIGID(t, "246967358"))
	if err == nil || !errors.Is(err, domain.ErrUpstreamRejected) {
		t.Fatalf("an HTML body read as %+v, %v", out, err)
	}
	if !strings.Contains(err.Error(), "did not return JSON") {
		t.Errorf("err = %v", err)
	}
	// And its span says so: a 200 that is not a document is a failure.
	s := spanNamed(t, rec, "instagram.graphql PolarisProfilePageContentQuery")
	if s.Status().Code != codes.Error {
		t.Errorf("span status = %v for an HTML 200, want Error", s.Status().Code)
	}
}

func TestACheckpointOnTheRawPathNeedsAHuman(t *testing.T) {
	issuer := &fakeGraphQLIssuer{err: httpclient.ErrCheckpointRequired}
	_, status, err := readSocialProfile(t.Context(), issuer, mustIGID(t, "246967358"))
	if !errors.Is(err, domain.ErrNeedsHuman) {
		t.Errorf("err = %v, want ErrNeedsHuman", err)
	}
	if status != 0 {
		t.Errorf("status = %d, want 0: no response came back", status)
	}
}

func TestARealProfileEnvelopeNamesEveryMeasuredField(t *testing.T) {
	issuer := &fakeGraphQLIssuer{body: []byte(realProfileEnvelope)}
	out, _, err := readSocialProfile(t.Context(), issuer, mustIGID(t, "246967358"))
	if err != nil {
		t.Fatalf("a real profile envelope failed to parse: %v", err)
	}
	want := domain.SocialProfile{
		IsPrivate:            true,
		FollowedBy:           true,
		Biography:            "From complexity to clarity",
		FollowerCount:        116,
		FollowingCount:       285,
		MutualFollowersCount: 5,
		MediaCount:           268,
		TotalClipsCount:      1,
		ProfilePicURL:        "https://scontent.cdninstagram.com/pic.jpg",
	}
	if out != want {
		t.Errorf("parsed profile = %+v, want %+v", out, want)
	}
}

func TestParseSocialProfileRefusesAUserlessBodyWithNoErrors(t *testing.T) {
	// Neither `errors` nor a `user` — a shape that should not exist but, per
	// this bundle's own history of wrong field-set guesses, gets a named
	// refusal rather than a silent zero-value success if it ever does.
	out, err := parseSocialProfile([]byte(`{"data":{}}`))
	if err == nil {
		t.Fatalf("a userless body with no errors parsed with no error; got %+v", out)
	}
	// And the same through the helper, where the field_exception bytes carry
	// no user either: refused either way.
	if _, err := parseSocialProfile([]byte(fieldExceptionEnvelope)); err == nil {
		t.Fatal("a field_exception body parsed as a profile")
	}
}

// The regression the raw call actually had: it must be keyed on the numeric
// igid, not the account's username. With a domain.IGID that no longer
// compiles, and this asserts what actually leaves in the request.
func TestTheSocialCallIsKeyedOnTheIGID(t *testing.T) {
	issuer := &fakeGraphQLIssuer{body: []byte(`{"data":{"user":{"is_private":true}}}`)}
	b := NewBridge(Options{Client: &fakeIG{}, Issuer: issuer})
	got := b.SocialProfile(t.Context(), mustIGID(t, "0246967358"))
	if len(issuer.sawIGID) != 1 || issuer.sawIGID[0] != "246967358" {
		t.Fatalf("social call keyed on %v, want [246967358]", issuer.sawIGID)
	}
	if got.Profile == nil || !got.Profile.IsPrivate || got.Err != nil || !got.Sent {
		t.Errorf("social read = %+v", got)
	}
}

// --- the upstream span (P1) ---------------------------------------------------

// The regression P1 exists for: a GraphQL failure that answers HTTP 200. A
// span that looked only at the status would be green here.
func TestGraphQLErrorsInsideA200MarkTheSpanFailed(t *testing.T) {
	rec := recordSpans(t)
	issuer := &fakeGraphQLIssuer{body: []byte(fieldExceptionEnvelope)}

	_, _, err := graphql(context.Background(), issuer, "PolarisProfilePageContentQuery", "profile query", nil)
	if err == nil {
		t.Fatal("a field_exception envelope was returned as a success")
	}
	s := spanNamed(t, rec, "instagram.graphql PolarisProfilePageContentQuery")
	if s.Status().Code != codes.Error {
		t.Fatalf("span status = %v for a field_exception envelope; a failed "+
			"document inside a 200 must read as a failure", s.Status().Code)
	}
	if v, _ := attrOf(s, "instagram.graphql.error_code"); v.AsInt64() != 1675030 {
		t.Errorf("instagram.graphql.error_code = %d, want 1675030", v.AsInt64())
	}
	if v, _ := attrOf(s, "error.type"); v.AsString() != "upstream_rejected" {
		t.Errorf("error.type = %q, want upstream_rejected", v.AsString())
	}
	if v, _ := attrOf(s, "http.response.status_code"); v.AsInt64() != 200 {
		t.Errorf("http.response.status_code = %d, want 200", v.AsInt64())
	}
	if v, _ := attrOf(s, "instagram.graphql.document"); v.AsString() != "PolarisProfilePageContentQuery" {
		t.Errorf("instagram.graphql.document = %q", v.AsString())
	}
}

// The positive half, so the failure above is not simply "every GraphQL span
// is red".
func TestASuccessfulGraphQLDocumentLeavesTheSpanUnset(t *testing.T) {
	rec := recordSpans(t)
	issuer := &fakeGraphQLIssuer{body: []byte(realProfileEnvelope)}

	if _, _, err := graphql(context.Background(), issuer, "PolarisProfilePageContentQuery", "profile query", nil); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	s := spanNamed(t, rec, "instagram.graphql PolarisProfilePageContentQuery")
	if s.Status().Code == codes.Error {
		t.Errorf("a successful document marked its span Error: %q", s.Status().Description)
	}
}

// A non-200 marks the span too, as `upstream_http`, not as a rejection of
// the document.
func TestANon200MarksTheSpanUpstreamHTTP(t *testing.T) {
	rec := recordSpans(t)
	issuer := &fakeGraphQLIssuer{status: http.StatusTooManyRequests}
	_, _, _ = graphql(context.Background(), issuer, "PolarisProfilePageContentQuery", "profile query", nil)
	s := spanNamed(t, rec, "instagram.graphql PolarisProfilePageContentQuery")
	if v, _ := attrOf(s, "error.type"); s.Status().Code != codes.Error || v.AsString() != "upstream_http" {
		t.Errorf("span status=%v error.type=%q, want Error/upstream_http", s.Status().Code, v.AsString())
	}
}

// Every TYPED call goes through `call`, and a challenge it returns is both a
// needs_human span and an ErrNeedsHuman to the caller.
func TestATypedCallClassifiesAChallengeOnTheSpanAndTheError(t *testing.T) {
	rec := recordSpans(t)
	b := NewBridge(Options{Client: &fakeIG{ProfileErr: httpclient.ErrCheckpointRequired}})
	_, err := b.Account(t.Context(), mustIGID(t, "246967358"))
	if !errors.Is(err, domain.ErrNeedsHuman) || !errors.Is(err, httpclient.ErrCheckpointRequired) {
		t.Errorf("err = %v, want ErrNeedsHuman wrapping the checkpoint", err)
	}
	s := spanNamed(t, rec, "instagram.GetProfile")
	if v, _ := attrOf(s, "error.type"); s.Status().Code != codes.Error || v.AsString() != "needs_human" {
		t.Errorf("span status=%v error.type=%q", s.Status().Code, v.AsString())
	}
	if v, _ := attrOf(s, "instagram.igid"); v.AsString() != "246967358" {
		t.Errorf("instagram.igid = %q", v.AsString())
	}
}

// "Message Too Long" is the library's opaque refusal text. The gateway is the
// one place allowed to read it, and it becomes ErrTooLarge.
func TestInstagramsTooLongRefusalIsErrTooLarge(t *testing.T) {
	f := &fakeIG{SendErr: errors.New("Message Too Long")}
	_, err := sendText(t.Context(), f, threadShort, "x", nil, "")
	if !errors.Is(err, domain.ErrTooLarge) {
		t.Errorf("err = %v, want ErrTooLarge", err)
	}
	// And an unknown failure stays unclassified: nothing guesses it
	// retryable.
	f = &fakeIG{SendErr: errors.New("some new graphql error nobody has seen")}
	if _, err := sendText(t.Context(), f, threadShort, "x", nil, ""); domain.KindOf(err) != nil {
		t.Errorf("an unknown failure was classified as %v", domain.KindOf(err))
	}
}

// --- Bridge.SocialProfile: the session-state side effect (issue #533) --------
//
// The raw GraphQL profile read goes through none of the library's own
// checkpoint detection, so a checkpoint it meets has to reach sessionRejected
// from Bridge.SocialProfile itself, as on Mentions.

func socialTestBridge(t *testing.T, issuer *fakeGraphQLIssuer) *Bridge {
	t.Helper()
	return NewBridge(Options{
		Store:     session.Store{Dir: t.TempDir()},
		State:     domain.StateAuthenticated,
		Connected: true,
		Self:      domain.Self{FBID: "18247361241432477", IGID: "79597894900", Username: fakeig.SelfUsername},
		Issuer:    issuer,
	})
}

func TestSocialProfileCheckpointFlipsTheSessionState(t *testing.T) {
	for name, issuer := range map[string]*fakeGraphQLIssuer{
		"4xx body":   {status: http.StatusBadRequest, body: []byte(`{"message":"checkpoint_required","checkpoint_url":"https://i.instagram.com/challenge/x"}`)},
		"200 fail":   {body: []byte(`{"status":"fail","message":"challenge_required"}`)},
		"dead token": {status: http.StatusForbidden, body: []byte(`{"message":"login_required","status":"fail"}`)},
	} {
		t.Run(name, func(t *testing.T) {
			b := socialTestBridge(t, issuer)
			read := b.SocialProfile(t.Context(), mustIGID(t, "123"))
			if !errors.Is(read.Err, domain.ErrNeedsHuman) {
				t.Fatalf("err = %v, want ErrNeedsHuman", read.Err)
			}
			snap := b.Snapshot()
			if snap.State != domain.StateCheckpointed && snap.State != domain.StateLoggedOut {
				t.Errorf("state = %q, want the session stopped", snap.State)
			}
		})
	}
}

// An ordinary failure (a plain 500, a GraphQL error with no checkpoint shape)
// must NOT stop the session.
func TestSocialProfileOrdinaryFailureDoesNotFlipTheSessionState(t *testing.T) {
	for name, issuer := range map[string]*fakeGraphQLIssuer{
		"500":           {status: http.StatusInternalServerError, body: []byte(`{}`)},
		"graphql error": {body: []byte(`{"errors":[{"message":"x","code":1,"summary":"y"}]}`)},
	} {
		t.Run(name, func(t *testing.T) {
			b := socialTestBridge(t, issuer)
			if read := b.SocialProfile(t.Context(), mustIGID(t, "123")); !errors.Is(read.Err, domain.ErrUpstreamRejected) {
				t.Fatalf("err = %v, want ErrUpstreamRejected", read.Err)
			}
			if snap := b.Snapshot(); snap.State != domain.StateAuthenticated {
				t.Errorf("state = %q, want authenticated (unchanged)", snap.State)
			}
		})
	}
}
