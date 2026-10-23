package session

import (
	"bytes"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"instagram-bridge/internal/domain"

	"go.mau.fi/mautrix-meta/pkg/messagix/cookies"
	"go.mau.fi/mautrix-meta/pkg/messagix/types"
)

// The documented operator flow is "log in at instagram.com in a private
// window, take any graphql XHR, Copy as cURL". Making the operator hand-extract
// three cookies out of that blob is a step that gets done wrong, so the bridge
// parses the whole thing.
func TestCookiesFromCurl(t *testing.T) {
	curl := `curl 'https://www.instagram.com/graphql/query' \
  -H 'accept: */*' \
  -H 'cookie: ig_did=ABC; rur="CLN\05479597894900"; csrftoken=CSRF123; ds_user_id=79597894900; sessionid=79597894900%3Asecret%3A28' \
  --data-raw 'x=1'`
	got, err := cookiesFromCurl(curl)
	if err != nil {
		t.Fatal(err)
	}
	for name, want := range map[cookies.MetaCookieName]string{
		"csrftoken":  "CSRF123",
		"ds_user_id": "79597894900",
		"sessionid":  "79597894900%3Asecret%3A28",
		// `rur` is a quoted-string cookie: its value legitimately contains a
		// double quote inside a single-quoted header, and the quotes are part
		// of the value that goes back to Instagram. A regex that treats both
		// quote styles as one class truncates the cookie list right here — and
		// in a real paste `sessionid` often comes AFTER rur, so the bridge
		// would report "missing cookies" for a complete session.
		"rur": `"CLN\05479597894900"`,
	} {
		if got[name] != want {
			t.Errorf("cookie %s = %q, want %q", name, got[name], want)
		}
	}
	// The three the client refuses to start without must all be present.
	jar := &cookies.Cookies{Platform: types.PlatformFromString("instagram")}
	jar.UpdateValues(got)
	if missing := jar.GetMissingCookieNames(); len(missing) > 0 {
		t.Fatalf("parsed jar is still missing %v", missing)
	}
}

func TestCookiesFromCurlRefusesRatherThanReturningAnEmptyJar(t *testing.T) {
	for _, bad := range []string{"", "curl https://example.com", "-H 'accept: */*'"} {
		if _, err := cookiesFromCurl(bad); err == nil {
			t.Errorf("input %q produced a jar", bad)
		}
	}
}

// --- the session directory ----------------------------------------------------

func TestStateAndSeqRoundTrip(t *testing.T) {
	s := Store{Dir: t.TempDir()}
	if _, ok := s.LoadState(); ok {
		t.Fatal("an empty directory reported state")
	}
	if seq, _ := s.LoadSeq(); seq != 0 {
		t.Fatalf("an empty directory reported seq %d", seq)
	}

	if err := s.SaveState(json.RawMessage(`{"Configs":{},"Timestamp":"2026-09-21T00:00:00Z"}`)); err != nil {
		t.Fatal(err)
	}
	now := time.Now().UTC().Truncate(time.Second)
	if err := s.SaveSeq(4242, now); err != nil {
		t.Fatal(err)
	}
	if blob, ok := s.LoadState(); !ok || len(blob) == 0 {
		t.Fatal("state did not come back")
	}
	seq, ts := s.LoadSeq()
	if seq != 4242 || !ts.Equal(now) {
		t.Fatalf("seq round trip gave %d at %s", seq, ts)
	}
}

// A truncated cookies.json read at the next boot is indistinguishable from a
// revoked session. writeAtomic is what stops a crash mid-write from producing
// one, so the temp file must not survive to be mistaken for anything.
func TestWriteAtomicLeavesNoTempFile(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "state.json")
	if err := writeAtomic(path, []byte("{}")); err != nil {
		t.Fatal(err)
	}
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 1 || entries[0].Name() != "state.json" {
		names := make([]string, 0, len(entries))
		for _, e := range entries {
			names = append(names, e.Name())
		}
		t.Fatalf("directory holds %v, want only state.json", names)
	}
	info, err := entries[0].Info()
	if err != nil {
		t.Fatal(err)
	}
	// The session directory holds a full account credential. 0600 is not
	// decoration: this is the file that must never be world-readable in a
	// shared volume.
	if perm := info.Mode().Perm(); perm != 0o600 {
		t.Errorf("mode = %o, want 600", perm)
	}
}

