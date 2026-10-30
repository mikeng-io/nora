package httptransport

// The shared /v1 bridge facade (the frozen nora-bridge-wire actions API):
//
//	POST /v1/actions            ActionRequest → ActionResponse
//	GET  /v1/actions/{id}       recovery readback
//	GET  /v1/self               {platform, primary, aliases[]}
//	GET  /v1/health             {ok, detail}
//	POST /v1/reads              {op, params} → result JSON
//	PUT  /v1/attachments/{key}  raw bytes → 201
//	GET  /v1/attachments/{key}  raw bytes → 404
//	GET  /metrics               the durable-path counters
//
// Every route is behind the same bearer auth as the resource surface. Action
// semantics are the contract's: reserve-then-execute in bridge_action;
// `confirmed` means the platform answered, never "queued".

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"time"

	"instagram-bridge/internal/app"
	"instagram-bridge/internal/bridge"
	"instagram-bridge/internal/domain"
)

// ActionStore is the slice of bridge.ActionStore the facade needs (an
// interface so the executor is testable without Postgres).
type ActionStore interface {
	Reserve(ctx context.Context, actionID, ownerKind, ownerID string, ownerGeneration *int, op string, request json.RawMessage, requestHash string) (bridge.ReserveOutcome, *bridge.ActionRow, error)
	Get(ctx context.Context, actionID string) (*bridge.ActionRow, error)
	MarkStarted(ctx context.Context, actionID string, messageID *int64) (bool, error)
	Finish(ctx context.Context, actionID, state, platformRef string, platformMS *int64, errKind, errMsg string) error
	ValidateOwner(ctx context.Context, kind, id string, generation *int, targetID string) (*bridge.OwnerVerdict, error)
}

// V1 is the facade's dependencies.
type V1 struct {
	App        *app.Service
	Store      ActionStore
	Custody    *bridge.Custody
	Metrics    *bridge.Metrics
	AccountKey string
	// UpstreamOK reports whether durable capture is healthy (admission
	// projection seen + journal intact). Surfaced in /v1/health's detail.
	UpstreamOK func() bool
}

// ActionRequest is POST /v1/actions's body.
type ActionRequest struct {
	ActionID string          `json:"action_id"`
	Owner    ActionOwner     `json:"owner"`
	Op       string          `json:"op"`
	Request  json.RawMessage `json:"request"`
}

// ActionOwner names the durable owner of an action.
type ActionOwner struct {
	Kind       string `json:"kind"`
	ID         string `json:"id"`
	Generation *int   `json:"generation"`
}

// ActionResponse is the readback shape.
type ActionResponse struct {
	ActionID    string          `json:"action_id"`
	State       string          `json:"state"`
	Outcome     any             `json:"outcome"`
	PlatformRef *string         `json:"platform_ref"`
	MessageID   *int64          `json:"message_id"`
	Error       json.RawMessage `json:"error"`
}

// actionOutcome derives the outcome vocabulary the app reads.
func actionOutcome(row *bridge.ActionRow) any {
	switch row.State {
	case bridge.ActionConfirmed:
		return bridge.OutcomeConfirmed
	case bridge.ActionRejected:
		var e struct {
			Kind string `json:"kind"`
		}
		if len(row.Error) > 0 && json.Unmarshal(row.Error, &e) == nil && e.Kind == "unsupported" {
			return bridge.OutcomeUnsupported
		}
		return bridge.OutcomeRejected
	case bridge.ActionUncertain:
		return bridge.OutcomeUncertain
	}
	return nil
}

