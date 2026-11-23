package httptransport

// The platform contract (design/service-api/; docs/service-api/openapi.json):
//
//	POST /v1/platform/operations                 a delivery → operation envelope
//	GET  /v1/platform/operations/{operation_id}  its record → read envelope
//	POST /v1/platform/queries                    {kind, params} → read envelope
//	GET  /v1/platform/accounts/{account_id}      `self` → read envelope
//	PUT  /v1/platform/blobs/{key}                raw bytes → operation envelope
//	GET  /v1/platform/blobs/{key}                raw bytes, or a read envelope
//	GET  /v1/operations/status                   the bridge's detail → read envelope
//	GET  /metrics                                the durable-path counters
//
// Every route is behind the same bearer auth as the resource surface.
// Operations reserve-then-execute in bridge_action; a stored `confirmed` is
// `complete` on the wire and means the platform answered, never "queued".

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"time"

	"instagram-bridge/internal/app"
	"instagram-bridge/internal/bridge"
	"instagram-bridge/internal/domain"
	"instagram-bridge/internal/observability"

	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/trace"
)

// ActionStore is the slice of bridge.ActionStore the facade needs (an
// interface so the executor is testable without Postgres).
type ActionStore interface {
	Reserve(ctx context.Context, actionID, ownerKind, ownerID string, ownerGeneration *int, op string, request json.RawMessage, requestHash string) (bridge.ReserveOutcome, *bridge.ActionRow, error)
	Get(ctx context.Context, actionID string) (*bridge.ActionRow, error)
	MarkStarted(ctx context.Context, actionID string, messageID *int64) (bool, error)
	Finish(ctx context.Context, actionID, state, platformRef string, platformRefs []string, platformMS *int64, errKind, errMsg string) error
	ValidateOwner(ctx context.Context, kind, id string, generation *int, targetID string) (*bridge.OwnerVerdict, error)
}

// V1 is the contract routes' dependencies.
type V1 struct {
	App        *app.Service
	Store      ActionStore
	Custody    *bridge.Custody
	Metrics    *bridge.Metrics
	AccountKey string
	// UpstreamOK reports whether durable capture is healthy (admission
	// projection seen + journal intact), for the status read's `capture`.
	UpstreamOK func() bool
}

// operationKinds and queryKinds are the contract's closed sets; a kind
// outside them is 422.
var (
	operationKinds = map[string]bool{"send": true, "react": true, "edit": true, "typing": true}
	queryKinds     = map[string]bool{
		"roster": true, "members": true, "channels": true, "channel": true,
		"member_presence": true, "voice_presence": true, "groups": true,
	}
)

// OperationRequest is POST /v1/platform/operations's body. Effect is the
// kind's content, and the request hash covers it alone.
type OperationRequest struct {
	OperationID string          `json:"operation_id"`
	Owner       ActionOwner     `json:"owner"`
	Kind        string          `json:"kind"`
	Effect      json.RawMessage `json:"effect"`
}

// ActionOwner names the durable owner of an operation.
type ActionOwner struct {
	Kind       string `json:"kind"`
	ID         string `json:"id"`
	Generation *int   `json:"generation"`
}

// operationPayload is an operation envelope's payload.
type operationPayload struct {
	PlatformRefs []string        `json:"platform_refs"`
	PlatformMS   *int64          `json:"platform_ms"`
	MessageID    *int64          `json:"message_id"`
	Error        json.RawMessage `json:"error"`
}

func errorPayload(kind, message string) operationPayload {
	raw, _ := json.Marshal(map[string]string{"kind": kind, "message": message})
	return operationPayload{PlatformRefs: []string{}, Error: raw}
}

