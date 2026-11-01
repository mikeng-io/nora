package instameow

// Typed platform observations — instameow's typed deltas mapped onto the
// contract's observation wire dict (nora/domain/observations.py, mirrored
// field-for-field in internal/bridge/observations.go).
//
// A delta about an OBJECT — the room's pin set, the room itself, the member
// or admin collection — is never an utterance. These builders emit the
// typed record; the embedded admin-text message a delta may also carry is
// emitted separately by emitDelta, exactly as before (one real service
// message, deduplicated by its own mid).
//
// Identity rules (design/platform-observations/02):
//   - uq_seq_id is the source's own per-delta monotonic revision: it rides
//     the observation as source_position inside revision_scope "uq_seq_id",
//     and it anchors the transition's upstream identity. A delta without
//     one falls back to content identity — a redelivery dedupes, a real
//     re-transition reports ambiguous downstream; nothing invents a clock.
//   - The pin/thread/member deltas carry NO actor field and NO timestamp:
//     actor_status is "not_supplied" and time_quality "unknown" — the
//     leave delta alone types actor_fbid vs left_participant_fbid.
//   - admin_igids are IGIDs, NOT messaging fbids: the admin collection is
//     a different object (admins:{room}) from the member set
//     (members:{room}) so the two id spaces can never fold into each other.

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"strconv"

	"go.mau.fi/mautrix-meta/pkg/instameow/slidetypes"
)

// observationContract is OBSERVATION_CONTRACT — mirrored; the validating
// side owns the same constant.
const observationContract = 1

// revisionScopeUQSeqID is the scope source_position orders inside.
const revisionScopeUQSeqID = "uq_seq_id"

// observationEvent is one typed observation on the event stream. `upstream`
// is the transition identity the source key is built from; `upstream_seq`
// is the delta's own revision (0 when the delta carried none); timestamp_ms
// is the occurred clock when the source supplies one — the envelope's
// sent_ms stays null when it does not.
type observationEvent struct {
	Kind        string         `json:"kind"` // "observation"
	RoomID      string         `json:"room_id"`
	Upstream    string         `json:"upstream"`
	SenderFBID  string         `json:"sender_fbid,omitempty"`
	TimestampMS int64          `json:"timestamp_ms,omitempty"`
	UpstreamSeq int64          `json:"upstream_seq,omitempty"`
	Observation map[string]any `json:"observation"`
}

// seqOr renders the delta's own revision inside an upstream identity;
// "na" when the delta carried none — ambiguity retained, never a minted
// sequence.
func seqOr(seq int64) string {
	if seq > 0 {
		return strconv.FormatInt(seq, 10)
	}
	return "na"
}

// contentHash is the deterministic content identity for transitions that
// carry no upstream revision — sorted-JSON sha256, a redelivery reproduces
// it byte-identically.
func contentHash(v any) string {
	sum := sha256.Sum256(canonicalJSON(v))
	return hex.EncodeToString(sum[:])[:20]
}

func canonicalJSON(v any) []byte {
	// encoding/json marshals map keys sorted — deterministic for the flat
	// field maps these identities hash over.
	raw, err := json.Marshal(v)
	if err != nil {
		return []byte("null")
	}
	return raw
}

func scopeDict(room string) map[string]any {
	return map[string]any{
		"platform": "instagram", "account_key": "",
		"conversation_id": room, "thread": "",
	}
}

// observationDict renders the contract's wire record. `account_key` is left
// empty — the capture side stamps the one identity it owns; scope.platform
// and scope.conversation_id are declared by the gateway and must match the
// envelope it rides, which the validating side enforces.
func observationDict(room, kind, objectRef, operation string,
	payload map[string]any, coverage map[string]any,
	actorID, actorStatus, subjectRef string,
	target map[string]any, occurredMS, seq int64) map[string]any {

	if coverage == nil {
		coverage = map[string]any{
			"complete": true, "covered": "event", "gaps": []any{}, "reason": "",
		}
	}
	var occurred any
	timeQ := "unknown"
	if occurredMS > 0 {
		occurred = occurredMS
		timeQ = "upstream"
	}
	var position any
	revisionScope := ""
	var sourceRevision any
	if seq > 0 {
		position = seq
		revisionScope = revisionScopeUQSeqID
		sourceRevision = "uq:" + strconv.FormatInt(seq, 10)
	}
	return map[string]any{
		"contract":         observationContract,
		"kind":             kind,
		"scope":            scopeDict(room),
		"object_ref":       objectRef,
		"target":           target,
		"target_author_id": "",
		"operation":        operation,
		"subject_ref":      subjectRef,
		"actor_id":         actorID,
		"actor_status":     actorStatus,
		"occurred_ms":      occurred,
		"received_ms":      0, // stamped at capture
		"time_quality":     timeQ,
		"source_revision":  sourceRevision,
		"source_position":  position,
		"revision_scope":   revisionScope,
		"coverage":         coverage,
		"payload":          payload,
	}
}

