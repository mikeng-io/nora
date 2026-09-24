package httptransport

// The write routes, as resources of a thread
// (design/instagram-adoption/16 §Resources, 17 §P14):
//
//	POST   /threads/{thread_fbid}/messages                          text or media
//	DELETE /threads/{thread_fbid}/messages/{message_id}             unsend
//	PUT    /threads/{thread_fbid}/messages/{message_id}/reaction    react
//	PUT    /threads/{thread_fbid}/typing                            typing
//
// They replace `POST /send`, `POST /send-media`, `POST /unsend`,
// `POST /react` and `POST /typing`, which P14 DELETED in the same change that
// moved Nora's Python adapter: an old path is now the mux's own 404.
//
// # Order: path ids, then the body, then the use case
//
// Every path id is parsed first and the body validated next, so a malformed
// request is 400 before the use case runs, and so before the session gate
// inside it. A checkpointed session answers 409 only to a well-formed request.
//
// # The room key is the SHORT thread id, on every route
//
// Which id a verb takes UPSTREAM is a per-verb fact the gateway owns (unsend
// is the long id, everything else the short one). The caller only ever holds
// the short id, and the long one never crosses this boundary.
//
// # What never leaves in telemetry
//
// MESSAGE TEXT goes into the upstream request and nowhere else: not a span
// attribute, event, name or status, not a log line, not an error envelope.
// The server span carries the route pattern, the method and the path, whose
// ids (thread, message) 16 §Tracing allows. The text is in the BODY, which no
// span reads. On failure, the gateway replaces the upstream error's text with
// a fixed one before it reaches a span or this layer (instameow's
// sendTextError), because an upstream refusal can quote what it refused.

import (
	"encoding/base64"
	"errors"
	"net/http"

	"instagram-bridge/internal/domain"
)

const (
	// maxJSONBody bounds every write route's body except a media message.
	// Generous relative to a message (20,000 characters were measured as
	// accepted on the wire) and small enough that a runaway caller cannot
	// exhaust the container.
	maxJSONBody = 1 << 20 // 1 MiB
	// maxMediaBody bounds a MEDIA message's body. base64 inflates by 4/3, so
	// this admits roughly a 24 MB file.
	maxMediaBody = 32 << 20 // 32 MiB
	// maxEmojiBytes bounds a reaction. One emoji, including a family or a
	// flag sequence, is well under this.
	maxEmojiBytes = 64
)

func messageFromPath(w http.ResponseWriter, r *http.Request) (domain.MessageID, bool) {
	id, err := domain.ParseMessageID(r.PathValue("message_id"))
	if err != nil {
		invalidRequest(w, "message_id must be 1-256 printable characters")
		return domain.MessageID{}, false
	}
	return id, true
}

// replyToOf parses an optional `reply_to`: absent is no thread, anything else
// must be a message id.
func replyToOf(w http.ResponseWriter, raw string) (string, bool) {
	if raw == "" {
		return "", true
	}
	id, err := domain.ParseMessageID(raw)
	if err != nil {
		invalidRequest(w, "reply_to must be a message id")
		return "", false
	}
	return id.String(), true
}

// --- POST /threads/{thread_fbid}/messages --------------------------------------

// messageRequest is ONE message: text, or one attachment. Never both, because
// the wire cannot carry both: a media message has no text field of any kind
// (slidetypes.SendMediaRequest), so a caption cannot be expressed. The caller
// sends the text as one message and each attachment as another.
//
// This INVERTS the WhatsApp adapter, which puts the reply text in `caption` on
// the first attachment. Copying that shape here would silently lose the text,
// which is why a body carrying both is a 400 rather than a text message with
// the media dropped (or the reverse). A `caption` key is not a field and is
// ignored like any unknown key.
type messageRequest struct {
	Text     string        `json:"text"`
	Mentions []mentionBody `json:"mentions"`
	Media    *mediaBody    `json:"media"`
	ReplyTo  string        `json:"reply_to"`
}

