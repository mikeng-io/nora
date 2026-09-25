package instameow

// The summons intake (design/instagram-adoption/19, "Intake, re-measured
// 2026-09-26").
//
// # What a summons looks like on the wire (measured 2026-09-26)
//
// A comment (or a caption) mentioning this account reaches it as a DM-thread
// NewMessageEvent: an XMA card (content_type MESSAGE_INLINE_SHARE, typename
// SlideMessageLayeredXMA) whose subtitle_text is the comment's text, whose
// title is the COMMENTER's username, and whose sender is the commenter. The
// live delta carries NO target_url, target_id or CTA: nothing in it names the
// post. The thread query (GetThread, IGDThreadDetailQuery, the read the
// roster makes) returns the same message WITH content.xma.target_url, the
// post's permalink. So one card costs one GetThread, and only for a card
// whose text actually mentions this account.
//
// # What this file does
//
// For such a card, sent by somebody else, it refetches the thread ONCE (off
// the socket goroutine, 30 s, deduplicated by message id), finds the message,
// reads the shortcode out of its target_url and decodes the media pk. It then
// emits the SSE `summons` event, IN ADDITION to the ordinary message event
// the card already produced: resolved=true with shortcode and media_id, or
// resolved=false with both empty when the refetch failed, the message was
// not on the first page, or it had no usable target_url. A resolved summons
// is recorded (SummonsRecorder) BEFORE the event goes out, so a caller that
// replies on the event finds the record that authorizes the reply (write
// guard 3(a), app/comments.go) already on disk.
//
// # What never reaches a log line
//
// The card's text, the sender's ids and username, the message id, the room,
// the shortcode and the media pk. The log lines here are fixed messages with
// booleans, counts and fixed reason words.

import (
	"context"
	"log/slog"
	"net/url"
	"strings"
	"sync"
	"time"

	"instagram-bridge/internal/domain"

	"go.mau.fi/mautrix-meta/pkg/instameow/slidetypes"
)

const (
	kindSummons = "summons"

	// summonsRefetchTimeout bounds the one GetThread a card costs.
	summonsRefetchTimeout = 30 * time.Second
	// summonsSeenCap bounds the dedupe set: the most recent card ids, so a
	// card the socket redelivers (a resync, a reconnect) is never refetched
	// twice while the process runs. A restart forgets it; the ledger keeps
	// the FIRST record of a card, so a replay after a restart records
	// nothing new.
	summonsSeenCap = 1024
)

// SummonsRecorder is where a resolved summons is recorded: the app's
// RecordSummons in production (cmd wire.go).
type SummonsRecorder interface {
	RecordSummons(ctx context.Context, s domain.Summons) error
}

// summonsEvent is the SSE `summons` event, field for field the contract's.
type summonsEvent struct {
	Kind      string      `json:"kind"`
	MessageID string      `json:"message_id"`
	RoomID    string      `json:"room_id"`
	Sender    eventSender `json:"sender"`
	// Text is the card's subtitle_text: the comment's text.
	Text string `json:"text"`
	// Shortcode is the one in the refetched target_url; "" unless resolved.
	Shortcode string `json:"shortcode"`
	// MediaID is the numeric pk the shortcode decodes to; "" unless resolved.
	MediaID      string `json:"media_id"`
	ReceivedAtMS int64  `json:"received_at_ms"`
	Resolved     bool   `json:"resolved"`
}

// summonsCard is what the delta tells about a card before the refetch.
type summonsCard struct {
	room       string
	messageID  string
	sender     eventSender
	text       string
	receivedAt time.Time
}

// seenSet is a bounded set of message ids, oldest evicted first.
type seenSet struct {
	mu    sync.Mutex
	ids   map[string]struct{}
	order []string
	cap   int
}

func newSeenSet(capacity int) *seenSet {
	return &seenSet{ids: map[string]struct{}{}, cap: capacity}
}

// add reports whether id was new, and records it.
func (s *seenSet) add(id string) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, ok := s.ids[id]; ok {
		return false
	}
	if len(s.order) >= s.cap {
		oldest := s.order[0]
		s.order = s.order[1:]
		delete(s.ids, oldest)
	}
	s.ids[id] = struct{}{}
	s.order = append(s.order, id)
	return true
}

// SetSummonsRecorder installs the recorder. wire.go sets it once, before the
// supervisor starts; with none, a resolved summons is still emitted but
// nothing records it, so no reply to it can pass guard 3 (fail closed).
func (b *Bridge) SetSummonsRecorder(r SummonsRecorder) {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.summonsRecorder = r
}

// Deliver runs one event through the socket callback and waits for any
// summons intake it started. Production never calls it (instameow calls the
// callback itself); the wired end-to-end tests drive a delta with it.
func (b *Bridge) Deliver(ctx context.Context, evt slidetypes.ClientEvent) error {
	err := b.onEvent(ctx, evt)
	b.summonsInflight.Wait()
	return err
}

