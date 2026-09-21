package main

// The HTTP surface. Seven routes, exactly the table in
// design/instagram-adoption/03-transport-contract.md.
//
// The status mapping is the load-bearing part and it is NOT a generic
// success/failure split. The adapter's retry decision reads it directly:
//
//	200  delivered; body carries {ok, id, timestamp_ms}
//	409  the session is checkpointed or logged out — NOT retryable without a
//	     human. Retrying this is how a captcha becomes a ban.
//	503  socket down or reconnecting — retryable, the bridge heals itself
//	413  payload over a limit
//	400  the caller's request is malformed
//	500  Instagram refused for a reason the bridge has no name for
//
// Collapsing 409 into 503 would make the adapter hammer a challenged account
// forever; collapsing 503 into 500 would make it give up on a socket that
// reconnects in seconds. They are distinct because the consequences are.

import (
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"net/http"
	"strconv"
	"strings"
	"time"

	"go.mau.fi/mautrix-meta/pkg/messagix/httpclient"
)

var errNotConnected = errors.New("instagram session is not connected")

const (
	// maxJSONBody bounds the control routes. Generous relative to a message
	// (20,000 characters were measured as accepted on the wire) and small
	// enough that a runaway caller cannot exhaust the container.
	maxJSONBody = 1 << 20 // 1 MiB
	// maxMediaBody bounds /send-media. base64 inflates by 4/3, so this admits
	// roughly a 24 MB file.
	maxMediaBody = 32 << 20 // 32 MiB
)

func (b *bridge) routes() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", b.handleHealth)
	mux.HandleFunc("GET /events", b.handleEvents)
	mux.HandleFunc("POST /send", b.handleSend)
	mux.HandleFunc("POST /send-media", b.handleSendMedia)
	mux.HandleFunc("POST /react", b.handleReact)
	mux.HandleFunc("POST /typing", b.handleTyping)
	mux.HandleFunc("POST /unsend", b.handleUnsend)
	return mux
}

// --- /health ------------------------------------------------------------------

type healthBody struct {
	OK        bool         `json:"ok"`
	Session   sessionState `json:"session"`
	Connected bool         `json:"connected"`
	Seq       int64        `json:"seq"`
	Detail    string       `json:"detail,omitempty"`
	// Beyond the contract's four fields. `self` is here because the adapter's
	// "was I tagged?" test binds on an fbid it would otherwise have to carry
	// in config, and a config value that silently disagrees with the live
	// session is a failure with no symptom. The event counters are the
	// drop-and-count record: the number survives here permanently so a loss is
	// readable long after the SSE notice that announced it.
	Self           selfIdentity `json:"self"`
	DroppedEvents  int          `json:"dropped_events"`
	BufferedEvents int          `json:"buffered_events"`
	Clients        int          `json:"clients"`
	UptimeSeconds  int          `json:"uptime_seconds"`
}

// handleHealth ALWAYS answers 200 while the HTTP server serves.
//
// This is inherited verbatim from whatsapp-bridge and it matters more here:
// Instagram challenges are routine, so a checkpointed session is a normal
// operating state the bridge must DESCRIBE, not a condition to fail on. A
// non-200 would make the container healthcheck flap on an ordinary captcha
// and restart a process whose restart fixes nothing.
func (b *bridge) handleHealth(w http.ResponseWriter, r *http.Request) {
	state, detail, connected, seq := b.snapshot()
	clients, buffered, dropped := b.hub.stats()
	writeJSON(w, http.StatusOK, healthBody{
		OK:             true,
		Session:        state,
		Connected:      connected,
		Seq:            seq,
		Detail:         detail,
		Self:           b.selfSnapshot(),
		DroppedEvents:  dropped,
		BufferedEvents: buffered,
		Clients:        clients,
		UptimeSeconds:  int(time.Since(b.startedAt).Seconds()),
	})
}

// --- /events ------------------------------------------------------------------

func (b *bridge) handleEvents(w http.ResponseWriter, r *http.Request) {
	flusher, ok := w.(http.Flusher)
	if !ok {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": "streaming unsupported"})
		return
	}
	h := w.Header()
	h.Set("Content-Type", "text/event-stream")
	h.Set("Cache-Control", "no-cache")
	h.Set("Connection", "keep-alive")
	// Defensive against a reverse proxy nobody has put in front of this yet:
	// buffering an SSE stream turns live events into a batch delivered on
	// close, which reads as a dead bridge until it suddenly is not.
	h.Set("X-Accel-Buffering", "no")
	w.WriteHeader(http.StatusOK)
	fmt.Fprint(w, ":\n\n")
	flusher.Flush()

	ch, replay, droppedTotal := b.hub.subscribe()
	defer b.hub.unsubscribe(ch)

	// The drop notice goes FIRST, before the replay. A consumer that reads its
	// backlog and only then learns some of it is missing has already made
	// decisions on an incomplete record.
	if droppedTotal > 0 {
		if payload, err := json.Marshal(&droppedEvent{Kind: kindDropped, DroppedTotal: droppedTotal}); err == nil {
			writeSSE(w, payload)
		}
	}
	for _, payload := range replay {
		writeSSE(w, payload)
	}
	flusher.Flush()

	for {
		select {
		case <-r.Context().Done():
			return
		case payload, open := <-ch:
			if !open {
				return
			}
			writeSSE(w, payload)
			flusher.Flush()
		}
	}
}

