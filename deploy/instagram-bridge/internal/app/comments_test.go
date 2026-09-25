package app

// The comment write's guards against a fake Comments port and the REAL
// session ledger (a test-only import; the architecture test counts
// production imports only). The port counts every call, so "refused before
// any upstream call" is measured as zero calls, not assumed.

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"instagram-bridge/internal/domain"
	"instagram-bridge/internal/session"
)

// Synthetic shortcode/media pairs, the same ones domain's shortcode tests pin
// (computed with instaloader's algorithm, fakeig/posts.go).
const (
	postCode  = "CmiJBr2LAAB"
	postMedia = "3000000000000000001"
	otherCode = "CsFaZNTtgAC"
	summonsA  = "17900000000000001"
	summonsB  = "17900000000000002"
	summonsC  = "17900000000000003"
	summonsD  = "17900000000000004"
)

var t0 = time.Date(2026, 9, 25, 12, 0, 0, 0, time.UTC)

type clock struct{ t time.Time }

func (c *clock) now() time.Time          { return c.t }
func (c *clock) advance(d time.Duration) { c.t = c.t.Add(d) }
func mustID(t *testing.T, s string) domain.CommentID {
	t.Helper()
	id, err := domain.ParseCommentID(s)
	if err != nil {
		t.Fatal(err)
	}
	return id
}

func code(t *testing.T, s string) domain.Shortcode {
	t.Helper()
	c, err := domain.ParseShortcode(s)
	if err != nil {
		t.Fatal(err)
	}
	return c
}

// fakeComments is the Comments port. postErr, when set, is every write's
// answer; onPost runs INSIDE the upstream call, before it answers.
type fakeComments struct {
	posts   int
	reads   int
	postErr error
	onPost  func()
	lastTo  string
	lastTxt string
}

func (f *fakeComments) Comments(context.Context, domain.Shortcode, domain.MediaID, *domain.CommentID) (domain.CommentPage, error) {
	f.reads++
	return domain.CommentPage{}, nil
}

func (f *fakeComments) PostComment(_ context.Context, _ domain.Shortcode, _ domain.MediaID, replyTo domain.CommentID, text string) (domain.PostedComment, error) {
	f.posts++
	f.lastTo, f.lastTxt = replyTo.String(), text
	if f.onPost != nil {
		f.onPost()
	}
	if f.postErr != nil {
		return domain.PostedComment{}, f.postErr
	}
	return domain.PostedComment{ID: "18000000000000009", ReplyToCommentID: replyTo.String()}, nil
}

type commentRig struct {
	svc     *Service
	port    *fakeComments
	social  *fakePorts
	session *fakeSession
	clk     *clock
	dir     string
}

func openPolicy() CommentPolicy {
	return CommentPolicy{WritesEnabled: true, DailyCap: 10, MinInterval: 90 * time.Second}
}

func newCommentRig(t *testing.T, policy CommentPolicy) *commentRig {
	t.Helper()
	r := &commentRig{
		port:    &fakeComments{},
		social:  &fakePorts{},
		session: &fakeSession{snap: domain.Session{State: domain.StateAuthenticated, HasClient: true}},
		clk:     &clock{t: t0},
		dir:     t.TempDir(),
	}
	r.svc = r.build(policy)
	return r
}

// build is a service over the rig's directory with a FRESH ledger: calling it
// again is a restart.
func (r *commentRig) build(policy CommentPolicy) *Service {
	return New(r.session, r.social, r.social).WithComments(CommentOptions{
		Port:   r.port,
		Ledger: session.NewLedger(session.Store{Dir: r.dir}),
		Policy: policy,
		Now:    r.clk.now,
	})
}

// serve runs GET /mentions' use case over these summonses on postMedia.
func (r *commentRig) serve(t *testing.T, commentIDs ...string) {
	t.Helper()
	r.social.mentions = nil
	for _, id := range commentIDs {
		r.social.mentions = append(r.social.mentions, domain.CommentMention{CommentID: id, MediaID: postMedia, At: r.clk.t.Unix()})
	}
	if _, _, err := r.svc.ReadMentions(t.Context()); err != nil {
		t.Fatal(err)
	}
}

