package main

// The status mapping is what the adapter's retry decision reads, so it is
// tested as behaviour rather than trusted as intent. Each case below names the
// consequence of getting it wrong, because "409 vs 503" is otherwise two
// numbers that look interchangeable.

import (
	"bufio"
	"context"
	"encoding/base64"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"go.mau.fi/mautrix-meta/pkg/messagix/httpclient"
	"go.mau.fi/mautrix-meta/pkg/messagix/types"
)

// newTestBridge wires a bridge with a fake session. Nothing here touches the
// network: the live account is in use and cannot be probed, so the HTTP layer
// is verified against a recording fake and the parts that genuinely need
// Instagram are left honestly unverified (see the session tests).
func newTestBridge(t *testing.T, f *fakeIG) *bridge {
	t.Helper()
	b := &bridge{
		store:     store{dir: t.TempDir()},
		hub:       newHub(),
		startedAt: time.Now(),
		state:     stateAuthenticated,
		connected: true,
		seq:       22,
		self:      selfIdentity{FBID: "14963523149149302", IGID: "79597894900", Username: "not_____nora"},
	}
	b.threads = newThreadIndex(f.FetchThreadID)
	b.out = f
	b.up = func(context.Context, int64, *httpclient.MercuryUploadMedia) (*types.MercuryUploadResponse, error) {
		return &types.MercuryUploadResponse{
			Payload: types.MediaPayloads{RealMetadata: &types.FileMetadata{ImageID: 2280432885466163}},
		}, nil
	}
	return b
}

func post(t *testing.T, b *bridge, path string, body any) *httptest.ResponseRecorder {
	t.Helper()
	blob, err := json.Marshal(body)
	if err != nil {
		t.Fatal(err)
	}
	req := httptest.NewRequest(http.MethodPost, path, strings.NewReader(string(blob)))
	rec := httptest.NewRecorder()
	b.routes().ServeHTTP(rec, req)
	return rec
}

// --- /health ------------------------------------------------------------------

