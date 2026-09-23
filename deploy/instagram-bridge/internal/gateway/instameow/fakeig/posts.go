package fakeig

// FetchMedia's answers, as LITERAL JSON bytes decoded the way the library
// decodes them (`json.Unmarshal` into `*responses.FetchMediaResponse`,
// pkg/instameow/misc.go).
//
// UNMEASURED. No live response to `/api/v1/media/{id}/info/` has been
// recorded. These bytes are built from the library's own struct tags
// (pkg/messagix/responses/media.go: `items`, `status`, `code`, `pk`, `id`,
// `media_type`, `taken_at`, `user.username`, `caption.text`,
// `image_versions2.candidates`, `video_versions`, `video_duration`,
// `carousel_media`), not captured from the wire, so they pin what this code
// does with that SHAPE and nothing about whether Instagram sends it. The live
// probe (17 P12) replaces them with captured bytes, scrubbed.
//
// Every value is synthetic: the shortcode/media-id pairs are the synthetic
// pairs domain/shortcode_test.go pins (computed with Python's base64 running
// instaloader's algorithm), the author is a placeholder, the urls are
// placeholder CDN paths.

import (
	"context"
	"encoding/json"
	"fmt"

	"go.mau.fi/mautrix-meta/pkg/messagix/httpclient"
	"go.mau.fi/mautrix-meta/pkg/messagix/responses"
)

const (
	CarouselShortcode = "CmiJBr2LAAB"
	CarouselMediaID   = "3000000000000000001"
	ImageShortcode    = "CsFaZNTtgAC"
	ImageMediaID      = "3100000000000000002"
	VideoShortcode    = "CxorwuxQAAD"
	VideoMediaID      = "3200000000000000003"

	PostAuthor = "example_author"
	// PostCaption is on every synthetic post. Tests assert it reaches the
	// 200 body and never a span, a log line or an error envelope.
	PostCaption = "synthetic caption that must stay out of spans c4e1"
)

// UnmeasuredCarouselJSON is a carousel of three, mixed: an image, a video and
// an image, in that order. The first child's LARGEST candidate is listed
// first and the video's largest clip first, so neither "first" nor "last"
// stands in for "largest".
const UnmeasuredCarouselJSON = `{
  "items": [{
    "taken_at": 1700000000,
    "pk": "3000000000000000001",
    "id": "3000000000000000001_10000001",
    "code": "CmiJBr2LAAB",
    "media_type": 8,
    "user": {"pk": "10000001", "username": "example_author", "full_name": "Example Author"},
    "caption": {"pk": "900000000000000009", "text": "synthetic caption that must stay out of spans c4e1", "created_at": 1700000000},
    "carousel_media_ids": ["3000000000000000011", "3000000000000000012", "3000000000000000013"],
    "carousel_media": [
      {"pk": "3000000000000000011", "id": "3000000000000000011_10000001", "media_type": 1,
       "image_versions2": {"candidates": [
         {"width": 1080, "height": 1350, "url": "https://scontent.cdninstagram.com/v/t51/synthetic-c1-1080.jpg?oh=00_A&oe=0"},
         {"width": 640, "height": 800, "url": "https://scontent.cdninstagram.com/v/t51/synthetic-c1-640.jpg?oh=00_B&oe=0"}]},
       "original_width": 1080, "original_height": 1350},
      {"pk": "3000000000000000012", "id": "3000000000000000012_10000001", "media_type": 2,
       "image_versions2": {"candidates": [
         {"width": 1080, "height": 1920, "url": "https://scontent.cdninstagram.com/v/t51/synthetic-c2-cover.jpg?oh=00_C&oe=0"}]},
       "video_versions": [
         {"type": 101, "width": 720, "height": 1280, "url": "https://instagram.fxxx1-1.fbcdn.net/v/t50/synthetic-c2-720.mp4?oh=00_D&oe=0", "id": "1"},
         {"type": 102, "width": 480, "height": 854, "url": "https://instagram.fxxx1-1.fbcdn.net/v/t50/synthetic-c2-480.mp4?oh=00_E&oe=0", "id": "2"}],
       "video_duration": 12.5},
      {"pk": "3000000000000000013", "id": "3000000000000000013_10000001", "media_type": 1,
       "image_versions2": {"candidates": [
         {"width": 1080, "height": 1080, "url": "https://scontent.cdninstagram.com/v/t51/synthetic-c3.jpg?oh=00_F&oe=0"}]}}
    ]
  }],
  "num_results": 1,
  "more_available": false,
  "auto_load_more_enabled": false,
  "status": "ok"
}`