type mediaBody struct {
	DataBase64 string `json:"data_base64"`
	Filename   string `json:"filename"`
	Mimetype   string `json:"mimetype"`
	// VoiceNote routes the upload through Instagram's voice-clip form field.
	// UNMEASURED: no voice note was ever sent by these probes, inbound or out.
	VoiceNote bool `json:"voice_note"`
}

// handleSendMessage answers POST /threads/{thread_fbid}/messages with the
// message Instagram accepted and named: `{"id", "timestamp_ms"}`.
func (s *Server) handleSendMessage(w http.ResponseWriter, r *http.Request) {
	room, ok := threadFromPath(w, r)
	if !ok {
		return
	}
	var req messageRequest
	raw, ok := readJSON(w, r, maxMediaBody, &req)
	if !ok {
		return
	}
	replyTo, ok := replyToOf(w, req.ReplyTo)
	if !ok {
		return
	}
	var (
		sent domain.Sent
		err  error
	)
	switch {
	case req.Media != nil && (req.Text != "" || len(req.Mentions) > 0):
		invalidRequest(w, "a message is text or media, not both: send the text as its own message")
		return
	case req.Media != nil:
		upload, ok := uploadOf(w, req.Media)
		if !ok {
			return
		}
		sent, err = s.app.SendMedia(r.Context(), room, upload, replyTo)
	default:
		// A text message is bounded tighter than a media one; the body was
		// read under the media limit because which it is was not yet known.
		if len(raw) > maxJSONBody {
			writeErrorEnvelope(w, http.StatusRequestEntityTooLarge, "too_large", "payload too large")
			return
		}
		if req.Text == "" {
			invalidRequest(w, "text or media is required")
			return
		}
		mentions, ok := mentionsOf(w, req.Mentions)
		if !ok {
			return
		}
		sent, err = s.app.SendText(r.Context(), room, req.Text, mentions, replyTo)
	}
	if err != nil {
		failEnvelope(r.Context(), w, "send_message", err, "thread_fbid", room.String())
		return
	}
	writeJSON(w, http.StatusOK, sentBody{ID: sent.ID, TimestampMS: sent.TimestampMS})
}

func uploadOf(w http.ResponseWriter, m *mediaBody) (domain.Upload, bool) {
	if m.DataBase64 == "" {
		invalidRequest(w, "media.data_base64 is required")
		return domain.Upload{}, false
	}
	data, err := decodeBase64(m.DataBase64)
	if err != nil {
		invalidRequest(w, "media.data_base64 is not valid base64")
		return domain.Upload{}, false
	}
	if len(data) == 0 {
		invalidRequest(w, "media.data_base64 decoded to zero bytes")
		return domain.Upload{}, false
	}
	return domain.Upload{
		Filename:  orDefault(m.Filename, "upload.bin"),
		MimeType:  orDefault(m.Mimetype, "application/octet-stream"),
		Data:      data,
		VoiceNote: m.VoiceNote,
	}, true
}

// mentionsOf validates each mention at the edge: its fbid parses as a
// messaging id and its span is non-negative. Before P14 a non-numeric fbid
// reached the gateway and came back as an unclassified 500; it is the caller's
// malformed request, so it is a 400 here.
func mentionsOf(w http.ResponseWriter, in []mentionBody) ([]domain.Mention, bool) {
	out := make([]domain.Mention, 0, len(in))
	for _, m := range in {
		fbid, err := domain.ParseFBID(m.FBID)
		if err != nil || m.Offset < 0 || m.Length < 0 {
			invalidRequest(w, "each mention needs a decimal fbid and a non-negative offset and length")
			return nil, false
		}
		out = append(out, domain.Mention{FBID: fbid.String(), Offset: m.Offset, Length: m.Length})
	}
	return out, true
}

// --- DELETE /threads/{thread_fbid}/messages/{message_id} ------------------------

// confirmedBody is the 200 of a mutation whose post-state Instagram may not
// vouch for: unsend and reaction.
//
// `confirmed: false` is REPORTED, not failed. The mutation was accepted and
// only its own response stayed silent about the result. For a reaction that
// was measured on every reaction Nora made while the reaction landed in the
// thread; for an unsend, `direct_unsend_message` has never been observed
// against an unsend known to have failed, and a false failure would strand a
// message in a real group. The field is always present now: with no `ok`
// beside it, an empty object would say nothing at all.
type confirmedBody struct {
	Confirmed bool `json:"confirmed"`
}

