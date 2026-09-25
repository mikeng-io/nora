package instameow

// GET /mentions — comment-mentions from Instagram's activity/notification
// feed. Step one toward a Grok-style public "summons" reply: Nora needs to
// learn when someone @mentions her in a COMMENT on a post before anything can
// answer it.
//
// # What the upstream call really is
//
// `POST https://www.instagram.com/api/v1/news/inbox/`, form-encoded, with
// `jazoest` and `fb_dtsg` and nothing else — the request Instagram WEB's own
// notifications panel makes on its "All" tab, as captured from the operator's logged-in browser on 2026-09-25
// (design 19, phase 1). It goes through restDo as a `webForm` POST (call.go):
// the form tokens come from the session's own configs, built the way the
// library builds its own web POST form (pkg/instameow/misc.go:40-48,57-58).
// Being a POST, it goes out through MakeRequestOnceNoRedirect: no library
// retry, no redirect followed, a 3xx read by redirectSentinel and a failure
// body by restCheckpointSentinel, so Bridge.Mentions still reaches
// sessionRejected.
//
// The web client ALSO posts `/api/v1/news/inbox_seen/` when the panel opens.
// This route does NOT: marking the operator's notifications read is a side
// effect on the account the operator did not ask for.
//
// Header set against the capture's list. Sent, with the value the library
// itself would send: X-CSRFToken, X-IG-App-ID, X-ASBD-ID,
// X-IG-Max-Touch-Points "0" and Accept "*/*" (BuildHeaders,
// httpclient/http.go:561,593,608-622), X-Web-Session-ID,
// X-Requested-With, Content-Type (restDo), X-Instagram-AJAX (the rollout
// hash) and X-IG-WWW-Claim (RESTIssuer.WebFormTokens' and restRequest.webForm's doc
// comments). KNOWN DIFFERENCES, not closable from the library:
//
//   - X-IG-WWW-Claim: the web client sent a non-zero claim. The library
//     holds the claim IN MEMORY only (cookies.go:57, set from a response's
//     `x-ig-set-www-claim`, cookies.go:183-185; Cookies.MarshalJSON persists
//     only the cookie map, cookies.go:69-70), so until some response since
//     the last restart has set it, this sends "0" (the library's own
//     fallback, login_web.go:198-199);
//   - the form's field ORDER is `url.Values.Encode()`'s sorted order, the
//     same as the library's own form (misc.go:57); the web client's order
//     was not recorded;
//   - BuildHeaders also sends `x-fb-lsd`, `x-mid` and `viewport-width`,
//     which the capture's header list does not name. Whether the browser
//     sent them and the list omitted them is unrecorded.
//
// # The response
//
// Top-level keys captured: counts, last_checked, priority_stories,
// new_stories, old_stories, continuation_token, subscription, is_last_page,
// partition, status. This route reads `new_stories` and `old_stories`. Each
// story carries an int `story_type` and an `args` object; the keys of `args`
// this file reads are `text`, `rich_text`, `profile_id`, `profile_name`,
// `timestamp`, `media[]` (`id`, `shortcode`) and `destination`.
// `logging_context` is read by nobody (see mentionsAccount). fakeig/mentions.go's fixture is built
// from the captured KEY set with synthetic values.
//
// Two things are tolerated on purpose:
//
//   - every id may arrive as a JSON string or a JSON number (flexID): the
//     capture has `profile_id` a string and some ids numbers;
//   - `timestamp` may arrive as a float or as a numeric string
//     (flexTimestamp); the capture has a number.
//
// Each STORY is decoded on its own, from `[]json.RawMessage`, so one story
// that does not fit never fails the whole read: it is counted under
// `undecodableStoryType`, the same way a story this route chooses not to
// classify is counted under its own real story_type.
//
// # Classification is STRUCTURAL, not English wording
//
// A story is a comment-mention when it has a comment id AND media AND it
// mentions THIS account.
//
// The COMMENT ID is `args.comment_id` or `args.comment_ids[0]` when present,
// else the `target_comment_id` query parameter of `args.destination`. The
// capture found NO story carrying `comment_id`/`comment_ids`: a comment's id
// rides in the destination, e.g. story_type 13 ("liked your comment"),
// `clips_home?id=<media>&target_comment_id=<comment>`. The MEDIA is
// `args.media[0]` when it has an id, else the destination's `id` — only for
// the schemes whose `id` IS a media (`media`, `clips_home`), never `user?id=`
// (a user) or `story_fullscreen?reel_id=`.
//
// The MENTION is an "@" token naming this account's own username, read off
// the live session (never a hardcoded string), word-bounded, in text or
// rich_text. `logging_context.mentioned_user_ids` is not a mention signal: the
// capture showed one id there on every story type, likes included.
//
// A "liked your comment" story (type 13) has a comment id AND media, so the
// mention check is what keeps it out: Instagram's sentence names the liker,
// not "@" this account. A mention IN A CAPTION rather than a comment can
// carry the "@" token and media but no comment id, which is why the comment
// id is a separate, required check.
//
// The "@" token is matched at a WORD BOUNDARY: a marker of "@nora" must not
// match "@nora_fan", so the byte right after the marker is checked against
// Instagram's own username alphabet (domain.ParseUsername's
// `[A-Za-z0-9._]`) and rejected if it continues one.
//
// Everything not classified as a mention is SKIPPED but COUNTED by its
// `story_type`. The numeric type a comment-mention arrives as is still
// UNMEASURED: none was present in the captured feed (types seen: 651, 13,
// 101, 75, 768, 117), and the counts are how a live read will show it.
//
// # What never crosses into a span or an error
//
// The only span this file's call opens is `rest`'s own
// `instagram.rest news_inbox`, and this file adds no attribute to it — only
// counts ever leave readMentions (transport/http/mentions.go's job). Every
// error this file raises has a FIXED text, the same `postReadError` pattern
// posts.go uses: no igid, username, media id, comment id or notification
// text ever reaches `Error()`.

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"math"
	"net/http"
	"net/url"
	"regexp"
	"sort"
	"strconv"
	"strings"

	"instagram-bridge/internal/domain"

	"go.mau.fi/mautrix-meta/pkg/messagix/httpclient"
)