// The single most important assertion about /health: it is 200 even when the
// account is checkpointed. A non-200 would make the container healthcheck flap
// on an ordinary Instagram captcha and restart a process whose restart fixes
// nothing — an ERROR reading as a dead bridge.
func TestHealthIs200InEverySessionState(t *testing.T) {
	for _, state := range []sessionState{stateAuthenticated, stateCheckpointed, stateLoggedOut} {
		b := newTestBridge(t, &fakeIG{})
		b.setState(state, "because")
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
	if body.Self.FBID != "14963523149149302" {
		t.Errorf("self.fbid = %q", body.Self.FBID)
	}
	if body.Self.IGID != "79597894900" {
		t.Errorf("self.igid = %q", body.Self.IGID)
	}
}

// --- the status mapping -------------------------------------------------------

// 409 means "a human must act". Mapping this to 503 would make the adapter
// retry a challenged account forever, which is the documented way to turn a
// checkpoint into a ban.
func TestCheckpointedSessionRefusesSendsWith409(t *testing.T) {
	for _, state := range []sessionState{stateCheckpointed, stateLoggedOut} {
		f := &fakeIG{}
		b := newTestBridge(t, f)
		b.setState(state, "challenge required")
		for _, route := range []string{"/send", "/send-media", "/react", "/typing", "/unsend"} {
			rec := post(t, b, route, map[string]any{
				"room_id": threadShort, "text": "hi", "message_id": "mid.$x",
				"emoji": "👍", "data_base64": base64.StdEncoding.EncodeToString([]byte("x")),
			})
			if rec.Code != http.StatusConflict {
				t.Errorf("session %q: %s answered %d, want 409", state, route, rec.Code)
			}
		}
		if f.textReq != nil || f.mediaReq != nil || f.reactReq != nil || f.unsend != nil || len(f.typing) > 0 {
			t.Errorf("session %q: a request reached Instagram while checkpointed", state)
		}
	}
}

// /typing is the only route that genuinely needs the realtime socket:
// SetTyping publishes over the MQTT-bypass stream, while everything else is a
// GraphQL call over plain HTTPS that works while the socket reconnects.
// Answering 503 on the other five would report a condition that does not
// actually stop them.
func TestOnlyTypingRequiresTheRealtimeSocket(t *testing.T) {
	f := &fakeIG{}
	b := newTestBridge(t, f)
	b.setConnected(false)

	if rec := post(t, b, "/typing", map[string]any{"room_id": threadShort, "typing": true}); rec.Code != http.StatusServiceUnavailable {
		t.Errorf("/typing answered %d with the socket down, want 503", rec.Code)
	}
	if rec := post(t, b, "/send", map[string]any{"room_id": threadShort, "text": "hi"}); rec.Code != http.StatusOK {
		t.Errorf("/send answered %d with the socket down, want 200 — a GraphQL send does "+
			"not need the realtime stream", rec.Code)
	}
}

func TestMalformedRequestsAre400(t *testing.T) {
	b := newTestBridge(t, &fakeIG{})
	cases := []struct {
		route string
		body  any
		why   string
	}{
		{"/send", map[string]any{"room_id": threadShort}, "no text"},
		{"/send", map[string]any{"room_id": "not-a-thread", "text": "hi"}, "room_id is not a thread_fbid"},
		{"/send", map[string]any{"room_id": threadLong, "text": "hi"}, "room_id is the LONG id"},
		{"/send-media", map[string]any{"room_id": threadShort}, "no bytes"},
		{"/send-media", map[string]any{"room_id": threadShort, "data_base64": "!!!!"}, "not base64"},
		{"/send-media", map[string]any{"room_id": threadShort, "data_base64": ""}, "empty bytes"},
		{"/react", map[string]any{"room_id": threadShort, "emoji": "👍"}, "no message_id"},
		{"/react", map[string]any{"room_id": threadShort, "message_id": "mid.$x"}, "no emoji"},
		{"/unsend", map[string]any{"room_id": threadShort}, "no message_id"},
	}
	for _, c := range cases {
		if rec := post(t, b, c.route, c.body); rec.Code != http.StatusBadRequest {
			t.Errorf("%s (%s) answered %d, want 400", c.route, c.why, rec.Code)
		}
	}
}

func TestInvalidJSONIs400NotAPanic(t *testing.T) {
	b := newTestBridge(t, &fakeIG{})
	req := httptest.NewRequest(http.MethodPost, "/send", strings.NewReader("{not json"))
	rec := httptest.NewRecorder()
	b.routes().ServeHTTP(rec, req)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("answered %d, want 400", rec.Code)
	}
}

func TestOversizeBodyIs413(t *testing.T) {
	b := newTestBridge(t, &fakeIG{})
	huge := strings.Repeat("x", maxJSONBody+1024)
	req := httptest.NewRequest(http.MethodPost, "/send",
		strings.NewReader(`{"room_id":"`+threadShort+`","text":"`+huge+`"}`))
	rec := httptest.NewRecorder()
	b.routes().ServeHTTP(rec, req)
	if rec.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("answered %d, want 413", rec.Code)
	}
}

// Instagram's own refusal text is the only signal available for this: the
// library surfaces it as an opaque error. Reporting a 50,000-character message
// as a 500 would have the adapter retry a message that can never be accepted.
func TestMessageTooLongMapsTo413(t *testing.T) {
	f := &fakeIG{sendErr: errorString("Message Too Long")}
	b := newTestBridge(t, f)
	rec := post(t, b, "/send", map[string]any{"room_id": threadShort, "text": "x"})
	if rec.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("answered %d, want 413", rec.Code)
	}
}

