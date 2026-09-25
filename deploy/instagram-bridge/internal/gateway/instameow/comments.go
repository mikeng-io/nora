package instameow

// A post's comments, read and (once per summons) written
// (design/instagram-adoption/19, phases 2 and 4).
//
// # What the upstream calls really are
//
// All three are HYPOTHESES. None has been measured against Instagram: no live
// request or response was captured, and design 19 requires the web client's
// own request to be captured from a real browser before the first live call.
// The library has none of them in its endpoint table
// (pkg/messagix/endpoints/instagram.go), so each is written out in full:
//
//   - top-level comments:
//     `GET https://www.instagram.com/api/v1/media/{media_pk}/comments/?can_support_threading=true&permalink_enabled=false`,
//     answering `{"comments": [...], "status": "ok"}` (HYPOTHESIS);
//   - one comment's replies:
//     `GET https://www.instagram.com/api/v1/media/{media_pk}/comments/{comment_id}/child_comments/`,
//     answering `{"child_comments": [...], "status": "ok"}` (HYPOTHESIS);
//   - the reply:
//     `POST https://www.instagram.com/api/v1/web/comments/{media_pk}/add/`,
//     form `comment_text` and `replied_to_comment_id`, answering
//     `{"id": "...", "status": "ok"}` (HYPOTHESIS).
//
// A comment is decoded TOLERANTLY, the way mentions.go decodes a story: each
// on its own from `[]json.RawMessage`, its id from `pk` or `id`, its author's
// igid from `user.pk`, `user.pk_id` or `user.id`, every id a JSON string or
// number (flexID), `created_at` a number or a numeric string (flexTimestamp).
// A comment that does not decode, or has no usable id, is COUNTED in
// CommentPage.Skipped and never fails the page.
//
// # What never crosses into a span or an error
//
// The span is restDo's own `instagram.rest <label>` with no attribute this
// file adds. Every URL here carries a media pk and possibly a comment id, so
// every request is `idsInURL`: it goes out through MakeRequestOnceNoRedirect
// (no retry; no redirect followed, so the library's checkHTTPRedirect, which
// logs a redirect's URLs, never runs; see RESTIssuer's doc comment), and a
// transport failure's text is replaced with a fixed one.
// Every error this file raises has a FIXED text: no id and no comment text
// ever reaches Error().

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"time"

	"instagram-bridge/internal/domain"
)

const (
	commentsEndpoint      = "https://www.instagram.com/api/v1/media/%s/comments/?can_support_threading=true&permalink_enabled=false"
	childCommentsEndpoint = "https://www.instagram.com/api/v1/media/%s/comments/%s/child_comments/"
	commentAddEndpoint    = "https://www.instagram.com/api/v1/web/comments/%s/add/"

	// commentWriteTimeout bounds the one write. The write runs on a context
	// DETACHED from the request's cancellation (context.WithoutCancel): a
	// caller that hangs up mid-call must not turn a comment that would have
	// been answered into an ambiguous one, which the ledger then has to hold
	// as claimed forever.
	commentWriteTimeout = 60 * time.Second
)

// postReferer is the referer the web client sends from a post's own page, the
// form FetchMedia builds (misc.go:85-89). It is built from the CANONICAL
// shortcode of the decoded media id, never the caller's string: a caller's
// spelling with leading `A` digits decodes to the same post but is not a URL
// the web client would ever be on.
func postReferer(media domain.MediaID) string {
	return restOrigin + "/p/" + media.Shortcode().String() + "/"
}

type wireComments struct {
	Comments      []json.RawMessage `json:"comments"`
	ChildComments []json.RawMessage `json:"child_comments"`
}

type wireComment struct {
	PK              flexID        `json:"pk"`
	ID              flexID        `json:"id"`
	ParentCommentID flexID        `json:"parent_comment_id"`
	Text            string        `json:"text"`
	CreatedAt       flexTimestamp `json:"created_at"`
	User            struct {
		PK       flexID `json:"pk"`
		PKID     flexID `json:"pk_id"`
		ID       flexID `json:"id"`
		Username string `json:"username"`
	} `json:"user"`
}

// commentsError is a failure with a FIXED text over its cause, the same
// pattern as mentionsReadError.
type commentsError struct {
	msg   string
	cause error
}

func (e *commentsError) Error() string { return e.msg }
func (e *commentsError) Unwrap() error { return e.cause }

// readComments reads one page of a post's comments: the top-level ones, or,
// with a parent, that comment's replies.
func readComments(
	ctx context.Context, issuer RESTIssuer, code domain.Shortcode, media domain.MediaID, parent *domain.CommentID,
) (domain.CommentPage, error) {
	req := restRequest{
		endpoint: fmt.Sprintf(commentsEndpoint, media.String()),
		label:    "media_comments",
		referer:  postReferer(media),
		idsInURL: true,
	}
	if parent != nil {
		req.endpoint = fmt.Sprintf(childCommentsEndpoint, media.String(), parent.String())
		req.label = "media_child_comments"
	}
	_, raw, err := restDo(ctx, issuer, req)
	if err != nil {
		return domain.CommentPage{}, err
	}
	var envelope wireComments
	if jsonErr := json.Unmarshal(raw, &envelope); jsonErr != nil {
		return domain.CommentPage{}, domain.Classify(domain.ErrUpstreamRejected,
			&commentsError{msg: req.label + " did not return the expected shape", cause: jsonErr})
	}
	rawItems := envelope.Comments
	if parent != nil && envelope.ChildComments != nil {
		rawItems = envelope.ChildComments
	}

	page := domain.CommentPage{Items: make([]domain.Comment, 0, len(rawItems))}
	for _, rawItem := range rawItems {
		var wc wireComment
		if json.Unmarshal(rawItem, &wc) != nil {
			page.Skipped++
			continue
		}
		c, ok := commentOf(wc, parent)
		if !ok {
			page.Skipped++
			continue
		}
		page.Items = append(page.Items, c)
	}
	return page, nil
}

