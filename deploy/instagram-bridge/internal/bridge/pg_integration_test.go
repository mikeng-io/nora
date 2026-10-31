package bridge

// Real-Postgres proofs for bridge_action / bridge_work / bridge_event
// semantics. The schema DDL below is lifted verbatim from the lead's
// migration 0141_bridge_inbox (column-for-column on the surfaces the bridge
// touches) — a contradictory fake proves nothing, so these run ONLY against
// a disposable Postgres (NORA_TEST_PG_DSN).

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"sync"
	"testing"

	"github.com/jackc/pgx/v5/pgxpool"
)

const pgDDL = `
DROP TABLE IF EXISTS bridge_action, bridge_work, bridge_event_alias, bridge_event, messages, otp_challenge;
CREATE TABLE bridge_event (
  id text PRIMARY KEY,
  platform text NOT NULL,
  account_key text NOT NULL,
  source_key text NOT NULL,
  source_digest text NOT NULL,
  kind text NOT NULL,
  stream text NOT NULL,
  stream_id text NOT NULL,
  seq bigint GENERATED ALWAYS AS IDENTITY,
  conversation_id text NOT NULL,
  conversation_type text NOT NULL,
  sender_id text NOT NULL,
  received_ms bigint NOT NULL,
  queued_ms bigint NOT NULL,
  time_quality text NOT NULL,
  payload jsonb NOT NULL,
  admission_rev int,
  admission jsonb,
  tag_id text,
  state text NOT NULL DEFAULT 'open',
  outcome_reason text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (platform, account_key, source_digest),
  CHECK (state IN ('open','done','intentional_skip','failed_retained','uncertain'))
);
CREATE TABLE bridge_event_alias (
  event_id text PRIMARY KEY,
  event_ref text NOT NULL REFERENCES bridge_event(id)
);
CREATE TABLE bridge_work (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  event_id text REFERENCES bridge_event(id),
  kind text NOT NULL,
  ordinal int NOT NULL,
  platform text NOT NULL,
  account_key text NOT NULL,
  conversation_id text NOT NULL,
  due_at timestamptz NOT NULL DEFAULT now(),
  state text NOT NULL DEFAULT 'pending',
  generation int NOT NULL DEFAULT 0,
  attempts int NOT NULL DEFAULT 0,
  payload jsonb NOT NULL,
  result jsonb,
  lease_owner text,
  leased_until timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz,
  UNIQUE (event_id, kind, ordinal),
  CHECK (state IN ('pending','running','done','intentional_skip','retryable_failure','failed_retained','uncertain','cancelled'))
);
CREATE TABLE bridge_action (
  action_id text PRIMARY KEY,
  platform text NOT NULL,
  account_key text NOT NULL,
  owner_kind text NOT NULL,
  owner_id text NOT NULL,
  owner_generation int,
  op text NOT NULL,
  request jsonb NOT NULL,
  request_hash text NOT NULL,
  state text NOT NULL DEFAULT 'prepared',
  platform_ref text,
  platform_ms bigint,
  error jsonb,
  message_id bigint,
  created_at timestamptz NOT NULL DEFAULT now(),
  started_at timestamptz,
  completed_at timestamptz,
  CHECK (state IN ('prepared','started','confirmed','rejected','uncertain')),
  CHECK (owner_kind IN ('work','event_receipt','otp_challenge','live_invocation'))
);
CREATE TABLE messages (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  platform text NOT NULL,
  account_key text NOT NULL,
  conversation_id text NOT NULL,
  direction text NOT NULL,
  sender_id text NOT NULL,
  text text,
  raw_json jsonb NOT NULL,
  sent_at timestamptz NOT NULL DEFAULT now(),
  received_ms bigint,
  edited_ms bigint,
  is_nora smallint NOT NULL DEFAULT 0,
  delivery_status text NOT NULL DEFAULT 'sent',
  external_id text
);
CREATE TABLE otp_challenge (
  challenge_id uuid PRIMARY KEY,
  platform text NOT NULL,
  account_key text,
  conversation_id text NOT NULL,
  sender_id text NOT NULL,
  code_hash text NOT NULL,
  salt text NOT NULL,
  code text,
  action text NOT NULL,
  status text NOT NULL DEFAULT 'pending',
  attempts int NOT NULL DEFAULT 0,
  ttl_seconds int NOT NULL DEFAULT 300,
  issued_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL DEFAULT now() + interval '5 minutes',
  resolved_at timestamptz,
  resolved_ms bigint,
  sent_text text
);
`

