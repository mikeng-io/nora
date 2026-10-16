// Package app is the bridge's use cases and the ports they need
// (design/instagram-adoption/16 §Layers, 17 P8).
//
// It imports the domain and nothing else of this module: no net/http, no
// instameow, no transport. A use case takes the caller's input as the caller
// gave it, applies the session gate, parses ids into their value objects,
// and calls a port. What comes back is a domain value or an error that is
// either a domain kind or one of the two stage errors below, which exist
// because the HTTP contract answers those two stages with their own bodies.
package app

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"time"

	"instagram-bridge/internal/domain"
)

// --- ports --------------------------------------------------------------------

// Session is what the use cases may ask about the one Instagram session.
type Session interface {
	Snapshot() domain.Session
}

// Messaging is the thread side of Instagram: resolve, send, react, read
// members.
type Messaging interface {
	ResolveThread(ctx context.Context, short domain.ThreadFBID) (domain.ThreadRef, error)
	SendText(ctx context.Context, thread domain.ThreadRef, text string, mentions []domain.Mention, replyTo string) (domain.Sent, error)
	SendMedia(ctx context.Context, thread domain.ThreadRef, media domain.Upload, replyTo string) (domain.Sent, error)
	React(ctx context.Context, thread domain.ThreadRef, messageID, emoji string, remove bool) (confirmed bool, err error)
	Typing(ctx context.Context, thread domain.ThreadRef, typing bool) error
	Unsend(ctx context.Context, thread domain.ThreadRef, messageID string) (confirmed bool, err error)
	Members(ctx context.Context, thread domain.ThreadRef) (domain.Thread, error)
}

// Social is the account side: who an account is, what it shows, its story,
// and finding one by free text.
type Social interface {
	Account(ctx context.Context, igid domain.IGID) (domain.Account, error)
	SocialProfile(ctx context.Context, igid domain.IGID) domain.SocialRead
	Story(ctx context.Context, igid domain.IGID) (domain.Story, error)
	// Items reads one page of an account's posts and reels, checked against
	// the igid. A zero username is resolved through the profile read; a
	// given one is trusted only as far as the page's owners agree with the
	// igid. `after` is "" for the newest page.
	Items(ctx context.Context, igid domain.IGID, username domain.Username, after string) (domain.TimelinePage, error)
	SearchAccounts(ctx context.Context, query string) ([]domain.AccountSummary, error)
	// Post reads one post by its shortcode, with every carousel item.
	Post(ctx context.Context, code domain.Shortcode) (domain.Post, error)
	// Mentions reads comment-mentions from Instagram's activity/notification
	// feed. The second return is a count of every story NOT classified as a
	// mention, keyed by its story_type.
	Mentions(ctx context.Context) ([]domain.CommentMention, map[int]int, error)
}

// --- stage errors -------------------------------------------------------------

// NotReadyError is the session gate refusing: the session is checkpointed or
// logged out, so a human must act before anything can succeed. It is
// ErrNeedsHuman, and the HTTP layer answers it with the session state and its
// detail rather than an error message.
type NotReadyError struct {
	State  domain.SessionState
	Detail string
}

func (e *NotReadyError) Error() string        { return string(e.State) }
func (e *NotReadyError) Is(target error) bool { return target == domain.ErrNeedsHuman }

// ThreadLookupError is the room key's route lookup failing against
// Instagram, as distinct from the verb that would have followed it.
type ThreadLookupError struct{ Err error }

func (e *ThreadLookupError) Error() string { return e.Err.Error() }
func (e *ThreadLookupError) Unwrap() error { return e.Err }

var (
	// ErrRebuilding is a session with no client yet: the supervisor is
	// (re)building one. Retryable.
	ErrRebuilding = domain.Classify(domain.ErrUnavailable, errors.New("session is being re-established"))
	// ErrSocketDown is the realtime socket being down, which only typing
	// needs. Retryable.
	ErrSocketDown = domain.Classify(domain.ErrUnavailable, errors.New("realtime socket is not connected"))
)

// --- the service --------------------------------------------------------------

// Service is the use cases.
type Service struct {
	session   Session
	messaging Messaging
	social    Social

	// The comment use cases (comments.go), set by WithComments. Nil comments
	// or ledger: the read is unsupported and every write is refused.
	comments Comments
	ledger   Ledger
	policy   CommentPolicy
	now      func() time.Time
}

