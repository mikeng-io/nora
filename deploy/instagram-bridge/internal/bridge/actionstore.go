package bridge

// The bridge_action store in Postgres (schema owned by migration 0141).
// Bridges own reservation/result writes; the app reads the same rows for
// recovery decisions and owner fencing reads bridge_work.generation.
//
// Idempotence (frozen contract):
//   - same action_id + same request_hash → the recorded result, replayed;
//   - same action_id + different request_hash → rejected (conflict), never
//     executed;
//   - state started|uncertain on replay → the current row, NOT a retry;
//   - confirmed is reused verbatim.
// A `prepared` row the previous process never finished resumes execution —
// that is exactly the recoverable crash the table exists for.

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"strings"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

// Action owner kinds (the CHECK constraint's closed set).
const (
	OwnerWork           = "work"
	OwnerEventReceipt   = "event_receipt"
	OwnerOTPChallenge   = "otp_challenge"
	OwnerLiveInvocation = "live_invocation"
)

// Action states (the CHECK constraint's closed set).
const (
	ActionPrepared  = "prepared"
	ActionStarted   = "started"
	ActionConfirmed = "confirmed"
	ActionRejected  = "rejected"
	ActionUncertain = "uncertain"
)

// Action outcomes the API derives (unsupported is stored as state=rejected
// with error.kind="unsupported" — the table has no outcome column).
const (
	OutcomeConfirmed   = "confirmed"
	OutcomeRejected    = "rejected"
	OutcomeUnsupported = "unsupported"
	OutcomeUncertain   = "uncertain"
)

// ActionRow is one bridge_action row as the API and the app read it.
type ActionRow struct {
	ActionID        string
	OwnerKind       string
	OwnerID         string
	OwnerGeneration *int
	Op              string
	Request         json.RawMessage
	RequestHash     string
	State           string
	PlatformRef     *string
	PlatformMS      *int64
	Error           json.RawMessage // {"kind","message"} or NULL
	MessageID       *int64          // bound pending outbound audit row, if any
}

// ReserveOutcome is what Reserve decided for one action_id.
type ReserveOutcome int

const (
	ReserveExecute  ReserveOutcome = iota // fresh reservation, or resume a `prepared`
	ReserveReplay                         // same request_hash: return the row, no call
	ReserveConflict                       // same action_id, different request_hash
)

// OwnerVerdict is the authority answer: OK plus the pending outbound audit
// row the action links (`bridge_action.message_id`), or Reason when denied.
type OwnerVerdict struct {
	OK        bool
	Reason    string
	MessageID *int64
}

// ActionStore is the bridge's side of bridge_action.
type ActionStore struct {
	pool       *pgxpool.Pool
	platform   string
	accountKey string
	// receiptIntents is the injectable journal-derived set: ids whose
	// journaled envelopes carry a FROZEN receipt intent — the producer
	// evidence an event_receipt owner must name. Nil means none: an
	// event_receipt owner then denies, never fabricates.
	receiptIntents func(ctx context.Context) (map[string]bool, error)
}

// NewActionStore connects (lazily) via the DATABASE_URL-style dsn.
func NewActionStore(ctx context.Context, dsn, platform, accountKey string) (*ActionStore, error) {
	pool, err := pgxpool.New(ctx, canonicalDSN(dsn))
	if err != nil {
		return nil, err
	}
	return &ActionStore{pool: pool, platform: platform, accountKey: accountKey}, nil
}

// canonicalDSN strips a "+driver" scheme qualifier — the canonical
// DATABASE_URL arrives as the app's SQLAlchemy string
// (postgresql+asyncpg://…), and pgx speaks the wire protocol, not
// dialects: it refuses a scheme it does not know outright.
func canonicalDSN(dsn string) string {
	i := strings.Index(dsn, "://")
	if i <= 0 {
		return dsn
	}
	scheme := dsn[:i]
	if j := strings.Index(scheme, "+"); j > 0 {
		return scheme[:j] + dsn[i:]
	}
	return dsn
}

// NewActionStorePool binds an existing pool (tests).
func NewActionStorePool(pool *pgxpool.Pool, platform, accountKey string) *ActionStore {
	return &ActionStore{pool: pool, platform: platform, accountKey: accountKey}
}

