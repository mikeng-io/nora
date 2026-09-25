package domain

// The comment surface of a post, and the summons ledger that guards the ONE
// write this bridge makes in public (design/instagram-adoption/19 §Account
// safety, phases 2 and 4).
//
// The ledger is the bridge's own memory of two things: which comment
// mentions it SERVED on `GET /mentions` (the only comments it may ever reply
// to), and which replies it has claimed or published (so a summons is
// answered at most once, and the rolling cap and spacing can be counted). The
// rules live in the app layer; this file holds the vocabulary and the pure
// arithmetic over it, so both the app and the session store can share it
// without either importing the other.
//
// None of these values may enter a span, a log line or an error text: a
// comment id, a media id, an author's igid or username, and above all a
// comment's text (16 §Tracing's never-list, 19 §Account safety).

import (
	"errors"
	"regexp"
	"strconv"
	"time"
)

// CommentID is a comment's numeric id, canonical decimal (no leading zeros).
type CommentID struct{ n uint64 }

var decimalPattern = regexp.MustCompile(`^[0-9]{1,20}$`)

// ParseCommentID accepts a positive decimal id that fits 64 bits. Leading
// zeros are canonicalized away, so one comment has one spelling in the
// ledger. The error text does NOT quote the input.
func ParseCommentID(s string) (CommentID, error) {
	if !decimalPattern.MatchString(s) {
		return CommentID{}, Classify(ErrInvalidID, errors.New("comment id must be a positive decimal id"))
	}
	n, err := strconv.ParseUint(s, 10, 64)
	if err != nil || n == 0 {
		return CommentID{}, Classify(ErrInvalidID, errors.New("comment id must be a positive decimal id"))
	}
	return CommentID{n: n}, nil
}

func (id CommentID) String() string { return strconv.FormatUint(id.n, 10) }
func (id CommentID) IsZero() bool   { return id.n == 0 }

// CanonicalMediaID is the decimal spelling of a media pk the way
// MediaID.String() writes it, or "" when s is not one. The ledger compares
// media by this, so a pk that arrived with leading zeros from the wire and
// one decoded from a shortcode are the same post.
func CanonicalMediaID(s string) string {
	if !decimalPattern.MatchString(s) {
		return ""
	}
	n, err := strconv.ParseUint(s, 10, 64)
	if err != nil || n == 0 {
		return ""
	}
	return strconv.FormatUint(n, 10)
}

// Comment is one comment on a post, as `GET /posts/{shortcode}/comments`
// reads it.
type Comment struct {
	ID string
	// ParentID is the comment this one replies to, or "" for a top-level
	// comment.
	ParentID       string
	AuthorIGID     string
	AuthorUsername string
	// Text is what a person wrote. It reaches the 200 body and nowhere else.
	Text string
	// At is Unix seconds, or 0 when the wire carried no usable time.
	At int64
}

// CommentPage is one page of comments. Skipped counts the items the wire
// carried that could not be decoded (counted, never dropped silently).
type CommentPage struct {
	Items   []Comment
	Skipped int
}

// PostedComment is a reply Instagram accepted AND named.
type PostedComment struct {
	ID               string
	ReplyToCommentID string
}

