package instameow

import (
	"context"
	"errors"
	"reflect"
	"strconv"
	"strings"
	"testing"

	"instagram-bridge/internal/domain"

	"go.mau.fi/mautrix-meta/pkg/instameow"
	"go.mau.fi/mautrix-meta/pkg/instameow/slidetypes"
	"go.mau.fi/mautrix-meta/pkg/messagix/httpclient"
	"go.mau.fi/mautrix-meta/pkg/messagix/types"
)

func newIndex(f *fakeIG) *threadIndex { return newThreadIndex(f.FetchThreadID) }

// shortThread parses a room key the way the HTTP edge does.
func shortThread(t *testing.T, s string) domain.ThreadFBID {
	t.Helper()
	id, err := domain.ParseThreadFBID(s)
	if err != nil {
		t.Fatalf("ParseThreadFBID(%q): %v", s, err)
	}
	return id
}

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
	thread, err := idx.resolve(t.Context(), shortThread(t, threadShort))
	if err != nil {
		t.Fatal(err)
	}
	if thread.Long.String() != threadLong || thread.Short.String() != threadShort {
		t.Fatalf("resolve gave %+v, want both ids", thread)
	}
	res, err := sendText(t.Context(), f, thread.Short.String(), "hello", nil, "")
	if err != nil {
		t.Fatal(err)
	}
	if res.ID != "mid.$sent" {
		t.Fatalf("the send did not arrive: %+v", res)
	}
	if f.TextReq.IGThreadIGID == nil || *f.TextReq.IGThreadIGID != threadShort {
		t.Fatalf("send carried %v, want the short id", f.TextReq.IGThreadIGID)
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
		thread, err := idx.resolve(t.Context(), shortThread(t, spelling))
		if err != nil {
			t.Fatal(err)
		}
		if thread.Short.String() != threadShort {
			t.Fatalf("resolve(%q).Short = %q", spelling, thread.Short)
		}
	}
	if f.Fetches != 1 {
		t.Fatalf("FetchThreadID called %d times for one thread", f.Fetches)
	}
}