func writeSSE(w http.ResponseWriter, payload []byte) {
	// One JSON object per `data:` line, exactly as the contract specifies.
	// The payloads are compact JSON from encoding/json, which never emits a
	// raw newline, so no multi-line framing is needed.
	fmt.Fprintf(w, "data: %s\n\n", payload)
}

// --- send routes --------------------------------------------------------------

type sendRequest struct {
	RoomID   string         `json:"room_id"`
	Text     string         `json:"text"`
	Mentions []eventMention `json:"mentions"`
	ReplyTo  string         `json:"reply_to"`
}

func (b *bridge) handleSend(w http.ResponseWriter, r *http.Request) {
	var req sendRequest
	if !readJSON(w, r, maxJSONBody, &req) {
		return
	}
	if req.Text == "" {
		badRequest(w, "text is required")
		return
	}
	// NO 1000-CHARACTER CAP. mautrix-meta declares MaxTextLength = 1000 as its
	// own policy; measured on this wire, 20,000 characters are accepted and
	// 50,000 are refused. Enforcing 1000 here would hand Nora's reply budget a
	// ceiling that does not exist, and a budget derived from a policy constant
	// is a budget nobody can raise.
	cli, ok := b.ready(w)
	if !ok {
		return
	}
	thread, ok := b.resolveRoom(w, r, req.RoomID)
	if !ok {
		return
	}
	res, err := sendText(r.Context(), cli, thread.Short, req.Text, req.Mentions, req.ReplyTo)
	if err != nil {
		b.failSend(w, "send", req.RoomID, err)
		return
	}
	writeJSON(w, http.StatusOK, res)
}

type sendMediaRequest struct {
	RoomID     string `json:"room_id"`
	DataBase64 string `json:"data_base64"`
	Filename   string `json:"filename"`
	Mimetype   string `json:"mimetype"`
	ReplyTo    string `json:"reply_to"`
	// VoiceNote routes the upload through Instagram's voice-clip form field.
	// UNMEASURED: no voice note was ever sent by these probes, inbound or out.
	VoiceNote bool `json:"voice_note"`
}

// handleSendMedia carries NO text, and that is structural rather than a
// choice: slidetypes.SendMediaRequest has no text field of any kind, so a
// caption cannot be expressed on this wire at all.
//
// This INVERTS the WhatsApp adapter, which deliberately puts the reply text in
// `caption` on the first attachment and skips /send so the group does not hear
// the reply twice. Copying that shape here silently loses the text. On
// Instagram the caller sends /send first, then /send-media per attachment.
func (b *bridge) handleSendMedia(w http.ResponseWriter, r *http.Request) {
	var req sendMediaRequest
	if !readJSON(w, r, maxMediaBody, &req) {
		return
	}
	if req.DataBase64 == "" {
		badRequest(w, "data_base64 is required")
		return
	}
	data, err := decodeBase64(req.DataBase64)
	if err != nil {
		badRequest(w, "data_base64 is not valid base64")
		return
	}
	if len(data) == 0 {
		badRequest(w, "data_base64 decoded to zero bytes")
		return
	}
	cli, ok := b.ready(w)
	if !ok {
		return
	}
	thread, ok := b.resolveRoom(w, r, req.RoomID)
	if !ok {
		return
	}
	// BOTH legs of /send-media take the short id, as one int64 so they cannot
	// disagree. resolveRoom has already proved it parses, so this cannot fail;
	// the error is still checked rather than dropped, because a silent 0 here
	// would upload against /direct/t/0/.
	shortID, err := strconv.ParseInt(thread.Short, 10, 64)
	if err != nil || shortID <= 0 {
		badRequest(w, "room_id must be a decimal thread_fbid")
		return
	}
	media := &httpclient.MercuryUploadMedia{
		Filename:    orDefault(req.Filename, "upload.bin"),
		MimeType:    orDefault(req.Mimetype, "application/octet-stream"),
		MediaData:   data,
		IsVoiceClip: req.VoiceNote,
	}
	up := b.uploadFn()
	if up == nil {
		unavailable(w, "session is being re-established")
		return
	}
	res, err := sendMedia(r.Context(), cli, up, shortID, media, req.ReplyTo)
	if err != nil {
		b.failSend(w, "send-media", req.RoomID, err)
		return
	}
	writeJSON(w, http.StatusOK, res)
}

