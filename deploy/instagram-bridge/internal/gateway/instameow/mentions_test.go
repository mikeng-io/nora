package instameow

// readMentions against LITERAL bytes (fakeig/mentions.go), decoded exactly as
// the raw REST path decodes them. NewsInboxJSON's KEYS were captured from the
// live web client on 2026-09-25; every value is synthetic. See mentions.go's
// doc comment.

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"strings"
	"testing"

	"instagram-bridge/internal/domain"
	"instagram-bridge/internal/gateway/instameow/fakeig"
	"instagram-bridge/internal/session"

	"go.mau.fi/mautrix-meta/pkg/messagix/httpclient"
	"go.mau.fi/mautrix-meta/pkg/messagix/types"
	"go.opentelemetry.io/otel/codes"
)

// fakeRESTIssuer records the headers and BuildHeaders args it was asked for
// and answers with the library's OWN (resp, body, error) shape — the same
// role fakeGraphQLIssuer plays for the raw GraphQL path
// (social_profile_test.go), but honest about what makeRequestOnce actually
// returns (http.go:498-541): a >=400 status hands back the response AND its
// body ALONGSIDE a non-nil error (`%w %d` around ErrUnexpectedError); 429
// hands back the response with NO body and ErrRateLimited; only a genuine
// transport failure (no response at all) omits the response.
type fakeRESTIssuer struct {
	status  int
	body    []byte
	err     error // a transport-level failure with no HTTP response at all
	session string

	headers          http.Header
	buildHeadersArgs [2]bool
	// built is what BuildHeaders returns (a copy), standing in for the
	// library's own header set; nil is an empty one.
	built http.Header
	// noTokens makes WebFormTokens answer empty values.
	noTokens bool

	// What the last request was, and how many went out through each entry
	// point: a guard's "zero upstream calls" is calls() == 0.
	method      string
	url         string
	payload     []byte
	contentType types.ContentType
	// respHeader is the response's header (Location, Set-Cookie) for a 3xx.
	respHeader http.Header

	retrying int // MakeRequest: the library's retry loop
	once     int // MakeRequestOnceNoRedirect: no retry, no redirect
}

func (f *fakeRESTIssuer) calls() int { return f.retrying + f.once }

func (f *fakeRESTIssuer) BuildHeaders(withCookies, isSecFetchDocument bool) http.Header {
	f.buildHeadersArgs = [2]bool{withCookies, isSecFetchDocument}
	if f.built == nil {
		return http.Header{}
	}
	return f.built.Clone()
}

// The synthetic web form tokens every webForm POST carries in these tests.
const (
	testJazoest     = "22222"
	testFBDTSG      = "synthetic-fb-dtsg"
	testRolloutHash = "1000000000"
)

func (f *fakeRESTIssuer) WebFormTokens() (string, string, string, error) {
	if f.noTokens {
		return "", "", "", nil
	}
	return testJazoest, testFBDTSG, testRolloutHash, nil
}

// testSelf is the account as the bridge knows it: the handle for the "@"
// token, the igid for `mentioned_user_ids`.
var testSelf = domain.Self{FBID: "14963523149149302", IGID: fakeig.SelfIGID, Username: fakeig.SelfUsername}

func (f *fakeRESTIssuer) WebSessionID() (string, error) {
	if f.session == "" {
		return "synthetic-web-session-id", nil
	}
	return f.session, nil
}

func (f *fakeRESTIssuer) MakeRequest(
	ctx context.Context, url, method string, headers http.Header, payload []byte, contentType types.ContentType,
) (*http.Response, []byte, error) {
	f.retrying++
	return f.answer(ctx, url, method, headers, payload, contentType)
}

func (f *fakeRESTIssuer) MakeRequestOnceNoRedirect(
	ctx context.Context, url, method string, headers http.Header, payload []byte, contentType types.ContentType,
) (*http.Response, []byte, error) {
	f.once++
	return f.answer(ctx, url, method, headers, payload, contentType)
}

func (f *fakeRESTIssuer) answer(
	_ context.Context, url, method string, headers http.Header, payload []byte, contentType types.ContentType,
) (*http.Response, []byte, error) {
	f.headers = headers
	f.method, f.url, f.payload, f.contentType = method, url, payload, contentType
	if f.err != nil {
		if f.status != 0 {
			// The redirect-following client's real checkpoint shape
			// (http.go:517-520): the 3xx response, no body, and the
			// RedirectedError unwrapped out of Go's *url.Error.
			return &http.Response{StatusCode: f.status, Header: f.respHeader}, nil, f.err
		}
		return nil, nil, f.err
	}
	status := f.status
	if status == 0 {
		status = http.StatusOK
	}
	resp := &http.Response{StatusCode: status, Header: f.respHeader}
	if status == http.StatusTooManyRequests {
		// makeRequestOnce returns EARLY on 429, before the body is ever read.
		return resp, nil, httpclient.ErrRateLimited
	}
	if status >= 400 {
		return resp, f.body, fmt.Errorf("%w %d", httpclient.ErrUnexpectedError, status)
	}
	return resp, f.body, nil
}

