// Package session is what the bridge keeps on disk so a restart is a RESUME
// and not a fresh login: the cookie jar, the reconnection state, the sequence
// id, and its own identity.
//
// The operational premise (design/instagram-adoption/01-bridge-container.md):
// an Instagram challenge is a NORMAL operating state, not a crash. Every
// function here is written so that "the account is checkpointed" ends with the
// bridge still serving HTTP and saying so, never with a non-zero exit.
//
// The supervisor that drives the session lives with the client it builds, in
// internal/gateway/instameow: every call it makes is an instameow call. This
// package is the part that is only files.
package session

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"instagram-bridge/internal/domain"

	"go.mau.fi/mautrix-meta/pkg/messagix/cookies"
	"go.mau.fi/mautrix-meta/pkg/messagix/types"
)

// --- the session directory ---------------------------------------------------
//
// Six files, each with one job, so a partial loss degrades instead of
// bricking the bridge:
//
//	cookies.json  the credential. Losing it means re-doing the browser login.
//	cookies.curl  a ONE-SHOT bootstrap: a raw `Copy as cURL` the operator drops
//	              in. Read only when cookies.json is absent, and immediately
//	              normalized into cookies.json.
//	state.json    DumpState()/LoadState() — the reconnection state, ~6 KB.
//	              Disposable: without it the bridge does a full LoadIndex.
//	seq.json      the iris sequence id. Disposable in the same way, but losing
//	              it means the stream resumes from "now" rather than from the
//	              last delta we saw.
//	self.json     own identity, cached so /health can answer it after a resume
//	              that never called LoadIndex (see Store.LoadSelf).
//	comments.json the summons ledger (ledger.go). NOT disposable: it is what
//	              stops a summons being answered twice, so an unreadable one
//	              refuses every comment write rather than reading as empty.

// Store is the session directory.
type Store struct{ Dir string }

// Path is the file `name` inside the session directory.
func (s Store) Path(name string) string { return filepath.Join(s.Dir, name) }

// writeAtomic is write-to-temp-then-rename, and it is load-bearing for
// cookies.json and state.json specifically: both are rewritten while the
// bridge is live, and a truncated cookie file read at the next boot is
// indistinguishable from a revoked session. Rename is atomic within a
// directory, so a reader sees the old file or the new one and never a half.
//
// The temp name is UNIQUE per call, not `path + ".tmp"`. Every caller here is
// reachable from more than one goroutine — instameow dispatches AuthError on
// an HTTP request goroutine and TypingNotification on a second socket's, the
// cookie flush has a ticker of its own, and the supervisor writes seq and self
// from a third — so a fixed temp name lets two writers interleave their
// WriteFile calls and publish a file that is half of each. On cookies.json,
// which cannot be regenerated without a human at a browser, that is the one
// unrecoverable loss in the whole session directory.
func writeAtomic(path string, data []byte) error {
	f, err := os.CreateTemp(filepath.Dir(path), "."+filepath.Base(path)+".*.tmp")
	if err != nil {
		return err
	}
	tmp := f.Name()
	// Removes the temp file on every failure path below. After a successful
	// rename it refers to a name that no longer exists and does nothing.
	defer func() { _ = os.Remove(tmp) }()
	// CreateTemp opens 0600 already; this is the assertion, not the change.
	// The session directory holds a full account credential.
	if err := f.Chmod(0o600); err != nil {
		_ = f.Close()
		return err
	}
	if _, err := f.Write(data); err != nil {
		_ = f.Close()
		return err
	}
	// The data reaches the disk BEFORE the rename publishes it: a rename of
	// an unsynced file can survive a power loss as an empty or partial file
	// under the new name. For comments.json that is a claim that vanished
	// after the upstream call it guarded went out.
	if err := syncFile(f); err != nil {
		_ = f.Close()
		return err
	}
	if err := f.Close(); err != nil {
		return err
	}
	if err := os.Rename(tmp, path); err != nil {
		return err
	}
	// And the rename itself: it lives in the directory, which is its own
	// inode with its own dirty state.
	return syncDir(filepath.Dir(path))
}