type reactRequest struct {
	RoomID    string `json:"room_id"`
	MessageID string `json:"message_id"`
	Emoji     string `json:"emoji"`
	Remove    bool   `json:"remove"`
}

func (b *bridge) handleReact(w http.ResponseWriter, r *http.Request) {
	var req reactRequest
	if !readJSON(w, r, maxJSONBody, &req) {
		return
	}
	if req.MessageID == "" {
		badRequest(w, "message_id is required")
		return
	}
	// The emoji is required even for a removal: the mutation identifies WHICH
	// reaction is being retracted, not merely that one is.
	if req.Emoji == "" {
		badRequest(w, "emoji is required (also when remove is true)")
		return
	}
	cli, ok := b.ready(w)
	if !ok {
		return
	}
	thread, ok := b.resolveRoom(w, r, req.RoomID)
	if !ok {
		return
	}
	// SHORT — and this is the one verb where that is load-bearing rather than
	// merely matching upstream. A reaction addressed with the long thread_igid
	// is accepted by the API and is absent from the thread on read-back
	// (measured 2026-09-22), where a SEND was measured arriving either way.
	// The long id is still resolved first: that lookup is what validates the
	// room exists and what warms the reverse cache /typing reads.
	if err := sendReaction(r.Context(), cli, thread.Short,
		req.MessageID, req.Emoji, req.Remove, b.ownFBID()); err != nil {
		b.failSend(w, "react", req.RoomID, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true})
}

type typingRequest struct {
	RoomID string `json:"room_id"`
	Typing bool   `json:"typing"`
}

// handleTyping is the one route that genuinely needs the realtime socket.
//
// Everything else here is a GraphQL call over plain HTTPS and works while the
// socket reconnects; SetTyping publishes over the MQTT-bypass stream and needs
// a live connection context. So this is the only handler that answers 503 on
// `connected == false` — the others would answer 503 for a condition that does
// not actually stop them.
func (b *bridge) handleTyping(w http.ResponseWriter, r *http.Request) {
	var req typingRequest
	if !readJSON(w, r, maxJSONBody, &req) {
		return
	}
	cli, ok := b.ready(w)
	if !ok {
		return
	}
	if _, _, connected, _ := b.snapshot(); !connected {
		unavailable(w, "realtime socket is not connected")
		return
	}
	thread, ok := b.resolveRoom(w, r, req.RoomID)
	if !ok {
		return
	}
	// SHORT, following upstream (handlematrix.go:484). Typing arrival was
	// never measured in either id space — there is no read-back for typing —
	// so this is followed, not proven.
	if err := cli.SetTyping(r.Context(), thread.Short, req.Typing); err != nil {
		b.failSend(w, "typing", req.RoomID, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true})
}

type unsendRequest struct {
	RoomID    string `json:"room_id"`
	MessageID string `json:"message_id"`
}

func (b *bridge) handleUnsend(w http.ResponseWriter, r *http.Request) {
	var req unsendRequest
	if !readJSON(w, r, maxJSONBody, &req) {
		return
	}
	if req.MessageID == "" {
		badRequest(w, "message_id is required")
		return
	}
	cli, ok := b.ready(w)
	if !ok {
		return
	}
	thread, ok := b.resolveRoom(w, r, req.RoomID)
	if !ok {
		return
	}
	// LONG: the one verb that is genuinely long on both sides (upstream
	// handlematrix.go:292 passes `meta.IGThreadID`), and the one every probe
	// cleanup ran this way.
	confirmed, err := unsendMessage(r.Context(), cli, thread.Long, req.MessageID)
	if err != nil {
		b.failSend(w, "unsend", req.RoomID, err)
		return
	}
	if !confirmed {
		// REPORTED, NOT FAILED. `direct_unsend_message` has never been
		// observed against an unsend known to have failed, upstream discards
		// it, and a false 500 here strands probe messages in a real group —
		// the cleanup path fails and a human is told the message is still
		// there. So the observation is surfaced both ways and the call still
		// reads 200. See unsendMessage.
		logf("unsend (room=%s) returned direct_unsend_message=false; "+
			"the message may still be in the thread", req.RoomID)
		writeJSON(w, http.StatusOK, map[string]any{"ok": true, "confirmed": false})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true})
}

// --- shared plumbing ----------------------------------------------------------