// UnmeasuredImageJSON is a single-image post.
const UnmeasuredImageJSON = `{
  "items": [{
    "taken_at": 1700003600,
    "pk": "3100000000000000002",
    "id": "3100000000000000002_10000001",
    "code": "CsFaZNTtgAC",
    "media_type": 1,
    "user": {"pk": "10000001", "username": "example_author"},
    "caption": {"text": "synthetic caption that must stay out of spans c4e1"},
    "image_versions2": {"candidates": [
      {"width": 640, "height": 640, "url": "https://scontent.cdninstagram.com/v/t51/synthetic-i-640.jpg?oh=00_G&oe=0"},
      {"width": 1440, "height": 1440, "url": "https://scontent.cdninstagram.com/v/t51/synthetic-i-1440.jpg?oh=00_H&oe=0"}]},
    "original_width": 1440, "original_height": 1440
  }],
  "num_results": 1,
  "status": "ok"
}`

// UnmeasuredVideoJSON is a single video (a reel): a clip, its cover, and a
// duration.
const UnmeasuredVideoJSON = `{
  "items": [{
    "taken_at": 1700007200,
    "pk": "3200000000000000003",
    "id": "3200000000000000003_10000001",
    "code": "CxorwuxQAAD",
    "media_type": 2,
    "product_type": "clips",
    "user": {"pk": "10000001", "username": "example_author"},
    "caption": {"text": "synthetic caption that must stay out of spans c4e1"},
    "image_versions2": {"candidates": [
      {"width": 1080, "height": 1920, "url": "https://scontent.cdninstagram.com/v/t51/synthetic-v-cover.jpg?oh=00_I&oe=0"}]},
    "video_versions": [
      {"type": 101, "width": 1080, "height": 1920, "url": "https://instagram.fxxx1-1.fbcdn.net/v/t50/synthetic-v-1080.mp4?oh=00_J&oe=0", "id": "1"}],
    "video_duration": 30.2,
    "has_audio": true
  }],
  "num_results": 1,
  "status": "ok"
}`

// UnmeasuredFailIn200JSON is a REST failure carried in an HTTP 200: the v1
// API's `status: "fail"` form, with a caption-bearing item alongside so a
// test can prove a refused read does not leak it.
const UnmeasuredFailIn200JSON = `{
  "items": [{
    "pk": "3000000000000000001",
    "code": "CmiJBr2LAAB",
    "media_type": 1,
    "caption": {"text": "synthetic caption that must stay out of spans c4e1"},
    "image_versions2": {"candidates": [{"width": 1, "height": 1, "url": "https://scontent.cdninstagram.com/x.jpg"}]}
  }],
  "message": "synthetic failure message",
  "status": "fail"
}`

// DecodeMedia decodes literal bytes exactly as FetchMedia does. It panics on
// malformed bytes: a fixture that does not decode is a broken test, not a
// case under test.
func DecodeMedia(raw string) *responses.FetchMediaResponse {
	var out *responses.FetchMediaResponse
	if err := json.Unmarshal([]byte(raw), &out); err != nil {
		panic(fmt.Sprintf("fakeig: fixture does not decode: %v", err))
	}
	return out
}

var postsByMediaID = map[string]string{
	CarouselMediaID: UnmeasuredCarouselJSON,
	ImageMediaID:    UnmeasuredImageJSON,
	VideoMediaID:    UnmeasuredVideoJSON,
}

// LibraryStatusError is the error FetchMedia returns for a non-2xx, in the
// library's exact form: `failed to fetch the media by id %s: %w` around
// httpclient's `%w %d` of ErrUnexpectedError (misc.go, http.go).
func LibraryStatusError(mediaID string, status int) error {
	return fmt.Errorf("failed to fetch the media by id %s: %w", mediaID,
		fmt.Errorf("%w %d", httpclient.ErrUnexpectedError, status))
}

func (f *IG) FetchMedia(_ context.Context, mediaID, mediaShortcode string) (*responses.FetchMediaResponse, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.MediaIDs = append(f.MediaIDs, mediaID)
	f.MediaShortcodes = append(f.MediaShortcodes, mediaShortcode)
	if f.MediaErr != nil {
		return nil, f.MediaErr
	}
	if f.MediaSet {
		return f.MediaResp, nil
	}
	raw, ok := postsByMediaID[mediaID]
	if !ok {
		return nil, LibraryStatusError(mediaID, 404)
	}
	return DecodeMedia(raw), nil
}
