package main

// Inbound normalization: instameow's typed delta -> the JSON on GET /events.
//
// The shapes here are fixed by design/instagram-adoption/03-transport-contract.md
// and nothing else. Drift between this file and that document is a contract
// failure, not a judgement call — the Python adapter is written against the
// document, not against this code.
//
// Two rules govern everything below.
//
// 1. DECODE, NEVER SCRAPE. Every URL comes out of a typed struct field
//    (slidetypes.Attachment.AttachmentCDNURL and friends), never out of a
//    regex over the raw payload. Instagram's CDN urls carry `&` inside query
//    strings; a regex that stops one character early, or that leaves a raw `&`
//    where the JSON had an escape, produces a url Instagram answers with
//    `403 Bad URL hash` — which reads exactly like an expired session and
//    sends the reader hunting for an auth bug that does not exist.
//
// 2. FOUR MEDIA SHAPES, NOT ONE. Images, video, animated stickers and
//    view-once each nest their attachment under a DIFFERENT key. A single
//    generic `attachments[]` path silently returns nothing for three of the
//    four, and "no media" is not distinguishable downstream from "media the
//    parser could not see".

import (
	"strconv"

	"go.mau.fi/mautrix-meta/pkg/instameow/slidetypes"
)

// Event kinds on the SSE stream. `message` is the contract's; the rest are
// documented extensions — the contract's §"Inbound event shape" defines only
// the message object, and an adapter is expected to ignore kinds it does not
// know (the same posture whatsapp-bridge's `type` discriminator takes).
const (
	kindMessage   = "message"
	kindReaction  = "reaction"
	kindEdit      = "edit"
	kindUnsend    = "unsend"
	kindTyping    = "typing"
	kindHeartbeat = "heartbeat"
	kindDropped   = "dropped"
)

// Media kinds. `image`, `video` and `animated` are the contract's; `audio` is
// added for voice notes, which 04-capability-evidence.md lists as structurally
// supported but UNMEASURED — it is emitted rather than dropped because in Nora
// a voice note engages unconditionally, so swallowing one is a silent loss of
// an engagement, not a cosmetic gap.
const (
	mediaImage    = "image"
	mediaVideo    = "video"
	mediaAnimated = "animated"
	mediaAudio    = "audio"
	mediaOneTime  = "one_time"
)

type eventSender struct {
	// FBID is `interop_messaging_user_fbid`: the messaging identity. It is
	// what sender.id carries, what mentions[].user_fbid carries, and what
	// GetOwnFBID() returns. R-12's "was this me?" binds on THIS field.
	FBID string `json:"fbid"`
	// IGID is the public Instagram numeric id, which is what GetProfile and
	// FetchReel take. Both are emitted because different subsystems need
	// different ones and neither can be derived from the other.
	IGID     string `json:"igid"`
	Username string `json:"username"`
}

// eventMention is one span of tagged text.
//
// RangeType is `profile_range_type` passed through, and it is not decoration:
// for `THREAD` (Instagram's @everyone) `user_fbid` carries the CHAT id, not a
// person's — the library says so on the field itself
// (slidetypes.Mention.UserFBID). Emitting that number unlabelled means it can
// never equal Nora's own fbid, so an @everyone that names the whole room reads
// as a mention of somebody who is not her and she silently never answers it.
// The value still goes out, because the adapter may want the room id; what it
// needs is to be told which question the number answers.
//
// The same struct is what /send accepts for outbound mentions, where only
// fbid/offset/length are read: a caller cannot ask for an @everyone by setting
// this field, and sendText ignores it.
type eventMention struct {
	FBID      string `json:"fbid"`
	Offset    int    `json:"offset"`
	Length    int    `json:"length"`
	RangeType string `json:"range_type,omitempty"`
}