// New wires the use cases to their ports.
func New(session Session, messaging Messaging, social Social) *Service {
	return &Service{session: session, messaging: messaging, social: social, now: time.Now}
}

// Health is the session as /health reports it. It never fails: a
// checkpointed session is a state to DESCRIBE, not an error.
func (s *Service) Health() domain.Session { return s.session.Snapshot() }

// ready is the session gate every Instagram-facing use case passes first.
//
// Checkpointed AND logged_out both mean "a human must act before this can
// succeed". It does NOT check the socket: only typing needs it, and the other
// verbs are GraphQL over plain HTTPS that work while the socket reconnects.
func (s *Service) ready() (domain.Session, error) {
	snap := s.session.Snapshot()
	if snap.State != domain.StateAuthenticated {
		return snap, &NotReadyError{State: snap.State, Detail: snap.Detail}
	}
	if !snap.HasClient {
		return snap, ErrRebuilding
	}
	return snap, nil
}

// thread resolves both thread ids for a room key the HTTP edge has ALREADY
// parsed.
//
// The parse moved out of here in P14 (design/instagram-adoption/17 §P14):
// every route now parses its path ids before any use case runs, so a
// malformed id is 400 whatever the session's state (16 §Resources: "a
// malformed id is 400 before any use case runs"). Before P14 the parse sat
// after the session gate and a checkpointed session answered 409 ahead of a
// malformed id's 400. A malformed key still never becomes a route lookup
// against Instagram: it cannot reach this function as a value at all.
func (s *Service) thread(ctx context.Context, room domain.ThreadFBID) (domain.ThreadRef, error) {
	thread, err := s.messaging.ResolveThread(ctx, room)
	if err != nil {
		return domain.ThreadRef{}, &ThreadLookupError{Err: err}
	}
	return thread, nil
}

// SendText posts a text message. No length cap: 20,000 characters were
// measured accepted on this wire, and mautrix-meta's 1000 is its own policy.
func (s *Service) SendText(ctx context.Context, room domain.ThreadFBID, text string, mentions []domain.Mention, replyTo string) (domain.Sent, error) {
	if _, err := s.ready(); err != nil {
		return domain.Sent{}, err
	}
	thread, err := s.thread(ctx, room)
	if err != nil {
		return domain.Sent{}, err
	}
	return s.messaging.SendText(ctx, thread, text, mentions, replyTo)
}

// SendMedia sends one attachment. It carries NO text: the wire has no
// caption field, so the caller sends the text first, then the media.
func (s *Service) SendMedia(ctx context.Context, room domain.ThreadFBID, media domain.Upload, replyTo string) (domain.Sent, error) {
	if _, err := s.ready(); err != nil {
		return domain.Sent{}, err
	}
	thread, err := s.thread(ctx, room)
	if err != nil {
		return domain.Sent{}, err
	}
	return s.messaging.SendMedia(ctx, thread, media, replyTo)
}

// React posts or retracts a reaction. `confirmed` false is a degraded
// success: the mutation was accepted and only its post-state stayed silent.
func (s *Service) React(ctx context.Context, room domain.ThreadFBID, messageID domain.MessageID, emoji string, remove bool) (bool, error) {
	if _, err := s.ready(); err != nil {
		return false, err
	}
	thread, err := s.thread(ctx, room)
	if err != nil {
		return false, err
	}
	return s.messaging.React(ctx, thread, messageID.String(), emoji, remove)
}

// Typing is the one use case that needs the realtime socket: SetTyping
// publishes over the MQTT-bypass stream.
func (s *Service) Typing(ctx context.Context, room domain.ThreadFBID, typing bool) error {
	snap, err := s.ready()
	if err != nil {
		return err
	}
	if !snap.Connected {
		return ErrSocketDown
	}
	thread, err := s.thread(ctx, room)
	if err != nil {
		return err
	}
	return s.messaging.Typing(ctx, thread, typing)
}

// Unsend retracts a message and REPORTS, rather than decides, what the
// mutation said: `direct_unsend_message` has never been observed against an
// unsend known to have failed, and a false failure strands probe messages in
// a real group.
func (s *Service) Unsend(ctx context.Context, room domain.ThreadFBID, messageID domain.MessageID) (bool, error) {
	if _, err := s.ready(); err != nil {
		return false, err
	}
	thread, err := s.thread(ctx, room)
	if err != nil {
		return false, err
	}
	confirmed, err := s.messaging.Unsend(ctx, thread, messageID.String())
	if err == nil && !confirmed {
		slog.WarnContext(ctx, "unsend returned direct_unsend_message=false; "+
			"the message may still be in the thread", "room", room.String())
	}
	return confirmed, err
}