// newsInboxEndpoint has no entry in the library's endpoint table
// (pkg/messagix/endpoints/instagram.go), so it is written out in full,
// exactly as the web client was captured calling it.
const newsInboxEndpoint = "https://www.instagram.com/api/v1/news/inbox/"

// No `selected_filters`: the "All" tab. The first live summons test
// (2026-09-25) came back with zero stories under `selected_filters=comments`
// after a real @mention in a comment. That filter is the panel's "Comments"
// tab (comments on HER posts); mentions sit under a separate tab whose filter
// value was not captured. "All" was captured exactly (jazoest, fb_dtsg only)
// and carries every story, which the classifier sorts.

// mediaDestinations are the `args.destination` schemes whose `id` query
// parameter is a MEDIA id (captured: `clips_home?id=<media>&...` on a
// "liked your comment" story, `media?id=` on a post like). Every other
// scheme's `id` is something else (`user?id=` is a user).
var mediaDestinations = map[string]bool{"media": true, "clips_home": true}

// commentIDPattern is a comment id: digits only.
var commentIDPattern = regexp.MustCompile(`^[0-9]+$`)

// undecodableStoryType is the sentinel `skipped` key for a story this
// hypothesis could not decode at all — never a real Instagram story_type,
// which is always a small non-negative integer (17 P.. UNMEASURED, but every
// value seen anywhere in the private API is >= 0).
const undecodableStoryType = -1

// flexID decodes a wire id that may arrive as a JSON string or a JSON
// number. UNMEASURED which this endpoint sends, so both are accepted and
// always yield the decimal string.
type flexID string

func (f *flexID) UnmarshalJSON(b []byte) error {
	if string(b) == "null" {
		*f = ""
		return nil
	}
	var s string
	if err := json.Unmarshal(b, &s); err != nil {
		var n json.Number
		if err := json.Unmarshal(b, &n); err != nil {
			return errors.New("id is neither a string nor a number")
		}
		s = n.String()
	}
	// An id is digits, or Instagram's `<pk>_<owner>` media form. Anything
	// else (a float, a sign, an exponent, free text) is not an id, and the
	// story is counted as undecodable rather than carried with a garbage id.
	// The error text carries no bytes of the value.
	if s != "" && !wireIDPattern.MatchString(s) {
		return errors.New("id is not a decimal id")
	}
	*f = flexID(s)
	return nil
}

