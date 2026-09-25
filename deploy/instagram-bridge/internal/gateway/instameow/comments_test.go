package instameow

// The comment read and write against LITERAL bytes (fakeig/comments.go,
// UNMEASURED), and restDo's POST form against the GET form it generalizes.

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"testing"

	"instagram-bridge/internal/domain"
	"instagram-bridge/internal/gateway/instameow/fakeig"

	"go.mau.fi/mautrix-meta/pkg/messagix/httpclient"
	"go.mau.fi/mautrix-meta/pkg/messagix/types"
)

func mustCode(t *testing.T, s string) (domain.Shortcode, domain.MediaID) {
	t.Helper()
	c, err := domain.ParseShortcode(s)
	if err != nil {
		t.Fatal(err)
	}
	m, err := c.MediaID()
	if err != nil {
		t.Fatal(err)
	}
	return c, m
}

func mustComment(t *testing.T, s string) domain.CommentID {
	t.Helper()
	id, err := domain.ParseCommentID(s)
	if err != nil {
		t.Fatal(err)
	}
	return id
}

// webClientHeaders is the header set restDo sends on EVERY request, GET or
// POST: the set TestReadMentionsSendsTheWebClientsHeaders pins for the GET.
var webClientHeaders = []string{"x-requested-with", "referer", "sec-fetch-dest", "sec-fetch-mode", "sec-fetch-site", "X-Web-Session-ID"}

// restDo's POST: the form body, the library's own FORM content type, the SAME
// header set the GET sends plus the library's POST additions (origin,
// priority), and no library retry.
func TestRestPOSTSendsTheFormWithTheSameHeaders(t *testing.T) {
	get := &fakeRESTIssuer{body: []byte(`{"status":"ok"}`)}
	if _, _, err := rest(t.Context(), get, "https://www.instagram.com/api/v1/news/inbox/", "news_inbox"); err != nil {
		t.Fatal(err)
	}
	post := &fakeRESTIssuer{body: []byte(`{"status":"ok"}`)}
	form := url.Values{"comment_text": {"a b&c"}, "replied_to_comment_id": {"17"}}
	if _, _, err := restDo(t.Context(), post, restRequest{
		method: http.MethodPost, endpoint: "https://www.instagram.com/api/v1/web/comments/1/add/",
		label: "comment_add", form: form,
	}); err != nil {
		t.Fatal(err)
	}

	if post.method != http.MethodPost {
		t.Errorf("method = %q, want POST", post.method)
	}
	if post.contentType != types.FORM || string(post.contentType) != "application/x-www-form-urlencoded" {
		t.Errorf("content type = %q, want the library's types.FORM", post.contentType)
	}
	if got, _ := url.ParseQuery(string(post.payload)); got.Get("comment_text") != "a b&c" || got.Get("replied_to_comment_id") != "17" {
		t.Errorf("payload = %q, want the form, encoded", post.payload)
	}
	if post.buildHeadersArgs != [2]bool{true, false} {
		t.Errorf("BuildHeaders(%v), want (true, false)", post.buildHeadersArgs)
	}
	for _, h := range webClientHeaders {
		if post.headers.Get(h) == "" || post.headers.Get(h) != get.headers.Get(h) {
			t.Errorf("header %q: POST %q, GET %q; want the same, set", h, post.headers.Get(h), get.headers.Get(h))
		}
	}
	if post.headers.Get("origin") != "https://www.instagram.com" || post.headers.Get("priority") != "u=1, i" {
		t.Errorf("origin %q priority %q, want route.go's POST additions", post.headers.Get("origin"), post.headers.Get("priority"))
	}
	if get.headers.Get("origin") != "" || get.headers.Get("priority") != "" {
		t.Error("the GET grew POST-only headers: the existing GET must not change")
	}
	if post.once != 1 || post.retrying != 0 {
		t.Errorf("POST went through MakeRequestOnceNoRedirect %d, MakeRequest %d; want once only (no library retry)", post.once, post.retrying)
	}
	if get.retrying != 1 || get.once != 0 || get.method != http.MethodGet || get.payload != nil || get.contentType != types.NONE {
		t.Errorf("the GET path changed: retrying %d once %d method %q payload %q ct %q",
			get.retrying, get.once, get.method, get.payload, get.contentType)
	}
}

