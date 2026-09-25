package domain

import "strings"

// MentionsHandle reports whether text carries "@"+username as a whole
// @mention, ASCII case-insensitively. An empty username matches nothing (fail
// closed: an account that does not know its own handle cannot tell it was
// summoned).
//
// It is the ONE word-bounded test the bridge uses for "was this account
// @-mentioned in text": the notification feed (gateway mentions.go), the
// summons card in the DM delta, and write guard 3's check of the comment
// itself all go through it, so the three cannot disagree about a boundary.
func MentionsHandle(text, username string) bool {
	if username == "" {
		return false
	}
	return ContainsMentionToken(asciiLower(text), "@"+asciiLower(username))
}

// asciiLower folds A-Z only. The handle alphabet is ASCII, and full Unicode
// folding would let a non-ASCII letter (the Turkish dotted İ, for one) turn
// into ASCII-looking bytes and complete a handle nobody typed. Every other
// byte, a multi-byte rune's included, is left exactly as it was.
func asciiLower(s string) string {
	b := []byte(s)
	for i, c := range b {
		if c >= 'A' && c <= 'Z' {
			b[i] = c + ('a' - 'A')
		}
	}
	return string(b)
}

// ContainsMentionToken finds marker as a whole @mention, not as a prefix of
// a longer one: "@nora" must not match inside "@nora_fan", nor inside
// "x@nora". The byte on each side of a candidate match is checked against
// Instagram's own username alphabet (ParseUsername's `[A-Za-z0-9._]`) and,
// if either continues a handle, the search resumes past that occurrence
// rather than accepting it. Both arguments are compared as given; callers
// lower-case them.
func ContainsMentionToken(haystack, marker string) bool {
	if marker == "" {
		return false
	}
	from := 0
	for {
		i := strings.Index(haystack[from:], marker)
		if i < 0 {
			return false
		}
		pos := from + i
		end := pos + len(marker)
		startsWord := pos == 0 || !isUsernameByte(haystack[pos-1])
		// A trailing "." that ends a sentence is punctuation, since no
		// Instagram username ends in one: "@nora." tags @nora, "@nora.x"
		// does not. The same rule as Nora's own (nora/domain/summons.py
		// _handle_pattern), so the two sides cannot disagree on a boundary.
		endsWord := end >= len(haystack) || !isUsernameByte(haystack[end]) ||
			(haystack[end] == '.' && (end+1 >= len(haystack) || !isHandleCoreByte(haystack[end+1])))
		if startsWord && endsWord {
			return true
		}
		from = pos + 1
	}
}

func isUsernameByte(b byte) bool {
	return (b >= 'a' && b <= 'z') || (b >= 'A' && b <= 'Z') || (b >= '0' && b <= '9') || b == '.' || b == '_'
}

// isHandleCoreByte is a username byte other than ".".
func isHandleCoreByte(b byte) bool {
	return b != '.' && isUsernameByte(b)
}