// eventReply is the contract's `reply_to`, which the document only ever shows
// as `null`. The populated shape is this bridge's choice: the message id is
// the part the adapter needs to hang a quote off, and the author/timestamp
// ride along because the delta already carries them and a later lookup could
// not recover them.
type eventReply struct {
	MessageID   string `json:"message_id"`
	SenderFBID  string `json:"sender_fbid,omitempty"`
	TimestampMS int64  `json:"timestamp_ms,omitempty"`
	Text        string `json:"text,omitempty"`
}

type eventMedia struct {
	Kind string `json:"kind"`
	// URL is a pointer so that a one-time message serializes as an explicit
	// `"url": null` rather than `""`. The contract spells that case out, and
	// the difference matters: null says "these bytes exist and we chose not to
	// fetch them", empty string would say "we looked and found nothing".
	URL            *string `json:"url"`
	AttachmentFBID string  `json:"attachment_fbid,omitempty"`
	Width          int     `json:"width,omitempty"`
	Height         int     `json:"height,omitempty"`
	DurationMS     int     `json:"duration_ms,omitempty"`
	// OneTime and ViewMode are only set for RAVEN_* content. view_mode 2
	// ("keep in chat") arrives WITH its attachment and behaves like an
	// ordinary image; 0 and 1 arrive with `attachment: null`. Keeping the flag
	// alongside a resolvable url means the adapter can still tell that the
	// sender chose an ephemeral mode, which a bare `kind: "image"` would erase.
	OneTime  bool `json:"one_time,omitempty"`
	ViewMode *int `json:"view_mode,omitempty"`
}

type messageEvent struct {
	Kind        string         `json:"kind"`
	RoomID      string         `json:"room_id"`
	MessageID   string         `json:"message_id"`
	Sender      eventSender    `json:"sender"`
	FromMe      bool           `json:"from_me"`
	TimestampMS int64          `json:"timestamp_ms"`
	Text        string         `json:"text"`
	Mentions    []eventMention `json:"mentions"`
	ReplyTo     *eventReply    `json:"reply_to"`
	Media       *eventMedia    `json:"media"`
	// ContentType is Instagram's own `content_type` (TEXT, IMAGES, VIDEOS,
	// RAVEN_IMAGE, ADD_PARTICIPANT_XMAT, …) passed through unchanged. It is
	// not part of the contract's field list; it is here because the system
	// events arrive as ordinary messages and this is the only field that
	// distinguishes "someone spoke" from "someone was added to the room".
	ContentType string `json:"content_type,omitempty"`
}

// normalizeMessage turns one slidetypes.Message into the contract's inbound
// event. ownFBID is the value of client.GetOwnFBID(); it is passed in rather
// than read from a package global so this function is a pure mapping and can
// be tested against a captured delta with no client at all.
func normalizeMessage(threadFBID string, msg *slidetypes.Message, ownFBID int64) *messageEvent {
	if msg == nil {
		return nil
	}
	room := msg.ThreadFBID
	if room == "" {
		// Delta.ThreadIGID is a misnomer in the library: its JSON tag is
		// `thread_fbid`, so it carries the SHORT id despite the field name.
		// It is the fallback for events whose message body omits the thread.
		room = threadFBID
	}

	ev := &messageEvent{
		Kind:        kindMessage,
		RoomID:      room,
		MessageID:   msg.MessageID,
		FromMe:      ownFBID != 0 && msg.SenderFBID == ownFBID,
		Text:        msg.TextBody,
		ContentType: msg.ContentType,
		// Always a non-nil slice. `"mentions": []` and `"mentions": null` are
		// the same thing to a careful reader and different things to a careless
		// one; emitting the empty array removes the question.
		Mentions: []eventMention{},
		Media:    extractMedia(msg),
	}
	if !msg.TimestampMS.IsZero() {
		// A zero jsontime.UnixMilliString is the zero time.Time, and
		// UnixMilli() on that is a large NEGATIVE number, not 0. Emitting it
		// would date the message to the year 1754 and quietly poison anything
		// that orders or ages messages.
		ev.TimestampMS = msg.TimestampMS.UnixMilli()
	}

	ev.Sender = senderOf(msg)

	for _, m := range msg.Mentions {
		if m == nil {
			continue
		}
		ev.Mentions = append(ev.Mentions, eventMention{
			FBID:      strconv.FormatInt(m.UserFBID, 10),
			Offset:    m.Offset,
			Length:    m.Length,
			RangeType: string(m.ProfileRangeType),
		})
	}

	ev.ReplyTo = replyOf(msg)
	return ev
}

