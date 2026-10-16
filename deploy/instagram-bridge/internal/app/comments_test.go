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
	"strconv"
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

	// summoner is the igid of the account whose card and comment make a
	// summons; stranger is anybody else. selfName is this account's handle.
	summoner = "500000000001"
	stranger = "500000000002"
	selfName = "not_____nora"
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

// fakeComments is the Comments port. pages is each post's first comments
// page, by media pk; readErr, when set, is every read's answer. postErr, when
// set, is every write's answer; onPost runs INSIDE the upstream call, before
// it answers.
type fakeComments struct {
	mu      sync.Mutex
	posts   int
	reads   int
	pages   map[string][]domain.Comment
	readErr error
	// onRead runs INSIDE the comments read, before it answers.
	onRead  func()
	postErr error
	onPost  func()
	lastTo  string
	lastTxt string
}

func (f *fakeComments) Comments(_ context.Context, _ domain.Shortcode, media domain.MediaID, parent *domain.CommentID) (domain.CommentPage, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.reads++
	if f.onRead != nil {
		f.onRead()
	}
	if f.readErr != nil {
		return domain.CommentPage{}, f.readErr
	}
	if parent != nil {
		return domain.CommentPage{}, nil
	}
	return domain.CommentPage{Items: append([]domain.Comment(nil), f.pages[media.String()]...)}, nil
}

// onPage puts one comment on a post's first page.
func (f *fakeComments) onPage(media string, c domain.Comment) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.pages == nil {
		f.pages = map[string][]domain.Comment{}
	}
	f.pages[media] = append(f.pages[media], c)
}

func (f *fakeComments) PostComment(_ context.Context, _ domain.Shortcode, _ domain.MediaID, replyTo domain.CommentID, text string) (domain.PostedComment, error) {
	f.mu.Lock()
	f.posts++
	f.mu.Unlock()
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
	cards   int
}

func openPolicy() CommentPolicy {
	return CommentPolicy{WritesEnabled: true, DailyCap: 10, MinInterval: 90 * time.Second}
}