var wireIDPattern = regexp.MustCompile(`^[0-9]+(_[0-9]+)?$`)

func (f flexID) String() string { return string(f) }

// set accepts only a finite, positive number of seconds: "NaN", "Inf" and
// negatives are not times.
func (f *flexTimestamp) set(v float64) error {
	if math.IsNaN(v) || math.IsInf(v, 0) || v <= 0 {
		return errors.New("timestamp is not a finite positive number")
	}
	*f = flexTimestamp(v)
	return nil
}

// flexTimestamp decodes `args.timestamp`, which may arrive as a JSON float
// (the hypothesis in this file's doc comment) or as a numeric string.
// UNMEASURED which this endpoint sends.
type flexTimestamp float64

func (f *flexTimestamp) UnmarshalJSON(b []byte) error {
	var n json.Number
	if err := json.Unmarshal(b, &n); err == nil {
		v, convErr := n.Float64()
		if convErr != nil {
			return convErr
		}
		return f.set(v)
	}
	var s string
	if err := json.Unmarshal(b, &s); err == nil {
		v, convErr := strconv.ParseFloat(s, 64)
		if convErr != nil {
			return convErr
		}
		return f.set(v)
	}
	return fmt.Errorf("timestamp is neither a number nor a string: %s", string(b))
}

// wireNewsInbox is the top-level envelope. Each story is kept RAW here: it is
// decoded individually in readMentions, so one story this hypothesis cannot
// parse does not fail the whole read.
type wireNewsInbox struct {
	NewStories []json.RawMessage `json:"new_stories"`
	OldStories []json.RawMessage `json:"old_stories"`
}

// wireStory is one notification. `pk` is not modelled: nothing in this
// route's output needs it.
type wireStory struct {
	StoryType int           `json:"story_type"`
	Type      int           `json:"type"`
	Args      wireStoryArgs `json:"args"`
}

type wireStoryArgs struct {
	Text        string           `json:"text"`
	RichText    string           `json:"rich_text"`
	ProfileID   flexID           `json:"profile_id"`
	ProfileName string           `json:"profile_name"`
	Timestamp   flexTimestamp    `json:"timestamp"`
	Media       []wireStoryMedia `json:"media"`
	// CommentID/CommentIDs were never seen in the capture; kept as the first
	// source because a story that does carry one names its comment directly.
	CommentID  flexID   `json:"comment_id"`
	CommentIDs []flexID `json:"comment_ids"`
	// Destination is the in-app link the story opens, e.g.
	// `clips_home?id=<media>&target_comment_id=<comment>`.
	Destination string `json:"destination"`
}

// wireStoryMedia is one entry of `args.media`: `id` is `<pk>_<owner igid>`,
// the same composite shape posts.go's `sameIdentity` already splits.
type wireStoryMedia struct {
	ID        flexID `json:"id"`
	Shortcode string `json:"shortcode"`
}

// mentionsReadError is a failure whose TEXT is fixed and whose identity is
// not: Unwrap exposes the cause, so needsHuman and errors.Is still see
// through it. Same pattern as posts.go's postReadError.
type mentionsReadError struct {
	msg   string
	cause error
}

func (e *mentionsReadError) Error() string { return e.msg }
func (e *mentionsReadError) Unwrap() error { return e.cause }

