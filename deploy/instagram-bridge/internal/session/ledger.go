package session

// comments.json: the summons ledger (design/instagram-adoption/19 §Account
// safety, rule 4), the fifth file in the session directory.
//
//	comments.json  every comment mention GET /mentions served (comment id ->
//	               media pk, first served at), every reply the bridge ever
//	               claimed (reply-to comment id -> media pk, state, times,
//	               the new comment's id once published; never pruned), and
//	               `writes_blocked`, the writes-off latch.
//
// # Clearing the latch
//
// `writes_blocked` ({"at", "reason"}) is set when Instagram answers a write
// with an action-block signal (domain.ActionBlocked) and refuses every later
// write with 403 `writes_blocked`. Nothing in the bridge clears it. An
// operator clears it by removing that one field, with the bridge STOPPED so
// no transaction rewrites the file underneath the edit:
//
//	docker compose stop instagram-bridge
//	jq 'del(.writes_blocked)' state/instagram/comments.json > /tmp/c.json \
//	  && mv /tmp/c.json state/instagram/comments.json && chmod 600 state/instagram/comments.json \
//	  && chown 1000:1000 state/instagram/comments.json   # the container runs as uid 1000
//	docker compose start instagram-bridge
//
// It is NOT disposable the way state.json and seq.json are. Losing it forgets
// which summonses were already answered, and the one thing the write path
// must never do is answer one twice. So an unreadable or corrupt file is not
// treated as empty: every write is refused (fail closed) until an operator
// looks, and the file is never overwritten while it is unreadable, so the
// evidence survives. Reads (GET /mentions and the comment read) are
// unaffected; only the recording of what they served is skipped.
//
// One mutex serializes every read-modify-write, and each write goes through
// writeAtomic, so a crash mid-write leaves the old file or the new one. The
// file is re-read on every transaction rather than cached: a fresh Store over
// the same directory (a restart) sees exactly what the last one persisted.

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"sync"
	"time"

	"instagram-bridge/internal/domain"
)

const ledgerFile = "comments.json"

// ledgerVersion is the file's schema version. A file with another version is
// refused (fail closed), never guessed at.
const ledgerVersion = 1

type ledgerRecord struct {
	Version       int                     `json:"version"`
	Served        map[string]servedRecord `json:"served"`
	Replies       map[string]replyRecord  `json:"replies"`
	WritesBlocked *blockRecord            `json:"writes_blocked,omitempty"`
}

type blockRecord struct {
	At     time.Time `json:"at"`
	Reason string    `json:"reason"`
}

type servedRecord struct {
	MediaID  string    `json:"media_id"`
	ServedAt time.Time `json:"served_at"`
}

type replyRecord struct {
	MediaID   string            `json:"media_id"`
	State     domain.ReplyState `json:"state"`
	ClaimedAt time.Time         `json:"claimed_at"`
	UpdatedAt time.Time         `json:"updated_at"`
	CommentID string            `json:"comment_id,omitempty"`
}

// Ledger is comments.json, behind one mutex.
type Ledger struct {
	store Store
	mu    sync.Mutex
}

// NewLedger is the ledger in the store's directory. It reads nothing until
// the first transaction.
func NewLedger(s Store) *Ledger { return &Ledger{store: s} }

// ledgerUnavailable is ErrLedgerUnavailable over a fixed-text cause. The OS or
// JSON error is not carried in the text: a path or a byte of the file has no
// business in an envelope.
func ledgerUnavailable(what string, cause error) error {
	return domain.Classify(domain.ErrLedgerUnavailable, &ledgerError{msg: what, cause: cause})
}

type ledgerError struct {
	msg   string
	cause error
}

func (e *ledgerError) Error() string { return e.msg }
func (e *ledgerError) Unwrap() error { return e.cause }

// Update runs fn on the ledger as it is on disk, under the one mutex. When fn
// returns nil, the ledger is pruned as of `now` and persisted atomically
// BEFORE Update returns, and Update's error is the persist's. When fn returns
// an error, nothing is written and that error is returned unchanged.
//
// A missing file is an empty ledger. An unreadable or corrupt one is
// ErrLedgerUnavailable, and fn does not run.
func (l *Ledger) Update(now time.Time, fn func(*domain.CommentLedger) error) error {
	l.mu.Lock()
	defer l.mu.Unlock()
	led, err := l.load()
	if err != nil {
		return err
	}
	if err := fn(led); err != nil {
		return err
	}
	led.Prune(now)
	return l.save(led)
}

func (l *Ledger) load() (*domain.CommentLedger, error) {
	blob, err := os.ReadFile(l.store.Path(ledgerFile))
	if errors.Is(err, os.ErrNotExist) {
		return domain.NewCommentLedger(), nil
	}
	if err != nil {
		return nil, ledgerUnavailable("comments.json cannot be read", err)
	}
	var rec ledgerRecord
	if err := json.Unmarshal(blob, &rec); err != nil {
		return nil, ledgerUnavailable("comments.json does not parse", err)
	}
	if rec.Version != ledgerVersion {
		return nil, ledgerUnavailable("comments.json has an unknown version",
			fmt.Errorf("version %d", rec.Version))
	}
	led := domain.NewCommentLedger()
	for k, s := range rec.Served {
		led.Served[k] = domain.ServedMention{MediaID: s.MediaID, ServedAt: s.ServedAt}
	}
	for k, r := range rec.Replies {
		switch r.State {
		case domain.ReplyClaimed, domain.ReplyPublished, domain.ReplyFailed:
		default:
			// A state this code does not know could be a published reply
			// under a name it has forgotten: refuse rather than read it as
			// "no reply".
			return nil, ledgerUnavailable("comments.json has a reply in an unknown state", nil)
		}
		led.Replies[k] = domain.Reply{
			MediaID: r.MediaID, State: r.State, ClaimedAt: r.ClaimedAt,
			UpdatedAt: r.UpdatedAt, CommentID: r.CommentID,
		}
	}
	if rec.WritesBlocked != nil {
		led.Blocked = &domain.WritesBlock{At: rec.WritesBlocked.At, Reason: rec.WritesBlocked.Reason}
	}
	return led, nil
}

func (l *Ledger) save(led *domain.CommentLedger) error {
	rec := ledgerRecord{
		Version: ledgerVersion,
		Served:  make(map[string]servedRecord, len(led.Served)),
		Replies: make(map[string]replyRecord, len(led.Replies)),
	}
	for k, s := range led.Served {
		rec.Served[k] = servedRecord{MediaID: s.MediaID, ServedAt: s.ServedAt.UTC()}
	}
	for k, r := range led.Replies {
		rec.Replies[k] = replyRecord{
			MediaID: r.MediaID, State: r.State, ClaimedAt: r.ClaimedAt.UTC(),
			UpdatedAt: r.UpdatedAt.UTC(), CommentID: r.CommentID,
		}
	}
	if led.Blocked != nil {
		rec.WritesBlocked = &blockRecord{At: led.Blocked.At.UTC(), Reason: led.Blocked.Reason}
	}
	blob, err := json.MarshalIndent(rec, "", "  ")
	if err != nil {
		return ledgerUnavailable("comments.json cannot be encoded", err)
	}
	if err := writeAtomic(l.store.Path(ledgerFile), blob); err != nil {
		return ledgerUnavailable("comments.json cannot be written", err)
	}
	return nil
}
