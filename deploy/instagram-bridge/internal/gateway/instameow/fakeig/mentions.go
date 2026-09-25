package fakeig

// GET /mentions' upstream: Instagram web's notifications panel,
// `POST /api/v1/news/inbox/` with the "Comments" filter (see
// gateway/instameow/mentions.go).
//
// NewsInboxJSON: KEYS CAPTURED FROM THE LIVE WEB CLIENT 2026-09-25, VALUES
// SYNTHETIC. The top-level keys and every story's `args` keys are the ones
// the operator's own browser received (design 19, phase 1); every value
// (ids, names, text, urls, times) is made up. The story-level keys beside
// `story_type` and `args` were not listed by the capture: `type` carries the
// capture's "tN" note where it gave one, and is left out where it did not.
//
// Its stories are the shapes a comment-mention route must tell apart:
//
//  1. a comment that DOES mention the account (story_type MentionStoryType,
//     a PLACEHOLDER: no comment-mention story was in the captured feed, so
//     its real numeric type is still unmeasured). Its comment id is ONLY in
//     `destination`'s `target_comment_id`, the one place the capture found a
//     comment id;
//  2. story_type 13, "liked your comment": a comment id in `destination` and
//     media too, but no mention of the account. The shape that proves the
//     mention check is load-bearing now that a comment id alone is common;
//  3. story_type 768, a post like (`media?id=`): media, no comment id;
//  4. story_type 651, a story like (`story_fullscreen?reel_id=`);
//  5. a mention in a CAPTION (story_type CaptionMentionStoryType, also a
//     placeholder): media and the SAME "@" mention as (1), but no comment
//     id.
//
// Media reuses the SAME synthetic shortcode/media-id pairs posts.go already
// pins (computed with Python's base64 running instaloader's algorithm),
// rather than inventing new ones.
//
// The other fixtures below are tolerance cases (a missing shortcode, ids as
// numbers, a story that cannot decode, a failure in a 200), not captures.

const (
	// SelfUsername is the account's own handle: the same one fakeig.GetProfile
	// (fake.go) and the end-to-end tests' default Options.Self already use, so
	// a mentions fixture and the rest of the fake wire agree on who "the
	// account" is.
	SelfUsername = "not_____nora"
	// SelfIGID is the same account's igid (fake.go's GetProfile Viewer, and
	// the tests' Options.Self). The fixture puts it in every story's
	// `logging_context.mentioned_user_ids`, as the capture showed an id there
	// on every story type; the classifier must not read it.
	SelfIGID = "79597894900"

	// MentionStoryType and CaptionMentionStoryType are PLACEHOLDERS, chosen
	// outside every story_type the capture saw (651, 13, 101, 75, 768, 117).
	MentionStoryType        = 900
	CaptionMentionStoryType = 901

	// MentionCommentAuthorIGID/Username are the COMMENTER, never the account
	// itself.
	MentionCommentAuthorIGID     = "500000000001"
	MentionCommentAuthorUsername = "commenter_alpha"
	MentionCommentID             = "17900000000000001"
	// LikedCommentID is the account's OWN comment that story (2) says was
	// liked.
	LikedCommentID = "17900000000000002"
	// MentionNotificationText carries a marker (like posts.go's PostCaption)
	// so a test can prove it stays out of a span or an error string. It is
	// Instagram's OWN notification sentence, not the comment's content — see
	// domain.CommentMention.NotificationText.
	MentionNotificationText = "@" + SelfUsername + " mentioned you in a comment: nice shot e9b3"
)