// readMentions reads the activity feed and classifies every story.
//
// self is the account's OWN identity (Bridge.selfSnapshot()): its Username
// makes the "@" token. An unknown username classifies nothing rather than
// guessing; see mentionsAccount.
func readMentions(ctx context.Context, issuer RESTIssuer, self domain.Self) ([]domain.CommentMention, map[int]int, error) {
	_, raw, err := restDo(ctx, issuer, restRequest{
		method:   http.MethodPost,
		endpoint: newsInboxEndpoint,
		label:    "news_inbox",
		form:     url.Values{},
		webForm:  true,
	})
	if err != nil {
		return nil, nil, err
	}

	var envelope wireNewsInbox
	if jsonErr := json.Unmarshal(raw, &envelope); jsonErr != nil {
		// restDo() already refuses a non-JSON 200 before returning bytes, so
		// this is reachable only if the envelope decodes as JSON but not as
		// this shape (e.g. a bare JSON array). Refused the same way, with a
		// fixed text.
		return nil, nil, domain.Classify(domain.ErrUpstreamRejected,
			&mentionsReadError{msg: "news_inbox did not return the expected shape", cause: jsonErr})
	}

	rawStories := make([]json.RawMessage, 0, len(envelope.NewStories)+len(envelope.OldStories))
	rawStories = append(rawStories, envelope.NewStories...)
	rawStories = append(rawStories, envelope.OldStories...)

	mentions := make([]domain.CommentMention, 0)
	skipped := make(map[int]int)
	for _, rawStory := range rawStories {
		var st wireStory
		if decodeErr := json.Unmarshal(rawStory, &st); decodeErr != nil {
			// This one story does not fit the hypothesis. Counted, never
			// dropped silently and never failing the rest of the read.
			skipped[undecodableStoryType]++
			continue
		}
		mention, ok := commentMentionOf(st, self)
		if !ok {
			skipped[st.StoryType]++
			// Why, in words and booleans only: no id, no text, no username.
			// The first live summons test (2026-09-25) skipped every story
			// with no way to tell which check refused it.
			scheme, query := destinationOf(st.Args.Destination)
			_, hasMedia := mediaOf(st.Args, scheme, query)
			slog.InfoContext(ctx, "news_inbox story skipped",
				"story_type", st.StoryType,
				"scheme", schemeWord(scheme),
				"has_comment_id", commentIDOf(st.Args, query) != "",
				"has_media", hasMedia,
				"has_self_token", mentionsAccount(st.Args, self),
				"type_excluded", nonMentionStoryTypes[st.StoryType],
				"text_has_at", strings.Contains(st.Args.Text, "@"),
				"rich_text_has_at", strings.Contains(st.Args.RichText, "@"),
				"dest_param_names", destParamNames(query))
			continue
		}
		mentions = append(mentions, mention)
	}
	return mentions, skipped, nil
}

// nonMentionStoryTypes are story types measured to be something other than a
// mention, which the "@" check alone cannot always tell apart: 13 is "liked
// your comment" and quotes her own comment (capture 2026-09-25).
var nonMentionStoryTypes = map[int]bool{13: true}

// commentMentionOf classifies one story.
func commentMentionOf(st wireStory, self domain.Self) (domain.CommentMention, bool) {
	scheme, query := destinationOf(st.Args.Destination)
	commentID := commentIDOf(st.Args, query)
	media, hasMedia := mediaOf(st.Args, scheme, query)
	if commentID == "" || !hasMedia || !mentionsAccount(st.Args, self) {
		return domain.CommentMention{}, false
	}
	// A "liked your comment" story (type 13, measured 2026-09-25) quotes HER
	// comment, so its text can carry her own handle. It is never a summons,
	// whatever the text says.
	if nonMentionStoryTypes[st.StoryType] {
		return domain.CommentMention{}, false
	}
	// The comment id can come from the destination while the media comes from
	// media[0]. When both name a post and they disagree, the pair names no one
	// thread: skip it rather than answer under the wrong post.
	if destID := query.Get("id"); mediaDestinations[scheme] && destID != "" {
		pk, _, _ := strings.Cut(media.ID.String(), "_")
		if destPK, _, _ := strings.Cut(destID, "_"); pk != destPK {
			return domain.CommentMention{}, false
		}
	}
	mediaID, shortcode := mediaIdentity(media)
	return domain.CommentMention{
		StoryType:        st.StoryType,
		At:               int64(st.Args.Timestamp),
		AuthorIGID:       st.Args.ProfileID.String(),
		AuthorUsername:   st.Args.ProfileName,
		MediaID:          mediaID,
		MediaShortcode:   shortcode,
		CommentID:        commentID,
		NotificationText: notificationTextOf(st.Args),
	}, true
}