// Every field a comment-mention carries, off the one story in the fixture
// shaped to produce one.
func TestReadMentionsClassifiesACommentMentionWithEveryField(t *testing.T) {
	issuer := &fakeRESTIssuer{body: []byte(fakeig.NewsInboxJSON)}
	mentions, skipped, err := readMentions(t.Context(), issuer, testSelf)
	if err != nil {
		t.Fatal(err)
	}
	if len(mentions) != 1 {
		t.Fatalf("mentions = %d, want 1: %+v", len(mentions), mentions)
	}
	m := mentions[0]
	if m.StoryType != fakeig.MentionStoryType {
		t.Errorf("story_type = %d, want %d", m.StoryType, fakeig.MentionStoryType)
	}
	if m.At != 1700000123 {
		t.Errorf("at = %d, want 1700000123", m.At)
	}
	if m.AuthorIGID != fakeig.MentionCommentAuthorIGID || m.AuthorUsername != fakeig.MentionCommentAuthorUsername {
		t.Errorf("author = %+v, want the fixture's commenter", m)
	}
	if m.MediaID != fakeig.CarouselMediaID || m.MediaShortcode != fakeig.CarouselShortcode {
		t.Errorf("media = %q/%q, want %q/%q", m.MediaID, m.MediaShortcode, fakeig.CarouselMediaID, fakeig.CarouselShortcode)
	}
	if m.CommentID != fakeig.MentionCommentID {
		t.Errorf("comment id = %q, want %q", m.CommentID, fakeig.MentionCommentID)
	}
	if m.NotificationText != fakeig.MentionNotificationText {
		t.Errorf("notification text = %q, want the fixture's text", m.NotificationText)
	}

	// The four non-mentions, counted by story_type: "liked your comment"
	// (13), post like (768), story like (651), caption mention with no
	// comment id.
	want := map[int]int{13: 1, 768: 1, 651: 1, fakeig.CaptionMentionStoryType: 1}
	if len(skipped) != len(want) {
		t.Fatalf("skipped = %+v, want %+v", skipped, want)
	}
	for k, v := range want {
		if skipped[k] != v {
			t.Errorf("skipped[%d] = %d, want %d", k, skipped[k], v)
		}
	}
}

// The media shortcode is derived from the numeric pk when the payload
// carries no "shortcode" key.
func TestReadMentionsDerivesShortcodeWhenMissing(t *testing.T) {
	issuer := &fakeRESTIssuer{body: []byte(fakeig.NewsInboxNoShortcodeJSON)}
	mentions, _, err := readMentions(t.Context(), issuer, testSelf)
	if err != nil {
		t.Fatal(err)
	}
	if len(mentions) != 1 {
		t.Fatalf("mentions = %d, want 1", len(mentions))
	}
	if mentions[0].MediaID != fakeig.ImageMediaID {
		t.Errorf("media id = %q, want %q", mentions[0].MediaID, fakeig.ImageMediaID)
	}
	if mentions[0].MediaShortcode != fakeig.ImageShortcode {
		t.Errorf("derived shortcode = %q, want %q", mentions[0].MediaShortcode, fakeig.ImageShortcode)
	}
}

// `comment_id` absent, `comment_ids[0]` present: the fallback still
// classifies the story and takes the FIRST id.
func TestReadMentionsFallsBackToCommentIDsFirstElement(t *testing.T) {
	raw := `{"new_stories":[{"story_type":3,"type":3,"args":{
		"text":"@` + fakeig.SelfUsername + ` mentioned you in a comment",
		"profile_id":"600000000001","profile_name":"commenter_zeta",
		"timestamp":1700000500,
		"media":[{"id":"` + fakeig.ImageMediaID + `_10000001","shortcode":"` + fakeig.ImageShortcode + `"}],
		"comment_ids":["17900000000000099","17900000000000100"]
	}}],"old_stories":[],"status":"ok"}`
	mentions, _, err := readMentions(t.Context(), &fakeRESTIssuer{body: []byte(raw)}, testSelf)
	if err != nil {
		t.Fatal(err)
	}
	if len(mentions) != 1 || mentions[0].CommentID != "17900000000000099" {
		t.Fatalf("mentions = %+v, want one with comment id 17900000000000099", mentions)
	}
}

// A mention in a CAPTION (media + the "@" token, no comment id) is skipped,
// not classified — the comment id check is load-bearing on its own.
func TestReadMentionsSkipsAMentionWithNoCommentID(t *testing.T) {
	mentions, skipped, err := readMentions(t.Context(),
		&fakeRESTIssuer{body: []byte(fakeig.NewsInboxJSON)}, testSelf)
	if err != nil {
		t.Fatal(err)
	}
	for _, m := range mentions {
		if m.StoryType == fakeig.CaptionMentionStoryType {
			t.Fatalf("the caption-mention (no comment id) was classified as a comment mention: %+v", m)
		}
	}
	if skipped[fakeig.CaptionMentionStoryType] != 1 {
		t.Errorf("skipped[caption] = %d, want 1", skipped[fakeig.CaptionMentionStoryType])
	}
}