// The top-level page: tolerant ids and times, author fallbacks, and every
// undecodable or id-less comment counted rather than dropped or fatal.
func TestReadCommentsDecodesTolerantlyAndCountsTheRest(t *testing.T) {
	code, media := mustCode(t, fakeig.CarouselShortcode)
	issuer := &fakeRESTIssuer{body: []byte(fakeig.UnmeasuredCommentsJSON)}
	page, err := readComments(t.Context(), issuer, code, media, nil)
	if err != nil {
		t.Fatal(err)
	}
	if want := "https://www.instagram.com/api/v1/media/" + fakeig.CarouselMediaID + "/comments/?can_support_threading=true&permalink_enabled=false"; issuer.url != want {
		t.Errorf("url = %q, want %q", issuer.url, want)
	}
	if issuer.headers.Get("referer") != "https://www.instagram.com/p/"+fakeig.CarouselShortcode+"/" {
		t.Errorf("referer = %q, want the post page", issuer.headers.Get("referer"))
	}
	if issuer.once != 1 || issuer.retrying != 0 {
		t.Error("a read whose URL carries ids went through the retrying, URL-logging MakeRequest")
	}
	if len(page.Items) != 2 || page.Skipped != 2 {
		t.Fatalf("items %d skipped %d, want 2 and 2: %+v", len(page.Items), page.Skipped, page)
	}
	first := page.Items[0]
	if first.ID != fakeig.CommentTopID || first.AuthorIGID != fakeig.CommentAuthorIGID ||
		first.AuthorUsername != fakeig.CommentAuthorName || first.Text != fakeig.CommentText ||
		first.At != 1700000500 || first.ParentID != "" {
		t.Errorf("first = %+v", first)
	}
	second := page.Items[1]
	if second.ID != "17900000000000013" || second.AuthorIGID != "500000000012" || second.At != 1700000600 {
		t.Errorf("second (numeric ids, string time, pk_id author) = %+v", second)
	}
}

// One comment's replies: the child URL, and a reply that left its parent out
// still names the parent it was read under.
func TestReadCommentsReadsOneCommentsReplies(t *testing.T) {
	code, media := mustCode(t, fakeig.CarouselShortcode)
	parent := mustComment(t, fakeig.CommentTopID)
	issuer := &fakeRESTIssuer{body: []byte(fakeig.UnmeasuredChildCommentsJSON)}
	page, err := readComments(t.Context(), issuer, code, media, &parent)
	if err != nil {
		t.Fatal(err)
	}
	if want := "https://www.instagram.com/api/v1/media/" + fakeig.CarouselMediaID + "/comments/" + fakeig.CommentTopID + "/child_comments/"; issuer.url != want {
		t.Errorf("url = %q, want %q", issuer.url, want)
	}
	if len(page.Items) != 2 || page.Skipped != 0 {
		t.Fatalf("page = %+v", page)
	}
	for _, c := range page.Items {
		if c.ParentID != fakeig.CommentTopID {
			t.Errorf("reply %s parent = %q, want %s", c.ID, c.ParentID, fakeig.CommentTopID)
		}
	}
	if page.Items[1].ID != "17900000000000016" || page.Items[1].AuthorIGID != "500000000016" || page.Items[1].At != 0 {
		t.Errorf("second reply (id, user.id, no time) = %+v", page.Items[1])
	}
}

