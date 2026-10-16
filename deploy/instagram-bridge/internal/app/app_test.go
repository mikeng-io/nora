package app

// The use cases against fake ports: the seam the design asks for, where PR
// #387 had to bolt one on. Nothing here knows Instagram exists.

import (
	"context"
	"errors"
	"testing"

	"instagram-bridge/internal/domain"
)

type fakeSession struct{ snap domain.Session }

func (f *fakeSession) Snapshot() domain.Session { return f.snap }

// fakePorts records what reached the ports. Every method it does not care
// about is a failure: a use case that calls something it should not is a
// test failure, not a silent pass.
type fakePorts struct {
	resolved        []string
	accounts        []string
	socials         []string
	stories         []string
	items           []string
	searches        []string
	typed           int
	lookupErr       error
	unsendOK        bool
	searchErr       error
	searchHits      []domain.AccountSummary
	posts           []string
	reactedID       string
	unsentID        string
	mentionsCalls   int
	mentions        []domain.CommentMention
	mentionsSkipped map[int]int
	mentionsErr     error
}

func (f *fakePorts) ResolveThread(_ context.Context, short domain.ThreadFBID) (domain.ThreadRef, error) {
	f.resolved = append(f.resolved, short.String())
	if f.lookupErr != nil {
		return domain.ThreadRef{}, f.lookupErr
	}
	long, _ := domain.NewThreadIGID("340282366841710301281152937372201004392")
	return domain.ThreadRef{Short: short, Long: long}, nil
}

func (f *fakePorts) SendText(context.Context, domain.ThreadRef, string, []domain.Mention, string) (domain.Sent, error) {
	return domain.Sent{ID: "mid.$sent"}, nil
}

func (f *fakePorts) SendMedia(context.Context, domain.ThreadRef, domain.Upload, string) (domain.Sent, error) {
	return domain.Sent{ID: "mid.$media"}, nil
}

func (f *fakePorts) React(_ context.Context, _ domain.ThreadRef, messageID, _ string, _ bool) (bool, error) {
	f.reactedID = messageID
	return true, nil
}

func (f *fakePorts) Typing(context.Context, domain.ThreadRef, bool) error {
	f.typed++
	return nil
}

func (f *fakePorts) Unsend(_ context.Context, _ domain.ThreadRef, messageID string) (bool, error) {
	f.unsentID = messageID
	return f.unsendOK, nil
}

func (f *fakePorts) Members(context.Context, domain.ThreadRef) (domain.Thread, error) {
	return domain.Thread{Title: "the group", Members: []domain.Member{{FBID: "186217604552756"}}}, nil
}

func (f *fakePorts) Account(_ context.Context, igid domain.IGID) (domain.Account, error) {
	f.accounts = append(f.accounts, igid.String())
	return domain.Account{IGID: igid.String(), Username: "nka_bpumvg"}, nil
}

func (f *fakePorts) SocialProfile(_ context.Context, igid domain.IGID) domain.SocialRead {
	f.socials = append(f.socials, igid.String())
	return domain.SocialRead{Sent: true, Status: 200, Profile: &domain.SocialProfile{IsPrivate: true}}
}

func (f *fakePorts) Story(_ context.Context, igid domain.IGID) (domain.Story, error) {
	f.stories = append(f.stories, igid.String())
	return domain.Story{IGID: igid.String()}, nil
}

func (f *fakePorts) Items(
	_ context.Context, igid domain.IGID, username domain.Username, after string,
) (domain.TimelinePage, error) {
	f.items = append(f.items, igid.String()+"/"+username.String()+"/"+after)
	return domain.TimelinePage{}, nil
}

func (f *fakePorts) SearchAccounts(_ context.Context, query string) ([]domain.AccountSummary, error) {
	f.searches = append(f.searches, query)
	if f.searchErr != nil {
		return nil, f.searchErr
	}
	if f.searchHits != nil {
		return f.searchHits, nil
	}
	return []domain.AccountSummary{{IGID: "246967358", Username: "nka_bpumvg"}}, nil
}

