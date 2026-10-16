package httptransport

// The response shapes, and the READ routes that answer about people rather
// than about the bridge: a thread's members, an account, its story, its
// posts and reels.
//
// Every shape here is built from DOMAIN types (internal/domain), never from
// Instagram's wire structs, which stay inside internal/gateway/instameow
// (design/instagram-adoption/16 §Layers). This is where the contract the
// Python side reads is decided, once.
//
// No success body carries an `ok` field (P14, 16 §Resources: "success is the
// resource"). The STATUS says whether a request succeeded; a failure is the
// envelope in errors.go. The bodies here lost `ok` in P14 and nothing else.

import (
	"fmt"

	"instagram-bridge/internal/domain"
)

// --- small shapes -------------------------------------------------------------

// selfBody is /health's `self`: the bridge's own account in all three spaces.
type selfBody struct {
	FBID     string `json:"fbid"`
	IGID     string `json:"igid"`
	Username string `json:"username"`
}

// mentionBody is one outbound mention on a text message
// (`POST /threads/{thread_fbid}/messages`). Only fbid/offset/length are read:
// a caller cannot ask for an @everyone through it.
type mentionBody struct {
	FBID   string `json:"fbid"`
	Offset int    `json:"offset"`
	Length int    `json:"length"`
}

// sentBody is a message's 200: the message Instagram accepted AND named.
//
// `id` is never empty on a 200: a send Instagram accepted without naming the
// message is a 502 (the gateway's errNoMessageID), because a message Nora
// cannot name she can neither react to, unsend, nor quote. `timestamp_ms` is
// the platform's own sent-timestamp, the field Nora's sender reads (it is
// what a later reply's quote resolves against), and it is OMITTED rather than
// sent as 0 when Instagram gave none: Nora records a 0 as a real timestamp
// and an absent one as unknown.
type sentBody struct {
	ID          string `json:"id"`
	TimestampMS int64  `json:"timestamp_ms,omitempty"`
}

// droppedEvent is the one SSE notice /events itself writes: how many events
// this bridge received and could not deliver.
type droppedEvent struct {
	Kind         string `json:"kind"`
	DroppedTotal int    `json:"dropped_total"`
}

const kindDropped = "dropped"

// --- GET /threads/{thread_fbid}/members ---------------------------------------

// rosterMember is one account in the thread. `fbid` is the only field any
// consumer matches on today; the rest are carried because the read already has
// them and an operator debugging «who is this unmapped account?» from a log
// line has nothing else to go on.
//
// `username` is EXPLICITLY not an identity. It is renameable and on Instagram
// the freed name can be taken by somebody else, so matching on it would unmap
// a person on a rename and resolve a stranger AS them — identity.example.toml
// says the same thing at the other end of the wire.
type rosterMember struct {
	FBID     string `json:"fbid"`
	IGID     string `json:"igid"`
	Username string `json:"username"`
	FullName string `json:"full_name"`
}

// rosterBody is the 200 shape. `members` is never null and never empty on a
// 200 (the use case refuses a short or empty read), so a consumer that sees a
// 200 has a list it can act on.
type rosterBody struct {
	RoomID string `json:"room_id"`
	Title  string `json:"title"`
	// IsGroup is passed through rather than enforced. The bridge is transport;
	// whether a 1:1 thread is admitted at all is Nora's policy decision and is
	// already made in `nora.platform.instagram` (deny-by-default this phase).
	IsGroup bool           `json:"is_group"`
	Members []rosterMember `json:"members"`
}

func rosterOf(room string, t domain.Thread) rosterBody {
	members := make([]rosterMember, 0, len(t.Members))
	for _, m := range t.Members {
		members = append(members, rosterMember(m))
	}
	return rosterBody{RoomID: room, Title: t.Title, IsGroup: t.IsGroup, Members: members}
}

// --- GET /accounts/{igid} -----------------------------------------------------