func intPtr(v int) *int { return &v }

func testPG(t *testing.T) (*ActionStore, *pgxpool.Pool) {
	t.Helper()
	dsn := os.Getenv("NORA_TEST_PG_DSN")
	if dsn == "" {
		t.Skip("NORA_TEST_PG_DSN unset; no disposable Postgres")
	}
	ctx := context.Background()
	pool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := pool.Exec(ctx, pgDDL); err != nil {
		t.Fatalf("schema: %v", err)
	}
	t.Cleanup(pool.Close)
	return NewActionStorePool(pool, "instagram", "acct-1"), pool
}

// seedWork inserts a CLAIMED work row by default (running + live lease —
// the only state with action authority); pass leaseAlive=false for a dead
// lease and a state override for pending/terminal shapes.
func seedWork(t *testing.T, pool *pgxpool.Pool, state string, gen int, leaseAlive bool) {
	t.Helper()
	lease := "now() + interval '5 minutes'"
	if !leaseAlive {
		lease = "now() - interval '1 minute'"
	}
	_, err := pool.Exec(context.Background(),
		`INSERT INTO bridge_work (kind, ordinal, platform, account_key, conversation_id, state, generation,
		  payload, lease_owner, leased_until)
		 VALUES ('send', 0, 'instagram', 'acct-1', 'room-1', $1, $2, '{}', 'w', `+lease+`)`,
		state, gen)
	if err != nil {
		t.Fatal(err)
	}
}

func seedEvent(t *testing.T, pool *pgxpool.Pool, id, digest, state string) {
	t.Helper()
	_, err := pool.Exec(context.Background(),
		`INSERT INTO bridge_event (id, platform, account_key, source_key, source_digest, kind, stream, stream_id,
		  conversation_id, conversation_type, sender_id, received_ms, queued_ms, time_quality, payload, state)
		 VALUES ($1, 'instagram', 'acct-1', 'sk', $2, 'message', 'nq:in:instagram:acct-1', '1-1',
		  'room-1', 'group', 'snd', 1, 1, 'upstream', '{}', $3)`, id, digest, state)
	if err != nil {
		t.Fatal(err)
	}
}

// Concurrent Reserve on the same action_id: the INSERT...ON CONFLICT is the
// atomic row fence — one row total is created; losers read the `prepared`
// row back as ReserveExecute (the crash-resume path, by design). The
// SINGLE-EXECUTION fence is MarkStarted's prepared→started CAS: every racer
// that got ReserveExecute attempts the start, exactly one wins, and that
// one is the only upstream call. This test simulates the executor's path —
// Reserve → MarkStarted → "upstream call" — and counts the calls.
func TestPGReserveCAS(t *testing.T) {
	store, _ := testPG(t)
	ctx := context.Background()
	const racers = 8
	var upstreamCalls int32
	var mu sync.Mutex
	var wg sync.WaitGroup
	for i := 0; i < racers; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			o, _, err := store.Reserve(ctx, "w-1:send:0", OwnerWork, "w-1", intPtr(3), "send",
				[]byte(`{"a":1}`), "hash-1")
			if err != nil {
				t.Error(err)
				return
			}
			if o != ReserveExecute {
				return
			}
			started, err := store.MarkStarted(ctx, "w-1:send:0", nil)
			if err != nil {
				t.Error(err)
				return
			}
			if started {
				mu.Lock()
				upstreamCalls++
				mu.Unlock()
				_ = store.Finish(ctx, "w-1:send:0", ActionConfirmed, "ref-1", nil, "", "")
			}
		}()
	}
	wg.Wait()
	if upstreamCalls != 1 {
		t.Fatalf("upstream called %d times under concurrency, want exactly 1", upstreamCalls)
	}
	var state string
	if err := store.pool.QueryRow(ctx,
		`SELECT state FROM bridge_action WHERE action_id='w-1:send:0'`).Scan(&state); err != nil {
		t.Fatal(err)
	}
	if state != ActionConfirmed {
		t.Fatalf("final state = %q, want confirmed", state)
	}
}

