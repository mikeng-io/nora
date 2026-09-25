package domain

import (
	"errors"
	"strings"
	"testing"
	"time"
)

func TestParseCommentIDCanonicalizesAndRefuses(t *testing.T) {
	id, err := ParseCommentID("0017900000000000001")
	if err != nil || id.String() != "17900000000000001" {
		t.Fatalf("got %v %v, want the canonical id", id, err)
	}
	for _, bad := range []string{"", "0", "-1", "1.5", "1e3", "abc", "17_18", " 1", "99999999999999999999", "123456789012345678901"} {
		_, err := ParseCommentID(bad)
		if !errors.Is(err, ErrInvalidID) {
			t.Errorf("%q: err = %v, want ErrInvalidID", bad, err)
		}
		if err != nil && bad != "" && strings.Contains(err.Error(), bad) && len(bad) > 1 {
			t.Errorf("%q is quoted in the error", bad)
		}
	}
}

func TestCanonicalMediaID(t *testing.T) {
	for in, want := range map[string]string{"3000000000000000001": "3000000000000000001", "03": "3", "0": "", "x": "", "3_4": ""} {
		if got := CanonicalMediaID(in); got != want {
			t.Errorf("CanonicalMediaID(%q) = %q, want %q", in, got, want)
		}
	}
}

// The window's edges: served exactly 7 days ago is still a summons, a moment
// later it is not, and a serve "in the future" (a clock that went back) is not.
func TestIsSummonsWindowEdges(t *testing.T) {
	now := time.Date(2026, 9, 25, 0, 0, 0, 0, time.UTC)
	id, _ := ParseCommentID("5")
	l := NewCommentLedger()
	l.RecordServed(id, "9", now.Add(-ServedWindow))
	if !l.IsSummons(id, "9", now) {
		t.Error("exactly at the window edge should still be a summons")
	}
	if l.IsSummons(id, "9", now.Add(time.Nanosecond)) {
		t.Error("past the window is still a summons")
	}
	if l.IsSummons(id, "8", now) || l.IsSummons(id, "", now) {
		t.Error("another media is a summons")
	}
	l2 := NewCommentLedger()
	l2.RecordServed(id, "9", now.Add(time.Hour))
	if l2.IsSummons(id, "9", now) {
		t.Error("a serve dated after now is a summons")
	}
}