// destinationOf splits `args.destination` into its scheme and its query:
// `clips_home?id=1&target_comment_id=2` is ("clips_home", {id, target_comment_id}).
// A malformed query yields whatever pairs did parse; every value read off it
// is checked against an id pattern before use.
func destinationOf(destination string) (string, url.Values) {
	scheme, rawQuery, _ := strings.Cut(destination, "?")
	query, _ := url.ParseQuery(rawQuery)
	return scheme, query
}

// commentIDOf is the story's comment id: `comment_id`, else
// `comment_ids[0]`, else the destination's `target_comment_id` (the only
// place the capture found one). "" when there is none.
func commentIDOf(args wireStoryArgs, query url.Values) string {
	if id := args.CommentID.String(); id != "" {
		return id
	}
	if len(args.CommentIDs) > 0 && args.CommentIDs[0] != "" {
		return args.CommentIDs[0].String()
	}
	if id := query.Get("target_comment_id"); commentIDPattern.MatchString(id) {
		return id
	}
	return ""
}

// mediaOf is the story's media: `media[0]` when it carries an id, else the
// destination's `id` for a scheme whose `id` is a media (mediaDestinations).
func mediaOf(args wireStoryArgs, scheme string, query url.Values) (wireStoryMedia, bool) {
	if len(args.Media) > 0 && args.Media[0].ID != "" {
		return args.Media[0], true
	}
	if !mediaDestinations[scheme] {
		return wireStoryMedia{}, false
	}
	if id := query.Get("id"); wireIDPattern.MatchString(id) {
		return wireStoryMedia{ID: flexID(id)}, true
	}
	return wireStoryMedia{}, false
}

// mentionsAccount is the structural check this file's doc comment describes:
// a WORD-BOUNDED "@" token naming THIS account's own username, in either
// text field. Nothing else counts.
//
// `logging_context.mentioned_user_ids` is deliberately NOT read. In the live
// capture (2026-09-25) every story type carried exactly one id there, likes of
// a story and likes of a post included, where nobody is mentioned. So the list
// most likely names the account being notified, and matching our own id in it
// would read "liked your comment" as a summons: a friend's like would make her
// answer in public. Reopen only with a capture that shows the list's content
// on a real comment-mention story next to a like.
func mentionsAccount(args wireStoryArgs, self domain.Self) bool {
	if self.Username == "" {
		// Own username unknown: fail closed rather than guess at who "the
		// account" is.
		return false
	}
	marker := "@" + strings.ToLower(self.Username)
	return containsMentionToken(strings.ToLower(args.Text), marker) ||
		containsMentionToken(strings.ToLower(args.RichText), marker)
}

