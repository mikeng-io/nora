package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/rs/zerolog"
	"go.mau.fi/mautrix-meta/pkg/messagix/cookies"
	"go.mau.fi/mautrix-meta/pkg/messagix/httpclient"
	"go.mau.fi/mautrix-meta/pkg/messagix/types"
)

// The three states are not interchangeable and the difference decides what the
// operator is told to do. `checkpointed` means the SAME cookies work again
// after a human clears a challenge; `logged_out` means they are dead and a new
// browser session must be handed over. Collapsing them sends the operator to
// the wrong remedy; collapsing either into "authenticated" makes /send retry
// against a wall.
func TestAuthErrorsAreClassifiedNotCollapsed(t *testing.T) {
	cases := []struct {
		err  error
		want sessionState
	}{
		{nil, stateAuthenticated},
		{httpclient.ErrChallengeRequired, stateCheckpointed},
		{httpclient.ErrCheckpointRequired, stateCheckpointed},
		{httpclient.ErrConsentRequired, stateCheckpointed},
		{httpclient.ErrTokenInvalidated, stateLoggedOut},
		// Wrapped, because that is how they actually arrive.
		{fmt.Errorf("failed to load inbox: %w", httpclient.ErrCheckpointRequired), stateCheckpointed},
		{fmt.Errorf("mercury upload: %w", httpclient.ErrTokenInvalidated), stateLoggedOut},
		// Unclassified. FAIL CLOSED: an error we have no name for is reported
		// as needing a human, never as a healthy session.
		{errors.New("something nobody has seen before"), stateCheckpointed},
	}
	for _, c := range cases {
		if got := classifyAuthError(c.err); got != c.want {
			t.Errorf("classifyAuthError(%v) = %q, want %q", c.err, got, c.want)
		}
	}
}

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
	s := store{dir: t.TempDir()}
	if _, ok := s.loadState(); ok {
		t.Fatal("an empty directory reported state")
	}
	if seq, _ := s.loadSeq(); seq != 0 {
		t.Fatalf("an empty directory reported seq %d", seq)
	}

	if err := s.saveState(json.RawMessage(`{"Configs":{},"Timestamp":"2026-09-21T00:00:00Z"}`)); err != nil {
		t.Fatal(err)
	}
	now := time.Now().UTC().Truncate(time.Second)
	if err := s.saveSeq(4242, now); err != nil {
		t.Fatal(err)
	}
	if blob, ok := s.loadState(); !ok || len(blob) == 0 {
		t.Fatal("state did not come back")
	}
	seq, ts := s.loadSeq()
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
	s := store{dir: t.TempDir()}
	empty := &cookies.Cookies{Platform: types.PlatformFromString("instagram")}
	if err := s.saveCookies(empty); err == nil {
		t.Fatal("an empty jar was persisted")
	}
	if _, err := os.Stat(s.path("cookies.json")); !os.IsNotExist(err) {
		t.Fatal("cookies.json was written anyway")
	}
	if err := s.saveCookies(nil); err == nil {
		t.Fatal("a nil jar was persisted")
	}
}

// cookies.curl is a ONE-SHOT bootstrap: it is normalized into cookies.json on
// first read, so a stale paste left behind in the volume cannot resurrect an
// old session over the live one on some later restart.
func TestCurlBootstrapIsNormalizedIntoCookiesJSON(t *testing.T) {
	s := store{dir: t.TempDir()}
	curl := `-H 'cookie: csrftoken=CSRF123; ds_user_id=79597894900; sessionid=79597894900%3Asecret%3A28'`
	if err := os.WriteFile(s.path("cookies.curl"), []byte(curl), 0o600); err != nil {
		t.Fatal(err)
	}
	jar, err := loadCookieJar(s)
	if err != nil {
		t.Fatal(err)
	}
	if !jar.IsLoggedIn() {
		t.Fatal("bootstrapped jar is not logged in")
	}
	if _, err := os.Stat(s.path("cookies.json")); err != nil {
		t.Fatalf("cookies.json was not written: %v", err)
	}

	// cookies.json now wins, even with the cURL file still present.
	if err := os.WriteFile(s.path("cookies.curl"), []byte(`-H 'cookie: csrftoken=STALE'`), 0o600); err != nil {
		t.Fatal(err)
	}
	again, err := loadCookieJar(s)
	if err != nil {
		t.Fatal(err)
	}
	if got := again.Get("csrftoken"); got != "CSRF123" {
		t.Fatalf("csrftoken = %q, want the normalized value, not the stale paste", got)
	}
}

func TestMissingSessionIsAnErrorNotAPanic(t *testing.T) {
	s := store{dir: t.TempDir()}
	if _, err := loadCookieJar(s); err == nil {
		t.Fatal("an empty session directory produced a jar")
	}
}

func TestSelfIdentityCacheSurvivesARestart(t *testing.T) {
	s := store{dir: t.TempDir()}
	if _, ok := s.loadSelf(); ok {
		t.Fatal("an empty directory reported an identity")
	}
	want := selfIdentity{FBID: "14963523149149302", IGID: "79597894900", Username: "not_____nora"}
	if err := s.saveSelf(want); err != nil {
		t.Fatal(err)
	}
	got, ok := s.loadSelf()
	if !ok || got != want {
		t.Fatalf("self = %+v, %v", got, ok)
	}
	// An identity with no fbid is not an identity: reporting it would let
	// /health claim a self the mention comparison cannot use.
	if err := s.saveSelf(selfIdentity{Username: "x"}); err != nil {
		t.Fatal(err)
	}
	if _, ok := s.loadSelf(); ok {
		t.Fatal("an identity with no fbid was reported as usable")
	}
}

// "No credential to try" and "Instagram refused the credential we tried" are
// different states with different remedies and different retry rates. The
// first is a bootstrap step an operator is in the middle of, polled every few
// seconds; the second is an account state, backed off to five minutes because
// hammering a login endpoint while waiting for a human is how a checkpoint
// becomes a ban. Collapsing them gives one of the two the wrong rate.
func TestMissingOrPartialSessionIsABootstrapStateNotAnAuthFailure(t *testing.T) {
	cases := map[string]string{
		"nothing on disk": "",
		"partial paste":   `-H 'cookie: csrftoken=CSRF123'`,
	}
	for name, curl := range cases {
		b := &bridge{store: store{dir: t.TempDir()}, hub: newHub(), state: stateAuthenticated}
		if curl != "" {
			if err := os.WriteFile(b.store.path("cookies.curl"), []byte(curl), 0o600); err != nil {
				t.Fatal(err)
			}
		}
		err := b.ensureSession(t.Context(), zerolog.Nop())
		if !errors.Is(err, errNoSession) {
			t.Errorf("%s: ensureSession gave %v, want errNoSession", name, err)
		}
		if got := b.currentState(); got != stateLoggedOut {
			t.Errorf("%s: session reported as %q, want logged_out", name, got)
		}
		if b.currentClient() != nil {
			t.Errorf("%s: a client was built from an unusable credential", name)
		}
	}
}
