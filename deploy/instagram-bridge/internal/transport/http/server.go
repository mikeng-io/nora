// Package httptransport is the bridge's HTTP surface: routes, request
// parsing, response DTOs and the status mapping
// (design/instagram-adoption/16 §Layers, 17 P8). Handlers are thin: they read
// the request, call ONE use case, and write what it returned. They import the
// use cases and the domain, never instameow and never a wire struct.
//
// The routes are the table in design/instagram-adoption/03-transport-contract.md,
// plus the thread and account reads, which the contract document does not
// carry yet.
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
//	404  the id names nothing
//	500  Instagram refused for a reason the bridge has no name for
//
// Collapsing 409 into 503 would make the adapter hammer a challenged account
// forever; collapsing 503 into 500 would make it give up on a socket that
// reconnects in seconds. They are distinct because the consequences are.
package httptransport

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"time"

	"instagram-bridge/internal/app"
	"instagram-bridge/internal/domain"
	"instagram-bridge/internal/observability"
)

const (
	// maxJSONBody bounds the control routes. Generous relative to a message
	// (20,000 characters were measured as accepted on the wire) and small
	// enough that a runaway caller cannot exhaust the container.
	maxJSONBody = 1 << 20 // 1 MiB
	// maxMediaBody bounds /send-media. base64 inflates by 4/3, so this admits
	// roughly a 24 MB file.
	maxMediaBody = 32 << 20 // 32 MiB
)

// Events is the SSE hub as /events and /health read it.
type Events interface {
	Subscribe() (cortez []byte, [][]byte, int)
	Unsubscribe(cortez []byte)
	Stats() (clients, buffered, dropped int)
}

// Server is the HTTP surface over the use cases.
type Server struct {
	app       *app.Service
	events    Events
	startedAt time.Time
}

// New builds the surface. startedAt is the process start, for /health's
// uptime.
func New(svc *app.Service, events Events, startedAt time.Time) *Server {
	return &Server{app: svc, events: events, startedAt: startedAt}
}

// Routes is the whole handler, traced.
func (s *Server) Routes() http.Handler {
	mux := http.NewServeMux()
	// The bridge talking about ITSELF: its health, its event stream.
	mux.HandleFunc("GET /health", s.handleHealth)
	mux.HandleFunc("GET /events", s.handleEvents)
	// --- RESOURCES ---------------------------------------------------------
	//
	// Two nouns — a THREAD and an ACCOUNT — and the reads hang off them. The
	// mutations below are still verbs on a thread; they move onto resource
	// paths in P14.
	//
	// They were `GET /roster`, `GET /profile` and `GET /story`, plus a
	// proposed `GET /profile-social` — a path named after which BACKEND a
	// field came from, which is not a resource and would have been the fourth
	// spelling of one noun. Renamed 2026-09-22 while it was still free.
	mux.HandleFunc("GET /threads/{thread_fbid}/members", observability.Route("GET /threads/{thread_fbid}/members", s.handleRoster))
	// ONE account resource. The social fields are not a second noun — they
	// are more of this one — so they are merged into this read rather than
	// given a path of their own. The response says which upstream halves
	// answered.
	mux.HandleFunc("GET /accounts/{igid}", observability.Route("GET /accounts/{igid}", s.handleProfile))
	// The only path to a story anywhere, and the only read VISIBLE to the
	// person being read. A sub-resource of the account, because that is what
	// it is.
	mux.HandleFunc("GET /accounts/{igid}/story", observability.Route("GET /accounts/{igid}/story", s.handleStory))
	mux.HandleFunc("POST /send", observability.Route("POST /send", s.handleSend))
	mux.HandleFunc("POST /send-media", observability.Route("POST /send-media", s.handleSendMedia))
	mux.HandleFunc("POST /react", observability.Route("POST /react", s.handleReact))
	mux.HandleFunc("POST /typing", observability.Route("POST /typing", s.handleTyping))
	mux.HandleFunc("POST /unsend", observability.Route("POST /unsend", s.handleUnsend))
	return observability.TraceHTTP(mux)
}

