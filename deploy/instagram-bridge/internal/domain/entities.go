package domain

// The entities a read or a send returns. They are what crosses from the
// gateway to the HTTP layer; Instagram's wire structs never do
// (design/instagram-adoption/16 §Layers). No JSON tags here: the response
// shapes are the HTTP layer's decision, made once, in its DTOs.
//
// Ids that came BACK from Instagram are carried as the strings Instagram sent,
// not re-parsed into value objects: they are output, not input, and a parse
// that refused an odd value would turn a readable answer into a failed one.

// SessionState is the `session` field of GET /health. The contract
// (03-transport-contract.md) fixes these three spellings. It is deliberately
// NOT a status code: a checkpointed session still answers /health with 200 so
// the container healthcheck stays green while a human deals with the
// challenge.
type SessionState string

const (
	StateAuthenticated SessionState = "authenticated"
	StateCheckpointed  SessionState = "checkpointed"
	StateLoggedOut     SessionState = "logged_out"
)

// Self is the bridge's own account, in all three id spaces.
type Self struct {
	// FBID is GetOwnFBID(): the id a mention carries, and the ONLY one "was I
	// tagged?" may compare against.
	FBID string
	// IGID is the public id. It is NOT a messaging id and must never be
	// compared to a DM mention. (The news inbox's
	// `logging_context.mentioned_user_ids` is NOT compared against it: the
	// live capture showed one id there on every story type, likes included.)
	IGID     string
	Username string
}

// Session is what the bridge can say about its own session at one instant.
type Session struct {
	State     SessionState
	Detail    string
	Connected bool
	Seq       int64
	Self      Self
	// HasClient is whether a client exists to serve a request. False while
	// the supervisor is (re)building one, which is retryable (503), not a
	// state a human has to clear (409).
	HasClient bool
}

// Account is one Instagram account as GetProfile reads it: who it is and the
// follow facts that gate reading its content.
type Account struct {
	IGID     string
	Username string
	FullName string
	// FBID is the messaging id, or "" for an account the messaging graph has
	// no row for (a public account never messaged). A missing join key, not
	// a failed read.
	FBID            string
	Following       bool
	Blocking        bool
	IsRestricted    bool
	IsVerified      bool
	LatestReelMedia int
	// Extra is every wire key the library does not model, verbatim. It exists
	// for `is_private`; it is not a stable contract.
	Extra map[string]any
}

// AccountSummary is one hit from `GET /accounts?query=` — the account-search
// resource (16 §Resources, 17 P9, P11). It is the OmniPicker's own shape
// (`slidetypes.SearchResult`, behind `SearchUsers` /
// `IGDOmniPickerSearchResultsListQuery`), not the fuller GetProfile read that
// backs Account above.
//
// There is NO IsPrivate field here, unlike the task that named this entity
// expected: `slidetypes.SearchResult` carries `interop_messaging_user_fbid`,
// `username`, `full_name`, `pk`, `profile_pic_url` and `is_verified` and
// nothing else — measured against the library's own struct
// (go.mau.fi/mautrix-meta/pkg/instameow/slidetypes/user.go). `is_private` is
// on `PolarisProfilePageContentQuery`'s `data.user` (SocialProfile below),
// a different document. Leaving it off is the "do not invent fields" rule
// applied to a field the search genuinely does not return; a bool defaulting
// to false here would read as "measured public" for every hit.
type AccountSummary struct {
	IGID     string
	Username string
	FullName string
	// FBID is the messaging id, or "" when the search has no row for it — a
	// missing join key, not a failed read, same as Account.FBID above.
	FBID       string
	IsVerified bool
}

// SocialProfile is the named subset of `PolarisProfilePageContentQuery`'s
// `data.user` that GetProfile's own response type throws away.
type SocialProfile struct {
	IsPrivate             bool
	FollowedBy            bool
	Biography             string
	ExternalURL           string
	FollowerCount         int
	FollowingCount        int
	MutualFollowersCount  int
	MediaCount            int
	TotalClipsCount       int
	IsBusiness            bool
	IsProfessionalAccount *bool
	Category              *string
	ProfilePicURL         string
}

// SocialRead is the SECOND, independent upstream call behind an account read.
// It fails on its own, so its outcome is carried rather than collapsed: a 429
// and a genuinely empty biography must not become the same empty string.
type SocialRead struct {
	// Profile is nil when the read failed; Err says why.
	Profile *SocialProfile
	Err     error
	// Sent is whether a request left the bridge at all. False means there was
	// no client able to issue one.
	Sent bool
	// Status is the HTTP status Instagram answered with (200 for a failure
	// inside the body), or 0 when the request got no response.
	Status int
}

// Story is one account's current story. Found separates "no story up" from
// "a story Nora may not see": both come back with zero items.
type Story struct {
	IGID       string
	Found      bool
	Username   string
	ExpiringAt int
	Seen       int
	Items      []StoryItem
}

// StoryItem is one frame.
type StoryItem struct {
	ID           string
	Kind         string
	URL          string
	ThumbnailURL string
	TakenAt      int
	ExpiringAt   int
	Caption      string
	AltText      string
	// Location is the name of the place a location sticker tags the frame
	// with, "" when it carries none. The author's published words: like a
	// caption, it must not enter a span, a log line or an error.
	Location string
}