// rowOutcome maps a stored row onto the contract's outcome and lifecycle
// state. A rejection names why in error.kind: `unsupported` and `fenced`
// have outcomes of their own.
func rowOutcome(row *bridge.ActionRow) (outcome, state string) {
	switch row.State {
	case bridge.ActionPrepared, bridge.ActionStarted:
		return "pending", row.State
	case bridge.ActionConfirmed:
		return "complete", "settled"
	case bridge.ActionUncertain:
		return "uncertain", "settled"
	}
	var e struct {
		Kind string `json:"kind"`
	}
	_ = json.Unmarshal(row.Error, &e)
	switch e.Kind {
	case "unsupported":
		return "unsupported", "settled"
	case "fenced":
		return "denied", "settled"
	}
	return "rejected", "settled"
}

// rowEnvelope is the operation envelope of a stored row.
func rowEnvelope(r *http.Request, row *bridge.ActionRow) envelope {
	outcome, state := rowOutcome(row)
	refs := row.PlatformRefs
	if refs == nil {
		refs = []string{}
	}
	var errRaw json.RawMessage
	if len(row.Error) > 0 {
		errRaw = row.Error
	}
	target := effectTarget(row.Request)
	return envelope{
		Outcome: outcome,
		Payload: operationPayload{PlatformRefs: refs, PlatformMS: row.PlatformMS, MessageID: row.MessageID, Error: errRaw},
		When:    opWhen{State: state, StartedAtMS: row.StartedAtMS, SettledAtMS: row.SettledAtMS},
		From:    opFrom{Target: target, PlatformRef: row.PlatformRef},
		Scope:   roomScope(target),
		Trace:   traceOf(r),
	}
}

// effectTarget is the conversation an effect names, or "".
func effectTarget(effect json.RawMessage) string {
	var tgt struct {
		Target struct {
			ID string `json:"id"`
		} `json:"target"`
	}
	_ = json.Unmarshal(effect, &tgt)
	return tgt.Target.ID
}

// sendRequest is the `send` op's request shape.
type sendRequest struct {
	Target      actionTarget         `json:"target"`
	Text        string               `json:"text"`
	Mentions    []bridge.WireMention `json:"mentions"`
	Styles      []any                `json:"styles"`
	Preview     any                  `json:"preview"`
	Quote       *actionQuote         `json:"quote"`
	ReplyTo     string               `json:"reply_to"`
	Attachments []actionAttachment   `json:"attachments"`
}

type actionTarget struct {
	Kind string `json:"kind"`
	ID   string `json:"id"`
}

type actionQuote struct {
	PlatformMessageID string `json:"platform_message_id"`
	TS                int64  `json:"ts"`
	AuthorID          string `json:"author_id"`
}

type actionAttachment struct {
	Key       string `json:"key"`
	FileName  string `json:"file_name"`
	MediaType string `json:"media_type"`
}

type reactRequest struct {
	Target      actionTarget `json:"target"`
	Emoji       string       `json:"emoji"`
	ToAuthor    string       `json:"to_author"`
	ToTimestamp int64        `json:"to_timestamp"`
	ToMessageID string       `json:"to_message_id"`
	Remove      bool         `json:"remove"`
}

type editRequest struct {
	Target     actionTarget `json:"target"`
	MessageRef string       `json:"message_ref"`
	Text       string       `json:"text"`
}

type v1TypingRequest struct {
	Target actionTarget `json:"target"`
}