// The write: which outcomes are DEFINITE refusals (ErrNotPosted: the reply
// may be marked failed) and which are ambiguous (the reply stays claimed).
func TestPostCommentTellsARefusalFromAnAmbiguousFailure(t *testing.T) {
	code, media := mustCode(t, fakeig.CarouselShortcode)
	to := mustComment(t, fakeig.MentionCommentID)
	for _, tc := range []struct {
		name       string
		issuer     *fakeRESTIssuer
		notPosted  bool
		kind       error
		wantPosted bool
	}{
		{"success", &fakeRESTIssuer{body: []byte(fakeig.UnmeasuredCommentAddedJSON)}, false, nil, true},
		{"400", &fakeRESTIssuer{status: 400, body: []byte(`{"message":"invalid comment","status":"fail"}`)}, true, domain.ErrUpstreamRejected, false},
		{"404", &fakeRESTIssuer{status: 404, body: []byte(`{}`)}, true, domain.ErrNotFound, false},
		{"200 status fail", &fakeRESTIssuer{body: []byte(`{"status":"fail","message":"no"}`)}, true, domain.ErrUpstreamRejected, false},
		{"500", &fakeRESTIssuer{status: 500, body: []byte(`{}`)}, false, domain.ErrUpstreamRejected, false},
		{"transport", &fakeRESTIssuer{err: context.DeadlineExceeded}, false, nil, false},
		{"200 not JSON", &fakeRESTIssuer{body: []byte(`<html>`)}, false, domain.ErrUpstreamRejected, false},
		{"200 ok without an id", &fakeRESTIssuer{body: []byte(`{"status":"ok"}`)}, false, domain.ErrUpstreamRejected, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			posted, err := postComment(t.Context(), tc.issuer, code, media, to, "hi")
			if tc.wantPosted {
				if err != nil || posted.ID != fakeig.NewCommentID || posted.ReplyToCommentID != fakeig.MentionCommentID {
					t.Fatalf("posted %+v err %v", posted, err)
				}
			} else if err == nil {
				t.Fatal("want an error")
			}
			if got := errors.Is(err, domain.ErrNotPosted); got != tc.notPosted {
				t.Errorf("ErrNotPosted = %v, want %v (err %v)", got, tc.notPosted, err)
			}
			if tc.kind != nil && domain.KindOf(err) != tc.kind {
				t.Errorf("kind = %v, want %v", domain.KindOf(err), tc.kind)
			}
			if tc.issuer.calls() != 1 || tc.issuer.once != 1 {
				t.Errorf("%d call(s), %d through MakeRequestOnce: want exactly one, never retried", tc.issuer.calls(), tc.issuer.once)
			}
			if want := "https://www.instagram.com/api/v1/web/comments/" + fakeig.CarouselMediaID + "/add/"; tc.issuer.url != want {
				t.Errorf("url = %q, want %q", tc.issuer.url, want)
			}
		})
	}
}

// Instagram's action-block signal on a write is domain.ActionBlocked with a
// fixed reason (and ErrNotPosted); an ordinary refusal is not.
func TestPostCommentRecognizesAnActionBlock(t *testing.T) {
	code, media := mustCode(t, fakeig.CarouselShortcode)
	to := mustComment(t, fakeig.MentionCommentID)
	for name, tc := range map[string]struct {
		issuer *fakeRESTIssuer
		reason string
	}{
		"feedback_required 400": {&fakeRESTIssuer{status: 400, body: []byte(`{"message":"feedback_required","spam":true,"feedback_title":"Action Blocked","status":"fail"}`)}, domain.BlockFeedbackRequired},
		"feedback_required 200": {&fakeRESTIssuer{body: []byte(`{"message":"feedback_required","status":"fail"}`)}, domain.BlockFeedbackRequired},
		"429":                   {&fakeRESTIssuer{status: 429}, domain.BlockRateLimited},
		"spam":                  {&fakeRESTIssuer{status: 400, body: []byte(`{"message":"x","spam":true,"status":"fail"}`)}, domain.BlockSpam},
		"sentry_block":          {&fakeRESTIssuer{status: 400, body: []byte(`{"message":"x","feedback_url":"repute/report_problem/scraping/sentry_block/","status":"fail"}`)}, domain.BlockSentry},
		"ordinary 400":          {&fakeRESTIssuer{status: 400, body: []byte(`{"message":"x","status":"fail"}`)}, ""},
		"500 with the words":    {&fakeRESTIssuer{status: 500, body: []byte(`{"message":"feedback_required"}`)}, ""},
	} {
		t.Run(name, func(t *testing.T) {
			_, err := postComment(t.Context(), tc.issuer, code, media, to, "hi")
			var blocked *domain.ActionBlocked
			got := ""
			if errors.As(err, &blocked) {
				got = blocked.Reason
				if !errors.Is(err, domain.ErrNotPosted) {
					t.Error("an action block is not ErrNotPosted")
				}
			}
			if got != tc.reason {
				t.Errorf("block reason = %q, want %q (err %v)", got, tc.reason, err)
			}
		})
	}
}

