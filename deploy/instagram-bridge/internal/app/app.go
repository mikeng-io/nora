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
	"log/slog"

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

// Social is the account side: who an account is, what it shows, its story.
type Social interface {
	Account(ctx context.Context, igid domain.IGID) (domain.Account, error)
	SocialProfile(ctx context.Context, igid domain.IGID) domain.SocialRead
	Story(ctx context.Context, igid domain.IGID) (domain.Story, error)
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
}

// New wires the use cases to their ports.
func New(session Session, messaging Messaging, social Social) *Service {
	return &Service{session: session, messaging: messaging, social: social}
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

// thread parses the room key and resolves both thread ids.
//
// The parse comes AFTER the session gate: a checkpointed session answers
// "a human must act" ahead of "your id is malformed", as these routes always
// have. A malformed key never becomes a route lookup against Instagram.
func (s *Service) thread(ctx context.Context, room string) (domain.ThreadRef, error) {
	short, err := domain.ParseThreadFBID(room)
	if err != nil {
		return domain.ThreadRef{}, err
	}
	thread, err := s.messaging.ResolveThread(ctx, short)
	if err != nil {
		return domain.ThreadRef{}, &ThreadLookupError{Err: err}
	}
	return thread, nil
}

// SendText posts a text message. No length cap: 20,000 characters were
// measured accepted on this wire, and mautrix-meta's 1000 is its own policy.
func (s *Service) SendText(ctx context.Context, room, text string, mentions []domain.Mention, replyTo string) (domain.Sent, error) {
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
func (s *Service) SendMedia(ctx context.Context, room string, media domain.Upload, replyTo string) (domain.Sent, error) {
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
func (s *Service) React(ctx context.Context, room, messageID, emoji string, remove bool) (bool, error) {
	if _, err := s.ready(); err != nil {
		return false, err
	}
	thread, err := s.thread(ctx, room)
	if err != nil {
		return false, err
	}
	return s.messaging.React(ctx, thread, messageID, emoji, remove)
}

// Typing is the one use case that needs the realtime socket: SetTyping
// publishes over the MQTT-bypass stream.
func (s *Service) Typing(ctx context.Context, room string, typing bool) error {
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
func (s *Service) Unsend(ctx context.Context, room, messageID string) (bool, error) {
	if _, err := s.ready(); err != nil {
		return false, err
	}
	thread, err := s.thread(ctx, room)
	if err != nil {
		return false, err
	}
	confirmed, err := s.messaging.Unsend(ctx, thread, messageID)
	if err == nil && !confirmed {
		slog.WarnContext(ctx, "unsend returned direct_unsend_message=false; "+
			"the message may still be in the thread", "room", room)
	}
	return confirmed, err
}

// Members reads who is in one thread, never short and never empty. It
// returns the canonical room key the answer is about.
func (s *Service) Members(ctx context.Context, room string) (domain.ThreadFBID, domain.Thread, error) {
	if _, err := s.ready(); err != nil {
		return domain.ThreadFBID{}, domain.Thread{}, err
	}
	thread, err := s.thread(ctx, room)
	if err != nil {
		return domain.ThreadFBID{}, domain.Thread{}, err
	}
	members, err := s.messaging.Members(ctx, thread)
	return thread.Short, members, err
}

// AccountRead is one account and the outcome of its second, independent read.
type AccountRead struct {
	Account domain.Account
	Social  domain.SocialRead
}

// ReadAccount reads one account by igid, then its social half keyed on the
// SAME parsed igid. The social half fails on its own: GetProfile has already
// answered, so a social failure degrades the read rather than losing it.
func (s *Service) ReadAccount(ctx context.Context, igid string) (AccountRead, error) {
	if _, err := s.ready(); err != nil {
		return AccountRead{}, err
	}
	id, err := domain.ParseIGID(igid)
	if err != nil {
		return AccountRead{}, err
	}
	acct, err := s.social.Account(ctx, id)
	if err != nil {
		return AccountRead{}, err
	}
	return AccountRead{Account: acct, Social: s.social.SocialProfile(ctx, id)}, nil
}

// ReadStory reads one account's current story — ONE igid, because a story
// read is visible to its author.
func (s *Service) ReadStory(ctx context.Context, igid string) (domain.Story, error) {
	if _, err := s.ready(); err != nil {
		return domain.Story{}, err
	}
	id, err := domain.ParseIGID(igid)
	if err != nil {
		return domain.Story{}, err
	}
	return s.social.Story(ctx, id)
}
