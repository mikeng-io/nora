package main

// Every response body Nora reads, pinned as files BOTH sides read
// (design/instagram-adoption/17 P4, P14).
//
// Nora's Python side is tested against `tests/fixtures/instagram_bridge/*.json`:
// the social client (`nora/infra/social/instagram_client.py`) for the account,
// story, items and search routes, and the platform adapter (`nora/platform/instagram/`:
// send, react, roster) for the thread routes, whose fake bridge serves exactly
// the routes these fixtures name. This file is what makes those bytes the
// bridge's: each fixture is GENERATED here by the real handler, through the
// real router, over the same fakes the rest of this package's tests use. A
// change to a body, a status mapping or the error envelope therefore fails
// here first, and the committed fixture it forces is what the Python tests
// then read. Neither side can drift alone.
//
// A write route's fixture also carries the REQUEST it was generated from
// (`request`), and what that request made the bridge hand INSTAGRAM
// (`upstream`: the calls the fake Instagram client received, with the fields
// the bridge read out of the request, and never the message text). The Python
// adapter's tests assert the adapter sends exactly `request`; this file
// asserts the handler turns `request` into exactly `upstream`. A renamed
// request field therefore fails here even when the response is unchanged: a
// `remove` the handler no longer reads still answers 200 {"confirmed": true},
// but places a reaction where one was meant to be retracted, and `upstream`
// says `created` instead of `deleted`.
//
// Every value is SYNTHETIC — made-up igids, usernames, bios and CDN paths in
// the placeholder ranges `identity.example.toml` uses. Nothing is copied
// from the live account (AGENTS.md: fixtures are generated, never pasted).
//
// Regenerate after an intended contract change, then review the diff:
//
//	go test ./cmd/instagram-bridge -run TestContractFixtures -update
//
// (from deploy/instagram-bridge; the package goes BEFORE -update, which only
// this package defines)

import (
	"bytes"
	"encoding/json"
	"flag"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"

	gateway "instagram-bridge/internal/gateway/instameow"
	"instagram-bridge/internal/gateway/instameow/fakeig"

	"go.mau.fi/mautrix-meta/pkg/instameow/slidetypes"
	"go.mau.fi/mautrix-meta/pkg/messagix/httpclient"
	"go.mau.fi/mautrix-meta/pkg/messagix/types"
)

var updateFixtures = flag.Bool("update", false,
	"rewrite tests/fixtures/instagram_bridge from the handlers")

// fixtureDir is the repository's shared fixture directory, relative to this
// package (cmd/instagram-bridge).
const fixtureDir = "../../../../tests/fixtures/instagram_bridge"

// skipOutsideTheRepo skips the fixture checks in ONE place only: the image
// build, whose context is this directory, so `tests/fixtures` is not in it.
// The Dockerfile says so explicitly with IG_BRIDGE_IMAGE_BUILD=1; anywhere
// else a missing fixture directory is still a failure, never a skip.
func skipOutsideTheRepo(t *testing.T) {
	t.Helper()
	if *updateFixtures || os.Getenv("IG_BRIDGE_IMAGE_BUILD") != "1" {
		return
	}
	if _, err := os.Stat(fixtureDir); err != nil {
		t.Skipf("image build: %s is outside the build context", fixtureDir)
	}
}

// contractFixture is the on-disk shape. `route`, `path` and `request` (a
// write route's JSON body, absent on a read) document what was asked;
// `status` and `body` are what the bridge answered.
type contractFixture struct {
	Route    string          `json:"route"`
	Path     string          `json:"path"`
	Request  json.RawMessage `json:"request,omitempty"`
	Upstream json.RawMessage `json:"upstream,omitempty"`
	Status   int             `json:"status"`
	Body     json.RawMessage `json:"body"`
}

// upstreamCall is one call the fake Instagram client received, with the
// fields the bridge filled from the caller's request. Text is recorded only as
// its length: message text never enters a fixture any more than a span.
type upstreamCall struct {
	Call           string            `json:"call"`
	ThreadID       string            `json:"thread_id,omitempty"`
	TextLength     *int              `json:"text_length,omitempty"`
	Mentions       []upstreamMention `json:"mentions,omitempty"`
	ReplyTo        string            `json:"reply_to_message_id,omitempty"`
	MessageID      string            `json:"message_id,omitempty"`
	Emoji          string            `json:"emoji,omitempty"`
	ReactionStatus string            `json:"reaction_status,omitempty"`
	Filename       string            `json:"filename,omitempty"`
	MimeType       string            `json:"mimetype,omitempty"`
	Bytes          *int              `json:"bytes,omitempty"`
	VoiceClip      *bool             `json:"voice_clip,omitempty"`
	Typing         *bool             `json:"typing,omitempty"`
}

type upstreamMention struct {
	FBID   int64 `json:"fbid"`
	Offset int   `json:"offset"`
	Length int   `json:"length"`
}

