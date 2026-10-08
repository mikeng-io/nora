package main

// The status mapping is what the adapter's retry decision reads, so it is
// tested as behaviour rather than trusted as intent. Each case below names the
// consequence of getting it wrong, because "409 vs 503" is otherwise two
// numbers that look interchangeable.

import (
	"bufio"
	"encoding/base64"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"instagram-bridge/internal/domain"

	"go.mau.fi/mautrix-meta/pkg/messagix/httpclient"
)

// --- /health ------------------------------------------------------------------

// The single most important assertion about /health: it is 200 even when the
// account is checkpointed. A non-200 would make the container healthcheck flap
// on an ordinary Instagram captcha and restart a process whose restart fixes
// nothing — an ERROR reading as a dead bridge.
func TestHealthIs200InEverySessionState(t *testing.T) {
	for _, state := range []domain.SessionState{domain.StateAuthenticated, domain.StateCheckpointed, domain.StateLoggedOut} {
		b := newTestBridge(t, &fakeIG{}, withState(state, "because"))
		req := httptest.NewRequest(http.MethodGet, "/health", nil)
		rec := httptest.NewRecorder()
		b.routes().ServeHTTP(rec, req)
		if rec.Code != http.StatusOK {
			t.Fatalf("session %q: /health answered %d, want 200", state, rec.Code)
		}
		var body map[string]any
		if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
			t.Fatal(err)
		}
		if body["session"] != string(state) {
			t.Errorf("session reported as %v, want %q", body["session"], state)
		}
		// The contract's four fields, by name.
		for _, key := range []string{"ok", "session", "connected", "seq"} {
			if _, ok := body[key]; !ok {
				t.Errorf("session %q: /health is missing %q", state, key)
			}
		}
	}
}

func TestHealthCarriesOwnIdentity(t *testing.T) {
	b := newTestBridge(t, &fakeIG{})
	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	rec := httptest.NewRecorder()
	b.routes().ServeHTTP(rec, req)
	var body healthBody
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	// This is the number "was I tagged?" compares against. Publishing it means
	// the adapter can check its config against the live session instead of
	// trusting a value that may silently have drifted.
	if body.Self.FBID != "18247361241432477" {
		t.Errorf("self.fbid = %q", body.Self.FBID)
	}
	if body.Self.IGID != "79597894900" {
		t.Errorf("self.igid = %q", body.Self.IGID)
	}
}

// --- the status mapping -------------------------------------------------------

// writeRoutes is each write route with a well-formed request for the measured
// room, so the only thing that can refuse it is what the test arranges.
func writeRoutes(t *testing.T, b *testBridge) map[string]func() *httptest.ResponseRecorder {
	return map[string]func() *httptest.ResponseRecorder{
		"send text":  func() *httptest.ResponseRecorder { return sendMessage(t, b, threadShort, map[string]any{"text": "hi"}) },
		"send media": func() *httptest.ResponseRecorder { return sendMessage(t, b, threadShort, mediaOf([]byte("x"), "", "")) },
		"reaction": func() *httptest.ResponseRecorder {
			return putReaction(t, b, threadShort, "mid.$x", map[string]any{"emoji": "👍"})
		},
		"typing": func() *httptest.ResponseRecorder { return putTyping(t, b, threadShort, map[string]any{"typing": true}) },
		"unsend": func() *httptest.ResponseRecorder { return unsendMessage(t, b, threadShort, "mid.$x") },
	}
}

// 409 means "a human must act". Mapping this to 503 would make the adapter
// retry a challenged account forever, which is the documented way to turn a
// checkpoint into a ban.
func TestCheckpointedSessionRefusesWritesWith409(t *testing.T) {
	for _, state := range []domain.SessionState{domain.StateCheckpointed, domain.StateLoggedOut} {
		f := &fakeIG{}
		b := newTestBridge(t, f, withState(state, "challenge required"))
		for name, call := range writeRoutes(t, b) {
			rec := call()
			if rec.Code != http.StatusConflict {
				t.Errorf("session %q: %s answered %d, want 409", state, name, rec.Code)
				continue
			}
			if env := decodeEnvelope(t, rec); env.Error.Code != "needs_human" ||
				env.Error.Message != string(state)+": challenge required" {
				t.Errorf("session %q: %s envelope = %+v", state, name, env)
			}
		}
		if f.TextReq != nil || f.MediaReq != nil || f.ReactReq != nil || f.Unsend != nil || len(f.Typing) > 0 || f.Fetches > 0 {
			t.Errorf("session %q: a request reached Instagram while checkpointed", state)
		}
	}
}

