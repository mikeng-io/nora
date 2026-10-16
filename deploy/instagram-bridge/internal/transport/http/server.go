// Package httptransport is the bridge's HTTP surface: routes, request
// parsing, response DTOs and the status mapping
// (design/instagram-adoption/16 §Layers, 17 P8, P14). Handlers are thin: they
// parse the path ids and the body, call ONE use case, and write what it
// returned. They import the use cases and the domain, never instameow and
// never a wire struct.
//
// The routes are 16 §Resources' table: nouns (a thread, its messages, an
// account, a post), plus the two paths where the bridge talks about ITSELF
// (`/health`, `/events`), which are operational and not resources.
//
// Every route shares ONE status table and ONE failure envelope (errors.go).
// Success is the resource, with no `ok` field: the status carries success or
// failure.
package httptransport

import (
	"encoding/json"
	"fmt"
	"net/http"
	"time"

	"instagram-bridge/internal/app"
	"instagram-bridge/internal/domain"
	"instagram-bridge/internal/observability"
)

// Events is the SSE hub as /events and /health read it.
type Events interface {
	Subscribe() (chan []byte, [][]byte, int)
	Unsubscribe(chan []byte)
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
	// Nouns, and every one of them on the same conventions. A THREAD and what
	// hangs off it (its members, its messages, a message's reaction, the
	// typing indicator), an ACCOUNT and its story, and a POST.
	//
	// The reads were `GET /roster`, `GET /profile` and `GET /story`, plus a
	// proposed `GET /profile-social` (a path named after which BACKEND a field
	// came from, which is not a resource). Renamed 2026-09-22. The writes were
	// verbs, `POST /send`, `/send-media`, `/react`, `/typing` and `/unsend`;
	// P14 moved them onto the thread and DELETED the verbs (17 §P14), in the
	// same change that moved Nora's adapter. An old path is the mux's 404.
	//
	// Each route is traced by its PATTERN (the span name and `http.route`).
	// The thread routes use Route: their path carries a thread id and a
	// message id, both allowed in a span (16 §Tracing). A route whose path
	// segment names somebody's content uses PatternOnlyRoute instead.
	mux.HandleFunc("GET /threads/{thread_fbid}/members", observability.Route("GET /threads/{thread_fbid}/members", s.handleRoster))
	mux.HandleFunc("POST /threads/{thread_fbid}/messages", observability.Route("POST /threads/{thread_fbid}/messages", s.handleSendMessage))
	mux.HandleFunc("DELETE /threads/{thread_fbid}/messages/{message_id}", observability.Route("DELETE /threads/{thread_fbid}/messages/{message_id}", s.handleUnsend))
	mux.HandleFunc("PUT /threads/{thread_fbid}/messages/{message_id}/reaction", observability.Route("PUT /threads/{thread_fbid}/messages/{message_id}/reaction", s.handleReaction))
	mux.HandleFunc("PUT /threads/{thread_fbid}/typing", observability.Route("PUT /threads/{thread_fbid}/typing", s.handleTyping))
	// Account search and lookup by username (accounts_search.go).
	mux.HandleFunc("GET /accounts", observability.Route("GET /accounts", s.handleAccountsSearch))
	// ONE account resource. The social fields are not a second noun — they
	// are more of this one — so they are merged into this read rather than
	// given a path of their own. The response says which upstream halves
	// answered.
	mux.HandleFunc("GET /accounts/{igid}", observability.Route("GET /accounts/{igid}", s.handleProfile))
	// The only path to a story anywhere. The social stream's poller reads
	// members' stories (design/cognition-loop/22-social-stream.md §3), and
	// the read never marks one seen (§1). A sub-resource of the account,
	// because that is what it is.
	mux.HandleFunc("GET /accounts/{igid}/story", observability.Route("GET /accounts/{igid}/story", s.handleStory))
	// One page of the account's posts and reels, newest first, paged by
	// `?after=` (§1a, §7). PatternOnlyRoute: the page is somebody's content,
	// and the cursor is Instagram's opaque state, neither of which may become
	// a span attribute value.
	mux.HandleFunc("GET /accounts/{igid}/items", observability.PatternOnlyRoute("GET /accounts/{igid}/items", s.handleItems))
	// One post, every item in it (17 P12). Traced by PATTERN ONLY: the
	// shortcode names somebody's post and never becomes a span attribute
	// value.
	mux.HandleFunc("GET /posts/{shortcode}", observability.PatternOnlyRoute("GET /posts/{shortcode}", s.handlePost))
	// The comment-mentions probe (mentions.go). No path variable, but
	// PatternOnlyRoute anyway: a comment's text and a commenter's identity
	// must never enter a span, the same guarantee the other content-bearing
	// routes already get from it.
	mux.HandleFunc("GET /mentions", observability.PatternOnlyRoute("GET /mentions", s.handleMentions))
	// A post's comments, and the bridge's ONE public write: a reply under a
	// summons (comments.go; design 19). PatternOnlyRoute: the shortcode names
	// somebody's post, and the reply_to query and the body carry comment ids
	// and people's words, none of which may become a span attribute.
	mux.HandleFunc("GET /posts/{shortcode}/comments", observability.PatternOnlyRoute("GET /posts/{shortcode}/comments", s.handleComments))
	mux.HandleFunc("POST /posts/{shortcode}/comments", observability.PatternOnlyRoute("POST /posts/{shortcode}/comments", s.handlePostComment))
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
