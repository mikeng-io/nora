package instameow

// One post by its shortcode: `GET /posts/{shortcode}` (design/instagram-adoption/17
// P12, 13 §Depth 3).
//
// # What the library call really is
//
// `(*instameow.Client).FetchMedia(ctx, mediaID, mediaShortcode)`
// (pkg/instameow/misc.go) is a REST GET of
// `https://www.instagram.com/api/v1/media/{mediaID}/info/`
// (pkg/messagix/endpoints/instagram.go, "media_info"), with the web client's
// headers plus `x-requested-with: XMLHttpRequest`, `X-Web-Session-ID`, and a
// `referer` of `/p/{mediaShortcode}/` when a shortcode is given. The shortcode
// is ONLY the referer: the id in the URL is the numeric media id, which the
// shortcode encodes (domain.Shortcode.MediaID). The library's own callers
// always hold that id already (an XMA's `TargetID`,
// pkg/msgconv/igconv/xma.go `fetchXMAMedia`); a link does not, so the decode
// is ours.
//
// It answers `responses.FetchMediaResponse` (pkg/messagix/responses/media.go):
// `items[]`, `num_results`, `more_available`, `status`. `items[0]` is the post:
// `code`, `pk`, `id` (`<pk>_<owner>`), `media_type` (1 image, 2 video, 8
// carousel), `taken_at`, `user.username`, `caption.text`,
// `image_versions2.candidates[]`, `video_versions[]`, `video_duration`, and for
// a carousel `carousel_media[]`, each child an item of the same shape.
//
// # Unmeasured
//
// No live response to this call has been recorded. The literal-byte fixtures
// in fakeig/posts.go are built from the library's struct tags, not captured
// bytes, and are labelled so. Four things are the live probe's to confirm:
//
//  1. that a shortcode decodes to the id this endpoint accepts (the identity
//     check below turns a wrong decode into a refused read, never into a
//     different post);
//  2. what a deleted post, and a private post Nora does not follow, answer.
//     FetchMedia discards the body of a non-2xx, so the STATUS is all this
//     code can read. A 404 is taken as Instagram saying "no such post" and
//     answers ErrNotFound. Anything else — including the 400 that a story id
//     got on this endpoint (17 P12) — is ErrUpstreamRejected: this code cannot
//     tell "no such post" from "not allowed to see it" from "wrong id" by a
//     400, so it does not claim to;
//  3. whether a carousel arrives with `carousel_media` filled in;
//  4. whether `video_duration` is present on a reel.
//
// # One upstream span, and what never goes in it
//
// Through the `call` helper: one CLIENT span, `instagram.FetchMedia`. Its
// attributes carry NEITHER the shortcode NOR the media id: the id is the
// shortcode re-encoded, so it names the same post. The library's own error
// text does carry the id ("failed to fetch the media by id N: ..."), and
// call() records whatever error the wrapped function returns, so every
// failure is replaced by a fixed description (postReadError) BEFORE it
// reaches call(). The caption never reaches an error at all.
//
// The response is also validated INSIDE the wrapped function, so a REST
// failure carried in an HTTP 200 (`"status": "fail"`, no items, a carousel
// without its children, somebody else's post) ends the span ERROR, the same
// guarantee 16 §Tracing requires of a GraphQL `errors` array.

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"strconv"
	"strings"

	"instagram-bridge/internal/domain"

	"go.mau.fi/mautrix-meta/pkg/messagix/httpclient"
	"go.mau.fi/mautrix-meta/pkg/messagix/responses"
)

// mediaTypeCarousel is `media_type` 8. 1 is an image and 2 a video; those two
// are not read from the integer (see postItemOf).
const mediaTypeCarousel = 8

// postReadError is a failure whose TEXT is fixed and whose identity is not:
// Unwrap exposes the cause, so needsHuman and errors.Is still see through it.
type postReadError struct {
	msg   string
	cause error
}

func (e *postReadError) Error() string { return e.msg }
func (e *postReadError) Unwrap() error { return e.cause }