// upstreamOf is what a request made the bridge send Instagram, in a FIXED
// call order (the fake records per call kind, not a timeline). An empty list
// is a fact too: nothing reached Instagram, which is what every refusal
// before the session gate, and the gate itself, must produce.
func upstreamOf(f *fakeIG) []upstreamCall {
	calls := []upstreamCall{}
	for _, id := range f.GetThreadIDs {
		calls = append(calls, upstreamCall{Call: "GetThread", ThreadID: id})
	}
	if f.TextReq != nil {
		n := len([]rune(f.TextReq.Text.Value))
		c := upstreamCall{Call: "SendMessage", TextLength: &n}
		if f.TextReq.IGThreadIGID != nil {
			c.ThreadID = *f.TextReq.IGThreadIGID
		}
		for _, m := range f.TextReq.Mentions {
			c.Mentions = append(c.Mentions, upstreamMention{FBID: m.FBID, Offset: m.Offset, Length: m.Length})
		}
		if f.TextReq.ReplyToMessageID != nil {
			c.ReplyTo = *f.TextReq.ReplyToMessageID
		}
		calls = append(calls, c)
	}
	for _, u := range f.Uploads {
		n, voice := len(u.MediaData), u.IsVoiceClip
		calls = append(calls, upstreamCall{
			Call: "Upload", Filename: u.Filename, MimeType: u.MimeType, Bytes: &n, VoiceClip: &voice,
		})
	}
	if f.MediaReq != nil {
		c := upstreamCall{Call: "SendMedia", ThreadID: f.MediaReq.ThreadID}
		if f.MediaReq.ReplyToMessageID != nil {
			c.ReplyTo = *f.MediaReq.ReplyToMessageID
		}
		calls = append(calls, c)
	}
	if f.ReactReq != nil {
		in := f.ReactReq.Input
		calls = append(calls, upstreamCall{
			Call: "SendReaction", ThreadID: in.ThreadID, MessageID: in.MessageID,
			Emoji: in.Emoji, ReactionStatus: string(in.ReactionStatus),
		})
	}
	if f.Unsend != nil {
		calls = append(calls, upstreamCall{
			Call: "UnsendMessage", ThreadID: f.Unsend.SendData.ThreadID, MessageID: f.Unsend.MessageID,
		})
	}
	for i, id := range f.Typing {
		typing := f.TypingValues[i]
		calls = append(calls, upstreamCall{Call: "SetTyping", ThreadID: id, Typing: &typing})
	}
	return calls
}

const (
	synthIGID     = "10000001"
	synthFBID     = 100000000000001
	synthUsername = "example_friend"
	// The thread routes' synthetic room and message (fakeig treats this
	// thread exactly like the measured one).
	synthThread  = fakeig.SynthThreadShort
	synthMessage = "mid.$synthetic0001"
	synthSentMS  = 1700000000123
)

// synthMembers is the synthetic room's two other accounts, in both id spaces.
func synthMembers() []*slidetypes.User {
	return []*slidetypes.User{
		{InteropMessagingUserFBID: synthFBID, ID: synthIGID, Username: synthUsername, FullName: "Example Friend"},
		{InteropMessagingUserFBID: 100000000000002, ID: "10000002", Username: "example_second_member"},
	}
}

func threadPath(suffix string) string { return "/threads/" + synthThread + suffix }

func synthProfileUser() *slidetypes.User {
	return &slidetypes.User{
		InteropMessagingUserFBID: synthFBID,
		ID:                       synthIGID,
		Username:                 synthUsername,
		FullName:                 "Example Friend",
		LatestReelMedia:          1700000000,
		FriendshipStatus: struct {
			IsRestricted bool `json:"is_restricted"`
			Blocking     bool `json:"blocking"`
			Following    bool `json:"following"`
		}{Following: true},
	}
}

// synthSocialEnvelope is a `PolarisProfilePageContentQuery` answer in the
// shape `parseSocialProfile` reads.
const synthSocialEnvelope = `{"data":{"user":{
	"is_private": true,
	"biography": "Synthetic bio for contract tests.",
	"external_url": "https://example.invalid/",
	"follower_count": 120,
	"following_count": 80,
	"mutual_followers_count": 3,
	"media_count": 42,
	"total_clips_count": 2,
	"is_business": false,
	"is_professional_account": null,
	"category": null,
	"profile_pic_url": "https://scontent.cdninstagram.com/v/t51/synthetic-pic.jpg",
	"friendship_status": {"followed_by": true}
}}}`

// synthGraphQLErrors is the 200-with-`errors` shape the social half refuses.
const synthGraphQLErrors = `{"errors":[{"message":"synthetic field exception","code":1675030,"summary":"Query error"}],"data":null}`

// synthSearchResults is GET /accounts?query='s hit list, in the synthetic
// placeholder ranges the rest of this file uses. No is_private: the search
// this route calls does not carry that field (domain.AccountSummary's doc
// comment says why), so there is nothing to fake for it.
func synthSearchResults() []*slidetypes.SearchResult {
	return []*slidetypes.SearchResult{
		{PK: synthIGID, Username: synthUsername, FullName: "Example Friend", InteropMessagingUserFBID: synthFBID},
		{PK: "10000002", Username: "example_second_result", IsVerified: true},
	}
}

// synthUsernameLookupResults is GET /accounts?username='s raw hits BEFORE the
// exact-match filter (app.LookupAccountByUsername): a fuzzy hit ranks ahead
// of the exact one, the P9 shape (17 §P9: "one exact match was not the first
// result"). A route that ever took items[0] would answer the wrong account
// against this fixture.
func synthUsernameLookupResults() []*slidetypes.SearchResult {
	return []*slidetypes.SearchResult{
		{PK: "10000010", Username: "example_friendlier", FullName: "Not The One"},
		{PK: synthIGID, Username: synthUsername, FullName: "Example Friend", InteropMessagingUserFBID: synthFBID},
	}
}