func (f *fakePorts) Post(_ context.Context, code domain.Shortcode) (domain.Post, error) {
	f.posts = append(f.posts, code.String())
	return domain.Post{Shortcode: code.String(), Items: []domain.PostItem{{Kind: "image"}}}, nil
}

func (f *fakePorts) Mentions(context.Context) ([]domain.CommentMention, map[int]int, error) {
	f.mentionsCalls++
	if f.mentionsErr != nil {
		return nil, nil, f.mentionsErr
	}
	return f.mentions, f.mentionsSkipped, nil
}

func ready() *fakeSession {
	return &fakeSession{snap: domain.Session{State: domain.StateAuthenticated, Connected: true, HasClient: true}}
}

func service(s *fakeSession, p *fakePorts) *Service { return New(s, p, p) }

func mustThread(t *testing.T, s string) domain.ThreadFBID {
	t.Helper()
	id, err := domain.ParseThreadFBID(s)
	if err != nil {
		t.Fatal(err)
	}
	return id
}

func mustIGID(t *testing.T, s string) domain.IGID {
	t.Helper()
	id, err := domain.ParseIGID(s)
	if err != nil {
		t.Fatal(err)
	}
	return id
}

func mustMessageID(t *testing.T, s string) domain.MessageID {
	t.Helper()
	id, err := domain.ParseMessageID(s)
	if err != nil {
		t.Fatal(err)
	}
	return id
}

const synthRoom = "100000000000042"

// The session gate is the FIRST thing every Instagram-facing use case does:
// a checkpointed session is "a human must act", and nothing reaches a port.
// Since P14 the use cases take PARSED ids (the HTTP edge parses them, so a
// malformed id is 400 before this gate runs), so every case here hands in a
// well-formed id and the gate alone is what refuses.
func TestTheSessionGateComesFirstInEveryUseCase(t *testing.T) {
	for _, state := range []domain.SessionState{domain.StateCheckpointed, domain.StateLoggedOut} {
		p := &fakePorts{}
		svc := service(&fakeSession{snap: domain.Session{State: state, Detail: "captcha", HasClient: true}}, p)
		room, msg := mustThread(t, synthRoom), mustMessageID(t, "mid.$synthetic")
		checks := map[string]error{}
		_, checks["send"] = svc.SendText(t.Context(), room, "hi", nil, "")
		_, checks["send_media"] = svc.SendMedia(t.Context(), room, domain.Upload{Data: []byte("x")}, "")
		_, checks["react"] = svc.React(t.Context(), room, msg, "x", false)
		_, checks["unsend"] = svc.Unsend(t.Context(), room, msg)
		_, checks["members"] = svc.Members(t.Context(), room)
		_, checks["account"] = svc.ReadAccount(t.Context(), mustIGID(t, "10000001"))
		_, checks["story"] = svc.ReadStory(t.Context(), mustIGID(t, "10000001"))
		_, checks["items"] = svc.ReadItems(t.Context(), mustIGID(t, "10000001"), domain.Username{}, "")
		_, checks["search"] = svc.SearchAccounts(t.Context(), "someone")
		_, checks["lookup_by_username"] = svc.LookupAccountByUsername(t.Context(), mustUsername(t, "someone"))
		_, checks["post"] = svc.ReadPost(t.Context(), mustShortcode(t, "CmiJBr2LAAB"))
		checks["typing"] = svc.Typing(t.Context(), room, true)
		for name, err := range checks {
			var nr *NotReadyError
			if !errors.As(err, &nr) || nr.State != state || nr.Detail != "captcha" {
				t.Errorf("%s/%s: err = %v, want NotReadyError(%s)", state, name, err, state)
			}
			if !errors.Is(err, domain.ErrNeedsHuman) {
				t.Errorf("%s/%s: a refused session is not ErrNeedsHuman", state, name)
			}
		}
		if len(p.resolved)+len(p.accounts)+len(p.stories)+len(p.items)+len(p.searches)+len(p.posts)+p.typed != 0 {
			t.Errorf("%s: a port was reached while the session needs a human", state)
		}
	}
}