func rejected(msg string) error {
	return domain.Classify(domain.ErrUpstreamRejected, &postReadError{msg: msg})
}

// readPost reads one post. A shortcode that does not decode never reaches
// Instagram and opens no upstream span.
func readPost(ctx context.Context, cli Client, code domain.Shortcode) (domain.Post, error) {
	id, err := code.MediaID()
	if err != nil {
		return domain.Post{}, err
	}
	return call(ctx, "instagram.FetchMedia", nil,
		func(ctx context.Context) (domain.Post, error) {
			resp, err := cli.FetchMedia(ctx, id.String(), code.String())
			if err != nil {
				return domain.Post{}, fetchMediaFailure(err)
			}
			return postOf(ctx, resp, code, id)
		})
}

// fetchMediaFailure maps what FetchMedia raised onto the closed set, with a
// fixed text. The library returns the status of a non-2xx only inside its
// error TEXT (`httpclient.ErrUnexpectedError` + " 404"), so the status is
// read from there, anchored on the library's own sentinel.
func fetchMediaFailure(err error) error {
	switch {
	case needsHuman(err):
		// Left unclassified here: call()'s classify marks it ErrNeedsHuman
		// through Unwrap.
		return &postReadError{msg: "instagram post read needs a human", cause: err}
	case errors.Is(err, httpclient.ErrUnexpectedError):
		status := unexpectedStatus(err)
		if status == 404 {
			return domain.Classify(domain.ErrNotFound,
				&postReadError{msg: "instagram answered 404 for that post", cause: err})
		}
		return domain.Classify(domain.ErrUpstreamRejected,
			&postReadError{msg: fmt.Sprintf("instagram refused the post read (HTTP %d)", status), cause: err})
	case isJSONFailure(err):
		// A 200 that is not the JSON the struct expects: an HTML challenge
		// page, a truncated body.
		return domain.Classify(domain.ErrUpstreamRejected,
			&postReadError{msg: "instagram's post read did not return the expected JSON", cause: err})
	default:
		// Unclassified: transport failures and anything unnamed stay 500,
		// the fail-closed default every other route has.
		return &postReadError{msg: "instagram post read failed", cause: err}
	}
}

// unexpectedStatus is the HTTP status the library wrote after its sentinel,
// or 0 when there is none.
func unexpectedStatus(err error) int {
	marker := httpclient.ErrUnexpectedError.Error() + " "
	text := err.Error()
	i := strings.Index(text, marker)
	if i < 0 || len(text) < i+len(marker)+3 {
		return 0
	}
	n, convErr := strconv.Atoi(text[i+len(marker) : i+len(marker)+3])
	if convErr != nil {
		return 0
	}
	return n
}

func isJSONFailure(err error) bool {
	var syntax *json.SyntaxError
	var typ *json.UnmarshalTypeError
	return errors.As(err, &syntax) || errors.As(err, &typ)
}