// synthUsernameLookupAmbiguous is two hits that both match synthUsername
// case-insensitively — data Instagram would never really produce (usernames
// are unique), which is exactly what makes it the ErrUpstreamRejected case.
func synthUsernameLookupAmbiguous() []*slidetypes.SearchResult {
	return []*slidetypes.SearchResult{
		{PK: synthIGID, Username: synthUsername},
		{PK: "10000099", Username: strings.ToUpper(synthUsername)},
	}
}

// synthPK is the n-th synthetic story frame pk, a 9 then n zero-padded to 17
// digits: the values the Python client's tests assert against
// (tests/unit/test_instagram_views.py). Built rather than written so this
// source carries no id-shaped literal, while story_found.json keeps the bytes
// it had when the read was REST.
func synthPK(n int) string { return fmt.Sprintf("9%017d", n) }

// synthStoryJSON is a `PolarisStoriesV3ReelPageStandaloneQuery` answer in the
// S0 shape (design/cognition-loop/22-social-stream.md §1a): the reel's own
// `expiring_at` null, each frame's its own.
func synthStoryJSON() string {
	return `{"data":{"xdt_api__v1__feed__reels_media":{"reels_media":[{
  "id": "` + synthIGID + `", "reel_type": "user_reel", "seen": 0, "expiring_at": null,
  "user": {"pk": "` + synthIGID + `", "username": "` + synthUsername + `"},
  "items": [
    {"pk": "` + synthPK(1) + `", "taken_at": 1700000000, "expiring_at": 1700086400, "media_type": 1,
     ` + synthStoryWords + `
     "image_versions2": {"candidates": [{"width": 1080, "height": 1920, "url": "https://scontent.cdninstagram.com/v/t51/synthetic-1.jpg?oh=00_A&oe=0"}]}},
    {"pk": "` + synthPK(2) + `", "taken_at": 1700003600, "expiring_at": 1700086400, "media_type": 2, "caption": null,
     "image_versions2": {"candidates": [{"width": 1080, "height": 1920, "url": "https://scontent.cdninstagram.com/v/t51/synthetic-2-cover.jpg?oh=00_B&oe=0"}]},
     "video_versions": [{"width": 1080, "url": "https://instagram.fxxx1-1.fbcdn.net/v/t50/synthetic-2.mp4?oh=00_C&oe=0"}]},
    ` + synthStoryNoPK + `
  ]}]}}}`
}

// The author's words and Instagram's description of the frame. The bridge
// forwards both; the Python client must not (a story result is a reference,
// never content), and these are the values it asserts against.
const synthStoryWords = `"caption": {"text": "synthetic caption text"}, "accessibility_caption": "synthetic alt text",`

// A frame with NO `pk`. `storyFrame` then falls back to `id`, which Instagram
// shapes as `<pk>_<owner igid>`, so the frame's `id` carries the story
// OWNER's igid. The Python client must never hand that to the model
// (design/instagram-adoption/18, R-18); this row is what it is tested against.
var synthStoryNoPK = `{"id": "` + synthPK(3) + `_` + synthIGID + `", "taken_at": 1700007200, "expiring_at": 1700086400, "media_type": 1,
     "image_versions2": {"candidates": [{"width": 1080, "height": 1920, "url": "https://scontent.cdninstagram.com/v/t51/synthetic-3.jpg?oh=00_D&oe=0"}]}}`

// synthStaleQuery is a GraphQL error naming the document: a rotated doc_id.
// UNMEASURED wording (docs.go's staleMarkers).
const synthStaleQuery = `{"errors":[{"message":"PersistedQueryNotFound","code":1675002,"summary":"Query not found"}]}`

// synthTimelineJSON is one `PolarisProfilePostsTabContentQuery_connection`
// page for the synthetic account: a reel and a carousel post, more after it
// when `more` is set.
func synthTimelineJSON(more bool) string {
	pageInfo := `{"end_cursor": null, "has_next_page": false}`
	if more {
		pageInfo = `{"end_cursor": "synthetic-cursor-1", "has_next_page": true}`
	}
	return `{"data":{"xdt_api__v1__feed__user_timeline_graphql_connection":{"edges":[
  {"node": {"code": "SynthReel01", "pk": "2001", "id": "2001_` + synthIGID + `", "user": {"pk": "` + synthIGID + `"}, "taken_at": 1700000300,
    "caption": {"text": "synthetic reel caption"}, "media_type": 2, "product_type": "clips"}},
  {"node": {"code": "SynthPost02", "pk": "2002", "id": "2002_` + synthIGID + `", "user": {"pk": "` + synthIGID + `"}, "taken_at": 1700000200,
    "caption": null, "media_type": 8, "product_type": "carousel_container", "carousel_media_count": 3}}
], "page_info": ` + pageInfo + `}}}`
}

// contractCase is one fixture: how to set the bridge up, and what to ask.
// `method` defaults to GET; `request` is a write route's JSON body.
type contractCase struct {
	file    string
	route   string
	path    string
	method  string
	request any
	// upstream records what reached the fake Instagram client into the
	// fixture. Set for every thread route (writeContractCases).
	upstream bool
	fake     func() *fakeIG
	social   func() gateway.GraphQLIssuer // nil: the default issuer resolution
	opts     []bridgeOpt                  // optional session adjustments
}