// A session with no client yet is retryable, and distinct from one a human
// must clear.
func TestASessionBeingRebuiltIsUnavailable(t *testing.T) {
	svc := service(&fakeSession{snap: domain.Session{State: domain.StateAuthenticated}}, &fakePorts{})
	_, err := svc.SendText(t.Context(), mustThread(t, synthRoom), "hi", nil, "")
	if !errors.Is(err, ErrRebuilding) || !errors.Is(err, domain.ErrUnavailable) {
		t.Errorf("err = %v, want ErrRebuilding (ErrUnavailable)", err)
	}
}

// Both halves of an account read are keyed on the SAME parsed, canonical
// igid. The social half keyed on the username was a real bug (2026-09-22).
func TestBothHalvesOfAnAccountReadUseTheParsedIGID(t *testing.T) {
	p := &fakePorts{}
	read, err := service(ready(), p).ReadAccount(t.Context(), mustIGID(t, "0246967358"))
	if err != nil {
		t.Fatal(err)
	}
	if len(p.accounts) != 1 || p.accounts[0] != "246967358" ||
		len(p.socials) != 1 || p.socials[0] != "246967358" {
		t.Fatalf("account read keyed on %v, social on %v; want the canonical igid for both",
			p.accounts, p.socials)
	}
	if read.Social.Profile == nil || !read.Social.Profile.IsPrivate {
		t.Errorf("the social half was lost: %+v", read.Social)
	}
}

// Typing is the only use case that needs the realtime socket.
func TestOnlyTypingNeedsTheSocket(t *testing.T) {
	s := ready()
	s.snap.Connected = false
	p := &fakePorts{}
	svc := service(s, p)
	if err := svc.Typing(t.Context(), mustThread(t, synthRoom), true); !errors.Is(err, ErrSocketDown) {
		t.Errorf("typing with the socket down gave %v, want ErrSocketDown", err)
	}
	if p.typed != 0 {
		t.Error("typing reached the port with the socket down")
	}
	if _, err := svc.SendText(t.Context(), mustThread(t, synthRoom), "hi", nil, ""); err != nil {
		t.Errorf("a send with the socket down failed: %v", err)
	}
}

// A lookup that failed against Instagram is marked as a lookup failure, and
// keeps its kind, so a checkpoint discovered there still reads as one.
func TestAFailedThreadLookupKeepsItsKind(t *testing.T) {
	p := &fakePorts{lookupErr: domain.Classify(domain.ErrNeedsHuman, errors.New("checkpoint"))}
	_, err := service(ready(), p).SendText(t.Context(), mustThread(t, synthRoom), "hi", nil, "")
	var lookup *ThreadLookupError
	if !errors.As(err, &lookup) || !errors.Is(err, domain.ErrNeedsHuman) {
		t.Errorf("err = %v, want a ThreadLookupError that is ErrNeedsHuman", err)
	}
}

// Members resolves the room it was handed, already canonical from the parse.
func TestMembersResolvesTheParsedRoom(t *testing.T) {
	p := &fakePorts{}
	thread, err := service(ready(), p).Members(t.Context(), mustThread(t, "0"+synthRoom))
	if err != nil || len(thread.Members) != 1 {
		t.Fatalf("Members = %+v, %v", thread, err)
	}
	if len(p.resolved) != 1 || p.resolved[0] != synthRoom {
		t.Errorf("resolved %v, want the canonical [%s]", p.resolved, synthRoom)
	}
}

