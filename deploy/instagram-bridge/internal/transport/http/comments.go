package httptransport

// GET  /posts/{shortcode}/comments[?reply_to=<comment_id>]  a post's comments,
//                                                          or one comment's
//                                                          replies
// POST /posts/{shortcode}/comments                         the ONE public
//                                                          write: a reply
//                                                          under a summons
//
// (design/instagram-adoption/19 phases 2 and 4.) Every guard on the write
// lives in the use case (app/comments.go) and refuses before any upstream
// call; this file parses, calls one use case, and writes what it returned.
//
// # What never leaves in telemetry
//
// A shortcode, a comment id, an igid, a username and a comment's text must
// never reach a span, a log line or an error text. Both routes are
// PatternOnlyRoute (server.go), this file adds only COUNTS to the server
// span, and every refusal the parse makes has a fixed message.

import (
	"net/http"
	"time"

	"instagram-bridge/internal/domain"

	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/trace"
)

// commentBodyLimit bounds the write's request body: 2200 characters, each at
// most a 12-byte JSON escape (a surrogate pair), plus the id, with room.
const commentBodyLimit = 64 << 10

type commentAuthor struct {
	IGID     string `json:"igid"`
	Username string `json:"username"`
}

// commentItem is one comment. `parent_id` and `at` are OMITTED when unknown,
// never empty or the Unix epoch.
type commentItem struct {
	ID       string        `json:"id"`
	ParentID string        `json:"parent_id,omitempty"`
	Author   commentAuthor `json:"author"`
	Text     string        `json:"text"`
	At       *string       `json:"at,omitempty"`
}

// commentsBody is the read's 200: 16's list convention. `next_cursor` is
// always null: this read fetches one page and no paging mechanism is built
// (UNMEASURED what cursor the endpoint offers). `skipped_count` is how many
// comments the wire carried that could not be decoded.
type commentsBody struct {
	Items        []commentItem `json:"items"`
	NextCursor   *string       `json:"next_cursor"`
	SkippedCount int           `json:"skipped_count"`
}

func commentItemsOf(in []domain.Comment) []commentItem {
	out := make([]commentItem, 0, len(in))
	for _, c := range in {
		item := commentItem{
			ID:       c.ID,
			ParentID: c.ParentID,
			Author:   commentAuthor{IGID: c.AuthorIGID, Username: c.AuthorUsername},
			Text:     c.Text,
		}
		if c.At != 0 {
			at := time.Unix(c.At, 0).UTC().Format(time.RFC3339)
			item.At = &at
		}
		out = append(out, item)
	}
	return out
}

// handleComments answers GET /posts/{shortcode}/comments.
func (s *Server) handleComments(w http.ResponseWriter, r *http.Request) {
	code, err := domain.ParseShortcode(r.PathValue("shortcode"))
	if err != nil {
		writeErrorEnvelope(w, http.StatusBadRequest, "invalid_id", err.Error())
		return
	}
	var parent *domain.CommentID
	if q := r.URL.Query(); q.Has("reply_to") {
		id, err := domain.ParseCommentID(q.Get("reply_to"))
		if err != nil {
			// ParseCommentID's text is fixed and does not quote the input.
			writeErrorEnvelope(w, http.StatusBadRequest, "invalid_id", "reply_to: "+err.Error())
			return
		}
		parent = &id
	}
	page, err := s.app.ReadComments(r.Context(), code, parent)
	if err != nil {
		failEnvelope(r.Context(), w, "comments", err)
		return
	}
	trace.SpanFromContext(r.Context()).SetAttributes(
		attribute.Int("instagram.comments.count", len(page.Items)),
		attribute.Int("instagram.comments.skipped_count", page.Skipped),
	)
	writeJSON(w, http.StatusOK, commentsBody{
		Items:        commentItemsOf(page.Items),
		NextCursor:   nil,
		SkippedCount: page.Skipped,
	})
}

// postCommentRequest is the write's body. There is no field for a top-level
// comment: a reply target is required, and the use case refuses its absence.
type postCommentRequest struct {
	Text             string `json:"text"`
	ReplyToCommentID string `json:"reply_to_comment_id"`
}

type postCommentBody struct {
	ID               string `json:"id"`
	ReplyToCommentID string `json:"reply_to_comment_id"`
}

// handlePostComment answers POST /posts/{shortcode}/comments: 201 with the new
// comment's id, or one envelope per guard (errors.go's refusal table).
func (s *Server) handlePostComment(w http.ResponseWriter, r *http.Request) {
	code, err := domain.ParseShortcode(r.PathValue("shortcode"))
	if err != nil {
		writeErrorEnvelope(w, http.StatusBadRequest, "invalid_id", err.Error())
		return
	}
	var req postCommentRequest
	if _, ok := readJSON(w, r, commentBodyLimit, &req); !ok {
		return
	}
	posted, err := s.app.PostComment(r.Context(), code, req.Text, req.ReplyToCommentID)
	if err != nil {
		failEnvelope(r.Context(), w, "post comment", err)
		return
	}
	writeJSON(w, http.StatusCreated, postCommentBody{ID: posted.ID, ReplyToCommentID: posted.ReplyToCommentID})
}
