package main

// Outbound: the send half, and the trap that makes it non-trivial.
//
// THE TRAP. Instagram gives one thread two numeric identities. Inbound deltas
// carry the SHORT id (`thread_fbid`, measured `1009543911792316`); the LONG
// one (`thread_igid`, `340282366841710301281152937372201004392`) comes back
// from a route lookup. They are NOT interchangeable everywhere — but they are
// interchangeable in more places than the first reading of this file assumed,
// and the one verb where they are not is the one nothing had read back.
//
// EVERY VERB NOW MATCHES UPSTREAM mautrix-meta. Three are measured on this
// account; two follow upstream on its authority alone, and the table says
// which is which, because that distinction is exactly what was missing when
// the reaction bug shipped:
//
//	verb         id     standing
//	/send        SHORT  MEASURED 2026-09-22 — a carrier message sent with the
//	                    SHORT id ARRIVED (mid.$gAASG4nvQLWim9pFqD2gxUwgMAAAq,
//	                    read back from thread history). The LONG id also
//	                    arrives, via the echo delta. BOTH WORK for text, so
//	                    matching upstream (msgconv/igconv/from-matrix.go:81,
//	                    `meta.IGID`) is free, and a standing disagreement with
//	                    the reference consumer is not.
//	/send-media  SHORT  UPSTREAM (from-matrix.go:95). Not measured with the
//	                    short id; the same endpoint family as /send, which is,
//	                    and upstream runs this shape in production. The UPLOAD
//	                    leg was always short — it builds a referer of
//	                    /direct/t/<short>/.
//	/react       SHORT  MEASURED 2026-09-22, and the ONLY verb that
//	                    discriminates. A reaction addressed with the LONG id is
//	                    ACCEPTED by the API — 200, no error of any kind — and
//	                    is ABSENT from the thread on read-back. The SHORT id is
//	                    present. Upstream: handlematrix.go:210.
//	/typing      SHORT  UPSTREAM (handlematrix.go:484). Arrival was never
//	                    verified in either id space; there is no read-back for
//	                    typing, so this is followed, not proven.
//	/unsend      LONG   MEASURED — every probe cleanup ran this way, and
//	                    upstream agrees (handlematrix.go:292,
//	                    `meta.IGThreadID`). The one verb that is genuinely
//	                    long, on both sides.
//
// A reaction addressed with the wrong id does not arrive and does not error
// usefully either, so there is no signal to notice it by. That is why the
// standing column exists: "follows upstream" is a reason to believe and
// "measured" is a reason to know, and writing one where the other belongs is
// how a verb goes wrong for a month.
//
// The translation lives HERE, behind the HTTP surface, and the contract
// exposes only the short id. That is the same reasoning that puts JID
// normalization on the WhatsApp adapter side: put the trap where exactly one
// piece of code can step in it. If the long id were on the wire, every caller
// would own the chance to use the wrong one.

import (
	"context"
	"errors"
	"fmt"
	"strconv"
	"sync"

	"go.mau.fi/mautrix-meta/pkg/instameow"
	"go.mau.fi/mautrix-meta/pkg/instameow/slidetypes"
	"go.mau.fi/mautrix-meta/pkg/messagix/httpclient"
	"go.mau.fi/mautrix-meta/pkg/messagix/methods"
	"go.mau.fi/mautrix-meta/pkg/messagix/types"
)

// igClient is the slice of *instameow.Client this bridge uses. It exists so
// the HTTP layer can be exercised without a live Instagram session: the
// account is real and in use, and "run it and see" is not an available test.
type igClient interface {
	SendMessage(context.Context, *slidetypes.SendTextRequest) (*slidetypes.SendTextResponse, error)
	SendMedia(context.Context, *slidetypes.SendMediaRequest) (*slidetypes.SendMediaResponse, error)
	SendReaction(context.Context, *slidetypes.CreateReactionRequest) (*slidetypes.SendReactionResponse, error)
	UnsendMessage(context.Context, *slidetypes.UnsendMessageRequest) (*slidetypes.UnsendMessageResponse, error)
	SetTyping(ctx context.Context, threadID string, typing bool) error
	FetchThreadID(ctx context.Context, threadFBID int64) (*instameow.ThreadIGIDs, error)
}

// uploader is SendMercuryUploadRequest, extracted as a function value for the
// same reason: it hangs off *httpclient.HTTPClient, which cannot be faked.
type uploader func(context.Context, int64, *httpclient.MercuryUploadMedia) (*types.MercuryUploadResponse, error)

// threadRef is both ids for one thread, resolved together and carried
// together. It exists because the per-verb table at the top of this file means
// a handler needs BOTH: there is no single "the thread id" to pass around, and
// a function that took one string would make picking the wrong space a
// one-character mistake again.
type threadRef struct {
	// Short is `thread_fbid`, canonicalized through ParseInt/FormatInt so the
	// cache cannot hold "012" and "12" as two different threads.
	Short string
	// Long is `thread_igid`.
	Long string
}