// An identity with neither a username nor an igid classifies nothing: fail
// closed rather than guess who "the account" is.
func TestReadMentionsWithNoSelfUsernameClassifiesNothing(t *testing.T) {
	mentions, skipped, err := readMentions(t.Context(),
		&fakeRESTIssuer{body: []byte(fakeig.NewsInboxJSON)}, domain.Self{})
	if err != nil {
		t.Fatal(err)
	}
	if len(mentions) != 0 {
		t.Fatalf("mentions = %+v, want none with no self identity", mentions)
	}
	if skipped[fakeig.MentionStoryType] != 1 {
		t.Errorf("skipped[%d] = %d, want 1 (the story that would otherwise have been the mention)",
			fakeig.MentionStoryType, skipped[fakeig.MentionStoryType])
	}
}

// "@" + self must not match as a PREFIX of a longer handle: "@nora_fan" is
// not a mention of "nora".
func TestReadMentionsRequiresAWordBoundaryAfterTheMention(t *testing.T) {
	raw := `{"new_stories":[{"story_type":3,"type":3,"args":{
		"text":"@` + fakeig.SelfUsername + `_fan loves this post, commented",
		"profile_id":"700000000001","profile_name":"impostor",
		"timestamp":1700000600,
		"media":[{"id":"` + fakeig.ImageMediaID + `_10000001","shortcode":"` + fakeig.ImageShortcode + `"}],
		"comment_id":"17900000000000200"
	}}],"old_stories":[],"status":"ok"}`
	mentions, skipped, err := readMentions(t.Context(), &fakeRESTIssuer{body: []byte(raw)}, testSelf)
	if err != nil {
		t.Fatal(err)
	}
	if len(mentions) != 0 {
		t.Fatalf("mentions = %+v, want none: \"@%s_fan\" is not a mention of %q", mentions, fakeig.SelfUsername, testSelf)
	}
	if skipped[3] != 1 {
		t.Errorf("skipped[3] = %d, want 1", skipped[3])
	}
}

// The exact same text with a boundary character (a space) after the handle
// DOES classify — proves the word-boundary test above is not just failing
// to match at all.
func TestReadMentionsMatchesAtAWordBoundary(t *testing.T) {
	raw := `{"new_stories":[{"story_type":3,"type":3,"args":{
		"text":"@` + fakeig.SelfUsername + ` nice! commented",
		"profile_id":"700000000002","profile_name":"real_fan",
		"timestamp":1700000700,
		"media":[{"id":"` + fakeig.ImageMediaID + `_10000001","shortcode":"` + fakeig.ImageShortcode + `"}],
		"comment_id":"17900000000000201"
	}}],"old_stories":[],"status":"ok"}`
	mentions, _, err := readMentions(t.Context(), &fakeRESTIssuer{body: []byte(raw)}, testSelf)
	if err != nil {
		t.Fatal(err)
	}
	if len(mentions) != 1 {
		t.Fatalf("mentions = %+v, want 1", mentions)
	}
}

// Ids arriving as JSON NUMBERS and a timestamp arriving as a numeric STRING
// both decode: nothing has measured which JSON type this endpoint actually
// uses for either.
func TestReadMentionsToleratesNumericIDsAndStringTimestamp(t *testing.T) {
	issuer := &fakeRESTIssuer{body: []byte(fakeig.UnmeasuredNewsInboxNumericIDsJSON)}
	mentions, _, err := readMentions(t.Context(), issuer, testSelf)
	if err != nil {
		t.Fatal(err)
	}
	if len(mentions) != 1 {
		t.Fatalf("mentions = %d, want 1: %+v", len(mentions), mentions)
	}
	m := mentions[0]
	if m.AuthorIGID != fakeig.MentionCommentAuthorIGID {
		t.Errorf("author igid = %q, want %q (decoded from a JSON number)", m.AuthorIGID, fakeig.MentionCommentAuthorIGID)
	}
	if m.CommentID != fakeig.MentionCommentID {
		t.Errorf("comment id = %q, want %q (decoded from a JSON number)", m.CommentID, fakeig.MentionCommentID)
	}
	if m.At != 1700000123 {
		t.Errorf("at = %d, want 1700000123 (decoded from a numeric string)", m.At)
	}
}

// One story that does not fit the hypothesis at all (args.text is a JSON
// number, not a string) is counted under undecodableStoryType and skipped —
// the rest of the read still succeeds.
func TestReadMentionsCountsAnUndecodableStoryAndKeepsGoing(t *testing.T) {
	issuer := &fakeRESTIssuer{body: []byte(fakeig.UnmeasuredNewsInboxUndecodableJSON)}
	mentions, skipped, err := readMentions(t.Context(), issuer, testSelf)
	if err != nil {
		t.Fatal(err)
	}
	if len(mentions) != 0 {
		t.Fatalf("mentions = %+v, want none", mentions)
	}
	if skipped[undecodableStoryType] != 1 {
		t.Errorf("skipped[%d] = %d, want 1", undecodableStoryType, skipped[undecodableStoryType])
	}
	if skipped[2] != 1 {
		t.Errorf("skipped[2] = %d, want 1 (the ordinary follow story alongside it)", skipped[2])
	}
}

