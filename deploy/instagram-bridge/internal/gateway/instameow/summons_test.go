package instameow

// The summons intake (summons.go) against the measured card shape
// (design 19, "Intake, re-measured 2026-09-26"): the live delta carries the
// comment's text and the commenter, and NO target_url; the thread refetch
// carries target_url. Every id and name below is synthetic.

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"strings"
	"sync"
	"testing"
	"time"

	"instagram-bridge/internal/domain"
	"instagram-bridge/internal/session"
	"instagram-bridge/internal/stream"

	"go.mau.fi/mautrix-meta/pkg/instameow/slidetypes"
	"go.mau.fi/mautrix-meta/pkg/messagix/httpclient"
)

const (
	cardMID        = "mid.$summonscard0001"
	cardSenderFBID = "186217604552756"
	cardSenderIGID = "500000000001"
	cardSenderName = "commenter_alpha"
	cardText       = "@not_____nora is this true 3f9c"
	cardTSMS       = int64(1790000000000)
	cardCode       = "CmiJBr2LAAB"
	cardMedia      = "3000000000000000001"
)

// liveCard is the card as the realtime delta carries it: the measured shape,
// with no target_url, target_id or CTA.
func liveCard(t *testing.T, subtitle string) *slidetypes.Message {
	t.Helper()
	return messageOf(t, `{
	  "__typename":"SlideUQPPNewMessage","thread_fbid":"`+threadShort+`",
	  "message":{"thread_fbid":"`+threadShort+`","message_id":"`+cardMID+`","sender_fbid":"`+cardSenderFBID+`",
	    "timestamp_ms":"1790000000000","content_type":"MESSAGE_INLINE_SHARE",
	    "content":{"__typename":"SlideMessageXMAContent","xma":{
	      "__typename":"SlideMessageLayeredXMA","xmaTitle":"`+cardSenderName+`","title_text":"`+cardSenderName+`",
	      "subtitle_text":`+jsonString(subtitle)+`}},
	    "sender":{"id":"`+cardSenderFBID+`","igid":"`+cardSenderIGID+`",
	      "user_dict":{"username":"`+cardSenderName+`","id":"`+cardSenderIGID+`"}}}}`)
}

func jsonString(s string) string {
	b, _ := json.Marshal(s)
	return string(b)
}

// refetched is the same message as the thread query returns it: with the
// post's permalink.
func refetched(t *testing.T, targetURL string) *slidetypes.Message {
	t.Helper()
	m := liveCard(t, cardText)
	cp := *m.Content.Content.(*slidetypes.MessageContentXMA).XMA
	cp.TargetURL = targetURL
	m.Content.Content = &slidetypes.MessageContentXMA{XMA: &cp}
	return m
}

type fakeRecorder struct {
	mu   sync.Mutex
	got  []domain.Summons
	fail error
	// onRecord runs INSIDE RecordSummons, before it answers.
	onRecord func()
}

func (f *fakeRecorder) RecordSummons(_ context.Context, s domain.Summons) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.onRecord != nil {
		f.onRecord()
	}
	f.got = append(f.got, s)
	return f.fail
}

func (f *fakeRecorder) calls() []domain.Summons {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]domain.Summons(nil), f.got...)
}

func summonsBridge(t *testing.T, ig *fakeIG) (*Bridge, *fakeRecorder) {
	t.Helper()
	b := NewBridge(Options{
		Store:  session.Store{Dir: t.TempDir()},
		Sink:   stream.NewHub(),
		State:  domain.StateAuthenticated,
		Self:   domain.Self{FBID: "18247361241432477", IGID: "79597894900", Username: "not_____nora"},
		Client: ig,
	})
	rec := &fakeRecorder{}
	b.SetSummonsRecorder(rec)
	return b, rec
}

// deliver runs one delta through the socket callback and waits for the
// intake it started.
func deliver(t *testing.T, b *Bridge, msg *slidetypes.Message) {
	t.Helper()
	if err := b.onEvent(t.Context(), &slidetypes.Delta{ThreadIGID: threadShort, Data: &slidetypes.NewMessageEvent{Message: msg}}); err != nil {
		t.Fatal(err)
	}
	b.summonsInflight.Wait()
}