// SetReceiptIntents injects the journal-derived intent set — called per
// validation so intents captured after boot count (never a stale snapshot).
func (s *ActionStore) SetReceiptIntents(fn func(ctx context.Context) (map[string]bool, error)) {
	s.receiptIntents = fn
}

// storedRequest is what bridge_action.request persists — the full request
// still rides the wire and request_hash; only an otp_challenge send's text
// is a credential that must not rest (the redaction `stored_request` makes
// in Python/JS: hash over the full request, JSONB holds the redacted form).
func storedRequest(ownerKind string, request json.RawMessage) json.RawMessage {
	if ownerKind != OwnerOTPChallenge {
		return request
	}
	var obj map[string]any
	if err := json.Unmarshal(request, &obj); err != nil {
		return request
	}
	if _, ok := obj["text"]; !ok {
		return request
	}
	obj["text"] = "[redacted]"
	out, err := json.Marshal(obj)
	if err != nil {
		return request
	}
	return out
}

// Close releases the pool.
func (s *ActionStore) Close() { s.pool.Close() }

// RequestHash is the sha256 that binds a reservation to exactly one request.
// The request is canonicalized by re-marshaling through encoding/json (map
// keys sort, so equal requests hash equal).
func RequestHash(request any) (string, []byte, error) {
	raw, err := json.Marshal(request)
	if err != nil {
		return "", nil, err
	}
	var canonical any
	if err := json.Unmarshal(raw, &canonical); err != nil {
		return "", nil, err
	}
	canon, err := json.Marshal(canonical)
	if err != nil {
		return "", nil, err
	}
	sum := sha256.Sum256(canon)
	return hex.EncodeToString(sum[:]), canon, nil
}

var errValidOwner = errors.New("owner.kind must be one of work|event_receipt|otp_challenge|live_invocation")

func validOwnerKind(k string) bool {
	switch k {
	case OwnerWork, OwnerEventReceipt, OwnerOTPChallenge, OwnerLiveInvocation:
		return true
	}
	return false
}

// Reserve inserts the row or decides replay/conflict for a resubmission —
// ONE atomic statement (INSERT … ON CONFLICT DO NOTHING), so two racing
// callers cannot both land a fresh reservation. A colliding row is then
// bound to the full identity tuple: platform + account + typed owner + op +
// request hash; ANY divergence is a conflict, never a silent resume.
// ownerGeneration is required for kind=work (the caller passes the app's
// claim; execution then fences it through ValidateOwner).
func (s *ActionStore) Reserve(ctx context.Context, actionID, ownerKind, ownerID string, ownerGeneration *int, op string, request json.RawMessage, requestHash string) (ReserveOutcome, *ActionRow, error) {
	if !validOwnerKind(ownerKind) || ownerID == "" {
		return ReserveConflict, nil, errValidOwner
	}
	tag, err := s.pool.Exec(ctx,
		`INSERT INTO bridge_action
		 (action_id, platform, account_key, owner_kind, owner_id, owner_generation, op, request, request_hash)
		 VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
		 ON CONFLICT (action_id) DO NOTHING`,
		actionID, s.platform, s.accountKey, ownerKind, ownerID, ownerGeneration, op,
		storedRequest(ownerKind, request), requestHash)
	if err != nil {
		return 0, nil, err
	}
	if tag.RowsAffected() == 1 {
		return ReserveExecute, &ActionRow{
			ActionID: actionID, OwnerKind: ownerKind, OwnerID: ownerID,
			OwnerGeneration: ownerGeneration, Op: op, Request: request,
			RequestHash: requestHash, State: ActionPrepared,
		}, nil
	}
	// The conflict could be OUR row or another tenant's: Get is scoped to
	// this platform+account, so a foreign row reads as a bare conflict.
	row, err := s.Get(ctx, actionID)
	if err != nil {
		return 0, nil, err
	}
	if row == nil {
		return ReserveConflict, nil, nil
	}
	if row.RequestHash != requestHash || row.OwnerKind != ownerKind ||
		row.OwnerID != ownerID || row.Op != op {
		return ReserveConflict, row, nil
	}
	if row.State == ActionPrepared {
		// A process died between reserve and start: resume it.
		return ReserveExecute, row, nil
	}
	return ReserveReplay, row, nil
}

