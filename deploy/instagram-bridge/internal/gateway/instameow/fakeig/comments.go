package fakeig

// The comment read and the comment write's upstream
// (gateway/instameow/comments.go), and a raw-REST fake that routes by method
// and URL, for tests that drive a whole request path.
//
// UNMEASURED. No live comment request or response has been recorded. Every
// body here is built from the HYPOTHESIS in comments.go's doc comment, not
// captured bytes; a live capture replaces them.

import (
	"context"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"

	"go.mau.fi/mautrix-meta/pkg/instameow/slidetypes"
	"go.mau.fi/mautrix-meta/pkg/messagix/httpclient"
	"go.mau.fi/mautrix-meta/pkg/messagix/types"
)

const (
	// CommentTopID/CommentReplyID are two synthetic comments on the carousel
	// post; CommentText carries a marker so a test can prove it stays out of
	// a span, a log line and an error.
	CommentTopID      = "17900000000000011"
	CommentReplyID    = "17900000000000012"
	CommentAuthorIGID = "500000000011"
	CommentAuthorName = "commenter_kappa"
	CommentText       = "is this true? words somebody wrote 5c7e"
	// NewCommentID is the id the write's fake answers with.
	NewCommentID = "18000000000000021"
)

// UnmeasuredCommentsJSON is a top-level page: one comment with string ids and
// a numeric time, one with NUMERIC ids, an id-less `pk`-less one (skipped),
// and one whose text is a number (undecodable, skipped).
const UnmeasuredCommentsJSON = `{
  "comments": [
    {"pk": "` + CommentTopID + `", "text": "` + CommentText + `", "created_at": 1700000500,
     "user": {"pk": "` + CommentAuthorIGID + `", "username": "` + CommentAuthorName + `"}},
    {"pk": 17900000000000013, "text": "second", "created_at": "1700000600",
     "user": {"pk_id": 500000000012, "username": "commenter_lambda"}},
    {"text": "no id at all", "user": {"pk": "500000000013", "username": "x"}},
    {"pk": "17900000000000014", "text": 12345, "user": {"pk": "500000000014", "username": "y"}}
  ],
  "has_more_comments": false,
  "status": "ok"
}`

// UnmeasuredChildCommentsJSON is one comment's replies. The first names its
// parent; the second leaves `parent_comment_id` out.
const UnmeasuredChildCommentsJSON = `{
  "child_comments": [
    {"pk": "` + CommentReplyID + `", "parent_comment_id": "` + CommentTopID + `", "text": "a reply",
     "created_at": 1700000700, "user": {"pk": "500000000015", "username": "replier_mu"}},
    {"id": "17900000000000016", "text": "another", "user": {"id": "500000000016", "username": "replier_nu"}}
  ],
  "status": "ok"
}`

// UnmeasuredCommentAddedJSON is the write's success.
const UnmeasuredCommentAddedJSON = `{"id": "` + NewCommentID + `", "text": "(echoed)", "status": "ok"}`

// RESTCall is one request REST received.
type RESTCall struct {
	Method      string
	URL         string
	Headers     http.Header
	Payload     []byte
	ContentType types.ContentType
	// Once is whether it came through MakeRequestOnceNoRedirect (no library retry).
	Once bool
}

// RESTAnswer is what REST says to one route: a status and a body, or a
// transport failure with no response at all.
type RESTAnswer struct {
	Status int
	Body   string
	Err    error
}

// REST is a RESTIssuer that answers by `METHOD url-prefix` and records every
// call. It answers the library's own (resp, body, error) shapes, as the
// gateway's fakeRESTIssuer does: a >=400 hands back the response AND its body
// with ErrUnexpectedError, a 429 no body and ErrRateLimited.
type REST struct {
	mu     sync.Mutex
	routes map[string]RESTAnswer
	calls  []RESTCall
}

// FreshNewsInboxJSON is NewsInboxJSON with the comment mention dated NOW.
// The fixture's own date is 2023, and the ledger does not record a mention
// the feed dates older than the 7-day window as a live summons.
func FreshNewsInboxJSON() string {
	return strings.Replace(NewsInboxJSON, `"timestamp": 1700000123`,
		`"timestamp": `+strconv.FormatInt(time.Now().Unix(), 10), 1)
}

// NewREST answers GET /mentions' feed (FreshNewsInboxJSON), a top-level
// comments page, a child comments page and a successful write.
func NewREST() *REST {
	return &REST{routes: map[string]RESTAnswer{
		"POST https://www.instagram.com/api/v1/news/inbox/":   {Body: FreshNewsInboxJSON()},
		"GET https://www.instagram.com/api/v1/media/":         {Body: UnmeasuredCommentsJSON},
		"GET child_comments":                                  {Body: UnmeasuredChildCommentsJSON},
		"POST https://www.instagram.com/api/v1/web/comments/": {Body: UnmeasuredCommentAddedJSON},
	}}
}

// Answer replaces one route's answer. The key is `METHOD url-prefix`, or
// "GET child_comments" for the replies read.
func (f *REST) Answer(key string, a RESTAnswer) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.routes[key] = a
}

