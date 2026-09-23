// Package fakeig is a stand-in for a live instameow session that behaves like
// the MEASURED wire. It satisfies the gateway's Client structurally and is
// shared by the gateway's own tests and every test that drives a whole request
// path above it. It is test support: nothing in the production build imports
// it.
package fakeig

import (
	"context"
	"sync"

	"go.mau.fi/mautrix-meta/pkg/instameow"
	"go.mau.fi/mautrix-meta/pkg/instameow/slidetypes"
	"go.mau.fi/mautrix-meta/pkg/messagix/responses"
)

// The two ids for the same thread, from 02-identity-and-policy.md, and the own
// fbid every reaction read-back is checked against.
const (
	ThreadShort = "1009543911792316"
	ThreadLong  = "340282366841710301281152937372201004392"
	SelfFBID    = int64(14963523149149302)
)

// --- a fake that behaves like the MEASURED wire -------------------------------
//
// This fake does NOT accept any string it is handed. That was the defect in
// the suite it replaces: every id-space test asserted the bridge's own choice
// against a fake with no opinion, so if the bridge had picked the wrong id the
// test would have gone on passing. A test that cannot fail measures nothing.
//
// What is modelled here is what the probes actually observed, including the
// part that makes the bug invisible in production:
//
//   - /send takes EITHER id. Measured 2026-09-22: a carrier message sent with
//     the SHORT id arrived, and the long id was already known to. So the fake
//     accepts both and refuses only an id that names no thread at all — which
//     is all the grounding a send admits, and the reason its id-space test is
//     labelled pinned rather than grounded.
//   - /react lands ONLY with the SHORT id. Addressed with the LONG one it is
//     accepted, returns 200, and the reaction is ABSENT from the post-state
//     list the mutation itself returns. That absence is the only signal there
//     is, which is why the bridge now reads it — and why a test asserting
//     "the reaction arrived" fails if the bridge goes back to the long id,
//     rather than passing because the fake shrugged.
//   - /unsend reports `direct_unsend_message` honestly. The bridge DEGRADES on
//     a false rather than failing, so the fake is what proves both halves: the
//     200 still happens, and the observation is not swallowed.

type IG struct {
	mu sync.Mutex

	TextReq  *slidetypes.SendTextRequest
	MediaReq *slidetypes.SendMediaRequest
	ReactReq *slidetypes.CreateReactionRequest
	Unsend   *slidetypes.UnsendMessageRequest
	Typing   []string
	Fetches  int

	// reactions is the fake thread's post-state, keyed by message id: what
	// Instagram would return on the next read-back.
	Reactions map[string][]slidetypes.Reaction

	SendErr   error
	FetchErr  error
	TypingErr error
	// emptySend makes a send return a null message with NO error, which is how
	// the wire produced `{"ok":true}` with no id.
	EmptySend bool
	// reactPostStateSilent models what production does: the mutation is
	// accepted and the response's post-state does not list the new reaction,
	// while the reaction IS on the message when the thread is read back.
	// Measured 2026-09-22 — it fired on every reaction Nora made.
	ReactPostStateSilent bool
	// unconfirmedUnsend makes `direct_unsend_message` come back false even for
	// a correctly addressed unsend. No probe has ever seen the field do that;
	// it is here so the DEGRADATION can be exercised, because a test that can
	// only reach the confirmed path is a test that cannot fail.
	UnconfirmedUnsend bool

	// --- the roster read ---------------------------------------------------
	//
	// getThreadIDs records every thread id GetThread was addressed with, so
	// the id-space choice is asserted against what went out rather than
	// against the bridge's own intent.
	GetThreadIDs []string
	// threadUsers overrides the measured member list. nil means "the live
	// three-person room", which is what every roster test that is not about a
	// malformed shape wants.
	ThreadUsers []*slidetypes.User
	// threadUsersSet distinguishes "not overridden" from "deliberately empty",
	// because an empty Users[] is one of the shapes the route must refuse and
	// a nil check alone cannot express it.
	ThreadUsersSet bool
	ThreadErr      error
	// noThread makes GetThread answer 200 with the thread absent — the shape a
	// GraphQL read takes when it finds nothing, which is NOT an error.
	NoThread bool

	// --- the profile read --------------------------------------------------
	//
	// profileIGIDs records every igid GetProfile was addressed with, for
	// getThreadIDs' reason: the route's id-space choice is asserted against
	// what actually went out, not against what the handler meant to send.
	ProfileIGIDs []string
	// profileUser overrides the answer. nil means "the measured account".
	ProfileUser *slidetypes.User
	// profileUserSet distinguishes "not overridden" from "deliberately no
	// user", which is the 200-with-nothing shape the route must turn into a
	// 404 rather than an empty profile.
	ProfileUserSet bool
	ProfileErr     error

	// --- the story read ----------------------------------------------------
	//
	// reelIDs records every CALL's id list, not a flattened set, so the "one
	// igid, never a batch" rule can actually be asserted. A story read is
	// visible to its author, and a batched one would put Nora in several
	// people's viewer lists to answer a question about one.
	ReelIDs [][]string
	// reelResp overrides the answer. nil means "the measured reel".
	ReelResp *responses.ReelInfoResponse
	ReelSet  bool
	ReelErr  error

	// --- the account search --------------------------------------------------
	//
	// SearchQueries records every query text SearchUsers was addressed with —
	// the seam the observability test needs to prove the query text stays OUT
	// of every span while still confirming it reached the library call.
	SearchQueries []string
	// SearchResults overrides the answer. nil (and SearchResultsSet false)
	// means "the measured two hits".
	SearchResults    []*slidetypes.SearchResult
	SearchResultsSet bool
	SearchErr        error
}

