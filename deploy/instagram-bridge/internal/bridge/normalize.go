package bridge

// Inbound normalization: one instameow SSE event (the 03-transport-contract
// shape, arrived as `any` on the sink) → one nora-bridge-wire v1 envelope.
// The semantics are ported 1:1 from nora/platform/instagram/parser.py — the
// bridge parses upstream ONCE (the comprehend-once rule) and the app
// rehydrates the typed value; it never re-interprets `raw`.
//
// `message` is serialized exactly as the lead's rehydrate.py reads it:
// platform, conversation_id, conversation_type, sender_id, sender_name,
// timestamp, body, raw, mentions, quote, media, message_id, media_blobs,
// external_files, voice, thread, edit_of, touch — plus `reaction`, which the
// frozen wire field list names (the lead's deserializer reads `touch`; both
// are emitted until the two contract texts agree).

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"strings"
)

// Platform is this bridge's wire identity.
const Platform = "instagram"

// mediaTypeByKind is _MEDIA_TYPE_BY_KIND: the wire carries no mimetype, so
// the subtype is honestly unknown (`*`).
var mediaTypeByKind = map[string]string{
	"image": "image/*",
	"video": "video/*",
	"audio": "audio/*",
}

// Normalized is a parsed inbound event: the envelope (minus the parts the
// capture pipeline fills — trace, queued_ms, admission_rev, media custody)
// plus the facts capture needs: the upstream identity already folded into
// SourceKey/EventID, and the pending media fetch, if any.
type Normalized struct {
	Env *Envelope
	// MediaURL is the signed CDN URL capture may copy into custody, "" when
	// the event has no fetchable attachment.
	MediaURL string
	// MediaIndex is which message.media entry the URL belongs to.
	MediaIndex int
	// Transitions lists the last-state keys this event advanced, so capture
	// can record them in the journal (reaction add/remove/add identity).
	Transitions []Transition
}

// Transition is one upstream state change the transition ledger tracks:
// identityKey scopes it (room+target+reactor+emoji), state is add/remove,
// UpstreamSeq is the delta's own iris uq_seq_id — 0 when the upstream
// carried none (truly clockless; the ledger then cannot ground ordering).
type Transition struct {
	Key         string
	State       string
	UpstreamSeq int64
}

// jsonMap renders the sink event into the dict the contract serializes.
// The sink hands `any` (instameow's unexported event structs); round-tripping
// through JSON yields exactly the wire shape the Python parser consumed.
func jsonMap(ev any) (map[string]any, error) {
	raw, err := json.Marshal(ev)
	if err != nil {
		return nil, err
	}
	var m map[string]any
	if err := json.Unmarshal(raw, &m); err != nil {
		return nil, err
	}
	return m, nil
}

func str(m map[string]any, key string) string {
	v, _ := m[key].(string)
	return v
}

func strPtr(m map[string]any, key string) *string {
	if v, ok := m[key].(string); ok {
		return &v
	}
	return nil
}

func intMS(m map[string]any, key string) int64 {
	switch v := m[key].(type) {
	case float64:
		return int64(v)
	case int64:
		return v
	}
	return 0
}

func boolVal(m map[string]any, key string) bool {
	v, _ := m[key].(bool)
	return v
}

// idForm is fbid_form/igid_form/conversation_id_for: strip, else "".
func idForm(v any) string {
	s, _ := v.(string)
	return strings.TrimSpace(s)
}

// isPersonMention ports ids.is_person_mention: an absent range_type is a
// profile mention (omitempty on the wire); a present one must be PROFILE —
// THREAD carries the CHAT's id, not a person's.
func isPersonMention(rangeType string) bool {
	return rangeType == "" || rangeType == "PROFILE"
}

// mediaLabel reduces a ref to one of the closed LABELS set.
func mediaLabel(mediaType string, sticker, onDemand bool) string {
	if sticker {
		return "sticker"
	}
	top := ""
	if i := strings.Index(mediaType, "/"); i > 0 {
		top = mediaType[:i]
	}
	var label string
	switch top {
	case "image":
		label = "image"
	case "video":
		label = "video"
	case "audio":
		label = "audio"
	case "text", "application":
		label = "document"
	default:
		label = "file"
	}
	if onDemand && label == "image" {
		return "cover image"
	}
	if top != "text" && top != "application" && label == "document" {
		return "file"
	}
	return label
}