// postOf turns the answer into a Post, or refuses it.
func postOf(ctx context.Context, resp *responses.FetchMediaResponse, code domain.Shortcode, id domain.MediaID) (domain.Post, error) {
	if resp == nil {
		return domain.Post{}, rejected("post read returned no body")
	}
	// The REST form of an error inside a 200. Instagram's v1 API says
	// `"status": "ok"` on success; an absent status is tolerated because the
	// library's struct makes it optional, and the items check below still
	// has to pass.
	if resp.Status != "" && resp.Status != "ok" {
		return domain.Post{}, rejected("post read answered HTTP 200 with a failure status")
	}
	if len(resp.Items) == 0 || resp.Items[0] == nil {
		// Not ErrNotFound: an empty answer does not say the post does not
		// exist, only that this read did not get it.
		return domain.Post{}, rejected("post read returned no item")
	}
	item := resp.Items[0]

	// The post must be the one that was asked for. A wrong shortcode decode
	// would otherwise return somebody else's post as the answer.
	if err := sameIdentity(item, code, id); err != nil {
		return domain.Post{}, err
	}

	var items []domain.PostItem
	if item.MediaType == mediaTypeCarousel || len(item.CarouselMedia) > 0 {
		if len(item.CarouselMedia) == 0 {
			// Refused, not degraded to the cover: a carousel read as its first
			// image is the exact bug this route exists to fix.
			return domain.Post{}, rejected("post read returned a carousel without its items")
		}
		for i, child := range item.CarouselMedia {
			if child == nil {
				slog.WarnContext(ctx, "post carousel item was null; skipped", "index", i)
				continue
			}
			pi, ok := postItemOf(child)
			if !ok {
				// Skipped, as a story frame with no url is (social.go): the
				// other items are still readable. Logged by position only.
				slog.WarnContext(ctx, "post carousel item had no media url; skipped", "index", i)
				continue
			}
			items = append(items, pi)
		}
	} else if pi, ok := postItemOf(item); ok {
		items = append(items, pi)
	}
	if len(items) == 0 {
		return domain.Post{}, rejected("post read returned no item with a media url")
	}

	return domain.Post{
		Shortcode: code.String(),
		Author:    item.User.Username,
		Caption:   item.Caption.Text,
		TakenAt:   int64(item.TakenAt),
		Items:     items,
	}, nil
}

// sameIdentity checks the answer against the request on every identity field
// it carries, and refuses an answer that carries none.
func sameIdentity(item *responses.Items, code domain.Shortcode, id domain.MediaID) error {
	pk := item.Pk
	if pk == "" {
		// `id` is `<pk>_<owner igid>`.
		pk, _, _ = strings.Cut(item.ID, "_")
	}
	if item.Code == "" && pk == "" {
		return rejected("post read returned an item with no code or pk to confirm it")
	}
	if item.Code != "" && item.Code != code.String() {
		return rejected("post read returned a different post than the shortcode names")
	}
	if pk != "" && pk != id.String() {
		return rejected("post read returned a different post than the shortcode names")
	}
	return nil
}

// postItemOf reduces one image or video to what a caller can fetch.
//
// The kind is read from what is PRESENT, not from `media_type`: an item that
// says video and carries no clip degrades to its cover frame rather than
// promising bytes that are not there (storyFrame's rule). The largest
// candidate by area wins, then by width, as the library's own
// `wrapXMAItem` picks.
func postItemOf(it *responses.Items) (domain.PostItem, bool) {
	videos := make([]mediaCandidate, 0, len(it.VideoVersions))
	for _, v := range it.VideoVersions {
		videos = append(videos, mediaCandidate{URL: v.URL, Width: v.Width, Height: v.Height})
	}
	images := make([]mediaCandidate, 0, len(it.ImageVersions2.Candidates))
	for _, c := range it.ImageVersions2.Candidates {
		images = append(images, mediaCandidate{URL: c.URL, Width: c.Width, Height: c.Height})
	}
	return pickMedia(videos, images, it.VideoDuration)
}

// pickMedia is postItemOf's rule over plain candidates, shared with the
// timeline, whose nodes carry the same renditions: the largest clip when
// there is one (with its duration when Instagram sent one), else the largest
// image.
func pickMedia(videos, images []mediaCandidate, duration float64) (domain.PostItem, bool) {
	var best domain.PostItem
	for _, v := range videos {
		if v.URL != "" && larger(v.Width, v.Height, best) {
			best = domain.PostItem{Kind: mediaVideo, URL: v.URL, Width: v.Width, Height: v.Height}
		}
	}
	if best.URL != "" {
		if duration > 0 {
			d := duration
			best.DurationSeconds = &d
		}
		return best, true
	}
	for _, c := range images {
		if c.URL != "" && larger(c.Width, c.Height, best) {
			best = domain.PostItem{Kind: mediaImage, URL: c.URL, Width: c.Width, Height: c.Height}
		}
	}
	return best, best.URL != ""
}

func larger(w, h int, than domain.PostItem) bool {
	if than.URL == "" {
		return true
	}
	if a, b := w*h, than.Width*than.Height; a != b {
		return a > b
	}
	return w > than.Width
}