// Typing is the only route that genuinely needs the realtime socket:
// SetTyping publishes over the MQTT-bypass stream, while everything else is a
// GraphQL call over plain HTTPS that works while the socket reconnects.
// Answering 503 on the others would report a condition that does not
// actually stop them.
func TestOnlyTypingRequiresTheRealtimeSocket(t *testing.T) {
	f := &fakeIG{}
	b := newTestBridge(t, f, withConnected(false))

	if rec := putTyping(t, b, threadShort, map[string]any{"typing": true}); rec.Code != http.StatusServiceUnavailable {
		t.Errorf("typing answered %d with the socket down, want 503", rec.Code)
	} else if env := decodeEnvelope(t, rec); env.Error.Code != "unavailable" {
		t.Errorf("typing envelope = %+v", env)
	}
	if rec := sendMessage(t, b, threadShort, map[string]any{"text": "hi"}); rec.Code != http.StatusOK {
		t.Errorf("a send answered %d with the socket down, want 200: a GraphQL send does "+
			"not need the realtime stream", rec.Code)
	}
}

func TestMalformedWritesAre400(t *testing.T) {
	f := &fakeIG{}
	b := newTestBridge(t, f)
	cases := []struct {
		why  string
		call func() *httptest.ResponseRecorder
	}{
		{"no text", func() *httptest.ResponseRecorder { return sendMessage(t, b, threadShort, map[string]any{}) }},
		{"room is not a thread_fbid", func() *httptest.ResponseRecorder {
			return sendMessage(t, b, "not-a-thread", map[string]any{"text": "hi"})
		}},
		{"room is the LONG id", func() *httptest.ResponseRecorder {
			return sendMessage(t, b, threadLong, map[string]any{"text": "hi"})
		}},
		{"media with no bytes", func() *httptest.ResponseRecorder {
			return sendMessage(t, b, threadShort, map[string]any{"media": map[string]any{}})
		}},
		{"media not base64", func() *httptest.ResponseRecorder {
			return sendMessage(t, b, threadShort, map[string]any{"media": map[string]any{"data_base64": "!!!!"}})
		}},
		{"text and media at once", func() *httptest.ResponseRecorder {
			body := mediaOf([]byte("x"), "", "")
			body["text"] = "hi"
			return sendMessage(t, b, threadShort, body)
		}},
		{"reaction with no emoji", func() *httptest.ResponseRecorder {
			return putReaction(t, b, threadShort, "mid.$x", map[string]any{})
		}},
		{"reaction removal with no emoji", func() *httptest.ResponseRecorder {
			return putReaction(t, b, threadShort, "mid.$x", map[string]any{"remove": true})
		}},
		{"typing with no field", func() *httptest.ResponseRecorder {
			return putTyping(t, b, threadShort, map[string]any{})
		}},
		{"a message id with a space", func() *httptest.ResponseRecorder {
			return unsendMessage(t, b, threadShort, "mid.$a b")
		}},
		{"invalid json", func() *httptest.ResponseRecorder {
			return sendMessage(t, b, threadShort, "{not json")
		}},
	}
	for _, c := range cases {
		rec := c.call()
		if rec.Code != http.StatusBadRequest {
			t.Errorf("%s answered %d, want 400: %s", c.why, rec.Code, rec.Body.String())
			continue
		}
		if env := decodeEnvelope(t, rec); env.Error.Code != "invalid_id" {
			t.Errorf("%s: envelope = %+v", c.why, env)
		}
	}
	if f.TextReq != nil || f.MediaReq != nil || f.ReactReq != nil || f.Unsend != nil || len(f.Typing) > 0 || f.Fetches > 0 {
		t.Error("a malformed request reached Instagram")
	}
}