// The REST form of an error inside an HTTP 200 (`"status": "fail"`) is a
// failure, and its upstream span says so.
func TestReadMentionsFailureIn200IsRejectedAndMarksTheSpan(t *testing.T) {
	rec := recordGatewaySpans(t)
	_, _, err := readMentions(t.Context(), &fakeRESTIssuer{body: []byte(fakeig.UnmeasuredNewsInboxFailJSON)}, testSelf)
	if !errors.Is(err, domain.ErrUpstreamRejected) {
		t.Fatalf("err = %v, want ErrUpstreamRejected", err)
	}
	spans := rec.Ended()
	if len(spans) != 1 || spans[0].Name() != "instagram.rest news_inbox" {
		t.Fatalf("spans = %+v, want exactly one instagram.rest news_inbox", spans)
	}
	if spans[0].Status().Code != codes.Error {
		t.Errorf("span status = %v, want Error for a failure inside a 200", spans[0].Status())
	}
}

// Every non-2xx status MakeRequest can answer with, classified the way
// fetchMediaFailure classifies FetchMedia's (posts.go:111-130): 404 is "no
// such thing", 429 is retryable, any other 4xx is upstream-rejected — with
// FIXED error text, no id ever reaching Error().
func TestReadMentionsClassifiesUpstreamStatuses(t *testing.T) {
	for _, tc := range []struct {
		name    string
		issuer  *fakeRESTIssuer
		kind    error
		wantMsg string
	}{
		{"404", &fakeRESTIssuer{status: http.StatusNotFound, body: []byte(`{}`)},
			domain.ErrNotFound, "news_inbox returned 404"},
		{"429", &fakeRESTIssuer{status: http.StatusTooManyRequests},
			domain.ErrUnavailable, "news_inbox was rate limited"},
		{"400 (no checkpoint shape)", &fakeRESTIssuer{status: http.StatusBadRequest, body: []byte(`{"message":"something else"}`)},
			domain.ErrUpstreamRejected, "news_inbox returned a non-200: 400"},
		{"500", &fakeRESTIssuer{status: http.StatusInternalServerError, body: []byte(`{}`)},
			domain.ErrUpstreamRejected, "news_inbox returned a non-200: 500"},
		{"transport-level checkpoint (no response at all)", &fakeRESTIssuer{err: httpclient.ErrCheckpointRequired}, domain.ErrNeedsHuman, ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			_, _, err := readMentions(t.Context(), tc.issuer, testSelf)
			if domain.KindOf(err) != tc.kind {
				t.Errorf("err = %v (kind %v), want %v", err, domain.KindOf(err), tc.kind)
			}
			if tc.wantMsg != "" && (err == nil || err.Error() != tc.wantMsg) {
				t.Errorf("err.Error() = %q, want the fixed text %q", err, tc.wantMsg)
			}
		})
	}
}

// A 4xx body naming a checkpoint/challenge/consent screen or a dead token
// classifies as ErrNeedsHuman, taking priority over the bare status — the
// ONLY place a raw REST failure body is ever read for this, since the
// library's own checkResponseError never sees a raw MakeRequest's error.
func TestReadMentionsReadsACheckpointOutOfA4xxBody(t *testing.T) {
	for _, tc := range []struct {
		name string
		body string
	}{
		{"checkpoint_required message", `{"message":"checkpoint_required"}`},
		{"non-empty checkpoint_url alone", `{"message":"something else","checkpoint_url":"https://i.instagram.com/challenge/x"}`},
		{"challenge_required", `{"message":"challenge_required"}`},
		{"consent_required", `{"message":"consent_required"}`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			issuer := &fakeRESTIssuer{status: http.StatusBadRequest, body: []byte(tc.body)}
			_, _, err := readMentions(t.Context(), issuer, testSelf)
			if !errors.Is(err, domain.ErrNeedsHuman) {
				t.Errorf("err = %v, want ErrNeedsHuman", err)
			}
		})
	}
	// login_required is the dead-token shape.
	issuer := &fakeRESTIssuer{status: http.StatusBadRequest, body: []byte(`{"message":"login_required"}`)}
	_, _, err := readMentions(t.Context(), issuer, testSelf)
	if !errors.Is(err, httpclient.ErrTokenInvalidated) {
		t.Errorf("err = %v, want it to wrap ErrTokenInvalidated", err)
	}
}