func TestThreadIDIsResolvedOncePerRoom(t *testing.T) {
	f := &fakeIG{}
	idx := newIndex(f)
	for range 5 {
		if _, err := idx.resolve(t.Context(), shortThread(t, threadShort)); err != nil {
			t.Fatal(err)
		}
	}
	// The lookup is an HTTP round trip against the navigation endpoint, not the
	// messaging API. Paying it per reply would add a rate-limited request to
	// every message Nora sends.
	if f.Fetches != 1 {
		t.Fatalf("FetchThreadID called %d times, want 1", f.Fetches)
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
	if got, err := idx.resolve(t.Context(), shortThread(t, threadShort)); err == nil {
		t.Fatalf("an empty thread_igid was accepted: %+v", got)
	}
}

func TestRoomForAnswersBothIDSpacesAndAdmitsIgnorance(t *testing.T) {
	idx := newIndex(&fakeIG{})
	if _, ok := idx.roomFor(threadLong); ok {
		t.Fatal("claimed a room for an id it has never seen")
	}
	if _, err := idx.resolve(t.Context(), shortThread(t, threadShort)); err != nil {
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
		[]domain.Mention{{FBID: "110966695387753", Offset: 0, Length: 9}}, "")
	if err != nil {
		t.Fatal(err)
	}
	if len(f.TextReq.Mentions) != 1 || f.TextReq.Mentions[0].FBID != 110966695387753 {
		t.Fatalf("Mentions = %+v", f.TextReq.Mentions)
	}
	if f.TextReq.Mentions[0].Offset != 0 || f.TextReq.Mentions[0].Length != 9 {
		t.Errorf("mention range lost: %+v", f.TextReq.Mentions[0])
	}
	// Both fields were populated in the measured send that round-tripped, and
	// which one Instagram actually reads was never isolated. Dropping the one
	// that turns out to matter yields a mention that renders as plain text —
	// the exact Discord plaintext-tag failure this adaptor exists not to repeat.
	if len(f.TextReq.MentionedUserIDs) != 1 || f.TextReq.MentionedUserIDs[0] != "110966695387753" {
		t.Fatalf("MentionedUserIDs = %v", f.TextReq.MentionedUserIDs)
	}
}

// Do NOT validate a mention id by shape beyond "is it a number". The measured
// interop fbids run 15 and 17 digits and share prefixes with each other, so
// any length or prefix rule would reject real members.
func TestMentionIDsOfDifferentLengthsAreBothAccepted(t *testing.T) {
	f := &fakeIG{}
	_, err := sendText(t.Context(), f, threadShort, "both", []domain.Mention{
		{FBID: "110966695387753", Offset: 0, Length: 1},   // 15 digits
		{FBID: "12116372067347151", Offset: 2, Length: 1}, // 17 digits
	}, "")
	if err != nil {
		t.Fatal(err)
	}
	if len(f.TextReq.Mentions) != 2 {
		t.Fatalf("%d mentions survived, want 2", len(f.TextReq.Mentions))
	}
}

func TestNonNumericMentionIsRefusedRatherThanSilentlyDropped(t *testing.T) {
	f := &fakeIG{}
	if _, err := sendText(t.Context(), f, threadShort, "hi",
		[]domain.Mention{{FBID: "@member_a"}}, ""); err == nil {
		t.Fatal("a non-numeric mention id was accepted")
	}
	if f.TextReq != nil {
		t.Fatal("the message was sent anyway, with the mention silently gone")
	}
}

func TestReplyPopulatesBothReplyFields(t *testing.T) {
	f := &fakeIG{}
	if _, err := sendText(t.Context(), f, threadShort, "re", nil, "mid.$parent"); err != nil {
		t.Fatal(err)
	}
	if f.TextReq.ReplyToMessageID == nil || *f.TextReq.ReplyToMessageID != "mid.$parent" {
		t.Errorf("ReplyToMessageID = %v", f.TextReq.ReplyToMessageID)
	}
	if f.TextReq.RepliedToItemID == nil || *f.TextReq.RepliedToItemID != "mid.$parent" {
		t.Errorf("RepliedToItemID = %v", f.TextReq.RepliedToItemID)
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
	if got := len(f.TextReq.Text.Value); got != 20000 {
		t.Fatalf("text was truncated to %d characters", got)
	}
}

func TestEveryMutationGetsItsOwnOfflineThreadingID(t *testing.T) {
	f := &fakeIG{}
	if _, err := sendText(t.Context(), f, threadShort, "one", nil, ""); err != nil {
		t.Fatal(err)
	}
	first := f.TextReq.OfflineThreadingID
	if _, err := sendText(t.Context(), f, threadShort, "two", nil, ""); err != nil {
		t.Fatal(err)
	}
	// Reusing the token across two sends is how a retry becomes a duplicate,
	// or a second message becomes a silent no-op.
	if first == "" || first == f.TextReq.OfflineThreadingID {
		t.Fatalf("offline threading id reused: %q", first)
	}
}

// --- media --------------------------------------------------------------------

func okUpload(fbid int64) Uploader {
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
	if f.MediaReq.ThreadID != threadShort {
		t.Errorf("SendMedia thread = %q, want the short id", f.MediaReq.ThreadID)
	}
	// The two legs are ONE value: they cannot name different threads.
	if f.MediaReq.ThreadID != strconv.FormatInt(uploadedTo, 10) {
		t.Errorf("the upload and the send addressed different threads: %d vs %q",
			uploadedTo, f.MediaReq.ThreadID)
	}
	if f.MediaReq.AttachmentFBID != "2280432885466163" {
		t.Errorf("attachment fbid = %q", f.MediaReq.AttachmentFBID)
	}
	if res.ID != "mid.$media" {
		t.Errorf("result = %+v", res)
	}
}

// An upload that returns no usable attachment id must FAIL. Sending with
// attachment_fbid "0" would be accepted by the request builder and produce a
// message with no picture in it.
func TestUploadWithoutMetadataIsAnError(t *testing.T) {
	f := &fakeIG{}
	for name, up := range map[string]Uploader{
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
	if f.ReactReq.Input.ThreadID != threadShort {
		t.Errorf("reaction thread = %q, want the short id", f.ReactReq.Input.ThreadID)
	}
	if f.ReactReq.Input.ItemID != "mid.$x" || f.ReactReq.Input.MessageID != "mid.$x" {
		t.Errorf("reaction target = %+v", f.ReactReq.Input)
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
	if f.ReactReq.Input.ReactionStatus != slidetypes.ReactionStatusDeleted {
		t.Errorf("status = %q", f.ReactReq.Input.ReactionStatus)
	}
	// The mutation identifies WHICH reaction is retracted, not merely that one
	// is, so an empty emoji on removal would retract nothing.
	if f.ReactReq.Input.Emoji != "👍" {
		t.Errorf("emoji dropped on removal: %q", f.ReactReq.Input.Emoji)
	}
	if got := f.Reactions["mid.$x"]; len(got) != 0 {
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
	f := &fakeIG{Reactions: map[string][]slidetypes.Reaction{
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
	if f.Unsend.SendData.ThreadID != threadLong || f.Unsend.MessageID != "mid.$y" {
		t.Fatalf("unsend = %+v", f.Unsend)
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
	confirmed, err := unsendMessage(t.Context(), &fakeIG{UnconfirmedUnsend: true}, threadLong, "mid.$y")
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
	for name, send := range map[string]func(*fakeIG) (domain.Sent, error){
		"text": func(f *fakeIG) (domain.Sent, error) {
			return sendText(t.Context(), f, threadShort, "hi", nil, "")
		},
		"media": func(f *fakeIG) (domain.Sent, error) {
			return sendMedia(t.Context(), f, okUpload(2280432885466163), 1009543911792316,
				&httpclient.MercuryUploadMedia{MediaData: []byte{1}}, "")
		},
	} {
		res, err := send(&fakeIG{EmptySend: true})
		if !errors.Is(err, errNoMessageID) {
			t.Errorf("%s: err = %v, want errNoMessageID", name, err)
		}
		if res.ID != "" {
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
	f := &fakeIG{ReactPostStateSilent: true}
	err := sendReaction(context.Background(), f, threadShort, "mid.$abc", "👀", false, 4242)
	if !errors.Is(err, errReactionUnconfirmed) {
		t.Fatalf("err = %v, want errReactionUnconfirmed", err)
	}
	if errors.Is(err, errReactionNotApplied) {
		t.Fatal("an unconfirmed reaction must not read as a failed one")
	}
}
