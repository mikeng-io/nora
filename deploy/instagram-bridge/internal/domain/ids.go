// Package domain is the bridge's vocabulary: the ids Instagram uses, the
// closed set of ways a request can fail, and the entities a read returns.
// It imports the standard library and nothing else
// (design/instagram-adoption/16 §Layers).
package domain

// # Why the ids are types
//
// Instagram has three account id spaces and two thread id spaces
// (02-identity-and-policy.md), and every one of them is a decimal string. So
// the wrong one validates: a messaging fbid handed to a profile read names a
// different account or none, a username sent into
// `PolarisProfilePageContentQuery`'s numeric field fails inside an HTTP 200,
// a long thread id handed to /react is accepted and never lands. None of those
// produces an error anywhere. Each was a real bug on 2026-09-22.
//
// Here each space is its own type with unexported fields, so the only way to
// hold one is through its parsing constructor, and a function that takes an
// IGID cannot be handed a username.
//
// The numeric parsers keep `strconv.ParseInt`'s exact acceptance (and so
// canonicalize leading zeros), because that is what the handlers accepted
// before these types existed: tightening it is a behaviour change, not a
// refactor.

import (
	"fmt"
	"regexp"
	"strconv"
)

// IGID is the PUBLIC Instagram account id (`User.ID`, `pk`): what GetProfile
// and the story document take. It is NOT a messaging address.
type IGID struct{ n int64 }

// ParseIGID accepts a positive decimal and canonicalizes it, so "0246967358"
// and "246967358" are one account and cannot be cached or addressed twice.
func ParseIGID(s string) (IGID, error) {
	n, ok := parsePositive(s)
	if !ok {
		return IGID{}, invalid("igid is not a decimal instagram id: %q", s)
	}
	return IGID{n: n}, nil
}

func (id IGID) String() string { return strconv.FormatInt(id.n, 10) }
func (id IGID) IsZero() bool   { return id.n == 0 }

// FBID is `interop_messaging_user_fbid`: the MESSAGING identity. It is what a
// sender, a mention and GetOwnFBID() carry, and the only id "was I tagged?"
// may compare against.
type FBID struct{ n int64 }

// ParseFBID accepts a positive decimal.
func ParseFBID(s string) (FBID, error) {
	n, ok := parsePositive(s)
	if !ok {
		return FBID{}, invalid("fbid is not a decimal messaging id: %q", s)
	}
	return FBID{n: n}, nil
}

// FBIDOf wraps a number the library already decoded. Zero is "no fbid" on
// every field this bridge reads, so it yields the zero FBID and false.
func FBIDOf(n int64) (FBID, bool) {
	if n <= 0 {
		return FBID{}, false
	}
	return FBID{n: n}, true
}

func (id FBID) String() string { return strconv.FormatInt(id.n, 10) }
func (id FBID) Int64() int64   { return id.n }
func (id FBID) IsZero() bool   { return id.n == 0 }

// ThreadFBID is a thread's SHORT id (`thread_fbid`, e.g. 1183780976338188):
// what inbound deltas carry, and the room key on every route.
type ThreadFBID struct{ n int64 }

// ParseThreadFBID accepts a positive decimal that fits int64. The LONG thread
// id is all digits too, and is refused here because it overflows: a room key
// in the wrong id space is exactly the mistake this type exists to stop.
func ParseThreadFBID(s string) (ThreadFBID, error) {
	n, ok := parsePositive(s)
	if !ok {
		return ThreadFBID{}, invalid("room_id must be a decimal thread_fbid: %q", s)
	}
	return ThreadFBID{n: n}, nil
}

func (id ThreadFBID) String() string { return strconv.FormatInt(id.n, 10) }
func (id ThreadFBID) Int64() int64   { return id.n }
func (id ThreadFBID) IsZero() bool   { return id.n == 0 }

// ThreadIGID is a thread's LONG id (`thread_igid`, 39 digits, beyond int64).
// It comes only from Instagram's route lookup and never crosses the HTTP
// boundary in either direction.
type ThreadIGID struct{ s string }

// NewThreadIGID accepts any non-empty value the route lookup returned. It is
// not validated further because it is never caller input.
func NewThreadIGID(s string) (ThreadIGID, error) {
	if s == "" {
		return ThreadIGID{}, invalid("thread_igid is empty")
	}
	return ThreadIGID{s: s}, nil
}

func (id ThreadIGID) String() string { return id.s }
func (id ThreadIGID) IsZero() bool   { return id.s == "" }

// ThreadRef is BOTH ids of one thread, resolved and carried together. Which
// one a verb takes is a per-verb fact (/unsend is long, the rest short), so
// there is no single "the thread id" to pass around.
type ThreadRef struct {
	Short ThreadFBID
	Long  ThreadIGID
}

// Username is an account's handle. It is renameable and a freed name can be
// taken by somebody else, so it is NEVER an identity; it is an input to be
// resolved to an IGID.
type Username struct{ s string }

// usernamePattern is Instagram's handle alphabet: letters, digits, `.` and
// `_`, at most 30 characters.
var usernamePattern = regexp.MustCompile(`^[A-Za-z0-9._]{1,30}$`)

// ParseUsername accepts a handle, with or without a leading `@`.
func ParseUsername(s string) (Username, error) {
	if len(s) > 0 && s[0] == '@' {
		s = s[1:]
	}
	if !usernamePattern.MatchString(s) {
		return Username{}, invalid("not an instagram username: %q", s)
	}
	return Username{s: s}, nil
}

func (u Username) String() string { return u.s }

// MessageID is one message's platform id (`mid.$…`): what a reaction, an
// unsend and a threaded reply address. Instagram documents no shape for it,
// so it is OPAQUE and the parse refuses only what cannot be one: an empty
// value, a byte outside printable ASCII (whitespace and control bytes
// included), or anything longer than maxMessageIDLength. The measured ids are
// about 40 characters.
//
// It arrives as a PATH SEGMENT on the message routes
// (`/threads/{thread_fbid}/messages/{message_id}`), so the parse is what
// makes it a value before any use case runs (16 §Resources: "a malformed id
// is 400 before any use case runs").
type MessageID struct{ s string }

const maxMessageIDLength = 256

// ParseMessageID accepts 1–256 printable, non-space ASCII bytes. Its error
// text is FIXED and does not echo the input.
func ParseMessageID(s string) (MessageID, error) {
	if s == "" || len(s) > maxMessageIDLength {
		return MessageID{}, invalid("message_id must be 1-%d printable characters", maxMessageIDLength)
	}
	for i := 0; i < len(s); i++ {
		if s[i] <= ' ' || s[i] > '~' {
			return MessageID{}, invalid("message_id must be 1-%d printable characters", maxMessageIDLength)
		}
	}
	return MessageID{s: s}, nil
}

func (id MessageID) String() string { return id.s }
func (id MessageID) IsZero() bool   { return id.s == "" }

func parsePositive(s string) (int64, bool) {
	n, err := strconv.ParseInt(s, 10, 64)
	return n, err == nil && n > 0
}

func invalid(format string, args ...any) error {
	return Classify(ErrInvalidID, fmt.Errorf(format, args...))
}