// profileBody is the 200 shape.
//
// `following`, `blocking` and `is_restricted` are forwarded from
// `FriendshipStatus` rather than collapsed into one `allowed` boolean here.
// The bridge is transport: whether `blocking` should also stop a read is
// Nora's policy call, and it is made in Python where the rest of the boundary
// lives. A bridge that pre-decided it would hide the reason from the layer
// that has to explain itself to a person.
type profileBody struct {
	IGID     string `json:"igid"`
	Username string `json:"username"`
	FullName string `json:"full_name"`
	// FBID is `interop_messaging_user_fbid` — the MESSAGING id, carried so a
	// caller holding one id space can join to the other without a second
	// round trip. It is 0 for an account the messaging graph has no row for,
	// which is normal for a public account Nora has never had a thread with;
	// that is a missing join key, NOT a failed read, so it does not refuse.
	FBID string `json:"fbid"`
	// Following is the FOLLOWED half of the gate: does NORA follow them.
	// Note the direction — this is not "do they follow Nora", which is a
	// different field the library does not model and which would be the wrong
	// question anyway: being followed BY someone grants no access to them.
	Following    bool `json:"following"`
	Blocking     bool `json:"blocking"`
	IsRestricted bool `json:"is_restricted"`
	IsVerified   bool `json:"is_verified"`
	// HasStory is `latest_reel_media != 0` — a timestamp, not a count, and the
	// only "is there a story up right now" signal on this query. Exposed so a
	// caller can skip a story fetch entirely rather than fetch-then-find-empty,
	// which matters because a story read is a deliberate act: the social
	// stream's poller reads one only when this probe moved
	// (design/cognition-loop/22-social-stream.md §3).
	HasStory bool `json:"has_story"`
	// LatestReelMedia is the raw value behind HasStory, forwarded because it
	// is a timestamp and a caller comparing it against
	// `reel_media_seen_timestamp` can tell "has a story" from "has a story
	// Nora has not seen". Nothing does that yet.
	LatestReelMedia int `json:"latest_reel_media"`
	// Extra is `User.Unrecognized` verbatim: every key on the wire that the
	// library does not model. It exists for `is_private` specifically (see the
	// file header) and stays afterwards, because the alternative is a bridge
	// change every time a gate needs a field Instagram was already sending.
	//
	// It is NOT a stable contract. A consumer reading a key out of here is
	// reading something nothing guarantees will be there next week, and should
	// treat an absent key as unknown rather than as false.
	Extra map[string]any `json:"extra,omitempty"`

	// --- the SOCIAL half ----------------------------------------------------
	//
	// `web_profile_info`'s fields, merged into this account rather than given a
	// path of their own (social_profile.go says why). A SECOND upstream call,
	// so it fails independently — and `SocialAvailable` is what keeps that
	// failure honest: without it, a 429 and a genuinely empty biography are the
	// same empty string, and «this account has no bio» would be asserted from a
	// rate limit. R-27's three states, one field down.
	SocialAvailable bool `json:"social_available"`
	// SocialError is why the social half is missing, for an operator. Present
	// only when `SocialAvailable` is false.
	SocialError string         `json:"social_error,omitempty"`
	Social      *socialProfile `json:"social,omitempty"`
}