// The measured three-person room, as `Users[]` actually returns it: TWO rows,
// because Instagram carries the viewer separately and Nora is not in her own
// member list. The fbids are the ones 02-identity-and-policy.md measured — 15
// and 17 digits, the second sharing Nora's `1784…` prefix — which is why no
// length or prefix rule may ever be used to tell an fbid from an igid.
var MeasuredUsers = []*slidetypes.User{
	{InteropMessagingUserFBID: 110966695387753, ID: "60739041440", Username: "nka_bpumvg", FullName: "Miles"},
	{InteropMessagingUserFBID: 12116372067347151, ID: "74340223606", Username: "third_member", FullName: ""},
}

// GetThread answers ONLY for the short thread id.
//
// That discrimination is the point: a roster read addressed with the long id
// must fail rather than be shrugged at, the same way the reaction fake refuses
// to mutate on the long id. A fake with no opinion about the id space cannot
// fail when the bridge picks the wrong one.
func (f *IG) GetThread(_ context.Context, req *slidetypes.GetThreadInfoRequest) (*slidetypes.ThreadInfoResponse, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.GetThreadIDs = append(f.GetThreadIDs, req.ThreadFBID)
	if f.ThreadErr != nil {
		return nil, f.ThreadErr
	}
	if f.NoThread || req.ThreadFBID != ThreadShort {
		// The thread is absent from the response and there is no error. This
		// is what the wire does, and it is why the route has to check.
		return &slidetypes.ThreadInfoResponse{}, nil
	}
	users := MeasuredUsers
	if f.ThreadUsersSet {
		users = f.ThreadUsers
	}
	return &slidetypes.ThreadInfoResponse{
		ThreadInfo: slidetypes.WrappedThreadInfo{
			ID: ThreadLong,
			AsIGDirectThread: &slidetypes.ThreadInfo{
				ThreadFBID: ThreadShort, ThreadTitle: "the group", IsGroup: true,
				Users: users,
			},
		},
	}, nil
}

// The measured profile account: public-side fields, a `friendship_status`
// Nora DOES follow, and an `Unrecognized` map carrying a key the library does
// not model. That last part is the one that matters — `is_private` is not on
// `slidetypes.User`, and if it ever arrives it arrives in here, so the fake
// has to be able to produce it or the route's `extra` passthrough is untested.
var MeasuredProfile = &slidetypes.User{
	InteropMessagingUserFBID: 110966695387753,
	ID:                       "221731317",
	Username:                 "nka_bpumvg",
	FullName:                 "Miles Novak",
	LatestReelMedia:          1789935673,
	FriendshipStatus: struct {
		IsRestricted bool `json:"is_restricted"`
		Blocking     bool `json:"blocking"`
		Following    bool `json:"following"`
	}{Following: true},
	Unrecognized: map[string]any{"is_private": true},
}

