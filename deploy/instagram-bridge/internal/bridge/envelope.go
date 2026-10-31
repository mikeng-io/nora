// Package bridge is the durable-capture half of the Instagram transport:
// the normalized nora-bridge-wire v1 envelope, its source identity, the
// fsynced capture journal, the Redis Streams producer, the admission
// projection reader and the action/custody facade behind /v1.
//
// The wire contract is frozen in design/platform-bridges/00-contract.md and
// mirrored field-for-field from the lead's Python reference
// (nora/platform/bridge/envelope.py). Nothing here invents a competing
// format: a divergence is a contract failure, not a style choice.
package bridge

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"strings"
)

// Envelope kinds (the contract's closed set). An Instagram "unsend" maps to
// the contract's `delete`; a summons stays its own kind.
const (
	KindMessage  = "message"
	KindEdit     = "edit"
	KindDelete   = "delete"
	KindReaction = "reaction"
	KindSummons  = "summons"
)

// TimeQuality says whose clock `sent_ms` is.
const (
	TimeUpstream    = "upstream"
	TimeBridgeLocal = "bridge_local"
)

// separator joins a canonical source key: U+001F, chosen because it cannot
// appear inside any segment's charset.
const separator = "\x1f"

// SourceKey is the canonical source identity: platform, account, room, kind
// and the upstream transition identity, in that order.
func SourceKey(platform, accountKey, conversationID, kind, upstreamIdentity string) string {
	return strings.Join([]string{platform, accountKey, conversationID, kind, upstreamIdentity}, separator)
}

// CanonicalDigest is the DB-level uniqueness key: sha256 of the source key,
// deliberately UNKEYED so it survives correlation-key rotation.
func CanonicalDigest(sourceKey string) string {
	sum := sha256.Sum256([]byte(sourceKey))
	return hex.EncodeToString(sum[:])
}

// EventID is the wire-level event identity: `v{keyVersion}:{hmac}` — an
// opaque, unguessable alias of the source key. Rotation mints a new alias;
// the canonical digest keeps pointing at the same event.
func EventID(sourceKey string, key []byte, keyVersion int) string {
	mac := hmac.New(sha256.New, key)
	mac.Write([]byte(sourceKey))
	return fmt.Sprintf("v%d:%s", keyVersion, hex.EncodeToString(mac.Sum(nil)))
}

// CorrelationKey decodes the hex HMAC key of NORA_BRIDGE_CORRELATION_KEY{,_PREV}.
func CorrelationKey(hexKey string) ([]byte, error) {
	key, err := hex.DecodeString(strings.TrimSpace(hexKey))
	if err != nil || len(key) == 0 {
		return nil, fmt.Errorf("correlation key must be non-empty hex")
	}
	return key, nil
}

// Attachment is one bridge-custody handle named on the envelope. The key is
// opaque and join-resolves `message.media[i]` to a durable object.
type Attachment struct {
	Key       string `json:"key"`
	MediaType string `json:"media_type"`
	FileName  string `json:"file_name,omitempty"`
	Size      int64  `json:"size"`
}

// Envelope is the nora-bridge-wire v1 event. Field names are the contract's
// and match the lead's `to_fields()` one for one.
type Envelope struct {
	V                int            `json:"v"`
	Platform         string         `json:"platform"`
	AccountKey       string         `json:"account_key"`
	Kind             string         `json:"kind"`
	SourceKey        string         `json:"source_key"`
	EventID          string         `json:"event_id"`
	ConversationID   string         `json:"conversation_id"`
	ConversationType string         `json:"conversation_type"`
	SenderID         string         `json:"sender_id"`
	SenderName       *string        `json:"sender_name"`
	SentMS           int64          `json:"sent_ms"`
	ReceivedMS       int64          `json:"received_ms"`
	QueuedMS         int64          `json:"queued_ms"`
	TimeQuality      string         `json:"time_quality"`
	Message          map[string]any `json:"message"`
	Attachments      []Attachment   `json:"attachments"`
	Trace            map[string]any `json:"trace"`
	Receipt          map[string]any `json:"receipt"`
	// AdmissionRev is the projection revision applied at capture. Provenance
	// only; nil when capture ran without a validated projection (which the
	// upstream gate otherwise prevents).
	AdmissionRev *int `json:"admission_rev"`
}

// Marshal serializes the envelope for the stream's single `e` field.
func (e *Envelope) Marshal() ([]byte, error) { return json.Marshal(e) }
