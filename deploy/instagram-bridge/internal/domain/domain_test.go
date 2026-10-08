package domain

import (
	"errors"
	"fmt"
	"strings"
	"testing"
)

// The bug these types exist for: a username sent into
// `PolarisProfilePageContentQuery`'s numeric id field, failing 4/4 inside an
// HTTP 200 (2026-09-22). With typed ids the call does not compile; the parser
// is what makes the type trustworthy, so it must refuse every handle.
func TestAUsernameDoesNotParseAsAnIGID(t *testing.T) {
	for _, s := range []string{"nka_bpumvg", "@nka_bpumvg", "miles.novak", "_qnpdwdmqvgho", "a1"} {
		if id, err := ParseIGID(s); err == nil {
			t.Errorf("ParseIGID(%q) = %v; a username parsed as an igid", s, id)
		} else if !errors.Is(err, ErrInvalidID) {
			t.Errorf("ParseIGID(%q) error %v is not ErrInvalidID", s, err)
		}
	}
	// ...and the handle itself is a Username.
	if u, err := ParseUsername("@nka_bpumvg"); err != nil || u.String() != "nka_bpumvg" {
		t.Errorf("ParseUsername(@nka_bpumvg) = %q, %v", u, err)
	}
}

// "0246967358" and "246967358" are one account. Left alone they would be two
// cache keys and two identities for one person.
func TestLeadingZerosCanonicalize(t *testing.T) {
	id, err := ParseIGID("0246967358")
	if err != nil {
		t.Fatal(err)
	}
	if id.String() != "246967358" {
		t.Errorf("ParseIGID(0246967358) = %s, want 246967358", id)
	}
	other, _ := ParseIGID("246967358")
	if id != other {
		t.Error("two spellings of one igid are not equal values")
	}
	thread, err := ParseThreadFBID("01183780976338188")
	if err != nil || thread.String() != "1183780976338188" {
		t.Errorf("ParseThreadFBID with a leading zero = %s, %v", thread, err)
	}
}

// A room key in the wrong id space is the mistake the short/long split exists
// to make impossible. The LONG id is all digits, so a naive "is it numeric"
// check waves it through; it overflows int64 and is refused.
func TestNonDecimalAndLongThreadIDsAreInvalid(t *testing.T) {
	const threadLong = "340282366841710301281152937372201004392"
	for _, bad := range []string{"", "abc", "-1", "0", "12a4", threadLong} {
		if _, err := ParseThreadFBID(bad); !errors.Is(err, ErrInvalidID) {
			t.Errorf("ParseThreadFBID(%q) = %v, want ErrInvalidID", bad, err)
		}
		if _, err := ParseIGID(bad); !errors.Is(err, ErrInvalidID) {
			t.Errorf("ParseIGID(%q) = %v, want ErrInvalidID", bad, err)
		}
	}
}

// The error text is operator-facing and already reaches response bodies; the
// types must not change it.
func TestParseErrorsKeepTheirExistingText(t *testing.T) {
	_, err := ParseIGID("nka_bpumvg")
	if got, want := err.Error(), `igid is not a decimal instagram id: "nka_bpumvg"`; got != want {
		t.Errorf("ParseIGID error = %q, want %q", got, want)
	}
	_, err = ParseThreadFBID("abc")
	if got, want := err.Error(), `room_id must be a decimal thread_fbid: "abc"`; got != want {
		t.Errorf("ParseThreadFBID error = %q, want %q", got, want)
	}
}

func TestZeroIsNoFBID(t *testing.T) {
	if _, ok := FBIDOf(0); ok {
		t.Error("FBIDOf(0) claimed an fbid; 0 is 'no fbid' on every field read")
	}
	if id, ok := FBIDOf(18247361241432477); !ok || id.String() != "18247361241432477" {
		t.Errorf("FBIDOf = %v, %v", id, ok)
	}
	if _, err := NewThreadIGID(""); !errors.Is(err, ErrInvalidID) {
		t.Error("an empty thread_igid was accepted")
	}
}

func TestUsernameRefusesWhatIsNotAHandle(t *testing.T) {
	for _, bad := range []string{"", "@", "has space", "semi;colon", "0123456789012345678901234567890"} {
		if _, err := ParseUsername(bad); !errors.Is(err, ErrInvalidID) {
			t.Errorf("ParseUsername(%q) = %v, want ErrInvalidID", bad, err)
		}
	}
}