// Fail closed. An unclassified failure becomes 500, never 503: 503 is a claim
// that retrying will work, and making that claim on an unknown error turns a
// permanent failure into an infinite retry loop.
func TestUnknownSendFailureIs500NotRetryable(t *testing.T) {
	f := &fakeIG{sendErr: errorString("some new graphql error nobody has seen")}
	b := newTestBridge(t, f)
	rec := post(t, b, "/send", map[string]any{"room_id": threadShort, "text": "x"})
	if rec.Code != http.StatusInternalServerError {
		t.Fatalf("answered %d, want 500", rec.Code)
	}
}

// A checkpoint discovered DURING a send must read the same as one discovered
// before it; otherwise the adapter's behaviour depends on when Instagram
// happened to notice.
func TestCheckpointDuringASendStillMapsTo409(t *testing.T) {
	f := &fakeIG{sendErr: httpclient.ErrCheckpointRequired}
	b := newTestBridge(t, f)
	rec := post(t, b, "/send", map[string]any{"room_id": threadShort, "text": "x"})
	if rec.Code != http.StatusConflict {
		t.Fatalf("answered %d, want 409", rec.Code)
	}
}

// --- the success bodies -------------------------------------------------------

func TestSendReturnsTheContractResultShape(t *testing.T) {
	f := &fakeIG{}
	b := newTestBridge(t, f)
	rec := post(t, b, "/send", map[string]any{
		"room_id": threadShort, "text": "hello",
		"mentions": []map[string]any{{"fbid": "110966695387753", "offset": 0, "length": 9}},
		"reply_to": "mid.$parent",
	})
	if rec.Code != http.StatusOK {
		t.Fatalf("answered %d: %s", rec.Code, rec.Body.String())
	}
	var body map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	if body["ok"] != true || body["id"] != "mid.$sent" {
		t.Fatalf("result = %v", body)
	}
	// The long id was used, and it never appeared on the wire the caller sees.
	if f.textReq.IGThreadIGID == nil || *f.textReq.IGThreadIGID != threadLong {
		t.Errorf("send used %v", f.textReq.IGThreadIGID)
	}
	if strings.Contains(rec.Body.String(), threadLong) {
		t.Error("the long thread id leaked into the HTTP response")
	}
}

// Text and media are two messages, never a caption: SendMediaRequest has no
// text field at all. This test asserts the route does not quietly accept one.
func TestSendMediaCarriesNoCaption(t *testing.T) {
	f := &fakeIG{}
	b := newTestBridge(t, f)
	rec := post(t, b, "/send-media", map[string]any{
		"room_id":     threadShort,
		"data_base64": base64.StdEncoding.EncodeToString([]byte("\x89PNG\r\n")),
		"filename":    "x.png", "mimetype": "image/png",
		// A caller that copied the WhatsApp shape would send this. It must be
		// ignored rather than silently turned into a second message or an
		// error, because the adapter's correct behaviour is to call /send first.
		"caption": "this is not a field",
	})
	if rec.Code != http.StatusOK {
		t.Fatalf("answered %d: %s", rec.Code, rec.Body.String())
	}
	if f.mediaReq.ThreadID != threadLong {
		t.Errorf("media thread = %q", f.mediaReq.ThreadID)
	}
	if f.textReq != nil {
		t.Error("/send-media also sent a text message")
	}
}