// events is every buffered SSE event, decoded, in order.
func events(t *testing.T, b *Bridge) []map[string]any {
	t.Helper()
	_, replay, _, _ := hubOf(b).Subscribe()
	out := make([]map[string]any, 0, len(replay))
	for _, raw := range replay {
		var ev map[string]any
		if err := json.Unmarshal(raw, &ev); err != nil {
			t.Fatal(err)
		}
		out = append(out, ev)
	}
	return out
}

func summonsEvents(t *testing.T, b *Bridge) []map[string]any {
	t.Helper()
	var out []map[string]any
	for _, ev := range events(t, b) {
		if ev["kind"] == kindSummons {
			out = append(out, ev)
		}
	}
	return out
}

// Detection: an XMA card from somebody else whose subtitle, caption or
// title carries word-bounded @own-handle. Nothing else.
func TestSummonsCardDetection(t *testing.T) {
	self := domain.Self{FBID: "18247361241432477", IGID: "79597894900", Username: "not_____nora"}
	own := int64(18247361241432477)
	// card is the measured card with one thing about its XMA changed.
	card := func(change func(m *slidetypes.Message, x *slidetypes.XMAContent)) *slidetypes.Message {
		m := liveCard(t, "is this true")
		change(m, m.Content.Content.(*slidetypes.MessageContentXMA).XMA)
		return m
	}
	for _, tc := range []struct {
		name string
		msg  *slidetypes.Message
		own  int64
		want bool
	}{
		{"measured card", liveCard(t, cardText), own, true},
		{"handle only in the caption", card(func(_ *slidetypes.Message, x *slidetypes.XMAContent) {
			x.CaptionBodyText = "look @not_____nora"
		}), own, false},
		{"handle only in the title", card(func(_ *slidetypes.Message, x *slidetypes.XMAContent) {
			x.TitleText, x.XMATitle = "@not_____nora", "@not_____nora"
		}), own, false},
		{"another XMA typename", card(func(_ *slidetypes.Message, x *slidetypes.XMAContent) {
			x.SubtitleText, x.Typename = cardText, "SlideMessagePortraitXMA"
		}), own, false},
		{"another content_type", card(func(m *slidetypes.Message, x *slidetypes.XMAContent) {
			x.SubtitleText, m.ContentType = cardText, "XMA"
		}), own, false},
		{"no handle", liveCard(t, "is this true"), own, false},
		{"a longer handle", liveCard(t, "@not_____nora_fan is this true"), own, false},
		{"an email-like prefix", liveCard(t, "me@not_____nora is this true"), own, false},
		{"own fbid unknown", liveCard(t, cardText), 0, false},
		{"sent by this account", func() *slidetypes.Message {
			m := liveCard(t, cardText)
			m.SenderFBID = own
			return m
		}(), own, false},
		{"sent under this account's username", func() *slidetypes.Message {
			m := liveCard(t, cardText)
			m.Sender.UserDict.Username = "NOT_____NORA"
			return m
		}(), own, false},
		{"not an XMA card", &slidetypes.Message{
			MessageID: cardMID, SenderFBID: 186217604552756, TextBody: cardText,
			Content: slidetypes.MessageContentWrapper{Content: &slidetypes.MessageContentText{TextBody: cardText}},
		}, own, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			_, got := summonsCardOf(threadShort, tc.msg, self, tc.own)
			if got != tc.want {
				t.Errorf("summons = %v, want %v", got, tc.want)
			}
		})
	}
}