// The request is the web client's own, as captured 2026-09-25: a POST to
// /api/v1/news/inbox/, form-encoded, carrying jazoest, fb_dtsg and
// selected_filters=comments, through the no-retry, no-redirect path. Its
// headers are the library's own set (passed through unchanged) plus what
// restDo adds: x-requested-with, a bare base_url referer, the sec-fetch-*
// triple, X-Web-Session-ID, origin/priority for a POST, X-Instagram-AJAX (the
// rollout hash) — and "0" for X-IG-WWW-Claim when BuildHeaders set none.
func TestReadMentionsSendsTheWebClientsRequest(t *testing.T) {
	library := http.Header{}
	for k, v := range map[string]string{
		"accept": "*/*", "x-csrftoken": "synthetic-csrf", "x-ig-app-id": "936619743392459",
		"x-asbd-id": "359341", "X-IG-Max-Touch-Points": "0",
	} {
		library.Set(k, v)
	}
	issuer := &fakeRESTIssuer{body: []byte(fakeig.NewsInboxJSON), built: library}
	if _, _, err := readMentions(t.Context(), issuer, testSelf); err != nil {
		t.Fatal(err)
	}
	if issuer.method != http.MethodPost || issuer.url != "https://www.instagram.com/api/v1/news/inbox/" {
		t.Errorf("request = %s %s, want POST https://www.instagram.com/api/v1/news/inbox/", issuer.method, issuer.url)
	}
	if issuer.once != 1 || issuer.retrying != 0 {
		t.Errorf("once = %d, retrying = %d; want the no-retry, no-redirect path only", issuer.once, issuer.retrying)
	}
	if issuer.contentType != types.FORM || string(issuer.contentType) != "application/x-www-form-urlencoded" {
		t.Errorf("content type = %q, want application/x-www-form-urlencoded", issuer.contentType)
	}
	form, err := url.ParseQuery(string(issuer.payload))
	if err != nil {
		t.Fatal(err)
	}
	wantForm := url.Values{"jazoest": {testJazoest}, "fb_dtsg": {testFBDTSG}, "selected_filters": {"comments"}}
	if form.Encode() != wantForm.Encode() {
		t.Errorf("form = %q, want exactly %q", form.Encode(), wantForm.Encode())
	}
	if issuer.buildHeadersArgs != [2]bool{true, false} {
		t.Errorf("BuildHeaders called with %v, want (true, false)", issuer.buildHeadersArgs)
	}
	for k := range library {
		if issuer.headers.Get(k) != library.Get(k) {
			t.Errorf("header %q = %q, want the library's %q unchanged", k, issuer.headers.Get(k), library.Get(k))
		}
	}
	for h, want := range map[string]string{
		"x-requested-with": "XMLHttpRequest",
		"referer":          "https://www.instagram.com",
		"sec-fetch-dest":   "empty",
		"sec-fetch-mode":   "cors",
		"sec-fetch-site":   "same-origin",
		"X-Web-Session-ID": "synthetic-web-session-id",
		"origin":           "https://www.instagram.com",
		"X-Instagram-AJAX": testRolloutHash,
		"X-IG-WWW-Claim":   "0",
	} {
		if got := issuer.headers.Get(h); got != want {
			t.Errorf("header %q = %q, want %q", h, got, want)
		}
	}
}

// A claim the session already holds (BuildHeaders sets it off the library's
// cookies) is sent as it is, never overwritten with "0".
func TestReadMentionsKeepsTheSessionsWWWClaim(t *testing.T) {
	library := http.Header{}
	library.Set("x-ig-www-claim", "hmac.synthetic-claim")
	issuer := &fakeRESTIssuer{body: []byte(fakeig.NewsInboxJSON), built: library}
	if _, _, err := readMentions(t.Context(), issuer, testSelf); err != nil {
		t.Fatal(err)
	}
	if got := issuer.headers.Get("X-IG-WWW-Claim"); got != "hmac.synthetic-claim" {
		t.Errorf("X-IG-WWW-Claim = %q, want the session's own claim", got)
	}
}

// With any web form token missing, no request goes out: a form without
// fb_dtsg is not the web client's request.
func TestReadMentionsSendsNoRequestWithoutWebFormTokens(t *testing.T) {
	issuer := &fakeRESTIssuer{body: []byte(fakeig.NewsInboxJSON), noTokens: true}
	_, _, err := readMentions(t.Context(), issuer, testSelf)
	if !errors.Is(err, errWebFormTokens) {
		t.Fatalf("err = %v, want errWebFormTokens", err)
	}
	if issuer.calls() != 0 {
		t.Errorf("%d request(s) went out with no web form tokens", issuer.calls())
	}
}

// The capture's own finding: no story carries comment_id/comment_ids; a
// comment's id rides in `destination`'s target_comment_id. The main
// fixture's mention has its comment id ONLY there.
func TestReadMentionsTakesTheCommentIDFromTheDestination(t *testing.T) {
	if strings.Contains(fakeig.NewsInboxJSON, `"comment_id"`) {
		t.Fatal("precondition: the captured-key fixture must carry no comment_id key")
	}
	mentions, _, err := readMentions(t.Context(), &fakeRESTIssuer{body: []byte(fakeig.NewsInboxJSON)}, testSelf)
	if err != nil {
		t.Fatal(err)
	}
	if len(mentions) != 1 || mentions[0].CommentID != fakeig.MentionCommentID {
		t.Fatalf("mentions = %+v, want one with comment id %s from the destination", mentions, fakeig.MentionCommentID)
	}
}