// --- the write's refusals -------------------------------------------------------
//
// A refusal is the bridge declining to call Instagram at all, which is not one
// of the closed set's upstream kinds. Each guard has its own sentinel, and the
// HTTP layer gives each its own code (transport/http/errors.go). Two of them
// refine a closed-set kind because that kind already says the right thing to a
// caller that knows nothing newer: bad text is the caller's mistake (400), and
// an unreadable ledger is a local fault the operator can fix (503).
var (
	// ErrWritesDisabled is INSTAGRAM_COMMENT_WRITES not being `on`.
	ErrWritesDisabled = errors.New("comment writes are disabled in this bridge")
	// ErrNotASummons is a reply target this bridge did not serve from
	// GET /mentions, on this post, within the served window. It is also
	// what a missing reply target answers: a top-level comment is never
	// written.
	ErrNotASummons = errors.New("the reply target is not a summons this bridge served on this post within the last 7 days")
	// ErrAlreadyReplied is a summons with ANY reply attempt: claimed,
	// published or failed.
	ErrAlreadyReplied = errors.New("this summons already had its one reply attempt")
	// ErrInvalidCommentText is empty text, text over the limit, or text that
	// is not UTF-8.
	ErrInvalidCommentText = refine(ErrInvalidID, "comment text must be 1 to 2200 characters of UTF-8 after trimming")
	// ErrDailyCap is the rolling 24-hour cap being reached.
	ErrDailyCap = errors.New("the rolling 24-hour comment cap is reached")
	// ErrTooSoon is the minimum spacing since the last claim not having
	// passed.
	ErrTooSoon = errors.New("the minimum interval since the last comment has not passed")
	// ErrLedgerUnavailable is a ledger that cannot be read or written. Writes
	// refuse; reads are unaffected.
	ErrLedgerUnavailable = refine(ErrUnavailable, "the comment ledger cannot be read or written")
	// ErrWritesBlocked is the persisted latch (CommentLedger.Block): Instagram
	// answered a write with an action-block signal, and every later write is
	// refused until an operator clears `writes_blocked` in comments.json.
	ErrWritesBlocked = errors.New("comment writes are latched off after instagram signalled an action block; an operator must clear writes_blocked in comments.json")
	// ErrNotPosted marks a write failure where Instagram ANSWERED with a
	// refusal (a 4xx, or a 200 carrying "status":"fail"), so the comment is
	// known not to exist. Every other failure is ambiguous: the comment may
	// exist, and the reply stays claimed forever rather than risk a second
	// public comment.
	ErrNotPosted = errors.New("instagram refused the comment; nothing was posted")
)

// ActionBlocked marks a write refusal that carried Instagram's action-block
// signal. Reason is one of the fixed words below, never text from the wire.
// It is also ErrNotPosted: the comment is known not to exist.
type ActionBlocked struct {
	Reason string
	Err    error
}

// The block reasons. A 429 on a write is treated as a block too: retrying a
// write into a rate limit is how a block becomes a checkpoint.
const (
	BlockFeedbackRequired = "feedback_required"
	BlockSpam             = "spam"
	BlockSentry           = "sentry_block"
	BlockRateLimited      = "rate_limited"
)

func (e *ActionBlocked) Error() string   { return "instagram blocked the write: " + e.Reason }
func (e *ActionBlocked) Unwrap() []error { return []error{e.Err, ErrNotPosted} }

// MaxCommentRunes is the longest reply the bridge sends. 2200 is Instagram's
// caption limit; the comment limit is UNMEASURED (design 19's table), so this
// is the conservative bound the design's caller agreed to.
const MaxCommentRunes = 2200

// --- the ledger -----------------------------------------------------------------

const (
	// ServedWindow is how long a served mention stays repliable, and how long
	// the ledger keeps it. Reply records are NEVER pruned: they are tiny, and
	// a pruned reply key is a summons that can be answered a second time.
	ServedWindow = 7 * 24 * time.Hour
	// CapWindow is the rolling window the daily cap counts over.
	CapWindow = 24 * time.Hour
)

// ReplyState is where one reply is in its life.
type ReplyState string

const (
	// ReplyClaimed is written and persisted BEFORE the upstream call. A reply
	// that stays claimed had an ambiguous outcome and is never retried.
	ReplyClaimed ReplyState = "claimed"
	// ReplyPublished is a reply Instagram accepted and named.
	ReplyPublished ReplyState = "published"
	// ReplyFailed is a reply Instagram definitely refused (ErrNotPosted). It
	// is as final as a published one: the summons is never attempted again,
	// by the bridge or by the caller. Retrying a refused write is how an
	// action block turns into a checkpoint.
	ReplyFailed ReplyState = "failed"
)

// ServedMention is one comment mention the bridge served on GET /mentions.
type ServedMention struct {
	MediaID  string
	ServedAt time.Time
}