// A jar emptied by a logout response must not overwrite a good file: that
// turns a transient failure into a permanent one, and the cookie file is the
// one thing here that cannot be regenerated without a human at a browser.
func TestSaveCookiesRefusesALoggedOutJar(t *testing.T) {
	s := Store{Dir: t.TempDir()}
	empty := &cookies.Cookies{Platform: types.PlatformFromString("instagram")}
	if err := s.SaveCookies(empty); err == nil {
		t.Fatal("an empty jar was persisted")
	}
	if _, err := os.Stat(s.Path("cookies.json")); !os.IsNotExist(err) {
		t.Fatal("cookies.json was written anyway")
	}
	if err := s.SaveCookies(nil); err == nil {
		t.Fatal("a nil jar was persisted")
	}
}

// cookies.curl is a ONE-SHOT bootstrap: it is normalized into cookies.json on
// first read, so a stale paste left behind in the volume cannot resurrect an
// old session over the live one on some later restart.
func TestCurlBootstrapIsNormalizedIntoCookiesJSON(t *testing.T) {
	s := Store{Dir: t.TempDir()}
	curl := `-H 'cookie: csrftoken=CSRF123; ds_user_id=79597894900; sessionid=79597894900%3Asecret%3A28'`
	if err := os.WriteFile(s.Path("cookies.curl"), []byte(curl), 0o600); err != nil {
		t.Fatal(err)
	}
	jar, err := LoadCookieJar(s)
	if err != nil {
		t.Fatal(err)
	}
	if !jar.IsLoggedIn() {
		t.Fatal("bootstrapped jar is not logged in")
	}
	if _, err := os.Stat(s.Path("cookies.json")); err != nil {
		t.Fatalf("cookies.json was not written: %v", err)
	}

	// cookies.json now wins, even with the cURL file still present.
	if err := os.WriteFile(s.Path("cookies.curl"), []byte(`-H 'cookie: csrftoken=STALE'`), 0o600); err != nil {
		t.Fatal(err)
	}
	again, err := LoadCookieJar(s)
	if err != nil {
		t.Fatal(err)
	}
	if got := again.Get("csrftoken"); got != "CSRF123" {
		t.Fatalf("csrftoken = %q, want the normalized value, not the stale paste", got)
	}
}

func TestMissingSessionIsAnErrorNotAPanic(t *testing.T) {
	s := Store{Dir: t.TempDir()}
	if _, err := LoadCookieJar(s); err == nil {
		t.Fatal("an empty session directory produced a jar")
	}
}

func TestSelfIdentityCacheSurvivesARestart(t *testing.T) {
	s := Store{Dir: t.TempDir()}
	if _, ok := s.LoadSelf(); ok {
		t.Fatal("an empty directory reported an identity")
	}
	want := domain.Self{FBID: "18247361241432477", IGID: "79597894900", Username: "not_____nora"}
	if err := s.SaveSelf(want); err != nil {
		t.Fatal(err)
	}
	got, ok := s.LoadSelf()
	if !ok || got != want {
		t.Fatalf("self = %+v, %v", got, ok)
	}
	// An identity with no fbid is not an identity: reporting it would let
	// /health claim a self the mention comparison cannot use.
	if err := s.SaveSelf(domain.Self{Username: "x"}); err != nil {
		t.Fatal(err)
	}
	if _, ok := s.LoadSelf(); ok {
		t.Fatal("an identity with no fbid was reported as usable")
	}
}

// --- the broken-credential remedy ---------------------------------------------

// A cookies.json that is PRESENT but unusable must not shadow the cURL
// bootstrap. The old form reached the cURL path only when the READ errored, so
// an empty, truncated or half-written cookies.json made the documented re-auth
// remedy — "log in in a browser, Copy as cURL, drop it in the volume" —
// silently do nothing, forever, while /health went on reporting logged_out and
// the operator went on believing they had already fixed it.
func TestABrokenCookiesJSONDoesNotShadowTheCurlBootstrap(t *testing.T) {
	good := `-H 'cookie: csrftoken=CSRF123; ds_user_id=79597894900; sessionid=79597894900%3Asecret%3A28'`
	for name, broken := range map[string]string{
		"empty file":        "",
		"truncated json":    `{"platform":1,"values":{"csrf`,
		"not json at all":   "<html>rate limited</html>",
		"valid but partial": `{"csrftoken":"CSRF123"}`,
	} {
		s := Store{Dir: t.TempDir()}
		if err := os.WriteFile(s.Path("cookies.json"), []byte(broken), 0o600); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(s.Path("cookies.curl"), []byte(good), 0o600); err != nil {
			t.Fatal(err)
		}
		jar, err := LoadCookieJar(s)
		if err != nil {
			t.Errorf("%s: the cURL remedy did not take: %v", name, err)
			continue
		}
		if missing := jar.GetMissingCookieNames(); len(missing) > 0 {
			t.Errorf("%s: recovered jar is still missing %v", name, missing)
		}
		// And the recovery is durable: cookies.json now holds the good
		// session, so the next boot does not depend on the paste still
		// being there.
		reread, err := LoadCookieJar(Store{Dir: s.Dir})
		if err != nil || reread.Get("csrftoken") != "CSRF123" {
			t.Errorf("%s: the recovery was not normalized into cookies.json (%v)", name, err)
		}
	}
}

