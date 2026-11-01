package bridge

// One Instagram link parser, ported 1:1 from nora/domain/instagram_links.py.
// The shared-item render may NEVER emit a raw URL: the link is rebuilt from
// validated path segments onto a fixed origin, so a story link's query —
// which carries the owner's igid (R-18) — and every fragment are dropped.
//
// linkShape is the label (path shape only, never a licence to read);
// parseLink is the strict answer a caller may act on.

import (
	"net/url"
	"regexp"
	"strings"
)

type LinkKind string

const (
	LinkProfile LinkKind = "profile"
	LinkPost    LinkKind = "post"
	LinkReel    LinkKind = "reel"
	LinkStory   LinkKind = "story"
)

var (
	shortcodeRE = regexp.MustCompile(`^[A-Za-z0-9_-]{1,64}$`)
	usernameRE  = regexp.MustCompile(`^[A-Za-z0-9._]{1,30}$`)
	storyPKRE   = regexp.MustCompile(`^[0-9]{1,25}$`)
	hasNonDigit = regexp.MustCompile(`[^0-9]`)
)

var instagramHosts = map[string]bool{"instagram.com": true, "www.instagram.com": true}

var reservedFirstSegments = map[string]bool{
	"about": true, "accounts": true, "api": true, "challenge": true,
	"developer": true, "direct": true, "directory": true, "emails": true,
	"explore": true, "graphql": true, "legal": true, "locations": true,
	"p": true, "privacy": true, "reel": true, "reels": true, "session": true,
	"stories": true, "terms": true, "tv": true, "web": true,
}

var notAStoryOwner = map[string]bool{"highlights": true}

// IsHandle is whether a string can stand as an Instagram handle: the
// username charset, whole, with at least one non-digit (an all-digit
// segment is an igid's shape and must never resolve as a name).
func IsHandle(v string) bool {
	return usernameRE.MatchString(v) && hasNonDigit.MatchString(v)
}

// InstagramLink is a validated link: its kind and the segments that name it.
type InstagramLink struct {
	Kind    LinkKind
	Key     string // username for profile/story, shortcode for post/reel
	StoryPK string // set for /stories/<user>/<pk>/
}

// URL rebuilds the link on the fixed origin: no query, no fragment.
// `/reels/` is rebuilt on the singular `/reel/`.
func (l InstagramLink) URL() string {
	var path string
	switch l.Kind {
	case LinkProfile:
		path = "/" + l.Key + "/"
	case LinkReel:
		path = "/reel/" + l.Key + "/"
	case LinkPost:
		path = "/p/" + l.Key + "/"
	default:
		if l.StoryPK != "" {
			path = "/stories/" + l.Key + "/" + l.StoryPK + "/"
		} else {
			path = "/stories/" + l.Key + "/"
		}
	}
	return "https://www.instagram.com" + path
}

func splitSegments(raw string) (*url.URL, []string, bool) {
	if strings.TrimSpace(raw) == "" {
		return nil, nil, false
	}
	u, err := url.Parse(strings.TrimSpace(raw))
	if err != nil {
		return nil, nil, false
	}
	path := strings.TrimSuffix(u.Path, "/")
	var segments []string
	if strings.HasPrefix(path, "/") {
		segments = strings.Split(path[1:], "/")
	}
	return u, segments, true
}

func shape(segments []string, storyRing bool) (LinkKind, bool) {
	switch {
	case len(segments) == 2 && (segments[0] == "reel" || segments[0] == "reels"):
		return LinkReel, true
	case len(segments) == 2 && segments[0] == "p":
		return LinkPost, true
	case len(segments) == 3 && segments[0] == "stories":
		return LinkStory, true
	case storyRing && len(segments) == 2 && segments[0] == "stories":
		return LinkStory, true
	case len(segments) == 1 && segments[0] != "" && !reservedFirstSegments[strings.ToLower(segments[0])]:
		return LinkProfile, true
	}
	return "", false
}

// LinkShape is the kind a url's PATH is shaped like, or false. storyRing=false
// keeps the two-segment `/stories/<user>/` ring form out of the story shape.
func LinkShape(raw string, storyRing bool) (LinkKind, bool) {
	_, segments, ok := splitSegments(raw)
	if !ok {
		return "", false
	}
	return shape(segments, storyRing)
}

func originOK(u *url.URL) bool {
	if u.Scheme != "https" && u.Scheme != "http" {
		return false
	}
	if !instagramHosts[strings.ToLower(u.Hostname())] {
		return false
	}
	// A port or credentials in the netloc refuse the link. url.Parse leaves
	// them in u.User / u.Port(); a malformed port (`host:abc`) makes
	// u.Port() return "" but keeps u.Host carrying the colon — refuse that
	// too, the same way the Python's parts.port raising refuses it.
	if u.Port() != "" || u.User != nil {
		return false
	}
	if strings.Contains(u.Host, ":") {
		return false
	}
	return true
}

// ParseLink is the strict answer: a validated InstagramLink or false.
func ParseLink(raw string) (InstagramLink, bool) {
	u, segments, ok := splitSegments(raw)
	if !ok {
		return InstagramLink{}, false
	}
	kind, ok := shape(segments, true)
	if !ok || !originOK(u) {
		return InstagramLink{}, false
	}
	switch kind {
	case LinkReel, LinkPost:
		code := segments[1]
		if !shortcodeRE.MatchString(code) {
			return InstagramLink{}, false
		}
		return InstagramLink{Kind: kind, Key: code}, true
	case LinkStory:
		user := segments[1]
		pk := ""
		if len(segments) == 3 {
			pk = segments[2]
		}
		if notAStoryOwner[strings.ToLower(user)] {
			return InstagramLink{}, false
		}
		if IsHandle(user) && (pk == "" || storyPKRE.MatchString(pk)) {
			return InstagramLink{Kind: LinkStory, Key: user, StoryPK: pk}, true
		}
		return InstagramLink{}, false
	default: // profile
		user := segments[0]
		if !IsHandle(user) {
			return InstagramLink{}, false
		}
		return InstagramLink{Kind: LinkProfile, Key: user}, true
	}
}