// Get reads one row OF THIS TENANT (platform + account), or nil. The action
// id alone is not an identity: a row another bridge owns must not be
// readable or finishable through this store.
func (s *ActionStore) Get(ctx context.Context, actionID string) (*ActionRow, error) {
	var r ActionRow
	err := s.pool.QueryRow(ctx,
		`SELECT action_id, owner_kind, owner_id, owner_generation, op, request, request_hash,
		        state, platform_ref, platform_ms, error, message_id
		 FROM bridge_action WHERE action_id=$1 AND platform=$2 AND account_key=$3`,
		actionID, s.platform, s.accountKey).
		Scan(&r.ActionID, &r.OwnerKind, &r.OwnerID, &r.OwnerGeneration, &r.Op,
			&r.Request, &r.RequestHash, &r.State, &r.PlatformRef, &r.PlatformMS, &r.Error,
			&r.MessageID)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &r, nil
}

// MarkStarted moves prepared → started (CAS, tenant-scoped; a false return
// means the row already moved on, which a caller treats as "do not
// double-execute"). `messageID` is the pending outbound audit row the
// authority check bound — persisted on the started transition so the
// action confirms the SAME messages row, never a second one.
func (s *ActionStore) MarkStarted(ctx context.Context, actionID string, messageID *int64) (bool, error) {
	tag, err := s.pool.Exec(ctx,
		`UPDATE bridge_action SET state='started', started_at=now(), message_id=$4
		 WHERE action_id=$1 AND platform=$2 AND account_key=$3 AND state='prepared'`,
		actionID, s.platform, s.accountKey, messageID)
	return tag.RowsAffected() == 1, err
}

// Finish writes the terminal result (tenant-scoped). `uncertain` is a
// stored terminal too: the platform call may have landed and nobody may
// blind-retry it.
func (s *ActionStore) Finish(ctx context.Context, actionID, state, platformRef string, platformMS *int64, errKind, errMsg string) error {
	var errJSON any
	if errKind != "" {
		errJSON = map[string]any{"kind": errKind, "message": errMsg}
	}
	var ref any
	if platformRef != "" {
		ref = platformRef
	}
	_, err := s.pool.Exec(ctx,
		`UPDATE bridge_action SET state=$2, platform_ref=$3, platform_ms=$4,
		        error=$5, completed_at=now()
		 WHERE action_id=$1 AND platform=$6 AND account_key=$7`,
		actionID, state, ref, platformMS, errJSON, s.platform, s.accountKey)
	return err
}