func roomCoverage() map[string]any {
	return map[string]any{
		"complete": true, "covered": "fields", "gaps": []any{}, "reason": "",
	}
}

// fbidOf renders a messaging-fbid field; "" when the delta carried 0 — a
// zero is never an identity.
func fbidOf(v int64) string {
	if v == 0 {
		return ""
	}
	return strconv.FormatInt(v, 10)
}

// pinObservations is SlideUQPPPinUnpinMessage: one payload naming both the
// pinned and unpinned message ids — per-target add/remove transitions on
// the room's pin-set object. No actor or timestamp exists on this event;
// both are honestly unavailable.
func pinObservations(room string, e *slidetypes.PinMessageEvent, seq int64) []*observationEvent {
	out := make([]*observationEvent, 0, len(e.PinnedMessages)+len(e.UnpinnedMessages))
	for _, id := range e.PinnedMessages {
		if id == "" {
			continue
		}
		out = append(out, pinTransition(room, id, "add", seq))
	}
	for _, id := range e.UnpinnedMessages {
		if id == "" {
			continue
		}
		out = append(out, pinTransition(room, id, "remove", seq))
	}
	return out
}

func pinTransition(room, messageID, op string, seq int64) *observationEvent {
	target := map[string]any{
		"message_id": messageID, "sent_ts": nil, "mentions_known": true,
	}
	obs := observationDict(room, "pin", "pins:"+room, op,
		map[string]any{"message": target, "pins": []any{}},
		nil, "", "not_supplied", "", target, 0, seq)
	return &observationEvent{
		Kind: "observation", RoomID: room,
		Upstream:    fmt.Sprintf("pin:%s:%s:%s", seqOr(seq), messageID, op),
		UpstreamSeq: seq, Observation: obs,
	}
}

// roomFieldObservation is a thread-attribute delta — title or image — as a
// room revise. The embedded admin message carries the actor and timestamp
// when the delta supplies one; the message itself is emitted separately by
// emitDelta (the service text is genuine content, deduped by its mid).
func roomFieldObservation(room string, fields map[string]any, msg *slidetypes.Message, seq int64) *observationEvent {
	var actor string
	var occurred int64
	if msg != nil {
		actor = fbidOf(msg.SenderFBID)
		if !msg.TimestampMS.IsZero() {
			occurred = msg.TimestampMS.UnixMilli()
		}
	}
	actorStatus := "not_supplied"
	if actor != "" {
		actorStatus = "known"
	}
	obs := observationDict(room, "room", "room:"+room, "revise",
		map[string]any{"fields": fields}, roomCoverage(),
		actor, actorStatus, "", nil, occurred, seq)
	ev := &observationEvent{
		Kind: "observation", RoomID: room,
		Upstream:    fmt.Sprintf("roomrev:%s:%s", seqOr(seq), contentHash(map[string]any{"f": fields, "a": actor})),
		SenderFBID:  actor,
		UpstreamSeq: seq,
		Observation: obs,
	}
	if occurred > 0 {
		ev.TimestampMS = occurred
	}
	return ev
}