// handleOperation is POST /v1/platform/operations.
func (v *V1) handleOperation(w http.ResponseWriter, r *http.Request) {
	// The deliver span is a bounded ROOT — never a child of the otelhttp
	// server span — holding a link to the caller's W3C context when one rides
	// the request headers (design/instagram-adoption/bridge.md «Messaging
	// contract»). An absent or invalid traceparent simply carries no link.
	remote := trace.SpanContextFromContext(
		propagation.TraceContext{}.Extract(context.Background(),
			propagation.HeaderCarrier(r.Header)))
	var links []trace.Link
	if remote.IsValid() {
		links = []trace.Link{{SpanContext: remote}}
	}
	_, span := observability.Tracer().Start(context.Background(), "queue.deliver",
		trace.WithSpanKind(trace.SpanKindProducer),
		trace.WithLinks(links...),
		trace.WithAttributes(attribute.String("nora.trace_kind", "queue")))
	defer span.End()
	var req OperationRequest
	if _, ok := readJSONOr(w, r, maxJSONBody, &req, func(w http.ResponseWriter) {
		writeEnvelope(w, settledEnvelope(r, "unavailable",
			errorPayload("unavailable", "too many request bodies are being read; retry"), "", installationScope()))
	}); !ok {
		return
	}
	if len(req.Kind) <= 64 {
		span.SetAttributes(attribute.String("bridge.op", req.Kind))
	}
	if req.OperationID == "" || req.Kind == "" || len(req.Effect) == 0 {
		invalidRequest(w, "operation_id, kind and effect are required")
		return
	}
	if req.Owner.Kind == "" || req.Owner.ID == "" {
		invalidRequest(w, "owner.kind and owner.id are required")
		return
	}
	if req.Owner.Kind == bridge.OwnerWork && req.Owner.Generation == nil {
		invalidRequest(w, "owner.generation is required for kind=work")
		return
	}
	var effect any
	if err := json.Unmarshal(req.Effect, &effect); err != nil {
		invalidRequest(w, "effect must be JSON")
		return
	}
	if !operationKinds[req.Kind] {
		writeErrorEnvelope(w, http.StatusUnprocessableEntity, "unknown_kind", "kind must be one of send|react|edit|typing")
		return
	}
	target := effectTarget(req.Effect)
	scope := roomScope(target)
	settled := func(outcome string, payload operationPayload) {
		writeEnvelope(w, settledEnvelope(r, outcome, payload, target, scope))
	}
	ledgerDown := func() { settled("unavailable", errorPayload("unavailable", "action store unavailable")) }
	if v.Store == nil {
		settled("unavailable", errorPayload("unavailable", "action store is not configured"))
		return
	}
	if !bridge.ValidOwnerKind(req.Owner.Kind) {
		settled("denied", errorPayload("fenced", "unknown owner kind"))
		return
	}
	hash, canon, err := bridge.RequestHash(effect)
	if err != nil {
		invalidRequest(w, "effect must be JSON")
		return
	}
	outcome, row, err := v.Store.Reserve(r.Context(), req.OperationID,
		req.Owner.Kind, req.Owner.ID, req.Owner.Generation, req.Kind, canon, hash)
	if err != nil {
		if errors.Is(err, context.Canceled) {
			return
		}
		ledgerDown()
		return
	}
	switch outcome {
	case bridge.ReserveConflict:
		writeErrorEnvelope(w, http.StatusConflict, "operation_conflict",
			"operation_id already reserved for a different effect")
		return
	case bridge.ReserveReplay:
		v.Metrics.Count("action_replayed")
		writeEnvelope(w, rowEnvelope(r, row))
		return
	}
	// Execute: the owner fence first — EVERY owner kind validates against
	// its live authority reference, scoped to the effect's target
	// conversation (work generation+claim, journaled receipt intent, real
	// challenge/pending-outbound rows). A stale or foreign owner never
	// reaches a platform call.
	verdict, err := v.Store.ValidateOwner(r.Context(), req.Owner.Kind, req.Owner.ID, req.Owner.Generation, target)
	if err != nil {
		ledgerDown()
		return
	}
	if !verdict.OK {
		_ = v.Store.Finish(r.Context(), req.OperationID, bridge.ActionRejected, "", nil, nil, "fenced", verdict.Reason)
		v.Metrics.Count("action_rejected")
		if row, _ := v.Store.Get(r.Context(), req.OperationID); row != nil {
			writeEnvelope(w, rowEnvelope(r, row))
			return
		}
		settled("denied", errorPayload("fenced", verdict.Reason))
		return
	}
	// The started transition carries the bound pending-outbound audit row —
	// the action confirms the SAME messages row, never a second one.
	started, err := v.Store.MarkStarted(r.Context(), req.OperationID, verdict.MessageID)
	if err != nil || !started {
		// Lost the prepared→started race: answer the row as it stands.
		if row, _ := v.Store.Get(r.Context(), req.OperationID); row != nil {
			writeEnvelope(w, rowEnvelope(r, row))
			return
		}
		ledgerDown()
		return
	}
	v.execute(w, r, req, target, scope)
}