// --- /health ------------------------------------------------------------------

type healthBody struct {
	OK        bool                `json:"ok"`
	Session   domain.SessionState `json:"session"`
	Connected bool                `json:"connected"`
	Seq       int64               `json:"seq"`
	Detail    string              `json:"detail,omitempty"`
	// Beyond the contract's four fields. `self` is here because the adapter's
	// "was I tagged?" test binds on an fbid it would otherwise have to carry
	// in config, and a config value that silently disagrees with the live
	// session is a failure with no symptom. The event counters are the
	// drop-and-count record: the number survives here permanently so a loss is
	// readable long after the SSE notice that announced it.
	Self           selfBody `json:"self"`
	DroppedEvents  int      `json:"dropped_events"`
	BufferedEvents int      `json:"buffered_events"`
	Clients        int      `json:"clients"`
	UptimeSeconds  int      `json:"uptime_seconds"`
}

// handleHealth ALWAYS answers 200 while the HTTP server serves.
//
// Instagram challenges are routine, so a checkpointed session is a normal
// operating state the bridge must DESCRIBE, not a condition to fail on. A
// non-200 would make the container healthcheck flap on an ordinary captcha
// and restart a process whose restart fixes nothing.
func (s *Server) handleHealth(w http.ResponseWriter, r *http.Request) {
	snap := s.app.Health()
	clients, buffered, dropped := s.events.Stats()
	writeJSON(w, http.StatusOK, healthBody{
		OK:             true,
		Session:        snap.State,
		Connected:      snap.Connected,
		Seq:            snap.Seq,
		Detail:         snap.Detail,
		Self:           selfBody(snap.Self),
		DroppedEvents:  dropped,
		BufferedEvents: buffered,
		Clients:        clients,
		UptimeSeconds:  int(time.Since(s.startedAt).Seconds()),
	})
}

// --- /events ------------------------------------------------------------------

func (s *Server) handleEvents(w http.ResponseWriter, r *http.Request) {
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

	ch, replay, droppedTotal := s.events.Subscribe()
	defer s.events.Unsubscribe(ch)

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
	RoomID   string        `json:"room_id"`
	Text     string        `json:"text"`
	Mentions []mentionBody `json:"mentions"`
	ReplyTo  string        `json:"reply_to"`
}

func (s *Server) handleSend(w http.ResponseWriter, r *http.Request) {
	var req sendRequest
	if !readJSON(w, r, maxJSONBody, &req) {
		return
	}
	if req.Text == "" {
		badRequest(w, "text is required")
		return
	}
	res, err := s.app.SendText(r.Context(), req.RoomID, req.Text, mentionsOf(req.Mentions), req.ReplyTo)
	if err != nil {
		fail(r.Context(), w, "send", req.RoomID, err)
		return
	}
	writeJSON(w, http.StatusOK, sentBody{OK: true, ID: res.ID, TimestampMS: res.TimestampMS})
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
// choice: the wire's media request has no text field of any kind, so a
// caption cannot be expressed at all.
//
// This INVERTS the WhatsApp adapter, which deliberately puts the reply text in
// `caption` on the first attachment and skips /send so the group does not hear
// the reply twice. Copying that shape here silently loses the text. On
// Instagram the caller sends /send first, then /send-media per attachment.
func (s *Server) handleSendMedia(w http.ResponseWriter, r *http.Request) {
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
	res, err := s.app.SendMedia(r.Context(), req.RoomID, domain.Upload{
		Filename:  orDefault(req.Filename, "upload.bin"),
		MimeType:  orDefault(req.Mimetype, "application/octet-stream"),
		Data:      data,
		VoiceNote: req.VoiceNote,
	}, req.ReplyTo)
	if err != nil {
		fail(r.Context(), w, "send-media", req.RoomID, err)
		return
	}
	writeJSON(w, http.StatusOK, sentBody{OK: true, ID: res.ID, TimestampMS: res.TimestampMS})
}

type reactRequest struct {
	RoomID    string `json:"room_id"`
	MessageID string `json:"message_id"`
	Emoji     string `json:"emoji"`
	Remove    bool   `json:"remove"`
}

func (s *Server) handleReact(w http.ResponseWriter, r *http.Request) {
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
	confirmed, err := s.app.React(r.Context(), req.RoomID, req.MessageID, req.Emoji, req.Remove)
	if err != nil {
		fail(r.Context(), w, "react", req.RoomID, err)
		return
	}
	if !confirmed {
		// 200 with the confirmation withheld, not a failure. The mutation was
		// accepted; only this response's post-state stayed silent about it,
		// which production showed happening on every reaction while the
		// reaction itself landed. The field's PRESENCE is the signal — a
		// confirmed reaction omits it, so `confirmed: false` means something
		// rather than being a default nobody set.
		writeJSON(w, http.StatusOK, map[string]any{"ok": true, "confirmed": false})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true})
}