// commentOf maps one decoded comment, or reports false when it carries no
// usable id. A reply read under a parent names that parent even when the wire
// left `parent_comment_id` out: the route asked for that parent's children.
func commentOf(wc wireComment, parent *domain.CommentID) (domain.Comment, bool) {
	rawID := wc.PK.String()
	if rawID == "" {
		rawID = wc.ID.String()
	}
	id, err := domain.ParseCommentID(rawID)
	if err != nil {
		return domain.Comment{}, false
	}
	c := domain.Comment{
		ID:             id.String(),
		AuthorUsername: wc.User.Username,
		Text:           wc.Text,
		At:             int64(wc.CreatedAt),
	}
	if p, err := domain.ParseCommentID(wc.ParentCommentID.String()); err == nil {
		c.ParentID = p.String()
	} else if parent != nil {
		c.ParentID = parent.String()
	}
	for _, author := range []flexID{wc.User.PK, wc.User.PKID, wc.User.ID} {
		if author != "" {
			c.AuthorIGID = author.String()
			break
		}
	}
	return c, true
}

// wireCommentAdded is the write's 200. `id` is the new comment's; `pk` is
// accepted as a fallback, the same tolerance a read gives a comment.
type wireCommentAdded struct {
	ID flexID `json:"id"`
	PK flexID `json:"pk"`
}

// postComment writes ONE reply under a comment. There is no top-level form:
// replyTo is required by the type.
//
// Its error says whether the comment is known NOT to exist: a refusal
// Instagram answered at the endpoint (restAnswered: a 4xx, or a 200 carrying
// "status":"fail") is also domain.ErrNotPosted. Everything else, including a
// 200 whose body names no new comment, is ambiguous, and the caller must hold
// the reply as claimed and never retry it.
func postComment(
	ctx context.Context, issuer RESTIssuer, code domain.Shortcode, media domain.MediaID,
	replyTo domain.CommentID, text string,
) (domain.PostedComment, error) {
	// KNOWN DIFFERENCE to settle from the browser capture design 19 requires
	// before the first live write: the library's own web POST
	// (RegisterPushNotifications, misc.go:41-48) also sends `jazoest` and
	// `fb_dtsg` in its form, and this form does not. Whether the web
	// client's comment POST carries them is unmeasured.
	form := url.Values{}
	form.Set("comment_text", text)
	form.Set("replied_to_comment_id", replyTo.String())
	_, raw, err := restDo(ctx, issuer, restRequest{
		method:   http.MethodPost,
		endpoint: fmt.Sprintf(commentAddEndpoint, media.String()),
		label:    "comment_add",
		referer:  postReferer(media),
		form:     form,
		idsInURL: true,
	})
	if err != nil {
		var answered *restAnswered
		if errors.As(err, &answered) {
			if reason := blockReason(answered.status, answered.body); reason != "" {
				return domain.PostedComment{}, &domain.ActionBlocked{Reason: reason, Err: err}
			}
			return domain.PostedComment{}, domain.Classify(domain.ErrNotPosted, err)
		}
		return domain.PostedComment{}, err
	}
	var added wireCommentAdded
	if jsonErr := json.Unmarshal(raw, &added); jsonErr != nil {
		return domain.PostedComment{}, domain.Classify(domain.ErrUpstreamRejected,
			&commentsError{msg: "comment_add did not return the expected shape", cause: jsonErr})
	}
	rawID := added.ID.String()
	if rawID == "" {
		rawID = added.PK.String()
	}
	id, idErr := domain.ParseCommentID(rawID)
	if idErr != nil {
		// Instagram said ok and named nothing usable. The comment probably
		// exists; this is NOT ErrNotPosted. The parse error is not carried as
		// the cause: it is ErrInvalidID, which KindOf would find first and
		// answer 400, blaming the caller for Instagram's answer.
		return domain.PostedComment{}, domain.Classify(domain.ErrUpstreamRejected,
			errors.New("comment_add answered without a comment id"))
	}
	return domain.PostedComment{ID: id.String(), ReplyToCommentID: replyTo.String()}, nil
}

// blockReason reads a write refusal for Instagram's action-block signal: a
// 429, `"message":"feedback_required"` (the "Action Blocked" dialog's body),
// `"spam":true`, or a `sentry_block` marker anywhere in the body (it arrives
// in `feedback_url`/`feedback_action` as well as in `message`). UNMEASURED
// on this endpoint: the shapes are the private API's, and none has been
// captured from a web comment write. Returns "" for an ordinary refusal.
func blockReason(status int, body []byte) string {
	if status == http.StatusTooManyRequests {
		return domain.BlockRateLimited
	}
	var b struct {
		Message string `json:"message"`
		Spam    bool   `json:"spam"`
	}
	_ = json.Unmarshal(body, &b)
	switch {
	case b.Message == "feedback_required":
		return domain.BlockFeedbackRequired
	case bytes.Contains(body, []byte("sentry_block")):
		return domain.BlockSentry
	case b.Spam:
		return domain.BlockSpam
	}
	return ""
}