// --- the closed error set ----------------------------------------------------

// Each kind is itself and nothing else. If two kinds matched each other, the
// HTTP layer's one-status-per-kind mapping would be decided by case order.
func TestErrorKindsAreDistinct(t *testing.T) {
	for i, a := range Kinds {
		for j, b := range Kinds {
			if (i == j) != errors.Is(a, b) {
				t.Errorf("errors.Is(%v, %v) = %v", a, b, errors.Is(a, b))
			}
		}
		if KindOf(a) != a {
			t.Errorf("KindOf(%v) = %v", a, KindOf(a))
		}
	}
	if len(Kinds) != 6 {
		t.Errorf("the closed set has %d kinds, want 6", len(Kinds))
	}
}

// Classify attaches a kind without rewriting the message, and a caller that
// matched the original sentinel still matches it.
func TestClassifyKeepsIdentityAndText(t *testing.T) {
	cause := errors.New("instagram session is not connected")
	err := Classify(ErrUnavailable, cause)
	if err.Error() != cause.Error() {
		t.Errorf("text changed: %q", err)
	}
	if !errors.Is(err, ErrUnavailable) || !errors.Is(err, cause) {
		t.Error("the classified error lost its kind or its cause")
	}
	if errors.Is(err, ErrNeedsHuman) || KindOf(err) != ErrUnavailable {
		t.Errorf("KindOf = %v", KindOf(err))
	}
	// Wrapping further keeps both.
	wrapped := fmt.Errorf("lookup: %w", err)
	if KindOf(wrapped) != ErrUnavailable || !errors.Is(wrapped, cause) {
		t.Error("a wrapped classified error lost its kind")
	}
	if Classify(ErrUnavailable, nil) != nil {
		t.Error("classifying nil produced an error")
	}
}

// An unclassified failure has NO kind. Guessing one would make a permanent
// failure look retryable, or the reverse.
func TestAnUnclassifiedErrorHasNoKind(t *testing.T) {
	if k := KindOf(errors.New("something nobody has a name for")); k != nil {
		t.Errorf("KindOf(unclassified) = %v, want nil", k)
	}
}

// ErrTooLarge is a refinement of ErrUpstreamRejected, not a seventh kind: it
// IS an upstream rejection, and one that must not read as retryable.
func TestTooLargeRefinesUpstreamRejected(t *testing.T) {
	err := Classify(ErrTooLarge, errors.New("Message Too Long"))
	if !errors.Is(err, ErrTooLarge) || !errors.Is(err, ErrUpstreamRejected) {
		t.Error("a too-large refusal is not both ErrTooLarge and ErrUpstreamRejected")
	}
	if KindOf(err) != ErrUpstreamRejected {
		t.Errorf("KindOf(too large) = %v, want ErrUpstreamRejected", KindOf(err))
	}
	if errors.Is(Classify(ErrUpstreamRejected, errors.New("x")), ErrTooLarge) {
		t.Error("every upstream rejection reads as too large")
	}
}

// A message id is opaque (Instagram documents no shape), so the parse refuses
// only what cannot be one, and its error never echoes the input, because the
// value arrives as a path segment a caller controls.
func TestMessageIDParse(t *testing.T) {
	for _, ok := range []string{"mid.$synthetic0001", "mid.$a+b/c=d_e-f", strings.Repeat("m", 256)} {
		id, err := ParseMessageID(ok)
		if err != nil || id.String() != ok {
			t.Errorf("ParseMessageID(%q) = %q, %v; want the value back", ok, id, err)
		}
	}
	for _, bad := range []string{"", " ", "mid.$a b", "mid.$a\tb", "mid.$a\x00", "mid.$é", strings.Repeat("m", 257)} {
		_, err := ParseMessageID(bad)
		if !errors.Is(err, ErrInvalidID) {
			t.Errorf("ParseMessageID(%q) = %v, want ErrInvalidID", bad, err)
			continue
		}
		// Inputs of a byte or two are substrings of any English sentence;
		// the echo check is meaningful only for the longer ones.
		if len(bad) > 2 && strings.Contains(err.Error(), bad) {
			t.Errorf("ParseMessageID(%q) echoed its input: %v", bad, err)
		}
	}
}