func (r *commentRig) reply(t *testing.T, to, text string) (domain.PostedComment, error) {
	t.Helper()
	return r.svc.PostComment(t.Context(), code(t, postCode), text, to)
}

func (r *commentRig) ledger(t *testing.T) *domain.CommentLedger {
	t.Helper()
	var got *domain.CommentLedger
	if err := session.NewLedger(session.Store{Dir: r.dir}).Update(r.clk.t, func(l *domain.CommentLedger) error {
		got = l
		return errNoChange
	}); err != nil && !errors.Is(err, errNoChange) {
		t.Fatal(err)
	}
	return got
}

// Every guard refuses with its own error and with ZERO upstream calls.
func TestEveryCommentGuardRefusesBeforeAnyUpstreamCall(t *testing.T) {
	for _, tc := range []struct {
		name  string
		setup func(t *testing.T, r *commentRig) (to, text string)
		want  error
	}{
		{"writes latched off by an earlier block", func(t *testing.T, r *commentRig) (string, string) {
			r.serve(t, summonsA)
			if err := session.NewLedger(session.Store{Dir: r.dir}).Update(r.clk.t, func(l *domain.CommentLedger) error {
				l.Block(domain.BlockFeedbackRequired, r.clk.t)
				return nil
			}); err != nil {
				t.Fatal(err)
			}
			return summonsA, "hi"
		}, domain.ErrWritesBlocked},
		{"writes off", func(t *testing.T, r *commentRig) (string, string) {
			r.serve(t, summonsA)
			r.svc = r.build(DefaultCommentPolicy())
			return summonsA, "hi"
		}, domain.ErrWritesDisabled},
		{"session checkpointed", func(t *testing.T, r *commentRig) (string, string) {
			r.serve(t, summonsA)
			r.session.snap.State = domain.StateCheckpointed
			return summonsA, "hi"
		}, domain.ErrNeedsHuman},
		{"no reply target (a top-level comment)", func(t *testing.T, r *commentRig) (string, string) {
			r.serve(t, summonsA)
			return "", "hi"
		}, domain.ErrNotASummons},
		{"malformed reply target", func(t *testing.T, r *commentRig) (string, string) {
			r.serve(t, summonsA)
			return "17900000000000001x", "hi"
		}, domain.ErrNotASummons},
		{"a comment id never served", func(t *testing.T, r *commentRig) (string, string) {
			r.serve(t, summonsA)
			return summonsB, "hi"
		}, domain.ErrNotASummons},
		{"served on another post", func(t *testing.T, r *commentRig) (string, string) {
			r.serve(t, summonsA)
			return summonsA, "hi" // asked on otherCode below
		}, domain.ErrNotASummons},
		{"served more than 7 days ago", func(t *testing.T, r *commentRig) (string, string) {
			r.serve(t, summonsA)
			r.clk.advance(domain.ServedWindow + time.Second)
			return summonsA, "hi"
		}, domain.ErrNotASummons},
		{"already replied", func(t *testing.T, r *commentRig) (string, string) {
			r.serve(t, summonsA)
			if _, err := r.reply(t, summonsA, "first"); err != nil {
				t.Fatal(err)
			}
			r.port.posts = 0
			r.clk.advance(time.Hour)
			return summonsA, "second"
		}, domain.ErrAlreadyReplied},
		{"blank text", func(t *testing.T, r *commentRig) (string, string) {
			r.serve(t, summonsA)
			return summonsA, " \n\t "
		}, domain.ErrInvalidCommentText},
		{"2201 characters", func(t *testing.T, r *commentRig) (string, string) {
			r.serve(t, summonsA)
			return summonsA, strings.Repeat("é", domain.MaxCommentRunes+1)
		}, domain.ErrInvalidCommentText},
		{"not UTF-8", func(t *testing.T, r *commentRig) (string, string) {
			r.serve(t, summonsA)
			return summonsA, "ok \xff"
		}, domain.ErrInvalidCommentText},
		{"daily cap", func(t *testing.T, r *commentRig) (string, string) {
			r.svc = r.build(CommentPolicy{WritesEnabled: true, DailyCap: 0, MinInterval: 0})
			r.serve(t, summonsA)
			return summonsA, "hi"
		}, domain.ErrDailyCap},
		{"too soon", func(t *testing.T, r *commentRig) (string, string) {
			r.serve(t, summonsA, summonsB)
			if _, err := r.reply(t, summonsA, "first"); err != nil {
				t.Fatal(err)
			}
			r.port.posts = 0
			r.clk.advance(89 * time.Second)
			return summonsB, "hi"
		}, domain.ErrTooSoon},
		{"ledger unreadable", func(t *testing.T, r *commentRig) (string, string) {
			if err := os.WriteFile(filepath.Join(r.dir, "comments.json"), []byte("{"), 0o600); err != nil {
				t.Fatal(err)
			}
			return summonsA, "hi"
		}, domain.ErrLedgerUnavailable},
	} {
		t.Run(tc.name, func(t *testing.T) {
			r := newCommentRig(t, openPolicy())
			to, text := tc.setup(t, r)
			on := postCode
			if tc.name == "served on another post" {
				on = otherCode
			}
			_, err := r.svc.PostComment(t.Context(), code(t, on), text, to)
			if !errors.Is(err, tc.want) {
				t.Fatalf("err = %v, want %v", err, tc.want)
			}
			if r.port.posts != 0 {
				t.Errorf("%d upstream write(s) after the refusal, want 0", r.port.posts)
			}
		})
	}
}