// threadIndex caches the short->long pair. The lookup is an HTTP round trip
// against Instagram's route endpoint, so doing it per send would add a request
// to every reply — and a rate-limited one, on the navigation endpoint rather
// than the messaging API.
//
// Nothing invalidates an entry. A thread's id pair does not change; if the
// thread is deleted the send fails on its own and re-resolving would not have
// helped. The reverse map exists for one caller only: turning a typing
// notification's unclassified thread id back into a room key we can vouch for.
type threadIndex struct {
	mu        sync.RWMutex
	shortLong map[string]string
	longShort map[string]string
	fetch     func(context.Context, int64) (*instameow.ThreadIGIDs, error)
}

func newThreadIndex(fetch func(context.Context, int64) (*instameow.ThreadIGIDs, error)) *threadIndex {
	return &threadIndex{
		shortLong: map[string]string{},
		longShort: map[string]string{},
		fetch:     fetch,
	}
}

var errBadRoomID = errors.New("room_id must be a decimal thread_fbid")

// resolve maps a room key to both thread ids, fetching on a miss.
func (t *threadIndex) resolve(ctx context.Context, short string) (threadRef, error) {
	// Parse before anything else: `room_id` arrives from HTTP and a non-numeric
	// value is a caller error (400), not an Instagram failure (5xx). Letting it
	// through would produce a route lookup for `/direct/t/0/` and a 404 that
	// reads like a deleted thread.
	shortID, err := strconv.ParseInt(short, 10, 64)
	if err != nil || shortID <= 0 {
		return threadRef{}, fmt.Errorf("%w: %q", errBadRoomID, short)
	}
	canonical := strconv.FormatInt(shortID, 10)

	t.mu.RLock()
	long, ok := t.shortLong[canonical]
	t.mu.RUnlock()
	if ok {
		return threadRef{Short: canonical, Long: long}, nil
	}

	ids, err := t.fetch(ctx, shortID)
	if err != nil {
		return threadRef{}, err
	}
	if ids == nil || ids.LongID == "" {
		// Fail closed. Returning the short id as a "best effort" would produce
		// a send that is accepted by the HTTP layer and never arrives, which
		// is the single worst outcome available here.
		return threadRef{}, fmt.Errorf("thread %s resolved to no thread_igid", canonical)
	}
	t.remember(canonical, ids.LongID)
	return threadRef{Short: canonical, Long: ids.LongID}, nil
}

func (t *threadIndex) remember(short, long string) {
	t.mu.Lock()
	defer t.mu.Unlock()
	t.shortLong[short] = long
	t.longShort[long] = short
}

// roomFor answers "is this id one I already know a room key for?" for either
// id space, and says so honestly when it does not. Used only by the typing
// path, where the id space of the incoming value is not established.
func (t *threadIndex) roomFor(anyID string) (string, bool) {
	t.mu.RLock()
	defer t.mu.RUnlock()
	if _, ok := t.shortLong[anyID]; ok {
		return anyID, true
	}
	if short, ok := t.longShort[anyID]; ok {
		return short, true
	}
	return "", false
}

// offlineThreadingID is the client-side idempotency token every mutation
// carries. Generated per call — reusing one across two sends is how a retry
// becomes a duplicate or, worse, a silent no-op.
func offlineThreadingID() string {
	return strconv.FormatInt(methods.GenerateEpochID(), 10)
}

type sendResult struct {
	OK          bool   `json:"ok"`
	ID          string `json:"id,omitempty"`
	TimestampMS int64  `json:"timestamp_ms,omitempty"`
}

// errNoMessageID is what a send that Instagram accepted without naming the
// message becomes. It is an ERROR rather than a degraded success on purpose:
// slidetypes.SentMessage is a VALUE field on the response, so a null or absent
// `…_send_with_slide_messaging_response` unmarshals to a zero struct with
// err == nil. Reporting that as 200 would answer `{"ok":true}` against a
// contract that promises `{ok,id,timestamp_ms}`, and an adapter that cannot
// name the message it just sent can neither react to it, unsend it, nor quote
// it — the same class of "accepted, invisible" failure the two-id trap
// produces, arriving through the response instead of the request.
var errNoMessageID = errors.New("send was accepted but returned no message id")

// messageIDOf reads the id out of a send response.
//
// SentMessage carries the id under TWO names, `message_id` and `id`, and which
// one the server populates for a given mutation is not decidable from here:
// upstream mautrix-meta reads `id` for a send (handlematrix.go:176) and
// `message_id` for inbound deltas that reactions then target, which can only
// work because the two agree. Reading both and refusing an empty result is the
// only form that is correct whichever one turns out to be filled.
func messageIDOf(msg slidetypes.SentMessage) string {
	if msg.MessageID != "" {
		return msg.MessageID
	}
	return msg.ID
}