// The comment routes follow no redirect and read the Location themselves:
// /accounts/login/ is a dead session (which the library's own redirect check
// misses), challenge and checkpoint paths need a human, and a deleted
// session cookie is a dead token. Each flips the session; an unknown
// redirect is refused without flipping it. The Location is never quoted.
func TestCommentRoutesClassifyARedirectThemselves(t *testing.T) {
	code, media := mustCode(t, fakeig.CarouselShortcode)
	to := mustComment(t, fakeig.MentionCommentID)
	next := "?next=/api/v1/web/comments/" + fakeig.CarouselMediaID + "/add/"
	for name, tc := range map[string]struct {
		header http.Header
		state  domain.SessionState
	}{
		"login":            {http.Header{"Location": {"https://www.instagram.com/accounts/login/" + next}}, domain.StateLoggedOut},
		"relative login":   {http.Header{"Location": {"/accounts/login/" + next}}, domain.StateLoggedOut},
		"challenge":        {http.Header{"Location": {"https://www.instagram.com/challenge/abc/" + next}}, domain.StateCheckpointed},
		"checkpoint":       {http.Header{"Location": {"https://www.instagram.com/checkpoint/1/" + next}}, domain.StateCheckpointed},
		"cookie deleted":   {http.Header{"Location": {"/"}, "Set-Cookie": {"sessionid=; Max-Age=0; Path=/"}}, domain.StateLoggedOut},
		"unknown redirect": {http.Header{"Location": {"https://www.instagram.com/somewhere/" + next}}, domain.StateAuthenticated},
	} {
		t.Run(name, func(t *testing.T) {
			issuer := &fakeRESTIssuer{status: http.StatusFound, respHeader: tc.header}
			b := mentionsTestBridge(t, issuer)
			_, err := b.PostComment(t.Context(), code, media, to, "hi")
			if err == nil {
				t.Fatal("a redirect read as success")
			}
			if tc.state != domain.StateAuthenticated && !errors.Is(err, domain.ErrNeedsHuman) {
				t.Errorf("err = %v, want needs human", err)
			}
			if tc.state == domain.StateAuthenticated && !errors.Is(err, domain.ErrUpstreamRejected) {
				t.Errorf("err = %v, want upstream rejected", err)
			}
			if !errors.Is(err, domain.ErrNotPosted) {
				t.Errorf("a refused redirect is not ErrNotPosted: %v", err)
			}
			if got := b.Snapshot().State; got != tc.state {
				t.Errorf("state = %q, want %q", got, tc.state)
			}
			checkNoSecret(t, "error text", err.Error(), []string{fakeig.CarouselMediaID, "next="})
			if issuer.retrying != 0 || issuer.once != 1 {
				t.Errorf("went through MakeRequest %d, no-redirect %d; want the no-redirect variant once", issuer.retrying, issuer.once)
			}
		})
	}
}

// The redirect-FOLLOWING client's real checkpoint shape (http.go:517-520): a
// 302 response, no body, and the library's RedirectedError. On GET /mentions
// (which still follows redirects) it needs a human and flips the session; on
// a comment route, should that shape ever arrive, the same, with a fixed
// text even for the RedirectedError kinds whose text quotes the URL.
func TestTheRealRedirectedErrorShape(t *testing.T) {
	code, media := mustCode(t, fakeig.CarouselShortcode)
	secretURL := "https://www.instagram.com/consent/?next=/api/v1/media/" + fakeig.CarouselMediaID + "/comments/"
	for name, rerr := range map[string]error{
		"checkpoint": httpclient.RedirectedError{Type: httpclient.ErrCheckpointRequired, URL: "https://www.instagram.com/checkpoint/1/"},
		"challenge":  httpclient.RedirectedError{Type: httpclient.ErrChallengeRequired, URL: "https://www.instagram.com/challenge/1/"},
		"consent":    httpclient.RedirectedError{Type: httpclient.ErrConsentRequired, URL: secretURL},
	} {
		t.Run("mentions "+name, func(t *testing.T) {
			b := mentionsTestBridge(t, &fakeRESTIssuer{status: http.StatusFound, err: rerr})
			if _, _, err := b.Mentions(t.Context()); !errors.Is(err, domain.ErrNeedsHuman) {
				t.Fatalf("err = %v, want needs human", err)
			}
			if b.Snapshot().State != domain.StateCheckpointed {
				t.Errorf("state = %q, want checkpointed", b.Snapshot().State)
			}
		})
		t.Run("comments "+name, func(t *testing.T) {
			b := mentionsTestBridge(t, &fakeRESTIssuer{status: http.StatusFound, err: rerr})
			_, err := b.Comments(t.Context(), code, media, nil)
			if !errors.Is(err, domain.ErrNeedsHuman) {
				t.Fatalf("err = %v, want needs human", err)
			}
			checkNoSecret(t, "error text", err.Error(), []string{fakeig.CarouselMediaID})
			if b.Snapshot().State != domain.StateCheckpointed {
				t.Errorf("state = %q, want checkpointed", b.Snapshot().State)
			}
		})
	}
}