// mediaRef builds the MediaRef dict from the event's `media` object.
// fetch_token is left for capture: it names the custody key once the bytes
// are spooled (a raw expiring URL is never a durable reference).
func mediaRef(media map[string]any) map[string]any {
	kind := str(media, "kind")
	if kind == "" {
		return nil
	}
	return map[string]any{
		"platform_id": idForm(media["attachment_fbid"]),
		"media_type":  mediaTypeByKind[kind],
		"filename":    nil,
		"size":        nil,
		"voice_note":  false, // Instagram cannot tell a voice note from a shared song.
		"sticker":     kind == "animated",
		"fetch_token": "", // filled by capture once custody holds the bytes
		"on_demand":   false,
	}
}

// sharedTarget is _shared_target: (kind, link) read off target_url's PATH;
// the link is REBUILT from validated segments, never passed through.
func sharedTarget(targetURL, author string) (LinkKind, string) {
	if strings.TrimSpace(targetURL) == "" {
		return LinkPost, ""
	}
	kind, ok := LinkShape(targetURL, false)
	if !ok || (kind != LinkReel && kind != LinkPost && kind != LinkStory) {
		return LinkPost, ""
	}
	link, ok := ParseLink(targetURL)
	if !ok || link.Kind != kind {
		return kind, ""
	}
	if kind == LinkStory && !strings.EqualFold(link.Key, strings.TrimPrefix(strings.TrimSpace(author), "@")) {
		return kind, ""
	}
	return kind, link.URL()
}

// sharedLine is _shared_line: `[shared reel by @x <link> — caption]`.
// It returns "" when there is nothing to say (absent or empty `shared`).
func sharedLine(shared map[string]any) (line string, kind LinkKind, link string) {
	if len(shared) == 0 {
		return "", "", ""
	}
	rawAuthor := strings.TrimPrefix(strings.TrimSpace(str(shared, "author")), "@")
	kind, link = sharedTarget(str(shared, "target_url"), rawAuthor)
	author := ""
	if IsHandle(rawAuthor) {
		author = rawAuthor
	}
	caption := strings.TrimSpace(str(shared, "caption"))
	if caption == "" {
		caption = strings.TrimSpace(str(shared, "title"))
	}
	// NO SQUARE BRACKETS FROM A STRANGER: `]` would forge a placeholder.
	caption = strings.NewReplacer("[", "(", "]", ")").Replace(caption)
	if link != "" {
		caption = captionExcerpt(caption)
	}
	line = "shared " + string(kind)
	if author != "" {
		line += " by @" + author
	}
	if link != "" {
		line += " " + link
	}
	if caption != "" {
		line += " — " + caption
	}
	return "[" + line + "]", kind, link
}

// mentionedIDs is _mentioned_ids: person mentions only, fbids.
func mentionedIDs(entries any) []string {
	list, _ := entries.([]any)
	out := map[string]bool{}
	for _, e := range list {
		m, ok := e.(map[string]any)
		if !ok || !isPersonMention(str(m, "range_type")) {
			continue
		}
		if fbid := idForm(m["fbid"]); fbid != "" {
			out[fbid] = true
		}
	}
	sorted := make([]string, 0, len(out))
	for f := range out {
		sorted = append(sorted, f)
	}
	// json.Marshal sorts map keys; keep a deterministic list either way.
	for i := 0; i < len(sorted); i++ {
		for j := i + 1; j < len(sorted); j++ {
			if sorted[j] < sorted[i] {
				sorted[i], sorted[j] = sorted[j], sorted[i]
			}
		}
	}
	return sorted
}

// quoteOf is _quoted: the reply pointer, with quoted_ts 0 when the delta
// carried only the parent's id.
func quoteOf(replyTo any) map[string]any {
	m, ok := replyTo.(map[string]any)
	if !ok {
		return nil
	}
	return map[string]any{
		"author_id": idForm(m["sender_fbid"]),
		"text":      strings.TrimSpace(str(m, "text")),
		"quoted_ts": intMS(m, "timestamp_ms"),
	}
}