func (f *IG) GetProfile(_ context.Context, igid string) (*slidetypes.ProfilePageResponse, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.ProfileIGIDs = append(f.ProfileIGIDs, igid)
	if f.ProfileErr != nil {
		return nil, f.ProfileErr
	}
	user := MeasuredProfile
	if f.ProfileUserSet {
		user = f.ProfileUser
	}
	// Viewer is ALWAYS populated, because the wire always populates it. The
	// route dropping it is a decision the fake must be able to catch it not
	// making.
	return &slidetypes.ProfilePageResponse{
		User:   user,
		Viewer: &slidetypes.User{ID: "79597894900", Username: "not_____nora"},
	}, nil
}

// The measured reel shape: a two-frame story, one image and one video, with
// the video also carrying a cover still. That combination is the point — the
// route must call the video a video AND keep the still as the thumbnail, so a
// caller can look without downloading a clip.
func MeasuredReel() *responses.ReelInfoResponse {
	image := &responses.ReelItem{ExpiringAt: 1790097673}
	image.Pk = "3575332932625258692"
	image.TakenAt = 1790000432
	image.MediaType = 1
	image.AccessibilityCaption = "a photo of a plate"
	image.ImageVersions2.Candidates = []responses.Candidates{
		{Width: 640, Height: 1136, URL: "https://cdn/small.jpg"},
		{Width: 1080, Height: 1920, URL: "https://cdn/large.jpg"},
	}

	video := &responses.ReelItem{ExpiringAt: 1790097673}
	video.Pk = "3993554204431792593"
	video.TakenAt = 1790011844
	video.MediaType = 2
	video.ImageVersions2.Candidates = []responses.Candidates{
		{Width: 1080, Height: 1920, URL: "https://cdn/cover.jpg"},
	}
	video.VideoVersions = []responses.VideoVersions{
		{Width: 480, URL: "https://cdn/small.mp4"},
		{Width: 1080, URL: "https://cdn/large.mp4"},
	}

	return &responses.ReelInfoResponse{
		Reels: map[string]responses.ReelInfo{
			"221731317": {
				ID:         "221731317",
				ExpiringAt: 1790097673,
				Seen:       1789935673,
				User:       &responses.User{Username: "nka_bpumvg"},
				Items:      []*responses.ReelItem{image, video},
			},
		},
	}
}

func (f *IG) FetchReel(_ context.Context, reelIDs []string, _ string) (*responses.ReelInfoResponse, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.ReelIDs = append(f.ReelIDs, reelIDs)
	if f.ReelErr != nil {
		return nil, f.ReelErr
	}
	if f.ReelSet {
		return f.ReelResp, nil
	}
	return MeasuredReel(), nil
}

// MeasuredSearchResults is two hits in the SHAPE SearchUsers actually
// returns: `pk`, `username`, `full_name`, `is_verified`,
// `interop_messaging_user_fbid` — and nothing else. No `is_private`: that
// field is not on `slidetypes.SearchResult` at all (see search.go), so a fake
// that invented one here would test a shape the wire never sends.
//
// The VALUES are synthetic, deliberately not reusing MeasuredProfile's or
// MeasuredUsers' ids/names above: this fixture is new, and a new fixture does
// not get to carry a name and id that read as a real account just because an
// older one in this file already does.
var MeasuredSearchResults = []*slidetypes.SearchResult{
	{PK: "900000001", Username: "search_result_alpha", FullName: "Search Result Alpha", InteropMessagingUserFBID: 900000000000001},
	{PK: "900000002", Username: "search_result_beta", FullName: "", IsVerified: true},
}