// Reply is one reply the bridge claimed, keyed in the ledger by the comment
// it replies to.
type Reply struct {
	MediaID   string
	State     ReplyState
	ClaimedAt time.Time
	UpdatedAt time.Time
	// CommentID is the new comment's id, once published.
	CommentID string
}

// CommentLedger is the whole ledger at one instant. Maps are keyed by the
// canonical comment id (CommentID.String()).
type CommentLedger struct {
	Served  map[string]ServedMention
	Replies map[string]Reply
	// Blocked is the writes-off latch: set when Instagram answered a write
	// with an action-block signal, and cleared only by an operator.
	Blocked *WritesBlock
}

// WritesBlock is when and why the latch was set.
type WritesBlock struct {
	At     time.Time
	Reason string
}

// Block sets the latch, keeping the FIRST block's time and reason.
func (l *CommentLedger) Block(reason string, at time.Time) {
	if l.Blocked == nil {
		l.Blocked = &WritesBlock{At: at, Reason: reason}
	}
}

// NewCommentLedger is an empty ledger.
func NewCommentLedger() *CommentLedger {
	return &CommentLedger{Served: map[string]ServedMention{}, Replies: map[string]Reply{}}
}

// RecordServed notes that the bridge served one mention. The FIRST serve
// time is kept: the window runs from when the summons was first seen, not
// from the latest poll that happened to show it again.
func (l *CommentLedger) RecordServed(commentID CommentID, mediaID string, at time.Time) {
	key := commentID.String()
	if _, seen := l.Served[key]; seen {
		return
	}
	l.Served[key] = ServedMention{MediaID: mediaID, ServedAt: at}
}

// IsSummons reports whether commentID was served on mediaID within the
// served window before now.
func (l *CommentLedger) IsSummons(commentID CommentID, mediaID string, now time.Time) bool {
	s, ok := l.Served[commentID.String()]
	if !ok || mediaID == "" || s.MediaID != mediaID {
		return false
	}
	age := now.Sub(s.ServedAt)
	return age >= 0 && age <= ServedWindow
}

// Replied reports whether commentID has ANY reply attempt, whatever its
// outcome: claimed, published or failed. One attempt per summons, ever.
func (l *CommentLedger) Replied(commentID CommentID) bool {
	_, ok := l.Replies[commentID.String()]
	return ok
}

// CountedSince counts every claim made after since, whatever its outcome:
// what the rolling cap counts. A refused write is still a write Instagram
// saw.
func (l *CommentLedger) CountedSince(since time.Time) int {
	n := 0
	for _, r := range l.Replies {
		if r.ClaimedAt.After(since) {
			n++
		}
	}
	return n
}

// LastClaim is the most recent claim of ANY state, or the zero time.
func (l *CommentLedger) LastClaim() time.Time {
	var last time.Time
	for _, r := range l.Replies {
		if r.ClaimedAt.After(last) {
			last = r.ClaimedAt
		}
	}
	return last
}

// Claim records a reply as claimed.
func (l *CommentLedger) Claim(replyTo CommentID, mediaID string, now time.Time) {
	l.Replies[replyTo.String()] = Reply{MediaID: mediaID, State: ReplyClaimed, ClaimedAt: now, UpdatedAt: now}
}

// Settle moves a claimed reply to published (with its new id) or failed. It
// never touches a reply that is not currently claimed.
func (l *CommentLedger) Settle(replyTo CommentID, state ReplyState, newID string, now time.Time) bool {
	key := replyTo.String()
	r, ok := l.Replies[key]
	if !ok || r.State != ReplyClaimed {
		return false
	}
	r.State, r.UpdatedAt, r.CommentID = state, now, newID
	l.Replies[key] = r
	return true
}

// Prune drops served mentions older than the served window. Replies and
// the latch are never pruned.
func (l *CommentLedger) Prune(now time.Time) {
	for k, s := range l.Served {
		if now.Sub(s.ServedAt) > ServedWindow {
			delete(l.Served, k)
		}
	}
}