// The request bodies the write fixtures are generated from. The Python
// adapter's tests build the same message and assert it sends exactly these.
var (
	synthTextRequest = map[string]any{
		"text":     "@example_friend synthetic message text",
		"mentions": []map[string]any{{"fbid": "100000000000001", "offset": 0, "length": 15}},
		"reply_to": "mid.$syntheticparent",
	}
	synthMediaRequest = map[string]any{
		"media": map[string]any{
			"data_base64": "c3ludGhldGljIGJ5dGVz", // "synthetic bytes"
			"filename":    "synthetic.png",
			"mimetype":    "image/png",
		},
	}
	synthReactionRequest = map[string]any{"emoji": "❤️", "remove": false}
	synthRemoveRequest   = map[string]any{"emoji": "❤️", "remove": true}
	synthTypingRequest   = map[string]any{"typing": true}
)

// writeContractCases are the thread routes (17 §P14): members, and the four
// writes, each with the success shapes and the failure envelopes Nora's
// adapter decides on.
func writeContractCases() []contractCase {
	sendOK := func() *fakeIG { return &fakeIG{SentTimestampMS: synthSentMS} }
	return []contractCase{
		// --- GET /threads/{thread_fbid}/members ---
		{
			file: "members_ok.json", route: "GET /threads/{thread_fbid}/members", path: threadPath("/members"),
			fake: func() *fakeIG { return &fakeIG{ThreadUsersSet: true, ThreadUsers: synthMembers()} },
		},
		{
			file: "members_checkpointed_409.json", route: "GET /threads/{thread_fbid}/members", path: threadPath("/members"),
			fake: func() *fakeIG { return &fakeIG{} },
			opts: []bridgeOpt{withState("checkpointed", "challenge required")},
		},
		{
			// The room's route lookup failed against Instagram: the kind's
			// status and code, and `stage: thread_lookup`.
			file: "members_thread_lookup_failed_409.json", route: "GET /threads/{thread_fbid}/members", path: threadPath("/members"),
			fake: func() *fakeIG { return &fakeIG{FetchErr: httpclient.ErrCheckpointRequired} },
		},
		{
			file: "members_bad_thread_400.json", route: "GET /threads/{thread_fbid}/members", path: "/threads/not-a-thread/members",
			fake: func() *fakeIG { return &fakeIG{} },
		},
		// --- POST /threads/{thread_fbid}/messages ---
		{
			file: "messages_text_ok.json", route: "POST /threads/{thread_fbid}/messages", path: threadPath("/messages"),
			method: http.MethodPost, request: synthTextRequest, fake: sendOK,
		},
		{
			file: "messages_media_ok.json", route: "POST /threads/{thread_fbid}/messages", path: threadPath("/messages"),
			method: http.MethodPost, request: synthMediaRequest, fake: sendOK,
		},
		{
			// Instagram refused the text as too long: 413, never retried.
			file: "messages_too_long_413.json", route: "POST /threads/{thread_fbid}/messages", path: threadPath("/messages"),
			method: http.MethodPost, request: synthTextRequest,
			fake: func() *fakeIG { return &fakeIG{SendErr: errorString("Message Too Long")} },
		},
		{
			file: "messages_checkpointed_409.json", route: "POST /threads/{thread_fbid}/messages", path: threadPath("/messages"),
			method: http.MethodPost, request: synthTextRequest, fake: sendOK,
			opts: []bridgeOpt{withState("checkpointed", "challenge required")},
		},
		{
			file: "messages_thread_lookup_failed_409.json", route: "POST /threads/{thread_fbid}/messages", path: threadPath("/messages"),
			method: http.MethodPost, request: synthTextRequest,
			fake: func() *fakeIG { return &fakeIG{FetchErr: httpclient.ErrCheckpointRequired} },
		},
		{
			file: "messages_reconnecting_503.json", route: "POST /threads/{thread_fbid}/messages", path: threadPath("/messages"),
			method: http.MethodPost, request: synthTextRequest, fake: sendOK,
			opts: []bridgeOpt{withNoClient()},
		},
		{
			// Accepted without a message id: 502, never a 200 Nora cannot name.
			file: "messages_no_message_id_502.json", route: "POST /threads/{thread_fbid}/messages", path: threadPath("/messages"),
			method: http.MethodPost, request: synthTextRequest,
			fake: func() *fakeIG { return &fakeIG{EmptySend: true} },
		},
		{
			file: "messages_internal_500.json", route: "POST /threads/{thread_fbid}/messages", path: threadPath("/messages"),
			method: http.MethodPost, request: synthTextRequest,
			fake: func() *fakeIG { return &fakeIG{SendErr: errorString("synthetic unnamed failure")} },
		},
		{
			file: "messages_bad_thread_400.json", route: "POST /threads/{thread_fbid}/messages", path: "/threads/not-a-thread/messages",
			method: http.MethodPost, request: synthTextRequest, fake: sendOK,
		},
		// --- PUT /threads/{thread_fbid}/messages/{message_id}/reaction ---
		{
			file: "reaction_ok.json", route: "PUT /threads/{thread_fbid}/messages/{message_id}/reaction",
			path: threadPath("/messages/" + synthMessage + "/reaction"), method: http.MethodPut,
			request: synthReactionRequest, fake: func() *fakeIG { return &fakeIG{} },
		},
		{
			file: "reaction_remove_ok.json", route: "PUT /threads/{thread_fbid}/messages/{message_id}/reaction",
			path: threadPath("/messages/" + synthMessage + "/reaction"), method: http.MethodPut,
			request: synthRemoveRequest, fake: func() *fakeIG { return &fakeIG{} },
		},
		{
			// Accepted, and this response's post-state silent about it: the
			// production shape. 200, reported, not failed.
			file: "reaction_unconfirmed.json", route: "PUT /threads/{thread_fbid}/messages/{message_id}/reaction",
			path: threadPath("/messages/" + synthMessage + "/reaction"), method: http.MethodPut,
			request: synthReactionRequest, fake: func() *fakeIG { return &fakeIG{ReactPostStateSilent: true} },
		},
		{
			file: "reaction_checkpointed_409.json", route: "PUT /threads/{thread_fbid}/messages/{message_id}/reaction",
			path: threadPath("/messages/" + synthMessage + "/reaction"), method: http.MethodPut,
			request: synthReactionRequest, fake: func() *fakeIG { return &fakeIG{} },
			opts: []bridgeOpt{withState("checkpointed", "challenge required")},
		},
		{
			file: "reaction_no_emoji_400.json", route: "PUT /threads/{thread_fbid}/messages/{message_id}/reaction",
			path: threadPath("/messages/" + synthMessage + "/reaction"), method: http.MethodPut,
			request: map[string]any{"remove": true}, fake: func() *fakeIG { return &fakeIG{} },
		},
		// --- DELETE /threads/{thread_fbid}/messages/{message_id} ---
		{
			file: "unsend_ok.json", route: "DELETE /threads/{thread_fbid}/messages/{message_id}",
			path: threadPath("/messages/" + synthMessage), method: http.MethodDelete,
			fake: func() *fakeIG { return &fakeIG{} },
		},
		{
			file: "unsend_unconfirmed.json", route: "DELETE /threads/{thread_fbid}/messages/{message_id}",
			path: threadPath("/messages/" + synthMessage), method: http.MethodDelete,
			fake: func() *fakeIG { return &fakeIG{UnconfirmedUnsend: true} },
		},
		// --- PUT /threads/{thread_fbid}/typing ---
		{
			file: "typing_ok.json", route: "PUT /threads/{thread_fbid}/typing", path: threadPath("/typing"),
			method: http.MethodPut, request: synthTypingRequest, fake: func() *fakeIG { return &fakeIG{} },
		},
		{
			// The one route that needs the realtime socket.
			file: "typing_socket_down_503.json", route: "PUT /threads/{thread_fbid}/typing", path: threadPath("/typing"),
			method: http.MethodPut, request: synthTypingRequest, fake: func() *fakeIG { return &fakeIG{} },
			opts: []bridgeOpt{withConnected(false)},
		},
	}
}

