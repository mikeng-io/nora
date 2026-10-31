package bridge

// The capture journal: one file of `<len> <crc32> <json>\n` records, fsynced
// after every append, on the bridge-local volume. It is the durable half of
// "the bridge holds the event across app restarts": a record that has been
// fsynced survives the process; a record that has not, does not exist.
//
// Format and recovery semantics are the frozen contract's, mirrored from the
// lead's journal.py:
//
//   - probe (preflight) MUST succeed before upstream receive starts;
//   - a torn LAST record (unterminated or bad checksum) is truncated and the
//     intact prefix replayed;
//   - corruption with MORE data after it raises JournalError — later records
//     are never silently discarded;
//   - appends are capacity-bounded: a full journal is a JournalError, which
//     the capture path turns into "stop upstream", never an oldest-drop.

import (
	"bufio"
	"encoding/json"
	"errors"
	"fmt"
	"hash/crc32"
	"os"
	"strconv"
	"strings"
	"sync"
)

// JournalError is durable capture being broken: refused appends (capacity,
// fsync failure) and unrecoverable interior corruption both land here so a
// caller can tell "stop upstream" from "try again".
type JournalError struct{ msg string }

func (e *JournalError) Error() string { return e.msg }

func journalErrorf(format string, args ...any) *JournalError {
	return &JournalError{msg: fmt.Sprintf(format, args...)}
}

// DefaultMaxBytes bounds the journal. The contract calls Redis-down
// buffering bounded; 512 MiB of ~2 KB envelopes is ~250k events of runway.
const DefaultMaxBytes int64 = 512 << 20

// Journal is an append-only durable record log.
type Journal struct {
	path     string
	maxBytes int64

	mu   sync.Mutex
	size int64
}

// OpenJournal binds the file (creating it if absent) and reports the
// on-disk size appends will be charged against.
func OpenJournal(path string, maxBytes int64) (*Journal, error) {
	if maxBytes <= 0 {
		maxBytes = DefaultMaxBytes
	}
	st, err := os.Stat(path)
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return nil, err
	}
	j := &Journal{path: path, maxBytes: maxBytes}
	if st != nil {
		j.size = st.Size()
	}
	return j, nil
}

// Probe is the preflight: append one probe record, fsync, and read the file
// back. It must be called — and must succeed — before upstream receive is
// allowed on every restart. A journal that cannot take one record cannot
// take the next inbound delta, so failure here stops upstream rather than
// draining it into a broken file.
func (j *Journal) Probe() error {
	rec := []byte(`{"type":"probe"}`)
	if err := j.Append(rec); err != nil {
		return err
	}
	// Truncate the probe back out: it is a write test, not data. The journal
	// stays empty of non-data so recovery never replays a probe.
	j.mu.Lock()
	defer j.mu.Unlock()
	if err := os.Truncate(j.path, j.size-int64(len(rec)+recordOverhead(rec))); err != nil {
		return journalErrorf("journal probe rollback failed: %v", err)
	}
	j.size -= int64(len(rec) + recordOverhead(rec))
	return nil
}

// recordOverhead is "<len> <crc32> " + "\n" for a record payload.
func recordOverhead(rec []byte) int {
	// "<len>" digits + space + 8 crc chars + space + newline
	return len(strconv.Itoa(len(rec))) + 1 + 8 + 1 + 1
}

// Append writes one record, fsyncs, and refuses (JournalError) when the
// capacity bound would be crossed. Callers serialize through mu; the fsync
// is INSIDE the lock so a completed Append means durable, in order.
func (j *Journal) Append(rec json.RawMessage) error {
	j.mu.Lock()
	defer j.mu.Unlock()
	line := fmt.Sprintf("%d %08x %s\n", len(rec), crc32.ChecksumIEEE(rec), rec)
	need := int64(len(line))
	if j.size+need > j.maxBytes {
		return journalErrorf("journal capacity reached (%d > %d bytes)", j.size+need, j.maxBytes)
	}
	f, err := os.OpenFile(j.path, os.O_WRONLY|os.O_CREATE|os.O_APPEND, 0o600)
	if err != nil {
		return journalErrorf("journal open failed: %v", err)
	}
	defer f.Close()
	if _, err := f.WriteString(line); err != nil {
		return journalErrorf("journal append failed: %v", err)
	}
	if err := f.Sync(); err != nil {
		return journalErrorf("journal fsync failed: %v", err)
	}
	j.size += need
	return nil
}