// execute runs the op, writes the terminal state and answers. Runs detached
// from the request's cancellation — a caller hanging up mid-send must not
// turn a landed platform call into an ambiguous one.
func (v *V1) execute(w http.ResponseWriter, r *http.Request, req OperationRequest, target string, scope envScope) {
	ctx, cancel := context.WithTimeout(context.WithoutCancel(r.Context()), 45*time.Second)
	defer cancel()
	state, ref, refs, ms, errKind, errMsg := v.runOp(ctx, req)
	if err := v.Store.Finish(ctx, req.OperationID, state, ref, refs, ms, errKind, errMsg); err != nil {
		// The platform call may have landed and its record is not settled:
		// uncertain, so nobody resends it.
		writeEnvelope(w, settledEnvelope(r, "uncertain",
			errorPayload("ledger", "operation ran; its result was not recorded"), target, scope))
		return
	}
	v.Metrics.Count("action_executed")
	v.Metrics.Count("action_" + outcomeMetric(state, errKind))
	row, err := v.Store.Get(ctx, req.OperationID)
	if err != nil || row == nil {
		writeEnvelope(w, settledEnvelope(r, "uncertain",
			errorPayload("ledger", "operation ran; its record could not be read back"), target, scope))
		return
	}
	writeEnvelope(w, rowEnvelope(r, row))
}

func outcomeMetric(state, errKind string) string {
	if state == bridge.ActionRejected && errKind == "unsupported" {
		return "unsupported"
	}
	return state
}