// Same id + different request hash → conflict, and a FOREIGN tenant's row
// is never readable or resumable through this store.
func TestPGConflictAndTenancy(t *testing.T) {
	store, pool := testPG(t)
	ctx := context.Background()
	if _, _, err := store.Reserve(ctx, "w-1:send:0", OwnerWork, "w-1", intPtr(3), "send",
		[]byte(`{"a":1}`), "hash-1"); err != nil {
		t.Fatal(err)
	}
	o, _, err := store.Reserve(ctx, "w-1:send:0", OwnerWork, "w-1", intPtr(3), "send",
		[]byte(`{"a":2}`), "hash-2")
	if err != nil || o != ReserveConflict {
		t.Fatalf("different hash = %v/%v, want conflict", o, err)
	}
	// Same action_id under ANOTHER platform's tenancy: invisible to us,
	// and our insert on that id is a conflict (PK shared), not a takeover.
	other := NewActionStorePool(pool, "signal", "acct-9")
	if _, _, err := other.Reserve(ctx, "shared-id", OwnerWork, "w-9", intPtr(1), "send",
		[]byte(`{}`), "h-x"); err != nil {
		t.Fatal(err)
	}
	row, err := store.Get(ctx, "shared-id")
	if err != nil || row != nil {
		t.Fatalf("foreign row visible: %v/%v", row, err)
	}
	o, _, err = store.Reserve(ctx, "shared-id", OwnerWork, "w-1", intPtr(3), "send",
		[]byte(`{}`), "h-y")
	if err != nil || o != ReserveConflict {
		t.Fatalf("foreign-id reserve = %v/%v, want conflict", o, err)
	}
}

// The owner fence binds generation AND live claim AND tenancy AND target —
// only a running row with an unexpired lease, the claimed generation, this
// platform+account and the action's conversation has authority. Stale
// generation, dead lease, pending/reparked, terminal and foreign work all
// deny.
func TestPGWorkOwnerFence(t *testing.T) {
	store, pool := testPG(t)
	ctx := context.Background()
	seedWork(t, pool, "running", 7, true)
	v, err := store.ValidateOwner(ctx, OwnerWork, "w-1", intPtr(7), "room-1")
	if err != nil || !v.OK {
		t.Fatalf("claimed work+gen must validate: %v/%v", v, err)
	}
	if v, _ := store.ValidateOwner(ctx, OwnerWork, "w-1", intPtr(6), "room-1"); v.OK {
		t.Fatal("stale generation must deny")
	}
	if v, _ := store.ValidateOwner(ctx, OwnerWork, "w-1", intPtr(7), "room-2"); v.OK {
		t.Fatal("foreign target conversation must deny")
	}
	if _, err := pool.Exec(ctx, `UPDATE bridge_work SET leased_until = now() - interval '1 minute'`); err != nil {
		t.Fatal(err)
	}
	if v, _ := store.ValidateOwner(ctx, OwnerWork, "w-1", intPtr(7), "room-1"); v.OK {
		t.Fatal("expired lease must deny")
	}
	if _, err := pool.Exec(ctx, `UPDATE bridge_work SET leased_until = now() + interval '5 minutes', state='done'`); err != nil {
		t.Fatal(err)
	}
	if v, _ := store.ValidateOwner(ctx, OwnerWork, "w-1", intPtr(7), "room-1"); v.OK {
		t.Fatal("terminal work must deny")
	}
	if _, err := pool.Exec(ctx, `UPDATE bridge_work SET state='pending'`); err != nil {
		t.Fatal(err)
	}
	if v, _ := store.ValidateOwner(ctx, OwnerWork, "w-1", intPtr(7), "room-1"); v.OK {
		t.Fatal("reparked work must deny")
	}
	// Foreign tenancy: same work id in another platform's rows.
	if _, err := pool.Exec(ctx, `UPDATE bridge_work SET state='running', platform='signal'`); err != nil {
		t.Fatal(err)
	}
	if v, _ := store.ValidateOwner(ctx, OwnerWork, "w-1", intPtr(7), "room-1"); v.OK {
		t.Fatal("foreign work row must deny")
	}
}