// The other half: a broken cookies.json with NO cURL paste beside it is still
// an error, and the error names what is actually wrong rather than claiming
// the file is absent.
func TestABrokenCookiesJSONWithNoRemedyIsStillAnError(t *testing.T) {
	s := Store{Dir: t.TempDir()}
	if err := os.WriteFile(s.Path("cookies.json"), []byte("{"), 0o600); err != nil {
		t.Fatal(err)
	}
	_, err := LoadCookieJar(s)
	if err == nil {
		t.Fatal("a corrupt cookies.json produced a jar")
	}
	if !strings.Contains(err.Error(), "cookies.json") {
		t.Errorf("the error does not name the broken file: %v", err)
	}
}

// --- concurrent writers -------------------------------------------------------

// writeAtomic is reachable from several goroutines at once: instameow
// dispatches AuthError on an HTTP request goroutine and TypingNotification on
// a second socket's, the cookie flush has a ticker of its own, and the
// supervisor writes seq and self from a third. With a fixed `path + ".tmp"`
// two writers interleave their WriteFile calls and rename a file that is half
// of each — on cookies.json, the one file here that cannot be regenerated
// without a human at a browser.
//
// This test fails with the fixed temp name (the file comes back as a mix of
// the two payloads) and passes with a per-call one.
func TestWriteAtomicUnderConcurrentWritersNeverPublishesAMix(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "cookies.json")
	a := []byte(strings.Repeat("a", 64<<10))
	b := []byte(strings.Repeat("b", 64<<10))

	var wg sync.WaitGroup
	for range 24 {
		for _, payload := range [][]byte{a, b} {
			wg.Add(1)
			go func() {
				defer wg.Done()
				if err := writeAtomic(path, payload); err != nil {
					t.Errorf("writeAtomic: %v", err)
				}
			}()
		}
	}
	wg.Wait()

	got, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(got, a) && !bytes.Equal(got, b) {
		t.Fatalf("the published file is neither writer's payload (%d bytes, "+
			"starts %q ends %q) — a reader would see a torn credential",
			len(got), got[:min(16, len(got))], got[max(0, len(got)-16):])
	}
	// No temp file survives to be mistaken for the real one.
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 1 || entries[0].Name() != "cookies.json" {
		names := make([]string, 0, len(entries))
		for _, e := range entries {
			names = append(names, e.Name())
		}
		t.Fatalf("directory holds %v, want only cookies.json", names)
	}
}

// clearSeq is the durable half of the resnapshot invalidation. A missing file
// is success — the point is that seq.json does not hold a stale number
// afterwards, not that a delete happened.
func TestClearSeqIsIdempotent(t *testing.T) {
	s := Store{Dir: t.TempDir()}
	if err := s.ClearSeq(); err != nil {
		t.Fatalf("clearing an absent seq.json errored: %v", err)
	}
	if err := s.SaveSeq(4242, time.Now()); err != nil {
		t.Fatal(err)
	}
	if err := s.ClearSeq(); err != nil {
		t.Fatal(err)
	}
	if seq, _ := s.LoadSeq(); seq != 0 {
		t.Fatalf("seq survived the clear: %d", seq)
	}
	if err := s.ClearSeq(); err != nil {
		t.Fatalf("a second clear errored: %v", err)
	}
}

// --- the raw bootstrap is scrubbed, and the session is private first ----------