func contractCases() []contractCase {
	writes := writeContractCases()
	for i := range writes {
		writes[i].upstream = true
	}
	return append(readContractCases(), writes...)
}

func readContractCases() []contractCase {
	return []contractCase{
		{
			file: "account_ok.json", route: "GET /accounts/{igid}", path: "/accounts/" + synthIGID,
			fake:   func() *fakeIG { return &fakeIG{ProfileUserSet: true, ProfileUser: synthProfileUser()} },
			social: func() gateway.GraphQLIssuer { return &fakeGraphQLIssuer{body: []byte(synthSocialEnvelope)} },
		},
		{
			// GetProfile answered; the second, social call did not. The read
			// still succeeds, and `social_available: false` says what is
			// missing — an empty bio here is NOT "this account has no bio".
			file: "account_social_unavailable.json", route: "GET /accounts/{igid}", path: "/accounts/" + synthIGID,
			fake:   func() *fakeIG { return &fakeIG{ProfileUserSet: true, ProfileUser: synthProfileUser()} },
			social: func() gateway.GraphQLIssuer { return &fakeGraphQLIssuer{body: []byte(synthGraphQLErrors)} },
		},
		{
			// 200 with no user: the igid names no account (`errNoProfile`).
			file: "account_not_found_404.json", route: "GET /accounts/{igid}", path: "/accounts/" + synthIGID,
			fake: func() *fakeIG { return &fakeIG{ProfileUserSet: true, ProfileUser: nil} },
		},
		{
			// The session needs a human; retrying hammers a challenged account.
			file: "account_checkpointed_409.json", route: "GET /accounts/{igid}", path: "/accounts/" + synthIGID,
			fake: func() *fakeIG { return &fakeIG{ProfileErr: httpclient.ErrCheckpointRequired} },
		},
		{
			// Between sessions: no client to read with.
			file: "account_reconnecting_503.json", route: "GET /accounts/{igid}", path: "/accounts/" + synthIGID,
			fake: func() *fakeIG { return &fakeIG{} },
			opts: []bridgeOpt{withNoClient()},
		},
		{
			file: "story_found.json", route: "GET /accounts/{igid}/story", path: "/accounts/" + synthIGID + "/story",
			fake: func() *fakeIG { return &fakeIG{StoryBody: []byte(synthStoryJSON())} },
		},
		{
			// An empty reels_media: no story up, OR not permitted to see it.
			// The route has no "no such account" answer; this is it too.
			file: "story_not_found.json", route: "GET /accounts/{igid}/story", path: "/accounts/" + synthIGID + "/story",
			fake: func() *fakeIG { return &fakeIG{StoryBody: []byte(fakeig.NoStoryJSON)} },
		},
		{
			file: "story_checkpointed_409.json", route: "GET /accounts/{igid}/story", path: "/accounts/" + synthIGID + "/story",
			fake: func() *fakeIG { return &fakeIG{DocErr: httpclient.ErrCheckpointRequired} },
		},
		{
			// A rotated doc_id: halt this read, not the login (22 §4).
			file: "story_stale_query_501.json", route: "GET /accounts/{igid}/story", path: "/accounts/" + synthIGID + "/story",
			fake: func() *fakeIG { return &fakeIG{StoryBody: []byte(synthStaleQuery)} },
		},
		{
			file: "story_unconfigured_501.json", route: "GET /accounts/{igid}/story", path: "/accounts/" + synthIGID + "/story",
			fake: func() *fakeIG { return &fakeIG{} },
			opts: []bridgeOpt{withDocs(gateway.QueryDocs{Timeline: testTimelineDocID})},
		},
		// GET /accounts/{igid}/items (22 §1a, §7): one timeline page.
		{
			file: "items_ok.json", route: "GET /accounts/{igid}/items", path: "/accounts/" + synthIGID + "/items",
			fake: func() *fakeIG {
				return &fakeIG{ProfileUserSet: true, ProfileUser: synthProfileUser(), TimelineBody: []byte(synthTimelineJSON(true))}
			},
		},
		{
			// The end of the timeline: `next_cursor: null`.
			file: "items_last_page.json", route: "GET /accounts/{igid}/items", path: "/accounts/" + synthIGID + "/items?after=synthetic-cursor-1",
			fake: func() *fakeIG {
				return &fakeIG{ProfileUserSet: true, ProfileUser: synthProfileUser(), TimelineBody: []byte(synthTimelineJSON(false))}
			},
		},
		{
			file: "items_stale_query_501.json", route: "GET /accounts/{igid}/items", path: "/accounts/" + synthIGID + "/items",
			fake: func() *fakeIG {
				return &fakeIG{ProfileUserSet: true, ProfileUser: synthProfileUser(), TimelineBody: []byte(synthStaleQuery)}
			},
		},
		{
			file: "items_unconfigured_501.json", route: "GET /accounts/{igid}/items", path: "/accounts/" + synthIGID + "/items",
			fake: func() *fakeIG { return &fakeIG{ProfileUserSet: true, ProfileUser: synthProfileUser()} },
			opts: []bridgeOpt{withDocs(gateway.QueryDocs{Story: testStoryDocID})},
		},
		{
			file: "items_checkpointed_409.json", route: "GET /accounts/{igid}/items", path: "/accounts/" + synthIGID + "/items",
			fake: func() *fakeIG { return &fakeIG{ProfileErr: httpclient.ErrCheckpointRequired} },
		},
		{
			// The first fixture on the NEW response conventions (16 §Resources):
			// {"items":[...],"next_cursor":null}, no "ok" field.
			file: "accounts_search_ok.json", route: "GET /accounts", path: "/accounts?query=example",
			fake: func() *fakeIG {
				return &fakeIG{SearchResultsSet: true, SearchResults: synthSearchResults()}
			},
		},
		{
			// A search that matched nobody is a normal 200 with an empty list,
			// not an error.
			file: "accounts_search_empty.json", route: "GET /accounts", path: "/accounts?query=nobody-matches-this",
			fake: func() *fakeIG { return &fakeIG{SearchResultsSet: true, SearchResults: nil} },
		},
		{
			// The new envelope's failure shape: {"error":{"code","message"}}.
			file: "accounts_search_bad_query_400.json", route: "GET /accounts", path: "/accounts?query=",
			fake: func() *fakeIG { return &fakeIG{} },
		},
		{
			// The exact-match rule (app.LookupAccountByUsername, 17 P9/P10):
			// a fuzzy hit ranks first, and this route must still answer the
			// exact one rather than items[0] — the P9 case where "one exact
			// match was not the first result".
			file: "accounts_by_username_found.json", route: "GET /accounts", path: "/accounts?username=" + synthUsername,
			fake: func() *fakeIG {
				return &fakeIG{SearchResultsSet: true, SearchResults: synthUsernameLookupResults()}
			},
		},
		{
			// No hit equals the input exactly: an empty list, the same
			// "search found nobody" convention ?query= already uses.
			file: "accounts_by_username_not_found.json", route: "GET /accounts", path: "/accounts?username=nonexistent_handle",
			fake: func() *fakeIG {
				return &fakeIG{SearchResultsSet: true, SearchResults: synthUsernameLookupResults()}
			},
		},
		{
			// Malformed input never reaches the use case: 400 before the
			// session gate, and domain.ParseUsername's own text (which
			// embeds the raw input) never reaches this envelope.
			file: "accounts_by_username_bad_username_400.json", route: "GET /accounts", path: "/accounts?username=bad-username",
			fake: func() *fakeIG { return &fakeIG{} },
		},
		{
			// Two exact, case-insensitive matches for one username cannot
			// happen for real data (usernames are unique): 502
			// upstream_rejected, never a silent items[0] pick.
			file: "accounts_by_username_ambiguous_502.json", route: "GET /accounts", path: "/accounts?username=" + synthUsername,
			fake: func() *fakeIG {
				return &fakeIG{SearchResultsSet: true, SearchResults: synthUsernameLookupAmbiguous()}
			},
		},
		// GET /posts/{shortcode} (17 P12), on the new conventions. The
		// upstream bytes behind these are UNMEASURED and synthetic
		// (fakeig/posts.go): a carousel of three, mixed, in order.
		{
			file: "posts_carousel_ok.json", route: "GET /posts/{shortcode}", path: "/posts/" + fakeig.CarouselShortcode,
			fake: func() *fakeIG { return &fakeIG{} },
		},
		{
			// A shortcode outside base64url: refused at the edge.
			file: "posts_bad_shortcode_400.json", route: "GET /posts/{shortcode}", path: "/posts/not.a.shortcode",
			fake: func() *fakeIG { return &fakeIG{} },
		},
		{
			// Instagram answered 404 for the decoded id.
			file: "posts_not_found_404.json", route: "GET /posts/{shortcode}", path: "/posts/C3L9IQOygAE",
			fake: func() *fakeIG { return &fakeIG{} },
		},
		{
			// Any other refusal, including a failure inside an HTTP 200. A
			// deleted post and a private one Nora does not follow are
			// expected to land here or at 404; which, is the live probe's.
			file: "posts_upstream_rejected_502.json", route: "GET /posts/{shortcode}", path: "/posts/" + fakeig.CarouselShortcode,
			fake: func() *fakeIG {
				return &fakeIG{MediaErr: fakeig.LibraryStatusError(fakeig.CarouselMediaID, 400)}
			},
		},
		{
			// Longer than 11 characters: well formed, no decoding known.
			file: "posts_long_shortcode_501.json", route: "GET /posts/{shortcode}", path: "/posts/" + fakeig.CarouselShortcode + "xyz",
			fake: func() *fakeIG { return &fakeIG{} },
		},
		// The three failure envelopes Nora's `instagram_search` reads as
		// `unavailable` (design/instagram-adoption/17 P11): the session
		// needs a human (409), Instagram rejected the search (502), and no
		// session to search with (503). Each is the envelope, never the old
		// `{"ok": false}` body, so the Python client is tested against the
		// shape this route actually answers.
		{
			file: "accounts_search_checkpointed_409.json", route: "GET /accounts", path: "/accounts?query=example",
			fake: func() *fakeIG { return &fakeIG{SearchErr: httpclient.ErrCheckpointRequired} },
		},
		{
			file: "accounts_search_upstream_rejected_502.json", route: "GET /accounts", path: "/accounts?query=example",
			fake: func() *fakeIG {
				return &fakeIG{SearchErr: (&types.ErrorResponse{Errors: []*types.GraphQLError{
					{Message: "synthetic field exception", Summary: "Query error", Code: 1675030},
				}}).AsError()}
			},
		},
		{
			file: "accounts_search_reconnecting_503.json", route: "GET /accounts", path: "/accounts?query=example",
			fake: func() *fakeIG { return &fakeIG{} },
			opts: []bridgeOpt{withNoClient()},
		},
	}
}