// The guards run in the documented order: when two would refuse, the earlier
// one answers.
func TestCommentGuardsRunInOrder(t *testing.T) {
	t.Run("writes off before session", func(t *testing.T) {
		r := newCommentRig(t, DefaultCommentPolicy())
		r.session.snap.State = domain.StateCheckpointed
		if _, err := r.reply(t, "", ""); !errors.Is(err, domain.ErrWritesDisabled) {
			t.Errorf("err = %v, want writes disabled", err)
		}
	})
	t.Run("session before summons", func(t *testing.T) {
		r := newCommentRig(t, openPolicy())
		r.session.snap.State = domain.StateLoggedOut
		if _, err := r.reply(t, "", ""); !errors.Is(err, domain.ErrNeedsHuman) {
			t.Errorf("err = %v, want needs human", err)
		}
	})
	t.Run("summons before text", func(t *testing.T) {
		r := newCommentRig(t, openPolicy())
		if _, err := r.reply(t, summonsA, ""); !errors.Is(err, domain.ErrNotASummons) {
			t.Errorf("err = %v, want not a summons", err)
		}
	})
	t.Run("once before text", func(t *testing.T) {
		r := newCommentRig(t, openPolicy())
		r.serve(t, summonsA)
		if _, err := r.reply(t, summonsA, "hi"); err != nil {
			t.Fatal(err)
		}
		if _, err := r.reply(t, summonsA, ""); !errors.Is(err, domain.ErrAlreadyReplied) {
			t.Errorf("err = %v, want already replied", err)
		}
	})
	t.Run("text before cap", func(t *testing.T) {
		r := newCommentRig(t, CommentPolicy{WritesEnabled: true, DailyCap: 0})
		r.serve(t, summonsA)
		if _, err := r.reply(t, summonsA, ""); !errors.Is(err, domain.ErrInvalidCommentText) {
			t.Errorf("err = %v, want invalid text", err)
		}
	})
	t.Run("cap before spacing", func(t *testing.T) {
		r := newCommentRig(t, CommentPolicy{WritesEnabled: true, DailyCap: 1, MinInterval: time.Hour})
		r.serve(t, summonsA, summonsB)
		if _, err := r.reply(t, summonsA, "hi"); err != nil {
			t.Fatal(err)
		}
		if _, err := r.reply(t, summonsB, "hi"); !errors.Is(err, domain.ErrDailyCap) {
			t.Errorf("err = %v, want daily cap", err)
		}
	})
}