func senderOf(msg *slidetypes.Message) eventSender {
	out := eventSender{FBID: strconv.FormatInt(msg.SenderFBID, 10)}
	if msg.SenderFBID == 0 {
		out.FBID = ""
	}
	if msg.Sender == nil {
		return out
	}
	// sender.id and sender_fbid were identical in every measured delta, but
	// sender_fbid is the field the whole identity chain is specified on, so it
	// stays authoritative and sender.id is only a fallback for the case where
	// the numeric field failed to parse.
	if out.FBID == "" && msg.Sender.ID != "" {
		out.FBID = msg.Sender.ID
	}
	out.IGID = msg.Sender.IGID
	out.Username = msg.Sender.UserDict.Username
	if out.IGID == "" {
		out.IGID = msg.Sender.UserDict.ID
	}
	return out
}

func replyOf(msg *slidetypes.Message) *eventReply {
	parent := msg.RepliedToMessage
	if parent == nil {
		if msg.RepliedToMessageID == "" {
			return nil
		}
		// The relation survives even when the parent body does not. This is
		// the same honest degradation whatsapp-bridge takes on a quote older
		// than its cache: the pointer is reported, the content is not invented.
		return &eventReply{MessageID: msg.RepliedToMessageID}
	}
	out := &eventReply{MessageID: parent.MessageID, Text: parent.TextBody}
	if out.MessageID == "" {
		out.MessageID = msg.RepliedToMessageID
	}
	if parent.SenderFBID != 0 {
		out.SenderFBID = strconv.FormatInt(parent.SenderFBID, 10)
	}
	if !parent.TimestampMS.IsZero() {
		out.TimestampMS = parent.TimestampMS.UnixMilli()
	}
	return out
}

// extractMedia is the four-shapes table from 03-transport-contract.md, in
// code. The switch is on the DECODED content type rather than on
// msg.ContentType because instameow has already done the discrimination
// (MessageContentWrapper.UnmarshalJSON) and re-deriving it from the string
// would be a second interpretation of the same input, free to disagree with
// the first.
func extractMedia(msg *slidetypes.Message) *eventMedia {
	switch c := msg.Content.Content.(type) {
	case *slidetypes.MessageContentImage:
		return fromAttachment(mediaImage, firstAttachment(c.Attachments))

	case *slidetypes.MessageContentVideo:
		// VIDEOS nests under `videos`, NOT `attachments`. This one line is the
		// whole reason a generic attachment path does not work.
		return fromAttachment(mediaVideo, firstAttachment(c.Videos))

	case *slidetypes.MessageContentAnimatedMedia:
		return fromAnimated(c.AnimatedMedia)

	case *slidetypes.MessageContentAudio:
		return fromAudio(c.AudioAttachments)

	case *slidetypes.MessageContentRavenImage:
		return fromRaven(mediaImage, c.ViewMode, c.Attachment)

	case *slidetypes.MessageContentRavenVideo:
		return fromRaven(mediaVideo, c.ViewMode, c.Attachment)

	case *slidetypes.MessageContentMultiMedia:
		// UNMEASURED. `ordered_photo_video_attachments` is a fifth nesting the
		// probes never saw, so the kind is INFERRED from the presence of a
		// dash manifest rather than observed. Handled anyway because dropping
		// it would make a mixed photo/video post arrive as a text message with
		// no text — a message that looks empty rather than unparsed.
		att := firstAttachment(c.Attachments)
		if att != nil && att.DashManifest != "" {
			return fromAttachment(mediaVideo, att)
		}
		return fromAttachment(mediaImage, att)
	}
	return nil
}