func (b *Bridge) recorder() SummonsRecorder {
	b.mu.RLock()
	defer b.mu.RUnlock()
	return b.summonsRecorder
}

// The measured summons card's two discriminators (design 19, 2026-09-26).
// Any other XMA is a share, not a summons: a post shared into a group chat
// whose caption tags her is somebody showing the room a post, and must not
// cost a thread read or become a summons.
const (
	summonsContentType = "MESSAGE_INLINE_SHARE"
	summonsXMATypename = "SlideMessageLayeredXMA"
)

// summonsCardOf reports the card when msg is a summons: content_type
// MESSAGE_INLINE_SHARE carrying a SlideMessageLayeredXMA, sent by somebody
// who is not this account, whose SUBTITLE (the comment's text) carries
// word-bounded "@"+own username. The title is the commenter's username and
// the caption is a shared post's, so neither is read. Own identity unknown
// is never a summons (fail closed: this account cannot tell its own card
// from anybody's).
func summonsCardOf(room string, msg *slidetypes.Message, self domain.Self, ownFBID int64) (summonsCard, bool) {
	if msg == nil || msg.MessageID == "" || self.Username == "" || ownFBID == 0 {
		return summonsCard{}, false
	}
	if msg.ContentType != summonsContentType {
		return summonsCard{}, false
	}
	content, ok := msg.Content.Content.(*slidetypes.MessageContentXMA)
	if !ok || content == nil || content.XMA == nil || content.XMA.Typename != summonsXMATypename {
		return summonsCard{}, false
	}
	sender := senderOf(msg)
	if msg.SenderFBID == ownFBID || sender.FBID == self.FBID || strings.EqualFold(sender.Username, self.Username) {
		return summonsCard{}, false
	}
	x := content.XMA
	if !domain.MentionsHandle(x.SubtitleText, self.Username) {
		return summonsCard{}, false
	}
	if msg.ThreadFBID != "" {
		room = msg.ThreadFBID
	}
	card := summonsCard{room: room, messageID: msg.MessageID, sender: sender, text: x.SubtitleText}
	if !msg.TimestampMS.IsZero() {
		card.receivedAt = msg.TimestampMS.Time
	}
	return card, true
}

// detectSummons starts the intake for one new message when it is a summons
// card not seen before. It never blocks: the refetch runs on its own
// goroutine, because this is called on the socket's.
func (b *Bridge) detectSummons(room string, msg *slidetypes.Message) {
	card, ok := summonsCardOf(room, msg, b.selfSnapshot(), b.ownFBID())
	if !ok || !b.summonsSeen.add(card.messageID) {
		return
	}
	if card.receivedAt.IsZero() {
		card.receivedAt = time.Now()
	}
	b.summonsInflight.Add(1)
	go func() {
		defer b.summonsInflight.Done()
		ctx, cancel := context.WithTimeout(context.Background(), summonsRefetchTimeout)
		defer cancel()
		b.resolveSummons(ctx, card)
	}()
}

// resolveSummons is the refetch, the record and the event, for one card.
func (b *Bridge) resolveSummons(ctx context.Context, card summonsCard) {
	ev := &summonsEvent{
		Kind: kindSummons, MessageID: card.messageID, RoomID: card.room,
		Sender: card.sender, Text: card.text, ReceivedAtMS: card.receivedAt.UnixMilli(),
	}
	var (
		code   domain.Shortcode
		media  domain.MediaID
		reason = "rate_limited"
	)
	// Every card costs one thread read on the session her group chats run on,
	// and anybody can @mention her. Past the budget a card is still reported,
	// unresolved, and costs nothing upstream.
	if b.summonsRate == nil || b.summonsRate.allow() {
		code, media, reason = b.refetchTarget(ctx, card)
	}
	// resolved=true is a promise that a reply to this summons can pass
	// guard 3(a): it is made only once the record is on disk.
	if reason == "" {
		if b.recordSummons(ctx, card, media) {
			ev.Shortcode, ev.MediaID, ev.Resolved = code.String(), media.String(), true
		} else {
			reason = "record_failed"
		}
	}
	slog.InfoContext(ctx, "summons card received", "resolved", ev.Resolved, "reason", reason)
	b.sink.Emit(ev)
}