// The referer is built from the canonical shortcode of the decoded media id,
// not the caller's spelling: a leading `A` is a zero digit, decodes to the
// same post, and is not a page the web client is ever on.
func TestTheRefererIsTheCanonicalShortcode(t *testing.T) {
	m, _ := domain.MediaIDOf(123456789)
	canonical := m.Shortcode().String()
	code, media := mustCode(t, "AA"+canonical)
	if media != m {
		t.Fatalf("precondition: %q decodes to %v, want %v", "AA"+canonical, media, m)
	}
	issuer := &fakeRESTIssuer{body: []byte(fakeig.UnmeasuredCommentAddedJSON)}
	if _, err := postComment(t.Context(), issuer, code, media, mustComment(t, "5"), "hi"); err != nil {
		t.Fatal(err)
	}
	if got, want := issuer.headers.Get("referer"), "https://www.instagram.com/p/"+canonical+"/"; got != want {
		t.Errorf("referer = %q, want %q", got, want)
	}
}

// A transport failure's text is Go's *url.Error, which quotes the URL: on a
// comment route that URL carries the media pk and a comment id, and it must
// not reach Error(), a span, or the envelope.
func TestCommentCallsNeverLeakAnIdentifierOrText(t *testing.T) {
	rec := recordGatewaySpans(t)
	code, media := mustCode(t, fakeig.CarouselShortcode)
	to := mustComment(t, fakeig.MentionCommentID)
	target := "https://www.instagram.com/api/v1/web/comments/" + fakeig.CarouselMediaID + "/add/"
	urlErr := fmt.Errorf("%w: %w", httpclient.ErrRequestFailed,
		&url.Error{Op: "Post", URL: target + "?replied_to_comment_id=" + fakeig.MentionCommentID, Err: context.DeadlineExceeded})

	secrets := []string{fakeig.CarouselMediaID, fakeig.MentionCommentID, fakeig.CarouselShortcode, fakeig.CommentText,
		fakeig.CommentAuthorIGID, fakeig.CommentAuthorName}
	var texts []string

	_, err := postComment(t.Context(), &fakeRESTIssuer{err: urlErr}, code, media, to, fakeig.CommentText)
	if err == nil || !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("err = %v, want the timeout still visible to errors.Is", err)
	}
	texts = append(texts, err.Error())
	_, err = postComment(t.Context(), &fakeRESTIssuer{status: 400, body: []byte(`{"message":"x"}`)}, code, media, to, fakeig.CommentText)
	texts = append(texts, err.Error())
	_, err = postComment(t.Context(), &fakeRESTIssuer{body: []byte("not json " + fakeig.CommentText)}, code, media, to, fakeig.CommentText)
	texts = append(texts, err.Error())
	_, err = readComments(t.Context(), &fakeRESTIssuer{err: urlErr}, code, media, &to)
	texts = append(texts, err.Error())
	if _, err := readComments(t.Context(), &fakeRESTIssuer{body: []byte(fakeig.UnmeasuredCommentsJSON)}, code, media, nil); err != nil {
		t.Fatal(err)
	}

	for _, text := range texts {
		checkNoSecret(t, "error text", text, secrets)
	}
	for _, s := range rec.Ended() {
		checkNoSecret(t, "span name", s.Name(), secrets)
		for _, kv := range s.Attributes() {
			checkNoSecret(t, "span "+s.Name()+" attribute "+string(kv.Key), kv.Value.Emit(), secrets)
		}
		for _, ev := range s.Events() {
			for _, kv := range ev.Attributes {
				checkNoSecret(t, "span "+s.Name()+" event "+ev.Name+" attribute "+string(kv.Key), kv.Value.Emit(), secrets)
			}
		}
		checkNoSecret(t, "span "+s.Name()+" status", s.Status().Description, secrets)
	}
}

