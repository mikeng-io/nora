package main

import (
	"context"
	"errors"
	"reflect"
	"strconv"
	"strings"
	"sync"
	"testing"

	"go.mau.fi/mautrix-meta/pkg/instameow"
	"go.mau.fi/mautrix-meta/pkg/instameow/slidetypes"
	"go.mau.fi/mautrix-meta/pkg/messagix/httpclient"
	"go.mau.fi/mautrix-meta/pkg/messagix/responses"
	"go.mau.fi/mautrix-meta/pkg/messagix/types"
)

// The two ids for the same thread, from 02-identity-and-policy.md, and the own
// fbid every reaction read-back is checked against.
const (
	threadShort = "1009543911792316"
	threadLong  = "340282366841710301281152937372201004392"
	selfFBID    = int64(14963523149149302)
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

type fakeIG struct {
	mu sync.Mutex

	textReq  *slidetypes.SendTextRequest
	mediaReq *slidetypes.SendMediaRequest
	reactReq *slidetypes.CreateReactionRequest
	unsend   *slidetypes.UnsendMessageRequest
	typing   []string
	fetches  int

	// reactions is the fake thread's post-state, keyed by message id: what
	// Instagram would return on the next read-back.
	reactions map[string][]slidetypes.Reaction

	sendErr   error
	fetchErr  error
	typingErr error
	// emptySend makes a send return a null message with NO error, which is how
	// the wire produced `{"ok":true}` with no id.
	emptySend bool
	// reactPostStateSilent models what production does: the mutation is
	// accepted and the response's post-state does not list the new reaction,
	// while the reaction IS on the message when the thread is read back.
	// Measured 2026-09-22 — it fired on every reaction Nora made.
	reactPostStateSilent bool
	// unconfirmedUnsend makes `direct_unsend_message` come back false even for
	// a correctly addressed unsend. No probe has ever seen the field do that;
	// it is here so the DEGRADATION can be exercised, because a test that can
	// only reach the confirmed path is a test that cannot fail.
	unconfirmedUnsend bool

	// --- the roster read ---------------------------------------------------
	//
	// getThreadIDs records every thread id GetThread was addressed with, so
	// the id-space choice is asserted against what went out rather than
	// against the bridge's own intent.
	getThreadIDs []string
	// threadUsers overrides the measured member list. nil means "the live
	// three-person room", which is what every roster test that is not about a
	// malformed shape wants.
	threadUsers []*slidetypes.User
	// threadUsersSet distinguishes "not overridden" from "deliberately empty",
	// because an empty Users[] is one of the shapes the route must refuse and
	// a nil check alone cannot express it.
	threadUsersSet bool
	threadErr      error
	// noThread makes GetThread answer 200 with the thread absent — the shape a
	// GraphQL read takes when it finds nothing, which is NOT an error.
	noThread bool

	// --- the profile read --------------------------------------------------
	//
	// profileIGIDs records every igid GetProfile was addressed with, for
	// getThreadIDs' reason: the route's id-space choice is asserted against
	// what actually went out, not against what the handler meant to send.
	profileIGIDs []string
	// profileUser overrides the answer. nil means "the measured account".
	profileUser *slidetypes.User
	// profileUserSet distinguishes "not overridden" from "deliberately no
	// user", which is the 200-with-nothing shape the route must turn into a
	// 404 rather than an empty profile.
	profileUserSet bool
	profileErr     error

	// --- the story read ----------------------------------------------------
	//
	// reelIDs records every CALL's id list, not a flattened set, so the "one
	// igid, never a batch" rule can actually be asserted. A story read is
	// visible to its author, and a batched one would put Nora in several
	// people's viewer lists to answer a question about one.
	reelIDs [][]string
	// reelResp overrides the answer. nil means "the measured reel".
	reelResp *responses.ReelInfoResponse
	reelSet  bool
	reelErr  error
}

// The measured three-person room, as `Users[]` actually returns it: TWO rows,
// because Instagram carries the viewer separately and Nora is not in her own
// member list. The fbids are the ones 02-identity-and-policy.md measured — 15
// and 17 digits, the second sharing Nora's `1784…` prefix — which is why no
// length or prefix rule may ever be used to tell an fbid from an igid.
var measuredUsers = []*slidetypes.User{
	{InteropMessagingUserFBID: 110966695387753, ID: "60739041440", Username: "nka_bpumvg", FullName: "Miles"},
	{InteropMessagingUserFBID: 12116372067347151, ID: "74340223606", Username: "third_member", FullName: ""},
}

// GetThread answers ONLY for the short thread id.
//
// That discrimination is the point: a roster read addressed with the long id
// must fail rather than be shrugged at, the same way the reaction fake refuses
// to mutate on the long id. A fake with no opinion about the id space cannot
// fail when the bridge picks the wrong one.
func (f *fakeIG) GetThread(_ context.Context, req *slidetypes.GetThreadInfoRequest) (*slidetypes.ThreadInfoResponse, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.getThreadIDs = append(f.getThreadIDs, req.ThreadFBID)
	if f.threadErr != nil {
		return nil, f.threadErr
	}
	if f.noThread || req.ThreadFBID != threadShort {
		// The thread is absent from the response and there is no error. This
		// is what the wire does, and it is why the route has to check.
		return &slidetypes.ThreadInfoResponse{}, nil
	}
	users := measuredUsers
	if f.threadUsersSet {
		users = f.threadUsers
	}
	return &slidetypes.ThreadInfoResponse{
		ThreadInfo: slidetypes.WrappedThreadInfo{
			ID: threadLong,
			AsIGDirectThread: &slidetypes.ThreadInfo{
				ThreadFBID: threadShort, ThreadTitle: "the group", IsGroup: true,
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
var measuredProfile = &slidetypes.User{
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

func (f *fakeIG) GetProfile(_ context.Context, igid string) (*slidetypes.ProfilePageResponse, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.profileIGIDs = append(f.profileIGIDs, igid)
	if f.profileErr != nil {
		return nil, f.profileErr
	}
	user := measuredProfile
	if f.profileUserSet {
		user = f.profileUser
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
func measuredReel() *responses.ReelInfoResponse {
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

func (f *fakeIG) FetchReel(_ context.Context, reelIDs []string, _ string) (*responses.ReelInfoResponse, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.reelIDs = append(f.reelIDs, reelIDs)
	if f.reelErr != nil {
		return nil, f.reelErr
	}
	if f.reelSet {
		return f.reelResp, nil
	}
	return measuredReel(), nil
}

func (f *fakeIG) SendMessage(_ context.Context, req *slidetypes.SendTextRequest) (*slidetypes.SendTextResponse, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.textReq = req
	if f.sendErr != nil {
		return nil, f.sendErr
	}
	if f.emptySend || req.IGThreadIGID == nil || !knownThread(*req.IGThreadIGID) {
		// Accepted, and nothing arrived. No error: that is the point.
		return &slidetypes.SendTextResponse{}, nil
	}
	return &slidetypes.SendTextResponse{Message: slidetypes.SentMessage{MessageID: "mid.$sent"}}, nil
}

func (f *fakeIG) SendMedia(_ context.Context, req *slidetypes.SendMediaRequest) (*slidetypes.SendMediaResponse, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.mediaReq = req
	if f.sendErr != nil {
		return nil, f.sendErr
	}
	if f.emptySend || !knownThread(req.ThreadID) {
		return &slidetypes.SendMediaResponse{}, nil
	}
	return &slidetypes.SendMediaResponse{Message: slidetypes.SentMessage{MessageID: "mid.$media"}}, nil
}

func (f *fakeIG) SendReaction(_ context.Context, req *slidetypes.CreateReactionRequest) (*slidetypes.SendReactionResponse, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.reactReq = req
	if f.sendErr != nil {
		return nil, f.sendErr
	}
	if f.reactions == nil {
		f.reactions = map[string][]slidetypes.Reaction{}
	}
	id := req.Input.MessageID
	// MEASURED 2026-09-22: only the short thread id mutates the thread. The
	// long one is accepted and changes nothing, so the post-state comes back
	// unchanged and the caller has no error to go on.
	if req.Input.ThreadID == threadShort {
		kept := f.reactions[id][:0:0]
		for _, r := range f.reactions[id] {
			if r.SenderFBID != selfFBID {
				kept = append(kept, r)
			}
		}
		if req.Input.ReactionStatus == slidetypes.ReactionStatusCreated {
			kept = append(kept, slidetypes.Reaction{Reaction: req.Input.Emoji, SenderFBID: selfFBID})
		}
		f.reactions[id] = kept
	}
	post := f.reactions[id]
	if f.reactPostStateSilent {
		post = nil
	}
	return &slidetypes.SendReactionResponse{
		Message: slidetypes.ReactionUpdateMessage{ID: id, Reactions: post},
	}, nil
}

func (f *fakeIG) UnsendMessage(_ context.Context, req *slidetypes.UnsendMessageRequest) (*slidetypes.UnsendMessageResponse, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.unsend = req
	if f.sendErr != nil {
		return nil, f.sendErr
	}
	// `direct_unsend_message` is the whole response body: the mutation says
	// whether it retracted anything. Addressed with the wrong thread it does
	// not, and says so — which is exactly the field the bridge used to drop.
	return &slidetypes.UnsendMessageResponse{
		DirectUnsendMessage: !f.unconfirmedUnsend && req.SendData.ThreadID == threadLong,
	}, nil
}

func (f *fakeIG) SetTyping(_ context.Context, threadID string, _ bool) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.typing = append(f.typing, threadID)
	return f.typingErr
}

func (f *fakeIG) FetchThreadID(_ context.Context, short int64) (*instameow.ThreadIGIDs, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.fetches++
	if f.fetchErr != nil {
		return nil, f.fetchErr
	}
	_ = short
	return &instameow.ThreadIGIDs{ShortID: threadShort, LongID: threadLong}, nil
}

// knownThread is the send legs' only discrimination: measured, BOTH id spaces
// deliver a text message, so the fake can honestly refuse only an id that
// names no thread at all.
func knownThread(id string) bool { return id == threadShort || id == threadLong }

func newIndex(f *fakeIG) *threadIndex { return newThreadIndex(f.FetchThreadID) }

// --- the two-thread-id trap ---------------------------------------------------

// A send addressed with the short id does not arrive, and does not error
// usefully either. This is the most expensive single fact on the Instagram
// wire, so it gets the most direct test: the id that goes out is the long one.
// PINNED, NOT GROUNDED, and labelled so because the measurement says so.
//
// Measured 2026-09-22: a text message sent with the SHORT id ARRIVED
// (mid.$gAASG4nvQLWim9pFqD2gxUwgMAAAq, read back from thread history), and the
// LONG id was already known to arrive via the echo delta. BOTH work, so there
// is no read-back that can tell them apart and nothing here can fail on the
// choice itself. What this asserts is the REASON for the choice: the bridge
// follows upstream (from-matrix.go:81, `meta.IGID`), so the reference consumer
// and this one cannot drift.
//
// The grounded id-space test in this file is the reaction one. That is the
// verb that discriminates; this is the verb that does not.
func TestSendFollowsUpstreamAndUsesTheShortThreadID(t *testing.T) {
	f := &fakeIG{}
	idx := newIndex(f)
	thread, err := idx.resolve(t.Context(), threadShort)
	if err != nil {
		t.Fatal(err)
	}
	if thread.Long != threadLong || thread.Short != threadShort {
		t.Fatalf("resolve gave %+v, want both ids", thread)
	}
	res, err := sendText(t.Context(), f, thread.Short, "hello", nil, "")
	if err != nil {
		t.Fatal(err)
	}
	if res.ID != "mid.$sent" {
		t.Fatalf("the send did not arrive: %+v", res)
	}
	if f.textReq.IGThreadIGID == nil || *f.textReq.IGThreadIGID != threadShort {
		t.Fatalf("send carried %v, want the short id", f.textReq.IGThreadIGID)
	}
	// An id that names no thread at all is still refused — the one thing a
	// send CAN be grounded on.
	if _, err := sendText(t.Context(), f, "999", "hello", nil, ""); err == nil {
		t.Fatal("a send to an unknown thread was reported as a success")
	}
}

// The canonical short id is what the cache keys on. "012" and "12" are the
// same thread, and two entries for one thread would mean a typing
// notification's reverse lookup misses depending on how the room key was spelled.
func TestShortIDIsCanonicalizedBeforeCaching(t *testing.T) {
	f := &fakeIG{}
	idx := newIndex(f)
	for _, spelling := range []string{threadShort, "0" + threadShort} {
		thread, err := idx.resolve(t.Context(), spelling)
		if err != nil {
			t.Fatal(err)
		}
		if thread.Short != threadShort {
			t.Fatalf("resolve(%q).Short = %q", spelling, thread.Short)
		}
	}
	if f.fetches != 1 {
		t.Fatalf("FetchThreadID called %d times for one thread", f.fetches)
	}
}

func TestThreadIDIsResolvedOncePerRoom(t *testing.T) {
	f := &fakeIG{}
	idx := newIndex(f)
	for range 5 {
		if _, err := idx.resolve(t.Context(), threadShort); err != nil {
			t.Fatal(err)
		}
	}
	// The lookup is an HTTP round trip against the navigation endpoint, not the
	// messaging API. Paying it per reply would add a rate-limited request to
	// every message Nora sends.
	if f.fetches != 1 {
		t.Fatalf("FetchThreadID called %d times, want 1", f.fetches)
	}
}

// A non-numeric room_id is the CALLER's error, not Instagram's. Letting it
// through would produce a route lookup for /direct/t/0/ and a 404 that reads
// like a deleted thread.
func TestNonNumericRoomIDIsRejectedWithoutALookup(t *testing.T) {
	f := &fakeIG{}
	idx := newIndex(f)
	// The last entry is the LONG thread id. It is all digits, so a naive
	// "is it numeric" check would wave it through — and a room_id in the wrong
	// id space is precisely the mistake this whole translation layer exists to
	// make impossible. It overflows int64 and is refused.
	for _, bad := range []string{"", "abc", "-1", "0", "12a4", threadLong} {
		_, err := idx.resolve(t.Context(), bad)
		if !errors.Is(err, errBadRoomID) {
			t.Errorf("room_id %q gave %v, want errBadRoomID", bad, err)
		}
	}
	if f.fetches != 0 {
		t.Fatalf("a malformed room_id reached the network %d time(s)", f.fetches)
	}
}

// Fail CLOSED. A lookup that comes back without a long id must be an error:
// falling back to the short id would produce a send the HTTP layer reports as
// 200 and that never reaches the group — the single worst outcome available.
func TestEmptyLongIDIsAnErrorNotAFallback(t *testing.T) {
	f := &fakeIG{}
	idx := newThreadIndex(func(context.Context, int64) (*instameow.ThreadIGIDs, error) {
		return &instameow.ThreadIGIDs{ShortID: threadShort}, nil
	})
	_ = f
	if got, err := idx.resolve(t.Context(), threadShort); err == nil {
		t.Fatalf("an empty thread_igid was accepted: %+v", got)
	}
}

func TestRoomForAnswersBothIDSpacesAndAdmitsIgnorance(t *testing.T) {
	idx := newIndex(&fakeIG{})
	if _, ok := idx.roomFor(threadLong); ok {
		t.Fatal("claimed a room for an id it has never seen")
	}
	if _, err := idx.resolve(t.Context(), threadShort); err != nil {
		t.Fatal(err)
	}
	for _, id := range []string{threadShort, threadLong} {
		room, ok := idx.roomFor(id)
		if !ok || room != threadShort {
			t.Errorf("roomFor(%s) = %q,%v; want the short id", id, room, ok)
		}
	}
}

// --- mentions -----------------------------------------------------------------

func TestOutboundMentionPopulatesBothFields(t *testing.T) {
	f := &fakeIG{}
	_, err := sendText(t.Context(), f, threadShort, "@member_a hi",
		[]eventMention{{FBID: "110966695387753", Offset: 0, Length: 9}}, "")
	if err != nil {
		t.Fatal(err)
	}
	if len(f.textReq.Mentions) != 1 || f.textReq.Mentions[0].FBID != 110966695387753 {
		t.Fatalf("Mentions = %+v", f.textReq.Mentions)
	}
	if f.textReq.Mentions[0].Offset != 0 || f.textReq.Mentions[0].Length != 9 {
		t.Errorf("mention range lost: %+v", f.textReq.Mentions[0])
	}
	// Both fields were populated in the measured send that round-tripped, and
	// which one Instagram actually reads was never isolated. Dropping the one
	// that turns out to matter yields a mention that renders as plain text —
	// the exact Discord plaintext-tag failure this adaptor exists not to repeat.
	if len(f.textReq.MentionedUserIDs) != 1 || f.textReq.MentionedUserIDs[0] != "110966695387753" {
		t.Fatalf("MentionedUserIDs = %v", f.textReq.MentionedUserIDs)
	}
}

// Do NOT validate a mention id by shape beyond "is it a number". The measured
// interop fbids run 15 and 17 digits and share prefixes with each other, so
// any length or prefix rule would reject real members.
func TestMentionIDsOfDifferentLengthsAreBothAccepted(t *testing.T) {
	f := &fakeIG{}
	_, err := sendText(t.Context(), f, threadShort, "both", []eventMention{
		{FBID: "110966695387753", Offset: 0, Length: 1},   // 15 digits
		{FBID: "12116372067347151", Offset: 2, Length: 1}, // 17 digits
	}, "")
	if err != nil {
		t.Fatal(err)
	}
	if len(f.textReq.Mentions) != 2 {
		t.Fatalf("%d mentions survived, want 2", len(f.textReq.Mentions))
	}
}

func TestNonNumericMentionIsRefusedRatherThanSilentlyDropped(t *testing.T) {
	f := &fakeIG{}
	if _, err := sendText(t.Context(), f, threadShort, "hi",
		[]eventMention{{FBID: "@member_a"}}, ""); err == nil {
		t.Fatal("a non-numeric mention id was accepted")
	}
	if f.textReq != nil {
		t.Fatal("the message was sent anyway, with the mention silently gone")
	}
}

func TestReplyPopulatesBothReplyFields(t *testing.T) {
	f := &fakeIG{}
	if _, err := sendText(t.Context(), f, threadShort, "re", nil, "mid.$parent"); err != nil {
		t.Fatal(err)
	}
	if f.textReq.ReplyToMessageID == nil || *f.textReq.ReplyToMessageID != "mid.$parent" {
		t.Errorf("ReplyToMessageID = %v", f.textReq.ReplyToMessageID)
	}
	if f.textReq.RepliedToItemID == nil || *f.textReq.RepliedToItemID != "mid.$parent" {
		t.Errorf("RepliedToItemID = %v", f.textReq.RepliedToItemID)
	}
}

// 20,000 characters were measured as accepted on this wire; mautrix-meta's
// MaxTextLength = 1000 is its own policy. Enforcing it here would hand Nora's
// reply budget a ceiling that does not exist.
func TestLongTextIsNotCapped(t *testing.T) {
	f := &fakeIG{}
	text := strings.Repeat("x", 20000)
	if _, err := sendText(t.Context(), f, threadShort, text, nil, ""); err != nil {
		t.Fatal(err)
	}
	if got := len(f.textReq.Text.Value); got != 20000 {
		t.Fatalf("text was truncated to %d characters", got)
	}
}

func TestEveryMutationGetsItsOwnOfflineThreadingID(t *testing.T) {
	f := &fakeIG{}
	if _, err := sendText(t.Context(), f, threadShort, "one", nil, ""); err != nil {
		t.Fatal(err)
	}
	first := f.textReq.OfflineThreadingID
	if _, err := sendText(t.Context(), f, threadShort, "two", nil, ""); err != nil {
		t.Fatal(err)
	}
	// Reusing the token across two sends is how a retry becomes a duplicate,
	// or a second message becomes a silent no-op.
	if first == "" || first == f.textReq.OfflineThreadingID {
		t.Fatalf("offline threading id reused: %q", first)
	}
}

// --- media --------------------------------------------------------------------

func okUpload(fbid int64) uploader {
	return func(context.Context, int64, *httpclient.MercuryUploadMedia) (*types.MercuryUploadResponse, error) {
		return &types.MercuryUploadResponse{
			Payload: types.MediaPayloads{RealMetadata: &types.FileMetadata{ImageID: types.StringOrInt(fbid)}},
		}, nil
	}
}

// BOTH LEGS take the short id, from ONE int64, so they cannot disagree. The
// upload always needed it (it builds a referer of /direct/t/<short>/); the
// send follows upstream (from-matrix.go:95). Unmeasured with the short id —
// the same standing as /typing — but /send is measured on this endpoint
// family and upstream runs this shape in production.
func TestSendMediaUsesTheShortThreadIDForBothLegs(t *testing.T) {
	f := &fakeIG{}
	var uploadedTo int64
	inner := okUpload(2280432885466163)
	up := func(ctx context.Context, short int64, media *httpclient.MercuryUploadMedia) (*types.MercuryUploadResponse, error) {
		uploadedTo = short
		return inner(ctx, short, media)
	}
	res, err := sendMedia(t.Context(), f, up, 1009543911792316,
		&httpclient.MercuryUploadMedia{Filename: "x.png", MimeType: "image/png", MediaData: []byte{1}}, "")
	if err != nil {
		t.Fatal(err)
	}
	if uploadedTo != 1009543911792316 {
		t.Errorf("upload addressed %d, want the short thread id", uploadedTo)
	}
	if f.mediaReq.ThreadID != threadShort {
		t.Errorf("SendMedia thread = %q, want the short id", f.mediaReq.ThreadID)
	}
	// The two legs are ONE value: they cannot name different threads.
	if f.mediaReq.ThreadID != strconv.FormatInt(uploadedTo, 10) {
		t.Errorf("the upload and the send addressed different threads: %d vs %q",
			uploadedTo, f.mediaReq.ThreadID)
	}
	if f.mediaReq.AttachmentFBID != "2280432885466163" {
		t.Errorf("attachment fbid = %q", f.mediaReq.AttachmentFBID)
	}
	if !res.OK || res.ID != "mid.$media" {
		t.Errorf("result = %+v", res)
	}
}

// An upload that returns no usable attachment id must FAIL. Sending with
// attachment_fbid "0" would be accepted by the request builder and produce a
// message with no picture in it.
func TestUploadWithoutMetadataIsAnError(t *testing.T) {
	f := &fakeIG{}
	for name, up := range map[string]uploader{
		"nil response": func(context.Context, int64, *httpclient.MercuryUploadMedia) (*types.MercuryUploadResponse, error) {
			return nil, nil
		},
		"no metadata": func(context.Context, int64, *httpclient.MercuryUploadMedia) (*types.MercuryUploadResponse, error) {
			return &types.MercuryUploadResponse{}, nil
		},
		"zero fbid": okUpload(0),
	} {
		if _, err := sendMedia(t.Context(), f, up, 1009543911792316,
			&httpclient.MercuryUploadMedia{MediaData: []byte{1}}, ""); err == nil {
			t.Errorf("%s: upload failure was reported as a successful send", name)
		}
	}
}

// SendMediaRequest has no text field of any kind — a caption cannot be
// expressed on this wire. This test is a tripwire on the library type: if a
// future version adds one, the reflection below notices and the inversion
// against WhatsApp's caption-carrying send.py can be revisited deliberately
// rather than discovered by a dropped reply.
func TestSendMediaRequestHasNoTextField(t *testing.T) {
	req := slidetypes.SendMediaRequest{}
	for _, name := range []string{"Text", "Body", "Caption", "Message"} {
		if fieldExists(req, name) {
			t.Fatalf("SendMediaRequest grew a %q field — media captions may now be "+
				"expressible, and the two-message rule should be re-decided", name)
		}
	}
}

// --- reaction / unsend --------------------------------------------------------

// THE REACTION BUG, as a test that fails on the code this replaces.
//
// MEASURED 2026-09-22: the long thread_igid is accepted by /react and the
// reaction is absent from the thread on read-back; the short thread_fbid
// lands. Both halves are asserted, because a capability recorded only from
// what worked is decoration.
func TestReactionLandsOnlyWithTheShortThreadID(t *testing.T) {
	f := &fakeIG{}
	if err := sendReaction(t.Context(), f, threadLong, "mid.$x", "👍", false, selfFBID); err == nil {
		t.Fatal("a reaction addressed with the LONG thread id was reported as delivered")
	}
	f = &fakeIG{}
	if err := sendReaction(t.Context(), f, threadShort, "mid.$x", "👍", false, selfFBID); err != nil {
		t.Fatalf("a reaction addressed with the SHORT thread id failed: %v", err)
	}
	if f.reactReq.Input.ThreadID != threadShort {
		t.Errorf("reaction thread = %q, want the short id", f.reactReq.Input.ThreadID)
	}
	if f.reactReq.Input.ItemID != "mid.$x" || f.reactReq.Input.MessageID != "mid.$x" {
		t.Errorf("reaction target = %+v", f.reactReq.Input)
	}
}

func TestReactionRemovalStillCarriesTheEmojiAndIsCheckedToo(t *testing.T) {
	f := &fakeIG{}
	if err := sendReaction(t.Context(), f, threadShort, "mid.$x", "👍", false, selfFBID); err != nil {
		t.Fatal(err)
	}
	if err := sendReaction(t.Context(), f, threadShort, "mid.$x", "👍", true, selfFBID); err != nil {
		t.Fatal(err)
	}
	if f.reactReq.Input.ReactionStatus != slidetypes.ReactionStatusDeleted {
		t.Errorf("status = %q", f.reactReq.Input.ReactionStatus)
	}
	// The mutation identifies WHICH reaction is retracted, not merely that one
	// is, so an empty emoji on removal would retract nothing.
	if f.reactReq.Input.Emoji != "👍" {
		t.Errorf("emoji dropped on removal: %q", f.reactReq.Input.Emoji)
	}
	if got := f.reactions["mid.$x"]; len(got) != 0 {
		t.Fatalf("the reaction survived the removal: %+v", got)
	}
	// And a removal that does NOT take is refused, same as a create that does
	// not: the long id retracts nothing and the emoji stays on the message.
	f2 := &fakeIG{}
	if err := sendReaction(t.Context(), f2, threadShort, "mid.$y", "👍", false, selfFBID); err != nil {
		t.Fatal(err)
	}
	if err := sendReaction(t.Context(), f2, threadLong, "mid.$y", "👍", true, selfFBID); err == nil {
		t.Fatal("a removal that retracted nothing was reported as a success")
	}
}

// Fail closed on the unverifiable case. With no own fbid there is nothing to
// look for in the post-state, so the mutation cannot be confirmed — and an
// unconfirmable reaction is an ERROR, never a 200. Same defect as the long id
// produced, one layer up.
func TestReactionWithNoOwnFBIDIsRefusedRatherThanAssumed(t *testing.T) {
	err := sendReaction(t.Context(), &fakeIG{}, threadShort, "mid.$x", "👍", false, 0)
	if !errors.Is(err, errReactionUnverifiable) {
		t.Fatalf("err = %v, want errReactionUnverifiable", err)
	}
}

// Somebody ELSE's reaction on the same message is not evidence that ours
// landed. Without this the post-state check would pass on any non-empty list.
func TestAnotherMembersReactionIsNotOurReceipt(t *testing.T) {
	f := &fakeIG{reactions: map[string][]slidetypes.Reaction{
		"mid.$x": {{Reaction: "❤️", SenderFBID: 110966695387753}},
	}}
	if err := sendReaction(t.Context(), f, threadLong, "mid.$x", "👍", false, selfFBID); err == nil {
		t.Fatal("another member's reaction was read as our own receipt")
	}
}

// /unsend is the one verb that is genuinely LONG on both sides: every probe
// cleanup ran this way and upstream agrees (handlematrix.go:292).
func TestUnsendCarriesTheLongThreadID(t *testing.T) {
	f := &fakeIG{}
	confirmed, err := unsendMessage(t.Context(), f, threadLong, "mid.$y")
	if err != nil {
		t.Fatal(err)
	}
	if !confirmed {
		t.Error("a successful unsend was not confirmed")
	}
	if f.unsend.SendData.ThreadID != threadLong || f.unsend.MessageID != "mid.$y" {
		t.Fatalf("unsend = %+v", f.unsend)
	}
}

// `direct_unsend_message: false` is REPORTED, not raised.
//
// Nothing has ever observed that field against an unsend known to have failed,
// upstream discards it entirely (`_, err = ic.Client.UnsendMessage(...)`), and
// /unsend is the one verb where a false failure strands probe messages in a
// REAL group: the cleanup path fails, a human is told the message is still
// there, and it gets deleted by hand or not at all. So the signal is surfaced
// and the call still succeeds. If the field is ever measured to mean what its
// name suggests, delete THIS TEST, not the check.
func TestAnUnconfirmedUnsendIsReportedNotRaised(t *testing.T) {
	confirmed, err := unsendMessage(t.Context(), &fakeIG{unconfirmedUnsend: true}, threadLong, "mid.$y")
	if err != nil {
		t.Fatalf("an unconfirmed unsend was raised as an error: %v", err)
	}
	if confirmed {
		t.Fatal("direct_unsend_message=false was reported as confirmed")
	}
}

// --- a send Instagram accepted without naming the message ---------------------

// SentMessage is a VALUE field on the response, so a null
// `…_send_with_slide_messaging_response` unmarshals to a zero struct with
// err == nil. Reporting that as 200 answers `{"ok":true}` against a contract
// that promises `{ok,id,timestamp_ms}`, and an adapter that cannot name the
// message it just sent can neither react to it, unsend it, nor quote it.
func TestSendWithNoMessageIDIsAnErrorNotA200(t *testing.T) {
	for name, send := range map[string]func(*fakeIG) (sendResult, error){
		"text": func(f *fakeIG) (sendResult, error) {
			return sendText(t.Context(), f, threadShort, "hi", nil, "")
		},
		"media": func(f *fakeIG) (sendResult, error) {
			return sendMedia(t.Context(), f, okUpload(2280432885466163), 1009543911792316,
				&httpclient.MercuryUploadMedia{MediaData: []byte{1}}, "")
		},
	} {
		res, err := send(&fakeIG{emptySend: true})
		if !errors.Is(err, errNoMessageID) {
			t.Errorf("%s: err = %v, want errNoMessageID", name, err)
		}
		if res.OK {
			t.Errorf("%s: an id-less send reported ok=true", name)
		}
	}
}

// SentMessage carries the id under two names and which one the server fills is
// not decidable from here: upstream reads `id` for a send and `message_id` for
// the deltas its reactions then target. Both are read, so neither spelling
// silently degrades a send to `{"ok":true}`.
func TestMessageIDIsReadFromEitherSpelling(t *testing.T) {
	for name, msg := range map[string]slidetypes.SentMessage{
		"message_id": {MessageID: "mid.$a"},
		"id":         {ID: "mid.$a"},
		"both":       {MessageID: "mid.$a", ID: "mid.$a"},
	} {
		if got := messageIDOf(msg); got != "mid.$a" {
			t.Errorf("%s: messageIDOf = %q", name, got)
		}
	}
	if got := messageIDOf(slidetypes.SentMessage{}); got != "" {
		t.Errorf("a zero SentMessage produced an id: %q", got)
	}
}

// --- helpers ------------------------------------------------------------------

func fieldExists(v any, name string) bool {
	_, ok := reflect.TypeOf(v).FieldByName(name)
	return ok
}

// TestAnUnconfirmedReactionIsNotAFailure pins the production finding: the
// mutation is accepted, this response's post-state does not show it, and the
// reaction IS on the message when the thread is read back. Reporting that as a
// failure turned every acknowledgement reaction into a 500 — ten of them in
// twelve minutes, which is how Sentry found it.
//
// Reverting the degradation makes this red, and it is deliberately separate
// from the unverifiable case: an unknown own-fbid is a real inability to
// check, while this is a check that answered and was wrong.
func TestAnUnconfirmedReactionIsNotAFailure(t *testing.T) {
	f := &fakeIG{reactPostStateSilent: true}
	err := sendReaction(context.Background(), f, threadShort, "mid.$abc", "👀", false, 4242)
	if !errors.Is(err, errReactionUnconfirmed) {
		t.Fatalf("err = %v, want errReactionUnconfirmed", err)
	}
	if errors.Is(err, errReactionNotApplied) {
		t.Fatal("an unconfirmed reaction must not read as a failed one")
	}
}