// NewsInboxJSON has the five stories this file's header lists: the mention
// and the comment like in `new_stories`, the post like, the story like and
// the caption mention in `old_stories`, so a test that reads only one array
// cannot pass.
const NewsInboxJSON = `{
  "counts": {},
  "last_checked": 1700000000.5,
  "priority_stories": [],
  "new_stories": [
    {
      "story_type": 900,
      "args": {
        "indicator_config": "",
        "destination": "clips_home?id=` + CarouselMediaID + `&target_comment_id=` + MentionCommentID + `",
        "rich_text": "{` + MentionCommentAuthorUsername + `|000000|1|user?id=` + MentionCommentAuthorIGID + `} ` + MentionNotificationText + `",
        "text": "` + MentionNotificationText + `",
        "links": [{"start": 0, "end": 15, "type": "user", "id": "` + MentionCommentAuthorIGID + `", "username": "` + MentionCommentAuthorUsername + `"}],
        "profile_id": "` + MentionCommentAuthorIGID + `",
        "profile_name": "` + MentionCommentAuthorUsername + `",
        "profile_image": "https://example.invalid/p/1.jpg",
        "media": [{"id": "` + CarouselMediaID + `_10000001", "image": "https://example.invalid/m/1.jpg", "shortcode": "` + CarouselShortcode + `"}],
        "images": [],
        "media_destination": "media?id=` + CarouselMediaID + `_10000001",
        "logging_context": {"mentioned_user_ids": [` + SelfIGID + `], "mentioned_content_ids": ["` + MentionCommentID + `"], "content_id": "` + MentionCommentID + `"},
        "content_version_id": "1",
        "aggregation_type": "none",
        "timestamp": 1700000123,
        "tuuid": "synthetic-tuuid-1",
        "af_candidate_id": "0",
        "latest_reel_media": "0",
        "latest_reel_seen_time": "0"
      }
    },
    {
      "story_type": 13,
      "type": 1,
      "args": {
        "indicator_config": "",
        "destination": "clips_home?id=` + ImageMediaID + `&target_comment_id=` + LikedCommentID + `",
        "rich_text": "{liker_beta|000000|1|user?id=500000000002} liked your comment: thanks all",
        "text": "liker_beta liked your comment: thanks all",
        "links": [{"start": 0, "end": 10, "type": "user", "id": "500000000002", "username": "liker_beta"}],
        "profile_id": "500000000002",
        "profile_name": "liker_beta",
        "profile_image": "https://example.invalid/p/2.jpg",
        "media": [{"id": "` + ImageMediaID + `_10000001", "image": "https://example.invalid/m/2.jpg", "shortcode": "` + ImageShortcode + `"}],
        "images": [],
        "media_destination": "media?id=` + ImageMediaID + `_10000001",
        "logging_context": {"mentioned_user_ids": [` + SelfIGID + `], "mentioned_content_ids": [], "content_id": "` + LikedCommentID + `"},
        "content_version_id": "1",
        "aggregation_type": "none",
        "timestamp": 1700000200,
        "tuuid": "synthetic-tuuid-2",
        "af_candidate_id": "0",
        "latest_reel_media": "0",
        "latest_reel_seen_time": "0"
      }
    }
  ],
  "old_stories": [
    {
      "story_type": 768,
      "args": {
        "destination": "media?id=` + VideoMediaID + `_10000001",
        "rich_text": "{liker_gamma|000000|1|user?id=500000000003} liked your post.",
        "text": "liker_gamma liked your post.",
        "links": [],
        "profile_id": "500000000003",
        "profile_name": "liker_gamma",
        "profile_image": "https://example.invalid/p/3.jpg",
        "media": [{"id": "` + VideoMediaID + `_10000001", "image": "https://example.invalid/m/3.jpg", "shortcode": "` + VideoShortcode + `"}],
        "logging_context": {"mentioned_user_ids": [], "mentioned_content_ids": [], "content_id": "` + VideoMediaID + `"},
        "timestamp": 1700000300
      }
    },
    {
      "story_type": 651,
      "type": 17,
      "args": {
        "destination": "story_fullscreen?reel_id=10000001",
        "text": "liker_delta liked your story.",
        "profile_id": "500000000004",
        "profile_name": "liker_delta",
        "media": [],
        "logging_context": {"mentioned_user_ids": [], "mentioned_content_ids": [], "content_id": "1"},
        "timestamp": 1700000400
      }
    },
    {
      "story_type": 901,
      "args": {
        "destination": "media?id=` + VideoMediaID + `_10000001",
        "text": "@` + SelfUsername + ` mentioned you in a caption",
        "profile_id": "500000000005",
        "profile_name": "poster_epsilon",
        "media": [{"id": "` + VideoMediaID + `_10000001", "shortcode": "` + VideoShortcode + `"}],
        "logging_context": {"mentioned_user_ids": [` + SelfIGID + `], "mentioned_content_ids": [], "content_id": "` + VideoMediaID + `"},
        "timestamp": 1700000500
      }
    }
  ],
  "continuation_token": 0,
  "subscription": null,
  "is_last_page": true,
  "partition": {},
  "status": "ok"
}`

