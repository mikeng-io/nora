package main

// End-to-end tests of the wired bridge: the real HTTP surface, the real use
// cases and the real gateway, over a fake session that behaves like the
// measured wire. Built through wire(), the function main uses, so a test
// cannot assemble the layers differently from production.

import (
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
	"time"

	"instagram-bridge/internal/app"
	"instagram-bridge/internal/domain"
	gateway "instagram-bridge/internal/gateway/instameow"
	"instagram-bridge/internal/gateway/instameow/fakeig"
	"instagram-bridge/internal/session"
	"instagram-bridge/internal/stream"
)

// The measured-wire fake, under the names these tests have always used.
type fakeIG = fakeig.IG

const (
	threadShort = fakeig.ThreadShort
	threadLong  = fakeig.ThreadLong
	selfFBID    = fakeig.SelfFBID
)

var (
	measuredUsers         = fakeig.MeasuredUsers
	measuredSearchResults = fakeig.MeasuredSearchResults
)

// testBridge is one wired bridge: its handler, its session, its event hub.
type testBridge struct {
	gw  *gateway.Bridge
	hub *stream.Hub
	h   http.Handler
}

func (b *testBridge) routes() http.Handler { return b.h }

// bridgeOpt adjusts the session a test bridge starts in. Everything a test
// used to poke into the bridge after building it is set here instead, at
// construction, the way the supervisor sets it in production.
type bridgeOpt func(*gateway.Options)

func withState(state domain.SessionState, detail string) bridgeOpt {
	return func(o *gateway.Options) { o.State, o.Detail = state, detail }
}

func withConnected(v bool) bridgeOpt { return func(o *gateway.Options) { o.Connected = v } }

func withSelf(self domain.Self) bridgeOpt { return func(o *gateway.Options) { o.Self = self } }

// withNoClient is a session whose client is being (re)built.
func withNoClient() bridgeOpt { return func(o *gateway.Options) { o.Client, o.Upload = nil, nil } }

func withIssuer(i gateway.GraphQLIssuer) bridgeOpt { return func(o *gateway.Options) { o.Issuer = i } }

// The doc_ids every test bridge is configured with. Obviously fake: the real
// ones are config on the host and never in the repo.
const (
	testStoryDocID    = "1234"
	testTimelineDocID = "5678"
)

// withDocs replaces the configured doc_ids ("" is an unconfigured read).
func withDocs(docs gateway.QueryDocs) bridgeOpt { return func(o *gateway.Options) { o.Docs = docs } }

// newTestBridge wires the whole path over a fake session. Nothing here
// touches the network: the live account is in use and cannot be probed.
func newTestBridge(t *testing.T, f *fakeIG, opts ...bridgeOpt) *testBridge {
	t.Helper()
	return newTestBridgeWithComments(t, f, app.DefaultCommentPolicy(), opts...)
}

// newTestBridgeWithComments is newTestBridge with the comment write's policy
// set (writes are off in newTestBridge, as in production by default).
func newTestBridgeWithComments(t *testing.T, f *fakeIG, commentPolicy app.CommentPolicy, opts ...bridgeOpt) *testBridge {
	t.Helper()
	o := gateway.Options{
		Store:     session.Store{Dir: t.TempDir()},
		State:     domain.StateAuthenticated,
		Connected: true,
		Seq:       22,
		Self:      domain.Self{FBID: "18247361241432477", IGID: "79597894900", Username: "not_____nora"},
		Client:    f,
		// The fake records every upload, so a test can assert what reached
		// the library (filename, mimetype, bytes), not only that one happened.
		Upload: f.Upload,
		// The configured GraphQL documents go through the same fake, which
		// records every request they send.
		DocIssuer: f,
		Docs:      gateway.QueryDocs{Story: testStoryDocID, Timeline: testTimelineDocID},
	}
	for _, opt := range opts {
		opt(&o)
	}
	hub := stream.NewHub()
	gw, h, _ := wire(o, hub, time.Now(), commentPolicy, nil)
	return &testBridge{gw: gw, hub: hub, h: h}
}

// request sends one request. A nil body sends none; a string is sent raw
// (for a body that is deliberately not JSON); anything else is marshalled.
func request(t *testing.T, b *testBridge, method, path string, body any) *httptest.ResponseRecorder {
	t.Helper()
	var payload string
	switch v := body.(type) {
	case nil:
	case string:
		payload = v
	default:
		blob, err := json.Marshal(body)
		if err != nil {
			t.Fatal(err)
		}
		payload = string(blob)
	}
	req := httptest.NewRequest(method, path, strings.NewReader(payload))
	rec := httptest.NewRecorder()
	b.routes().ServeHTTP(rec, req)
	return rec
}

// The write routes (17 §P14), addressed the way Nora's adapter addresses
// them: the SHORT thread id and the message id as path segments, the message
// id escaped because it carries `$` and may carry `/` or `+`.
func messagesPath(room string) string { return "/threads/" + url.PathEscape(room) + "/messages" }

func messagePath(room, mid string) string { return messagesPath(room) + "/" + url.PathEscape(mid) }

func sendMessage(t *testing.T, b *testBridge, room string, body any) *httptest.ResponseRecorder {
	t.Helper()
	return request(t, b, http.MethodPost, messagesPath(room), body)
}

func unsendMessage(t *testing.T, b *testBridge, room, mid string) *httptest.ResponseRecorder {
	t.Helper()
	return request(t, b, http.MethodDelete, messagePath(room, mid), nil)
}

func putReaction(t *testing.T, b *testBridge, room, mid string, body any) *httptest.ResponseRecorder {
	t.Helper()
	return request(t, b, http.MethodPut, messagePath(room, mid)+"/reaction", body)
}

func putTyping(t *testing.T, b *testBridge, room string, body any) *httptest.ResponseRecorder {
	t.Helper()
	return request(t, b, http.MethodPut, "/threads/"+url.PathEscape(room)+"/typing", body)
}

// mediaOf is a media message's body for bytes.
func mediaOf(data []byte, filename, mimetype string) map[string]any {
	return map[string]any{"media": map[string]any{
		"data_base64": base64.StdEncoding.EncodeToString(data),
		"filename":    filename, "mimetype": mimetype,
	}}
}

// decodeEnvelope reads a failure body, failing the test if it is not the
// envelope.
func decodeEnvelope(t *testing.T, rec *httptest.ResponseRecorder) errorEnvelope {
	t.Helper()
	var env errorEnvelope
	if err := json.Unmarshal(rec.Body.Bytes(), &env); err != nil || env.Error.Code == "" {
		t.Fatalf("body is not the error envelope: %v (%s)", err, rec.Body.String())
	}
	return env
}

type errorString string

func (e errorString) Error() string { return string(e) }