// Normalize converts one SSE event dict into the v1 envelope + the facts the
// capture pipeline needs. `accountKey` is this bridge's stable account key;
// `key`/`keyVersion` mint the HMAC event alias; `receivedMS` is the bridge's
// own arrival clock. Returns nil for events the contract drops (heartbeat,
// dropped, typing, self echoes, empty turns, missing room), exactly as the
// Python parser returns None.
func Normalize(ev any, accountKey string, key []byte, keyVersion int, receivedMS int64) (*Normalized, error) {
	m, err := jsonMap(ev)
	if err != nil {
		return nil, err
	}
	kind := str(m, "kind")
	switch kind {
	case KindEdit:
		return normalizeEdit(m, accountKey, key, keyVersion, receivedMS)
	case KindReaction, "unsend":
		return normalizeTouch(m, accountKey, key, keyVersion, receivedMS)
	case KindSummons:
		return normalizeSummons(m, accountKey, key, keyVersion, receivedMS)
	case KindMessage:
		return normalizeMessage(m, accountKey, key, keyVersion, receivedMS)
	}
	return nil, nil // heartbeat / dropped / typing / unknown kinds
}

// normalizeMessage is parse_event's message branch.
func normalizeMessage(m map[string]any, accountKey string, key []byte, keyVersion int, receivedMS int64) (*Normalized, error) {
	if boolVal(m, "from_me") {
		return nil, nil // R-12's first pass: never ingest our own sends
	}
	room := idForm(m["room_id"])
	if room == "" {
		return nil, nil
	}
	shared, _ := m["shared"].(map[string]any)
	sharedLineText, sharedKind, sharedLink := sharedLine(shared)
	var ref map[string]any
	if media, ok := m["media"].(map[string]any); ok {
		ref = mediaRef(media)
	}
	if ref != nil && len(shared) > 0 {
		// The media on a shared item is its THUMBNAIL. A linked post or reel
		// drops it (the link reads the item itself); a story keeps its cover
		// (the link reads the CURRENT story, which may no longer hold that
		// frame); no link at all keeps it — the cover is all there is.
		if sharedLink != "" && sharedKind != LinkStory {
			ref = nil
		} else {
			ref["on_demand"] = true
		}
	}
	var parts []string
	if text := strings.TrimSpace(str(m, "text")); text != "" {
		parts = append(parts, text)
	}
	if sharedLineText != "" {
		parts = append(parts, sharedLineText)
	}
	if ref != nil {
		label := mediaLabel(ref["media_type"].(string), boolVal(ref, "sticker"), boolVal(ref, "on_demand"))
		parts = append(parts, "["+label+"]")
	}
	if len(parts) == 0 {
		return nil, nil // neither text nor media — nothing to ingest
	}
	sender, _ := m["sender"].(map[string]any)
	ts := intMS(m, "timestamp_ms")
	convType := "group"
	if v, ok := m["is_group"].(bool); ok && !v {
		convType = "dm"
	}
	messageID := str(m, "message_id")
	upstream := "mid:" + messageID
	if messageID == "" {
		// No platform handle: identity from content, stable across replay —
		// a redelivery reproduces the same bytes and is deduped, while a
		// different message cannot collide unless it is byte-identical.
		sum := sha256.Sum256([]byte(str(m, "text") + "\x00" + str(m, "content_type")))
		upstream = fmt.Sprintf("ts:%d:snd:%s:h:%s", ts, idForm(sender["fbid"]), hex.EncodeToString(sum[:])[:16])
	}
	message := map[string]any{
		"platform":          Platform,
		"conversation_id":   room,
		"conversation_type": convType,
		"sender_id":         idForm(sender["fbid"]),
		"sender_name":       nilIfEmpty(idForm(sender["username"])),
		"timestamp":         ts,
		"body":              strings.Join(parts, " "),
		"raw":               m,
		"mentions":          mentionedIDs(m["mentions"]),
		"quote":             quoteOf(m["reply_to"]),
		"media":             refsList(ref),
		"message_id":        messageID,
		"media_blobs":       []any{},
		"external_files":    []any{},
		"voice":             false,
		"thread":            "",
		"edit_of":           nil,
		"touch":             nil,
		"reaction":          nil,
	}
	sentMS, quality := ts, TimeUpstream
	if ts <= 0 {
		sentMS, quality = receivedMS, TimeBridgeLocal
	}
	sourceKey := SourceKey(Platform, accountKey, room, KindMessage, upstream)
	env := &Envelope{
		V: 1, Platform: Platform, AccountKey: accountKey, Kind: KindMessage,
		SourceKey: sourceKey, EventID: EventID(sourceKey, key, keyVersion),
		ConversationID: room, ConversationType: convType,
		SenderID: idForm(sender["fbid"]), SenderName: strOrNil(idForm(sender["username"])),
		SentMS: sentMS, ReceivedMS: receivedMS, TimeQuality: quality,
		Message: message, Attachments: []Attachment{},
		Trace: map[string]any{}, Receipt: nil,
	}
	n := &Normalized{Env: env, MediaIndex: 0}
	if ref != nil {
		if media, ok := m["media"].(map[string]any); ok {
			n.MediaURL = str(media, "url")
		}
	}
	return n, nil
}