func TestOversizeBodyIs413(t *testing.T) {
	b := newTestBridge(t, &fakeIG{})
	huge := strings.Repeat("x", maxJSONBody+1024)
	rec := sendMessage(t, b, threadShort, `{"text":"`+huge+`"}`)
	if rec.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("answered %d, want 413", rec.Code)
	}
	if env := decodeEnvelope(t, rec); env.Error.Code != "too_large" {
		t.Errorf("envelope = %+v", env)
	}
}

// Instagram's own refusal text is the only signal available for this: the
// library surfaces it as an opaque error. Reporting a 50,000-character message
// as a 500 would have the adapter retry a message that can never be accepted.
func TestMessageTooLongMapsTo413(t *testing.T) {
	f := &fakeIG{SendErr: errorString("Message Too Long")}
	b := newTestBridge(t, f)
	rec := sendMessage(t, b, threadShort, map[string]any{"text": "x"})
	if rec.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("answered %d, want 413", rec.Code)
	}
	if env := decodeEnvelope(t, rec); env.Error.Code != "too_large" {
		t.Errorf("envelope = %+v", env)
	}
}

// Fail closed. An unclassified failure becomes 500, never 503: 503 is a claim
// that retrying will work, and making that claim on an unknown error turns a
// permanent failure into an infinite retry loop.
func TestUnknownSendFailureIs500NotRetryable(t *testing.T) {
	f := &fakeIG{SendErr: errorString("some new graphql error nobody has seen")}
	b := newTestBridge(t, f)
	rec := sendMessage(t, b, threadShort, map[string]any{"text": "x"})
	if rec.Code != http.StatusInternalServerError {
		t.Fatalf("answered %d, want 500", rec.Code)
	}
	if env := decodeEnvelope(t, rec); env.Error.Code != "internal" {
		t.Errorf("envelope = %+v", env)
	}
}

// A checkpoint discovered DURING a send must read the same as one discovered
// before it; otherwise the adapter's behaviour depends on when Instagram
// happened to notice.
func TestCheckpointDuringASendStillMapsTo409(t *testing.T) {
	f := &fakeIG{SendErr: httpclient.ErrCheckpointRequired}
	b := newTestBridge(t, f)
	rec := sendMessage(t, b, threadShort, map[string]any{"text": "x"})
	if rec.Code != http.StatusConflict {
		t.Fatalf("answered %d, want 409", rec.Code)
	}
	if env := decodeEnvelope(t, rec); env.Error.Code != "needs_human" || env.Error.Stage != "" {
		t.Errorf("envelope = %+v, want needs_human with no lookup stage", env)
	}
}

// A checkpoint discovered in the room's route LOOKUP is still 409, and says
// that it was the lookup: the old body's `thread_lookup_failed`, kept as a
// stage beside the kind.
func TestACheckpointInTheRoomLookupIs409AtTheLookupStage(t *testing.T) {
	f := &fakeIG{FetchErr: httpclient.ErrCheckpointRequired}
	b := newTestBridge(t, f)
	rec := sendMessage(t, b, threadShort, map[string]any{"text": "x"})
	if rec.Code != http.StatusConflict {
		t.Fatalf("answered %d, want 409", rec.Code)
	}
	if env := decodeEnvelope(t, rec); env.Error.Code != "needs_human" || env.Error.Stage != "thread_lookup" {
		t.Errorf("envelope = %+v, want needs_human at stage thread_lookup", env)
	}
	if f.TextReq != nil {
		t.Error("the send went out after its room lookup failed")
	}
}

// --- the success bodies -------------------------------------------------------

