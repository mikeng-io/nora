package instameow

// readMentions against LITERAL bytes (fakeig/mentions.go), decoded exactly as
// the raw REST path decodes them. The bytes are UNMEASURED: built from the
// task's own hypothesis, not captured. See mentions.go's package doc comment.

import (
	"context"
	"errors"
	"net/http"
	"strings"
	"testing"

	"instagram-bridge/internal/domain"
	"instagram-bridge/internal/gateway/instameow/fakeig"

	"go.mau.fi/mautrix-meta/pkg/messagix/httpclient"
	"go.mau.fi/mautrix-meta/pkg/messagix/types"
	"go.opentelemetry.io/otel/codes"
)

// fakeRESTIssuer records the headers it was asked to send and answers with
// canned bytes — the seam that stands in for `*httpclient.HTTPClient`, the
// same role fakeGraphQLIssuer plays for the raw GraphQL path
// (social_profile_test.go).
type fakeRESTIssuer struct {
	status  int
	body    []byte
	err     error
	headers http.Header
	// noSessionID makes WebSessionID fail, to prove rest() then sends nothing.
	noSessionID bool
	calls       int
}

func (f *fakeRESTIssuer) BuildHeaders(bool, bool) http.Header { return http.Header{} }

// fakeWebSessionID is what the fake reports unless noSessionID is set.
const fakeWebSessionID = "fake:web:session"

func (f *fakeRESTIssuer) WebSessionID() (string, error) {
	if f.noSessionID {
		return "", errors.New("no web session id")
	}
	return fakeWebSessionID, nil
}

func (f *fakeRESTIssuer) MakeRequest(
	_ context.Context, _ string, _ string, headers http.Header, _ []byte, _ types.ContentType,
) (*http.Response, []byte, error) {
	f.headers = headers
	f.calls++
	if f.err != nil {
		return nil, nil, f.err
	}
	status := f.status
	if status == 0 {
		status = http.StatusOK
	}
	return &http.Response{StatusCode: status}, f.body, nil
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
	if m.CommentText != fakeig.MentionCommentText {
		t.Errorf("comment text = %q, want the fixture's text", m.CommentText)
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

// Non-2xx statuses and a transport-level checkpoint classify onto the closed
// set, with FIXED error text: no id ever reaches Error().
func TestReadMentionsClassifiesUpstreamFailures(t *testing.T) {
	for _, tc := range []struct {
		name    string
		issuer  *fakeRESTIssuer
		kind    error
		wantMsg string
	}{
		{"429", &fakeRESTIssuer{status: http.StatusTooManyRequests, body: []byte(`{}`)},
			domain.ErrUpstreamRejected, "news_inbox returned a non-200: 429"},
		{"checkpoint", &fakeRESTIssuer{err: httpclient.ErrCheckpointRequired}, domain.ErrNeedsHuman, ""},
		{"not json", &fakeRESTIssuer{body: []byte("<html>challenge</html>")},
			domain.ErrUpstreamRejected, ""},
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

// The headers a probe sends must be the SAME shape the library's own REST
// callers send (call.go's `rest` doc comment): x-requested-with, referer and
// the sec-fetch-* triple on top of BuildHeaders — never a different header
// set that could read as a new client.
func TestReadMentionsSendsTheWebClientsHeaders(t *testing.T) {
	issuer := &fakeRESTIssuer{body: []byte(fakeig.UnmeasuredNewsInboxJSON)}
	if _, _, err := readMentions(t.Context(), issuer, fakeig.SelfUsername); err != nil {
		t.Fatal(err)
	}
	for _, h := range []string{"x-requested-with", "referer", "sec-fetch-dest", "sec-fetch-mode", "sec-fetch-site"} {
		if issuer.headers.Get(h) == "" {
			t.Errorf("header %q was not set", h)
		}
	}
	if got := issuer.headers.Get("x-requested-with"); got != "XMLHttpRequest" {
		t.Errorf("x-requested-with = %q, want XMLHttpRequest", got)
	}
}

// R-18: no igid, username, media id, comment id or comment text may reach a
// span attribute/event or an error string.
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
		fakeig.MentionCommentID, fakeig.MentionCommentText,
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

// The web client always sends X-Web-Session-ID. rest() sends the session's
// own value, and without one it sends NOTHING rather than a request that
// looks different from the web client (the checkpoint risk design 19 names).
func TestRestSendsTheWebSessionIDAndRefusesWithoutOne(t *testing.T) {
	issuer := &fakeRESTIssuer{body: []byte(fakeig.UnmeasuredNewsInboxJSON)}
	if _, _, err := readMentions(t.Context(), issuer, fakeig.SelfUsername); err != nil {
		t.Fatal(err)
	}
	if got := issuer.headers.Get("X-Web-Session-ID"); got != fakeWebSessionID {
		t.Fatalf("X-Web-Session-ID = %q, want %q", got, fakeWebSessionID)
	}

	bare := &fakeRESTIssuer{body: []byte(fakeig.UnmeasuredNewsInboxJSON), noSessionID: true}
	if _, _, err := readMentions(t.Context(), bare, fakeig.SelfUsername); err == nil {
		t.Fatal("readMentions succeeded without a web session id")
	}
	if bare.calls != 0 {
		t.Fatalf("MakeRequest called %d times without a web session id, want 0", bare.calls)
	}
}
