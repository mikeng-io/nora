package session

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"instagram-bridge/internal/domain"
)

var t0 = time.Date(2026, 9, 25, 12, 0, 0, 0, time.UTC)

func summons(t *testing.T, mid, media, sender string, at time.Time) domain.Summons {
	t.Helper()
	n, err := strconv.ParseUint(media, 10, 64)
	if err != nil {
		t.Fatal(err)
	}
	m, _ := domain.MediaIDOf(n)
	igid, err := domain.ParseIGID(sender)
	if err != nil {
		t.Fatal(err)
	}
	return domain.Summons{MessageID: mid, Media: m, Sender: igid, ReceivedAt: at}
}

func cid(t *testing.T, s string) domain.CommentID {
	t.Helper()
	id, err := domain.ParseCommentID(s)
	if err != nil {
		t.Fatal(err)
	}
	return id
}

// A claim written by one Store is what a FRESH Store over the same directory
// reads: the ledger survives a restart, and a summons answered before it is
// still answered after it.
func TestLedgerSurvivesARestart(t *testing.T) {
	dir := t.TempDir()
	first := NewLedger(Store{Dir: dir})
	if err := first.Update(t0, func(l *domain.CommentLedger) error {
		l.RecordSummons(summons(t, "mid.$a", "3000000000000000001", "500000000001", t0))
		l.Claim(cid(t, "17900000000000001"), "3000000000000000001", t0)
		return nil
	}); err != nil {
		t.Fatal(err)
	}

	fresh := NewLedger(Store{Dir: dir})
	var got *domain.CommentLedger
	if err := fresh.Update(t0.Add(time.Minute), func(l *domain.CommentLedger) error {
		got = l
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	if !got.SummonsSenders("3000000000000000001", t0.Add(time.Minute))["500000000001"] {
		t.Error("the summons did not survive a fresh Store")
	}
	if !got.Replied(cid(t, "17900000000000001")) {
		t.Error("the claim did not survive a fresh Store: the summons could be answered twice")
	}
	r := got.Replies["17900000000000001"]
	if r.State != domain.ReplyClaimed || !r.ClaimedAt.Equal(t0) {
		t.Errorf("reply = %+v, want claimed at %v", r, t0)
	}
}

// fn's error writes nothing and comes back unchanged.
func TestLedgerRefusalWritesNothing(t *testing.T) {
	dir := t.TempDir()
	l := NewLedger(Store{Dir: dir})
	refusal := errors.New("refused")
	err := l.Update(t0, func(led *domain.CommentLedger) error {
		led.Claim(cid(t, "5"), "6", t0)
		return refusal
	})
	if !errors.Is(err, refusal) {
		t.Fatalf("err = %v, want the fn's own", err)
	}
	if _, statErr := os.Stat(Store{Dir: dir}.Path(ledgerFile)); !errors.Is(statErr, os.ErrNotExist) {
		t.Errorf("a refused transaction wrote comments.json (stat err %v)", statErr)
	}
}

// A comments.json written before the 2026-09-26 re-measure carries `served`
// (the mentions GET /mentions served). It loads, its replies and latch are
// kept, and the old key authorizes nothing and is dropped on the next write.
func TestAnOldServedKeyLoadsAndIsDropped(t *testing.T) {
	dir := t.TempDir()
	old := `{"version":1,"served":{"17900000000000001":{"media_id":"10","served_at":"2026-09-25T12:00:00Z"}},` +
		`"replies":{"7":{"media_id":"10","state":"published","claimed_at":"2026-09-25T12:00:00Z","updated_at":"2026-09-25T12:00:00Z"}}}`
	if err := os.WriteFile(Store{Dir: dir}.Path(ledgerFile), []byte(old), 0o600); err != nil {
		t.Fatal(err)
	}
	var got *domain.CommentLedger
	if err := NewLedger(Store{Dir: dir}).Update(t0, func(led *domain.CommentLedger) error { got = led; return nil }); err != nil {
		t.Fatal(err)
	}
	if !got.Replied(cid(t, "7")) || len(got.Summonses) != 0 || len(got.SummonsSenders("10", t0)) != 0 {
		t.Fatalf("ledger = %+v", got)
	}
	blob, _ := os.ReadFile(Store{Dir: dir}.Path(ledgerFile))
	if strings.Contains(string(blob), `"served"`) || !strings.Contains(string(blob), `"summonses"`) {
		t.Errorf("rewritten file = %s", blob)
	}
}

// Pruning on write: summonses older than 7 days go, younger ones stay, and
// replies are NEVER pruned, however old: a pruned reply key is a summons that
// could be answered twice.
func TestLedgerPrunesSummonsesButNeverReplies(t *testing.T) {
	dir := t.TempDir()
	l := NewLedger(Store{Dir: dir})
	now := t0
	if err := l.Update(now, func(led *domain.CommentLedger) error {
		led.RecordSummons(summons(t, "1", "10", "500", now.Add(-domain.ServedWindow-time.Second))) // expired
		led.RecordSummons(summons(t, "2", "10", "500", now.Add(-domain.ServedWindow+time.Hour)))   // kept
		led.Claim(cid(t, "3"), "10", now.Add(-5*365*24*time.Hour))                                 // kept: replies are forever
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	var got *domain.CommentLedger
	_ = NewLedger(Store{Dir: dir}).Update(now, func(led *domain.CommentLedger) error { got = led; return nil })
	if _, ok := got.Summonses["1"]; ok {
		t.Error("a summons older than 7 days was kept")
	}
	if _, ok := got.Summonses["2"]; !ok {
		t.Error("a summons younger than 7 days was pruned")
	}
	if _, ok := got.Replies["3"]; !ok {
		t.Error("a five-year-old reply was pruned: that summons could now be answered again")
	}
}

// The writes-off latch survives a restart, keeps the FIRST block, and is
// cleared only by removing the field from the file.
func TestTheWritesBlockedLatchPersists(t *testing.T) {
	dir := t.TempDir()
	if err := NewLedger(Store{Dir: dir}).Update(t0, func(led *domain.CommentLedger) error {
		led.Block(domain.BlockFeedbackRequired, t0)
		led.Block(domain.BlockSpam, t0.Add(time.Hour))
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	var got *domain.CommentLedger
	_ = NewLedger(Store{Dir: dir}).Update(t0, func(led *domain.CommentLedger) error { got = led; return nil })
	if got.Blocked == nil || got.Blocked.Reason != domain.BlockFeedbackRequired || !got.Blocked.At.Equal(t0) {
		t.Fatalf("latch after a restart = %+v, want the first block", got.Blocked)
	}
	path := Store{Dir: dir}.Path(ledgerFile)
	blob, _ := os.ReadFile(path)
	if !strings.Contains(string(blob), `"writes_blocked"`) {
		t.Fatalf("the latch is not the documented field: %s", blob)
	}
	// The documented operator action: remove the field.
	var raw map[string]any
	_ = json.Unmarshal(blob, &raw)
	delete(raw, "writes_blocked")
	cleared, _ := json.Marshal(raw)
	if err := os.WriteFile(path, cleared, 0o600); err != nil {
		t.Fatal(err)
	}
	_ = NewLedger(Store{Dir: dir}).Update(t0, func(led *domain.CommentLedger) error { got = led; return nil })
	if got.Blocked != nil {
		t.Errorf("latch after the field was removed = %+v, want cleared", got.Blocked)
	}
}

// writeAtomic syncs the file before the rename and the directory after it.
func TestWriteAtomicSyncsTheFileAndTheDirectory(t *testing.T) {
	var order []string
	prevFile, prevDir := syncFile, syncDir
	t.Cleanup(func() { syncFile, syncDir = prevFile, prevDir })
	dir := t.TempDir()
	path := filepath.Join(dir, "x.json")
	syncFile = func(f *os.File) error {
		if _, err := os.Stat(path); err == nil {
			order = append(order, "file-after-rename")
		} else {
			order = append(order, "file")
		}
		return f.Sync()
	}
	syncDir = func(d string) error {
		if d != dir {
			t.Errorf("synced %q, want the file's directory", d)
		}
		if _, err := os.Stat(path); err != nil {
			order = append(order, "dir-before-rename")
		} else {
			order = append(order, "dir")
		}
		return prevDir(d)
	}
	if err := writeAtomic(path, []byte("{}")); err != nil {
		t.Fatal(err)
	}
	if strings.Join(order, ",") != "file,dir" {
		t.Errorf("sync order = %v, want the file before the rename and the directory after", order)
	}
	// A failed sync is a failed write: nothing is published.
	syncFile = func(*os.File) error { return errors.New("disk said no") }
	if err := writeAtomic(filepath.Join(dir, "y.json"), []byte("{}")); err == nil {
		t.Error("a failed fsync was reported as a successful write")
	}
	if _, err := os.Stat(filepath.Join(dir, "y.json")); !errors.Is(err, os.ErrNotExist) {
		t.Error("a file whose fsync failed was published anyway")
	}
}

// A corrupt, unknown-version or unknown-state file is NOT an empty ledger:
// every transaction is refused as ErrLedgerUnavailable, fn never runs, and
// the file is left exactly as it was.
func TestLedgerFailsClosedOnAnUnreadableFile(t *testing.T) {
	for name, content := range map[string]string{
		"not json":        `{"version":1,"served":`,
		"unknown version": `{"version":2,"served":{},"replies":{}}`,
		"unknown state":   `{"version":1,"served":{},"replies":{"5":{"media_id":"6","state":"posted"}}}`,
	} {
		t.Run(name, func(t *testing.T) {
			dir := t.TempDir()
			path := Store{Dir: dir}.Path(ledgerFile)
			if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
				t.Fatal(err)
			}
			ran := false
			err := NewLedger(Store{Dir: dir}).Update(t0, func(*domain.CommentLedger) error { ran = true; return nil })
			if !errors.Is(err, domain.ErrLedgerUnavailable) {
				t.Fatalf("err = %v, want ErrLedgerUnavailable", err)
			}
			if ran {
				t.Error("fn ran over an unreadable ledger")
			}
			after, _ := os.ReadFile(path)
			if string(after) != content {
				t.Error("the unreadable ledger was overwritten")
			}
			if err.Error() == "" || strings.Contains(err.Error(), dir) {
				t.Errorf("error text %q names the path", err.Error())
			}
		})
	}
}

// One mutex: concurrent transactions each see the previous one's write, so
// N increments land as N claims, none lost to an interleaved read-modify-write.
func TestLedgerSerializesTransactions(t *testing.T) {
	dir := t.TempDir()
	l := NewLedger(Store{Dir: dir})
	const n = 40
	var wg sync.WaitGroup
	for i := 1; i <= n; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			id, _ := domain.ParseCommentID(strconv.Itoa(1000 + i))
			if err := l.Update(t0, func(led *domain.CommentLedger) error {
				led.Claim(id, "1", t0)
				return nil
			}); err != nil {
				t.Error(err)
			}
		}(i)
	}
	wg.Wait()
	var got *domain.CommentLedger
	_ = NewLedger(Store{Dir: dir}).Update(t0, func(led *domain.CommentLedger) error { got = led; return nil })
	if len(got.Replies) != n {
		t.Errorf("%d replies persisted, want %d: a concurrent write was lost", len(got.Replies), n)
	}
}