// containsMentionToken finds marker as a whole @mention, not as a prefix of
// a longer one: "@nora" must not match inside "@nora_fan". The byte right
// after a candidate match is checked against Instagram's own username
// alphabet (domain.ParseUsername's `[A-Za-z0-9._]`) and, if it continues one,
// the search resumes past that occurrence rather than accepting it.
func containsMentionToken(haystack, marker string) bool {
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

// mediaIdentity is the numeric media pk (the `<pk>` half of `<pk>_<owner>`,
// the same composite id posts.go's `sameIdentity` splits) and its shortcode:
// the wire's own, when the payload carries one, otherwise derived from the
// pk with domain's own encoder (the inverse of Shortcode.MediaID).
func mediaIdentity(m wireStoryMedia) (id, shortcode string) {
	pk, _, _ := strings.Cut(m.ID.String(), "_")
	if m.Shortcode != "" {
		return pk, m.Shortcode
	}
	n, err := strconv.ParseUint(pk, 10, 64)
	if err != nil {
		return pk, ""
	}
	mediaID, ok := domain.MediaIDOf(n)
	if !ok {
		return pk, ""
	}
	return pk, mediaID.Shortcode().String()
}

// notificationTextOf prefers the plain text Instagram sent; rich_text is a
// fallback for a story that carries only the decorated form. Neither is the
// comment's own content — see domain.CommentMention.NotificationText.
func notificationTextOf(args wireStoryArgs) string {
	if args.Text != "" {
		return args.Text
	}
	return args.RichText
}

// restIssuerFor resolves the raw REST issuer off a client: the session's HTTP
// client, mirroring social.go's issuerFor for the raw GraphQL path. Only a
// real *instameow.Client satisfies rawSource; the request-path fake does not,
// by design (fakeig's doc comment: it stands in for the typed methods, not
// for a raw *httpclient.HTTPClient).
func restIssuerFor(cli Client) (RESTIssuer, error) {
	source, ok := cli.(rawSource)
	if !ok {
		return nil, errors.New("this client cannot issue raw requests")
	}
	state, ok := cli.(stateDumper)
	if !ok {
		return nil, errors.New("this client cannot report its web session id")
	}
	h := source.GetHTTP()
	if h == nil {
		return nil, fmt.Errorf("%w: no http client", errNotConnected)
	}
	return sessionREST{HTTPClient: h, state: state}, nil
}

// stateDumper is `(*instameow.Client).DumpState`, the one exported path to
// the session's configs (WebSessionID among them).
type stateDumper interface {
	DumpState() (json.RawMessage, error)
}

// sessionREST is the live RESTIssuer: the session's own HTTP client, plus the
// web session id read from the same client's dumped configs on every call.
// Read per call and never cached here, because instameow regenerates it when
// it reloads its configs.
type sessionREST struct {
	*httpclient.HTTPClient
	state stateDumper
}

func (s sessionREST) WebSessionID() (string, error) {
	cfg, err := s.configs()
	if err != nil {
		return "", err
	}
	if cfg.WebSessionID == "" {
		return "", errors.New("no web session id in the dumped state")
	}
	return cfg.WebSessionID, nil
}

// WebFormTokens reads the three web form values off the same dumped configs,
// at the JSON paths `httpclient.Configs` marshals them under: `Jazoest`, and
// the config table's `DTSGInitData.token` and `InstagramWebPushInfo.rollout_hash`
// (types/configs.go:68-70,260,271; types/account.go:73-76). An empty value is
// returned as empty: restDo refuses to send without all three.
func (s sessionREST) WebFormTokens() (jazoest, fbDTSG, rolloutHash string, err error) {
	cfg, err := s.configs()
	if err != nil {
		return "", "", "", err
	}
	if t := cfg.BrowserConfigTable; t != nil {
		fbDTSG, rolloutHash = t.DTSGInitData.Token, t.InstagramWebPushInfo.RolloutHash
	}
	return cfg.Jazoest, fbDTSG, rolloutHash, nil
}

// dumpedConfigs is the part of `DumpState()`'s `Configs` this file reads.
type dumpedConfigs struct {
	WebSessionID       string `json:"WebSessionID"`
	Jazoest            string `json:"Jazoest"`
	BrowserConfigTable *struct {
		DTSGInitData struct {
			Token string `json:"token"`
		} `json:"DTSGInitData"`
		InstagramWebPushInfo struct {
			RolloutHash string `json:"rollout_hash"`
		} `json:"InstagramWebPushInfo"`
	} `json:"BrowserConfigTable"`
}

func (s sessionREST) configs() (dumpedConfigs, error) {
	raw, err := s.state.DumpState()
	if err != nil {
		return dumpedConfigs{}, err
	}
	var dumped struct {
		Configs *dumpedConfigs `json:"Configs"`
	}
	if err := json.Unmarshal(raw, &dumped); err != nil {
		return dumpedConfigs{}, err
	}
	if dumped.Configs == nil {
		return dumpedConfigs{}, errors.New("no configs in the dumped state")
	}
	return *dumped.Configs, nil
}

// schemeWord is a destination scheme with every digit removed, so a scheme
// that ever carries an id cannot put it in a log line.
func schemeWord(scheme string) string {
	return strings.Map(func(r rune) rune {
		if r >= '0' && r <= '9' {
			return -1
		}
		return r
	}, scheme)
}

// destParamNames is the sorted parameter NAMES of a destination, never values.
func destParamNames(q url.Values) string {
	names := make([]string, 0, len(q))
	for k := range q {
		names = append(names, schemeWord(k))
	}
	sort.Strings(names)
	return strings.Join(names, ",")
}