func firstAttachment(atts []*slidetypes.Attachment) *slidetypes.Attachment {
	// Only the first attachment is surfaced: the contract's `media` is a
	// single object. Every measured multi-media delta carried exactly one, and
	// inventing an array shape the adapter has no parser for would be worse
	// than the known limitation.
	for _, a := range atts {
		if a != nil {
			return a
		}
	}
	return nil
}

func fromAttachment(kind string, att *slidetypes.Attachment) *eventMedia {
	if att == nil {
		return nil
	}
	url := att.AttachmentCDNURL
	if url == "" {
		url = att.AttachmentCDNFallbackURL
	}
	if url == "" {
		// The preview is a real, fetchable image — a smaller one. Falling back
		// to it beats emitting `url: null`, which the contract reserves for
		// "these bytes exist and we deliberately did not fetch them".
		url = att.PreviewCDNURL
	}
	if url == "" {
		return nil
	}
	return &eventMedia{
		Kind:           kind,
		URL:            &url,
		AttachmentFBID: att.AttachmentFBID,
		Width:          att.PreviewWidth,
		Height:         att.PreviewHeight,
	}
}

func fromAnimated(atts []*slidetypes.AnimatedAttachment) *eventMedia {
	for _, a := range atts {
		if a == nil {
			continue
		}
		// Preference order is "most useful to a consumer that wants to render
		// or transcribe it": the mp4 is the real animation, the webp is the
		// sticker still, the preview is the last resort. An animated sticker
		// carries no attachment_fbid — it lives on an external CDN, not in
		// Instagram's attachment store — so that field stays empty here.
		url := a.AttachmentMP4URL
		if url == "" {
			url = a.AttachmentWebpURL
		}
		if url == "" {
			url = a.PreviewCDNURL
		}
		if url == "" {
			continue
		}
		return &eventMedia{
			Kind:   mediaAnimated,
			URL:    &url,
			Width:  a.PreviewWidth,
			Height: a.PreviewHeight,
		}
	}
	return nil
}

func fromAudio(atts []*slidetypes.AudioAttachment) *eventMedia {
	for _, a := range atts {
		if a == nil || a.AttachmentCDNURL == "" {
			continue
		}
		url := a.AttachmentCDNURL
		return &eventMedia{
			Kind:           mediaAudio,
			URL:            &url,
			AttachmentFBID: a.AttachmentFBID,
			DurationMS:     a.PlayableDurationMS,
		}
	}
	return nil
}

// fromRaven handles view-once media, which is a CONSENT question wearing a
// parsing problem's clothes.
//
// view_mode 0 ("view once") and 1 ("allow replay") arrive with
// `attachment: null` — measured, in the live delta AND in thread history.
// Retrieving those bytes takes an explicit fetch which, on Instagram, IS the
// act of viewing and is visible to the sender. v1 does not do it: Nora learns
// something was sent and cannot see it, and whether she should be able to is
// the operator's decision, not a parser default.
//
// view_mode 2 ("keep in chat") is different in kind, not in degree: the
// attachment is already IN the delta. There is nothing to fetch and nothing to
// consent to, so those bytes are surfaced — with one_time still set, because
// the sender did choose an ephemeral mode and erasing that would misreport
// what happened.
func fromRaven(kind string, viewMode slidetypes.RavenViewMode, att *slidetypes.Attachment) *eventMedia {
	mode := int(viewMode)
	if att == nil {
		return &eventMedia{Kind: mediaOneTime, URL: nil, OneTime: true, ViewMode: &mode}
	}
	media := fromAttachment(kind, att)
	if media == nil {
		return &eventMedia{Kind: mediaOneTime, URL: nil, OneTime: true, ViewMode: &mode}
	}
	media.OneTime = true
	media.ViewMode = &mode
	return media
}

// --- the non-message events --------------------------------------------------