func (f *IG) SearchUsers(_ context.Context, query string) (*slidetypes.SearchResponse, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.SearchQueries = append(f.SearchQueries, query)
	if f.SearchErr != nil {
		return nil, f.SearchErr
	}
	results := MeasuredSearchResults
	if f.SearchResultsSet {
		results = f.SearchResults
	}
	resp := &slidetypes.SearchResponse{}
	resp.Data.Results = results
	return resp, nil
}

func (f *IG) SendMessage(_ context.Context, req *slidetypes.SendTextRequest) (*slidetypes.SendTextResponse, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.TextReq = req
	if f.SendErr != nil {
		return nil, f.SendErr
	}
	if f.EmptySend || req.IGThreadIGID == nil || !knownThread(*req.IGThreadIGID) {
		// Accepted, and nothing arrived. No error: that is the point.
		return &slidetypes.SendTextResponse{}, nil
	}
	return &slidetypes.SendTextResponse{Message: slidetypes.SentMessage{MessageID: "mid.$sent"}}, nil
}

func (f *IG) SendMedia(_ context.Context, req *slidetypes.SendMediaRequest) (*slidetypes.SendMediaResponse, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.MediaReq = req
	if f.SendErr != nil {
		return nil, f.SendErr
	}
	if f.EmptySend || !knownThread(req.ThreadID) {
		return &slidetypes.SendMediaResponse{}, nil
	}
	return &slidetypes.SendMediaResponse{Message: slidetypes.SentMessage{MessageID: "mid.$media"}}, nil
}

func (f *IG) SendReaction(_ context.Context, req *slidetypes.CreateReactionRequest) (*slidetypes.SendReactionResponse, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.ReactReq = req
	if f.SendErr != nil {
		return nil, f.SendErr
	}
	if f.Reactions == nil {
		f.Reactions = map[string][]slidetypes.Reaction{}
	}
	id := req.Input.MessageID
	// MEASURED 2026-09-22: only the short thread id mutates the thread. The
	// long one is accepted and changes nothing, so the post-state comes back
	// unchanged and the caller has no error to go on.
	if req.Input.ThreadID == ThreadShort {
		kept := f.Reactions[id][:0:0]
		for _, r := range f.Reactions[id] {
			if r.SenderFBID != SelfFBID {
				kept = append(kept, r)
			}
		}
		if req.Input.ReactionStatus == slidetypes.ReactionStatusCreated {
			kept = append(kept, slidetypes.Reaction{Reaction: req.Input.Emoji, SenderFBID: SelfFBID})
		}
		f.Reactions[id] = kept
	}
	post := f.Reactions[id]
	if f.ReactPostStateSilent {
		post = nil
	}
	return &slidetypes.SendReactionResponse{
		Message: slidetypes.ReactionUpdateMessage{ID: id, Reactions: post},
	}, nil
}

func (f *IG) UnsendMessage(_ context.Context, req *slidetypes.UnsendMessageRequest) (*slidetypes.UnsendMessageResponse, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.Unsend = req
	if f.SendErr != nil {
		return nil, f.SendErr
	}
	// `direct_unsend_message` is the whole response body: the mutation says
	// whether it retracted anything. Addressed with the wrong thread it does
	// not, and says so — which is exactly the field the bridge used to drop.
	return &slidetypes.UnsendMessageResponse{
		DirectUnsendMessage: !f.UnconfirmedUnsend && req.SendData.ThreadID == ThreadLong,
	}, nil
}

func (f *IG) SetTyping(_ context.Context, threadID string, _ bool) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.Typing = append(f.Typing, threadID)
	return f.TypingErr
}

func (f *IG) FetchThreadID(_ context.Context, short int64) (*instameow.ThreadIGIDs, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.Fetches++
	if f.FetchErr != nil {
		return nil, f.FetchErr
	}
	_ = short
	return &instameow.ThreadIGIDs{ShortID: ThreadShort, LongID: ThreadLong}, nil
}

// knownThread is the send legs' only discrimination: measured, BOTH id spaces
// deliver a text message, so the fake can honestly refuse only an id that
// names no thread at all.
func knownThread(id string) bool { return id == ThreadShort || id == ThreadLong }
