package main

// Session: what the bridge keeps on disk so a restart is a RESUME and not a
// fresh login, and the vocabulary it uses to describe an account Instagram is
// currently unhappy with.
//
// The operational premise (design/instagram-adoption/01-bridge-container.md):
// an Instagram challenge is a NORMAL operating state, not a crash. Every
// function here is written so that "the account is checkpointed" ends with the
// bridge still serving HTTP and saying so, never with a non-zero exit.

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"go.mau.fi/mautrix-meta/pkg/messagix/cookies"
	"go.mau.fi/mautrix-meta/pkg/messagix/httpclient"
	"go.mau.fi/mautrix-meta/pkg/messagix/types"
)

// sessionState is the `session` field of GET /health, and the contract
// (03-transport-contract.md) fixes these three spellings. It is deliberately
// NOT a status code: a checkpointed session still answers /health with 200 so
// the container healthcheck stays green while a human deals with the
// challenge. Crashing on a checkpoint would make an ordinary Instagram event
// look like a dead process.
type sessionState string

const (
	stateAuthenticated sessionState = "authenticated"
	stateCheckpointed  sessionState = "checkpointed"
	stateLoggedOut     sessionState = "logged_out"
)

// classifyAuthError maps an instameow error onto the contract's vocabulary.
//
// The distinction is not cosmetic. `checkpointed` means Instagram wants a
// human in a browser and the SAME cookies will work again afterwards;
// `logged_out` means the cookies are dead and a new session must be handed
// over. Collapsing them would tell the operator to do the wrong thing, and
// collapsing either into "error" would make /send retry forever against a
// wall — which is why the contract gives checkpointed its own non-retryable
// 409 rather than a 503.
func classifyAuthError(err error) sessionState {
	switch {
	case err == nil:
		return stateAuthenticated
	case errors.Is(err, httpclient.ErrChallengeRequired),
		errors.Is(err, httpclient.ErrCheckpointRequired),
		errors.Is(err, httpclient.ErrConsentRequired):
		return stateCheckpointed
	case errors.Is(err, httpclient.ErrTokenInvalidated):
		return stateLoggedOut
	default:
		// An unclassified failure is NOT reported as authenticated. Reaching
		// here means the session did something we do not have a name for, and
		// the fail-closed reading is "a human should look", not "carry on".
		return stateCheckpointed
	}
}

// --- the session directory ---------------------------------------------------
//
// Four files, each with one job, so a partial loss degrades instead of
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
//	              that never called LoadIndex (see store.loadSelf).

type store struct{ dir string }

func (s store) path(name string) string { return filepath.Join(s.dir, name) }

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
	if err := f.Close(); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

type selfIdentity struct {
	// FBID is `interop_messaging_user_fbid` — client.GetOwnFBID(). It is the
	// id a mention carries, and the ONLY one "was I tagged?" may compare
	// against. viewer.GetFBID() is a different number for the same account
	// and matches nothing (02-identity-and-policy.md, measured).
	FBID string `json:"fbid"`
	// IGID is the public Instagram numeric id — what the Phase 2 social calls
	// take. It is NOT a messaging id and must never be compared to a mention.
	IGID     string `json:"igid"`
	Username string `json:"username"`
}

func (s store) saveSelf(self selfIdentity) error {
	blob, err := json.Marshal(self)
	if err != nil {
		return err
	}
	return writeAtomic(s.path("self.json"), blob)
}

// loadSelf exists because a resumed boot never calls LoadIndex, and LoadIndex
// is the only thing that returns the viewer (hence the username). Without this
// cache /health would report an empty username for the entire life of a
// resumed process — a field that reads "we don't know who we are" when in fact
// we do.
func (s store) loadSelf() (selfIdentity, bool) {
	var self selfIdentity
	blob, err := os.ReadFile(s.path("self.json"))
	if err != nil || json.Unmarshal(blob, &self) != nil {
		return selfIdentity{}, false
	}
	return self, self.FBID != ""
}

func (s store) saveState(state json.RawMessage) error {
	return writeAtomic(s.path("state.json"), state)
}

func (s store) loadState() (json.RawMessage, bool) {
	blob, err := os.ReadFile(s.path("state.json"))
	if err != nil || len(blob) == 0 {
		return nil, false
	}
	return blob, true
}

type seqRecord struct {
	SeqID int64     `json:"seq_id"`
	TS    time.Time `json:"ts"`
}