// joinSnapshot is ParticipantsAddedToGroupThread: the event carries NO
// typed joiner field — who joined is derivable only by diffing member
// snapshots or reading the admin text. What the delta DOES carry is the
// POST-ACTION member set (thread.users[]), so the honest record is a
// membership snapshot with covered-set semantics: the reported users are
// the covered member collection, not a claim about the delta's subject.
//
// Subjects are messaging fbids (interop_messaging_user_fbid) — the same id
// space every other subject_ref on this bridge uses. A user whose fbid is
// absent is not silently re-keyed on the igid: the coverage gap records
// that some entries had no messaging identity.
func joinSnapshot(room string, e *slidetypes.ParticipantJoinEvent, seq int64) *observationEvent {
	var actor string
	var occurred int64
	if e.Message != nil {
		actor = fbidOf(e.Message.SenderFBID)
		if !e.Message.TimestampMS.IsZero() {
			occurred = e.Message.TimestampMS.UnixMilli()
		}
	}
	actorStatus := "not_supplied"
	if actor != "" {
		actorStatus = "known"
	}
	var members []any
	missingFBID := 0
	memberSetListed := false
	if t := e.Thread.AsIGDirectThread; t != nil {
		for _, u := range t.Users {
			if u == nil {
				continue
			}
			memberSetListed = true
			fbid := fbidOf(u.InteropMessagingUserFBID)
			if fbid == "" {
				missingFBID++
				continue
			}
			members = append(members, map[string]any{
				"subject": fbid, "role": "", "state": "member",
			})
		}
	}
	// A delta carrying no usable member list is a PARTIAL snapshot: an
	// empty-but-complete covered set would fold as "the room has nobody"
	// and erase the roster downstream.
	coverage := map[string]any{
		"complete": memberSetListed && missingFBID == 0, "covered": "members",
		"gaps": []any{}, "reason": "",
	}
	if !memberSetListed {
		coverage["gaps"] = []any{"member_set"}
		coverage["reason"] = "the delta carried no member list — no member is covered"
	} else if missingFBID > 0 {
		coverage["gaps"] = []any{"member_fbids"}
		coverage["reason"] = fmt.Sprintf(
			"%d member(s) carried no interop_messaging_user_fbid and are not keyed", missingFBID)
	}
	obs := observationDict(room, "membership", "members:"+room, "snapshot",
		map[string]any{"role": "", "state": "", "covered": "members", "members": members},
		coverage, actor, actorStatus, "", nil, occurred, seq)
	ev := &observationEvent{
		Kind: "observation", RoomID: room,
		Upstream:    fmt.Sprintf("msnap:%s:%s", seqOr(seq), contentHash(members)),
		UpstreamSeq: seq, Observation: obs,
	}
	if actor != "" {
		ev.SenderFBID = actor
	}
	if occurred > 0 {
		ev.TimestampMS = occurred
	}
	return ev
}

// leaveObservation is ParticipantLeftGroupThread — the ONE delta that
// types actor and subject separately. actor==subject is a self-departure
// (state "left"); actor!=subject is a removal — the remove operation
// records the departure without asserting intent (reason is unobservable).
// viewer_fbid only scopes whose copy this delta is; it is not evidence.
func leaveObservation(room string, e *slidetypes.ParticipantLeaveEvent, seq int64) *observationEvent {
	actor := fbidOf(e.ActorFBID)
	subject := fbidOf(e.LeftParticipantFBID)
	actorStatus := "not_supplied"
	if actor != "" {
		actorStatus = "known"
	}
	state := ""
	if actor != "" && actor == subject {
		state = "left" // the leaver's own action — the source proves it
	}
	var occurred int64
	if e.Message != nil && !e.Message.TimestampMS.IsZero() {
		occurred = e.Message.TimestampMS.UnixMilli()
	}
	obs := observationDict(room, "membership", "members:"+room, "remove",
		map[string]any{"role": "", "state": state, "covered": "", "members": []any{}},
		nil, actor, actorStatus, subject, nil, occurred, seq)
	ev := &observationEvent{
		Kind: "observation", RoomID: room,
		Upstream:    fmt.Sprintf("memleave:%s:%s:%s", seqOr(seq), subject, actor),
		SenderFBID:  actor,
		UpstreamSeq: seq,
		Observation: obs,
	}
	if occurred > 0 {
		ev.TimestampMS = occurred
	}
	return ev
}

// adminSnapshot is SlideUQPPGenericMapAdminsKeyMutation: admin_igids is the
// admin SET as a snapshot — and it is IGID space, NOT the messaging-fbid
// space every member subject uses. The admin collection is therefore its
// own object (admins:{room}): the two sets can never merge, and a subject
// here is honestly an igid, not a re-keyed fbid. No actor and no delta
// direction exist — the set itself is all the source reports.
func adminSnapshot(room string, e *slidetypes.AdminChangeEvent, seq int64) *observationEvent {
	var members []any
	for _, igid := range e.AdminIGIDs {
		if igid == "" {
			continue
		}
		members = append(members, map[string]any{
			"subject": igid, "role": "admin", "state": "member",
		})
	}
	coverage := map[string]any{
		"complete": true, "covered": "admins",
		"gaps":   []any{"subjects_are_igids"},
		"reason": "admin_igids is the public igid space, not the messaging fbid space",
	}
	obs := observationDict(room, "membership", "admins:"+room, "snapshot",
		map[string]any{"role": "", "state": "", "covered": "admins", "members": members},
		coverage, "", "not_applicable", "", nil, 0, seq)
	return &observationEvent{
		Kind: "observation", RoomID: room,
		Upstream:    fmt.Sprintf("asnap:%s:%s", seqOr(seq), contentHash(members)),
		UpstreamSeq: seq,
		Observation: obs,
	}
}