// A checkpoint on the WRITE flips the session, exactly as on /mentions, and
// so does one on the comment read.
func TestACheckpointOnACommentCallFlipsTheSession(t *testing.T) {
	code, media := mustCode(t, fakeig.CarouselShortcode)
	to := mustComment(t, fakeig.MentionCommentID)
	for name, call := range map[string]func(b *Bridge) error{
		"write": func(b *Bridge) error {
			_, err := b.PostComment(t.Context(), code, media, to, "hi")
			return err
		},
		"read": func(b *Bridge) error {
			_, err := b.Comments(t.Context(), code, media, nil)
			return err
		},
	} {
		for shape, issuer := range map[string]*fakeRESTIssuer{
			"4xx body":   {status: 400, body: []byte(`{"message":"checkpoint_required","checkpoint_url":"https://i.instagram.com/challenge/x"}`)},
			"200 fail":   {body: []byte(`{"status":"fail","message":"challenge_required"}`)},
			"a redirect": {err: httpclient.ErrCheckpointRequired},
			"dead token": {status: 403, body: []byte(`{"message":"login_required"}`)},
		} {
			t.Run(name+" "+shape, func(t *testing.T) {
				b := mentionsTestBridge(t, issuer)
				if err := call(b); !errors.Is(err, domain.ErrNeedsHuman) {
					t.Fatalf("err = %v, want needs human", err)
				}
				if st := b.Snapshot().State; st == domain.StateAuthenticated {
					t.Errorf("state = %q after a checkpoint on the %s", st, name)
				}
			})
		}
	}
	// An ordinary refusal does not.
	b := mentionsTestBridge(t, &fakeRESTIssuer{status: 400, body: []byte(`{"message":"feedback_required"}`)})
	if _, err := b.PostComment(t.Context(), code, media, to, "hi"); err == nil {
		t.Fatal("want an error")
	}
	if st := b.Snapshot().State; st != domain.StateAuthenticated {
		t.Errorf("state = %q after an ordinary refusal, want authenticated", st)
	}
}

// The write is detached from the caller hanging up: a request context that
// is already cancelled still reaches the issuer with a live context.
func TestTheWriteIsNotCancelledByTheCaller(t *testing.T) {
	code, media := mustCode(t, fakeig.CarouselShortcode)
	to := mustComment(t, fakeig.MentionCommentID)
	issuer := &ctxRecordingIssuer{fakeRESTIssuer: &fakeRESTIssuer{body: []byte(fakeig.UnmeasuredCommentAddedJSON)}}
	b := NewBridge(Options{State: domain.StateAuthenticated, RESTIssuer: issuer})
	ctx, cancel := context.WithCancel(t.Context())
	cancel()
	if _, err := b.PostComment(ctx, code, media, to, "hi"); err != nil {
		t.Fatal(err)
	}
	if issuer.ctxErr != nil {
		t.Errorf("the write ran on a cancelled context (%v): a caller hanging up would make it ambiguous", issuer.ctxErr)
	}
	if _, ok := issuer.deadline(); !ok {
		t.Error("the detached write has no deadline")
	}
}

type ctxRecordingIssuer struct {
	*fakeRESTIssuer
	ctxErr error
	ctx    context.Context
}

func (c *ctxRecordingIssuer) MakeRequestOnceNoRedirect(ctx context.Context, u, m string, h http.Header, p []byte, ct types.ContentType) (*http.Response, []byte, error) {
	c.ctxErr, c.ctx = ctx.Err(), ctx
	return c.fakeRESTIssuer.MakeRequestOnceNoRedirect(ctx, u, m, h, p, ct)
}

func (c *ctxRecordingIssuer) deadline() (any, bool) {
	d, ok := c.ctx.Deadline()
	return d, ok
}