// A reply that passes every guard goes out once, trimmed, and is recorded
// published with the new comment's id.
func TestAReplyIsPublishedAndRecorded(t *testing.T) {
	r := newCommentRig(t, openPolicy())
	r.serve(t, summonsA)
	got, err := r.reply(t, "0"+summonsA, "  is this true? no.  ")
	if err != nil {
		t.Fatal(err)
	}
	if got.ID != "18000000000000009" || got.ReplyToCommentID != summonsA {
		t.Errorf("posted = %+v", got)
	}
	if r.port.posts != 1 || r.port.lastTo != summonsA || r.port.lastTxt != "is this true? no." {
		t.Errorf("port saw %d call(s), to %q, text %q", r.port.posts, r.port.lastTo, r.port.lastTxt)
	}
	rep := r.ledger(t).Replies[summonsA]
	if rep.State != domain.ReplyPublished || rep.CommentID != "18000000000000009" || rep.MediaID != postMedia {
		t.Errorf("ledger reply = %+v, want published with the new id", rep)
	}
}

// The claim is on DISK before the upstream call starts, and a transport error
// after it (an ambiguous outcome) leaves it claimed: the second attempt is
// 409 and never reaches Instagram.
func TestTheClaimIsPersistedBeforeTheCallAndAnAmbiguousFailureStaysClaimed(t *testing.T) {
	r := newCommentRig(t, openPolicy())
	r.serve(t, summonsA)
	var during domain.ReplyState
	r.port.onPost = func() { during = r.ledger(t).Replies[summonsA].State }
	r.port.postErr = domain.Classify(domain.ErrUnavailable, errors.New("comment_add request failed before an answer"))

	if _, err := r.reply(t, summonsA, "hi"); err == nil {
		t.Fatal("want the transport error")
	}
	if during != domain.ReplyClaimed {
		t.Fatalf("state DURING the upstream call = %q, want claimed (persisted before the call)", during)
	}
	if st := r.ledger(t).Replies[summonsA].State; st != domain.ReplyClaimed {
		t.Errorf("state after an ambiguous failure = %q, want claimed", st)
	}

	r.clk.advance(time.Hour)
	r.port.postErr, r.port.onPost = nil, nil
	if _, err := r.reply(t, summonsA, "hi"); !errors.Is(err, domain.ErrAlreadyReplied) {
		t.Fatalf("second attempt err = %v, want already replied", err)
	}
	if r.port.posts != 1 {
		t.Errorf("%d upstream writes, want exactly 1: an ambiguous reply was retried", r.port.posts)
	}
}

// A refusal Instagram answered (ErrNotPosted) is recorded failed and is
// FINAL: the bridge never retries it, and neither may the caller, however
// long it waits.
func TestADefiniteRefusalIsFailedAndFinal(t *testing.T) {
	r := newCommentRig(t, openPolicy())
	r.serve(t, summonsA)
	r.port.postErr = domain.Classify(domain.ErrNotPosted, domain.Classify(domain.ErrUpstreamRejected, errors.New("comment_add returned a non-200: 400")))
	if _, err := r.reply(t, summonsA, "hi"); !errors.Is(err, domain.ErrUpstreamRejected) {
		t.Fatalf("err = %v", err)
	}
	if st := r.ledger(t).Replies[summonsA].State; st != domain.ReplyFailed {
		t.Errorf("state = %q, want failed", st)
	}
	r.port.postErr = nil
	r.clk.advance(3 * 24 * time.Hour)
	if _, err := r.reply(t, summonsA, "hi"); !errors.Is(err, domain.ErrAlreadyReplied) {
		t.Errorf("a re-ask days later err = %v, want already replied", err)
	}
	if r.port.posts != 1 {
		t.Errorf("%d upstream writes, want exactly 1: a refused write was retried", r.port.posts)
	}
}

