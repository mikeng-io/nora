package domain

import "strings"

// MentionsHandle reports whether text carries "@"+username as a whole
// @mention, case-insensitively. An empty username matches nothing (fail
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
	return ContainsMentionToken(strings.ToLower(text), "@"+strings.ToLower(username))
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
		endsWord := end >= len(haystack) || !isUsernameByte(haystack[end])
		if startsWord && endsWord {
			return true
		}
		from = pos + 1
	}
}

func isUsernameByte(b byte) bool {
	return (b >= 'a' && b <= 'z') || (b >= 'A' && b <= 'Z') || (b >= '0' && b <= '9') || b == '.' || b == '_'
}