// recordSummons records a resolved summons and reports whether it is on
// disk. A failure is logged with a fixed word; the caller then emits the
// card unresolved, because a reply to it would be refused.
func (b *Bridge) recordSummons(ctx context.Context, card summonsCard, media domain.MediaID) bool {
	rec := b.recorder()
	if rec == nil {
		slog.WarnContext(ctx, "summons not recorded: no recorder is wired; emitted unresolved")
		return false
	}
	sender, err := domain.ParseIGID(card.sender.IGID)
	if err != nil {
		slog.WarnContext(ctx, "summons not recorded: the card's sender has no igid; emitted unresolved")
		return false
	}
	if err := rec.RecordSummons(ctx, domain.Summons{
		MessageID: card.messageID, Media: media, Sender: sender, ReceivedAt: card.receivedAt,
	}); err != nil {
		slog.WarnContext(ctx, "summons not recorded; emitted unresolved", "code", recordCode(err))
		return false
	}
	return true
}

func recordCode(err error) string {
	if domain.KindOf(err) == domain.ErrUnavailable {
		return "ledger_unavailable"
	}
	return "not_recordable"
}

// refetchTarget reads the card's thread once and returns the post its
// target_url names, or a fixed reason word why it could not.
func (b *Bridge) refetchTarget(ctx context.Context, card summonsCard) (domain.Shortcode, domain.MediaID, string) {
	cli, err := b.client()
	if err != nil {
		return domain.Shortcode{}, domain.MediaID{}, "no_client"
	}
	req := slidetypes.MakeGetThreadInfoRequest(card.room)
	resp, err := call(ctx, "instagram.GetThread", nil, func(ctx context.Context) (*slidetypes.ThreadInfoResponse, error) {
		return cli.GetThread(ctx, req)
	})
	if err != nil {
		b.checkRejected(ctx, err)
		return domain.Shortcode{}, domain.MediaID{}, "refetch_failed"
	}
	if resp == nil || resp.ThreadInfo.AsIGDirectThread == nil || resp.ThreadInfo.AsIGDirectThread.SlideMessages == nil {
		return domain.Shortcode{}, domain.MediaID{}, "no_messages"
	}
	for _, edge := range resp.ThreadInfo.AsIGDirectThread.SlideMessages.Edges {
		m := edge.Node
		if m == nil || m.MessageID != card.messageID {
			continue
		}
		content, ok := m.Content.Content.(*slidetypes.MessageContentXMA)
		if !ok || content == nil || content.XMA == nil || content.XMA.TargetURL == "" {
			return domain.Shortcode{}, domain.MediaID{}, "no_target_url"
		}
		code, media, ok := postOfTargetURL(content.XMA.TargetURL)
		if !ok {
			return domain.Shortcode{}, domain.MediaID{}, "target_url_not_a_post"
		}
		return code, media, ""
	}
	return domain.Shortcode{}, domain.MediaID{}, "not_on_first_page"
}

// postOfTargetURL reads a post's shortcode out of an Instagram permalink,
// `https://www.instagram.com/p/<code>/` or `/reel/<code>/` (optionally under
// an account's path, `/<user>/p/<code>/`), and decodes its media pk with
// domain's decoder. Any other host, shape or undecodable code is false.
func postOfTargetURL(raw string) (domain.Shortcode, domain.MediaID, bool) {
	u, err := url.Parse(raw)
	if err != nil {
		return domain.Shortcode{}, domain.MediaID{}, false
	}
	host := strings.ToLower(u.Hostname())
	if host != "instagram.com" && host != "www.instagram.com" {
		return domain.Shortcode{}, domain.MediaID{}, false
	}
	segs := strings.Split(strings.Trim(u.Path, "/"), "/")
	for i := 0; i+1 < len(segs) && i < 2; i++ {
		if segs[i] != "p" && segs[i] != "reel" {
			continue
		}
		code, err := domain.ParseShortcode(segs[i+1])
		if err != nil {
			return domain.Shortcode{}, domain.MediaID{}, false
		}
		media, err := code.MediaID()
		if err != nil {
			return domain.Shortcode{}, domain.MediaID{}, false
		}
		return code, media, true
	}
	return domain.Shortcode{}, domain.MediaID{}, false
}

const (
	// summonsRefetchMax thread reads per summonsRefetchWindow, across all
	// senders: a friends-only feature needs a handful an hour, and a flood of
	// strangers' mentions must not become a flood of reads.
	summonsRefetchMax    = 6
	summonsRefetchWindow = 10 * time.Minute
)

// windowLimiter allows at most max events per sliding window.
type windowLimiter struct {
	mu     sync.Mutex
	max    int
	window time.Duration
	now    func() time.Time
	times  []time.Time
}

func newWindowLimiter(max int, window time.Duration, now func() time.Time) *windowLimiter {
	return &windowLimiter{max: max, window: window, now: now}
}

func (l *windowLimiter) allow() bool {
	l.mu.Lock()
	defer l.mu.Unlock()
	t := l.now()
	keep := l.times[:0]
	for _, x := range l.times {
		if t.Sub(x) < l.window {
			keep = append(keep, x)
		}
	}
	l.times = keep
	if len(l.times) >= l.max {
		return false
	}
	l.times = append(l.times, t)
	return true
}