// With no media array, the media comes from a media destination's `id`;
// a `user?id=` destination's id is a user, never taken as media.
func TestReadMentionsTakesTheMediaFromAMediaDestinationOnly(t *testing.T) {
	story := func(destination string) string {
		return `{"new_stories":[{"story_type":900,"args":{
			"text":"@` + fakeig.SelfUsername + ` look",
			"profile_id":"600000000002","profile_name":"commenter_eta",
			"timestamp":1700000800,
			"destination":"` + destination + `"
		}}],"old_stories":[],"status":"ok"}`
	}
	mentions, _, err := readMentions(t.Context(), &fakeRESTIssuer{body: []byte(story(
		"clips_home?id=" + fakeig.ImageMediaID + "&target_comment_id=17900000000000300"))}, testSelf)
	if err != nil {
		t.Fatal(err)
	}
	if len(mentions) != 1 || mentions[0].MediaID != fakeig.ImageMediaID || mentions[0].MediaShortcode != fakeig.ImageShortcode ||
		mentions[0].CommentID != "17900000000000300" {
		t.Fatalf("mentions = %+v, want one on %s/%s with comment 17900000000000300", mentions, fakeig.ImageMediaID, fakeig.ImageShortcode)
	}
	mentions, skipped, err := readMentions(t.Context(), &fakeRESTIssuer{body: []byte(story(
		"user?id=600000000002&username=commenter_eta&target_comment_id=17900000000000300"))}, testSelf)
	if err != nil {
		t.Fatal(err)
	}
	if len(mentions) != 0 || skipped[900] != 1 {
		t.Fatalf("mentions = %+v, skipped = %v; a user destination's id is not a media id", mentions, skipped)
	}
}

// story_type 13, "liked your comment": it has a comment id (in the
// destination) and media, but it does not mention the account, so it is
// skipped and counted — never a summons.
func TestReadMentionsSkipsALikedYourCommentStory(t *testing.T) {
	mentions, skipped, err := readMentions(t.Context(), &fakeRESTIssuer{body: []byte(fakeig.NewsInboxJSON)}, testSelf)
	if err != nil {
		t.Fatal(err)
	}
	for _, m := range mentions {
		if m.CommentID == fakeig.LikedCommentID || m.StoryType == 13 {
			t.Fatalf("the liked-your-comment story was classified as a mention: %+v", m)
		}
	}
	if skipped[13] != 1 {
		t.Errorf("skipped[13] = %d, want 1", skipped[13])
	}
}

// The account's own id in `logging_context.mentioned_user_ids` is NOT a
// mention. The live capture (2026-09-25) had exactly one id there on every
// story type, likes included, so it most likely names the notified account.
// A story that carries a comment id and media but names her only there, like
// a "liked your comment", must never become a summons.
func TestReadMentionsIgnoresTheSelfIDInMentionedUserIDs(t *testing.T) {
	raw := `{"new_stories":[{"story_type":13,"args":{
		"text":"commenter_theta liked your comment: nice",
		"profile_id":"600000000003","profile_name":"commenter_theta",
		"timestamp":1700000900,
		"destination":"clips_home?id=` + fakeig.ImageMediaID + `&target_comment_id=17900000000000400",
		"logging_context":{"mentioned_user_ids":[` + fakeig.SelfIGID + `],"mentioned_content_ids":[],"content_id":"1"}
	}}],"old_stories":[],"status":"ok"}`
	mentions, skipped, err := readMentions(t.Context(), &fakeRESTIssuer{body: []byte(raw)}, testSelf)
	if err != nil {
		t.Fatal(err)
	}
	if len(mentions) != 0 || skipped[13] != 1 {
		t.Fatalf("mentions = %+v, skipped = %v; want none and one skipped type-13", mentions, skipped)
	}
}

// A logging_context this file does not expect (a string, a list of
// non-ids) costs only that signal: the story still decodes, and the "@"
// token still classifies it.
func TestReadMentionsToleratesAnUnexpectedLoggingContext(t *testing.T) {
	for name, lc := range map[string]string{
		"a string":      `"logging_context":"{\"mentioned_user_ids\":[1]}"`,
		"garbage ids":   `"logging_context":{"mentioned_user_ids":["abc", 1.5e3, null]}`,
		"ids not array": `"logging_context":{"mentioned_user_ids":7}`,
	} {
		t.Run(name, func(t *testing.T) {
			raw := `{"new_stories":[{"story_type":900,"args":{
				"text":"@` + fakeig.SelfUsername + ` hello",` + lc + `,
				"profile_id":"600000000004","profile_name":"commenter_iota",
				"timestamp":1700001000,
				"destination":"clips_home?id=` + fakeig.ImageMediaID + `&target_comment_id=17900000000000500"
			}}],"old_stories":[],"status":"ok"}`
			mentions, skipped, err := readMentions(t.Context(), &fakeRESTIssuer{body: []byte(raw)}, testSelf)
			if err != nil {
				t.Fatal(err)
			}
			if len(mentions) != 1 || skipped[undecodableStoryType] != 0 {
				t.Fatalf("mentions = %+v, skipped = %v; want the story classified by its @ token", mentions, skipped)
			}
		})
	}
}

// A comment id in the destination that is not all digits is not carried.
func TestReadMentionsRefusesANonNumericDestinationCommentID(t *testing.T) {
	raw := `{"new_stories":[{"story_type":900,"args":{
		"text":"@` + fakeig.SelfUsername + ` hello",
		"profile_id":"600000000005","profile_name":"commenter_kappa",
		"timestamp":1700001100,
		"destination":"clips_home?id=` + fakeig.ImageMediaID + `&target_comment_id=1%3B2"
	}}],"old_stories":[],"status":"ok"}`
	mentions, skipped, err := readMentions(t.Context(), &fakeRESTIssuer{body: []byte(raw)}, testSelf)
	if err != nil {
		t.Fatal(err)
	}
	if len(mentions) != 0 || skipped[900] != 1 {
		t.Fatalf("mentions = %+v, skipped = %v; want none", mentions, skipped)
	}
}

