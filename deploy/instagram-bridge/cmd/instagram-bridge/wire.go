package main

import (
	"net/http"
	"time"

	"instagram-bridge/internal/app"
	gateway "instagram-bridge/internal/gateway/instameow"
	"instagram-bridge/internal/session"
	"instagram-bridge/internal/stream"
	httptransport "instagram-bridge/internal/transport/http"
)

// wire assembles the layers: the gateway's one session implements every app
// port, the use cases sit on those ports, and the HTTP surface sits on the use
// cases. main and the end-to-end tests build the bridge through this one
// function, so a test cannot wire it differently from production.
//
// The summons ledger (comments.json) lives in the same session directory as
// the credential, so a restart keeps it (design 19). The gateway's summons
// intake records into it through the use cases (RecordSummons), set here
// before the supervisor starts: without it no summons is ever recorded and
// every reply is refused.
func wire(o gateway.Options, hub *stream.Hub, startedAt time.Time, comments app.CommentPolicy) (*gateway.Bridge, http.Handler) {
	o.Sink = hub
	gw := gateway.NewBridge(o)
	svc := app.New(gw, gw, gw).WithComments(app.CommentOptions{
		Port:   gw,
		Ledger: session.NewLedger(o.Store),
		Policy: comments,
	})
	gw.SetSummonsRecorder(svc)
	return gw, httptransport.New(svc, hub, startedAt).Routes()
}
