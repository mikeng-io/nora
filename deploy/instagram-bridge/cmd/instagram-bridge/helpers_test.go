package main

// End-to-end tests of the wired bridge: the real HTTP surface, the real use
// cases and the real gateway, over a fake session that behaves like the
// measured wire. Built through wire(), the function main uses, so a test
// cannot assemble the layers differently from production.

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"instagram-bridge/internal/domain"
	gateway "instagram-bridge/internal/gateway/instameow"
	"instagram-bridge/internal/gateway/instameow/fakeig"
	"instagram-bridge/internal/session"
	"instagram-bridge/internal/stream"

	"go.mau.fi/mautrix-meta/pkg/messagix/httpclient"
	"go.mau.fi/mautrix-meta/pkg/messagix/types"
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

// newTestBridge wires the whole path over a fake session. Nothing here
// touches the network: the live account is in use and cannot be probed.
func newTestBridge(t *testing.T, f *fakeIG, opts ...bridgeOpt) *testBridge {
	t.Helper()
	o := gateway.Options{
		Store:     session.Store{Dir: t.TempDir()},
		State:     domain.StateAuthenticated,
		Connected: true,
		Seq:       22,
		Self:      domain.Self{FBID: "14963523149149302", IGID: "79597894900", Username: "not_____nora"},
		Client:    f,
		Upload: func(context.Context, int64, *httpclient.MercuryUploadMedia) (*types.MercuryUploadResponse, error) {
			return &types.MercuryUploadResponse{
				Payload: types.MediaPayloads{RealMetadata: &types.FileMetadata{ImageID: 2280432885466163}},
			}, nil
		},
	}
	for _, opt := range opts {
		opt(&o)
	}
	hub := stream.NewHub()
	gw, h := wire(o, hub, time.Now())
	return &testBridge{gw: gw, hub: hub, h: h}
}

func post(t *testing.T, b *testBridge, path string, body any) *httptest.ResponseRecorder {
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

type errorString string

func (e errorString) Error() string { return string(e) }