func TestSendReturnsTheMessageItSent(t *testing.T) {
	f := &fakeIG{}
	b := newTestBridge(t, f)
	rec := sendMessage(t, b, threadShort, map[string]any{
		"text":     "hello",
		"mentions": []map[string]any{{"fbid": "186217604552756", "offset": 0, "length": 9}},
		"reply_to": "mid.$parent",
	})
	if rec.Code != http.StatusOK {
		t.Fatalf("answered %d: %s", rec.Code, rec.Body.String())
	}
	var raw map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &raw); err != nil {
		t.Fatal(err)
	}
	if _, has := raw["ok"]; has {
		t.Errorf("the send result still carries `ok`: %s", rec.Body.String())
	}
	var body sentBody
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	// The sent message's id: what a reaction, an unsend and a quote address.
	if body.ID != "mid.$sent" {
		t.Fatalf("id = %q, want the message Instagram named", body.ID)
	}
	// The SHORT id is what a send carries (measured arriving 2026-09-22, and
	// what upstream uses), and the long id never appears on the wire the
	// caller sees, in either direction.
	if f.TextReq.IGThreadIGID == nil || *f.TextReq.IGThreadIGID != threadShort {
		t.Errorf("send used %v, want the short id", f.TextReq.IGThreadIGID)
	}
	if strings.Contains(rec.Body.String(), threadLong) {
		t.Error("the long thread id leaked into the HTTP response")
	}
	// The mention and the reply rode through.
	if len(f.TextReq.Mentions) != 1 || f.TextReq.Mentions[0].FBID != 186217604552756 {
		t.Errorf("mentions = %+v", f.TextReq.Mentions)
	}
	if f.TextReq.ReplyToMessageID == nil || *f.TextReq.ReplyToMessageID != "mid.$parent" {
		t.Errorf("reply_to = %v", f.TextReq.ReplyToMessageID)
	}
}

// Text and media are two messages, never a caption: SendMediaRequest has no
// text field at all. This test asserts the route does not quietly accept one.
func TestSendMediaCarriesNoCaption(t *testing.T) {
	f := &fakeIG{}
	b := newTestBridge(t, f)
	body := mediaOf([]byte("\x89PNG\r\n"), "x.png", "image/png")
	// A caller that copied the WhatsApp shape would send this. It must be
	// ignored rather than silently turned into a second message or an error,
	// because the adapter's correct behaviour is to send the text first.
	body["caption"] = "this is not a field"
	rec := sendMessage(t, b, threadShort, body)
	if rec.Code != http.StatusOK {
		t.Fatalf("answered %d: %s", rec.Code, rec.Body.String())
	}
	if f.MediaReq.ThreadID != threadShort {
		t.Errorf("media thread = %q, want the short id", f.MediaReq.ThreadID)
	}
	if f.TextReq != nil {
		t.Error("a media message also sent a text message")
	}
	var sent sentBody
	if err := json.Unmarshal(rec.Body.Bytes(), &sent); err != nil || sent.ID != "mid.$media" {
		t.Errorf("media result = %s", rec.Body.String())
	}
}

// FOUR VERBS SHORT, ONE LONG, and the route table is where that stops being a
// comment and starts being behaviour. The reaction is the verb that
// DISCRIMINATES (the long id is accepted and the reaction never lands), so the
// fake refuses the other spelling the way the wire does, and a route that
// swaps them fails here rather than in a group chat. Unsend is the one that is
// genuinely long, on both sides.
func TestReactAndUnsendSucceedOnTheirOwnIDSpaces(t *testing.T) {
	f := &fakeIG{}
	b := newTestBridge(t, f)
	if rec := putReaction(t, b, threadShort, "mid.$x", map[string]any{"emoji": "👍"}); rec.Code != http.StatusOK {
		t.Fatalf("reaction answered %d: %s", rec.Code, rec.Body.String())
	}
	if rec := unsendMessage(t, b, threadShort, "mid.$x"); rec.Code != http.StatusOK {
		t.Fatalf("unsend answered %d: %s", rec.Code, rec.Body.String())
	}
	if f.ReactReq.Input.ThreadID != threadShort {
		t.Errorf("reaction addressed %q, want the SHORT id", f.ReactReq.Input.ThreadID)
	}
	if f.Unsend.SendData.ThreadID != threadLong {
		t.Errorf("unsend addressed %q, want the LONG id", f.Unsend.SendData.ThreadID)
	}
	// The reaction is on the message afterwards. This is the read-back, not
	// the request: it is what makes the assertion above fail rather than pass
	// silently if the id ever goes back to the long one.
	if got := f.Reactions["mid.$x"]; len(got) != 1 || got[0].SenderFBID != selfFBID {
		t.Fatalf("the reaction is not on the message: %+v", got)
	}
}