// runOp maps one op onto the platform calls and returns
// (state, platformRef, platformRefs, platformMS, errorKind, errorMessage).
// platformRefs carries EVERY confirmed piece's native handle — a text post
// and each media post are their own items; a rejected or uncertain outcome
// reports none.
//
// Honest-outcome rules:
//   - confirmed: the platform answered and named its handle;
//   - rejected: a definite refusal (validation, checkpoint, upstream 4xx);
//   - unsupported: no mechanism exists (edit on Instagram);
//   - uncertain: the call may have landed and its answer is lost — NEVER
//     blind-retried.
func (v *V1) runOp(ctx context.Context, req OperationRequest) (string, string, []string, *int64, string, string) {
	reject := func(kind, msg string) (string, string, []string, *int64, string, string) {
		return bridge.ActionRejected, "", nil, nil, kind, msg
	}
	uncertain := func(kind, msg string) (string, string, []string, *int64, string, string) {
		return bridge.ActionUncertain, "", nil, nil, kind, msg
	}
	switch req.Kind {
	case "send":
		var in sendRequest
		if err := json.Unmarshal(req.Effect, &in); err != nil {
			return reject("invalid", "malformed send request")
		}
		if in.Target.Kind != "dm" && in.Target.Kind != "group" {
			return reject("invalid", "target.kind must be dm|group")
		}
		room, err := domain.ParseThreadFBID(in.Target.ID)
		if err != nil {
			return reject("invalid", "target.id must be a thread fbid")
		}
		replyTo := in.ReplyTo
		if replyTo == "" && in.Quote != nil {
			replyTo = in.Quote.PlatformMessageID
		}
		if replyTo != "" {
			if _, err := domain.ParseMessageID(replyTo); err != nil {
				return reject("invalid", "reply target is not a message id")
			}
		}
		text, mentions := bridge.SpliceMentions(in.Text, in.Mentions)
		dom := make([]domain.Mention, 0, len(mentions))
		for _, m := range mentions {
			dom = append(dom, domain.Mention{FBID: m.FBID, Offset: m.Offset, Length: m.Length})
		}
		var ref string
		var refs []string
		var sentMS *int64
		if text != "" {
			sent, err := v.App.SendText(ctx, room, text, dom, replyTo)
			if err != nil {
				return mapOpErr(err, reject, uncertain)
			}
			ref, sentMS = sent.ID, &sent.TimestampMS
			if sent.ID != "" {
				refs = append(refs, sent.ID)
			}
		}
		for _, att := range in.Attachments {
			rec, data, ok := v.Custody.Get(att.Key)
			if !ok {
				return reject("invalid", "attachment key not in custody")
			}
			mime := att.MediaType
			if mime == "" {
				mime = rec.MediaType
			}
			name := att.FileName
			if name == "" {
				name = rec.FileName
			}
			sent, err := v.App.SendMedia(ctx, room, domain.Upload{
				Filename: name, MimeType: mime, Data: data,
			}, replyTo)
			if err != nil {
				// The text leg may already have landed: a media failure is
				// uncertain, not a clean rejection.
				if len(refs) > 0 {
					return uncertain("partial", "text sent; attachment send outcome unknown")
				}
				return mapOpErr(err, reject, uncertain)
			}
			ref, sentMS = sent.ID, &sent.TimestampMS
			if sent.ID != "" {
				refs = append(refs, sent.ID)
			}
		}
		if ref == "" && text == "" {
			return reject("invalid", "send needs text or attachments")
		}
		return bridge.ActionConfirmed, ref, refs, sentMS, "", ""

	case "react":
		var in reactRequest
		if err := json.Unmarshal(req.Effect, &in); err != nil {
			return reject("invalid", "malformed react request")
		}
		if in.Target.Kind != "dm" && in.Target.Kind != "group" {
			return reject("invalid", "target.kind must be dm|group")
		}
		room, err := domain.ParseThreadFBID(in.Target.ID)
		if err != nil {
			return reject("invalid", "target.id must be a thread fbid")
		}
		mid, err := domain.ParseMessageID(in.ToMessageID)
		if err != nil {
			return reject("invalid", "to_message_id is required for instagram reactions")
		}
		if in.Emoji == "" {
			return reject("invalid", "emoji is required (also when remove is true)")
		}
		confirmed, err := v.App.React(ctx, room, mid, in.Emoji, in.Remove)
		if err != nil {
			return mapOpErr(err, reject, uncertain)
		}
		if !confirmed {
			// The mutation was accepted but its post-state stayed silent —
			// the reaction path's measured uncertainty.
			return uncertain("unconfirmed", "reaction accepted but post-state silent")
		}
		return bridge.ActionConfirmed, in.ToMessageID, nil, nil, "", ""

	case "typing":
		var in v1TypingRequest
		if err := json.Unmarshal(req.Effect, &in); err != nil {
			return reject("invalid", "malformed typing request")
		}
		if in.Target.Kind != "dm" && in.Target.Kind != "group" {
			return reject("invalid", "target.kind must be dm|group")
		}
		room, err := domain.ParseThreadFBID(in.Target.ID)
		if err != nil {
			return reject("invalid", "target.id must be a thread fbid")
		}
		if err := v.App.Typing(ctx, room, true); err != nil {
			return mapOpErr(err, reject, uncertain)
		}
		return bridge.ActionConfirmed, "", nil, nil, "", ""

	case "edit":
		return reject("unsupported", "instagram has no outbound edit")
	}
	return reject("invalid", "unknown op")
}

// mapOpErr classifies an op failure into rejected vs uncertain. The rule:
// `uncertain` ONLY when the platform call may already have landed — a lost
// answer, a mid-call timeout. "Could not submit" (no client, validation,
// checkpoint, upstream refusal) is rejected.
func mapOpErr(err error, reject, uncertain func(string, string) (string, string, []string, *int64, string, string)) (string, string, []string, *int64, string, string) {
	switch {
	case errors.Is(err, context.DeadlineExceeded), errors.Is(err, context.Canceled):
		return uncertain("timeout", "upstream answer lost")
	case errors.Is(err, domain.ErrNeedsHuman):
		return reject("checkpoint", "session needs a human")
	case errors.Is(err, domain.ErrUnsupported):
		return reject("unsupported", "not supported by instagram")
	case errors.Is(err, domain.ErrInvalidID):
		return reject("invalid", "invalid identifier")
	case errors.Is(err, domain.ErrUnavailable):
		// No client existed to make the call — nothing was sent.
		return reject("unavailable", "session is reconnecting")
	case errors.Is(err, domain.ErrUpstreamRejected), errors.Is(err, domain.ErrTooLarge):
		return reject("upstream", "upstream refused")
	default:
		// An unclassified failure after a call may have started is honest
		// uncertainty, never a fabricated rejection.
		return uncertain("unknown", "upstream outcome unknown")
	}
}