// The measured path end to end in the gateway: the live card (no
// target_url) goes out as its ordinary message event FIRST, then one
// GetThread on the SHORT id finds the same message with target_url, and the
// summons event goes out resolved, after the summons was recorded.
func TestASummonsCardResolvesThroughTheRefetch(t *testing.T) {
	for name, target := range map[string]string{
		"post": "https://www.instagram.com/p/" + cardCode + "/",
		"reel": "https://www.instagram.com/reel/" + cardCode + "/?igsh=x",
	} {
		t.Run(name, func(t *testing.T) {
			ig := &fakeIG{ThreadMessages: []*slidetypes.Message{
				{MessageID: "mid.$another"}, refetched(t, target),
			}}
			b, rec := summonsBridge(t, ig)
			// The record is on disk BEFORE the summons event goes out: when
			// it is made, the only event emitted is the ordinary message.
			recordedBeforeEmit := false
			rec.onRecord = func() {
				_, buffered, _, _ := hubOf(b).Stats()
				recordedBeforeEmit = buffered == 1
			}
			deliver(t, b, liveCard(t, cardText))
			if !recordedBeforeEmit {
				t.Error("the summons event was emitted before the summons was recorded")
			}

			all := events(t, b)
			if len(all) != 2 || all[0]["kind"] != kindMessage || all[1]["kind"] != kindSummons {
				t.Fatalf("events = %v, want the message event then the summons event", all)
			}
			ev := all[1]
			want := map[string]any{
				"message_id": cardMID, "room_id": threadShort, "text": cardText,
				"shortcode": cardCode, "media_id": cardMedia, "resolved": true,
				"received_at_ms": float64(cardTSMS),
				"sender":         map[string]any{"fbid": cardSenderFBID, "igid": cardSenderIGID, "username": cardSenderName},
			}
			for k, v := range want {
				if !equalJSON(ev[k], v) {
					t.Errorf("%s = %v, want %v", k, ev[k], v)
				}
			}
			if len(ev) != 9 {
				t.Errorf("summons event has %d fields, want the contract's 9: %v", len(ev), ev)
			}
			if len(ig.GetThreadIDs) != 1 || ig.GetThreadIDs[0] != threadShort {
				t.Errorf("GetThread calls = %v, want one, on the short id", ig.GetThreadIDs)
			}
			got := rec.calls()
			if len(got) != 1 || got[0].MessageID != cardMID || got[0].Media.String() != cardMedia ||
				got[0].Sender.String() != cardSenderIGID || got[0].ReceivedAt.UnixMilli() != cardTSMS {
				t.Errorf("recorded = %+v", got)
			}
		})
	}
}

func equalJSON(a, b any) bool {
	x, _ := json.Marshal(a)
	y, _ := json.Marshal(b)
	return bytes.Equal(x, y)
}

// Every way the refetch cannot name the post emits resolved=false, with an
// empty shortcode and media_id, and records nothing.
func TestAnUnresolvedSummonsIsEmittedAndNotRecorded(t *testing.T) {
	for name, ig := range map[string]*fakeIG{
		"refetch fails":              {ThreadErr: errors.New("boom")},
		"no message page":            {},
		"not on the first page":      {ThreadMessages: []*slidetypes.Message{{MessageID: "mid.$another"}}},
		"no target_url":              {ThreadMessages: []*slidetypes.Message{liveCard(t, cardText)}},
		"target_url is not a post":   {ThreadMessages: []*slidetypes.Message{refetched(t, "https://www.instagram.com/"+cardSenderName+"/")}},
		"target_url on another host": {ThreadMessages: []*slidetypes.Message{refetched(t, "https://example.invalid/p/"+cardCode+"/")}},
		"undecodable shortcode":      {ThreadMessages: []*slidetypes.Message{refetched(t, "https://www.instagram.com/p/ABCDEFGHIJKLM/")}},
	} {
		t.Run(name, func(t *testing.T) {
			b, rec := summonsBridge(t, ig)
			deliver(t, b, liveCard(t, cardText))
			evs := summonsEvents(t, b)
			if len(evs) != 1 {
				t.Fatalf("%d summons events, want 1", len(evs))
			}
			if evs[0]["resolved"] != false || evs[0]["shortcode"] != "" || evs[0]["media_id"] != "" {
				t.Errorf("event = %v, want resolved=false with empty shortcode and media_id", evs[0])
			}
			if evs[0]["message_id"] != cardMID || evs[0]["text"] != cardText {
				t.Errorf("event = %v, the card's own fields are missing", evs[0])
			}
			if n := len(rec.calls()); n != 0 {
				t.Errorf("%d record(s) of an unresolved summons, want 0", n)
			}
		})
	}
}

// A checkpoint on the refetch flips the session, the same as on any read.
func TestACheckpointOnTheRefetchFlipsTheSession(t *testing.T) {
	b, _ := summonsBridge(t, &fakeIG{ThreadErr: httpclient.ErrCheckpointRequired})
	deliver(t, b, liveCard(t, cardText))
	if st := b.currentState(); st != domain.StateCheckpointed {
		t.Errorf("state = %q, want checkpointed", st)
	}
}