// event_receipt owners name an event THIS bridge's journal holds with a
// frozen receipt intent — resolved by canonical digest, direct id or a live
// alias — and a terminal event row later revokes it. An id the journal
// never carried an intent for denies (no minting authority from nothing).
func TestPGReceiptOwnerScope(t *testing.T) {
	store, pool := testPG(t)
	ctx := context.Background()
	intents := map[string]bool{"digest-1": true, "v1:orig": true, "v2:rot": true}
	store.SetReceiptIntents(func(context.Context) (map[string]bool, error) { return intents, nil })
	seedEvent(t, pool, "v1:orig", "digest-1", "open")
	if v, _ := store.ValidateOwner(ctx, OwnerEventReceipt, "digest-1", nil, "room-1"); !v.OK {
		t.Fatal("digest owner must validate")
	}
	if v, _ := store.ValidateOwner(ctx, OwnerEventReceipt, "v1:orig", nil, "room-1"); !v.OK {
		t.Fatal("direct id must validate")
	}
	if v, _ := store.ValidateOwner(ctx, OwnerEventReceipt, "v2:rot", nil, "room-1"); !v.OK {
		t.Fatal("live alias must validate")
	}
	if v, _ := store.ValidateOwner(ctx, OwnerEventReceipt, "v1:http-minted", nil, "room-1"); v.OK {
		t.Fatal("an owner the journal never intented must deny")
	}
	if _, err := pool.Exec(ctx, `UPDATE bridge_event SET state='done'`); err != nil {
		t.Fatal(err)
	}
	if v, _ := store.ValidateOwner(ctx, OwnerEventReceipt, "digest-1", nil, "room-1"); v.OK {
		t.Fatal("terminal event must deny")
	}
}

// otp_challenge owners bind the real persisted challenge row plus the
// committed pending outbound audit row tagged otp-{id} — its id lands on
// the action's message_id at start. A malformed id, an expired challenge,
// a foreign platform or a missing pending row all deny.
func TestPGOTPOwnerParity(t *testing.T) {
	store, pool := testPG(t)
	ctx := context.Background()
	cid := fmt.Sprintf("%08x-%04x-%04x-%04x-%012x", 1, 2, 3, 4, 5)
	if _, err := pool.Exec(ctx,
		`INSERT INTO otp_challenge (challenge_id, platform, account_key, conversation_id,
		  sender_id, code_hash, salt, action, expires_at)
		 VALUES ($1::uuid, 'instagram', 'acct-1', 'room-1', 'snd', 'h', 's', 'send', now() + interval '5 minutes')`, cid); err != nil {
		t.Fatal(err)
	}
	if _, err := pool.Exec(ctx,
		`INSERT INTO messages (platform, account_key, conversation_id, direction, sender_id,
		  raw_json, is_nora, delivery_status)
		 VALUES ('instagram', 'acct-1', 'room-1', 'out', 'nora', '{"tag_id": "otp-`+cid+`"}', 1, 'pending')`); err != nil {
		t.Fatal(err)
	}
	v, err := store.ValidateOwner(ctx, OwnerOTPChallenge, cid, nil, "room-1")
	if err != nil || !v.OK || v.MessageID == nil {
		t.Fatalf("valid otp owner must validate with message_id: %v/%v", v, err)
	}
	if v, _ := store.ValidateOwner(ctx, OwnerOTPChallenge, "not-a-uuid", nil, "room-1"); v.OK {
		t.Fatal("malformed challenge id must deny")
	}
	if v, _ := store.ValidateOwner(ctx, OwnerOTPChallenge, cid, nil, "room-2"); v.OK {
		t.Fatal("wrong target conversation must deny")
	}
	if _, err := pool.Exec(ctx, `UPDATE otp_challenge SET expires_at = now() - interval '1 minute'`); err != nil {
		t.Fatal(err)
	}
	if v, _ := store.ValidateOwner(ctx, OwnerOTPChallenge, cid, nil, "room-1"); v.OK {
		t.Fatal("expired challenge must deny")
	}
	if _, err := pool.Exec(ctx, `UPDATE otp_challenge SET expires_at = now() + interval '5 minutes', platform='signal'`); err != nil {
		t.Fatal(err)
	}
	if v, _ := store.ValidateOwner(ctx, OwnerOTPChallenge, cid, nil, "room-1"); v.OK {
		t.Fatal("foreign-platform challenge must deny")
	}
	if _, err := pool.Exec(ctx, `UPDATE otp_challenge SET platform='instagram'`); err != nil {
		t.Fatal(err)
	}
	if _, err := pool.Exec(ctx, `UPDATE messages SET delivery_status='sent'`); err != nil {
		t.Fatal(err)
	}
	if v, _ := store.ValidateOwner(ctx, OwnerOTPChallenge, cid, nil, "room-1"); v.OK {
		t.Fatal("resolved outbound row must deny")
	}
}

