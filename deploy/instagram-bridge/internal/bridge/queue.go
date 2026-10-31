package bridge

// The Redis Streams producer, on the shared contract:
//
//   - stream `nq:in:{platform}:{account_key}`, single field `e` = envelope JSON;
//   - source-alias key `nq:src:{stream}:{source_key}` → ORIGINAL event_id;
//   - dedupe key `nq:dedupe:{event_id}` → stream id;
//   - the enqueue is ONE Lua call: resolve the canonical source_key first,
//     then this event's alias; XADD + both SETs on a miss. The literal text is
//     duplicated verbatim from the lead's queue.py on purpose: a drifted copy
//     fails the parity test rather than agreeing with a wrong one.
//
// Rotation safety lives in the source-alias step: a replayed event under a
// NEW key version resolves its source_key to the ORIGINAL event_id and
// returns that original stream id AND the original alias — no second entry,
// and every downstream effect (receipt, action id) keys on the canonical
// event_id, not the submitted alias.
//
// Redis ids are transport positions, never event identities; nothing trims
// the stream in v1.
//
// Parity: this is the frozen nora-bridge-wire v1 script
// (sha1 1f85b82d28739797b95784c0b7c3a118007ecadd), duplicated verbatim from
// the lead's queue.py — a drifted copy fails the parity test rather than
// agreeing with a wrong one.

import (
	"context"
	"errors"
	"strings"

	"github.com/redis/go-redis/v9"
)

// enqueueLua is the lead's literal script (queue.py ENQUEUE_LUA, .strip()).
// Do not reformat: parity is by literal text.
//
// KEYS[1] = stream, KEYS[2] = src->event_id key, KEYS[3] = dedupe prefix,
// KEYS[4] = this event's dedupe key; ARGV[1] = envelope JSON, ARGV[2] = event_id.
// Returns {stream_id, canonical_event_id}: the canonical id is the ORIGINAL
// alias the entry was written under.
const enqueueLua = `local orig = redis.call('GET', KEYS[2])
if orig then
    local prior = redis.call('GET', KEYS[3] .. orig)
    if prior then return {prior, orig} end
end
local prior = redis.call('GET', KEYS[4])
if prior then
    redis.call('SET', KEYS[2], ARGV[2])
    return {prior, ARGV[2]}
end
local id = redis.call('XADD', KEYS[1], '*', 'e', ARGV[1])
redis.call('SET', KEYS[4], id)
redis.call('SET', KEYS[2], ARGV[2])
return {id, ARGV[2]}`

// EnqueueScript is exported for the parity test that pins its text.
const EnqueueScript = enqueueLua

// Queue publishes envelopes to the account's inbound stream.
type Queue struct {
	client *redis.Client
	script *redis.Script
	stream string
	dedupe string
	src    string
	group  string
}

// NewQueue binds the producer for one platform/account. url is the
// NORA_REDIS_URL dial string.
func NewQueue(url, platform, accountKey, group string) (*Queue, error) {
	opts, err := redis.ParseURL(url)
	if err != nil {
		return nil, err
	}
	return &Queue{
		client: redis.NewClient(opts),
		script: redis.NewScript(enqueueLua),
		stream: "nq:in:" + platform + ":" + accountKey,
		dedupe: "nq:dedupe:",
		src:    "nq:src:",
		group:  group,
	}, nil
}

// Close releases the client.
func (q *Queue) Close() error { return q.client.Close() }

// EnsureGroup creates the stream (MKSTREAM) and the consumer group once;
// BUSYGROUP means it already exists and is not an error.
func (q *Queue) EnsureGroup(ctx context.Context) error {
	err := q.client.XGroupCreateMkStream(ctx, q.stream, q.group, "0").Err()
	if err != nil && !strings.Contains(err.Error(), "BUSYGROUP") {
		return err
	}
	return nil
}

// Enqueue publishes the envelope under canonical-source dedupe. sourceKey is
// the envelope's canonical identity: a retry under the same or a rotated
// event_id resolves to the ORIGINAL stream id — one logical entry per
// source. The returned canonicalID is the ORIGINAL alias the entry was
// written under (the submitted event_id for a fresh entry, the original's
// alias for a rotated replay); receipts and action identities MUST key on
// it, never on the submitted alias.
func (q *Queue) Enqueue(ctx context.Context, envelopeJSON []byte, sourceKey, eventID string) (streamID, canonicalID string, err error) {
	res, err := q.script.Run(ctx, q.client,
		[]string{q.stream, q.src + q.stream + ":" + sourceKey, q.dedupe, q.dedupe + eventID},
		string(envelopeJSON), eventID).Result()
	if err != nil {
		return "", "", err
	}
	pair, ok := res.([]any)
	if !ok || len(pair) != 2 {
		return "", "", errors.New("enqueue returned a non-pair value")
	}
	id, ok1 := pair[0].(string)
	canon, ok2 := pair[1].(string)
	if !ok1 || !ok2 {
		return "", "", errors.New("enqueue returned non-string pair members")
	}
	return id, canon, nil
}

// Ping is the health probe.
func (q *Queue) Ping(ctx context.Context) error { return q.client.Ping(ctx).Err() }