// Members reads who is in one thread, never short and never empty. The room
// key arrives parsed, and so already canonical: it is the key the answer is
// about.
func (s *Service) Members(ctx context.Context, room domain.ThreadFBID) (domain.Thread, error) {
	if _, err := s.ready(); err != nil {
		return domain.Thread{}, err
	}
	thread, err := s.thread(ctx, room)
	if err != nil {
		return domain.Thread{}, err
	}
	return s.messaging.Members(ctx, thread)
}

// AccountRead is one account and the outcome of its second, independent read.
type AccountRead struct {
	Account domain.Account
	Social  domain.SocialRead
}

// ReadAccount reads one account by igid, then its social half keyed on the
// SAME parsed igid. The social half fails on its own: GetProfile has already
// answered, so a social failure degrades the read rather than losing it.
//
// It takes the PARSED igid (P14): the HTTP edge parses it, so a malformed one
// is 400 before the session gate.
func (s *Service) ReadAccount(ctx context.Context, id domain.IGID) (AccountRead, error) {
	if _, err := s.ready(); err != nil {
		return AccountRead{}, err
	}
	acct, err := s.social.Account(ctx, id)
	if err != nil {
		return AccountRead{}, err
	}
	return AccountRead{Account: acct, Social: s.social.SocialProfile(ctx, id)}, nil
}

// ReadStory reads one account's current story — ONE igid, because a batch
// would open stories nobody asked about. The social stream's poller reads
// members' stories, each only when its probe moved
// (design/cognition-loop/22-social-stream.md §3); the read never marks a
// story seen (§1).
//
// Like ReadAccount, it takes the igid already parsed at the edge.
func (s *Service) ReadStory(ctx context.Context, id domain.IGID) (domain.Story, error) {
	if _, err := s.ready(); err != nil {
		return domain.Story{}, err
	}
	return s.social.Story(ctx, id)
}

// ReadItems reads one page of an account's posts and reels (`GET
// /accounts/{igid}/items`, design/cognition-loop/22-social-stream.md §1a,
// §7). The timeline document takes a USERNAME: the caller's, parsed at the
// edge, when it gave one (a back-fill paging on, one upstream call a page);
// otherwise the gateway resolves it through the same profile read `GET
// /accounts/{igid}` makes. Either way the page's owners are checked against
// the igid, because a username can change hands.
//
// `after` is the caller's opaque cursor, "" for the newest page; the edge
// bounds it.
func (s *Service) ReadItems(
	ctx context.Context, id domain.IGID, username domain.Username, after string,
) (domain.TimelinePage, error) {
	if _, err := s.ready(); err != nil {
		return domain.TimelinePage{}, err
	}
	return s.social.Items(ctx, id, username, after)
}

// SearchAccounts searches Instagram accounts by free text. Session gate
// first, as every other Instagram-facing use case (`ready` runs before
// anything else). The query is not parsed into a value object: it is free
// text a person typed, not one of the ids that name an account.
func (s *Service) SearchAccounts(ctx context.Context, query string) ([]domain.AccountSummary, error) {
	if _, err := s.ready(); err != nil {
		return nil, err
	}
	return s.social.SearchAccounts(ctx, query)
}