// A message id is a PATH SEGMENT now, and it carries `$` and may carry `/` or
// `+`. Escaped by the caller, it must reach Instagram byte for byte, and a
// `/` inside it must not be read as a deeper path.
func TestAMessageIDSurvivesThePath(t *testing.T) {
	const mid = "mid.$gA+b/c=d_e-f"
	f := &fakeIG{}
	b := newTestBridge(t, f)
	if rec := putReaction(t, b, threadShort, mid, map[string]any{"emoji": "👍"}); rec.Code != http.StatusOK {
		t.Fatalf("reaction answered %d: %s", rec.Code, rec.Body.String())
	}
	if rec := unsendMessage(t, b, threadShort, mid); rec.Code != http.StatusOK {
		t.Fatalf("unsend answered %d: %s", rec.Code, rec.Body.String())
	}
	if f.ReactReq.Input.MessageID != mid || f.Unsend.MessageID != mid {
		t.Errorf("Instagram was addressed with %q / %q, want %q", f.ReactReq.Input.MessageID, f.Unsend.MessageID, mid)
	}
}

// A reaction that Instagram accepts and does not apply must NOT answer 200.
// The bridge cannot tell the difference from the request, only from the
// post-state the mutation returns, so this drives the failure through the
// whole route.
func TestReactThatDoesNotLandIsNotA200(t *testing.T) {
	f := &fakeIG{}
	// An own fbid of 0 makes the read-back unverifiable, which is an ERROR.
	b := newTestBridge(t, f, withSelf(domain.Self{IGID: "79597894900", Username: "not_____nora"}))
	rec := putReaction(t, b, threadShort, "mid.$x", map[string]any{"emoji": "👍"})
	if rec.Code == http.StatusOK {
		t.Fatalf("an unverifiable reaction answered 200: %s", rec.Body.String())
	}
	decodeEnvelope(t, rec)
}

// An unconfirmed reaction is the production finding: accepted, landed, and
// this response's post-state silent about it. 200, with `confirmed: false`.
func TestAnUnconfirmedReactionAnswers200AndSaysSo(t *testing.T) {
	b := newTestBridge(t, &fakeIG{ReactPostStateSilent: true})
	rec := putReaction(t, b, threadShort, "mid.$x", map[string]any{"emoji": "👍"})
	var body confirmedBody
	if rec.Code != http.StatusOK || json.Unmarshal(rec.Body.Bytes(), &body) != nil ||
		body.Confirmed == nil || *body.Confirmed {
		t.Fatalf("answered %d %s, want 200 {confirmed:false}", rec.Code, rec.Body.String())
	}
}

// An unsend the mutation did not confirm still answers 200, and says so in the
// body rather than only in a log line nobody will read. A false 500 here
// strands probe messages in a real group; the field has never been observed
// against an unsend known to have failed, and upstream discards it.
func TestAnUnconfirmedUnsendStillAnswers200AndSaysSo(t *testing.T) {
	// The route resolves the room to the LONG id, which the fake confirms, so
	// the unconfirmed path can only be reached by making the MUTATION answer
	// false, which is the thing being tested.
	b := newTestBridge(t, &fakeIG{UnconfirmedUnsend: true})
	rec := unsendMessage(t, b, threadShort, "mid.$x")
	if rec.Code != http.StatusOK {
		t.Fatalf("unsend answered %d, want 200: %s", rec.Code, rec.Body.String())
	}
	var body confirmedBody
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil || body.Confirmed == nil || *body.Confirmed {
		t.Fatalf("body = %s, want an explicit confirmed:false", rec.Body.String())
	}
	if strings.Contains(rec.Body.String(), `"ok"`) {
		t.Errorf("the unsend result still carries `ok`: %s", rec.Body.String())
	}

	// And a CONFIRMED unsend says so explicitly: with no `ok` beside it, the
	// field is the whole answer.
	ok := unsendMessage(t, newTestBridge(t, &fakeIG{}), threadShort, "mid.$x")
	body = confirmedBody{}
	if err := json.Unmarshal(ok.Body.Bytes(), &body); err != nil || body.Confirmed == nil || !*body.Confirmed {
		t.Fatalf("a confirmed unsend answered %s, want confirmed:true", ok.Body.String())
	}
}