func actionResponse(row *bridge.ActionRow) ActionResponse {
	errRaw := row.Error
	if len(errRaw) == 0 {
		errRaw = json.RawMessage("null")
	}
	return ActionResponse{
		ActionID: row.ActionID, State: row.State,
		Outcome: actionOutcome(row), PlatformRef: row.PlatformRef,
		MessageID: row.MessageID, Error: errRaw,
	}
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

// errActionUsage is a caller-side request error (validation), never a
// platform answer. Mapped to state=rejected, error.kind="invalid".
var errActionUsage = errors.New("invalid action request")

// handleActions is POST /v1/actions.
func (v *V1) handleActions(w http.ResponseWriter, r *http.Request) {
	if v.Store == nil {
		writeErrorEnvelope(w, http.StatusServiceUnavailable, "action_store", "action store is not configured")
		return
	}
	var req ActionRequest
	if _, ok := readJSON(w, r, maxJSONBody, &req); !ok {
		return
	}
	if req.ActionID == "" || req.Op == "" || len(req.Request) == 0 {
		invalidRequest(w, "action_id, op and request are required")
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
	var reqObj any
	if err := json.Unmarshal(req.Request, &reqObj); err != nil {
		invalidRequest(w, "request must be JSON")
		return
	}
	hash, canon, err := bridge.RequestHash(reqObj)
	if err != nil {
		invalidRequest(w, "request must be JSON")
		return
	}
	outcome, row, err := v.Store.Reserve(r.Context(), req.ActionID,
		req.Owner.Kind, req.Owner.ID, req.Owner.Generation, req.Op, canon, hash)
	if err != nil {
		if errors.Is(err, context.Canceled) {
			return
		}
		writeErrorEnvelope(w, http.StatusInternalServerError, "action_store", "action store unavailable")
		return
	}
	switch outcome {
	case bridge.ReserveConflict:
		writeErrorEnvelope(w, http.StatusConflict, "action_conflict",
			"action_id already reserved for a different request")
		return
	case bridge.ReserveReplay:
		v.Metrics.Count("action_replayed")
		writeJSON(w, http.StatusOK, actionResponse(row))
		return
	}
	// Execute: the owner fence first — EVERY owner kind validates against
	// its live authority reference, scoped to the request's target
	// conversation (work generation+claim, journaled receipt intent, real
	// challenge/pending-outbound rows). A stale or foreign owner never
	// reaches a platform call.
	var targetID string
	var tgt struct {
		Target struct {
			ID string `json:"id"`
		} `json:"target"`
	}
	if err := json.Unmarshal(req.Request, &tgt); err == nil {
		targetID = tgt.Target.ID
	}
	verdict, err := v.Store.ValidateOwner(r.Context(), req.Owner.Kind, req.Owner.ID, req.Owner.Generation, targetID)
	if err != nil {
		writeErrorEnvelope(w, http.StatusInternalServerError, "action_store", "action store unavailable")
		return
	}
	if !verdict.OK {
		_ = v.Store.Finish(r.Context(), req.ActionID, bridge.ActionRejected, "", nil, "fenced", verdict.Reason)
		v.Metrics.Count("action_rejected")
		row, _ := v.Store.Get(r.Context(), req.ActionID)
		writeJSON(w, http.StatusOK, actionResponse(row))
		return
	}
	// The started transition carries the bound pending-outbound audit row —
	// the action confirms the SAME messages row, never a second one.
	started, err := v.Store.MarkStarted(r.Context(), req.ActionID, verdict.MessageID)
	if err != nil || !started {
		// Lost the prepared→started race: return the row as it stands.
		row, _ := v.Store.Get(r.Context(), req.ActionID)
		if row != nil {
			writeJSON(w, http.StatusOK, actionResponse(row))
			return
		}
		writeErrorEnvelope(w, http.StatusInternalServerError, "action_store", "action store unavailable")
		return
	}
	v.execute(w, r, req)
}

// execute runs the op, writes the terminal state and answers. Runs detached
// from the request's cancellation — a caller hanging up mid-send must not
// turn a landed platform call into an ambiguous one.
func (v *V1) execute(w http.ResponseWriter, r *http.Request, req ActionRequest) {
	ctx, cancel := context.WithTimeout(context.WithoutCancel(r.Context()), 45*time.Second)
	defer cancel()
	state, ref, ms, errKind, errMsg := v.runOp(ctx, req)
	if err := v.Store.Finish(ctx, req.ActionID, state, ref, ms, errKind, errMsg); err != nil {
		writeErrorEnvelope(w, http.StatusInternalServerError, "action_store", "action store unavailable")
		return
	}
	v.Metrics.Count("action_executed")
	v.Metrics.Count("action_" + outcomeMetric(state, errKind))
	row, _ := v.Store.Get(ctx, req.ActionID)
	if row == nil {
		writeErrorEnvelope(w, http.StatusInternalServerError, "action_store", "action store unavailable")
		return
	}
	writeJSON(w, http.StatusOK, actionResponse(row))
}

func outcomeMetric(state, errKind string) string {
	if state == bridge.ActionRejected && errKind == "unsupported" {
		return "unsupported"
	}
	return state
}

// runOp maps one op onto the platform calls and returns
// (state, platformRef, platformMS, errorKind, errorMessage).
//
// Honest-outcome rules:
//   - confirmed: the platform answered and named its handle;
//   - rejected: a definite refusal (validation, checkpoint, upstream 4xx);
//   - unsupported: no mechanism exists (edit on Instagram);
//   - uncertain: the call may have landed and its answer is lost — NEVER
//     blind-retried.
func (v *V1) runOp(ctx context.Context, req ActionRequest) (string, string, *int64, string, string) {
	reject := func(kind, msg string) (string, string, *int64, string, string) {
		return bridge.ActionRejected, "", nil, kind, msg
	}
	uncertain := func(kind, msg string) (string, string, *int64, string, string) {
		return bridge.ActionUncertain, "", nil, kind, msg
	}
	switch req.Op {
	case "send":
		var in sendRequest
		if err := json.Unmarshal(req.Request, &in); err != nil {
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
		var sentMS *int64
		if text != "" {
			sent, err := v.App.SendText(ctx, room, text, dom, replyTo)
			if err != nil {
				return mapOpErr(err, reject, uncertain)
			}
			ref, sentMS = sent.ID, &sent.TimestampMS
		}
		for i, att := range in.Attachments {
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
				if ref != "" {
					return uncertain("partial", "text sent; attachment send outcome unknown")
				}
				return mapOpErr(err, reject, uncertain)
			}
			if i == len(in.Attachments)-1 {
				ref, sentMS = sent.ID, &sent.TimestampMS
			}
		}
		if ref == "" && text == "" {
			return reject("invalid", "send needs text or attachments")
		}
		return bridge.ActionConfirmed, ref, sentMS, "", ""

	case "react":
		var in reactRequest
		if err := json.Unmarshal(req.Request, &in); err != nil {
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
		return bridge.ActionConfirmed, in.ToMessageID, nil, "", ""

	case "typing":
		var in v1TypingRequest
		if err := json.Unmarshal(req.Request, &in); err != nil {
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
		return bridge.ActionConfirmed, "", nil, "", ""

	case "edit":
		return reject("unsupported", "instagram has no outbound edit")
	}
	return reject("invalid", "unknown op")
}

// mapOpErr classifies an op failure into rejected vs uncertain. The rule:
// `uncertain` ONLY when the platform call may already have landed — a lost
// answer, a mid-call timeout. "Could not submit" (no client, validation,
// checkpoint, upstream refusal) is rejected.
func mapOpErr(err error, reject, uncertain func(string, string) (string, string, *int64, string, string)) (string, string, *int64, string, string) {
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

// handleActionGet is GET /v1/actions/{action_id}.
func (v *V1) handleActionGet(w http.ResponseWriter, r *http.Request) {
	if v.Store == nil {
		writeErrorEnvelope(w, http.StatusServiceUnavailable, "action_store", "action store is not configured")
		return
	}
	row, err := v.Store.Get(r.Context(), r.PathValue("action_id"))
	if err != nil {
		writeErrorEnvelope(w, http.StatusInternalServerError, "action_store", "action store unavailable")
		return
	}
	if row == nil {
		writeErrorEnvelope(w, http.StatusNotFound, "not_found", "unknown action_id")
		return
	}
	writeJSON(w, http.StatusOK, actionResponse(row))
}

// handleV1Self is GET /v1/self.
func (v *V1) handleV1Self(w http.ResponseWriter, r *http.Request) {
	snap := v.App.Health()
	primary := snap.Self.FBID
	aliases := []string{}
	if primary != "" {
		aliases = append(aliases, primary)
	}
	if snap.Self.IGID != "" && snap.Self.IGID != primary {
		aliases = append(aliases, snap.Self.IGID)
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"platform": "instagram", "primary": primary, "aliases": aliases,
	})
}

// handleV1Health is GET /v1/health.
func (v *V1) handleV1Health(w http.ResponseWriter, r *http.Request) {
	snap := v.App.Health()
	ok := snap.State == domain.StateAuthenticated
	var detail any
	if snap.Detail != "" {
		detail = snap.Detail
	}
	upstream := "unknown"
	if v.UpstreamOK != nil {
		if v.UpstreamOK() {
			upstream = "capturing"
		} else {
			upstream = "not_capturing"
		}
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"ok": ok, "detail": detail,
		"session": snap.State, "connected": snap.Connected,
		"capture": upstream,
	})
}

// handleV1Reads is POST /v1/reads. Supported ops:
//   - "roster": {params:{conversation_id}} → the thread's members.
func (v *V1) handleV1Reads(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Op     string         `json:"op"`
		Params map[string]any `json:"params"`
	}
	if _, ok := readJSON(w, r, maxJSONBody, &req); !ok {
		return
	}
	switch req.Op {
	case "roster":
		conv, _ := req.Params["conversation_id"].(string)
		room, err := domain.ParseThreadFBID(conv)
		if err != nil {
			invalidRequest(w, "params.conversation_id must be a thread fbid")
			return
		}
		thread, err := v.App.Members(r.Context(), room)
		if err != nil {
			failEnvelope(r.Context(), w, "reads_roster", err, "conversation_id", conv)
			return
		}
		members := make([]map[string]any, 0, len(thread.Members))
		for _, m := range thread.Members {
			members = append(members, map[string]any{
				"id": m.FBID, "igid": m.IGID,
				"username": m.Username, "display_name": m.FullName,
			})
		}
		writeJSON(w, http.StatusOK, map[string]any{
			"ok": true, "observed_ms": time.Now().UnixMilli(),
			"result": map[string]any{
				"conversation_id": conv, "is_group": thread.IsGroup,
				"title": thread.Title, "members": members,
			},
		})
	default:
		writeErrorEnvelope(w, http.StatusNotImplemented, "unsupported", "unknown read op")
	}
}

// handleV1AttachmentPut is PUT /v1/attachments/{key}?pin=<owner>.
func (v *V1) handleV1AttachmentPut(w http.ResponseWriter, r *http.Request) {
	if v.Custody == nil {
		writeErrorEnvelope(w, http.StatusServiceUnavailable, "custody", "attachment custody is not configured")
		return
	}
	key := r.PathValue("key")
	if !bridge.ValidKey(key) {
		invalidRequest(w, "invalid attachment key")
		return
	}
	mediaType := r.Header.Get("Content-Type")
	rec, err := v.Custody.Put(key, http.MaxBytesReader(w, r.Body, bridge.MaxAttachmentBytes+1),
		mediaType, r.URL.Query().Get("name"), r.URL.Query().Get("pin"))
	if errors.Is(err, bridge.ErrKeyConflict) {
		writeErrorEnvelope(w, http.StatusConflict, "key_conflict", "key already held for different bytes")
		return
	}
	if err != nil {
		writeErrorEnvelope(w, http.StatusInternalServerError, "custody", "custody write failed")
		return
	}
	v.Metrics.Count("custody_spooled")
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	_ = json.NewEncoder(w).Encode(map[string]any{"key": rec.Key, "size": rec.Size, "sha256": rec.SHA256})
}

// handleV1AttachmentGet is GET /v1/attachments/{key}.
func (v *V1) handleV1AttachmentGet(w http.ResponseWriter, r *http.Request) {
	if v.Custody == nil {
		writeErrorEnvelope(w, http.StatusServiceUnavailable, "custody", "attachment custody is not configured")
		return
	}
	rec, data, ok := v.Custody.Get(r.PathValue("key"))
	if !ok {
		writeErrorEnvelope(w, http.StatusNotFound, "not_found", "unknown attachment key")
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