// An action-block signal latches writes off, persisted: the write that drew
// it answers writes_blocked (never a retry-inviting status), every later
// write on ANY summons is refused with zero calls, and a restart does not
// clear it.
func TestAnActionBlockLatchesWritesOff(t *testing.T) {
	for _, reason := range []string{domain.BlockFeedbackRequired, domain.BlockRateLimited, domain.BlockSpam, domain.BlockSentry} {
		t.Run(reason, func(t *testing.T) {
			r := newCommentRig(t, openPolicy())
			r.serve(t, summonsA, summonsB)
			r.port.postErr = &domain.ActionBlocked{Reason: reason, Err: domain.Classify(domain.ErrUpstreamRejected, errors.New("comment_add returned a non-200: 400"))}
			_, err := r.reply(t, summonsA, "hi")
			if !errors.Is(err, domain.ErrWritesBlocked) {
				t.Fatalf("err = %v, want writes blocked", err)
			}
			if errors.Is(err, domain.ErrUnavailable) || errors.Is(err, domain.ErrUpstreamRejected) {
				t.Errorf("err %v still carries a retry-inviting kind", err)
			}
			led := r.ledger(t)
			if led.Blocked == nil || led.Blocked.Reason != reason || led.Replies[summonsA].State != domain.ReplyFailed {
				t.Fatalf("ledger latch %+v reply %+v", led.Blocked, led.Replies[summonsA])
			}
			r.port.postErr = nil
			r.clk.advance(48 * time.Hour)
			r.serve(t, summonsC)
			r.svc = r.build(openPolicy()) // a restart
			for _, to := range []string{summonsB, summonsC} {
				if _, err := r.reply(t, to, "hi"); !errors.Is(err, domain.ErrWritesBlocked) {
					t.Errorf("a later write on another summons err = %v, want writes blocked", err)
				}
			}
			if r.port.posts != 1 {
				t.Errorf("%d upstream writes, want 1: the latch let a write through", r.port.posts)
			}
		})
	}
}

// A mention GET /mentions serves with no date is never recorded, so it can
// never be answered; and a reply record is never pruned, so a summons the
// feed shows again long after is still answered.
func TestNoReReplyAfterPruning(t *testing.T) {
	r := newCommentRig(t, openPolicy())
	r.social.mentions = []domain.CommentMention{{CommentID: summonsA, MediaID: postMedia}}
	if _, _, err := r.svc.ReadMentions(t.Context()); err != nil {
		t.Fatal(err)
	}
	if _, ok := r.ledger(t).Served[summonsA]; ok {
		t.Fatal("a mention with no date was recorded as a summons")
	}
	if _, err := r.reply(t, summonsA, "hi"); !errors.Is(err, domain.ErrNotASummons) {
		t.Errorf("reply to an undated mention err = %v, want not a summons", err)
	}

	r.serve(t, summonsB)
	if _, err := r.reply(t, summonsB, "hi"); err != nil {
		t.Fatal(err)
	}
	// 40 days on, the served record is long pruned; the feed shows the same
	// comment again, freshly dated.
	r.clk.advance(40 * 24 * time.Hour)
	r.serve(t, summonsC) // a write, which prunes B's 40-day-old served record
	if _, ok := r.ledger(t).Served[summonsB]; ok {
		t.Fatal("precondition: the served record was not pruned")
	}
	r.serve(t, summonsB) // recorded afresh
	if !r.ledger(t).IsSummons(mustID(t, summonsB), postMedia, r.clk.t) {
		t.Fatal("precondition: the re-served mention is not a live summons")
	}
	if _, err := r.reply(t, summonsB, "hi"); !errors.Is(err, domain.ErrAlreadyReplied) {
		t.Errorf("re-reply after pruning err = %v, want already replied", err)
	}
	if r.port.posts != 1 {
		t.Errorf("%d upstream writes, want 1", r.port.posts)
	}
}