// A cookies.curl paste is the whole account credential in plain text. Once the
// jar is saved from it, the paste is a second copy with nothing left to do: it
// is truncated and removed, so the credential is not left readable on disk.
func TestTheCurlBootstrapIsScrubbedOnceTheJarIsSaved(t *testing.T) {
	s := Store{Dir: t.TempDir()}
	curl := `-H 'cookie: csrftoken=CSRF123; ds_user_id=79597894900; sessionid=79597894900%3Asecret%3A28'`
	if err := os.WriteFile(s.Path("cookies.curl"), []byte(curl), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadCookieJar(s); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(s.Path("cookies.curl")); !os.IsNotExist(err) {
		t.Fatalf("cookies.curl survived the bootstrap: stat err = %v", err)
	}
}

// The directory and the credential files are tightened before anything is read
// from them. A directory left 0755 by a copy into the volume is readable by
// every user on the host, and a jar at 0644 is the same credential, readable.
func TestTheSessionIsMadePrivateBeforeItsCredentialIsRead(t *testing.T) {
	s := Store{Dir: t.TempDir()}
	if err := os.Chmod(s.Dir, 0o755); err != nil {
		t.Fatal(err)
	}
	curl := `-H 'cookie: csrftoken=CSRF123; ds_user_id=79597894900; sessionid=79597894900%3Asecret%3A28'`
	if err := os.WriteFile(s.Path("cookies.curl"), []byte(curl), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(s.Path("cookies.json"), []byte(`{}`), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadCookieJar(s); err != nil {
		t.Fatal(err)
	}
	if got := modeOf(t, s.Dir); got != 0o700 {
		t.Errorf("session directory mode = %o, want 0700", got)
	}
	if got := modeOf(t, s.Path("cookies.json")); got != 0o600 {
		t.Errorf("cookies.json mode = %o, want 0600", got)
	}
}

// When the session cannot be made private, the credential is not read at all.
// The bridge keeps serving and reports why: an error, not a jar.
func TestAnUnprivatizableSessionFailsClosedAndIsNotRead(t *testing.T) {
	s := Store{Dir: t.TempDir()}
	good := `-H 'cookie: csrftoken=CSRF123; ds_user_id=79597894900; sessionid=79597894900%3Asecret%3A28'`
	if err := os.WriteFile(s.Path("cookies.curl"), []byte(good), 0o644); err != nil {
		t.Fatal(err)
	}
	prev := chmod
	chmod = func(string, os.FileMode) error { return errors.New("operation not permitted") }
	t.Cleanup(func() { chmod = prev })

	jar, err := LoadCookieJar(s)
	if err == nil {
		t.Fatalf("a readable session was loaded: jar logged in = %v", jar != nil && jar.IsLoggedIn())
	}
	if _, statErr := os.Stat(s.Path("cookies.json")); !os.IsNotExist(statErr) {
		t.Fatal("the bootstrap was normalized into cookies.json on a session that was not private")
	}
}

func modeOf(t *testing.T, path string) os.FileMode {
	t.Helper()
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	return info.Mode().Perm()
}

// A session normalized by an earlier version still has its paste on disk. The jar
// is the session, so the next load removes the paste instead of leaving a second
// plain-text copy of the credential.
func TestAnObsoletePasteIsScrubbedWhenAValidJarAlreadyExists(t *testing.T) {
	s := Store{Dir: t.TempDir()}
	curl := `-H 'cookie: csrftoken=CSRF123; ds_user_id=79597894900; sessionid=79597894900%3Asecret%3A28'`
	if err := os.WriteFile(s.Path("cookies.curl"), []byte(curl), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadCookieJar(s); err != nil { // writes cookies.json and scrubs the paste
		t.Fatal(err)
	}
	if err := os.WriteFile(s.Path("cookies.curl"), []byte(curl), 0o600); err != nil { // the paste returns
		t.Fatal(err)
	}
	if _, err := LoadCookieJar(s); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(s.Path("cookies.curl")); !os.IsNotExist(err) {
		t.Fatalf("cookies.curl survived a load that used cookies.json: stat err = %v", err)
	}
}

// A paste with a DIFFERENT session than the jar is an operator re-authenticating: it is
// not scrubbed, whatever the file times say.
func TestAPasteWithAnotherSessionIsLeftAlone(t *testing.T) {
	s := Store{Dir: t.TempDir()}
	curl := `-H 'cookie: csrftoken=CSRF123; ds_user_id=79597894900; sessionid=79597894900%3Asecret%3A28'`
	if err := os.WriteFile(s.Path("cookies.curl"), []byte(curl), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadCookieJar(s); err != nil {
		t.Fatal(err)
	}
	fresh := `-H 'cookie: csrftoken=CSRF123; ds_user_id=79597894900; sessionid=79597894900%3Aother%3A28'`
	if err := os.WriteFile(s.Path("cookies.curl"), []byte(fresh), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadCookieJar(s); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(s.Path("cookies.curl")); err != nil {
		t.Fatalf("a paste with another session was removed: %v", err)
	}
}