// Truncate drops the journal to n bytes (used after torn-tail recovery and
// the probe rollback).
func (j *Journal) truncateLocked(n int64) error {
	if err := os.Truncate(j.path, n); err != nil {
		return journalErrorf("journal truncate failed: %v", err)
	}
	j.size = n
	return nil
}

// Recover reads every intact record. A torn tail — a last line that is
// unterminated, unparseable, or fails its checksum — is truncated to the
// intact prefix, which is then returned for replay. A bad line with more
// data after it is interior corruption and a JournalError: later records
// are never silently discarded. Returns the recovered records (may be
// empty) and whether a torn tail was repaired.
func (j *Journal) Recover() (records []json.RawMessage, repaired bool, err error) {
	j.mu.Lock()
	defer j.mu.Unlock()
	f, err := os.Open(j.path)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return nil, false, nil
		}
		return nil, false, journalErrorf("journal open failed: %v", err)
	}
	defer f.Close()

	var intact, lastLineBytes int64
	scanner := bufio.NewScanner(f)
	scanner.Buffer(make([]byte, 1<<20), 64<<20)
	lineNo := 0
	for scanner.Scan() {
		lineNo++
		line := scanner.Text()
		rec, ok := parseRecord(line)
		if !ok {
			// Distinguish tail from interior by POSITION, not by reading the
			// remainder: the scanner has already buffered past this line, so
			// a read here would lie. j.size is the exact file size (mu held).
			remaining := j.size - intact - int64(len(line)) - 1
			if remaining <= 0 {
				// Torn tail: truncate to the intact prefix and replay it.
				if err := j.truncateLocked(intact); err != nil {
					return nil, false, err
				}
				return records, true, nil
			}
			return nil, false, journalErrorf("journal interior corruption at line %d", lineNo)
		}
		records = append(records, rec)
		lastLineBytes = int64(len(line)) + 1
		intact += lastLineBytes
	}
	if err := scanner.Err(); err != nil {
		return nil, false, journalErrorf("journal read failed: %v", err)
	}
	// A file not ending in '\n' has a torn last record BY DEFINITION: the
	// format writes every record's newline before fsync, so a missing one
	// means the writer was killed mid-append — even when the truncated bytes
	// happen to scan as valid. Truncate to the prefix that excludes it.
	if j.size > 0 {
		tail := make([]byte, 1)
		if _, err := f.ReadAt(tail, j.size-1); err == nil && tail[0] != '\n' {
			if n := len(records); n > 0 {
				intact -= lastLineBytes
				records = records[:n-1]
			}
			if err := j.truncateLocked(intact); err != nil {
				return nil, false, err
			}
			return records, true, nil
		}
	}
	return records, false, nil
}

// parseRecord validates "<len> <crc32> <json>" and returns the payload.
func parseRecord(line string) (json.RawMessage, bool) {
	sp := strings.IndexByte(line, ' ')
	if sp <= 0 {
		return nil, false
	}
	wantLen, err := strconv.Atoi(line[:sp])
	if err != nil {
		return nil, false
	}
	rest := line[sp+1:]
	sp = strings.IndexByte(rest, ' ')
	if sp != 8 {
		return nil, false
	}
	crc, err := strconv.ParseUint(rest[:sp], 16, 32)
	if err != nil {
		return nil, false
	}
	payload := rest[sp+1:]
	if len(payload) != wantLen || crc32.ChecksumIEEE([]byte(payload)) != uint32(crc) {
		return nil, false
	}
	var js any
	if err := json.Unmarshal([]byte(payload), &js); err != nil {
		return nil, false
	}
	return json.RawMessage(payload), true
}
