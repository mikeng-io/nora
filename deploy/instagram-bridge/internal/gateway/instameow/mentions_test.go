package instameow

// readMentions against LITERAL bytes (fakeig/mentions.go), decoded exactly as
// the raw REST path decodes them. The bytes are UNMEASURED: built from the
// task's own hypothesis, not captured. See mentions.go's package doc comment.

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
}

func (f *fakeRESTIssuer) BuildHeaders(withCookies, isSecFetchDocument bool) http.Header {
	f.buildHeadersArgs = [2]bool{withCookies, isSecFetchDocument}
	return http.Header{}
}

func (f *fakeRESTIssuer) WebSessionID() (string, error) {
	if f.session == "" {
		return "synthetic-web-session-id", nil
	}
	return f.session, nil
}

func (f *fakeRESTIssuer) MakeRequest(
	_ context.Context, _ string, _ string, headers http.Header, _ []byte, _ types.ContentType,
) (*http.Response, []byte, error) {
	f.headers = headers
	if f.err != nil {
		return nil, nil, f.err
	}
	status := f.status
	if status == 0 {
		status = http.StatusOK
	}
	resp := &http.Response{StatusCode: status}
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
	issuer := &fakeRESTIssuer{body: []byte(fakeig.UnmeasuredNewsInboxJSON)}
	mentions, skipped, err := readMentions(t.Context(), issuer, fakeig.SelfUsername)
	if err != nil {
		t.Fatal(err)
	}
	if len(mentions) != 1 {
		t.Fatalf("mentions = %d, want 1: %+v", len(mentions), mentions)
	}
	m := mentions[0]
	if m.StoryType != 3 {
		t.Errorf("story_type = %d, want 3", m.StoryType)
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

	// The three non-mentions, counted by story_type: like (1), follow (2),
	// caption-mention with no comment id (4).
	want := map[int]int{1: 1, 2: 1, 4: 1}
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
	issuer := &fakeRESTIssuer{body: []byte(fakeig.UnmeasuredNewsInboxNoShortcodeJSON)}
	mentions, _, err := readMentions(t.Context(), issuer, fakeig.SelfUsername)
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
	mentions, _, err := readMentions(t.Context(), &fakeRESTIssuer{body: []byte(raw)}, fakeig.SelfUsername)
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
		&fakeRESTIssuer{body: []byte(fakeig.UnmeasuredNewsInboxJSON)}, fakeig.SelfUsername)
	if err != nil {
		t.Fatal(err)
	}
	for _, m := range mentions {
		if m.StoryType == 4 {
			t.Fatalf("the caption-mention (story_type 4, no comment id) was classified as a comment mention: %+v", m)
		}
	}
	if skipped[4] != 1 {
		t.Errorf("skipped[4] = %d, want 1", skipped[4])
	}
}

// An empty selfUsername classifies nothing: fail closed rather than guess
// who "the account" is.
func TestReadMentionsWithNoSelfUsernameClassifiesNothing(t *testing.T) {
	mentions, skipped, err := readMentions(t.Context(),
		&fakeRESTIssuer{body: []byte(fakeig.UnmeasuredNewsInboxJSON)}, "")
	if err != nil {
		t.Fatal(err)
	}
	if len(mentions) != 0 {
		t.Fatalf("mentions = %+v, want none with no self username", mentions)
	}
	if skipped[3] != 1 {
		t.Errorf("skipped[3] = %d, want 1 (the story that would otherwise have been the mention)", skipped[3])
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
	mentions, skipped, err := readMentions(t.Context(), &fakeRESTIssuer{body: []byte(raw)}, fakeig.SelfUsername)
	if err != nil {
		t.Fatal(err)
	}
	if len(mentions) != 0 {
		t.Fatalf("mentions = %+v, want none: \"@%s_fan\" is not a mention of %q", mentions, fakeig.SelfUsername, fakeig.SelfUsername)
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
	mentions, _, err := readMentions(t.Context(), &fakeRESTIssuer{body: []byte(raw)}, fakeig.SelfUsername)
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
	mentions, _, err := readMentions(t.Context(), issuer, fakeig.SelfUsername)
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
	mentions, skipped, err := readMentions(t.Context(), issuer, fakeig.SelfUsername)
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
	_, _, err := readMentions(t.Context(), &fakeRESTIssuer{body: []byte(fakeig.UnmeasuredNewsInboxFailJSON)}, fakeig.SelfUsername)
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
			_, _, err := readMentions(t.Context(), tc.issuer, fakeig.SelfUsername)
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
			_, _, err := readMentions(t.Context(), issuer, fakeig.SelfUsername)
			if !errors.Is(err, domain.ErrNeedsHuman) {
				t.Errorf("err = %v, want ErrNeedsHuman", err)
			}
		})
	}
	// login_required is the dead-token shape.
	issuer := &fakeRESTIssuer{status: http.StatusBadRequest, body: []byte(`{"message":"login_required"}`)}
	_, _, err := readMentions(t.Context(), issuer, fakeig.SelfUsername)
	if !errors.Is(err, httpclient.ErrTokenInvalidated) {
		t.Errorf("err = %v, want it to wrap ErrTokenInvalidated", err)
	}
}