// N concurrent requests for the same summons, against a port that blocks
// inside the upstream call: exactly one write leaves the bridge.
func TestConcurrentRepliesToOneSummonsWriteOnce(t *testing.T) {
	r := newCommentRig(t, openPolicy())
	r.serve(t, summonsA)
	release := make(cortez struct{})
	var mu sync.Mutex
	entered := 0
	r.port.onPost = func() {
		mu.Lock()
		entered++
		mu.Unlock()
		<-release
	}
	const n = 16
	errs := make(cortez error, n)
	var wg sync.WaitGroup
	for i := 0; i < n; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			_, err := r.svc.PostComment(context.Background(), code(t, postCode), "hi", summonsA)
			errs <- err
		}()
	}
	// Every loser returns without reaching the port; the winner is parked
	// inside it. Wait for the losers, then release the winner.
	deadline := time.After(5 * time.Second)
	for refused := 0; refused < n-1; {
		select {
		case err := <-errs:
			if !errors.Is(err, domain.ErrAlreadyReplied) {
				t.Errorf("a concurrent loser err = %v, want already replied", err)
			}
			refused++
		case <-deadline:
			close(release)
			t.Fatalf("only %d of %d concurrent requests were refused", refused, n-1)
		}
	}
	close(release)
	wg.Wait()
	if err := <-errs; err != nil {
		t.Errorf("the one winner failed: %v", err)
	}
	mu.Lock()
	defer mu.Unlock()
	if entered != 1 || r.port.posts != 1 {
		t.Errorf("%d request(s) entered the upstream call (%d posts), want exactly 1", entered, r.port.posts)
	}
}

// Idempotency survives a restart: a service over a FRESH ledger in the same
// directory refuses the answered summons with zero upstream calls.
func TestOncePerSummonsSurvivesARestart(t *testing.T) {
	r := newCommentRig(t, openPolicy())
	r.serve(t, summonsA)
	if _, err := r.reply(t, summonsA, "hi"); err != nil {
		t.Fatal(err)
	}
	r.port.posts = 0
	r.clk.advance(time.Hour)
	r.svc = r.build(openPolicy()) // the restart
	if _, err := r.reply(t, summonsA, "hi"); !errors.Is(err, domain.ErrAlreadyReplied) {
		t.Fatalf("after a restart err = %v, want already replied", err)
	}
	if r.port.posts != 0 {
		t.Errorf("%d upstream writes after the restart, want 0", r.port.posts)
	}
}

// The cap is ROLLING and counts every claim, a refused one included.
func TestTheDailyCapRollsAndCountsEveryClaim(t *testing.T) {
	r := newCommentRig(t, CommentPolicy{WritesEnabled: true, DailyCap: 2, MinInterval: 0})
	r.serve(t, summonsA, summonsB, summonsC, summonsD)

	// A definite refusal counts: Instagram saw the write.
	r.port.postErr = domain.Classify(domain.ErrNotPosted, errors.New("refused"))
	if _, err := r.reply(t, summonsA, "hi"); err == nil {
		t.Fatal("want the refusal")
	}
	r.port.postErr = nil
	r.clk.advance(time.Hour)
	if _, err := r.reply(t, summonsB, "hi"); err != nil {
		t.Fatal(err)
	}
	r.clk.advance(time.Hour)
	if _, err := r.reply(t, summonsC, "hi"); !errors.Is(err, domain.ErrDailyCap) {
		t.Fatalf("third reply in 24h err = %v, want daily cap", err)
	}
	// 24 hours after the FIRST reply, one slot frees; not before.
	r.clk.advance(22*time.Hour - time.Second)
	if _, err := r.reply(t, summonsC, "hi"); !errors.Is(err, domain.ErrDailyCap) {
		t.Fatalf("one second early err = %v, want daily cap", err)
	}
	r.clk.advance(2 * time.Second)
	if _, err := r.reply(t, summonsC, "hi"); err != nil {
		t.Fatalf("after the window rolled: %v", err)
	}
}