// socialProfile is the named subset of `data.user`, chosen from a real
// measurement rather than guessed ahead of it.
//
// The prior version of this type returned raw key LISTS instead of fields,
// because what the document carries was the open question. It has now been
// asked: `data.user` on this account holds 65 keys (measured 2026-09-22,
// captured whole via a temporarily uncapped response dump). Most are display
// plumbing this bridge has no caller for — `ring_creator_metadata`,
// `regulated_news_in_locations`, `text_post_app_badge_label` — and are left
// out on purpose rather than carried as more `Unrecognized`-style dead
// weight. What is named below is what a caller measured in this bundle
// actually needs: the operator's public-or-followed gate, and the reach and
// freshness signals a social dreamer would read before deciding whether
// there is anything new to look at.
type socialProfile struct {
	// IsPrivate is the half of the operator's public-or-followed rule that
	// `GetProfile` does not carry (see the file header). Present here,
	// confirmed on the wire.
	IsPrivate bool `json:"is_private"`
	// FollowedBy is Instagram's own answer to "does this account follow
	// Nora back" — `friendship_status.followed_by` on this query, a
	// different fact from `following` (does Nora follow THEM), which
	// `GetProfile` already carries. Neither implies the other.
	FollowedBy bool `json:"followed_by"`
	// Biography is the account's own words. Empty is a real answer here,
	// not a failure — `SocialAvailable` on the caller is what tells an
	// empty bio from a read that never happened.
	Biography string `json:"biography,omitempty"`
	// ExternalURL is the one link a profile is allowed to carry outside its
	// bio.
	ExternalURL string `json:"external_url,omitempty"`
	// FollowerCount, FollowingCount and MutualFollowersCount are reach
	// signals — a social dreamer comparing two reads apart in time can tell
	// growth from noise without re-fetching anything else.
	FollowerCount        int `json:"follower_count"`
	FollowingCount       int `json:"following_count"`
	MutualFollowersCount int `json:"mutual_followers_count"`
	// MediaCount and TotalClipsCount are POST and REEL counts respectively.
	// A caller diffing these against its own last-seen count learns "has
	// this account posted since I last looked" for the price of one call,
	// with no per-post fetch.
	MediaCount      int `json:"media_count"`
	TotalClipsCount int `json:"total_clips_count"`
	// IsBusiness and IsProfessionalAccount are account-type facts a social
	// dreamer needs to tell a friend's account from a brand's before it
	// spends a "genuine connection" turn on one.
	IsBusiness bool `json:"is_business"`
	// IsProfessionalAccount is a pointer for the same reason Category is:
	// measured `null` on a personal account (2026-09-22), and `null` is not
	// the same fact as "measured false".
	IsProfessionalAccount *bool `json:"is_professional_account,omitempty"`
	// Category is the account's declared category (nil for a personal
	// account with none set) — forwarded as a pointer because "not set" and
	// "set to empty" are different facts on this field.
	Category *string `json:"category,omitempty"`
	// ProfilePicURL is fetchable directly, the same as a story or post CDN
	// url: no cookies, no special case.
	ProfilePicURL string `json:"profile_pic_url,omitempty"`
}

func profileOf(a domain.Account, social domain.SocialRead) profileBody {
	out := profileBody{
		IGID:            a.IGID,
		Username:        a.Username,
		FullName:        a.FullName,
		FBID:            a.FBID,
		Following:       a.Following,
		Blocking:        a.Blocking,
		IsRestricted:    a.IsRestricted,
		IsVerified:      a.IsVerified,
		HasStory:        a.LatestReelMedia != 0,
		LatestReelMedia: a.LatestReelMedia,
		Extra:           a.Extra,
	}
	switch {
	case social.Profile != nil:
		s := socialProfile(*social.Profile)
		out.SocialAvailable, out.Social = true, &s
	case !social.Sent:
		// No request left the bridge: say why, with no status to attach.
		out.SocialError = social.Err.Error()
	default:
		// The STATUS goes with it, because telling a 429 from a challenge page
		// from a parse failure is the whole reason this half is reported. With
		// no response at all it is the status this bridge would have answered.
		status := social.Status
		if status == 0 {
			status = statusForDomainError(social.Err)
		}
		out.SocialError = fmt.Sprintf("%d: %v", status, social.Err)
	}
	return out
}

// --- GET /accounts/{igid}/story -----------------------------------------------

// storyItem is one frame of a story.
type storyItem struct {
	// ID is Instagram's media id (`pk`), carried for provenance. Nothing
	// fetches by it — the url does that.
	ID string `json:"id"`
	// Kind is derived from `media_type` (1 image, 2 video), and defaults to
	// image, because `image_versions2` is populated for BOTH: a video story
	// carries its own cover frame. A frame described as a video would have
	// Nora talk about a clip she only saw one still of.
	Kind string `json:"kind"`
	// URL is the best candidate: the video when there is one and the caller
	// asked for depth, otherwise the largest still. Directly fetchable — the
	// CDN url carries its own signature and needs no cookies, which is what
	// lets `media_fetch` take it with no special case.
	URL string `json:"url"`
	// ThumbnailURL is always the still, even for a video. A caller that only
	// wants to know what a story LOOKS like fetches this and never the clip.
	ThumbnailURL string `json:"thumbnail_url,omitempty"`
	TakenAt      int    `json:"taken_at,omitempty"`
	// ExpiringAt is the natural retention bound and the reason 06's "social
	// reads are not kept" rule has a number behind it: a story is gone in 24
	// hours, so anything Nora kept would outlive its source.
	ExpiringAt int `json:"expiring_at,omitempty"`
	// Caption is the author's own words on the frame, when there are any.
	Caption string `json:"caption,omitempty"`
	// AltText is `accessibility_caption` — Instagram's own description of the
	// image. Free, already on the delta, and often enough to answer a question
	// without any fetch or vision call at all.
	AltText string `json:"alt_text,omitempty"`
	// Location is the name of the place a location sticker tags the frame
	// with, when there is one.
	Location string `json:"location,omitempty"`
}

