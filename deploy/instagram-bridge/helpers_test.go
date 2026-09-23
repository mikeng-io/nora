package main

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

var measuredUsers = fakeig.MeasuredUsers

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

// newTestBridge wires the whole HTTP path over a fake session. Nothing here
// touches the network: the live account is in use and cannot be probed, so
// the HTTP layer is verified against a recording fake that behaves like the
// measured wire, through the same gateway code production runs — spans
// included.
func newTestBridge(t *testing.T, f *fakeIG, opts ...bridgeOpt) *server {
	t.Helper()
	o := gateway.Options{
		Store:     session.Store{Dir: t.TempDir()},
		Sink:      stream.NewHub(),
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
	hub := o.Sink.(*stream.Hub)
	return &server{gw: gateway.NewBridge(o), hub: hub, startedAt: time.Now()}
}

func post(t *testing.T, b *server, path string, body any) *httptest.ResponseRecorder {
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