// THE ID SPACE IS MIXED PER VERB, and the route table is where that stops
// being a comment and starts being behaviour. /react takes the SHORT id and
// /unsend the LONG one; the fake refuses the other spelling the way the wire
// does, so a route that swaps them fails here rather than in a group chat.
func TestReactAndUnsendSucceedOnTheirOwnIDSpaces(t *testing.T) {
	f := &fakeIG{}
	b := newTestBridge(t, f)
	if rec := post(t, b, "/react", map[string]any{
		"room_id": threadShort, "message_id": "mid.$x", "emoji": "👍",
	}); rec.Code != http.StatusOK {
		t.Fatalf("/react answered %d: %s", rec.Code, rec.Body.String())
	}
	if rec := post(t, b, "/unsend", map[string]any{
		"room_id": threadShort, "message_id": "mid.$x",
	}); rec.Code != http.StatusOK {
		t.Fatalf("/unsend answered %d: %s", rec.Code, rec.Body.String())
	}
	if f.reactReq.Input.ThreadID != threadShort {
		t.Errorf("/react addressed %q, want the SHORT id", f.reactReq.Input.ThreadID)
	}
	if f.unsend.SendData.ThreadID != threadLong {
		t.Errorf("/unsend addressed %q, want the LONG id", f.unsend.SendData.ThreadID)
	}
	// The reaction is on the message afterwards. This is the read-back, not
	// the request: it is what makes the assertion above fail rather than pass
	// silently if the id ever goes back to the long one.
	if got := f.reactions["mid.$x"]; len(got) != 1 || got[0].SenderFBID != selfFBID {
		t.Fatalf("the reaction is not on the message: %+v", got)
	}
}

// A /react that Instagram accepts and does not apply must NOT answer 200. The
// bridge cannot tell the difference from the request, only from the post-state
// the mutation returns, so this drives the failure through the whole route.
func TestReactThatDoesNotLandIsNotA200(t *testing.T) {
	f := &fakeIG{}
	b := newTestBridge(t, f)
	// An own fbid of 0 makes the read-back unverifiable, which is an ERROR.
	b.setSelf(selfIdentity{IGID: "79597894900", Username: "not_____nora"})
	rec := post(t, b, "/react", map[string]any{
		"room_id": threadShort, "message_id": "mid.$x", "emoji": "👍",
	})
	if rec.Code == http.StatusOK {
		t.Fatalf("an unverifiable reaction answered 200: %s", rec.Body.String())
	}
}

// PINNED, NOT GROUNDED, and labelled so on purpose. Typing arrival was never
// measured in either id space, so there is no read-back to check and no fake
// behaviour that could be honest about it. What this asserts is only that the
// bridge follows upstream (handlematrix.go:484, `meta.IGID` = the short id) —
// if typing is ever measured, this test is the thing to replace, not to trust.
func TestTypingFollowsUpstreamAndUsesTheShortThreadID(t *testing.T) {
	f := &fakeIG{}
	b := newTestBridge(t, f)
	if rec := post(t, b, "/typing", map[string]any{"room_id": threadShort, "typing": true}); rec.Code != http.StatusOK {
		t.Fatalf("/typing answered %d", rec.Code)
	}
	if len(f.typing) != 1 || f.typing[0] != threadShort {
		t.Fatalf("typing addressed %v, want the short id", f.typing)
	}
}

// A send Instagram accepted without naming the message must not reach the
// adapter as 200 {"ok":true}: the contract promises {ok,id,timestamp_ms}.
func TestSendWithoutAMessageIDDoesNotAnswer200(t *testing.T) {
	for _, route := range []string{"/send", "/send-media"} {
		b := newTestBridge(t, &fakeIG{emptySend: true})
		rec := post(t, b, route, map[string]any{
			"room_id": threadShort, "text": "hi",
			"data_base64": base64.StdEncoding.EncodeToString([]byte("x")),
		})
		if rec.Code == http.StatusOK {
			t.Errorf("%s answered 200 with no message id: %s", route, rec.Body.String())
		}
	}
}

// --- /events ------------------------------------------------------------------

func TestEventsStreamsBufferedEventsThenLive(t *testing.T) {
	b := newTestBridge(t, &fakeIG{})
	b.hub.emit(&messageEvent{Kind: kindMessage, RoomID: threadShort, MessageID: "mid.$buffered"})

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
	for !b.hub.hasClients() && time.Now().Before(deadline) {
		time.Sleep(5 * time.Millisecond)
	}
	b.hub.emit(&messageEvent{Kind: kindMessage, RoomID: threadShort, MessageID: "mid.$live"})
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

// --- helpers ------------------------------------------------------------------

type errorString string

func (e errorString) Error() string { return string(e) }