// live_invocation owners name the pending outbound audit row itself,
// scoped to platform + conversation; the row id lands on message_id.
func TestPGLiveOwnerParity(t *testing.T) {
	store, pool := testPG(t)
	ctx := context.Background()
	if _, err := pool.Exec(ctx,
		`INSERT INTO messages (platform, account_key, conversation_id, direction, sender_id,
		  raw_json, is_nora, delivery_status)
		 VALUES ('instagram', 'acct-1', 'room-1', 'out', 'nora', '{}', 1, 'pending')`); err != nil {
		t.Fatal(err)
	}
	v, err := store.ValidateOwner(ctx, OwnerLiveInvocation, "1", nil, "room-1")
	if err != nil || !v.OK || v.MessageID == nil || *v.MessageID != 1 {
		t.Fatalf("valid live owner must bind message 1: %v/%v", v, err)
	}
	if v, _ := store.ValidateOwner(ctx, OwnerLiveInvocation, "app", nil, "room-1"); v.OK {
		t.Fatal("non-numeric owner id must deny")
	}
	if v, _ := store.ValidateOwner(ctx, OwnerLiveInvocation, "1", nil, "room-2"); v.OK {
		t.Fatal("foreign target must deny")
	}
	if _, err := pool.Exec(ctx, `UPDATE messages SET delivery_status='failed'`); err != nil {
		t.Fatal(err)
	}
	if v, _ := store.ValidateOwner(ctx, OwnerLiveInvocation, "1", nil, "room-1"); v.OK {
		t.Fatal("resolved row must deny")
	}
}

// The started transition persists the bound audit row on the action.
func TestPGMarkStartedCarriesMessageID(t *testing.T) {
	store, _ := testPG(t)
	ctx := context.Background()
	mid := int64(77)
	if _, _, err := store.Reserve(ctx, "w-1:send:0", OwnerWork, "w-1", intPtr(3), "send",
		[]byte(`{"a":1}`), "hash-1"); err != nil {
		t.Fatal(err)
	}
	if ok, err := store.MarkStarted(ctx, "w-1:send:0", &mid); err != nil || !ok {
		t.Fatalf("MarkStarted = %v/%v", ok, err)
	}
	row, err := store.Get(ctx, "w-1:send:0")
	if err != nil || row == nil || row.MessageID == nil || *row.MessageID != 77 {
		t.Fatalf("message_id must persist: %v/%v", row, err)
	}
}

// An otp_challenge send persists the REDACTED request while request_hash
// still covers the full body — two different codes conflict.
func TestPGOTPRequestRedacted(t *testing.T) {
	store, _ := testPG(t)
	ctx := context.Background()
	_, _, err := store.Reserve(ctx, "otp-1:send:0", OwnerOTPChallenge, fmt.Sprintf("%08x-%04x-%04x-%04x-%012x", 1, 2, 3, 4, 5),
		nil, "send", []byte(`{"target":{"id":"room-1"},"text":"code 123456"}`), "hash-a")
	if err != nil {
		t.Fatal(err)
	}
	row, err := store.Get(ctx, "otp-1:send:0")
	if err != nil || row == nil {
		t.Fatal("row missing")
	}
	var stored map[string]any
	if err := json.Unmarshal(row.Request, &stored); err != nil {
		t.Fatal(err)
	}
	if stored["text"] != "[redacted]" {
		t.Fatalf("stored text must be redacted, got %v", stored["text"])
	}
	o, _, err := store.Reserve(ctx, "otp-1:send:0", OwnerOTPChallenge, fmt.Sprintf("%08x-%04x-%04x-%04x-%012x", 1, 2, 3, 4, 5),
		nil, "send", []byte(`{"target":{"id":"room-1"},"text":"code 999999"}`), "hash-b")
	if err != nil || o != ReserveConflict {
		t.Fatalf("different code under same id must conflict: %v/%v", o, err)
	}
}

// The deployed DATABASE_URL is the app's SQLAlchemy dialect string; pgx
// must see the bare scheme. Guard the normaliser with a parse check —
// ParseConfig returns an error for the un-normalised dialect URL.
func TestCanonicalDSN(t *testing.T) {
	if _, err := pgxpool.ParseConfig("postgresql+asyncpg://u:p@localhost:5432/db"); err == nil {
		t.Skip("pgx now accepts dialect schemes; canonicalDSN may be removable")
	}
	if got := canonicalDSN("postgresql+asyncpg://u:p@h:5432/db"); got != "postgresql://u:p@h:5432/db" {
		t.Fatalf("canonicalDSN dropped or mangled the URL: %q", got)
	}
	if _, err := pgxpool.ParseConfig(canonicalDSN("postgresql+asyncpg://u:p@localhost:5432/db")); err != nil {
		t.Fatalf("dialect URL must parse after normalisation: %v", err)
	}
}