// The headers a probe sends must be the SAME header set the library's own
// REST callers send (call.go's `rest` doc comment): BuildHeaders(true,
// false) plus x-requested-with, a bare base_url referer (no trailing
// slash — FetchReel's own form) and the sec-fetch-* triple — never a
// different header set that could read as a new client.
func TestReadMentionsSendsTheWebClientsHeaders(t *testing.T) {
	issuer := &fakeRESTIssuer{body: []byte(fakeig.UnmeasuredNewsInboxJSON)}
	if _, _, err := readMentions(t.Context(), issuer, fakeig.SelfUsername); err != nil {
		t.Fatal(err)
	}
	if issuer.buildHeadersArgs != [2]bool{true, false} {
		t.Errorf("BuildHeaders called with %v, want (true, false)", issuer.buildHeadersArgs)
	}
	for _, h := range []string{"x-requested-with", "referer", "sec-fetch-dest", "sec-fetch-mode", "sec-fetch-site", "X-Web-Session-ID"} {
		if issuer.headers.Get(h) == "" {
			t.Errorf("header %q was not set", h)
		}
	}
	if got := issuer.headers.Get("x-requested-with"); got != "XMLHttpRequest" {
		t.Errorf("x-requested-with = %q, want XMLHttpRequest", got)
	}
	if got := issuer.headers.Get("referer"); got != "https://www.instagram.com" {
		t.Errorf("referer = %q, want the bare base_url with NO trailing slash", got)
	}
}

// No web session id, no request: a request missing a header the real web
// client always sends is the mismatch `rest` exists to avoid.
func TestReadMentionsSendsNoRequestWithNoWebSessionID(t *testing.T) {
	inner := &fakeRESTIssuer{body: []byte(fakeig.UnmeasuredNewsInboxJSON)}
	failing := &failingWebSessionIssuer{fakeRESTIssuer: inner}
	_, _, err := readMentions(t.Context(), failing, fakeig.SelfUsername)
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
	mentions, _, err := readMentions(t.Context(), &fakeRESTIssuer{body: []byte(fakeig.UnmeasuredNewsInboxJSON)}, fakeig.SelfUsername)
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

	_, _, err2 := readMentions(t.Context(), &fakeRESTIssuer{status: http.StatusBadRequest, body: []byte(`{}`)}, fakeig.SelfUsername)
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
	mentions, _, err := readMentions(t.Context(), &fakeRESTIssuer{body: []byte(raw)}, fakeig.SelfUsername)
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
			mentions, skipped, err := readMentions(t.Context(), &fakeRESTIssuer{body: []byte(raw)}, fakeig.SelfUsername)
			if err != nil {
				t.Fatal(err)
			}
			if len(mentions) != 0 || skipped[undecodableStoryType] != 1 {
				t.Fatalf("mentions = %+v, skipped = %v; want none and one undecodable", mentions, skipped)
			}
		})
	}
}