// storyBody is the 200 shape.
type storyBody struct {
	IGID string `json:"igid"`
	// Found distinguishes "this account has no story up" from "this account
	// has one and Nora may not see it". Both come back as zero items, and
	// reporting the second as the first would state something about a person
	// on the strength of a permission error.
	Found bool `json:"found"`
	// Username is off the reel's own user row, not echoed from the request.
	Username string `json:"username,omitempty"`
	// ExpiringAt is the reel's, which bounds the whole set.
	ExpiringAt int `json:"expiring_at,omitempty"`
	// Seen is Instagram's own cursor: the timestamp of the last frame this
	// account viewed. It is NOT Nora's unread cursor — 10 §"Unread must be
	// Nora's own cursor" is explicit that hers has to be hers — but it is the
	// evidence that a view was recorded, which an operator auditing "what did
	// she look at" needs.
	Seen  int         `json:"seen,omitempty"`
	Items []storyItem `json:"items"`
}

// storyOf is the story's 200 body.
func storyOf(s domain.Story) storyBody {
	items := make([]storyItem, 0, len(s.Items))
	for _, it := range s.Items {
		items = append(items, storyItem(it))
	}
	return storyBody{
		IGID: s.IGID, Found: s.Found, Username: s.Username,
		ExpiringAt: s.ExpiringAt, Seen: s.Seen, Items: items,
	}
}

// --- GET /accounts/{igid}/items -----------------------------------------------

// timelineItem is one post or reel as the timeline lists it.
type timelineItem struct {
	// Code is the shortcode `GET /posts/{shortcode}` reads the item by.
	Code string `json:"code"`
	PK   string `json:"pk"`
	// TakenAt is Unix seconds.
	TakenAt int64 `json:"taken_at"`
	// Kind is "reel" for Instagram's `product_type: clips`, else "post".
	Kind      string `json:"kind"`
	MediaType int    `json:"media_type"`
	// Caption is the author's own words, "" when there are none.
	Caption       string `json:"caption"`
	CarouselCount int    `json:"carousel_count"`
	// Collab is a post another account owns and this one co-authored.
	Collab bool `json:"collab"`
	// Items is the media the listing carries, in `GET /posts/{shortcode}`'s
	// item shape; never null, empty when the node had no usable url.
	Items []postItem `json:"items"`
	// AltText is Instagram's own description of the image, "" when none.
	AltText string `json:"alt_text"`
	// Location is the name of the place the item is tagged with, "" when
	// none.
	Location string `json:"location"`
}

// itemsBody is the 200 shape, on the list conventions `GET /accounts`
// already uses: `items` never null, `next_cursor` null at the end.
type itemsBody struct {
	Items      []timelineItem `json:"items"`
	NextCursor *string        `json:"next_cursor"`
}

func itemsOf(p domain.TimelinePage) itemsBody {
	items := make([]timelineItem, 0, len(p.Items))
	for _, it := range p.Items {
		items = append(items, timelineItem{
			Code: it.Code, PK: it.PK, TakenAt: it.TakenAt, Kind: it.Kind,
			MediaType: it.MediaType, Caption: it.Caption,
			CarouselCount: it.CarouselCount, Collab: it.Collab,
			Items: postItemsOf(it.Items), AltText: it.AltText, Location: it.Location,
		})
	}
	return itemsBody{Items: items, NextCursor: p.NextCursor}
}