// LookupAccountByUsername resolves a username to at most one account
// (design/instagram-adoption/16 §Resources, 17 P9, P10).
//
// It calls the SAME search port `SearchAccounts` uses (SearchUsers, over the
// DM contact picker's search), then filters the raw hits to an exact,
// case-insensitive username match. `items[0]` is never taken: P9 measured a
// real case where the exact match was not the first result (17 §P9), so
// ranking cannot stand in for identity here. The filter runs HERE, in the
// use case, not in the transport layer, so the rule is provable independent
// of the HTTP shape around it.
//
// # The match is pattern-checked before it is ever fold-compared
//
// A hit whose username does not itself parse as a `domain.Username`
// (`ParseUsername`'s `[A-Za-z0-9._]{1,30}` pattern) is skipped, never
// compared. This is load-bearing, not a defensive extra: `strings.EqualFold`
// folds by full Unicode case-folding rules, which fold characters OUTSIDE
// that pattern onto ones inside it — U+212A KELVIN SIGN folds to `k`, U+017F
// LATIN SMALL LETTER LONG S folds to `s`. An upstream hit spelled with one of
// those would read as an exact, case-insensitive match for an ASCII `k` or
// `s` username it is not, which is a spoofable identity check, not a
// case-insensitive one. Checking the pattern first rules that out
// categorically: `asciiEqualFold`, which runs afterward, only ever sees two
// strings already known to contain nothing but `[A-Za-z0-9._]`.
//
// Zero matches is a normal empty answer, not ErrNotFound: P9 also measured
// zero items for a username that does not exist, and the search returning
// nothing for a real one it simply missed would look identical, so there is
// no way to tell "no such account" from "the search didn't surface it" —
// answering ErrNotFound would claim a certainty the mechanism does not have.
// More than one match cannot happen for a genuine username, since usernames
// are unique on Instagram, so it means the upstream data disagreed with
// itself: ErrUpstreamRejected, which the transport maps to 502.
func (s *Service) LookupAccountByUsername(ctx context.Context, username domain.Username) ([]domain.AccountSummary, error) {
	if _, err := s.ready(); err != nil {
		return nil, err
	}
	hits, err := s.social.SearchAccounts(ctx, username.String())
	if err != nil {
		return nil, err
	}
	want := username.String()
	matches := make([]domain.AccountSummary, 0, 1)
	for _, hit := range hits {
		hitUsername, err := domain.ParseUsername(hit.Username)
		if err != nil {
			// Not a pattern-conforming username: never an exact match,
			// whatever a Unicode-aware fold would say about it. See this
			// function's doc comment.
			continue
		}
		if asciiEqualFold(hitUsername.String(), want) {
			matches = append(matches, hit)
		}
	}
	if len(matches) > 1 {
		// The count is safe to say; the username is not (it is never put in
		// this error's text, by construction — `want` never appears above).
		return nil, domain.Classify(domain.ErrUpstreamRejected,
			fmt.Errorf("username search matched %d accounts, want at most 1", len(matches)))
	}
	return matches, nil
}

// asciiEqualFold reports whether a and b are equal under ASCII-only case
// folding: it upper/lower-cases only the byte range `A`-`Z`/`a`-`z` and
// compares byte for byte. LookupAccountByUsername calls it ONLY after both
// strings have already passed `domain.ParseUsername`'s pattern
// (`[A-Za-z0-9._]{1,30}`), so both are already known to be pure ASCII —
// but the point of this function, rather than `strings.EqualFold`, is that
// it makes that safe even if that invariant is ever weakened: it has no
// Unicode fold table to exploit, so a homoglyph can never make it answer
// true for two strings that are not the same ASCII bytes up to case.
func asciiEqualFold(a, b string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := 0; i < len(a); i++ {
		ca, cb := a[i], b[i]
		if 'A' <= ca && ca <= 'Z' {
			ca += 'a' - 'A'
		}
		if 'A' <= cb && cb <= 'Z' {
			cb += 'a' - 'A'
		}
		if ca != cb {
			return false
		}
	}
	return true
}

// ReadPost reads one post by its shortcode (17 P12).
//
// It takes the PARSED shortcode: the HTTP layer parses it at the edge, so a
// malformed one is 400 before this runs, whatever the session's state, as
// `GET /accounts?query=` does. The session gate comes next, before anything
// reaches Instagram.
func (s *Service) ReadPost(ctx context.Context, code domain.Shortcode) (domain.Post, error) {
	if _, err := s.ready(); err != nil {
		return domain.Post{}, err
	}
	return s.social.Post(ctx, code)
}

// ReadMentions reads comment-mentions from Instagram's activity/notification
// feed (`GET /mentions`). Session gate first, like every other
// Instagram-facing use case.
//
// It is a READ and nothing more. It used to record what it served as the
// authority for a comment reply; the 2026-09-26 re-measure (design 19)
// found the feed never carried a comment-mention, and guard 3 now binds on
// the summons card the DM intake records (comments.go's RecordSummons).
func (s *Service) ReadMentions(ctx context.Context) ([]domain.CommentMention, map[int]int, error) {
	if _, err := s.ready(); err != nil {
		return nil, nil, err
	}
	return s.social.Mentions(ctx)
}