// syncFile and syncDir are variables so a test can count them; nothing else
// replaces them.
var (
	syncFile = func(f *os.File) error { return f.Sync() }
	syncDir  = func(dir string) error {
		d, err := os.Open(dir)
		if err != nil {
			return err
		}
		defer d.Close()
		return d.Sync()
	}
)

// selfRecord is self.json's shape. Its field names are the ones the file has
// always had, so a cache written before this package existed still loads.
type selfRecord struct {
	FBID     string `json:"fbid"`
	IGID     string `json:"igid"`
	Username string `json:"username"`
}

// SaveSelf caches the bridge's own identity.
func (s Store) SaveSelf(self domain.Self) error {
	blob, err := json.Marshal(selfRecord(self))
	if err != nil {
		return err
	}
	return writeAtomic(s.Path("self.json"), blob)
}

// LoadSelf exists because a resumed boot never calls LoadIndex, and LoadIndex
// is the only thing that returns the viewer (hence the username). Without this
// cache /health would report an empty username for the entire life of a
// resumed process — a field that reads "we don't know who we are" when in fact
// we do.
func (s Store) LoadSelf() (domain.Self, bool) {
	var rec selfRecord
	blob, err := os.ReadFile(s.Path("self.json"))
	if err != nil || json.Unmarshal(blob, &rec) != nil {
		return domain.Self{}, false
	}
	return domain.Self(rec), rec.FBID != ""
}

// SaveState persists instameow's reconnection state.
func (s Store) SaveState(state json.RawMessage) error {
	return writeAtomic(s.Path("state.json"), state)
}

// LoadState reads it back, if there is one.
func (s Store) LoadState() (json.RawMessage, bool) {
	blob, err := os.ReadFile(s.Path("state.json"))
	if err != nil || len(blob) == 0 {
		return nil, false
	}
	return blob, true
}

type seqRecord struct {
	SeqID int64     `json:"seq_id"`
	TS    time.Time `json:"ts"`
}

// SaveSeq persists the sequence id.
//
// It lives in its own file rather than inside state.json because instameow's
// DumpState does not carry it: LoadState restores the HTTP configs and nothing
// else, so a bridge that persisted only state.json would call HasSeqID() and
// get false on every boot, do a full LoadIndex every time, and look to
// Instagram like a client that never resumes anything.
func (s Store) SaveSeq(seqID int64, ts time.Time) error {
	blob, err := json.Marshal(seqRecord{SeqID: seqID, TS: ts})
	if err != nil {
		return err
	}
	return writeAtomic(s.Path("seq.json"), blob)
}

// ClearSeq deletes the persisted sequence id.
//
// This is the DURABLE half of the supervisor's forced reindex. The in-memory
// flag stops the current process from resuming against a void sequence id; on
// its own it does nothing about the number still sitting in seq.json, so a
// restart between the invalidation and the next LoadIndex takes the resume
// shortcut, skips LoadIndex entirely, and subscribes from a sequence id
// Instagram has already thrown away. Nothing errors and no message arrives —
// the room simply goes quiet until somebody notices.
//
// A missing file is success: the point is that seq.json does not hold a stale
// number afterwards, not that a delete happened.
func (s Store) ClearSeq() error {
	if err := os.Remove(s.Path("seq.json")); err != nil && !errors.Is(err, os.ErrNotExist) {
		return err
	}
	return nil
}

// LoadSeq reads the sequence id, or 0 when there is none.
func (s Store) LoadSeq() (int64, time.Time) {
	var rec seqRecord
	blob, err := os.ReadFile(s.Path("seq.json"))
	if err != nil || json.Unmarshal(blob, &rec) != nil {
		return 0, time.Time{}
	}
	return rec.SeqID, rec.TS
}