type typingRequest struct {
	RoomID string `json:"room_id"`
	Typing bool   `json:"typing"`
}

// handleTyping is the one route that genuinely needs the realtime socket, so
// the only one that answers 503 on `connected == false`.
func (s *Server) handleTyping(w http.ResponseWriter, r *http.Request) {
	var req typingRequest
	if !readJSON(w, r, maxJSONBody, &req) {
		return
	}
	if err := s.app.Typing(r.Context(), req.RoomID, req.Typing); err != nil {
		fail(r.Context(), w, "typing", req.RoomID, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true})
}

type unsendRequest struct {
	RoomID    string `json:"room_id"`
	MessageID string `json:"message_id"`
}

func (s *Server) handleUnsend(w http.ResponseWriter, r *http.Request) {
	var req unsendRequest
	if !readJSON(w, r, maxJSONBody, &req) {
		return
	}
	if req.MessageID == "" {
		badRequest(w, "message_id is required")
		return
	}
	confirmed, err := s.app.Unsend(r.Context(), req.RoomID, req.MessageID)
	if err != nil {
		fail(r.Context(), w, "unsend", req.RoomID, err)
		return
	}
	if !confirmed {
		// REPORTED, NOT FAILED: the observation is surfaced in the body and
		// the call still reads 200.
		writeJSON(w, http.StatusOK, map[string]any{"ok": true, "confirmed": false})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true})
}

// --- failures -----------------------------------------------------------------

// fail writes a use case's error. Two stages answer with their own bodies,
// as they always have:
//
//   - the session gate (409): the session STATE and its detail, because what
//     the caller needs is which human action is due, not an error string;
//   - a room's route lookup failing against Instagram: `thread_lookup_failed`
//     with the detail, mapped through the same status classifier, so a
//     checkpoint discovered there reads 409 and not a 500 the adapter would
//     retry.
//
// Everything else is `{"ok": false, "error": ...}` at statusForError's code.
func fail(ctx context.Context, w http.ResponseWriter, route, key string, err error) {
	var notReady *app.NotReadyError
	if errors.As(err, &notReady) {
		writeJSON(w, http.StatusConflict, map[string]any{
			"ok": false, "error": string(notReady.State), "detail": notReady.Detail,
		})
		return
	}
	status := statusForError(err)
	var lookup *app.ThreadLookupError
	if errors.As(err, &lookup) && !errors.Is(err, domain.ErrUnavailable) {
		writeJSON(w, status, map[string]any{
			"ok": false, "error": "thread_lookup_failed", "detail": err.Error(),
		})
		return
	}
	// The key is a room (a group thread id, not a person) or an igid. Both
	// are safe to log and are the only handle an operator has for
	// correlating a failure; the log stays on the host.
	slog.WarnContext(ctx, route+" failed", "key", key, "status", status, "error", err)
	writeJSON(w, status, map[string]any{"ok": false, "error": err.Error()})
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
