package main

// Outbound: the send half, and the trap that makes it non-trivial.
//
// THE TRAP. Instagram gives one thread two numeric identities and they are not
// interchangeable. Inbound deltas carry the SHORT id (`thread_fbid`,
// measured `1009543911792316`); every send — text, media, reaction, edit,
// unsend — requires the LONG one (`thread_igid`,
// `340282366841710301281152937372201004392`). A send addressed with the short
// id does not arrive, and does not error usefully either.
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

// resolve maps a short thread id to the long one, fetching on a miss.
func (t *threadIndex) resolve(ctx context.Context, short string) (string, error) {
	// Parse before anything else: `room_id` arrives from HTTP and a non-numeric
	// value is a caller error (400), not an Instagram failure (5xx). Letting it
	// through would produce a route lookup for `/direct/t/0/` and a 404 that
	// reads like a deleted thread.
	shortID, err := strconv.ParseInt(short, 10, 64)
	if err != nil || shortID <= 0 {
		return "", fmt.Errorf("%w: %q", errBadRoomID, short)
	}

	t.mu.RLock()
	long, ok := t.shortLong[short]
	t.mu.RUnlock()
	if ok {
		return long, nil
	}

	ids, err := t.fetch(ctx, shortID)
	if err != nil {
		return "", err
	}
	if ids == nil || ids.LongID == "" {
		// Fail closed. Returning the short id as a "best effort" would produce
		// a send that is accepted by the HTTP layer and never arrives, which
		// is the single worst outcome available here.
		return "", fmt.Errorf("thread %s resolved to no thread_igid", short)
	}
	t.remember(short, ids.LongID)
	return ids.LongID, nil
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
	ctx context.Context, cli igClient, longThreadID, text string,
	mentions []eventMention, replyTo string,
) (sendResult, error) {
	req := &slidetypes.SendTextRequest{
		IGThreadIGID:       &longThreadID,
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
	msg := resp.GetMessage()
	return sendResult{OK: true, ID: msg.MessageID, TimestampMS: unixMilliOrZero(msg)}, nil
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
// Note the two id spaces appear in the same function: the UPLOAD is addressed
// with the short id (it builds a referer of /direct/t/<short>/), the SEND with
// the long one.
func sendMedia(
	ctx context.Context, cli igClient, up uploader,
	shortThreadID int64, longThreadID string,
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
		ThreadID:           longThreadID,
		OfflineThreadingID: offlineThreadingID(),
	}
	if replyTo != "" {
		req.ReplyToMessageID = &replyTo
	}
	sent, err := cli.SendMedia(ctx, req)
	if err != nil {
		return sendResult{}, err
	}
	msg := sent.GetMessage()
	return sendResult{OK: true, ID: msg.MessageID, TimestampMS: unixMilliOrZero(msg)}, nil
}

func sendReaction(ctx context.Context, cli igClient, longThreadID, messageID, emoji string, remove bool) error {
	status := slidetypes.ReactionStatusCreated
	if remove {
		status = slidetypes.ReactionStatusDeleted
		// Instagram still wants the emoji on a removal — the mutation
		// identifies WHICH reaction is being retracted, not just that one is.
	}
	_, err := cli.SendReaction(ctx, &slidetypes.CreateReactionRequest{
		Input: slidetypes.ReactionInput{
			Emoji:          emoji,
			MessageID:      messageID,
			ItemID:         messageID,
			ReactionStatus: status,
			ThreadID:       longThreadID,
		},
	})
	return err
}

func unsendMessage(ctx context.Context, cli igClient, longThreadID, messageID string) error {
	_, err := cli.UnsendMessage(ctx, &slidetypes.UnsendMessageRequest{
		MessageID: messageID,
		SendData:  slidetypes.SendData{ThreadID: longThreadID},
	})
	return err
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