// React and Unsend hand the port the message id they were given, unchanged.
func TestReactAndUnsendForwardTheMessageID(t *testing.T) {
	p := &fakePorts{}
	svc := service(ready(), p)
	msg := mustMessageID(t, "mid.$synthetic")
	if _, err := svc.React(t.Context(), mustThread(t, synthRoom), msg, "x", false); err != nil {
		t.Fatal(err)
	}
	if _, err := svc.Unsend(t.Context(), mustThread(t, synthRoom), msg); err != nil {
		t.Fatal(err)
	}
	if p.reactedID != msg.String() || p.unsentID != msg.String() {
		t.Errorf("port saw react=%q unsend=%q, want %q for both", p.reactedID, p.unsentID, msg)
	}
}

// SearchAccounts passes the query straight through — it is free text, not an
// id, so there is no parse step to prove separately from the session gate
// (already covered by TestTheSessionGateComesBeforeTheIDParse's "search" case).
func TestSearchAccountsPassesTheQueryThrough(t *testing.T) {
	p := &fakePorts{}
	hits, err := service(ready(), p).SearchAccounts(t.Context(), "someone")
	if err != nil {
		t.Fatal(err)
	}
	if len(p.searches) != 1 || p.searches[0] != "someone" {
		t.Fatalf("port was addressed with %v, want [someone]", p.searches)
	}
	if len(hits) != 1 || hits[0].Username != "nka_bpumvg" {
		t.Errorf("hits = %+v, want the port's fixed answer passed through unchanged", hits)
	}
}

func mustShortcode(t *testing.T, s string) domain.Shortcode {
	t.Helper()
	sc, err := domain.ParseShortcode(s)
	if err != nil {
		t.Fatal(err)
	}
	return sc
}

func mustUsername(t *testing.T, s string) domain.Username {
	t.Helper()
	u, err := domain.ParseUsername(s)
	if err != nil {
		t.Fatal(err)
	}
	return u
}

// LookupAccountByUsername calls the SAME port SearchAccounts does (the
// design's single search mechanism, 17 P9), addressed with the username's
// own text.
func TestLookupAccountByUsernameCallsSearchAccounts(t *testing.T) {
	p := &fakePorts{searchHits: []domain.AccountSummary{{IGID: "1", Username: "target_user"}}}
	_, err := service(ready(), p).LookupAccountByUsername(t.Context(), mustUsername(t, "target_user"))
	if err != nil {
		t.Fatal(err)
	}
	if len(p.searches) != 1 || p.searches[0] != "target_user" {
		t.Fatalf("port was addressed with %v, want [target_user]", p.searches)
	}
}

// The P9 case: the exact match is not the first hit, and items[0] is never
// taken (17 §P9: "one exact match was not the first result").
func TestLookupAccountByUsernameNeverTakesItemZero(t *testing.T) {
	p := &fakePorts{searchHits: []domain.AccountSummary{
		{IGID: "1", Username: "decoy_one"},
		{IGID: "2", Username: "decoy_two"},
		{IGID: "3", Username: "target_user"},
	}}
	matches, err := service(ready(), p).LookupAccountByUsername(t.Context(), mustUsername(t, "target_user"))
	if err != nil {
		t.Fatal(err)
	}
	if len(matches) != 1 || matches[0].IGID != "3" {
		t.Fatalf("matches = %+v, want exactly the exact match at rank 2", matches)
	}
}

// The exact match at rank 0 works too.
func TestLookupAccountByUsernameMatchAtRankZero(t *testing.T) {
	p := &fakePorts{searchHits: []domain.AccountSummary{
		{IGID: "1", Username: "target_user"},
		{IGID: "2", Username: "decoy"},
	}}
	matches, err := service(ready(), p).LookupAccountByUsername(t.Context(), mustUsername(t, "target_user"))
	if err != nil {
		t.Fatal(err)
	}
	if len(matches) != 1 || matches[0].IGID != "1" {
		t.Fatalf("matches = %+v, want the exact match", matches)
	}
}