// No web session id, no request: a request missing a header the real web
// client always sends is the mismatch `rest` exists to avoid.
func TestReadMentionsSendsNoRequestWithNoWebSessionID(t *testing.T) {
	inner := &fakeRESTIssuer{body: []byte(fakeig.NewsInboxJSON)}
	failing := &failingWebSessionIssuer{fakeRESTIssuer: inner}
	_, _, err := readMentions(t.Context(), failing, testSelf)
	if err == nil {
		t.Fatal("want an error")
	}
	if inner.headers != nil {
		t.Error("a request went out with no web session id")
	}
}

// failingWebSessionIssuer overrides WebSessionID() to fail, so
// TestReadMentionsSendsNoRequestWithNoWebSessionID does not need a THIRD
// sentinel field on the shared fake.
type failingWebSessionIssuer struct {
	*fakeRESTIssuer
}

func (f *failingWebSessionIssuer) WebSessionID() (string, error) {
	return "", errors.New("no session id available")
}

// R-18: no igid, username, media id, comment id or notification text may
// reach a span attribute/event or an error string.
func TestReadMentionsNeverLeaksAnIdentifier(t *testing.T) {
	rec := recordGatewaySpans(t)
	mentions, _, err := readMentions(t.Context(), &fakeRESTIssuer{body: []byte(fakeig.NewsInboxJSON)}, testSelf)
	if err != nil {
		t.Fatal(err)
	}
	if len(mentions) == 0 {
		t.Fatal("want at least one mention to check for leakage around")
	}
	secrets := []string{
		fakeig.MentionCommentAuthorIGID, fakeig.MentionCommentAuthorUsername,
		fakeig.MentionCommentID, fakeig.MentionNotificationText,
		fakeig.CarouselMediaID, fakeig.CarouselShortcode,
	}

	for _, s := range rec.Ended() {
		for _, kv := range s.Attributes() {
			checkNoSecret(t, "span "+s.Name()+" attribute "+string(kv.Key), kv.Value.Emit(), secrets)
		}
		for _, ev := range s.Events() {
			for _, kv := range ev.Attributes {
				checkNoSecret(t, "span "+s.Name()+" event "+ev.Name+" attribute "+string(kv.Key), kv.Value.Emit(), secrets)
			}
		}
		checkNoSecret(t, "span "+s.Name()+" status description", s.Status().Description, secrets)
	}

	_, _, err2 := readMentions(t.Context(), &fakeRESTIssuer{status: http.StatusBadRequest, body: []byte(`{}`)}, testSelf)
	if err2 == nil {
		t.Fatal("want an error")
	}
	checkNoSecret(t, "error text", err2.Error(), secrets)
}

func checkNoSecret(t *testing.T, where, got string, secrets []string) {
	t.Helper()
	for _, secret := range secrets {
		if strings.Contains(got, secret) {
			t.Errorf("%s = %q carries %q", where, got, secret)
		}
	}
}

// --- Bridge.Mentions: the session-state side effect ---------------------------
//
// A raw REST call goes through none of the library's own checkpoint
// detection (bridge.go's sessionRejected doc comment), so Bridge.Mentions has
// to run it itself. This is the blocker: without it, a checkpoint discovered
// through /mentions left the session reporting `authenticated` forever.

func mentionsTestBridge(t *testing.T, issuer *fakeRESTIssuer) *Bridge {
	t.Helper()
	return NewBridge(Options{
		Store:      session.Store{Dir: t.TempDir()},
		State:      domain.StateAuthenticated,
		Connected:  true,
		Self:       domain.Self{FBID: "14963523149149302", IGID: "79597894900", Username: fakeig.SelfUsername},
		RESTIssuer: issuer,
	})
}

// The blocker's own test: a 400 body naming checkpoint_required flips the
// BRIDGE's session state to checkpointed, asserted through Snapshot() — not
// just the returned error.
func TestMentionsCheckpointBodyFlipsTheSessionState(t *testing.T) {
	issuer := &fakeRESTIssuer{status: http.StatusBadRequest, body: []byte(`{"message":"checkpoint_required","checkpoint_url":"https://i.instagram.com/challenge/x"}`)}
	b := mentionsTestBridge(t, issuer)

	if snap := b.Snapshot(); snap.State != domain.StateAuthenticated {
		t.Fatalf("precondition: state = %q, want authenticated", snap.State)
	}

	_, _, err := b.Mentions(t.Context())
	if !errors.Is(err, domain.ErrNeedsHuman) {
		t.Fatalf("err = %v, want ErrNeedsHuman", err)
	}
	snap := b.Snapshot()
	if snap.State != domain.StateCheckpointed {
		t.Errorf("state = %q, want checkpointed", snap.State)
	}
	if snap.Detail == "" {
		t.Error("detail is empty; want it to name what Instagram said")
	}
}