// SaveCookies writes the jar back. Instagram rotates values inside the session
// (`csrftoken`, `rur`, the routing cookie) and instameow mutates the jar in
// place as responses arrive, so the jar in memory drifts away from the file
// the operator originally supplied. Persisting it means a restart replays what
// Instagram last said rather than what a browser said days ago.
//
// Guarded on IsLoggedIn: a jar that has been emptied by a logout response must
// not be allowed to overwrite a good file, or a transient failure becomes a
// permanent one.
func (s Store) SaveCookies(jar *cookies.Cookies) error {
	if jar == nil || !jar.IsLoggedIn() {
		return errors.New("refusing to persist a jar that is not logged in")
	}
	blob, err := json.Marshal(jar)
	if err != nil {
		return err
	}
	return writeAtomic(s.Path("cookies.json"), blob)
}

// LoadCookieJar reads cookies.json, or bootstraps it from a cURL paste.
//
// The second form exists because the documented operator flow is "log in at
// instagram.com in a private window, take any graphql XHR, Copy as cURL"
// (01-bridge-container.md). Making the operator hand-extract three cookies out
// of that blob is a step that gets done wrong; letting them drop the whole
// thing in and normalizing it here is one less way to arrive at a half-session
// that fails with an opaque auth error.
func LoadCookieJar(s Store) (*cookies.Cookies, error) {
	// Before any credential is read. A session that cannot be made private is
	// not read at all: the bridge keeps serving and says why.
	if err := s.Private(); err != nil {
		return nil, err
	}
	// A cookies.json that is PRESENT but unusable must not shadow the cURL
	// bootstrap. Reaching the cURL path only on a read error meant an empty,
	// truncated or half-written cookies.json made the documented re-auth
	// remedy — "drop a fresh Copy as cURL into the volume" — silently do
	// nothing, forever, while /health kept reporting logged_out. So the file
	// has to be usable, not merely readable, to win: parseable AND carrying
	// the three cookies the client refuses to start without.
	jsonErr := errors.New("no cookies.json")
	if blob, readErr := os.ReadFile(s.Path("cookies.json")); readErr == nil {
		jar := &cookies.Cookies{Platform: types.PlatformFromString("instagram")}
		switch {
		case json.Unmarshal(blob, jar) != nil:
			jsonErr = errors.New("cookies.json does not parse")
		case len(jar.GetMissingCookieNames()) > 0:
			jsonErr = fmt.Errorf("cookies.json is missing %v", jar.GetMissingCookieNames())
		default:
			// A paste left from before this version is a second plain-text copy of the
			// credential, so one that repeats the jar's session is scrubbed. A paste with a
			// different session is an operator's pending re-authentication and stays.
			if s.pasteRepeatsJar(jar) {
				if err := s.scrubBootstrap(); err != nil {
					return nil, fmt.Errorf("cookies.curl is obsolete but could not be removed: %w", err)
				}
			}
			return jar, nil
		}
	}

	jar := &cookies.Cookies{Platform: types.PlatformFromString("instagram")}
	blob, err := os.ReadFile(s.Path("cookies.curl"))
	if err != nil {
		return nil, fmt.Errorf("%s, and no cookies.curl in %s", jsonErr, s.Dir)
	}
	pairs, err := cookiesFromCurl(string(blob))
	if err != nil {
		return nil, err
	}
	jar.UpdateValues(pairs)
	// Normalize immediately: after this boot the cURL file is inert, so a
	// stale paste left behind in the volume cannot silently resurrect an old
	// session over the live one on some later restart.
	if err := s.SaveCookies(jar); err != nil {
		return nil, fmt.Errorf("cookies.curl did not yield a usable session: %w", err)
	}
	// And the paste itself goes. Once the jar is saved it is a second, plain-text
	// copy of the account credential, and nothing reads it again. A failure to
	// remove it is an error: a credential left readable on disk is not a
	// session this bridge should go on serving.
	if err := s.scrubBootstrap(); err != nil {
		return nil, fmt.Errorf("cookies.curl was used but could not be removed: %w", err)
	}
	return jar, nil
}

