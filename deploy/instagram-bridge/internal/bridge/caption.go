package bridge

// The shared-item caption excerpt, ported 1:1 from
// nora/platform/instagram/parser.py's _caption_excerpt and helpers. A LINKED
// share's caption is cut to its opening: a line holding a whole caption is
// answered as if it were the whole post, so the `…` says there is more and
// the link reads it.

import (
	"regexp"
	"strings"
	"unicode"
)

const (
	captionExcerptMax     = 160
	captionExcerptMinText = 40
	ellipsis              = "…"
)

var (
	tagOrMention  = regexp.MustCompile(`[#@][^\s#@]+`)
	trailingEllip = regexp.MustCompile(`(?:\.{3,}|…)+$`)
)

// closing marks that stay with the sentence they close (Python's
// `["'”’」』）)]`).
func isCloser(r rune) bool {
	switch r {
	case '"', '\'', '”', '’', '」', '』', '）', ')':
		return true
	}
	return false
}

// sentenceEndAt reports whether position i begins a sentence end and, if so,
// the offset just past it (the closing quotes ride with the sentence).
// Mirrors _SENTENCE_END:
//   - a Latin . ! ? run ends a sentence only when whitespace or the end of
//     text follows (so `3.5` or `example.com` never cuts);
//   - U+3002, U+FF01, U+FF1F end where they stand;
//   - a newline ends.
func sentenceEndAt(rs []rune, i int) (int, bool) {
	r := rs[i]
	isLatin := r == '.' || r == '!' || r == '?'
	isWide := r == '。' || r == '！' || r == '？'
	if !isLatin && !isWide && r != '\n' {
		return 0, false
	}
	j := i
	if r == '\n' {
		return i + 1, true
	}
	// Consume the punctuation run.
	for j < len(rs) && rs[j] == r || (isLatin && j < len(rs) && (rs[j] == '.' || rs[j] == '!' || rs[j] == '?')) || (isWide && j < len(rs) && (rs[j] == '。' || rs[j] == '！' || rs[j] == '？')) {
		j++
	}
	// Consume closers.
	for j < len(rs) && isCloser(rs[j]) {
		j++
	}
	if isLatin && j < len(rs) && !unicode.IsSpace(rs[j]) {
		return 0, false
	}
	return j, true
}

func continuesPrevious(r rune) bool {
	cp := uint32(r)
	switch {
	case r == '‍':
		return true
	case cp >= 0xFE00 && cp <= 0xFE0F:
		return true
	case cp >= 0x1F3FB && cp <= 0x1F3FF:
		return true
	case cp >= 0xE0020 && cp <= 0xE007F:
		return true
	case cp == 0x20E3:
		return true
	}
	return unicode.IsMark(r)
}

func isRegional(r rune) bool { return r >= 0x1F1E6 && r <= 0x1F1FF }

// hardCut is rs[:limit], moved back so it never splits an emoji.
func hardCut(rs []rune, limit int) string {
	i := limit
	for i > 0 && i < len(rs) && (continuesPrevious(rs[i]) || rs[i-1] == '‍') {
		i--
	}
	if i > 0 && i < len(rs) && isRegional(rs[i]) {
		run := 0
		for run < i && isRegional(rs[i-1-run]) {
			run++
		}
		if run%2 == 1 {
			i--
		}
	}
	return string(rs[:i])
}

func realTextLength(text string) int {
	stripped := tagOrMention.ReplaceAllString(text, " ")
	n := 0
	for _, r := range stripped {
		if unicode.IsLetter(r) || unicode.IsNumber(r) {
			n++
		}
	}
	return n
}

// captionExcerpt is the opening of a LINKED shared item's caption.
func captionExcerpt(caption string) string {
	rs := []rune(strings.TrimSpace(caption))
	cut := false
	text := rs
	for i := 0; i < len(text); i++ {
		end, ok := sentenceEndAt(text, i)
		if !ok {
			continue
		}
		head := []rune(strings.TrimSpace(string(text[:end])))
		if len(head) >= captionExcerptMax || realTextLength(string(head)) >= captionExcerptMinText {
			if strings.TrimSpace(string(text[end:])) != "" {
				text, cut = head, true
			}
			break
		}
	}
	if len(text) > captionExcerptMax {
		window := string(text[:captionExcerptMax])
		space := strings.LastIndex(window, " ")
		if space >= captionExcerptMax/2 {
			text = []rune(window[:space])
		} else {
			text = []rune(hardCut(text, captionExcerptMax))
		}
		cut = true
	}
	if !cut {
		return string(text)
	}
	return trailingEllip.ReplaceAllString(strings.TrimSpace(string(text)), "") + ellipsis
}