// sendText posts a text message, optionally carrying mentions and a reply.
//
// NO LENGTH CAP IS APPLIED. mautrix-meta's capabilities.go declares
// MaxTextLength = 1000, and that is its own conservative policy, not the
// protocol's: measured on the wire, 1,001 / 2,000 / 5,000 / 10,000 / 20,000
// characters were all accepted and 50,000 was refused with "Message Too Long".
// Enforcing 1000 here would import a foreign policy constant into Nora's reply
// budget and make it unraisable. The binding cross-platform limit stays
// Discord's 2,000, which lives in Nora, not here.
func sendText(
	ctx context.Context, cli igClient, shortThreadID, text string,
	mentions []eventMention, replyTo string,
) (sendResult, error) {
	// `IGThreadIGID` is a MISNOMER in the library, the same way
	// Delta.ThreadIGID is on the inbound side: upstream puts `meta.IGID` — the
	// SHORT id — in this field (from-matrix.go:81), and a send addressed that
	// way was measured arriving. Do not let the field name talk you into the
	// long one.
	req := &slidetypes.SendTextRequest{
		IGThreadIGID:       &shortThreadID,
		OfflineThreadingID: offlineThreadingID(),
		Text:               slidetypes.SensitiveString{Value: text},
	}
	for _, m := range mentions {
		fbid, err := strconv.ParseInt(m.FBID, 10, 64)
		if err != nil {
			// Do NOT validate a mention id by shape beyond "is it a number".
			// The measured interop fbids run 15 and 17 digits and share
			// prefixes with each other; any length or prefix rule would reject
			// real members.
			return sendResult{}, fmt.Errorf("mention fbid %q is not numeric", m.FBID)
		}
		req.Mentions = append(req.Mentions, slidetypes.InputMention{
			Offset: m.Offset, Length: m.Length, FBID: fbid,
		})
		// Both fields are populated because both were populated in the
		// measured send that round-tripped. Which one Instagram actually reads
		// was not isolated, and dropping the one that turns out to matter
		// produces a mention that renders as plain text — the exact Discord
		// plaintext-tag failure this adaptor exists to avoid repeating.
		req.MentionedUserIDs = append(req.MentionedUserIDs, m.FBID)
	}
	if replyTo != "" {
		req.ReplyToMessageID = &replyTo
		req.RepliedToItemID = &replyTo
	}
	resp, err := cli.SendMessage(ctx, req)
	if err != nil {
		return sendResult{}, err
	}
	if resp == nil {
		return sendResult{}, errNoMessageID
	}
	return resultOf(resp.GetMessage())
}

// sendMedia uploads bytes and then sends the resulting attachment.
//
// There is NO caption parameter and that is structural, not an omission:
// slidetypes.SendMediaRequest has fields for attachment, thread, threading id
// and reply, and no text field of any kind. A caption cannot be expressed on
// this wire.
//
// This INVERTS the WhatsApp adapter, where send.py deliberately puts the reply
// text in `caption` on the first attachment and then skips /send so the group
// does not hear the reply twice. Copying that shape here would silently drop
// the text. On Instagram the caller sends /send first, then /send-media.
//
// BOTH LEGS take the short id, and it is taken as ONE int64 so the two cannot
// disagree. The upload always needed it (it builds a referer of
// /direct/t/<short>/); the send now matches upstream (from-matrix.go:95,
// `meta.IGID`). Passing the two separately — as this function did while the
// send leg was long — is an invitation to hand it a thread and a different
// thread.
func sendMedia(
	ctx context.Context, cli igClient, up uploader,
	shortThreadID int64,
	media *httpclient.MercuryUploadMedia, replyTo string,
) (sendResult, error) {
	resp, err := up(ctx, shortThreadID, media)
	if err != nil {
		return sendResult{}, fmt.Errorf("upload: %w", err)
	}
	if resp == nil || resp.Payload.RealMetadata == nil {
		return sendResult{}, errors.New("upload returned no attachment metadata")
	}
	attachID := resp.Payload.RealMetadata.GetFbId()
	if attachID == 0 {
		return sendResult{}, errors.New("upload returned attachment fbid 0")
	}

	req := &slidetypes.SendMediaRequest{
		AttachmentFBID:     strconv.FormatInt(attachID, 10),
		ThreadID:           strconv.FormatInt(shortThreadID, 10),
		OfflineThreadingID: offlineThreadingID(),
	}
	if replyTo != "" {
		req.ReplyToMessageID = &replyTo
	}
	sent, err := cli.SendMedia(ctx, req)
	if err != nil {
		return sendResult{}, err
	}
	if sent == nil {
		return sendResult{}, errNoMessageID
	}
	return resultOf(sent.GetMessage())
}

