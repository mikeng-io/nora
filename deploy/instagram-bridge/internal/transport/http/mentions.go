package httptransport

// GET /mentions — comment-mentions from Instagram's activity/notification
// feed: the read-only probe route behind a later "summons" reply
// (design/instagram-adoption task: Nora learning when someone @mentions her
// in a COMMENT on a post). Superseded as the summons intake on 2026-09-26
// (design 19): the summons arrives as a DM card and the SSE `summons` event;
// this route stays as a read and authorizes no comment write.
//
// On 16's list convention: success is `{"items": [...], "next_cursor":
// null, "skipped_story_types": {...}}` — items are domain-built DTOs, never
// wire structs, and `skipped_story_types` is this route's own addition
// beyond the shared convention: how many stories of each numeric
// `story_type` were NOT classified as a mention, so the live probe can read
// which type a real one arrives as. Failure is the one shared envelope
// (errors.go), at the shared status table.
//
// # What never leaves in telemetry
//
// A comment's text and a commenter's igid/username must never reach a span,
// a log line or an error (16 §Tracing's never-list, applied here the same
// way handlePost's file header applies it to a caption). The gateway
// (gateway/instameow/mentions.go) already keeps them out of the upstream
// span and every error it raises; this handler adds only a COUNT to the
// server span, the same convention handlePost and handleAccountsSearch use
// for their own result counts.

import (
	"net/http"
	"strconv"
	"time"

	"instagram-bridge/internal/domain"

	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/trace"
)

type mentionAuthor struct {
	IGID     string `json:"igid"`
	Username string `json:"username"`
}

type mentionMedia struct {
	ID        string `json:"id"`
	Shortcode string `json:"shortcode"`
}

// mentionComment carries only the id: the COMMENT's own text is not what
// this route reads. `args.text`/`args.rich_text` on the activity feed is
// Instagram's own NOTIFICATION sentence, not the comment's content — see
// domain.CommentMention.NotificationText — so it rides as its own top-level
// field, never inside `comment`, where a reader would mistake it for the
// comment.
type mentionComment struct {
	ID string `json:"id"`
}

// mentionItem is one comment-mention. No thread or message id: this is
// Instagram's public comment surface, not a DM. `at` is OMITTED, not
// rendered as the Unix epoch, when the story carried no timestamp: a missing
// `at` must read as "unknown", never as "1970-01-01".
type mentionItem struct {
	StoryType        int            `json:"story_type"`
	At               *string        `json:"at,omitempty"`
	Author           mentionAuthor  `json:"author"`
	Media            mentionMedia   `json:"media"`
	Comment          mentionComment `json:"comment"`
	NotificationText string         `json:"notification_text"`
}

// mentionsBody is the 200 shape.
type mentionsBody struct {
	Items             []mentionItem  `json:"items"`
	NextCursor        *string        `json:"next_cursor"`
	SkippedStoryTypes map[string]int `json:"skipped_story_types"`
}

func mentionItemOf(m domain.CommentMention) mentionItem {
	item := mentionItem{
		StoryType:        m.StoryType,
		Author:           mentionAuthor{IGID: m.AuthorIGID, Username: m.AuthorUsername},
		Media:            mentionMedia{ID: m.MediaID, Shortcode: m.MediaShortcode},
		Comment:          mentionComment{ID: m.CommentID},
		NotificationText: m.NotificationText,
	}
	if m.At != 0 {
		at := time.Unix(m.At, 0).UTC().Format(time.RFC3339)
		item.At = &at
	}
	return item
}

func mentionItemsOf(in []domain.CommentMention) []mentionItem {
	out := make([]mentionItem, 0, len(in))
	for _, m := range in {
		out = append(out, mentionItemOf(m))
	}
	return out
}

// skippedStoryTypesOf turns the gateway's int-keyed count into the
// string-keyed shape a JSON object requires.
func skippedStoryTypesOf(in map[int]int) map[string]int {
	out := make(map[string]int, len(in))
	for k, v := range in {
		out[strconv.Itoa(k)] = v
	}
	return out
}

// handleMentions answers GET /mentions.
func (s *Server) handleMentions(w http.ResponseWriter, r *http.Request) {
	mentions, skipped, err := s.app.ReadMentions(r.Context())
	if err != nil {
		failEnvelope(r.Context(), w, "mentions", err)
		return
	}
	// COUNTS only — never an id, a username or comment text (file header;
	// 16 §Tracing's never-list). The same convention handlePost's item_count
	// and handleAccountsSearch's result_count already use.
	//
	// skipped_count is the number of SKIPPED STORIES (the sum of every
	// story_type's count), not the number of distinct story_types: len(skipped)
	// would undercount whenever two or more skipped stories share a type.
	skippedTotal := 0
	for _, n := range skipped {
		skippedTotal += n
	}
	trace.SpanFromContext(r.Context()).SetAttributes(
		attribute.Int("instagram.mentions.count", len(mentions)),
		attribute.Int("instagram.mentions.skipped_count", skippedTotal),
	)
	writeJSON(w, http.StatusOK, mentionsBody{
		Items:             mentionItemsOf(mentions),
		NextCursor:        nil,
		SkippedStoryTypes: skippedStoryTypesOf(skipped),
	})
}
