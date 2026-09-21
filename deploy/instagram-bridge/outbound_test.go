package main

import (
	"context"
	"errors"
	"reflect"
	"strings"
	"sync"
	"testing"

	"go.mau.fi/mautrix-meta/pkg/instameow"
	"go.mau.fi/mautrix-meta/pkg/instameow/slidetypes"
	"go.mau.fi/mautrix-meta/pkg/messagix/httpclient"
	"go.mau.fi/mautrix-meta/pkg/messagix/types"
)

// The two ids for the same thread, from 02-identity-and-policy.md. They are
// spelled out here because half of these tests exist to prove the LONG one is
// what leaves the process and the SHORT one is what enters it.
const (
	threadShort = "1009543911792316"
	threadLong  = "340282366841710301281152937372201004392"
)

// --- a recording fake --------------------------------------------------------

type fakeIG struct {
	mu sync.Mutex

	textReq  *slidetypes.SendTextRequest
	mediaReq *slidetypes.SendMediaRequest
	reactReq *slidetypes.CreateReactionRequest
	unsend   *slidetypes.UnsendMessageRequest
	typing   []string
	fetches  int

	sendErr   error
	fetchErr  error
	typingErr error
}

func (f *fakeIG) SendMessage(_ context.Context, req *slidetypes.SendTextRequest) (*slidetypes.SendTextResponse, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.textReq = req
	if f.sendErr != nil {
		return nil, f.sendErr
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
	return &slidetypes.SendMediaResponse{Message: slidetypes.SentMessage{MessageID: "mid.$media"}}, nil
}

func (f *fakeIG) SendReaction(_ context.Context, req *slidetypes.CreateReactionRequest) (*slidetypes.SendReactionResponse, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.reactReq = req
	return &slidetypes.SendReactionResponse{}, f.sendErr
}

func (f *fakeIG) UnsendMessage(_ context.Context, req *slidetypes.UnsendMessageRequest) (*slidetypes.UnsendMessageResponse, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.unsend = req
	return &slidetypes.UnsendMessageResponse{}, f.sendErr
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

func newIndex(f *fakeIG) *threadIndex { return newThreadIndex(f.FetchThreadID) }

// --- the two-thread-id trap ---------------------------------------------------

// A send addressed with the short id does not arrive, and does not error
// usefully either. This is the most expensive single fact on the Instagram
// wire, so it gets the most direct test: the id that goes out is the long one.
func TestSendUsesTheLongThreadID(t *testing.T) {
	f := &fakeIG{}
	idx := newIndex(f)
	long, err := idx.resolve(t.Context(), threadShort)
	if err != nil {
		t.Fatal(err)
	}
	if long != threadLong {
		t.Fatalf("resolve gave %q, want the long thread_igid", long)
	}
	if _, err := sendText(t.Context(), f, long, "hello", nil, ""); err != nil {
		t.Fatal(err)
	}
	if f.textReq.IGThreadIGID == nil || *f.textReq.IGThreadIGID != threadLong {
		t.Fatalf("send carried %v, want the long id", f.textReq.IGThreadIGID)
	}
	if strings.Contains(*f.textReq.IGThreadIGID, threadShort) {
		t.Fatal("the short id leaked into the send")
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
	if _, err := idx.resolve(t.Context(), threadShort); err == nil {
		t.Fatal("an empty thread_igid was accepted")
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
	_, err := sendText(t.Context(), f, threadLong, "@member_a hi",
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
	_, err := sendText(t.Context(), f, threadLong, "both", []eventMention{
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
	if _, err := sendText(t.Context(), f, threadLong, "hi",
		[]eventMention{{FBID: "@member_a"}}, ""); err == nil {
		t.Fatal("a non-numeric mention id was accepted")
	}
	if f.textReq != nil {
		t.Fatal("the message was sent anyway, with the mention silently gone")
	}
}

func TestReplyPopulatesBothReplyFields(t *testing.T) {
	f := &fakeIG{}
	if _, err := sendText(t.Context(), f, threadLong, "re", nil, "mid.$parent"); err != nil {
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
	if _, err := sendText(t.Context(), f, threadLong, text, nil, ""); err != nil {
		t.Fatal(err)
	}
	if got := len(f.textReq.Text.Value); got != 20000 {
		t.Fatalf("text was truncated to %d characters", got)
	}
}

func TestEveryMutationGetsItsOwnOfflineThreadingID(t *testing.T) {
	f := &fakeIG{}
	if _, err := sendText(t.Context(), f, threadLong, "one", nil, ""); err != nil {
		t.Fatal(err)
	}
	first := f.textReq.OfflineThreadingID
	if _, err := sendText(t.Context(), f, threadLong, "two", nil, ""); err != nil {
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

// The upload is addressed with the SHORT id (it builds a referer of
// /direct/t/<short>/) and the send with the LONG one. Both id spaces appear in
// one call path, which is exactly why they are easy to swap.
func TestSendMediaUsesShortForUploadAndLongForSend(t *testing.T) {
	f := &fakeIG{}
	var uploadedTo int64
	inner := okUpload(2280432885466163)
	up := func(ctx context.Context, short int64, media *httpclient.MercuryUploadMedia) (*types.MercuryUploadResponse, error) {
		uploadedTo = short
		return inner(ctx, short, media)
	}
	res, err := sendMedia(t.Context(), f, up, 1009543911792316, threadLong,
		&httpclient.MercuryUploadMedia{Filename: "x.png", MimeType: "image/png", MediaData: []byte{1}}, "")
	if err != nil {
		t.Fatal(err)
	}
	if uploadedTo != 1009543911792316 {
		t.Errorf("upload addressed %d, want the short thread id", uploadedTo)
	}
	if f.mediaReq.ThreadID != threadLong {
		t.Errorf("SendMedia thread = %q, want the long id", f.mediaReq.ThreadID)
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
		if _, err := sendMedia(t.Context(), f, up, 1, threadLong,
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

func TestReactionRemovalStillCarriesTheEmoji(t *testing.T) {
	f := &fakeIG{}
	if err := sendReaction(t.Context(), f, threadLong, "mid.$x", "👍", true); err != nil {
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
	if f.reactReq.Input.ThreadID != threadLong {
		t.Errorf("reaction thread = %q, want the long id", f.reactReq.Input.ThreadID)
	}
	if f.reactReq.Input.ItemID != "mid.$x" || f.reactReq.Input.MessageID != "mid.$x" {
		t.Errorf("reaction target = %+v", f.reactReq.Input)
	}
}

func TestUnsendCarriesTheLongThreadID(t *testing.T) {
	f := &fakeIG{}
	if err := unsendMessage(t.Context(), f, threadLong, "mid.$y"); err != nil {
		t.Fatal(err)
	}
	if f.unsend.SendData.ThreadID != threadLong || f.unsend.MessageID != "mid.$y" {
		t.Fatalf("unsend = %+v", f.unsend)
	}
}

// --- helpers ------------------------------------------------------------------

func fieldExists(v any, name string) bool {
	_, ok := reflect.TypeOf(v).FieldByName(name)
	return ok
}