func (s *Server) handleUnsend(w http.ResponseWriter, r *http.Request) {
	room, ok := threadFromPath(w, r)
	if !ok {
		return
	}
	msg, ok := messageFromPath(w, r)
	if !ok {
		return
	}
	confirmed, err := s.app.Unsend(r.Context(), room, msg)
	if err != nil {
		failEnvelope(r.Context(), w, "unsend", err, "thread_fbid", room.String())
		return
	}
	writeJSON(w, http.StatusOK, confirmedBody{Confirmed: confirmed})
}

// --- PUT /threads/{thread_fbid}/messages/{message_id}/reaction -------------------

// reactionRequest is the reaction Nora's account holds on one message.
//
// `remove` stays a field of this body rather than becoming a DELETE of the
// resource, because the mutation that retracts a reaction must name WHICH
// emoji it retracts, and a DELETE has no body to carry it in (design 16
// §Resources names only this PUT). The emoji is required on a removal too.
type reactionRequest struct {
	Emoji  string `json:"emoji"`
	Remove bool   `json:"remove"`
}

func (s *Server) handleReaction(w http.ResponseWriter, r *http.Request) {
	room, ok := threadFromPath(w, r)
	if !ok {
		return
	}
	msg, ok := messageFromPath(w, r)
	if !ok {
		return
	}
	var req reactionRequest
	if _, ok := readJSON(w, r, maxJSONBody, &req); !ok {
		return
	}
	if req.Emoji == "" || len(req.Emoji) > maxEmojiBytes {
		invalidRequest(w, "emoji is required (also when remove is true), at most 64 bytes")
		return
	}
	confirmed, err := s.app.React(r.Context(), room, msg, req.Emoji, req.Remove)
	if err != nil {
		failEnvelope(r.Context(), w, "reaction", err, "thread_fbid", room.String())
		return
	}
	writeJSON(w, http.StatusOK, confirmedBody{Confirmed: confirmed})
}

// --- PUT /threads/{thread_fbid}/typing -------------------------------------------

// typingBody is both the request and the 200: whether Nora is shown typing.
// A pointer on the request, so a body with no `typing` at all is a 400 rather
// than a silent "stop typing".
type typingRequest struct {
	Typing *bool `json:"typing"`
}

type typingBody struct {
	Typing bool `json:"typing"`
}

// handleTyping is the one route that genuinely needs the realtime socket, so
// the only one that answers 503 on `connected == false`.
func (s *Server) handleTyping(w http.ResponseWriter, r *http.Request) {
	room, ok := threadFromPath(w, r)
	if !ok {
		return
	}
	var req typingRequest
	if _, ok := readJSON(w, r, maxJSONBody, &req); !ok {
		return
	}
	if req.Typing == nil {
		invalidRequest(w, "typing is required")
		return
	}
	if err := s.app.Typing(r.Context(), room, *req.Typing); err != nil {
		failEnvelope(r.Context(), w, "typing", err, "thread_fbid", room.String())
		return
	}
	writeJSON(w, http.StatusOK, typingBody{Typing: *req.Typing})
}

// decodeBase64 accepts both the standard and the URL-safe alphabet, and both
// padded and unpadded. The caller is another program, not a browser, and a
// send refused over an alphabet choice would be a failure with a misleading
// message ("not valid base64") for bytes that were perfectly fine.
func decodeBase64(s string) ([]byte, error) {
	for _, enc := range []*base64.Encoding{
		base64.StdEncoding, base64.RawStdEncoding,
		base64.URLEncoding, base64.RawURLEncoding,
	} {
		if data, err := enc.DecodeString(s); err == nil {
			return data, nil
		}
	}
	return nil, errors.New("not valid base64")
}

func orDefault(v, fallback string) string {
	if v == "" {
		return fallback
	}
	return v
}
