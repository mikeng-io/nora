package bridge

// Typed platform observations for Instagram — the Go mirror of
// `nora/domain/observations.py` (the header + payload union) and
// `nora/platform/bridge/rehydrate.py:observation_to_dict` (the wire dict).
//
// The gateway (instameow) builds observation dicts from typed deltas and
// emits them on `kind:"observation"` events; this side validates the dict
// before it reaches the journal — fail closed, never a best-effort parse.
// A source event about an OBJECT (pin set, room, member set, artifact) is
// never an utterance: no fabricated speaker line, no fake empty message.
//
// Identity rules (design/platform-observations/observations.md):
//   - `upstream` is the transition identity for the envelope's source_key.
//     `uq_seq_id` is the upstream's own per-delta monotonic revision —
//     present it grounds identity AND ordering (source_position inside
//     revision_scope "uq_seq_id"); absent the identity falls back to the
//     content itself and ambiguity is retained, never a minted sequence.
//   - `object_ref` is the source's object handle: `pins:{room}`,
//     `room:{room}`, `members:{room}`, `admins:{room}`, `xma:{post|msg}`.
//     The admin set is a DIFFERENT object from the member set — its
//     subjects are IGIDs (public instagram ids), not messaging fbids,
//     and the two collections can never fold into each other.
//   - `actor` is who acted per the source (`sender_fbid`, `actor_fbid`);
//     `subject_ref` is who the event is ABOUT (left_participant_fbid).
//     actor==subject on a leave is a self-departure; actor!=subject is a
//     removal — reason is still unobservable either way.

// ObservationContract is OBSERVATION_CONTRACT — the payload-union version
// the app rejects anything other than.
const ObservationContract = 1

// RevisionScopeUQSeqID names the scope `source_position` orders inside:
// the iris uq_seq_id, per-delta, monotone as the client observes it.
const RevisionScopeUQSeqID = "uq_seq_id"

var observationKindOps = map[string]map[string]bool{
	"reaction":         {"add": true, "remove": true, "clear": true, "snapshot": true},
	"message_metadata": {"create": true, "revise": true, "withdraw": true},
	"poll":             {"create": true, "revise": true, "withdraw": true, "add": true, "remove": true, "clear": true, "snapshot": true},
	"room":             {"create": true, "revise": true, "withdraw": true, "snapshot": true},
	"membership":       {"add": true, "remove": true, "revise": true, "snapshot": true},
	"pin":              {"add": true, "remove": true, "snapshot": true},
	"artifact":         {"create": true, "revise": true, "withdraw": true},
}

var actorStatuses = map[string]bool{
	"known": true, "anonymous": true, "not_supplied": true, "not_applicable": true,
}

// observationProblem is `observation_from_dict`'s invariants, ported:
// "" when the dict is a legal wire observation, else the failing field.
// A malformed dict fails closed — it never reaches the journal.
func observationProblem(obs map[string]any) string {
	if obs == nil {
		return "not an object"
	}
	if c, _ := obs["contract"].(float64); int(c) != ObservationContract {
		return "contract"
	}
	kind, _ := obs["kind"].(string)
	ops, ok := observationKindOps[kind]
	if !ok {
		return "kind"
	}
	op, _ := obs["operation"].(string)
	if !ops[op] {
		return "operation"
	}
	if s, _ := obs["object_ref"].(string); s == "" {
		return "object_ref"
	}
	if st, present := obs["actor_status"]; present {
		s, _ := st.(string)
		if !actorStatuses[s] {
			return "actor_status"
		}
	}
	actorStatus, _ := obs["actor_status"].(string)
	if actorStatus == "" {
		actorStatus = "not_supplied"
	}
	actorID, _ := obs["actor_id"].(string)
	if actorStatus == "known" && actorID == "" {
		return "actor"
	}
	if actorID != "" && actorStatus == "not_applicable" {
		return "actor"
	}
	timeQ, _ := obs["time_quality"].(string)
	switch timeQ {
	case "", "upstream", "bridge_local", "unknown":
	default:
		return "time_quality"
	}
	if timeQ == "upstream" {
		if _, ok := obs["occurred_ms"].(float64); !ok {
			return "occurred_ms"
		}
	}
	scope, _ := obs["scope"].(map[string]any)
	for _, k := range []string{"platform", "account_key", "conversation_id"} {
		if s, _ := scope[k].(string); s == "" {
			return "scope." + k
		}
	}
	if sp, present := obs["source_position"]; present && sp != nil {
		if rs, _ := obs["revision_scope"].(string); rs == "" {
			return "revision_scope"
		}
	}
	return ""
}

