package fakeig

// GET /mentions' upstream: Instagram web's notifications panel
// (`GET /api/v1/news/inbox/`, UNMEASURED; see gateway/instameow/mentions.go).
//
// UNMEASURED. No live response has been recorded. Built from the shape the
// task's own hypothesis describes (top-level `new_stories`/`old_stories`,
// each story's `story_type`/`type`/`args`), not from captured bytes — the
// live probe replaces this fixture with what Instagram actually sends.
//
// The four stories in UnmeasuredNewsInboxJSON are the four shapes a
// comment-mention route must tell apart:
//
//  1. a comment that DOES mention the account: a comment id, media, and an
//     "@" token naming the account;
//  2. a like: no comment id, no mention, but media (the post that was liked);
//  3. a follow: no media at all;
//  4. a mention in a CAPTION rather than a comment: media and the SAME "@"
//     mention as (1), but no comment id — the shape that proves the comment
//     id check is load-bearing and not redundant with the media+mention
//     check.
//
// Media reuses the SAME synthetic shortcode/media-id pairs posts.go already
// pins (computed with Python's base64 running instaloader's algorithm),
// rather than inventing new ones.

const (
	// SelfUsername is the account's own handle: the same one fakeig.GetProfile
	// (fake.go) and the end-to-end tests' default Options.Self already use, so
	// a mentions fixture and the rest of the fake wire agree on who "the
	// account" is.
	SelfUsername = "not_____nora"

	// MentionCommentAuthorIGID/Username are the COMMENTER, never the account
	// itself.
	MentionCommentAuthorIGID     = "500000000001"
	MentionCommentAuthorUsername = "commenter_alpha"
	MentionCommentID             = "17900000000000001"
	// MentionCommentText carries a marker (like posts.go's PostCaption) so a
	// test can prove it stays out of a span or an error string.
	MentionCommentText = "@" + SelfUsername + " mentioned you in a comment: nice shot e9b3"
)

// UnmeasuredNewsInboxJSON has all four shapes (see this file's header): a
// comment mention and a like in `new_stories`, a follow and a caption
// mention (no comment id) in `old_stories` — so a test that reads only one
// array cannot pass.
const UnmeasuredNewsInboxJSON = `{
  "new_stories": [
    {
      "story_type": 3,
      "type": 3,
      "args": {
        "text": "` + MentionCommentText + `",
        "profile_id": "` + MentionCommentAuthorIGID + `",
        "profile_name": "` + MentionCommentAuthorUsername + `",
        "timestamp": 1700000123,
        "media": [{"id": "` + CarouselMediaID + `_10000001", "shortcode": "` + CarouselShortcode + `"}],
        "comment_id": "` + MentionCommentID + `"
      }
    },
    {
      "story_type": 1,
      "type": 1,
      "args": {
        "text": "liked your photo",
        "profile_id": "500000000002",
        "profile_name": "liker_beta",
        "timestamp": 1700000200,
        "media": [{"id": "` + ImageMediaID + `_10000001", "shortcode": "` + ImageShortcode + `"}]
      }
    }
  ],
  "old_stories": [
    {
      "story_type": 2,
      "type": 2,
      "args": {
        "text": "started following you",
        "profile_id": "500000000003",
        "profile_name": "follower_gamma",
        "timestamp": 1700000300
      }
    },
    {
      "story_type": 4,
      "type": 4,
      "args": {
        "text": "@` + SelfUsername + ` mentioned you in a caption",
        "profile_id": "500000000004",
        "profile_name": "poster_delta",
        "timestamp": 1700000400,
        "media": [{"id": "` + VideoMediaID + `_10000001", "shortcode": "` + VideoShortcode + `"}]
      }
    }
  ],
  "status": "ok"
}`

// UnmeasuredNewsInboxNoShortcodeJSON is one comment-mention story whose
// media item carries no "shortcode" key — the shape the route must derive
// one from (domain.MediaID.Shortcode()).
const UnmeasuredNewsInboxNoShortcodeJSON = `{
  "new_stories": [
    {
      "story_type": 3,
      "type": 3,
      "args": {
        "text": "` + MentionCommentText + `",
        "profile_id": "` + MentionCommentAuthorIGID + `",
        "profile_name": "` + MentionCommentAuthorUsername + `",
        "timestamp": 1700000123,
        "media": [{"id": "` + ImageMediaID + `_10000001"}],
        "comment_id": "` + MentionCommentID + `"
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