// A case difference between the requested username and the hit still counts
// as an exact match.
func TestLookupAccountByUsernameIsCaseInsensitive(t *testing.T) {
	p := &fakePorts{searchHits: []domain.AccountSummary{{IGID: "1", Username: "Target_User"}}}
	matches, err := service(ready(), p).LookupAccountByUsername(t.Context(), mustUsername(t, "target_user"))
	if err != nil {
		t.Fatal(err)
	}
	if len(matches) != 1 || matches[0].Username != "Target_User" {
		t.Fatalf("matches = %+v, want the case-different exact match", matches)
	}
}

// No hit equals the input exactly: an empty, non-error answer. Fuzzy hits
// that merely contain the username do not count.
func TestLookupAccountByUsernameNoExactMatchIsEmpty(t *testing.T) {
	p := &fakePorts{searchHits: []domain.AccountSummary{
		{IGID: "1", Username: "target_userx"},
		{IGID: "2", Username: "xtarget_user"},
	}}
	matches, err := service(ready(), p).LookupAccountByUsername(t.Context(), mustUsername(t, "target_user"))
	if err != nil {
		t.Fatal(err)
	}
	if len(matches) != 0 {
		t.Errorf("matches = %+v, want none", matches)
	}
}

// A hit spelled with U+212A KELVIN SIGN, which Unicode case-folds to 'k',
// must never match an ASCII "k" username: strings.EqualFold would call it a
// match, and that is exactly the identity spoof LookupAccountByUsername's
// pattern check exists to rule out (see its doc comment). The hit is not
// pattern-conforming, so it is skipped entirely, not merely "not equal".
func TestLookupAccountByUsernameRejectsKelvinSignHomoglyph(t *testing.T) {
	const kelvinSign = "K" // KELVIN SIGN: strings.EqualFold("K", "K") is true
	p := &fakePorts{searchHits: []domain.AccountSummary{
		{IGID: "1", Username: kelvinSign + "elvin_user"},
	}}
	matches, err := service(ready(), p).LookupAccountByUsername(t.Context(), mustUsername(t, "kelvin_user"))
	if err != nil {
		t.Fatal(err)
	}
	if len(matches) != 0 {
		t.Fatalf("matches = %+v, want none: a Kelvin-sign homoglyph must never count as an exact match", matches)
	}
}

// A hit whose username does not itself parse as a domain.Username (here: a
// space, outside the pattern) is skipped rather than compared at all, and a
// genuine exact match sitting next to it is still returned.
func TestLookupAccountByUsernameSkipsANonPatternHitButKeepsTheValidOne(t *testing.T) {
	p := &fakePorts{searchHits: []domain.AccountSummary{
		{IGID: "1", Username: "target user"}, // a space: not [A-Za-z0-9._]
		{IGID: "2", Username: "target_user"},
	}}
	matches, err := service(ready(), p).LookupAccountByUsername(t.Context(), mustUsername(t, "target_user"))
	if err != nil {
		t.Fatal(err)
	}
	if len(matches) != 1 || matches[0].IGID != "2" {
		t.Fatalf("matches = %+v, want exactly the pattern-conforming exact match", matches)
	}
}

// Two exact, case-insensitive matches cannot happen for a real username, so
// it is ErrUpstreamRejected — the transport maps that to 502 — never a
// silent items[0] pick.
func TestLookupAccountByUsernameTwoExactMatchesIsUpstreamRejected(t *testing.T) {
	p := &fakePorts{searchHits: []domain.AccountSummary{
		{IGID: "1", Username: "target_user"},
		{IGID: "2", Username: "Target_User"},
	}}
	_, err := service(ready(), p).LookupAccountByUsername(t.Context(), mustUsername(t, "target_user"))
	if !errors.Is(err, domain.ErrUpstreamRejected) {
		t.Fatalf("err = %v, want ErrUpstreamRejected", err)
	}
}