// ready is the session gate every send route passes through first.
//
// It answers 409 for checkpointed AND logged_out: both mean "a human must act
// before this can succeed", which is precisely what 409 tells the adapter. It
// does NOT check the socket — see handleTyping for why that would be wrong for
// the other five routes.
func (b *bridge) ready(w http.ResponseWriter) (igClient, bool) {
	state, detail, _, _ := b.snapshot()
	if state != stateAuthenticated {
		writeJSON(w, http.StatusConflict, map[string]any{
			"ok": false, "error": string(state), "detail": detail,
		})
		return nil, false
	}
	cli := b.sendClient()
	if cli == nil {
		unavailable(w, "session is being re-established")
		return nil, false
	}
	return cli, true
}

// resolveRoom turns the contract's room key (the SHORT thread_fbid) into BOTH
// thread ids. Which one a given verb takes is a per-verb fact, not a rule —
// see the table at the top of outbound.go. The long id never crosses the HTTP
// boundary in either direction.
func (b *bridge) resolveRoom(w http.ResponseWriter, r *http.Request, roomID string) (threadRef, bool) {
	thread, err := b.threads.resolve(r.Context(), roomID)
	if err == nil {
		return thread, true
	}
	if errors.Is(err, errBadRoomID) {
		badRequest(w, err.Error())
		return threadRef{}, false
	}
	if errors.Is(err, errNotConnected) {
		unavailable(w, err.Error())
		return threadRef{}, false
	}
	// A lookup that failed against Instagram is mapped through the same
	// classifier as a send, so a checkpoint discovered here reads 409 and not
	// a generic 500 the adapter would retry.
	writeJSON(w, statusForError(err), map[string]any{
		"ok": false, "error": "thread_lookup_failed", "detail": err.Error(),
	})
	return threadRef{}, false
}

func (b *bridge) failSend(w http.ResponseWriter, route, roomID string, err error) {
	status := statusForError(err)
	// room_id is a group thread id, not a person. It is safe to log and is the
	// only handle an operator has for correlating a failure with a room.
	logf("%s failed (room=%s status=%d): %v", route, roomID, status, err)
	writeJSON(w, status, map[string]any{"ok": false, "error": err.Error()})
}

// statusForError is the fail-closed half of the status mapping: an error the
// bridge cannot classify becomes 500, never 200 and never 503. 503 is a claim
// that retrying will work, and making that claim on an unknown failure is how
// a permanent error turns into an infinite retry loop.
func statusForError(err error) int {
	switch {
	case err == nil:
		return http.StatusOK
	case errors.Is(err, errBadRoomID):
		return http.StatusBadRequest
	case errors.Is(err, errNotConnected):
		return http.StatusServiceUnavailable
	case errors.Is(err, httpclient.ErrChallengeRequired),
		errors.Is(err, httpclient.ErrCheckpointRequired),
		errors.Is(err, httpclient.ErrConsentRequired),
		errors.Is(err, httpclient.ErrTokenInvalidated):
		return http.StatusConflict
	case isTooLarge(err):
		return http.StatusRequestEntityTooLarge
	default:
		return http.StatusInternalServerError
	}
}

// isTooLarge matches Instagram's own refusal text, because the library surfaces
// it as an opaque error rather than a typed one. Matching on a string is
// unpleasant and is confined to exactly this: the alternative is reporting a
// 20,001-character message as a server fault the adapter would retry forever.
func isTooLarge(err error) bool {
	msg := strings.ToLower(err.Error())
	return strings.Contains(msg, "message too long") ||
		strings.Contains(msg, "too large") ||
		strings.Contains(msg, "entity too large")
}

func readJSON(w http.ResponseWriter, r *http.Request, limit int64, into any) bool {
	r.Body = http.MaxBytesReader(w, r.Body, limit)
	dec := json.NewDecoder(r.Body)
	if err := dec.Decode(into); err != nil {
		var tooBig *http.MaxBytesError
		if errors.As(err, &tooBig) {
			writeJSON(w, http.StatusRequestEntityTooLarge,
				map[string]any{"ok": false, "error": "payload too large"})
			return false
		}
		badRequest(w, "invalid json body")
		return false
	}
	return true
}

func writeJSON(w http.ResponseWriter, status int, body any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(body)
}

func badRequest(w http.ResponseWriter, detail string) {
	writeJSON(w, http.StatusBadRequest, map[string]any{"ok": false, "error": detail})
}

func unavailable(w http.ResponseWriter, detail string) {
	writeJSON(w, http.StatusServiceUnavailable, map[string]any{"ok": false, "error": detail})
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

func logf(format string, args ...any) { log.Printf(format, args...) }

func orDefault(v, fallback string) string {
	if v == "" {
		return fallback
	}
	return v
}
