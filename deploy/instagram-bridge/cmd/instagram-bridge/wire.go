package main

import (
	"context"
	"fmt"
	"log/slog"
	"net/http"
	"path/filepath"
	"time"

	"instagram-bridge/internal/app"
	"instagram-bridge/internal/bridge"
	"instagram-bridge/internal/domain"
	gateway "instagram-bridge/internal/gateway/instameow"
	"instagram-bridge/internal/session"
	"instagram-bridge/internal/stream"
	httptransport "instagram-bridge/internal/transport/http"
)

// durable carries the nora-bridge-wire v1 dependencies built from env in
// main. Everything is optional except Journal — a bridge with no
// NORA_BRIDGE_JOURNAL runs the compatibility surface only (SSE as before,
// no durable capture), which /v1/health reports honestly.
type durable struct {
	journal    *bridge.Journal
	projection *bridge.ProjectionSource
	queue      *bridge.Queue
	custody    *bridge.Custody
	actions    *bridge.ActionStore
	metrics    *bridge.Metrics
	accountKey string
	key        []byte
	keyVersion int
}

// wire assembles the layers: the gateway's one session implements every app
// port, the use cases sit on those ports, and the HTTP surface sits on the
// use cases. The durable capture sink — when configured — is the EventSink
// the gateway emits into: admit → journal → enqueue → (compat) hub.
//
// The summons ledger (comments.json) lives in the same session directory as
// the credential, so a restart keeps it (design 19).
func wire(o gateway.Options, hub *stream.Hub, startedAt time.Time, comments app.CommentPolicy, d *durable) (*gateway.Bridge, http.Handler, *bridge.Sink) {
	var sink *bridge.Sink
	if d != nil && d.journal != nil {
		sink = bridge.NewSink(bridge.SinkConfig{
			AccountKey: d.accountKey,
			Key:        d.key,
			KeyVersion: d.keyVersion,
			Journal:    d.journal,
			Queue:      d.queue,
			Projection: d.projection,
			Custody:    d.custody,
			Actions:    d.actions,
			Metrics:    d.metrics,
			Compat:     hub,
			OnHalt:     nil, // set below once the gateway exists
		})
		o.Sink = sink
	} else {
		o.Sink = hub
	}
	gw := gateway.NewBridge(o)
	svc := app.New(gw, gw, gw).WithComments(app.CommentOptions{
		Port:   gw,
		Ledger: session.NewLedger(o.Store),
		Policy: comments,
	})
	gw.SetSummonsRecorder(svc)
	handler := httptransport.New(svc, hub, startedAt)
	if sink != nil {
		sink.SetReceipt(func(ctx context.Context, room, messageID, emoji string) error {
			r, err := domain.ParseThreadFBID(room)
			if err != nil {
				return err
			}
			mid, err := domain.ParseMessageID(messageID)
			if err != nil {
				return err
			}
			_, err = svc.React(ctx, r, mid, emoji, false)
			return err
		})
		gw.UpstreamGate(sink.UpstreamOK)
		handler = handler.WithV1(&httptransport.V1{
			App: svc, Store: d.actions, Custody: d.custody,
			Metrics: d.metrics, AccountKey: d.accountKey,
			UpstreamOK: sink.UpstreamOK,
		})
	}
	return gw, handler.Routes(), sink
}

// durableFromEnv builds the durable half. Required config (frozen contract):
// NORA_BRIDGE_JOURNAL, NORA_REDIS_URL, NORA_BRIDGE_CORRELATION_KEY{,_VERSION}
// and NORA_BRIDGE_ACCOUNT_KEY — the authoritative opaque account leg, never
// derived from a platform id at runtime. durableWanted (env or journal set)
// with anything missing is a fatal misconfiguration: an explicitly enabled
// durable deployment must never silently run legacy SSE mode.
func durableFromEnv(journalPath, redisURL, dsn, projectionDir, accountKey, keyHex string, keyVersion int, durableWanted bool) (*durable, error) {
	if !durableWanted {
		return nil, nil
	}
	var missing []string
	if journalPath == "" {
		missing = append(missing, "NORA_BRIDGE_JOURNAL")
	}
	if redisURL == "" {
		missing = append(missing, "NORA_REDIS_URL")
	}
	if accountKey == "" {
		missing = append(missing, "NORA_BRIDGE_ACCOUNT_KEY")
	}
	if keyHex == "" {
		missing = append(missing, "NORA_BRIDGE_CORRELATION_KEY")
	}
	if len(missing) > 0 {
		return nil, fmt.Errorf("durable bridge requested but config missing: %v", missing)
	}
	key, err := bridge.CorrelationKey(keyHex)
	if err != nil {
		return nil, err
	}
	j, err := bridge.OpenJournal(journalPath, 0)
	if err != nil {
		return nil, err
	}
	d := &durable{
		journal:    j,
		projection: bridge.NewProjectionSource(projectionDir, "instagram"),
		metrics:    bridge.NewMetrics(),
		accountKey: accountKey,
		key:        key,
		keyVersion: keyVersion,
	}
	custodyDir := filepath.Join(filepath.Dir(journalPath), "attachments")
	if d.custody, err = bridge.NewCustody(custodyDir); err != nil {
		return nil, err
	}
	if d.queue, err = bridge.NewQueue(redisURL, "instagram", accountKey, "nora-v1"); err != nil {
		return nil, err
	}
	if dsn != "" {
		if d.actions, err = bridge.NewActionStore(context.Background(), dsn, "instagram", accountKey); err != nil {
			slog.Warn("action store unavailable; /v1/actions will answer 503", "error", err)
		} else {
			// Receipt authority binds to THIS bridge's journal — an
			// event_receipt owner names an event the journal holds with a
			// frozen receipt intent, under any of its three id spellings.
			d.actions.SetReceiptIntents(bridge.JournalReceiptIntents(j, key, keyVersion))
		}
	} else {
		slog.Warn("DATABASE_URL unset: /v1/actions answers 503 and receipts are journaled as skipped")
	}
	return d, nil
}