// One refetch per card: the socket redelivering the same card costs no
// second GetThread and no second summons event. A message that is not a
// summons costs none at all.
func TestOneRefetchPerCard(t *testing.T) {
	ig := &fakeIG{ThreadMessages: []*slidetypes.Message{refetched(t, "https://www.instagram.com/p/"+cardCode+"/")}}
	b, rec := summonsBridge(t, ig)
	deliver(t, b, liveCard(t, cardText))
	deliver(t, b, liveCard(t, cardText))
	if len(ig.GetThreadIDs) != 1 || len(summonsEvents(t, b)) != 1 || len(rec.calls()) != 1 {
		t.Errorf("%d GetThread(s), %d summons event(s), %d record(s); want 1, 1, 1",
			len(ig.GetThreadIDs), len(summonsEvents(t, b)), len(rec.calls()))
	}
	deliver(t, b, liveCard(t, "no mention here"))
	if len(ig.GetThreadIDs) != 1 {
		t.Errorf("a card with no mention cost a GetThread")
	}
}

func TestTheSeenSetIsBounded(t *testing.T) {
	s := newSeenSet(2)
	if !s.add("a") || !s.add("b") || s.add("a") {
		t.Fatal("dedupe within capacity failed")
	}
	s.add("c") // evicts a
	if !s.add("a") {
		t.Error("the oldest id was not evicted")
	}
	if len(s.ids) != 2 || len(s.order) != 2 {
		t.Errorf("set holds %d/%d, want 2", len(s.ids), len(s.order))
	}
}

// A card whose record failed is emitted UNRESOLVED: resolved=true promises
// a reply can pass guard 3(a), and without the record none can.
func TestAFailedRecordIsEmittedUnresolved(t *testing.T) {
	ig := &fakeIG{ThreadMessages: []*slidetypes.Message{refetched(t, "https://www.instagram.com/p/"+cardCode+"/")}}
	b, rec := summonsBridge(t, ig)
	rec.fail = domain.ErrLedgerUnavailable
	deliver(t, b, liveCard(t, cardText))
	evs := summonsEvents(t, b)
	if len(evs) != 1 || evs[0]["resolved"] != false || evs[0]["shortcode"] != "" || evs[0]["media_id"] != "" {
		t.Errorf("events = %v, want one unresolved summons with empty shortcode and media_id", evs)
	}
	if len(rec.calls()) != 1 {
		t.Errorf("%d record attempt(s), want 1", len(rec.calls()))
	}
}

// A shared post whose caption tags her, and a card whose only mention is in
// its title, cost no thread read and emit no summons.
func TestASharedPostTaggingHerIsNotASummons(t *testing.T) {
	shared := liveCard(t, "")
	x := shared.Content.Content.(*slidetypes.MessageContentXMA).XMA
	x.Typename, x.CaptionBodyText = "SlideMessagePortraitXMA", "look @not_____nora"
	titled := liveCard(t, "nice")
	y := titled.Content.Content.(*slidetypes.MessageContentXMA).XMA
	y.TitleText, y.XMATitle = "@not_____nora", "@not_____nora"
	for name, msg := range map[string]*slidetypes.Message{"shared post": shared, "title only": titled} {
		t.Run(name, func(t *testing.T) {
			ig := &fakeIG{ThreadMessages: []*slidetypes.Message{refetched(t, "https://www.instagram.com/p/"+cardCode+"/")}}
			b, rec := summonsBridge(t, ig)
			deliver(t, b, msg)
			all := events(t, b) // the replay is drained by reading it: read once
			if len(ig.GetThreadIDs) != 0 || len(rec.calls()) != 0 {
				t.Errorf("%d read(s), %d record(s); want none", len(ig.GetThreadIDs), len(rec.calls()))
			}
			if len(all) != 1 || all[0]["kind"] != kindMessage {
				t.Errorf("events = %v, want the ordinary message event only", all)
			}
		})
	}
}