func runContractCase(t *testing.T, tc contractCase) contractFixture {
	t.Helper()
	opts := append([]bridgeOpt{}, tc.opts...)
	if tc.social != nil {
		opts = append(opts, withIssuer(tc.social()))
	}
	f := tc.fake()
	b := newTestBridge(t, f, opts...)
	method := tc.method
	if method == "" {
		method = http.MethodGet
	}
	var reqBody json.RawMessage
	if tc.request != nil {
		blob, err := json.Marshal(tc.request)
		if err != nil {
			t.Fatal(err)
		}
		reqBody = blob
	}
	req := httptest.NewRequest(method, tc.path, bytes.NewReader(reqBody))
	rec := httptest.NewRecorder()
	b.routes().ServeHTTP(rec, req)
	var upstream json.RawMessage
	if tc.upstream {
		blob, err := json.Marshal(upstreamOf(f))
		if err != nil {
			t.Fatal(err)
		}
		upstream = blob
	}
	return contractFixture{
		Route: tc.route, Path: tc.path, Request: reqBody, Upstream: upstream, Status: rec.Code,
		Body: json.RawMessage(bytes.TrimSpace(rec.Body.Bytes())),
	}
}

// TestContractFixtures regenerates (with -update) or checks every shared
// fixture against what the handlers answer today.
func TestContractFixtures(t *testing.T) {
	skipOutsideTheRepo(t)
	for _, tc := range contractCases() {
		t.Run(tc.file, func(t *testing.T) {
			got := runContractCase(t, tc)
			path := filepath.Join(fixtureDir, tc.file)
			if *updateFixtures {
				blob, err := json.MarshalIndent(got, "", "  ")
				if err != nil {
					t.Fatal(err)
				}
				if err := os.MkdirAll(fixtureDir, 0o755); err != nil {
					t.Fatal(err)
				}
				if err := os.WriteFile(path, append(blob, '\n'), 0o644); err != nil {
					t.Fatal(err)
				}
				return
			}
			raw, err := os.ReadFile(path)
			if err != nil {
				t.Fatalf("missing fixture %s (run with -update): %v", path, err)
			}
			var want contractFixture
			if err := json.Unmarshal(raw, &want); err != nil {
				t.Fatalf("fixture %s does not decode: %v", path, err)
			}
			if got.Route != want.Route || got.Path != want.Path || got.Status != want.Status {
				t.Fatalf("%s: route/path/status = %s %s %d, fixture has %s %s %d",
					tc.file, got.Route, got.Path, got.Status, want.Route, want.Path, want.Status)
			}
			if !jsonEqual(t, got.Request, want.Request) {
				t.Errorf("%s: the request no longer matches the fixture's: got %s, want %s",
					tc.file, got.Request, want.Request)
			}
			if !jsonEqual(t, got.Upstream, want.Upstream) {
				t.Errorf("%s: the request no longer reaches Instagram as the fixture says — "+
					"the handler reads the request differently (a renamed field?).\n got: %s\nwant: %s",
					tc.file, got.Upstream, want.Upstream)
			}
			// Compared as decoded JSON, not bytes: indentation is the
			// fixture's, key order and spacing are the encoder's.
			var gotBody, wantBody any
			if err := json.Unmarshal(got.Body, &gotBody); err != nil {
				t.Fatalf("handler body is not JSON: %v (%s)", err, got.Body)
			}
			if err := json.Unmarshal(want.Body, &wantBody); err != nil {
				t.Fatalf("fixture body is not JSON: %v", err)
			}
			if !reflect.DeepEqual(gotBody, wantBody) {
				t.Errorf("%s: the handler's body no longer matches the shared fixture — "+
					"Nora's client is tested against the fixture, so this is a contract "+
					"change on both sides.\n got: %s\nwant: %s", tc.file, got.Body, want.Body)
			}
		})
	}
}

