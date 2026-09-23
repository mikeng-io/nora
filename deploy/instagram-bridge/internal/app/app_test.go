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
	resolved   []string
	accounts   []string
	socials    []string
	stories    []string
	searches   []string
	typed      int
	lookupErr  error
	unsendOK   bool
	searchErr  error
	searchHits []domain.AccountSummary
	posts      []string
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

func (f *fakePorts) React(context.Context, domain.ThreadRef, string, string, bool) (bool, error) {
	return true, nil
}

func (f *fakePorts) Typing(context.Context, domain.ThreadRef, bool) error {
	f.typed++
	return nil
}

func (f *fakePorts) Unsend(context.Context, domain.ThreadRef, string) (bool, error) {
	return f.unsendOK, nil
}

func (f *fakePorts) Members(context.Context, domain.ThreadRef) (domain.Thread, error) {
	return domain.Thread{Title: "the group", Members: []domain.Member{{FBID: "110966695387753"}}}, nil
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

func (f *fakePorts) SearchAccounts(_ context.Context, query string) ([]domain.AccountSummary, error) {
	f.searches = append(f.searches, query)
	if f.searchErr != nil {
		return nil, f.searchErr
	}
	if f.searchHits != nil {
		return f.searchHits, nil
	}
	return []domain.AccountSummary{{IGID: "221731317", Username: "nka_bpumvg"}}, nil
}

func (f *fakePorts) Post(_ context.Context, code domain.Shortcode) (domain.Post, error) {
	f.posts = append(f.posts, code.String())
	return domain.Post{Shortcode: code.String(), Items: []domain.PostItem{{Kind: "image"}}}, nil
}

func ready() *fakeSession {
	return &fakeSession{snap: domain.Session{State: domain.StateAuthenticated, Connected: true, HasClient: true}}
}

func service(s *fakeSession, p *fakePorts) *Service { return New(s, p, p) }

// The session gate runs BEFORE the id is parsed: a checkpointed session is
// "a human must act", whatever the id looks like, and nothing reaches a port.
func TestTheSessionGateComesBeforeTheIDParse(t *testing.T) {
	for _, state := range []domain.SessionState{domain.StateCheckpointed, domain.StateLoggedOut} {
		p := &fakePorts{}
		svc := service(&fakeSession{snap: domain.Session{State: state, Detail: "captcha", HasClient: true}}, p)
		checks := map[string]error{}
		_, checks["send"] = svc.SendText(t.Context(), "not-a-thread", "hi", nil, "")
		_, _, checks["members"] = svc.Members(t.Context(), "not-a-thread")
		_, checks["account"] = svc.ReadAccount(t.Context(), "nka_bpumvg")
		_, checks["story"] = svc.ReadStory(t.Context(), "nka_bpumvg")
		_, checks["search"] = svc.SearchAccounts(t.Context(), "someone")
		_, checks["lookup_by_username"] = svc.LookupAccountByUsername(t.Context(), mustUsername(t, "someone"))
		_, checks["post"] = svc.ReadPost(t.Context(), mustShortcode(t, "CmiJBr2LAAB"))
		checks["typing"] = svc.Typing(t.Context(), "not-a-thread", true)
		for name, err := range checks {
			var nr *NotReadyError
			if !errors.As(err, &nr) || nr.State != state || nr.Detail != "captcha" {
				t.Errorf("%s/%s: err = %v, want NotReadyError(%s)", state, name, err, state)
			}
			if !errors.Is(err, domain.ErrNeedsHuman) {
				t.Errorf("%s/%s: a refused session is not ErrNeedsHuman", state, name)
			}
		}
		if len(p.resolved)+len(p.accounts)+len(p.stories)+len(p.searches)+len(p.posts)+p.typed != 0 {
			t.Errorf("%s: a port was reached while the session needs a human", state)
		}
	}
}

// A session with no client yet is retryable, and distinct from one a human
// must clear.
func TestASessionBeingRebuiltIsUnavailable(t *testing.T) {
	svc := service(&fakeSession{snap: domain.Session{State: domain.StateAuthenticated}}, &fakePorts{})
	_, err := svc.SendText(t.Context(), "1009543911792316", "hi", nil, "")
	if !errors.Is(err, ErrRebuilding) || !errors.Is(err, domain.ErrUnavailable) {
		t.Errorf("err = %v, want ErrRebuilding (ErrUnavailable)", err)
	}
}

// A malformed id is refused before a port: the caller's 400, never a lookup.
func TestAMalformedIDNeverReachesAPort(t *testing.T) {
	p := &fakePorts{}
	svc := service(ready(), p)
	if _, err := svc.SendText(t.Context(), "340282366841710301281152937372201004392", "hi", nil, ""); !errors.Is(err, domain.ErrInvalidID) {
		t.Errorf("a long thread id as a room key gave %v, want ErrInvalidID", err)
	}
	if _, err := svc.ReadAccount(t.Context(), "nka_bpumvg"); !errors.Is(err, domain.ErrInvalidID) {
		t.Errorf("a username as an igid gave %v, want ErrInvalidID", err)
	}
	if len(p.resolved)+len(p.accounts) != 0 {
		t.Errorf("a malformed id reached a port: %v %v", p.resolved, p.accounts)
	}
}

// Both halves of an account read are keyed on the SAME parsed, canonical
// igid. The social half keyed on the username was a real bug (2026-09-22).
func TestBothHalvesOfAnAccountReadUseTheParsedIGID(t *testing.T) {
	p := &fakePorts{}
	read, err := service(ready(), p).ReadAccount(t.Context(), "0221731317")
	if err != nil {
		t.Fatal(err)
	}
	if len(p.accounts) != 1 || p.accounts[0] != "221731317" ||
		len(p.socials) != 1 || p.socials[0] != "221731317" {
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
	if err := svc.Typing(t.Context(), "1009543911792316", true); !errors.Is(err, ErrSocketDown) {
		t.Errorf("typing with the socket down gave %v, want ErrSocketDown", err)
	}
	if p.typed != 0 {
		t.Error("typing reached the port with the socket down")
	}
	if _, err := svc.SendText(t.Context(), "1009543911792316", "hi", nil, ""); err != nil {
		t.Errorf("a send with the socket down failed: %v", err)
	}
}

// A lookup that failed against Instagram is marked as a lookup failure, and
// keeps its kind, so a checkpoint discovered there still reads as one.
func TestAFailedThreadLookupKeepsItsKind(t *testing.T) {
	p := &fakePorts{lookupErr: domain.Classify(domain.ErrNeedsHuman, errors.New("checkpoint"))}
	_, err := service(ready(), p).SendText(t.Context(), "1009543911792316", "hi", nil, "")
	var lookup *ThreadLookupError
	if !errors.As(err, &lookup) || !errors.Is(err, domain.ErrNeedsHuman) {
		t.Errorf("err = %v, want a ThreadLookupError that is ErrNeedsHuman", err)
	}
}

// Members answers with the canonical room key the answer is about.
func TestMembersEchoesTheCanonicalRoom(t *testing.T) {
	room, thread, err := service(ready(), &fakePorts{}).Members(t.Context(), "01009543911792316")
	if err != nil || room.String() != "1009543911792316" || len(thread.Members) != 1 {
		t.Errorf("Members = %v, %+v, %v", room, thread, err)
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