// The sequence id lives in its own file rather than inside state.json because
// instameow's DumpState does not carry it: LoadState restores the HTTP configs
// and nothing else, so a bridge that persisted only state.json would call
// HasSeqID() and get false on every boot, do a full LoadIndex every time, and
// look to Instagram like a client that never resumes anything.
func (s store) saveSeq(seqID int64, ts time.Time) error {
	blob, err := json.Marshal(seqRecord{SeqID: seqID, TS: ts})
	if err != nil {
		return err
	}
	return writeAtomic(s.path("seq.json"), blob)
}

// clearSeq deletes the persisted sequence id.
//
// This is the DURABLE half of forceReindex. The in-memory flag stops the
// current process from resuming against a void sequence id; on its own it does
// nothing about the number still sitting in seq.json, so a restart between the
// invalidation and the next LoadIndex takes ensureSession's resume shortcut,
// skips LoadIndex entirely, and subscribes from a sequence id Instagram has
// already thrown away. Nothing errors and no message arrives — the room simply
// goes quiet until somebody notices.
//
// A missing file is success: the point is that seq.json does not hold a stale
// number afterwards, not that a delete happened.
func (s store) clearSeq() error {
	if err := os.Remove(s.path("seq.json")); err != nil && !errors.Is(err, os.ErrNotExist) {
		return err
	}
	return nil
}

func (s store) loadSeq() (int64, time.Time) {
	var rec seqRecord
	blob, err := os.ReadFile(s.path("seq.json"))
	if err != nil || json.Unmarshal(blob, &rec) != nil {
		return 0, time.Time{}
	}
	return rec.SeqID, rec.TS
}

// saveCookies writes the jar back. Instagram rotates values inside the session
// (`csrftoken`, `rur`, the routing cookie) and instameow mutates the jar in
// place as responses arrive, so the jar in memory drifts away from the file
// the operator originally supplied. Persisting it means a restart replays what
// Instagram last said rather than what a browser said days ago.
//
// Guarded on IsLoggedIn: a jar that has been emptied by a logout response must
// not be allowed to overwrite a good file, or a transient failure becomes a
// permanent one.
func (s store) saveCookies(jar *cookies.Cookies) error {
	if jar == nil || !jar.IsLoggedIn() {
		return errors.New("refusing to persist a jar that is not logged in")
	}
	blob, err := json.Marshal(jar)
	if err != nil {
		return err
	}
	return writeAtomic(s.path("cookies.json"), blob)
}

// loadCookieJar reads cookies.json, or bootstraps it from a cURL paste.
//
// The second form exists because the documented operator flow is "log in at
// instagram.com in a private window, take any graphql XHR, Copy as cURL"
// (01-bridge-container.md). Making the operator hand-extract three cookies out
// of that blob is a step that gets done wrong; letting them drop the whole
// thing in and normalizing it here is one less way to arrive at a half-session
// that fails with an opaque auth error.
func loadCookieJar(s store) (*cookies.Cookies, error) {
	// A cookies.json that is PRESENT but unusable must not shadow the cURL
	// bootstrap. Reaching the cURL path only on a read error meant an empty,
	// truncated or half-written cookies.json made the documented re-auth
	// remedy — "drop a fresh Copy as cURL into the volume" — silently do
	// nothing, forever, while /health kept reporting logged_out. So the file
	// has to be usable, not merely readable, to win: parseable AND carrying
	// the three cookies the client refuses to start without.
	jsonErr := errors.New("no cookies.json")
	if blob, readErr := os.ReadFile(s.path("cookies.json")); readErr == nil {
		jar := &cookies.Cookies{Platform: types.PlatformFromString("instagram")}
		switch {
		case json.Unmarshal(blob, jar) != nil:
			jsonErr = errors.New("cookies.json does not parse")
		case len(jar.GetMissingCookieNames()) > 0:
			jsonErr = fmt.Errorf("cookies.json is missing %v", jar.GetMissingCookieNames())
		default:
			return jar, nil
		}
	}

	jar := &cookies.Cookies{Platform: types.PlatformFromString("instagram")}
	blob, err := os.ReadFile(s.path("cookies.curl"))
	if err != nil {
		return nil, fmt.Errorf("%s, and no cookies.curl in %s", jsonErr, s.dir)
	}
	pairs, err := cookiesFromCurl(string(blob))
	if err != nil {
		return nil, err
	}
	jar.UpdateValues(pairs)
	// Normalize immediately: after this boot the cURL file is inert, so a
	// stale paste left behind in the volume cannot silently resurrect an old
	// session over the live one on some later restart.
	if err := s.saveCookies(jar); err != nil {
		return nil, fmt.Errorf("cookies.curl did not yield a usable session: %w", err)
	}
	return jar, nil
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