// Calls is every request so far.
func (f *REST) Calls() []RESTCall {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]RESTCall(nil), f.calls...)
}

// Writes is how many comment POSTs reached it. The news-inbox read is a POST
// too (the web client's own method) and writes nothing, so it is not counted.
func (f *REST) Writes() int {
	n := 0
	for _, c := range f.Calls() {
		if c.Method == http.MethodPost && strings.HasPrefix(c.URL, commentWritePrefix) {
			n++
		}
	}
	return n
}

const commentWritePrefix = "https://www.instagram.com/api/v1/web/comments/"

func (f *REST) BuildHeaders(bool, bool) http.Header { return http.Header{} }

func (f *REST) WebSessionID() (string, error) { return "synthetic-web-session-id", nil }

// WebFormTokens answers synthetic values for all three, so a webForm POST
// goes out.
func (f *REST) WebFormTokens() (jazoest, fbDTSG, rolloutHash string, err error) {
	return "22222", "synthetic-fb-dtsg", "1000000000", nil
}

func (f *REST) MakeRequest(ctx context.Context, url, method string, h http.Header, p []byte, ct types.ContentType) (*http.Response, []byte, error) {
	return f.answer(url, method, h, p, ct, false)
}

func (f *REST) MakeRequestOnceNoRedirect(ctx context.Context, url, method string, h http.Header, p []byte, ct types.ContentType) (*http.Response, []byte, error) {
	return f.answer(url, method, h, p, ct, true)
}

func (f *REST) answer(url, method string, h http.Header, p []byte, ct types.ContentType, once bool) (*http.Response, []byte, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.calls = append(f.calls, RESTCall{Method: method, URL: url, Headers: h, Payload: p, ContentType: ct, Once: once})
	key := ""
	if method == http.MethodGet && strings.HasSuffix(url, "/child_comments/") {
		key = "GET child_comments"
	} else {
		for k := range f.routes {
			m, prefix, _ := strings.Cut(k, " ")
			if m == method && strings.HasPrefix(url, prefix) && len(k) > len(key) {
				key = k
			}
		}
	}
	a, ok := f.routes[key]
	if !ok {
		return &http.Response{StatusCode: http.StatusNotFound}, []byte(`{}`),
			fmt.Errorf("%w %d", httpclient.ErrUnexpectedError, http.StatusNotFound)
	}
	if a.Err != nil {
		return nil, nil, a.Err
	}
	status := a.Status
	if status == 0 {
		status = http.StatusOK
	}
	resp := &http.Response{StatusCode: status}
	switch {
	case status == http.StatusTooManyRequests:
		return resp, nil, httpclient.ErrRateLimited
	case status >= 400:
		return resp, []byte(a.Body), fmt.Errorf("%w %d", httpclient.ErrUnexpectedError, status)
	}
	return resp, []byte(a.Body), nil
}

// The summons intake's fixtures (design 19, "Intake, re-measured
// 2026-09-26"). A summons is a DM-thread card from the commenter
// (SummonsCard); the post's comments page then carries that commenter's
// comment mentioning the account (SummonsCommentsJSON), next to CommentTopID
// by somebody who never sent a card, mentioning the account too.
const (
	SummonsMessageID  = "mid.$summonscard0001"
	SummonsSenderFBID = "106115654119830"
	SummonsCardText   = "@" + SelfUsername + " is this true 3f9c"
)

// SummonsCommentsJSON is CarouselShortcode's first comments page with the
// summons comment on it.
const SummonsCommentsJSON = `{
  "comments": [
    {"pk": "` + MentionCommentID + `", "text": "` + SummonsCardText + `", "created_at": 1700000500,
     "user": {"pk": "` + MentionCommentAuthorIGID + `", "username": "` + MentionCommentAuthorUsername + `"}},
    {"pk": "` + CommentTopID + `", "text": "@` + SelfUsername + ` ` + CommentText + `", "created_at": 1700000600,
     "user": {"pk": "` + CommentAuthorIGID + `", "username": "` + CommentAuthorName + `"}}
  ],
  "status": "ok"
}`

// SummonsCard is the card as the live delta carries it (no target_url) when
// targetURL is "", and as the thread refetch returns it otherwise.
func SummonsCard(targetURL string) *slidetypes.Message {
	m := &slidetypes.Message{
		ThreadFBID:  ThreadShort,
		MessageID:   SummonsMessageID,
		SenderFBID:  106115654119830,
		ContentType: "MESSAGE_INLINE_SHARE",
		Content: slidetypes.MessageContentWrapper{Content: &slidetypes.MessageContentXMA{XMA: &slidetypes.XMAContent{
			Typename:     "SlideMessageLayeredXMA",
			XMATitle:     MentionCommentAuthorUsername,
			TitleText:    MentionCommentAuthorUsername,
			SubtitleText: SummonsCardText,
			TargetURL:    targetURL,
		}}},
		Sender: &slidetypes.MessageSender{ID: SummonsSenderFBID, IGID: MentionCommentAuthorIGID},
	}
	m.Sender.UserDict.Username = MentionCommentAuthorUsername
	return m
}