// handleOperationGet is GET /v1/platform/operations/{operation_id}.
func (v *V1) handleOperationGet(w http.ResponseWriter, r *http.Request) {
	if v.Store == nil {
		writeEnvelope(w, readEnvelope(r, "unavailable", nil, false, installationScope()))
		return
	}
	row, err := v.Store.Get(r.Context(), r.PathValue("operation_id"))
	if err != nil {
		writeEnvelope(w, readEnvelope(r, "unavailable", nil, false, installationScope()))
		return
	}
	if row == nil {
		writeEnvelope(w, readEnvelope(r, "nothing_found", nil, true, installationScope()))
		return
	}
	op := rowEnvelope(r, row)
	writeEnvelope(w, readEnvelope(r, "ok", op, true, op.Scope))
}

// handleAccount is GET /v1/platform/accounts/{account_id}: this bridge
// answers for `self` only, once the login has named it.
func (v *V1) handleAccount(w http.ResponseWriter, r *http.Request) {
	if r.PathValue("account_id") != "self" {
		writeEnvelope(w, readEnvelope(r, "nothing_found", nil, true, installationScope()))
		return
	}
	snap := v.App.Health()
	primary := snap.Self.FBID
	if primary == "" {
		writeEnvelope(w, readEnvelope(r, "unavailable", nil, false, installationScope()))
		return
	}
	aliases := []string{primary}
	if snap.Self.IGID != "" && snap.Self.IGID != primary {
		aliases = append(aliases, snap.Self.IGID)
	}
	writeEnvelope(w, readEnvelope(r, "ok", map[string]any{
		"platform": "instagram", "primary": primary, "aliases": aliases,
	}, true, installationScope()))
}

// readiness is whether the bridge can serve the contract, and why not.
func (v *V1) readiness() (bool, string) {
	switch {
	case v.Store == nil:
		return false, "action store is not configured"
	case v.Custody == nil:
		return false, "attachment custody is not configured"
	}
	snap := v.App.Health()
	if snap.State != domain.StateAuthenticated {
		if snap.Detail != "" {
			return false, string(snap.State) + ": " + snap.Detail
		}
		return false, string(snap.State)
	}
	return true, snap.Detail
}

// handleStatus is GET /v1/operations/status.
func (v *V1) handleStatus(w http.ResponseWriter, r *http.Request) {
	snap := v.App.Health()
	ok, reason := v.readiness()
	var detail any
	if reason != "" {
		detail = reason
	}
	capture := "unknown"
	if v.UpstreamOK != nil {
		if v.UpstreamOK() {
			capture = "capturing"
		} else {
			capture = "not_capturing"
		}
	}
	writeEnvelope(w, readEnvelope(r, "ok", map[string]any{
		"ok": ok, "detail": detail, "degraded": false, "connected_at_ms": nil,
		"session": snap.State, "connected": snap.Connected, "capture": capture,
	}, ok, installationScope()))
}