// normalizeEdit is _parse_edit: a `kind:"edit"` event → an edit of the
// message it names. No sender on the wire; arrival time stands in when the
// delta carried no timestamp (a zero would sort before every other edit).
func normalizeEdit(m map[string]any, accountKey string, key []byte, keyVersion int, receivedMS int64) (*Normalized, error) {
	room := idForm(m["room_id"])
	target := str(m, "message_id")
	text := strings.TrimSpace(str(m, "text"))
	if room == "" || target == "" || text == "" {
		return nil, nil
	}
	ts := intMS(m, "timestamp_ms")
	sentMS, quality := ts, TimeUpstream
	if ts <= 0 {
		sentMS, quality = receivedMS, TimeBridgeLocal
	}
	sum := sha256.Sum256([]byte(text))
	upstream := fmt.Sprintf("edit:%s:%d:%s", target, ts, hex.EncodeToString(sum[:])[:8])
	editOf := map[string]any{"message_id": target, "sent_ts": nil, "mentions_known": false}
	message := map[string]any{
		"platform": Platform, "conversation_id": room, "conversation_type": "group",
		"sender_id": "", "sender_name": nil, "timestamp": sentMS, "body": text,
		"raw": m, "mentions": []any{}, "quote": nil, "media": []any{},
		"message_id": "", "media_blobs": []any{}, "external_files": []any{},
		"voice": false, "thread": "", "edit_of": editOf, "touch": nil, "reaction": nil,
	}
	sourceKey := SourceKey(Platform, accountKey, room, KindEdit, upstream)
	return &Normalized{Env: &Envelope{
		V: 1, Platform: Platform, AccountKey: accountKey, Kind: KindEdit,
		SourceKey: sourceKey, EventID: EventID(sourceKey, key, keyVersion),
		ConversationID: room, ConversationType: "group",
		SenderID: "", SenderName: nil,
		SentMS: sentMS, ReceivedMS: receivedMS, TimeQuality: quality,
		Message: message, Attachments: []Attachment{}, Trace: map[string]any{}, Receipt: nil,
	}}, nil
}