// NewsInboxNoShortcodeJSON is one comment-mention story whose media item
// carries no "shortcode" key — the shape the route must derive one from
// (domain.MediaID.Shortcode()). Its comment id is in `destination`, as in
// NewsInboxJSON.
const NewsInboxNoShortcodeJSON = `{
  "new_stories": [
    {
      "story_type": 900,
      "args": {
        "text": "` + MentionNotificationText + `",
        "destination": "clips_home?id=` + ImageMediaID + `&target_comment_id=` + MentionCommentID + `",
        "profile_id": "` + MentionCommentAuthorIGID + `",
        "profile_name": "` + MentionCommentAuthorUsername + `",
        "timestamp": 1700000123,
        "media": [{"id": "` + ImageMediaID + `_10000001"}]
      }
    }
  ],
  "old_stories": [],
  "status": "ok"
}`

// UnmeasuredNewsInboxFailJSON is a REST failure carried in an HTTP 200: the
// v1 API's "status": "fail" shape, the same guarantee posts.go's
// UnmeasuredFailIn200JSON pins for FetchMedia.
const UnmeasuredNewsInboxFailJSON = `{"message": "synthetic failure message", "status": "fail"}`

// UnmeasuredNewsInboxNumericIDsJSON is one comment-mention story whose ids
// arrive as JSON NUMBERS (`profile_id`, `comment_id`) rather than strings,
// and whose `timestamp` arrives as a numeric STRING rather than a float —
// the tolerant-parsing cases (`flexID`, `flexTimestamp`). NOT a capture: the
// capture had `profile_id` a string and no `comment_id` key at all; this
// pins the retained `comment_id` source and the tolerance.
const UnmeasuredNewsInboxNumericIDsJSON = `{
  "new_stories": [
    {
      "story_type": 900,
      "args": {
        "text": "` + MentionNotificationText + `",
        "profile_id": 500000000001,
        "profile_name": "` + MentionCommentAuthorUsername + `",
        "timestamp": "1700000123",
        "media": [{"id": "` + CarouselMediaID + `_10000001", "shortcode": "` + CarouselShortcode + `"}],
        "comment_id": 17900000000000001
      }
    }
  ],
  "old_stories": [],
  "status": "ok"
}`

// UnmeasuredNewsInboxUndecodableJSON has one story whose `args.text` is a
// JSON NUMBER where this hypothesis expects a string, so it fails to decode
// under `wireStory` — the shape `undecodableStoryType` exists for — beside
// one ordinary follow story, so a test can prove the undecodable story is
// counted and skipped WITHOUT losing the rest of the read.
const UnmeasuredNewsInboxUndecodableJSON = `{
  "new_stories": [
    {"story_type": 9, "type": 9, "args": {"text": 12345, "profile_id": "1", "profile_name": "x"}},
    {
      "story_type": 2,
      "type": 2,
      "args": {
        "text": "started following you",
        "profile_id": "500000000003",
        "profile_name": "follower_gamma",
        "timestamp": 1700000300
      }
    }
  ],
  "old_stories": [],
  "status": "ok"
}`