// TimelinePage is one page of an account's posts and reels, newest first
// (`GET /accounts/{igid}/items`). NextCursor is nil at the end of the
// timeline, and is otherwise the opaque `after` for the next page.
type TimelinePage struct {
	Items      []TimelineItem
	NextCursor *string
}

// TimelineItem is one post or reel as the timeline lists it: enough to key
// it and to record it, media included, without a read of its own.
type TimelineItem struct {
	Code string
	PK   string
	// TakenAt is Unix seconds, as Instagram sends it.
	TakenAt int64
	// Kind is "reel" for `product_type: clips`, otherwise "post".
	Kind      string
	MediaType int
	// Caption is the author's free text, "" when there is none. It must not
	// enter a span, a log line or an error (16 §Tracing's never-list).
	Caption       string
	CarouselCount int
	// Collab is a collaboration post another account owns and this one
	// co-authored: on this account's grid, under the owner's id.
	Collab bool
	// Items is the media the listing itself carries, one entry per image or
	// video (every child of a carousel, in order); empty when the node had no
	// usable url. URLs must not enter a span, a log line or an error.
	Items []PostItem
	// AltText is `accessibility_caption`, Instagram's description of the
	// image, "" when there is none.
	AltText string
	// Location is the name of the place the item is tagged with, "" when it
	// carries none. The author's published words: like a caption, it must
	// not enter a span, a log line or an error.
	Location string
}

// Thread is one group thread's membership, never short and never empty.
type Thread struct {
	Title   string
	IsGroup bool
	Members []Member
}

// Member is one account in a thread, in BOTH id spaces.
type Member struct {
	FBID     string
	IGID     string
	Username string
	FullName string
}

// Mention is one span of tagged text on an outbound message. FBID stays the
// caller's string: it is parsed where the wire needs a number, with the
// acceptance the send path has always had.
type Mention struct {
	FBID   string
	Offset int
	Length int
}

// Upload is one attachment to send.
type Upload struct {
	Filename  string
	MimeType  string
	Data      []byte
	VoiceNote bool
}

// Sent is a message Instagram accepted AND named.
type Sent struct {
	ID          string
	TimestampMS int64
}

// Post is one feed post or reel, read by its shortcode (`GET
// /posts/{shortcode}`, 17 P12): who posted it, what they wrote, when, and
// EVERY item in it, in the author's order. A carousel is the reason this read
// exists: a shared post carries only its cover thumbnail, so without this a
// ten-image carousel reads as one image.
//
// Only fields `FetchMedia`'s response carries (responses.FetchMediaResponse):
// `user.username`, `caption.text`, `taken_at`, and per item the chosen
// `image_versions2` / `video_versions` candidate and `video_duration`. No ids
// cross into it: the shortcode is what the caller asked with, and the numeric
// ids are the gateway's business.
type Post struct {
	Shortcode string
	Author    string
	// Caption is the author's free text. It must not enter a span, a log
	// line or an error (16 §Tracing's never-list).
	Caption string
	// TakenAt is Unix seconds, as Instagram sends it.
	TakenAt int64
	Items   []PostItem
}

// PostItem is one image or video of a post. For a video, URL is the clip and
// Width/Height are the clip's; DurationSeconds is set only when Instagram
// sent a duration.
type PostItem struct {
	Kind            string
	URL             string
	Width           int
	Height          int
	DurationSeconds *float64
}

// CommentMention is one comment on a post that @mentioned this account, read
// from Instagram's activity/notification feed (`GET /mentions`, UNMEASURED;
// see gateway/instameow/mentions.go). It is Instagram's PUBLIC comment
// surface, not a DM: no thread id, no message id.
type CommentMention struct {
	// StoryType is Instagram's own numeric classification of the
	// notification. Nothing has measured which value(s) a real
	// comment-mention carries yet, so this is recorded on every mention the
	// route DOES classify, for the live probe to pin down.
	StoryType int
	// At is Unix seconds, truncated from Instagram's `args.timestamp`
	// (a float).
	At int64
	// AuthorIGID/AuthorUsername are the COMMENTER, never this account.
	AuthorIGID     string
	AuthorUsername string
	// MediaID is the post's numeric pk. MediaShortcode is Instagram's own
	// when the payload carries one, otherwise derived from MediaID
	// (domain.MediaID.Shortcode()).
	MediaID        string
	MediaShortcode string
	CommentID      string
	// NotificationText is Instagram's OWN notification sentence
	// ("X mentioned you in a comment: ..."), not the comment's content —
	// `args.text`/`args.rich_text` on this feed is the notification's
	// wording, never a clean copy of what the commenter typed. Reading the
	// real comment text is phase 2's job, once a comment-read mechanism
	// exists; this route only knows a comment happened and roughly what
	// Instagram said about it. It must not enter a span, a log line or an
	// error (16 §Tracing's never-list) — it reaches the 200 body and nowhere
	// else, the same rule Post.Caption already carries.
	NotificationText string
}