// pasteRepeatsJar reports whether cookies.curl exists and carries the same session
// as the jar.
func (s Store) pasteRepeatsJar(jar *cookies.Cookies) bool {
	blob, err := os.ReadFile(s.Path("cookies.curl"))
	if err != nil {
		return false
	}
	pairs, err := cookiesFromCurl(string(blob))
	if err != nil {
		return false
	}
	want := pairs[cookies.IGCookieSessionID]
	return want != "" && want == jar.Get(cookies.IGCookieSessionID)
}

// scrubBootstrap truncates and removes the one-shot cookies.curl. A missing
// file is already scrubbed.
func (s Store) scrubBootstrap() error {
	path := s.Path("cookies.curl")
	f, err := os.OpenFile(path, os.O_WRONLY, 0)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	if err := f.Truncate(0); err != nil {
		_ = f.Close()
		return err
	}
	if err := f.Sync(); err != nil {
		_ = f.Close()
		return err
	}
	if err := f.Close(); err != nil {
		return err
	}
	if err := os.Remove(path); err != nil && !errors.Is(err, os.ErrNotExist) {
		return err
	}
	return nil
}

// chmod is a variable so a test can make a permission change fail; nothing
// else replaces it.
var chmod = os.Chmod

// Private tightens the session directory to 0700 and each credential file to
// 0600, and says so when it cannot. The directory is created 0700 by the
// bridge, but a volume that was filled by a copy keeps the copy's modes, and a
// jar readable by other users on the host is the credential, readable. A
// missing file has nothing to protect and is not an error here; the read that
// follows reports it.
func (s Store) Private() error {
	if err := tighten(s.Dir, 0o700); err != nil {
		return err
	}
	for _, name := range []string{"cookies.json", "cookies.curl"} {
		if err := tighten(s.Path(name), 0o600); err != nil {
			return err
		}
	}
	return nil
}

func tighten(path string, want os.FileMode) error {
	info, err := os.Stat(path)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	if info.Mode().Perm()&^want == 0 {
		return nil
	}
	if err := chmod(path, want); err != nil {
		return fmt.Errorf("%s is readable beyond its owner and cannot be made private: %w", filepath.Base(path), err)
	}
	return nil
}

// Each alternative matches a quote to the SAME quote character. The obvious
// shorter form — one character class holding both quote styles — stops at the
// first quote of EITHER kind, and Instagram's `rur` cookie carries a literal
// double quote inside a single-quoted header. That form silently truncates the
// cookie list there, which in a real `Copy as cURL` can drop `sessionid` and
// leave the bridge reporting "missing cookies" for a paste that was complete.
var curlCookieHeader = regexp.MustCompile(`(?i)-H\s+'cookie:\s*([^']*)'` +
	`|(?i)-H\s+"cookie:\s*([^"]*)"` +
	`|(?i)-b\s+'([^']*)'` +
	`|(?i)-b\s+"([^"]*)"`)

func cookiesFromCurl(curl string) (map[cookies.MetaCookieName]string, error) {
	m := curlCookieHeader.FindStringSubmatch(curl)
	if m == nil {
		return nil, errors.New("no cookie header found in cookies.curl")
	}
	var blob string
	for _, group := range m[1:] {
		if group != "" {
			blob = group
			break
		}
	}
	out := map[cookies.MetaCookieName]string{}
	for _, pair := range strings.Split(blob, ";") {
		name, value, ok := strings.Cut(strings.TrimSpace(pair), "=")
		if !ok {
			continue
		}
		out[cookies.MetaCookieName(strings.TrimSpace(name))] = strings.TrimSpace(value)
	}
	if len(out) == 0 {
		return nil, errors.New("cookie header in cookies.curl parsed to zero pairs")
	}
	return out, nil
}
