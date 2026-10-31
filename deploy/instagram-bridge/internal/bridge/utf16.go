package bridge

// The outbound-mention splice, ported from nora/platform/instagram/send.py:
// Instagram renders a mention chip only when the span at offset:offset+length
// reads, literally, "@" + the account's own username. The app sends
// {start,length,recipient,handle} over the DRAFTED text; this bridge rewrites
// each span to "@handle" and reports the NEW offsets — all in UTF-16 code
// units, which is what both the request shape and Instagram's mention
// offsets are measured in.

import "unicode/utf16"

// WireMention is the action request's mention entry.
type WireMention struct {
	Start     int    `json:"start"`
	Length    int    `json:"length"`
	Recipient string `json:"recipient"` // fbid — the messaging identity
	Handle    string `json:"handle"`    // the account's own username; "" splices nothing
}

// DomainMention is the gateway's outbound shape.
type DomainMention struct {
	FBID   string
	Offset int
	Length int
}

// SpliceMentions rewrites the drafted text for Instagram's mention rule and
// returns the final text + mentions over it. A mention with no handle keeps
// its drafted span (a display-name ping renders as plain text — Instagram's
// rule, not this bridge's to fix).
func SpliceMentions(text string, in []WireMention) (string, []DomainMention) {
	units := utf16.Encode([]rune(text))
	out := make([]DomainMention, 0, len(in))
	// Apply splices right-to-left so earlier offsets stay valid.
	type splice struct {
		start, length int
		repl          []uint16
		m             WireMention
	}
	var splices []splice
	for _, m := range in {
		if m.Handle == "" || m.Start < 0 || m.Length < 0 || m.Start+m.Length > len(units) {
			continue
		}
		splices = append(splices, splice{m.Start, m.Length, utf16.Encode([]rune("@" + m.Handle)), m})
	}
	// Sort by start descending for stable splicing.
	for i := 0; i < len(splices); i++ {
		for j := i + 1; j < len(splices); j++ {
			if splices[j].start > splices[i].start {
				splices[i], splices[j] = splices[j], splices[i]
			}
		}
	}
	deltaBefore := func(pos int) int {
		d := 0
		for _, s := range splices {
			if s.start+s.length <= pos {
				d += len(s.repl) - s.length
			}
		}
		return d
	}
	for _, s := range splices {
		repl := make([]uint16, 0, len(units)-s.length+len(s.repl))
		repl = append(repl, units[:s.start]...)
		repl = append(repl, s.repl...)
		repl = append(repl, units[s.start+s.length:]...)
		units = repl
	}
	for _, m := range in {
		offset := m.Start + deltaBefore(m.Start)
		length := m.Length
		if m.Handle != "" {
			length = len(utf16.Encode([]rune("@" + m.Handle)))
		}
		out = append(out, DomainMention{FBID: m.Recipient, Offset: offset, Length: length})
	}
	return string(utf16.Decode(units)), out
}