// normalizeTouch is _parse_touch: `reaction` → a reaction Touch, `unsend` →
// a delete Touch. from_me true is Nora's own reaction — dropped (R-12).
func normalizeTouch(m map[string]any, accountKey string, key []byte, keyVersion int, receivedMS int64) (*Normalized, error) {
	if fm, ok := m["from_me"].(bool); ok && fm {
		return nil, nil
	}
	room := idForm(m["room_id"])
	target := str(m, "message_id")
	if room == "" || target == "" {
		return nil, nil
	}
	kind := str(m, "kind")
	ts := intMS(m, "timestamp_ms")
	var touch map[string]any
	var sender, upstream, envKind string
	var transitions []Transition
	if kind == KindReaction {
		emoji := str(m, "emoji")
		removed := boolVal(m, "removed")
		if emoji == "" && !removed {
			return nil, nil // a new reaction naming nothing is dropped
		}
		touch = map[string]any{
			"kind": "reaction",
			"target": map[string]any{
				"message_id": target, "sent_ts": nil, "mentions_known": true,
			},
			"target_author": "", "emoji": emoji, "removed": removed,
		}
		sender = idForm(m["sender_fbid"])
		// add/remove/add are distinct transitions. Identity is grounded in
		// what the upstream actually carries, in priority order:
		//   1. the reaction's own timestamp (a replay reproduces the same
		//      identity and dedupes; a re-add at a new time is new);
		//   2. the delta's own iris uq_seq_id (monotonic upstream revision —
		//      a redelivered delta keeps its seq and dedupes, a real re-add
		//      gets a later one);
		//   3. nothing — content identity only, and capture's transition
		//      ledger marks unresolvable repeats as ambiguous rather than
		//      minting a guessed sequence number.
		state := "add"
		if removed {
			state = "remove"
		}
		tKey := fmt.Sprintf("%s|%s|%s|%s", room, target, sender, emoji)
		seq := intMS(m, "upstream_seq")
		switch {
		case ts > 0:
			upstream = fmt.Sprintf("react:%s:%s:%s:%s:%d", target, sender, emoji, state, ts)
		case seq > 0:
			upstream = fmt.Sprintf("react:%s:%s:%s:%s:s%d", target, sender, emoji, state, seq)
			transitions = []Transition{{Key: tKey, State: state, UpstreamSeq: seq}}
		default:
			upstream = fmt.Sprintf("react:%s:%s:%s:%s", target, sender, emoji, state)
			transitions = []Transition{{Key: tKey, State: state}}
		}
		envKind = KindReaction
	} else { // unsend → delete
		touch = map[string]any{
			"kind": "delete",
			"target": map[string]any{
				"message_id": target, "sent_ts": nil, "mentions_known": true,
			},
			"target_author": "", "emoji": "", "removed": false,
		}
		upstream = "del:" + target
		envKind = KindDelete
	}
	sentMS, quality := ts, TimeUpstream
	if ts <= 0 {
		sentMS, quality = receivedMS, TimeBridgeLocal
	}
	message := map[string]any{
		"platform": Platform, "conversation_id": room, "conversation_type": "group",
		"sender_id": sender, "sender_name": nil, "timestamp": sentMS, "body": "",
		"raw": m, "mentions": []any{}, "quote": nil, "media": []any{},
		"message_id": "", "media_blobs": []any{}, "external_files": []any{},
		"voice": false, "thread": "", "edit_of": nil,
		"touch": touch, "reaction": touch,
	}
	sourceKey := SourceKey(Platform, accountKey, room, envKind, upstream)
	return &Normalized{Env: &Envelope{
		V: 1, Platform: Platform, AccountKey: accountKey, Kind: envKind,
		SourceKey: sourceKey, EventID: EventID(sourceKey, key, keyVersion),
		ConversationID: room, ConversationType: "group",
		SenderID: sender, SenderName: nil,
		SentMS: sentMS, ReceivedMS: receivedMS, TimeQuality: quality,
		Message: message, Attachments: []Attachment{}, Trace: map[string]any{}, Receipt: nil,
	}, Transitions: transitions}, nil
}

// normalizeSummons: a `kind:"summons"` event stays its own kind. Its text is
// the comment's own words; the post it names rides as provenance in `raw`.
func normalizeSummons(m map[string]any, accountKey string, key []byte, keyVersion int, receivedMS int64) (*Normalized, error) {
	room := idForm(m["room_id"])
	messageID := str(m, "message_id")
	if room == "" || messageID == "" {
		return nil, nil
	}
	sender, _ := m["sender"].(map[string]any)
	ts := intMS(m, "received_at_ms")
	sentMS, quality := ts, TimeUpstream
	if ts <= 0 {
		sentMS, quality = receivedMS, TimeBridgeLocal
	}
	message := map[string]any{
		"platform": Platform, "conversation_id": room, "conversation_type": "group",
		"sender_id": idForm(sender["fbid"]), "sender_name": nilIfEmpty(idForm(sender["username"])),
		"timestamp": sentMS, "body": strings.TrimSpace(str(m, "text")),
		"raw": m, "mentions": []any{}, "quote": nil, "media": []any{},
		"message_id": messageID, "media_blobs": []any{}, "external_files": []any{},
		"voice": false, "thread": "", "edit_of": nil, "touch": nil, "reaction": nil,
	}
	sourceKey := SourceKey(Platform, accountKey, room, KindSummons, "summons:"+messageID)
	return &Normalized{Env: &Envelope{
		V: 1, Platform: Platform, AccountKey: accountKey, Kind: KindSummons,
		SourceKey: sourceKey, EventID: EventID(sourceKey, key, keyVersion),
		ConversationID: room, ConversationType: "group",
		SenderID: idForm(sender["fbid"]), SenderName: strOrNil(idForm(sender["username"])),
		SentMS: sentMS, ReceivedMS: receivedMS, TimeQuality: quality,
		Message: message, Attachments: []Attachment{}, Trace: map[string]any{}, Receipt: nil,
	}}, nil
}

func nilIfEmpty(s string) any {
	if s == "" {
		return nil
	}
	return s
}

func strOrNil(s string) *string {
	if s == "" {
		return nil
	}
	return &s
}

func refsList(ref map[string]any) []any {
	if ref == nil {
		return []any{}
	}
	return []any{ref}
}