func newCommentRig(t *testing.T, policy CommentPolicy) *commentRig {
	t.Helper()
	r := &commentRig{
		port:   &fakeComments{},
		social: &fakePorts{},
		session: &fakeSession{snap: domain.Session{
			State: domain.StateAuthenticated, HasClient: true,
			Self: domain.Self{FBID: "18247361241432477", IGID: "79597894900", Username: selfName},
		}},
		clk: &clock{t: t0},
		dir: t.TempDir(),
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

// serve makes each comment id a summons on postMedia: a summons card from
// summoner reaches the bridge (RecordSummons, what the gateway's intake
// calls), and summoner's comment mentioning this account is on the post's
// first page.
func (r *commentRig) serve(t *testing.T, commentIDs ...string) {
	t.Helper()
	for _, id := range commentIDs {
		r.card(t, postMedia, summoner)
		r.port.onPage(postMedia, domain.Comment{ID: id, AuthorIGID: summoner, Text: "@" + selfName + " is this true?"})
	}
}

// card records one summons card for media from sender, received now, under
// a fresh message id.
func (r *commentRig) card(t *testing.T, media, sender string) {
	t.Helper()
	r.cards++
	n, err := strconv.ParseUint(media, 10, 64)
	if err != nil {
		t.Fatal(err)
	}
	m, _ := domain.MediaIDOf(n)
	igid, err := domain.ParseIGID(sender)
	if err != nil {
		t.Fatal(err)
	}
	if err := r.svc.RecordSummons(t.Context(), domain.Summons{
		MessageID: "mid.$card" + strconv.Itoa(r.cards), Media: m, Sender: igid, ReceivedAt: r.clk.t,
	}); err != nil {
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

// Every guard refuses with its own error and with ZERO upstream calls, read
// or write. (Guard 3(b), the one that reads, has its own test below.)
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
			r.port.posts, r.port.reads = 0, 0
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
			r.port.posts, r.port.reads = 0, 0
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
			// No guard in this table is 3(b), the one that reads: each
			// refuses before ANY call, the comments read included.
			if r.port.reads != 0 {
				t.Errorf("%d upstream read(s) after the refusal, want 0", r.port.reads)
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

// A reply record is never pruned, so a summons whose card is long pruned
// and whose comment draws a fresh card later is still answered only once.
func TestNoReReplyAfterPruning(t *testing.T) {
	r := newCommentRig(t, openPolicy())
	r.serve(t, summonsB)
	if _, err := r.reply(t, summonsB, "hi"); err != nil {
		t.Fatal(err)
	}
	// 40 days on, the card is long pruned; a fresh card arrives for the
	// same post and the same comment is still on the page.
	r.clk.advance(40 * 24 * time.Hour)
	r.card(t, otherMedia(t), summoner) // a write, which prunes B's 40-day-old card
	if len(r.ledger(t).SummonsSenders(postMedia, r.clk.t)) != 0 {
		t.Fatal("precondition: the old card was not pruned")
	}
	r.card(t, postMedia, summoner) // recorded afresh
	if !r.ledger(t).SummonsSenders(postMedia, r.clk.t)[summoner] {
		t.Fatal("precondition: the fresh card is not a live summons")
	}
	if _, err := r.reply(t, summonsB, "hi"); !errors.Is(err, domain.ErrAlreadyReplied) {
		t.Errorf("re-reply after pruning err = %v, want already replied", err)
	}
	if r.port.posts != 1 {
		t.Errorf("%d upstream writes, want 1", r.port.posts)
	}
}

func otherMedia(t *testing.T) string {
	t.Helper()
	m, err := code(t, otherCode).MediaID()
	if err != nil {
		t.Fatal(err)
	}
	return m.String()
}

// N concurrent requests for the same summons, against a port that blocks
// inside the upstream call: exactly one write leaves the bridge.
func TestConcurrentRepliesToOneSummonsWriteOnce(t *testing.T) {
	r := newCommentRig(t, openPolicy())
	r.serve(t, summonsA)
	release := make(chan struct{})
	var mu sync.Mutex
	entered := 0
	r.port.onPost = func() {
		mu.Lock()
		entered++
		mu.Unlock()
		<-release
	}
	const n = 16
	errs := make(chan error, n)
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

// GET /mentions is a read and nothing more: a mention it serves, dated now,
// on this post, authorizes no reply (guard 3 binds on the summons card).
func TestReadMentionsAuthorizesNothing(t *testing.T) {
	r := newCommentRig(t, openPolicy())
	r.social.mentions = []domain.CommentMention{{CommentID: summonsA, MediaID: postMedia, At: r.clk.t.Unix()}}
	r.port.onPage(postMedia, domain.Comment{ID: summonsA, AuthorIGID: summoner, Text: "@" + selfName + " hi"})
	if _, _, err := r.svc.ReadMentions(t.Context()); err != nil {
		t.Fatal(err)
	}
	if n := len(r.ledger(t).Summonses); n != 0 {
		t.Fatalf("GET /mentions recorded %d summons(es), want 0", n)
	}
	if _, err := r.reply(t, summonsA, "hi"); !errors.Is(err, domain.ErrNotASummons) {
		t.Errorf("reply to a served mention err = %v, want not a summons", err)
	}
	if r.port.posts != 0 || r.port.reads != 0 {
		t.Errorf("%d write(s), %d read(s), want 0 and 0", r.port.posts, r.port.reads)
	}
}

// RecordSummons keeps the FIRST record of a card, dates a future card now,
// refuses one beyond the window, and a broken ledger is an error, never a
// panic or a silent success.
func TestRecordSummons(t *testing.T) {
	r := newCommentRig(t, openPolicy())
	m, _ := code(t, postCode).MediaID()
	igid, _ := domain.ParseIGID(summoner)
	other, _ := domain.ParseIGID(stranger)
	card := domain.Summons{MessageID: "mid.$x", Media: m, Sender: igid, ReceivedAt: r.clk.t.Add(time.Minute)}
	if err := r.svc.RecordSummons(t.Context(), card); err != nil {
		t.Fatal(err)
	}
	got := r.ledger(t).Summonses["mid.$x"]
	if got.MediaID != postMedia || got.SenderIGID != summoner || !got.ReceivedAt.Equal(t0) {
		t.Fatalf("record = %+v, want media %s, sender %s, received now", got, postMedia, summoner)
	}
	card.Sender, card.ReceivedAt = other, r.clk.t.Add(-time.Hour)
	if err := r.svc.RecordSummons(t.Context(), card); err != nil {
		t.Fatal(err)
	}
	if got := r.ledger(t).Summonses["mid.$x"]; got.SenderIGID != summoner || !got.ReceivedAt.Equal(t0) {
		t.Errorf("a replayed card replaced the first record: %+v", got)
	}
	stale := domain.Summons{MessageID: "mid.$old", Media: m, Sender: igid, ReceivedAt: r.clk.t.Add(-domain.ServedWindow - time.Second)}
	if err := r.svc.RecordSummons(t.Context(), stale); err == nil {
		t.Error("a card dated beyond the window was recorded")
	}
	if err := r.svc.RecordSummons(t.Context(), domain.Summons{Media: m, Sender: igid}); err == nil {
		t.Error("a card with no message id was recorded")
	}
	if err := os.WriteFile(filepath.Join(r.dir, "comments.json"), []byte("garbage"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := r.svc.RecordSummons(t.Context(), domain.Summons{MessageID: "mid.$y", Media: m, Sender: igid}); !errors.Is(err, domain.ErrLedgerUnavailable) {
		t.Errorf("broken ledger err = %v, want ledger unavailable", err)
	}
}

// Guard 3, both halves: a reply goes out ONLY under a comment that (a) a
// live summons card for this post vouches for, and (b) the post's first
// page shows, written by that card's sender and mentioning this account.
// Every refusal is not_a_summons with ZERO writes; the (b) refusals cost
// exactly one read, the (a) refusals none.
func TestGuardThreeNeedsTheCardAndTheComment(t *testing.T) {
	mention := "@" + selfName + " is this true?"
	for _, tc := range []struct {
		name  string
		setup func(t *testing.T, r *commentRig)
		on    string
		reads int
		want  error
	}{
		{"card and comment agree", func(t *testing.T, r *commentRig) {
			r.card(t, postMedia, summoner)
			r.port.onPage(postMedia, domain.Comment{ID: summonsA, AuthorIGID: summoner, Text: mention})
		}, postCode, 1, nil},
		{"handle in another case and mid-sentence", func(t *testing.T, r *commentRig) {
			r.card(t, postMedia, summoner)
			r.port.onPage(postMedia, domain.Comment{ID: summonsA, AuthorIGID: "0" + summoner, Text: "so, @NOT_____NORA?"})
		}, postCode, 1, nil},
		{"no summons card at all", func(t *testing.T, r *commentRig) {
			r.port.onPage(postMedia, domain.Comment{ID: summonsA, AuthorIGID: summoner, Text: mention})
		}, postCode, 0, domain.ErrNotASummons},
		{"the card is for another post", func(t *testing.T, r *commentRig) {
			r.card(t, otherMedia(t), summoner)
			r.port.onPage(postMedia, domain.Comment{ID: summonsA, AuthorIGID: summoner, Text: mention})
		}, postCode, 0, domain.ErrNotASummons},
		{"the card is older than 7 days", func(t *testing.T, r *commentRig) {
			r.card(t, postMedia, summoner)
			r.port.onPage(postMedia, domain.Comment{ID: summonsA, AuthorIGID: summoner, Text: mention})
			r.clk.advance(domain.ServedWindow + time.Second)
		}, postCode, 0, domain.ErrNotASummons},
		{"the comment is not on the first page", func(t *testing.T, r *commentRig) {
			r.card(t, postMedia, summoner)
			r.port.onPage(postMedia, domain.Comment{ID: summonsB, AuthorIGID: summoner, Text: mention})
		}, postCode, 1, domain.ErrNotASummons},
		{"the comment's author is not the card's sender", func(t *testing.T, r *commentRig) {
			r.card(t, postMedia, summoner)
			r.port.onPage(postMedia, domain.Comment{ID: summonsA, AuthorIGID: stranger, Text: mention})
		}, postCode, 1, domain.ErrNotASummons},
		{"the comment has no author", func(t *testing.T, r *commentRig) {
			r.card(t, postMedia, summoner)
			r.port.onPage(postMedia, domain.Comment{ID: summonsA, Text: mention})
		}, postCode, 1, domain.ErrNotASummons},
		{"the comment does not mention this account", func(t *testing.T, r *commentRig) {
			r.card(t, postMedia, summoner)
			r.port.onPage(postMedia, domain.Comment{ID: summonsA, AuthorIGID: summoner, Text: "@" + selfName + "_fan is this true?"})
		}, postCode, 1, domain.ErrNotASummons},
		{"the comments read fails", func(t *testing.T, r *commentRig) {
			r.card(t, postMedia, summoner)
			r.port.onPage(postMedia, domain.Comment{ID: summonsA, AuthorIGID: summoner, Text: mention})
			r.port.readErr = domain.Classify(domain.ErrUnavailable, errors.New("media_comments request failed"))
		}, postCode, 1, domain.ErrNotASummons},
		{"own username unknown", func(t *testing.T, r *commentRig) {
			r.card(t, postMedia, summoner)
			r.port.onPage(postMedia, domain.Comment{ID: summonsA, AuthorIGID: summoner, Text: mention})
			r.session.snap.Self.Username = ""
		}, postCode, 0, domain.ErrNotASummons},
	} {
		t.Run(tc.name, func(t *testing.T) {
			r := newCommentRig(t, openPolicy())
			tc.setup(t, r)
			_, err := r.svc.PostComment(t.Context(), code(t, tc.on), "hi", summonsA)
			if tc.want == nil {
				if err != nil || r.port.posts != 1 || r.port.lastTo != summonsA {
					t.Fatalf("err = %v, %d write(s) to %q, want one write to the summons", err, r.port.posts, r.port.lastTo)
				}
				return
			}
			if !errors.Is(err, tc.want) {
				t.Fatalf("err = %v, want %v", err, tc.want)
			}
			if r.port.posts != 0 {
				t.Errorf("%d upstream write(s) after the refusal, want 0", r.port.posts)
			}
			if tc.name != "own username unknown" && r.port.reads != tc.reads {
				t.Errorf("%d upstream read(s), want %d", r.port.reads, tc.reads)
			}
			if _, ok := r.ledger(t).Replies[summonsA]; ok {
				t.Error("a refused reply was claimed")
			}
		})
	}
}

// The claim transaction checks the author against the ledger AGAIN: a card
// whose window closes while guard 3(b) reads the page authorizes nothing,
// even with another sender's card for the same post still live.
func TestTheClaimRechecksTheSummonsAfterTheRead(t *testing.T) {
	r := newCommentRig(t, openPolicy())
	r.serve(t, summonsA)
	r.clk.advance(time.Hour)
	r.card(t, postMedia, stranger)
	r.clk.advance(domain.ServedWindow - time.Hour - time.Second)
	r.port.onRead = func() { r.clk.advance(2 * time.Second) }
	if _, err := r.reply(t, summonsA, "hi"); !errors.Is(err, domain.ErrNotASummons) {
		t.Fatalf("err = %v, want not a summons", err)
	}
	if r.port.posts != 0 || r.port.reads != 1 {
		t.Errorf("%d write(s), %d read(s); want 0 and 1", r.port.posts, r.port.reads)
	}
}

// A card from ANOTHER sender for the same post authorizes only that
// sender's comments: two cards, two authors, each comment answerable, and a
// third person's comment on the same post is not.
func TestEachCardAuthorizesOnlyItsOwnSender(t *testing.T) {
	r := newCommentRig(t, CommentPolicy{WritesEnabled: true, DailyCap: 10})
	r.card(t, postMedia, summoner)
	r.port.onPage(postMedia, domain.Comment{ID: summonsA, AuthorIGID: summoner, Text: "@" + selfName})
	r.port.onPage(postMedia, domain.Comment{ID: summonsB, AuthorIGID: stranger, Text: "@" + selfName})
	if _, err := r.reply(t, summonsB, "hi"); !errors.Is(err, domain.ErrNotASummons) {
		t.Fatalf("the uncarded author's comment err = %v, want not a summons", err)
	}
	if _, err := r.reply(t, summonsA, "hi"); err != nil {
		t.Fatal(err)
	}
	r.card(t, postMedia, stranger)
	if _, err := r.reply(t, summonsB, "hi"); err != nil {
		t.Fatalf("after the second card: %v", err)
	}
	if r.port.posts != 2 {
		t.Errorf("%d writes, want 2", r.port.posts)
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