// targetDict renders an EditTarget {message_id, sent_ts, mentions_known}
// the way `_target_dict` does on the Python side.
func targetDict(messageID string) map[string]any {
	return map[string]any{
		"message_id": messageID, "sent_ts": nil, "mentions_known": true,
	}
}

// artifactObservation is the shared-XMA sidecar the bridge derives at
// intake: the message event already carries the decoded `shared` fields,
// so the artifact record is built here where the link validator lives —
// never re-parsing the stanza, and never classifying Instagram's own
// typename (unmeasured; it rides verbatim in metadata).
//
// artifact_kind is the VALIDATED link kind (reel/post/story) when the
// target_url parses — a measured classification. When no link validates,
// "xma" names the native content kind the library decoded; guessing
// "post" would claim a classification nobody measured.
func artifactObservation(accountKey, room, messageID string, sender map[string]any, shared map[string]any, occurredMS, receivedMS int64, mediaHeld bool) map[string]any {
	kind := "xma"
	if _, linkKind, link := sharedLine(shared); link != "" {
		kind = string(linkKind) // reel | post | story — the validated URL shape
	}
	metadata := map[string]any{}
	for _, k := range []string{
		"caption", "author", "subtitle", "title", "target_url",
		"target_id", "typename", "layout",
	} {
		if v, ok := shared[k]; ok {
			metadata[k] = v
		}
	}
	if actions, ok := shared["actions"].([]any); ok && len(actions) > 0 {
		metadata["actions"] = actions
	}
	// The artifact's own ref is the platform's handle for the shared post
	// when the XMA supplies one (target_id — its id space is unmeasured,
	// carried verbatim); else the share stanza's message id anchors it.
	objectRef := "xma:" + messageID
	if tid, _ := shared["target_id"].(string); tid != "" {
		objectRef = "xma:" + tid
	}
	senderID := idForm(sender["fbid"])
	actorStatus := "not_supplied"
	if senderID != "" {
		actorStatus = "known"
	}
	timeQ := "unknown"
	var occurred any
	if occurredMS > 0 {
		occurred = float64(occurredMS)
		timeQ = "upstream"
	}
	return map[string]any{
		"contract": ObservationContract,
		"kind":     KindArtifact,
		"scope": map[string]any{
			"platform": Platform, "account_key": accountKey,
			"conversation_id": room, "thread": "",
		},
		"object_ref":       objectRef,
		"target":           targetDict(messageID),
		"target_author_id": "",
		"operation":        "create",
		"subject_ref":      "",
		"actor_id":         senderID,
		"actor_status":     actorStatus,
		"occurred_ms":      occurred,
		"received_ms":      receivedMS, // capture time, next to the envelope's
		"time_quality":     timeQ,
		"source_revision":  messageID,
		"source_position":  nil,
		"revision_scope":   "",
		"coverage": map[string]any{
			"complete": true, "covered": "reference",
			"gaps": []any{}, "reason": "",
		},
		"payload": map[string]any{
			"artifact_kind":  kind,
			"metadata":       metadata,
			"custody":        "declared", // the post's bytes are never held at ingest
			"perception_ref": "",
			"preview":        mediaHeld, // a cover thumbnail rode the delta
		},
	}
}