// resultOf is the one place a send turns into a 200 body, so it is the one
// place that can refuse to.
func resultOf(msg slidetypes.SentMessage) (sendResult, error) {
	id := messageIDOf(msg)
	if id == "" {
		return sendResult{}, errNoMessageID
	}
	return sendResult{OK: true, ID: id, TimestampMS: unixMilliOrZero(msg)}, nil
}

// errReactionNotApplied is the read-back failing, not the request failing.
//
// MEASURED 2026-09-22: a reaction addressed with the long thread id is
// accepted — HTTP 200, no error value, nothing in the body that reads as a
// refusal — and is simply not in the thread afterwards. The ONLY signal that
// separates that from a real reaction is the post-state list the mutation
// returns, which is why it is read here instead of discarded.
var errReactionNotApplied = errors.New("reaction mutation was accepted but the reaction is not on the message")

// errReactionUnverifiable is the fail-closed half: with no own fbid there is
// nothing to look for in the post-state list, so the mutation cannot be
// confirmed. That is an ERROR and never a PASS — reporting 200 for an
// unverifiable reaction is exactly the defect above, one layer up.
var errReactionUnverifiable = errors.New("cannot verify the reaction: own fbid is unknown")

// sendReaction posts (or retracts) one reaction and then CHECKS IT LANDED.
//
// shortThreadID, not the long one — see the table at the top of this file.
func sendReaction(
	ctx context.Context, cli igClient,
	shortThreadID, messageID, emoji string, remove bool, ownFBID int64,
) error {
	status := slidetypes.ReactionStatusCreated
	if remove {
		status = slidetypes.ReactionStatusDeleted
		// Instagram still wants the emoji on a removal — the mutation
		// identifies WHICH reaction is being retracted, not just that one is.
	}
	resp, err := cli.SendReaction(ctx, &slidetypes.CreateReactionRequest{
		Input: slidetypes.ReactionInput{
			Emoji:          emoji,
			MessageID:      messageID,
			ItemID:         messageID,
			ReactionStatus: status,
			ThreadID:       shortThreadID,
		},
	})
	if err != nil {
		return err
	}
	if resp == nil {
		return errReactionNotApplied
	}
	if ownFBID == 0 {
		return errReactionUnverifiable
	}
	// Reactions is the POST-state of the message, which is what upstream
	// scans for its own SenderFBID to date the reaction it just made
	// (handlematrix.go:214). Here it answers the stronger question: is the
	// reaction there at all?
	var mine bool
	for _, r := range resp.Message.Reactions {
		if r.SenderFBID == ownFBID {
			mine = true
			break
		}
	}
	if mine == remove {
		// create + absent, or remove + still present. Either way the wire
		// said yes and the thread disagrees.
		return fmt.Errorf("%w (remove=%t)", errReactionNotApplied, remove)
	}
	return nil
}

// unsendMessage retracts a message and REPORTS, rather than decides, what the
// mutation said about it.
//
// `direct_unsend_message` is the whole response body and it is a boolean, so a
// false there reads like the mutation saying it did nothing. It is reported
// and NOT turned into a failure, and the asymmetry is deliberate:
//
//   - No evidence says the field is a real success signal. Upstream discards
//     it outright (`_, err = ic.Client.UnsendMessage(...)`,
//     handlematrix.go:289), and the only sibling bool in that response file
//     (`xig_direct_mark_read_id_validation`) is unread too. Nothing here has
//     ever observed its value against an unsend known to have failed.
//   - The cost of being wrong is asymmetric. A false 500 on /unsend strands
//     probe messages in a REAL group: the cleanup path fails, a human is told
//     the message is still there, and it gets deleted by hand or not at all.
//     A missed signal only loses a warning nobody had before.
//
// So: 200, with the observation attached. If the field is ever read back
// against an unsend known to have failed, this becomes an error and the test
// that pins the degradation is the one to delete.
func unsendMessage(ctx context.Context, cli igClient, longThreadID, messageID string) (confirmed bool, err error) {
	resp, err := cli.UnsendMessage(ctx, &slidetypes.UnsendMessageRequest{
		MessageID: messageID,
		SendData:  slidetypes.SendData{ThreadID: longThreadID},
	})
	if err != nil {
		return false, err
	}
	return resp != nil && resp.DirectUnsendMessage, nil
}

// unixMilliOrZero guards the same zero-time trap normalizeMessage does:
// UnixMilli() on a zero time.Time is a large negative number, and
// `timestamp_ms` is what a later reply's quote resolves against.
func unixMilliOrZero(msg slidetypes.SentMessage) int64 {
	if msg.TimestampMS.IsZero() {
		return 0
	}
	return msg.TimestampMS.UnixMilli()
}