type reactionEvent struct {
	Kind        string `json:"kind"`
	RoomID      string `json:"room_id"`
	MessageID   string `json:"message_id"`
	SenderFBID  string `json:"sender_fbid"`
	Emoji       string `json:"emoji"`
	Removed     bool   `json:"removed"`
	FromMe      *bool  `json:"from_me"`
	TimestampMS int64  `json:"timestamp_ms,omitempty"`
}

func normalizeReaction(room string, r slidetypes.Reaction, messageID string, removed bool, ownFBID int64) *reactionEvent {
	ev := &reactionEvent{
		Kind:      kindReaction,
		RoomID:    room,
		MessageID: messageID,
		Emoji:     r.Reaction,
		Removed:   removed,
		FromMe:    fromMe(r.SenderFBID, ownFBID),
	}
	if r.SenderFBID != 0 {
		ev.SenderFBID = strconv.FormatInt(r.SenderFBID, 10)
	}
	if !r.ReactionTimestampMS.IsZero() {
		ev.TimestampMS = r.ReactionTimestampMS.UnixMilli()
	}
	return ev
}

// editEvent carries the NEW text. Like Signal and WhatsApp, an edit is its own
// row rather than a mutation of an earlier one — the audit trail is
// append-only, so the bridge reports the event and never rewrites history.
type editEvent struct {
	Kind      string `json:"kind"`
	RoomID    string `json:"room_id"`
	MessageID string `json:"message_id"`
	Text      string `json:"text"`
	// FromMe is a POINTER and it is null here, always, and that is the honest
	// answer rather than a gap. slidetypes.EditMessageEvent carries
	// message_id, the new body and a timestamp and NO sender field of any
	// kind, so who edited cannot be derived from the delta. `false` would be a
	// claim — that a member did it — and the account is also used from a
	// browser and a phone, so it is a claim that can be wrong. `null` says the
	// bridge does not know, which is a thing an adapter can branch on; a
	// confident `false` is not.
	FromMe      *bool `json:"from_me"`
	TimestampMS int64 `json:"timestamp_ms,omitempty"`
}

type unsendEvent struct {
	Kind      string `json:"kind"`
	RoomID    string `json:"room_id"`
	MessageID string `json:"message_id"`
	// Null for the same reason as editEvent's: DeleteMessageEvent is a message
	// id and nothing else.
	FromMe *bool `json:"from_me"`
}

// typingEvent deliberately does NOT claim a room_id it cannot prove.
//
// slidetypes.TypingNotification.ThreadID is parsed out of a stream-controller
// path and the probes never established which of Instagram's two thread id
// spaces it belongs to. Guessing would put a long id in a field the contract
// defines as the short one, and every consumer keying on room_id would miss
// silently. So: the raw value goes out under its own name, and room_id is
// filled only when the bridge's own short<->long cache can vouch for it.
type typingEvent struct {
	Kind       string `json:"kind"`
	RoomID     string `json:"room_id,omitempty"`
	ThreadID   string `json:"thread_id"`
	SenderFBID string `json:"sender_fbid,omitempty"`
	Typing     bool   `json:"typing"`
	// Derivable here, unlike on edit/unsend: the notification names its
	// sender. Null when it does not, or when our own fbid is unknown.
	FromMe *bool `json:"from_me"`
}

// fromMe answers "was this us?" and is allowed to answer "I cannot tell".
//
// Both zeroes are real cases: a delta that carries no sender, and a session
// whose own fbid never resolved (see ensureSession's GetOwnFBID branch). In
// either one a plain `false` would be indistinguishable from a confident "no",
// and the consequence of getting it wrong is Nora answering her own echo.
func fromMe(senderFBID, ownFBID int64) *bool {
	if senderFBID == 0 || ownFBID == 0 {
		return nil
	}
	v := senderFBID == ownFBID
	return &v
}

type heartbeatEvent struct {
	Kind      string       `json:"kind"`
	TSMS      int64        `json:"ts_ms"`
	Connected bool         `json:"connected"`
	Session   sessionState `json:"session"`
}

type droppedEvent struct {
	Kind         string `json:"kind"`
	DroppedTotal int    `json:"dropped_total"`
}