// PINNED, NOT GROUNDED, and labelled so on purpose. Typing arrival was never
// measured in either id space, so there is no read-back to check and no fake
// behaviour that could be honest about it. What this asserts is only that the
// bridge follows upstream (handlematrix.go:484, `meta.IGID` = the short id).
// If typing is ever measured, this test is the thing to replace, not to trust.
func TestTypingFollowsUpstreamAndUsesTheShortThreadID(t *testing.T) {
	f := &fakeIG{}
	b := newTestBridge(t, f)
	if rec := putTyping(t, b, threadShort, map[string]any{"typing": true}); rec.Code != http.StatusOK {
		t.Fatalf("typing answered %d", rec.Code)
	}
	if len(f.Typing) != 1 || f.Typing[0] != threadShort {
		t.Fatalf("typing addressed %v, want the short id", f.Typing)
	}
}

// A send Instagram accepted without naming the message must not reach the
// adapter as a 200: the body promises the sent message's id, and a message
// Nora cannot name she can neither react to, unsend, nor quote.
func TestSendWithoutAMessageIDDoesNotAnswer200(t *testing.T) {
	for name, body := range map[string]any{
		"text":  map[string]any{"text": "hi"},
		"media": mediaOf([]byte("x"), "", ""),
	} {
		b := newTestBridge(t, &fakeIG{EmptySend: true})
		rec := sendMessage(t, b, threadShort, body)
		if rec.Code != http.StatusBadGateway {
			t.Errorf("%s answered %d with no message id, want 502: %s", name, rec.Code, rec.Body.String())
			continue
		}
		if env := decodeEnvelope(t, rec); env.Error.Code != "upstream_rejected" {
			t.Errorf("%s: envelope = %+v", name, env)
		}
	}
}

// The verb paths are DELETED (17 §P14). Each answers the mux's own 404 and no
// request reaches Instagram through one.
func TestTheOldVerbPathsAnswer404(t *testing.T) {
	f := &fakeIG{}
	b := newTestBridge(t, f)
	for _, path := range []string{"/send", "/send-media", "/react", "/typing", "/unsend"} {
		rec := request(t, b, http.MethodPost, path, map[string]any{
			"room_id": threadShort, "text": "hi", "message_id": "mid.$x", "emoji": "👍", "typing": true,
			"data_base64": base64.StdEncoding.EncodeToString([]byte("x")),
		})
		if rec.Code != http.StatusNotFound {
			t.Errorf("POST %s answered %d, want 404", path, rec.Code)
		}
	}
	if f.TextReq != nil || f.MediaReq != nil || f.ReactReq != nil || f.Unsend != nil || len(f.Typing) > 0 || f.Fetches > 0 {
		t.Error("an old verb path reached Instagram")
	}
}

// --- /events ------------------------------------------------------------------

func TestEventsStreamsBufferedEventsThenLive(t *testing.T) {
	b := newTestBridge(t, &fakeIG{})
	b.hub.Emit(map[string]any{"kind": "message", "room_id": threadShort, "message_id": "mid.$buffered"})

	srv := httptest.NewServer(b.routes())
	defer srv.Close()

	resp, err := http.Get(srv.URL + "/events")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if got := resp.Header.Get("Content-Type"); got != "text/event-stream" {
		t.Fatalf("content-type = %q", got)
	}

	reader := bufio.NewReader(resp.Body)
	first := readSSEData(t, reader)
	if !strings.Contains(first, "mid.$buffered") {
		t.Fatalf("first event = %q, want the buffered one", first)
	}

	// Wait for the handler to have registered before emitting live, otherwise
	// the event races into the buffer and this test measures its own timing
	// rather than the hub's behaviour.
	deadline := time.Now().Add(2 * time.Second)
	for !b.hub.HasClients() && time.Now().Before(deadline) {
		time.Sleep(5 * time.Millisecond)
	}
	b.hub.Emit(map[string]any{"kind": "message", "room_id": threadShort, "message_id": "mid.$live"})
	if second := readSSEData(t, reader); !strings.Contains(second, "mid.$live") {
		t.Fatalf("second event = %q, want the live one", second)
	}
}

func readSSEData(t *testing.T, r *bufio.Reader) string {
	t.Helper()
	for {
		line, err := r.ReadString('\n')
		if err != nil {
			if err == io.EOF {
				t.Fatal("stream closed before an event arrived")
			}
			t.Fatal(err)
		}
		if strings.HasPrefix(line, "data: ") {
			return strings.TrimSpace(strings.TrimPrefix(line, "data: "))
		}
	}
}
