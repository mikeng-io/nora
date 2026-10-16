package fakeig

// The configured GraphQL documents' answers (the gateway's docs.go), as
// LITERAL JSON bytes in the shapes captured from the web client in S0
// (design/cognition-loop/22-social-stream.md §1a). Every value is synthetic:
// short placeholder ids, placeholder CDN paths, placeholder usernames.

import (
	"context"
	"fmt"
	"net/http"

	"go.mau.fi/mautrix-meta/pkg/messagix/httpclient"
)

// DocRequest is one request MakeDocRequest received.
type DocRequest struct {
	Endpoint     string
	Referer      string
	FriendlyName string
	DocID        string
	Variables    string
}

// The friendly names the fake answers. Any other is answered 400 with a
// GraphQL error, so a renamed document fails here the way it would live.
const (
	StoryDocument    = "PolarisStoriesV3ReelPageStandaloneQuery"
	TimelineDocument = "PolarisProfilePostsTabContentQuery_connection"
)

// StoryIGID is the account MeasuredStoryJSON's reel belongs to.
const StoryIGID = "246967358"

// MeasuredStoryJSON is a two-frame story in the S0 shape: one image and one
// video whose cover still is in `image_versions2`. The reel's own
// `expiring_at` is null and each frame's is its `taken_at` + 86400, as S0
// measured; `caption` is null on the video, an object on the image. One
// unread key per level (`reel_type`, `has_liked`) stands for the ~60 the
// bridge leaves unparsed.
const MeasuredStoryJSON = `{"data":{"xdt_api__v1__feed__reels_media":{"reels_media":[{
  "id": "246967358",
  "reel_type": "user_reel",
  "latest_reel_media": 1790011844,
  "seen": 0,
  "expiring_at": null,
  "user": {"pk": "246967358", "username": "nka_bpumvg"},
  "items": [
    {"pk": "1001", "id": "1001_246967358", "taken_at": 1790000432, "expiring_at": 1790086832,
     "media_type": 1, "has_liked": false,
     "caption": {"text": "synthetic story caption"},
     "accessibility_caption": "a photo of a plate",
     "image_versions2": {"candidates": [
       {"width": 640, "height": 1136, "url": "https://cdn/small.jpg"},
       {"width": 1080, "height": 1920, "url": "https://cdn/large.jpg"}]}},
    {"pk": "1002", "id": "1002_246967358", "taken_at": 1790011844, "expiring_at": 1790098244,
     "media_type": 2, "caption": null,
     "image_versions2": {"candidates": [{"width": 1080, "height": 1920, "url": "https://cdn/cover.jpg"}]},
     "video_versions": [
       {"type": 101, "width": 480, "height": 854, "url": "https://cdn/small.mp4"},
       {"type": 102, "width": 1080, "height": 1920, "url": "https://cdn/large.mp4"}]}
  ]
}]}},"extensions":{"is_final":true}}`

// NoStoryJSON is an empty `reels_media`: no visible story.
const NoStoryJSON = `{"data":{"xdt_api__v1__feed__reels_media":{"reels_media":[]}}}`

// TimelineOwnerIGID is the account the timeline pages below belong to.
const TimelineOwnerIGID = "246967358"

// TimelineFirstPageJSON is a page with more after it: a reel, a carousel
// post, and a post with no caption, newest first. Each node names its owner
// twice, in the `<pk>_<owner>` id and in `user.pk`.
const TimelineFirstPageJSON = `{"data":{"xdt_api__v1__feed__user_timeline_graphql_connection":{
  "edges": [
    {"node": {"code": "SynthReel01", "pk": "2001", "id": "2001_246967358", "user": {"pk": "246967358"}, "taken_at": 1790000300,
      "caption": {"text": "synthetic reel caption"}, "media_type": 2, "product_type": "clips",
      "video_duration": 7.5,
      "video_versions": [
        {"width": 360, "height": 640, "url": "https://cdn/r-small.mp4"},
        {"width": 720, "height": 1280, "url": "https://cdn/r.mp4"}],
      "image_versions2": {"candidates": [{"width": 720, "height": 1280, "url": "https://cdn/r.jpg"}]}}},
    {"node": {"code": "SynthPost02", "pk": "2002", "id": "2002_246967358", "user": {"pk": "246967358"}, "taken_at": 1790000200,
      "caption": {"text": "synthetic carousel caption"}, "media_type": 8,
      "product_type": "carousel_container", "carousel_media_count": 3,
      "carousel_media": [
        {"media_type": 1, "image_versions2": {"candidates": [
          {"width": 320, "height": 320, "url": "https://cdn/c1-small.jpg"},
          {"width": 1080, "height": 1080, "url": "https://cdn/c1.jpg"}]}},
        {"media_type": 2, "video_duration": 3,
         "image_versions2": {"candidates": [{"width": 1080, "height": 1080, "url": "https://cdn/c2.jpg"}]},
         "video_versions": [{"width": 1080, "height": 1080, "url": "https://cdn/c2.mp4"}]},
        {"media_type": 1}]}},
    {"node": {"code": "SynthPost03", "pk": "2003", "id": "2003_246967358", "user": {"pk": "246967358"}, "taken_at": 1790000100,
      "caption": null, "media_type": 1, "product_type": "feed",
      "accessibility_caption": "a photo of a plate",
      "image_versions2": {"candidates": [{"width": 1080, "height": 1350, "url": "https://cdn/p3.jpg"}]}}}
  ],
  "page_info": {"end_cursor": "synthetic-cursor-1", "has_next_page": true}
}}}`

// TimelineLastPageJSON is the end of the timeline: one post, no next page.
const TimelineLastPageJSON = `{"data":{"xdt_api__v1__feed__user_timeline_graphql_connection":{
  "edges": [
    {"node": {"code": "SynthPost04", "pk": "2004", "id": "2004_246967358", "user": {"pk": "246967358"}, "taken_at": 1790000000,
      "caption": {"text": "synthetic first post"}, "media_type": 1, "product_type": "feed"}}
  ],
  "page_info": {"end_cursor": null, "has_next_page": false}
}}}`

// MakeDocRequest is the gateway's DocIssuer.
func (f *IG) MakeDocRequest(
	_ context.Context, endpoint, referer, friendlyName, docID string, variables []byte,
) (*http.Response, []byte, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.DocRequests = append(f.DocRequests, DocRequest{
		Endpoint: endpoint, Referer: referer, FriendlyName: friendlyName,
		DocID: docID, Variables: string(variables),
	})
	if f.DocErr != nil {
		return nil, nil, f.DocErr
	}
	var body []byte
	switch friendlyName {
	case StoryDocument:
		body = []byte(MeasuredStoryJSON)
		if f.StoryBody != nil {
			body = f.StoryBody
		}
	case TimelineDocument:
		body = []byte(TimelineLastPageJSON)
		if f.TimelineBody != nil {
			body = f.TimelineBody
		}
	default:
		return &http.Response{StatusCode: http.StatusBadRequest},
			[]byte(`{"errors":[{"message":"unknown document","code":1}]}`),
			fmt.Errorf("%w 400", httpclient.ErrUnexpectedError)
	}
	status := f.DocStatus
	if status == 0 {
		status = http.StatusOK
	}
	if status >= 400 {
		// The live library's shape (httpclient/http.go makeRequestOnce): a
		// status >= 400 comes back as an error AND the response and body.
		return &http.Response{StatusCode: status}, body,
			fmt.Errorf("%w %d", httpclient.ErrUnexpectedError, status)
	}
	return &http.Response{StatusCode: status}, body, nil
}