// handleQuery is POST /v1/platform/queries. This bridge implements `roster`:
// {params:{conversation_id}} → the thread's members. The contract's other
// kinds are `unavailable` here.
func (v *V1) handleQuery(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Kind   string         `json:"kind"`
		Params map[string]any `json:"params"`
	}
	if _, ok := readJSONOr(w, r, maxJSONBody, &req, func(w http.ResponseWriter) {
		writeEnvelope(w, readEnvelope(r, "unavailable", nil, false, installationScope()))
	}); !ok {
		return
	}
	if !queryKinds[req.Kind] {
		writeErrorEnvelope(w, http.StatusUnprocessableEntity, "unknown_kind", "unknown query kind")
		return
	}
	conv, _ := req.Params["conversation_id"].(string)
	scope := roomScope(conv)
	if req.Kind != "roster" {
		writeEnvelope(w, readEnvelope(r, "unavailable", nil, false, scope))
		return
	}
	room, err := domain.ParseThreadFBID(conv)
	if err != nil {
		invalidRequest(w, "params.conversation_id must be a thread fbid")
		return
	}
	thread, err := v.App.Members(r.Context(), room)
	if errors.Is(err, domain.ErrNotFound) {
		writeEnvelope(w, readEnvelope(r, "nothing_found", nil, true, scope))
		return
	}
	if err != nil {
		slog.WarnContext(r.Context(), "query_roster failed", "code", errorCode(err), "conversation_id", conv)
		writeEnvelope(w, readEnvelope(r, "unavailable", nil, false, scope))
		return
	}
	members := make([]map[string]any, 0, len(thread.Members))
	for _, m := range thread.Members {
		members = append(members, map[string]any{
			"id": m.FBID, "igid": m.IGID,
			"username": m.Username, "display_name": m.FullName,
		})
	}
	writeEnvelope(w, readEnvelope(r, "ok", map[string]any{
		"conversation_id": conv, "is_group": thread.IsGroup,
		"title": thread.Title, "members": members,
	}, true, scope))
}

// handleBlobPut is PUT /v1/platform/blobs/{key}?name=<file>&pin=<owner>: an
// operation settled at once.
func (v *V1) handleBlobPut(w http.ResponseWriter, r *http.Request) {
	key := r.PathValue("key")
	settled := func(outcome string, payload any) {
		writeEnvelope(w, settledEnvelope(r, outcome, payload, key, installationScope()))
	}
	rejected := func(kind, message string) {
		settled("rejected", map[string]any{"error": map[string]string{"kind": kind, "message": message}})
	}
	if v.Custody == nil {
		settled("unavailable", map[string]any{"error": map[string]string{"kind": "unavailable", "message": "attachment custody is not configured"}})
		return
	}
	if !bridge.ValidKey(key) {
		invalidRequest(w, "invalid blob key")
		return
	}
	rec, err := v.Custody.Put(key, http.MaxBytesReader(w, r.Body, bridge.MaxAttachmentBytes+1),
		r.Header.Get("Content-Type"), r.URL.Query().Get("name"), r.URL.Query().Get("pin"))
	switch {
	case errors.Is(err, bridge.ErrKeyConflict):
		rejected("conflict", "key already held for different bytes")
		return
	case errors.Is(err, bridge.ErrTooLarge):
		rejected("too_large", "blob exceeds the size bound")
		return
	case err != nil:
		settled("unavailable", map[string]any{"error": map[string]string{"kind": "unavailable", "message": "custody write failed"}})
		return
	}
	v.Metrics.Count("custody_spooled")
	settled("complete", map[string]any{"key": rec.Key})
}

// handleBlobGet is GET /v1/platform/blobs/{key}: the bytes, or a read
// envelope when there are none to return.
func (v *V1) handleBlobGet(w http.ResponseWriter, r *http.Request) {
	if v.Custody == nil {
		writeEnvelope(w, readEnvelope(r, "unavailable", nil, false, installationScope()))
		return
	}
	rec, data, err := v.Custody.Find(r.PathValue("key"))
	if errors.Is(err, bridge.ErrNoObject) {
		writeEnvelope(w, readEnvelope(r, "nothing_found", nil, true, installationScope()))
		return
	}
	if err != nil {
		writeEnvelope(w, readEnvelope(r, "unavailable", nil, false, installationScope()))
		return
	}
	w.Header().Set("Content-Type", rec.MediaType)
	w.Header().Set("X-Content-Sha256", rec.SHA256)
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(data)
}

// handleMetrics is GET /metrics.
func (v *V1) handleMetrics(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "text/plain; version=0.0.4")
	_, _ = io.WriteString(w, v.Metrics.Render())
}