// An ordinary upstream failure (a plain 500, no checkpoint shape) must NOT
// flip the session state: only a needs-human answer does.
func TestMentionsOrdinaryFailureDoesNotFlipTheSessionState(t *testing.T) {
	issuer := &fakeRESTIssuer{status: http.StatusInternalServerError, body: []byte(`{}`)}
	b := mentionsTestBridge(t, issuer)
	if _, _, err := b.Mentions(t.Context()); !errors.Is(err, domain.ErrUpstreamRejected) {
		t.Fatalf("err = %v, want ErrUpstreamRejected", err)
	}
	if snap := b.Snapshot(); snap.State != domain.StateAuthenticated {
		t.Errorf("state = %q, want authenticated (unchanged)", snap.State)
	}
}

// A checkpoint can arrive as a 200 carrying "status":"fail" and a
// checkpoint message. It must flip the session like the 4xx form does.
func TestMentionsCheckpointInA200FailFlipsTheSessionState(t *testing.T) {
	issuer := &fakeRESTIssuer{body: []byte(`{"status":"fail","message":"challenge_required"}`)}
	b := mentionsTestBridge(t, issuer)
	if _, _, err := b.Mentions(t.Context()); !errors.Is(err, domain.ErrNeedsHuman) {
		t.Fatalf("err = %v, want ErrNeedsHuman", err)
	}
	if snap := b.Snapshot(); snap.State != domain.StateCheckpointed {
		t.Errorf("state = %q, want checkpointed", snap.State)
	}
}

// Now a POST, /mentions goes out through the no-redirect path: a 3xx to a
// challenge or to /accounts/login/ is read by redirectSentinel, needs a
// human, and flips the session exactly as a 4xx checkpoint body does.
func TestMentionsRedirectToAChallengeFlipsTheSessionState(t *testing.T) {
	for name, loc := range map[string]string{
		"challenge":  "https://www.instagram.com/challenge/action/x/",
		"login":      "https://www.instagram.com/accounts/login/?next=/",
		"checkpoint": "https://www.instagram.com/checkpoint/1/",
	} {
		t.Run(name, func(t *testing.T) {
			issuer := &fakeRESTIssuer{status: http.StatusFound, respHeader: http.Header{"Location": {loc}}}
			b := mentionsTestBridge(t, issuer)
			if _, _, err := b.Mentions(t.Context()); !errors.Is(err, domain.ErrNeedsHuman) {
				t.Fatalf("err = %v, want ErrNeedsHuman", err)
			}
			if issuer.once != 1 || issuer.retrying != 0 {
				t.Errorf("once = %d, retrying = %d; want the no-redirect path", issuer.once, issuer.retrying)
			}
			if st := b.Snapshot().State; st != domain.StateCheckpointed && st != domain.StateLoggedOut {
				t.Errorf("state = %q, want it flipped off authenticated", st)
			}
		})
	}
}

// "x@nora" is an email-like token, not a mention: the byte BEFORE the "@"
// must not continue a username either.
func TestReadMentionsRequiresAWordBoundaryBeforeTheMention(t *testing.T) {
	raw := `{"new_stories":[{"story_type":3,"type":3,"args":{
		"text":"mail x@` + fakeig.SelfUsername + ` about it",
		"profile_id":"700000000001","profile_name":"someone",
		"timestamp":1700000600,
		"media":[{"id":"` + fakeig.ImageMediaID + `_10000001","shortcode":"` + fakeig.ImageShortcode + `"}],
		"comment_id":"17900000000000200"
	}}],"old_stories":[],"status":"ok"}`
	mentions, _, err := readMentions(t.Context(), &fakeRESTIssuer{body: []byte(raw)}, testSelf)
	if err != nil {
		t.Fatal(err)
	}
	if len(mentions) != 0 {
		t.Fatalf("mentions = %+v, want none", mentions)
	}
}

// A non-decimal id or a non-finite timestamp is not carried: the story is
// counted as undecodable, and the read goes on.
func TestReadMentionsRefusesAGarbageIDOrTimestamp(t *testing.T) {
	for name, args := range map[string]string{
		"exponent id":   `"profile_id":1.5e3,"timestamp":1700000600`,
		"negative id":   `"profile_id":-4,"timestamp":1700000600`,
		"text id":       `"profile_id":"abc","timestamp":1700000600`,
		"NaN time":      `"profile_id":"700000000001","timestamp":"NaN"`,
		"Inf time":      `"profile_id":"700000000001","timestamp":"Inf"`,
		"negative time": `"profile_id":"700000000001","timestamp":-1`,
	} {
		t.Run(name, func(t *testing.T) {
			raw := `{"new_stories":[{"story_type":3,"type":3,"args":{
				"text":"@` + fakeig.SelfUsername + ` is this true",` + args + `,
				"profile_name":"someone",
				"media":[{"id":"` + fakeig.ImageMediaID + `_10000001","shortcode":"` + fakeig.ImageShortcode + `"}],
				"comment_id":"17900000000000200"
			}}],"old_stories":[],"status":"ok"}`
			mentions, skipped, err := readMentions(t.Context(), &fakeRESTIssuer{body: []byte(raw)}, testSelf)
			if err != nil {
				t.Fatal(err)
			}
			if len(mentions) != 0 || skipped[undecodableStoryType] != 1 {
				t.Fatalf("mentions = %+v, skipped = %v; want none and one undecodable", mentions, skipped)
			}
		})
	}
}
