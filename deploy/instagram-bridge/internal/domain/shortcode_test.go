package domain

import (
	"errors"
	"strings"
	"testing"
)

// Pairs computed OUTSIDE this code: Python's standard-library base64 running
// instaloader's exact `shortcode_to_mediaid` / `mediaid_to_shortcode`
// (instaloader/structures.py, quoted in shortcode.go's header):
//
//	code = 'A' * (12 - len(code)) + code
//	int.from_bytes(b64decode(code.encode(), b'-_'), 'big')
//
// No pair here is a real post. The ids are synthetic (round numbers in the
// range current media ids occupy, plus the alphabet's edges); no live pair
// was measured and none may be committed (AGENTS.md). So this pins the
// ALGORITHM against an independent implementation of it. Whether Instagram's
// shortcodes follow that algorithm is the live probe's question, and the
// gateway refuses a post whose code or pk disagrees with the request.
var knownPairs = []struct {
	code string
	id   uint64
}{
	{"B", 1},
	{"_", 63},
	{"BA", 64},
	{"__", 4095},
	{"CmiJBr2LAAB", 3000000000000000001},
	{"CsFaZNTtgAC", 3100000000000000002},
	{"CxorwuxQAAD", 3200000000000000003},
	{"C3L9IQOygAE", 3300000000000000004},
	{"H__________", 1<<63 - 1},
	{"P__________", 1<<64 - 1},
}

func TestShortcodeDecodesToTheIndependentlyComputedID(t *testing.T) {
	for _, p := range knownPairs {
		sc, err := ParseShortcode(p.code)
		if err != nil {
			t.Fatalf("ParseShortcode(%q): %v", p.code, err)
		}
		id, err := sc.MediaID()
		if err != nil {
			t.Fatalf("%q.MediaID(): %v", p.code, err)
		}
		if id.n != p.id {
			t.Errorf("%q decoded to %d, want %d", p.code, id.n, p.id)
		}
	}
}

func TestMediaIDEncodesToTheIndependentlyComputedShortcode(t *testing.T) {
	for _, p := range knownPairs {
		id, ok := MediaIDOf(p.id)
		if !ok {
			t.Fatalf("MediaIDOf(%d) refused", p.id)
		}
		if got := id.Shortcode().String(); got != p.code {
			t.Errorf("%d encoded to %q, want %q", p.id, got, p.code)
		}
	}
}

// Round trip both ways over a spread of ids, so a digit-order or
// alphabet-order slip anywhere in the range fails even where no pair above
// happens to sit.
func TestShortcodeRoundTripsBothWays(t *testing.T) {
	// n*7+3 from below 2^60 stays below 2^63, so the step never wraps.
	for n := uint64(1); n < 1<<60; n = n*7 + 3 {
		id, _ := MediaIDOf(n)
		sc := id.Shortcode()
		parsed, err := ParseShortcode(sc.String())
		if err != nil {
			t.Fatalf("encoding of %d (%q) does not parse: %v", n, sc, err)
		}
		back, err := parsed.MediaID()
		if err != nil || back.n != n {
			t.Fatalf("%d -> %q -> %d, %v", n, sc, back.n, err)
		}
		if strings.HasPrefix(sc.String(), "A") {
			t.Errorf("%d encoded with a leading zero digit: %q", n, sc)
		}
	}
}

// The charset is the value object's whole promise. Every character outside
// base64url, and the empty string, is the caller's mistake (400) and never
// reaches the decode.
func TestShortcodeRefusesAnythingOutsideBase64URL(t *testing.T) {
	for _, s := range []string{"", "abc.def", "abc/def", "abc def", "abc+def", "abc=", "é", "abc%2F",
		strings.Repeat("a", 65)} {
		if sc, err := ParseShortcode(s); err == nil {
			t.Errorf("ParseShortcode(%q) = %v, want a refusal", s, sc)
		} else if !errors.Is(err, ErrInvalidID) {
			t.Errorf("ParseShortcode(%q) error %v is not ErrInvalidID", s, err)
		}
	}
	if _, err := ParseShortcode(strings.Repeat("a", 64)); err != nil {
		t.Errorf("a 64-character shortcode was refused: %v", err)
	}
}

// The refusal does not quote what it refused: a shortcode names somebody's
// post, and this error reaches an envelope.
func TestShortcodeRefusalDoesNotEchoTheInput(t *testing.T) {
	const bad = "post-by-somebody.x"
	_, err := ParseShortcode(bad)
	if err == nil || strings.Contains(err.Error(), bad) {
		t.Errorf("err = %v, want a refusal that does not quote the input", err)
	}
}

// Longer than 11 characters parses (the shape is Instagram's) but does not
// decode: there is no mechanism for it, which is ErrUnsupported, not the
// caller's mistake and not a guess at which characters are the id.
func TestALongShortcodeIsUnsupportedNotInvalid(t *testing.T) {
	sc, err := ParseShortcode("CmiJBr2LAABxyz")
	if err != nil {
		t.Fatalf("a 14-character base64url shortcode did not parse: %v", err)
	}
	_, err = sc.MediaID()
	if !errors.Is(err, ErrUnsupported) || errors.Is(err, ErrInvalidID) {
		t.Errorf("err = %v, want ErrUnsupported only", err)
	}
}

// A number wider than 64 bits (instaloader's own bound) and id zero are not
// media ids.
func TestShortcodeOutsideTheIDRangeIsInvalid(t *testing.T) {
	for _, s := range []string{"QAAAAAAAAAA", "___________", "A", "AAAA"} {
		sc, err := ParseShortcode(s)
		if err != nil {
			t.Fatalf("ParseShortcode(%q): %v", s, err)
		}
		if id, err := sc.MediaID(); !errors.Is(err, ErrInvalidID) {
			t.Errorf("%q.MediaID() = %v, %v, want ErrInvalidID", s, id, err)
		}
	}
}
