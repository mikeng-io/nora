package app

// The comment use cases: reading a post's comments, and the bridge's ONE
// public write, a reply under a summons (design/instagram-adoption/19).
//
// # Why every guard is here, and in this order
//
// This Instagram account also carries Nora's group chats, so a checkpoint or
// a ban from a public-comment bot takes her off Instagram entirely (19
// §Account safety). Every rule is therefore enforced IN THE BRIDGE, in the
// process that writes, and every one of them refuses BEFORE any call to
// Instagram:
//
//  1. writes switch: INSTAGRAM_COMMENT_WRITES must be `on`   403 writes_disabled
//  2. session ready (the same gate as every route)            409 needs_human
//     then the persisted writes-off latch (an earlier write
//     drew an action block; only an operator clears it)      403 writes_blocked
//  3. the reply target is a comment mention THIS bridge
//     served from GET /mentions, on the same post, within
//     the last 7 days (a missing target is refused too: no
//     top-level comment is ever written)                      403 not_a_summons
//  4. once per summons: no earlier attempt, WHATEVER its
//     outcome (a refused write is never retried)              409 already_replied
//  5. text: 1..2200 runes of UTF-8 after trimming             400 invalid_text
//  6. rolling 24h cap over every claim, whatever its outcome  429 daily_cap
//  7. minimum spacing since the last claim                    429 too_soon
//
// Guards 3 to 7 and the claim run inside ONE ledger transaction, so two
// concurrent requests cannot both pass a guard the first one's claim would
// have failed. The claim is persisted BEFORE the upstream call. After it:
// published on success, failed on a refusal Instagram answered at the
// endpoint (domain.ErrNotPosted), and left CLAIMED on anything ambiguous,
// forever. Nothing here ever retries: a double public comment is worse than
// a missing one, and a retried refusal is how an action block becomes a
// checkpoint. A refusal carrying Instagram's action-block signal
// (domain.ActionBlocked: feedback_required, spam, sentry_block, or a 429)
// also sets the latch, in the same transaction that marks the reply failed,
// and the caller gets 403 writes_blocked, never a status that invites a
// retry.

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"strings"
	"time"
	"unicode/utf8"

	"instagram-bridge/internal/domain"
)

// Comments is the public comment surface of a post.
type Comments interface {
	Comments(ctx context.Context, code domain.Shortcode, media domain.MediaID, parent *domain.CommentID) (domain.CommentPage, error)
	PostComment(ctx context.Context, code domain.Shortcode, media domain.MediaID, replyTo domain.CommentID, text string) (domain.PostedComment, error)
}

// Ledger is the summons ledger's persistence (session.Ledger in
// production). Update runs fn on the ledger under one lock and, iff fn
// returns nil, prunes it as of now and persists it before returning. An
// unreadable ledger is domain.ErrLedgerUnavailable and fn does not run.
type Ledger interface {
	Update(now time.Time, fn func(*domain.CommentLedger) error) error
}

// CommentPolicy is the operator's configuration of the write.
type CommentPolicy struct {
	// WritesEnabled is INSTAGRAM_COMMENT_WRITES == "on". Off by default.
	WritesEnabled bool
	// DailyCap is INSTAGRAM_COMMENT_DAILY_CAP: claimed+published replies per
	// rolling 24 hours. 0 allows none.
	DailyCap int
	// MinInterval is INSTAGRAM_COMMENT_MIN_INTERVAL: the least time between
	// two claims.
	MinInterval time.Duration
}

// DefaultCommentPolicy is writes off, a cap of 10 (design 19 rule 4's
// starting number) and 90 seconds between comments.
func DefaultCommentPolicy() CommentPolicy {
	return CommentPolicy{WritesEnabled: false, DailyCap: 10, MinInterval: 90 * time.Second}
}

// CommentOptions wires the comment use cases.
type CommentOptions struct {
	Port   Comments
	Ledger Ledger
	Policy CommentPolicy
	// Now is the clock; time.Now when nil.
	Now func() time.Time
}

// WithComments adds the comment use cases to the service. A service without
// them answers the comment read as unsupported and refuses every write as
// disabled.
func (s *Service) WithComments(o CommentOptions) *Service {
	s.comments = o.Port
	s.ledger = o.Ledger
	s.policy = o.Policy
	s.now = o.Now
	if s.now == nil {
		s.now = time.Now
	}
	return s
}

var errNoCommentsPort = domain.Classify(domain.ErrUnsupported, errors.New("no comment mechanism is wired into this bridge"))

// errNoChange ends a ledger transaction that has nothing to write.
var errNoChange = errors.New("nothing to record")

// ReadComments reads one page of a post's comments, or, with parent, one
// comment's replies. Session gate first; a shortcode that does not decode to
// a media id is unsupported (the same answer GET /posts/{shortcode} gives).
func (s *Service) ReadComments(ctx context.Context, code domain.Shortcode, parent *domain.CommentID) (domain.CommentPage, error) {
	if _, err := s.ready(); err != nil {
		return domain.CommentPage{}, err
	}
	if s.comments == nil {
		return domain.CommentPage{}, errNoCommentsPort
	}
	media, err := code.MediaID()
	if err != nil {
		return domain.CommentPage{}, err
	}
	return s.comments.Comments(ctx, code, media, parent)
}