// jsonEqual compares two JSON values decoded, treating absent as absent.
func jsonEqual(t *testing.T, a, b json.RawMessage) bool {
	t.Helper()
	if len(a) == 0 || len(b) == 0 {
		return len(a) == len(b)
	}
	var av, bv any
	if err := json.Unmarshal(a, &av); err != nil {
		t.Fatalf("not JSON: %s", a)
	}
	if err := json.Unmarshal(b, &bv); err != nil {
		t.Fatalf("not JSON: %s", b)
	}
	return reflect.DeepEqual(av, bv)
}

// No fixture body carries the `ok` field (P14): success is the resource and
// the status says it succeeded; failure is the envelope. A body that kept
// `ok` would be a shape Nora's side no longer reads, still pinned as if it did.
func TestNoContractFixtureCarriesOk(t *testing.T) {
	for _, tc := range contractCases() {
		got := runContractCase(t, tc)
		var body map[string]any
		if err := json.Unmarshal(got.Body, &body); err != nil {
			t.Fatalf("%s: body is not a JSON object: %s", tc.file, got.Body)
		}
		if _, has := body["ok"]; has {
			t.Errorf("%s: the body carries `ok`: %s", tc.file, got.Body)
		}
		if got.Status != http.StatusOK {
			env, _ := body["error"].(map[string]any)
			if len(body) != 1 || env["code"] == nil || env["message"] == nil {
				t.Errorf("%s: a %d is not the error envelope: %s", tc.file, got.Status, got.Body)
			}
		}
	}
}