// A session being rebuilt is retryable, and nothing reaches the port.
func TestLookupAccountByUsernameWhileRebuildingIsUnavailable(t *testing.T) {
	p := &fakePorts{}
	svc := service(&fakeSession{snap: domain.Session{State: domain.StateAuthenticated}}, p)
	if _, err := svc.LookupAccountByUsername(t.Context(), mustUsername(t, "target_user")); !errors.Is(err, ErrRebuilding) {
		t.Errorf("err = %v, want ErrRebuilding", err)
	}
	if len(p.searches) != 0 {
		t.Error("a lookup reached the port with no client")
	}
}

// ReadPost hands the port the SAME parsed shortcode it was given, once, and
// passes the port's answer through unchanged.
func TestReadPostPassesTheShortcodeThrough(t *testing.T) {
	p := &fakePorts{}
	post, err := service(ready(), p).ReadPost(t.Context(), mustShortcode(t, "CmiJBr2LAAB"))
	if err != nil {
		t.Fatal(err)
	}
	if len(p.posts) != 1 || p.posts[0] != "CmiJBr2LAAB" {
		t.Fatalf("port was addressed with %v, want [CmiJBr2LAAB]", p.posts)
	}
	if post.Shortcode != "CmiJBr2LAAB" || len(post.Items) != 1 {
		t.Errorf("post = %+v, want the port's answer unchanged", post)
	}
}

// A session being rebuilt is retryable for a post read too, and nothing
// reaches the port.
func TestReadPostWhileRebuildingIsUnavailable(t *testing.T) {
	p := &fakePorts{}
	svc := service(&fakeSession{snap: domain.Session{State: domain.StateAuthenticated}}, p)
	if _, err := svc.ReadPost(t.Context(), mustShortcode(t, "CmiJBr2LAAB")); !errors.Is(err, ErrRebuilding) {
		t.Errorf("err = %v, want ErrRebuilding", err)
	}
	if len(p.posts) != 0 {
		t.Error("a post read reached the port with no client")
	}
}

// ReadMentions passes the port's answer through unchanged, session gate
// first like every other Instagram-facing use case.
func TestReadMentionsPassesThePortsAnswerThrough(t *testing.T) {
	p := &fakePorts{
		mentions:        []domain.CommentMention{{StoryType: 3, CommentID: "1"}},
		mentionsSkipped: map[int]int{1: 2},
	}
	mentions, skipped, err := service(ready(), p).ReadMentions(t.Context())
	if err != nil {
		t.Fatal(err)
	}
	if p.mentionsCalls != 1 {
		t.Fatalf("port was called %d time(s), want 1", p.mentionsCalls)
	}
	if len(mentions) != 1 || mentions[0].CommentID != "1" {
		t.Errorf("mentions = %+v, want the port's answer unchanged", mentions)
	}
	if skipped[1] != 2 {
		t.Errorf("skipped = %+v, want the port's answer unchanged", skipped)
	}
}

// A checkpointed session refuses before the port is ever called.
func TestReadMentionsSessionGate(t *testing.T) {
	p := &fakePorts{}
	svc := service(&fakeSession{snap: domain.Session{State: domain.StateCheckpointed, Detail: "captcha"}}, p)
	if _, _, err := svc.ReadMentions(t.Context()); !errors.Is(err, domain.ErrNeedsHuman) {
		t.Errorf("err = %v, want ErrNeedsHuman", err)
	}
	if p.mentionsCalls != 0 {
		t.Error("a checkpointed session reached the port")
	}
}

// The use case hands the gateway the igid, the caller's username and cursor
// unchanged.
func TestReadItemsForwardsTheIGIDUsernameAndCursor(t *testing.T) {
	p := &fakePorts{}
	svc := service(&fakeSession{snap: domain.Session{State: domain.StateAuthenticated, Connected: true, HasClient: true}}, p)
	if _, err := svc.ReadItems(t.Context(), mustIGID(t, "10000001"), mustUsername(t, "someone"), "cursor-1"); err != nil {
		t.Fatal(err)
	}
	if len(p.items) != 1 || p.items[0] != "10000001/someone/cursor-1" {
		t.Errorf("timeline reads = %v, want igid/username/cursor", p.items)
	}
}