// recordServed notes every mention GET /mentions is about to serve, so a
// reply to it can pass guard 3. It never fails the read: a ledger that cannot
// be written only means those mentions cannot be replied to (fail closed),
// and it is logged with a count, never an id.
func (s *Service) recordServed(ctx context.Context, mentions []domain.CommentMention) {
	if s.ledger == nil || len(mentions) == 0 {
		return
	}
	now := s.now()
	err := s.ledger.Update(now, func(led *domain.CommentLedger) error {
		changed := false
		for _, m := range mentions {
			id, err := domain.ParseCommentID(m.CommentID)
			media := domain.CanonicalMediaID(m.MediaID)
			if err != nil || media == "" {
				continue
			}
			// A mention with no date is never recorded (fail closed): once
			// its served record is pruned at 7 days, the feed showing it
			// again would record it as fresh. A mention the feed dates older
			// than the window is not a live summons either.
			if m.At == 0 || now.Sub(time.Unix(m.At, 0)) > domain.ServedWindow {
				continue
			}
			if _, seen := led.Served[id.String()]; seen {
				continue
			}
			led.RecordServed(id, media, now)
			changed = true
		}
		if !changed {
			return errNoChange
		}
		return nil
	})
	if err != nil && !errors.Is(err, errNoChange) {
		slog.WarnContext(ctx, "could not record served mentions; replies to them will be refused",
			"count", len(mentions), "code", ledgerCode(err))
	}
}

func ledgerCode(err error) string {
	if errors.Is(err, domain.ErrLedgerUnavailable) {
		return "ledger_unavailable"
	}
	return "internal"
}

// PostComment writes one reply under a summons comment, behind every guard in
// this file's header, in that order. replyTo is the caller's raw string: a
// missing or malformed one is not a summons (guard 3), not a 400, because no
// such id can ever have been served.
func (s *Service) PostComment(ctx context.Context, code domain.Shortcode, text, replyTo string) (domain.PostedComment, error) {
	// 1. The switch. Also refuses a service with nothing wired to write
	// through: misconfigured is off, never on.
	if !s.policy.WritesEnabled || s.comments == nil || s.ledger == nil {
		return domain.PostedComment{}, domain.ErrWritesDisabled
	}
	// 2. The session.
	if _, err := s.ready(); err != nil {
		return domain.PostedComment{}, err
	}

	var (
		target domain.CommentID
		media  domain.MediaID
		body   = strings.TrimSpace(text)
	)
	now := s.now()
	err := s.ledger.Update(now, func(led *domain.CommentLedger) error {
		// 2b. The latch.
		if led.Blocked != nil {
			return blockedError(led.Blocked.Reason)
		}
		// 3. A summons this bridge served, on this post, in the window.
		id, err := domain.ParseCommentID(replyTo)
		if err != nil {
			return domain.ErrNotASummons
		}
		m, err := code.MediaID()
		if err != nil || !led.IsSummons(id, m.String(), now) {
			return domain.ErrNotASummons
		}
		// 4. Once.
		if led.Replied(id) {
			return domain.ErrAlreadyReplied
		}
		// 5. The text. utf8.ValidString is not dead weight: the HTTP edge's
		// JSON decode replaces invalid UTF-8 with U+FFFD, but this use case
		// does not know its caller decodes JSON, and it is the layer that
		// promises Instagram only ever receives valid text.
		if body == "" || !utf8.ValidString(body) || utf8.RuneCountInString(body) > domain.MaxCommentRunes {
			return domain.ErrInvalidCommentText
		}
		// 6. The rolling cap.
		if led.CountedSince(now.Add(-domain.CapWindow)) >= s.policy.DailyCap {
			return domain.ErrDailyCap
		}
		// 7. The spacing. A clock that went backwards reads as too soon.
		if last := led.LastClaim(); !last.IsZero() && now.Sub(last) < s.policy.MinInterval {
			return domain.ErrTooSoon
		}
		// The claim, persisted by Update before it returns.
		led.Claim(id, m.String(), now)
		target, media = id, m
		return nil
	})
	if err != nil {
		return domain.PostedComment{}, err
	}

	posted, err := s.comments.PostComment(ctx, code, media, target, body)
	var blocked *domain.ActionBlocked
	switch {
	case err == nil:
		s.settle(ctx, target, domain.ReplyPublished, posted.ID, "")
		return posted, nil
	case errors.As(err, &blocked):
		s.settle(ctx, target, domain.ReplyFailed, "", blocked.Reason)
		slog.ErrorContext(ctx, "instagram signalled an action block on a comment write; comment writes are latched off",
			"reason", blocked.Reason)
		return domain.PostedComment{}, blockedError(blocked.Reason)
	case errors.Is(err, domain.ErrNotPosted):
		s.settle(ctx, target, domain.ReplyFailed, "", "")
		return domain.PostedComment{}, err
	default:
		// Ambiguous: the comment may exist. It stays claimed, which blocks
		// another reply to this summons and counts against the cap.
		slog.WarnContext(ctx, "comment write outcome unknown; the reply stays claimed and is never retried",
			"code", "ambiguous")
		return domain.PostedComment{}, err
	}
}

// blockedError is ErrWritesBlocked naming its reason, a fixed word.
func blockedError(reason string) error {
	return fmt.Errorf("%w (reason: %s)", domain.ErrWritesBlocked, reason)
}

// settle records a write's outcome and, with a block reason, sets the latch
// in the same transaction. A failure to persist it leaves the reply claimed
// on disk, which is the safe side, and is logged without ids. (A latch that
// fails to persist is logged at error level by the caller's block branch as
// well: the write that drew it has already been refused.)
func (s *Service) settle(ctx context.Context, target domain.CommentID, state domain.ReplyState, newID, blockReason string) {
	err := s.ledger.Update(s.now(), func(led *domain.CommentLedger) error {
		settled := led.Settle(target, state, newID, s.now())
		if blockReason != "" {
			led.Block(blockReason, s.now())
			return nil
		}
		if !settled {
			return errNoChange
		}
		return nil
	})
	if err != nil && !errors.Is(err, errNoChange) {
		slog.WarnContext(ctx, "could not record a comment write's outcome; the reply stays claimed",
			"state", string(state), "code", ledgerCode(err))
	}
}
