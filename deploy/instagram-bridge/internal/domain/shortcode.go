package domain

// A post's shortcode and the numeric media id it encodes
// (design/instagram-adoption/17 P12).
//
// # Why the decode lives here
//
// The one library call that reads a post, instameow's `FetchMedia`, takes the
// NUMERIC media id: it GETs `/api/v1/media/{id}/info/`. The shortcode it also
// accepts is used only to build the `referer` header (pkg/instameow/misc.go).
// The library never derives one from the other; its own callers always hold
// the numeric id already, from an XMA's `TargetID`. A link, which is what Nora
// has, carries only the shortcode. So the decode is ours.
//
// # The encoding
//
// A shortcode is the media id written in base 64 with the URL-safe alphabet
// `A-Z a-z 0-9 - _`, most significant digit first, with no leading `A` (zero)
// digits. This is the algorithm instaloader implements as
// `Post.shortcode_to_mediaid` / `Post.mediaid_to_shortcode`
// (instaloader/structures.py): it pads the code to 12 characters with `A`,
// base64url-decodes it into 9 bytes, and reads them big-endian; it refuses a
// code longer than 11 characters, and a media id wider than 64 bits.
//
// Nothing here is measured against Instagram: no live pair was recorded, and
// no real shortcode may be written into this repository. The tests pin the
// decode against pairs computed by Python's standard-library base64 running
// instaloader's exact function — an implementation independent of this one —
// and round-trip it both ways. The gateway then checks the answer: a post
// whose `code` or `pk` disagrees with what was asked for is refused, so a
// wrong decode shows up as a failed read, never as somebody else's post.

import (
	"errors"
	"math/bits"
	"regexp"
	"strconv"
)

// Shortcode is the path segment of a post or reel link:
// `instagram.com/p/<shortcode>/`, `/reel/<shortcode>/`.
type Shortcode struct{ s string }

// shortcodePattern is the base64url alphabet, at most 64 characters: the SAME
// bound Nora's link parser uses (`_SHORTCODE` in
// nora/platform/instagram/parser.py), so a link Nora accepts is a shortcode
// the bridge accepts, and the reverse.
var shortcodePattern = regexp.MustCompile(`^[A-Za-z0-9_-]{1,64}$`)

// maxDecodableShortcode is the longest shortcode that encodes a media id and
// nothing else. 11 base-64 digits hold 66 bits, enough for any 64-bit id.
// Longer codes exist (reported for posts on private accounts) and their extra
// characters are not part of the id in any way this code can vouch for, so
// they parse but do not decode.
const maxDecodableShortcode = 11

// ParseShortcode accepts one path segment in the base64url alphabet.
//
// The error does NOT quote the input. A shortcode names one person's post,
// and the refusal reaches an error envelope; the caller already knows what it
// sent.
func ParseShortcode(s string) (Shortcode, error) {
	if !shortcodePattern.MatchString(s) {
		return Shortcode{}, Classify(ErrInvalidID,
			errors.New("shortcode must be 1 to 64 characters of A-Z, a-z, 0-9, - and _"))
	}
	return Shortcode{s: s}, nil
}

func (c Shortcode) String() string { return c.s }
func (c Shortcode) IsZero() bool   { return c.s == "" }

// MediaID is a post's numeric id (`pk`): what `FetchMedia` takes.
type MediaID struct{ n uint64 }

func (id MediaID) String() string { return strconv.FormatUint(id.n, 10) }
func (id MediaID) IsZero() bool   { return id.n == 0 }

// MediaIDOf wraps a number. Zero is "no id".
func MediaIDOf(n uint64) (MediaID, bool) {
	if n == 0 {
		return MediaID{}, false
	}
	return MediaID{n: n}, true
}

// ErrShortcodeNotDecodable is a well-formed shortcode longer than 11
// characters. It is not the caller's mistake (the shape is Instagram's), and
// there is no mechanism to read it yet: ErrUnsupported, which R-27 surfaces as
// `unavailable` with its reason.
var ErrShortcodeNotDecodable = Classify(ErrUnsupported,
	errors.New("a shortcode longer than 11 characters does not decode to a media id; no mechanism reads it yet"))

// MediaID decodes the shortcode into the media id it encodes.
func (c Shortcode) MediaID() (MediaID, error) {
	if len(c.s) > maxDecodableShortcode {
		return MediaID{}, ErrShortcodeNotDecodable
	}
	var n uint64
	for i := 0; i < len(c.s); i++ {
		d := shortcodeDigit(c.s[i])
		if d < 0 {
			// Unreachable through ParseShortcode; kept so the decode never
			// turns a character outside the alphabet into a number.
			return MediaID{}, Classify(ErrInvalidID, errors.New("shortcode has a character outside base64url"))
		}
		hi, lo := bits.Mul64(n, 64)
		if hi != 0 {
			return MediaID{}, Classify(ErrInvalidID, errors.New("shortcode encodes a number wider than 64 bits"))
		}
		sum, carry := bits.Add64(lo, uint64(d), 0)
		if carry != 0 {
			return MediaID{}, Classify(ErrInvalidID, errors.New("shortcode encodes a number wider than 64 bits"))
		}
		n = sum
	}
	if n == 0 {
		return MediaID{}, Classify(ErrInvalidID, errors.New("shortcode encodes media id zero"))
	}
	return MediaID{n: n}, nil
}

// Shortcode encodes the id: the inverse of Shortcode.MediaID, with no leading
// `A`, as instaloader's mediaid_to_shortcode writes it.
func (id MediaID) Shortcode() Shortcode {
	if id.n == 0 {
		return Shortcode{s: "A"}
	}
	var buf [maxDecodableShortcode]byte
	i := len(buf)
	for n := id.n; n > 0; n /= 64 {
		i--
		buf[i] = shortcodeAlphabet[n%64]
	}
	return Shortcode{s: string(buf[i:])}
}

const shortcodeAlphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"

// shortcodeDigit is the value of one base64url character, or -1.
func shortcodeDigit(b byte) int {
	switch {
	case b >= 'A' && b <= 'Z':
		return int(b - 'A')
	case b >= 'a' && b <= 'z':
		return int(b-'a') + 26
	case b >= '0' && b <= '9':
		return int(b-'0') + 52
	case b == '-':
		return 62
	case b == '_':
		return 63
	default:
		return -1
	}
}
