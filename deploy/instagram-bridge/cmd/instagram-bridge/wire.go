package main

import (
	"net/http"
	"time"

	"instagram-bridge/internal/app"
	gateway "instagram-bridge/internal/gateway/instameow"
	"instagram-bridge/internal/stream"
	httptransport "instagram-bridge/internal/transport/http"
)

// wire assembles the layers: the gateway's one session implements every app
// port, the use cases sit on those ports, and the HTTP surface sits on the use
// cases. main and the end-to-end tests build the bridge through this one
// function, so a test cannot wire it differently from production.
func wire(o gateway.Options, hub *stream.Hub, startedAt time.Time) (*gateway.Bridge, http.Handler) {
	o.Sink = hub
	gw := gateway.NewBridge(o)
	svc := app.New(gw, gw, gw)
	return gw, httptransport.New(svc, hub, startedAt).Routes()
}
