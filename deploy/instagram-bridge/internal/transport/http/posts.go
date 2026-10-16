package httptransport

// GET /posts/{shortcode} — one post, every item in it
// (design/instagram-adoption/16 §Resources, 17 P12).
//
// On 16's new conventions, like GET /accounts?query=: success is the post
// itself, with no `ok` and no ids; failure is the one envelope at the full
// status table (400 malformed shortcode, 404 Instagram says no such post, 409
// the session needs a human, 501 a shortcode too long to decode, 502
// Instagram refused or answered something that is not this post, 503 no
// client yet, 500 anything unnamed).
//
// # Order
//
// The shortcode is parsed HERE, before the use case, so a malformed one is 400
// whatever the session's state (16: "a malformed id is 400 before any use
// case runs"). The session gate runs inside the use case, next.
//
// # What never leaves in telemetry
//
//   - The CAPTION is the author's free text: it goes into the 200 body and
//     nowhere else. Not a span, not a log line, not an envelope.
//   - The SHORTCODE names somebody's post. It appears in the span only as the
//     `{shortcode}` placeholder of the route pattern: the span name and
//     `http.route` are the pattern, and `url.path`, which otelhttp fills with
//     the raw path, is overwritten with the pattern too
//     (observability.PatternOnlyRoute). The gateway keeps the numeric media id
//     (the same post, re-encoded) out of its span.

import (
	"net/http"

	"instagram-bridge/internal/domain"

	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/trace"
)

// postItem is one image or video. `duration_seconds` is present only when
// Instagram sent one.
type postItem struct {
	Kind            string   `json:"kind"`
	URL             string   `json:"url"`
	Width           int      `json:"width"`
	Height          int      `json:"height"`
	DurationSeconds *float64 `json:"duration_seconds,omitempty"`
}

// postBody is the 200 shape. No ids: the caller addressed the post by its
// shortcode, and the numeric ids name the author and the post in spaces
// Nora's model must not see (R-18).
type postBody struct {
	Shortcode string     `json:"shortcode"`
	Author    string     `json:"author"`
	Caption   string     `json:"caption"`
	TakenAt   int64      `json:"taken_at"`
	Items     []postItem `json:"items"`
}

// postItemsOf is the wire items, never nil.
func postItemsOf(in []domain.PostItem) []postItem {
	items := make([]postItem, 0, len(in))
	for _, it := range in {
		items = append(items, postItem{
			Kind: it.Kind, URL: it.URL, Width: it.Width, Height: it.Height,
			DurationSeconds: it.DurationSeconds,
		})
	}
	return items
}

func postOf(p domain.Post) postBody {
	return postBody{
		Shortcode: p.Shortcode, Author: p.Author, Caption: p.Caption,
		TakenAt: p.TakenAt, Items: postItemsOf(p.Items),
	}
}

// handlePost answers GET /posts/{shortcode}.
func (s *Server) handlePost(w http.ResponseWriter, r *http.Request) {
	code, err := domain.ParseShortcode(r.PathValue("shortcode"))
	if err != nil {
		writeErrorEnvelope(w, http.StatusBadRequest, "invalid_id", err.Error())
		return
	}
	post, err := s.app.ReadPost(r.Context(), code)
	if err != nil {
		failEnvelope(r.Context(), w, "post", err)
		return
	}
	// A COUNT, like the search route's result count: it says how many items
	// the post had, and nothing about whose post it was.
	trace.SpanFromContext(r.Context()).SetAttributes(
		attribute.Int("instagram.post.item_count", len(post.Items)))
	writeJSON(w, http.StatusOK, postOf(post))
}