// ValidateOwner binds an action owner to its ACTUAL authority reference —
// the Go port of `nora/platform/bridge/actions.py::validate_owner`, the
// shared check behind every op:
//
//   - work: a real bridge_work row FOR THIS PLATFORM+ACCOUNT whose claim is
//     CURRENT — state='running' with an unexpired leased_until on the DB
//     clock — conversation-scoped to the action's target, generation
//     required and equal to the row's. pending/retryable_failure are
//     live-but-unclaimed: a lease-dead or re-parked work must not keep
//     signing actions.
//   - event_receipt: the owner names an event THIS bridge's journal holds
//     with a frozen receipt intent (the injected intents set covers the
//     journaled id AND the canonical alias/digest), and no terminal
//     bridge_event row later revoked it.
//   - otp_challenge: a real, unexpired otp_challenge row for this platform
//     plus the committed pending outbound messages row tagged `otp-{id}` —
//     its id becomes bridge_action.message_id.
//   - live_invocation: the owner names the pending outbound messages row
//     itself, scoped to platform + conversation.
//
// A denied owner answers OK=false, never an error — store failures still
// return err so the route can answer unavailable.
func (s *ActionStore) ValidateOwner(ctx context.Context, kind, id string, generation *int, targetID string) (*OwnerVerdict, error) {
	switch kind {
	case OwnerWork:
		if generation == nil {
			return &OwnerVerdict{Reason: "work owner must declare a generation"}, nil
		}
		var (
			gen        int
			state      string
			platform   string
			accountKey string
			convID     string
			payload    json.RawMessage
			leaseLive  *bool
		)
		err := s.pool.QueryRow(ctx,
			`SELECT generation, state, platform, account_key, conversation_id,
			        payload, (leased_until > now()) AS lease_live
			 FROM bridge_work
			 WHERE (id::text=$1 OR ('w-'||id::text)=$1)
			   AND platform=$2 AND account_key=$3`,
			id, s.platform, s.accountKey).
			Scan(&gen, &state, &platform, &accountKey, &convID, &payload, &leaseLive)
		if errors.Is(err, pgx.ErrNoRows) {
			return &OwnerVerdict{Reason: "unknown work owner"}, nil
		}
		if err != nil {
			return nil, err
		}
		if convID != targetID {
			return &OwnerVerdict{Reason: "owner conversation mismatch"}, nil
		}
		if state != "running" {
			return &OwnerVerdict{Reason: "owner work is not running"}, nil
		}
		if gen != *generation {
			return &OwnerVerdict{Reason: "stale owner generation"}, nil
		}
		// The claim fence's second half: generation says WHICH claim, the
		// DB-clock lease says it is still ALIVE — a lease that lapsed before
		// the expire_leases sweep is still a dead owner.
		if leaseLive == nil || !*leaseLive {
			return &OwnerVerdict{Reason: "owner lease expired"}, nil
		}
		var p map[string]any
		if err := json.Unmarshal(payload, &p); err == nil {
			if tag, ok := p["tag_id"].(string); ok && tag != "" {
				mid, err := s.pendingOutbound(ctx, targetID, tag, nil)
				if err != nil {
					return nil, err
				}
				return &OwnerVerdict{OK: true, MessageID: mid}, nil
			}
		}
		return &OwnerVerdict{OK: true}, nil

	case OwnerEventReceipt:
		var intents map[string]bool
		if s.receiptIntents != nil {
			var err error
			intents, err = s.receiptIntents(ctx)
			if err != nil {
				return nil, err
			}
		}
		if !intents[id] {
			// Not in this bridge's journal with a frozen receipt intent —
			// an HTTP caller cannot mint receipt authority from nothing.
			return &OwnerVerdict{Reason: "no journaled receipt intent for owner"}, nil
		}
		// Later-phase revocation: an event row that already closed cannot
		// collect another receipt (canonical digest, direct id or live alias).
		var st string
		err := s.pool.QueryRow(ctx,
			`SELECT e.state FROM bridge_event e
			 LEFT JOIN bridge_event_alias a ON a.event_ref = e.id
			 WHERE (e.source_digest = $1 OR e.id = $1 OR a.event_id = $1)
			   AND e.platform = $2 AND e.account_key = $3`,
			id, s.platform, s.accountKey).Scan(&st)
		if err != nil && !errors.Is(err, pgx.ErrNoRows) {
			return nil, err
		}
		if err == nil && st != "open" {
			return &OwnerVerdict{Reason: "receipt event is terminal"}, nil
		}
		return &OwnerVerdict{OK: true}, nil

	case OwnerOTPChallenge:
		var cid string
		if _, err := parseUUID(id); err != nil {
			return &OwnerVerdict{Reason: "malformed otp challenge owner id"}, nil
		}
		err := s.pool.QueryRow(ctx,
			`SELECT challenge_id::text FROM otp_challenge
			 WHERE challenge_id = $1::uuid AND platform = $2
			   AND expires_at > now()`,
			id, s.platform).Scan(&cid)
		if errors.Is(err, pgx.ErrNoRows) {
			return &OwnerVerdict{Reason: "unknown or expired otp challenge owner"}, nil
		}
		if err != nil {
			return nil, err
		}
		mid, err := s.pendingOutbound(ctx, targetID, "otp-"+id, nil)
		if err != nil {
			return nil, err
		}
		if mid == nil {
			return &OwnerVerdict{Reason: "no pending outbound row for otp challenge"}, nil
		}
		return &OwnerVerdict{OK: true, MessageID: mid}, nil

	case OwnerLiveInvocation:
		if !isDigits(id) {
			return &OwnerVerdict{Reason: "malformed live invocation owner id"}, nil
		}
		var rowID int64
		if _, err := fmt.Sscan(id, &rowID); err != nil {
			return &OwnerVerdict{Reason: "malformed live invocation owner id"}, nil
		}
		mid, err := s.pendingOutbound(ctx, targetID, "", &rowID)
		if err != nil {
			return nil, err
		}
		if mid == nil {
			return &OwnerVerdict{Reason: "no pending outbound row for live invocation"}, nil
		}
		return &OwnerVerdict{OK: true, MessageID: mid}, nil
	}
	return &OwnerVerdict{Reason: "unknown owner kind"}, nil
}