// The spacing is measured from the last claim, with the injected clock.
func TestTheMinimumIntervalIsMeasuredFromTheLastClaim(t *testing.T) {
	r := newCommentRig(t, openPolicy())
	r.serve(t, summonsA, summonsB)
	if _, err := r.reply(t, summonsA, "hi"); err != nil {
		t.Fatal(err)
	}
	r.clk.advance(90*time.Second - time.Nanosecond)
	if _, err := r.reply(t, summonsB, "hi"); !errors.Is(err, domain.ErrTooSoon) {
		t.Fatalf("err = %v, want too soon", err)
	}
	r.clk.advance(time.Nanosecond)
	if _, err := r.reply(t, summonsB, "hi"); err != nil {
		t.Fatalf("at exactly the interval: %v", err)
	}
}

// GET /mentions records what it serves, keeps the FIRST serve time, skips a
// mention the feed dates older than the window, and a broken ledger never
// fails the read.
func TestReadMentionsRecordsWhatItServes(t *testing.T) {
	r := newCommentRig(t, openPolicy())
	r.serve(t, summonsA)
	first := r.ledger(t).Served[summonsA]
	if first.MediaID != postMedia || !first.ServedAt.Equal(t0) {
		t.Fatalf("served = %+v", first)
	}
	r.clk.advance(time.Hour)
	r.serve(t, summonsA)
	if got := r.ledger(t).Served[summonsA]; !got.ServedAt.Equal(t0) {
		t.Errorf("served_at moved to %v on a later poll, want the first serve %v", got.ServedAt, t0)
	}

	r.social.mentions = []domain.CommentMention{{CommentID: summonsB, MediaID: postMedia, At: r.clk.t.Add(-domain.ServedWindow - time.Hour).Unix()}}
	if _, _, err := r.svc.ReadMentions(t.Context()); err != nil {
		t.Fatal(err)
	}
	if _, ok := r.ledger(t).Served[summonsB]; ok {
		t.Error("a mention dated older than the window was recorded as a live summons")
	}

	if err := os.WriteFile(filepath.Join(r.dir, "comments.json"), []byte("garbage"), 0o600); err != nil {
		t.Fatal(err)
	}
	r.social.mentions = []domain.CommentMention{{CommentID: summonsC, MediaID: postMedia}}
	got, _, err := r.svc.ReadMentions(t.Context())
	if err != nil || len(got) != 1 {
		t.Errorf("a broken ledger failed the read: %v, %d mention(s)", err, len(got))
	}
}

// The read: session gate first, and a shortcode with no media id decode is
// unsupported with no upstream call.
func TestReadCommentsGates(t *testing.T) {
	r := newCommentRig(t, openPolicy())
	r.session.snap.State = domain.StateCheckpointed
	if _, err := r.svc.ReadComments(t.Context(), code(t, postCode), nil); !errors.Is(err, domain.ErrNeedsHuman) {
		t.Errorf("err = %v, want needs human", err)
	}
	r.session.snap.State = domain.StateAuthenticated
	if _, err := r.svc.ReadComments(t.Context(), code(t, "ABCDEFGHIJKL"), nil); !errors.Is(err, domain.ErrUnsupported) {
		t.Errorf("err = %v, want unsupported", err)
	}
	if r.port.reads != 0 {
		t.Errorf("%d upstream reads, want 0", r.port.reads)
	}
	if _, err := New(r.session, r.social, r.social).ReadComments(t.Context(), code(t, postCode), nil); !errors.Is(err, domain.ErrUnsupported) {
		t.Errorf("unwired err = %v, want unsupported", err)
	}
	if _, err := New(r.session, r.social, r.social).PostComment(t.Context(), code(t, postCode), "hi", summonsA); !errors.Is(err, domain.ErrWritesDisabled) {
		t.Errorf("unwired write err = %v, want writes disabled", err)
	}
}