// Every write route has a fixture, success and failure both, so the Python
// adapter can be tested against every route it calls.
func TestEveryThreadRouteHasAFixture(t *testing.T) {
	have := map[string]map[bool]bool{}
	for _, tc := range writeContractCases() {
		if have[tc.route] == nil {
			have[tc.route] = map[bool]bool{}
		}
		have[tc.route][runContractCase(t, tc).Status == http.StatusOK] = true
	}
	for _, route := range []string{
		"GET /threads/{thread_fbid}/members",
		"POST /threads/{thread_fbid}/messages",
		"DELETE /threads/{thread_fbid}/messages/{message_id}",
		"PUT /threads/{thread_fbid}/messages/{message_id}/reaction",
		"PUT /threads/{thread_fbid}/typing",
	} {
		if !have[route][true] {
			t.Errorf("%s has no success fixture", route)
		}
		if route != "DELETE /threads/{thread_fbid}/messages/{message_id}" && !have[route][false] {
			t.Errorf("%s has no failure fixture", route)
		}
	}
}

// TestContractFixturesHaveNoOrphans: every committed fixture is produced by a
// case above. A fixture nothing regenerates is a shape the bridge is no longer
// held to while the Python tests still read it.
func TestContractFixturesHaveNoOrphans(t *testing.T) {
	skipOutsideTheRepo(t)
	entries, err := os.ReadDir(fixtureDir)
	if err != nil {
		t.Fatalf("cannot read %s: %v", fixtureDir, err)
	}
	made := map[string]bool{}
	for _, tc := range contractCases() {
		made[tc.file] = true
	}
	var orphans []string
	for _, e := range entries {
		if strings.HasSuffix(e.Name(), ".json") && !made[e.Name()] {
			orphans = append(orphans, e.Name())
		}
	}
	sort.Strings(orphans)
	if len(orphans) > 0 {
		t.Errorf("fixtures no case generates: %v", orphans)
	}
}
