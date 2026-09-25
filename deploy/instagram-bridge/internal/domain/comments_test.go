package domain

import (
	"errors"
	"strconv"
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

func summonsOf(t *testing.T, mid, media, sender string, at time.Time) Summons {
	t.Helper()
	n, err := strconv.ParseUint(media, 10, 64)
	if err != nil {
		t.Fatal(err)
	}
	m, ok := MediaIDOf(n)
	if !ok {
		t.Fatal("media id zero")
	}
	igid, err := ParseIGID(sender)
	if err != nil {
		t.Fatal(err)
	}
	return Summons{MessageID: mid, Media: m, Sender: igid, ReceivedAt: at}
}

// The window's edges: received exactly 7 days ago is still a live summons, a
// moment later it is not, a record dated after now (a clock that went back)
// is not, and another post's summons names nobody on this one.
func TestSummonsSendersWindowEdges(t *testing.T) {
	now := time.Date(2026, 9, 25, 0, 0, 0, 0, time.UTC)
	l := NewCommentLedger()
	l.RecordSummons(summonsOf(t, "mid.$a", "9", "0500", now.Add(-ServedWindow)))
	if got := l.SummonsSenders("9", now); !got["500"] || len(got) != 1 {
		t.Errorf("exactly at the window edge: senders %v, want the canonical 500", got)
	}
	if got := l.SummonsSenders("9", now.Add(time.Nanosecond)); len(got) != 0 {
		t.Error("past the window is still a summons")
	}
	if len(l.SummonsSenders("8", now)) != 0 || len(l.SummonsSenders("", now)) != 0 {
		t.Error("another media is a summons")
	}
	l2 := NewCommentLedger()
	l2.RecordSummons(summonsOf(t, "mid.$a", "9", "500", now.Add(time.Hour)))
	if len(l2.SummonsSenders("9", now)) != 0 {
		t.Error("a summons dated after now is live")
	}
}

// The first record of a card is kept: a replay of the same card does not
// move its window or change its sender.
func TestRecordSummonsKeepsTheFirstRecord(t *testing.T) {
	now := time.Date(2026, 9, 25, 0, 0, 0, 0, time.UTC)
	l := NewCommentLedger()
	if !l.RecordSummons(summonsOf(t, "mid.$a", "9", "500", now)) {
		t.Fatal("a new card was not recorded")
	}
	if l.RecordSummons(summonsOf(t, "mid.$a", "9", "600", now.Add(time.Hour))) {
		t.Error("a replayed card was recorded again")
	}
	if got := l.Summonses["mid.$a"]; got.SenderIGID != "500" || !got.ReceivedAt.Equal(now) {
		t.Errorf("record = %+v, want the first", got)
	}
	l.Prune(now.Add(ServedWindow + time.Second))
	if len(l.Summonses) != 0 {
		t.Error("a summons older than the window survived a prune")
	}
}

// The one word-bounded @mention test.
func TestMentionsHandleIsWordBounded(t *testing.T) {
	for text, want := range map[string]bool{
		"@noraexample is this true": true,
		"is this true @NoraExample": true,
		"(@noraexample)":            true,
		"@noraexample_fan hi":       false,
		"@noraexample.x hi":         false,
		"mail@noraexample hi":       false,
		"noraexample hi":            false,
		"":                          false,
		"@nora @noraexample":        true,
	} {
		if got := MentionsHandle(text, "noraexample"); got != want {
			t.Errorf("MentionsHandle(%q) = %v, want %v", text, got, want)
		}
	}
	if MentionsHandle("@ hi", "") {
		t.Error("an empty own username matched")
	}
}