// The refetch budget at the intake: seven distinct cards inside one window
// cost exactly six thread reads, and the seventh is emitted unresolved.
func TestTheRefetchBudgetHoldsAtTheIntake(t *testing.T) {
	var page []*slidetypes.Message
	var cards []*slidetypes.Message
	for i := range summonsRefetchMax + 1 {
		mid := fmt.Sprintf("mid.$budget%02d", i)
		live := liveCard(t, cardText)
		live.MessageID = mid
		cards = append(cards, live)
		r := refetched(t, "https://www.instagram.com/p/"+cardCode+"/")
		r.MessageID = mid
		page = append(page, r)
	}
	ig := &fakeIG{ThreadMessages: page}
	b, _ := summonsBridge(t, ig)
	for _, c := range cards {
		deliver(t, b, c)
	}
	if len(ig.GetThreadIDs) != summonsRefetchMax {
		t.Errorf("%d thread reads, want %d", len(ig.GetThreadIDs), summonsRefetchMax)
	}
	evs := summonsEvents(t, b)
	if len(evs) != summonsRefetchMax+1 {
		t.Fatalf("%d summons events, want %d", len(evs), summonsRefetchMax+1)
	}
	for i, ev := range evs {
		want := i < summonsRefetchMax
		if ev["resolved"] != want {
			t.Errorf("event %d (%v) resolved = %v, want %v", i, ev["message_id"], ev["resolved"], want)
		}
	}
}

// The intake's log lines carry no id, no name and no text.
func TestTheIntakeLogsNoIdentifierOrText(t *testing.T) {
	var buf bytes.Buffer
	prev := slog.Default()
	slog.SetDefault(slog.New(slog.NewJSONHandler(&buf, &slog.HandlerOptions{Level: slog.LevelDebug})))
	t.Cleanup(func() { slog.SetDefault(prev) })

	ok := &fakeIG{ThreadMessages: []*slidetypes.Message{refetched(t, "https://www.instagram.com/p/"+cardCode+"/")}}
	b, rec := summonsBridge(t, ok)
	rec.fail = domain.ErrLedgerUnavailable
	deliver(t, b, liveCard(t, cardText))
	b2, _ := summonsBridge(t, &fakeIG{ThreadErr: errors.New("GetThread failed for " + threadShort)})
	deliver(t, b2, liveCard(t, cardText))

	logs := buf.String()
	if !strings.Contains(logs, "summons card received") {
		t.Fatalf("the intake logged nothing: %s", logs)
	}
	for _, secret := range []string{cardMID, cardSenderFBID, cardSenderIGID, cardSenderName, "3f9c", cardCode, cardMedia, threadShort} {
		if strings.Contains(logs, secret) {
			t.Errorf("a log line carries %q: %s", secret, logs)
		}
	}
}

func TestPostOfTargetURL(t *testing.T) {
	for raw, want := range map[string]string{
		"https://www.instagram.com/p/" + cardCode + "/":                        cardMedia,
		"https://instagram.com/reel/" + cardCode:                               cardMedia,
		"https://www.instagram.com/" + cardSenderName + "/p/" + cardCode + "/": cardMedia,
		"https://www.instagram.com/stories/x/123/":                             "",
		"https://www.instagram.com/p/":                                         "",
		"https://www.instagram.com/p/bad%20code/":                              "",
		"https://evil.example/p/" + cardCode + "/":                             "",
		"not a url \x7f": "",
	} {
		_, media, ok := postOfTargetURL(raw)
		if got := map[bool]string{true: media.String(), false: ""}[ok]; got != want {
			t.Errorf("postOfTargetURL(%q) = %q, want %q", raw, got, want)
		}
	}
}

// The refetch budget: six thread reads per ten minutes, then cards are
// reported unresolved and cost nothing upstream.
func TestTheWindowLimiterCapsAndRecovers(t *testing.T) {
	now := time.Unix(1_790_000_000, 0)
	l := newWindowLimiter(summonsRefetchMax, summonsRefetchWindow, func() time.Time { return now })
	for i := 0; i < summonsRefetchMax; i++ {
		if !l.allow() {
			t.Fatalf("call %d refused inside the budget", i+1)
		}
	}
	if l.allow() {
		t.Fatal("a call past the budget was allowed")
	}
	now = now.Add(summonsRefetchWindow)
	if !l.allow() {
		t.Fatal("the budget did not recover after the window")
	}
}