// pendingOutbound is the committed pending outbound audit row an action
// confirms — by `otp-{challenge}`/payload tag or by direct row id — scoped
// to this platform and the action's target conversation.
func (s *ActionStore) pendingOutbound(ctx context.Context, conversationID, tagID string, rowID *int64) (*int64, error) {
	var id int64
	switch {
	case rowID != nil:
		err := s.pool.QueryRow(ctx,
			`SELECT id FROM messages
			 WHERE is_nora = 1 AND delivery_status = 'pending'
			   AND platform = $1 AND conversation_id = $2 AND id = $3`,
			s.platform, conversationID, *rowID).Scan(&id)
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, nil
		}
		if err != nil {
			return nil, err
		}
		return &id, nil
	case tagID != "":
		err := s.pool.QueryRow(ctx,
			`SELECT id FROM messages
			 WHERE is_nora = 1 AND delivery_status = 'pending'
			   AND platform = $1 AND conversation_id = $2
			   AND raw_json::jsonb ->> 'tag_id' = $3`,
			s.platform, conversationID, tagID).Scan(&id)
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, nil
		}
		if err != nil {
			return nil, err
		}
		return &id, nil
	}
	return nil, nil
}

// JournalReceiptIntents returns the per-call intent-set reader the action
// store injects: every journaled event envelope carrying a frozen `receipt`
// contributes its ids — the journaled event_id, the recomputed canonical
// alias under the CURRENT correlation key, and the unkeyed canonical
// digest — so an owner named under any of the three spellings resolves.
func JournalReceiptIntents(j *Journal, key []byte, keyVersion int) func(ctx context.Context) (map[string]bool, error) {
	return func(ctx context.Context) (map[string]bool, error) {
		records, _, err := j.Recover()
		if err != nil {
			return nil, err
		}
		intents := map[string]bool{}
		for _, raw := range records {
			var rec struct {
				Type     string    `json:"type"`
				Envelope *Envelope `json:"envelope"`
			}
			if err := json.Unmarshal(raw, &rec); err != nil ||
				rec.Type != recEvent || rec.Envelope == nil ||
				rec.Envelope.Receipt == nil {
				continue
			}
			env := rec.Envelope
			intents[env.EventID] = true
			intents[EventID(env.SourceKey, key, keyVersion)] = true
			intents[CanonicalDigest(env.SourceKey)] = true
		}
		return intents, nil
	}
}

var uuidShape = regexp.MustCompile(`^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$`)

func parseUUID(s string) (string, error) {
	if !uuidShape.MatchString(s) {
		return "", fmt.Errorf("not a uuid")
	}
	return s, nil
}

func isDigits(s string) bool {
	if s == "" {
		return false
	}
	for _, r := range s {
		if r < '0' || r > '9' {
			return false
		}
	}
	return true
}

// WorkGeneration reads bridge_work.generation for this tenant — kept for
// diagnostics; the authority check is ValidateOwner.
func (s *ActionStore) WorkGeneration(ctx context.Context, workID string) (int, bool, error) {
	var gen int
	err := s.pool.QueryRow(ctx,
		`SELECT generation FROM bridge_work
		 WHERE (id::text=$1 OR ('w-'||id::text)=$1)
		   AND platform=$2 AND account_key=$3`,
		workID, s.platform, s.accountKey).Scan(&gen)
	if errors.Is(err, pgx.ErrNoRows) {
		return 0, false, nil
	}
	if err != nil {
		return 0, false, err
	}
	return gen, true, nil
}
