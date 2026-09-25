package instameow

// GET /mentions — comment-mentions from Instagram's activity/notification
// feed. Step one toward a Grok-style public "summons" reply: Nora needs to
// learn when someone @mentions her in a COMMENT on a post before anything can
// answer it.
//
// # What the upstream call really is
//
// `GET https://www.instagram.com/api/v1/news/inbox/` — Instagram WEB's own
// notifications panel, issued with the web client's headers (call.go's
// `rest` helper). UNMEASURED: no live response has been recorded. The shape
// below is a HYPOTHESIS carried over from the private API and parsed
// TOLERANTLY: a top-level `new_stories`/`old_stories` pair, each story an int
// `story_type`/`type` and an `args` object (`text`, `rich_text`,
// `profile_id`, `profile_name`, `timestamp`, `media[]`,
// `comment_id`/`comment_ids`). fakeig/mentions.go's fixture is built from
// this hypothesis, not captured bytes, and is labelled so.
//
// # Classification is STRUCTURAL, not English wording
//
// A story is a comment-mention when it has a comment id (`args.comment_id`
// or `args.comment_ids[0]`) AND carries media AND its text/rich_text
// contains an "@" token naming THIS account — its own username, read off the
// live session (never a hardcoded string) — rather than a keyword search for
// a phrase like "mentioned you in a comment", which is locale-dependent and
// something this route has not measured Instagram to send at all. A mention
// IN A CAPTION rather than a comment can carry the same "@" token and even
// the same media, but no comment id, which is why the comment id is a
// separate, required check rather than folded into "looks like a mention".
//
// Everything not classified as a mention is SKIPPED but COUNTED by its
// `story_type`, so the live probe (still to run) can read which numeric
// type(s) a real comment-mention arrives as.
//
// # What never crosses into a span or an error
//
// The only span this file's call opens is `rest`'s own
// `instagram.rest news_inbox`, and this file adds no attribute to it — only
// counts ever leave readMentions (transport/http/mentions.go's job). Every
// error this file raises has a FIXED text, the same `postReadError` pattern
// posts.go uses: no igid, username, media id, comment id or comment text
// ever reaches `Error()`.

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"strings"

	"instagram-bridge/internal/domain"

	"go.mau.fi/mautrix-meta/pkg/messagix/httpclient"
)

// newsInboxEndpoint has no entry in the library's endpoint table
// (pkg/messagix/endpoints/instagram.go), so it is written out in full,
// exactly as specified.
const newsInboxEndpoint = "https://www.instagram.com/api/v1/news/inbox/"

// wireNewsInbox is the top-level envelope.
type wireNewsInbox struct {
	NewStories []wireStory `json:"new_stories"`
	OldStories []wireStory `json:"old_stories"`
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
	ProfileID   string           `json:"profile_id"`
	ProfileName string           `json:"profile_name"`
	Timestamp   float64          `json:"timestamp"`
	Media       []wireStoryMedia `json:"media"`
	CommentID   string           `json:"comment_id"`
	CommentIDs  []string         `json:"comment_ids"`
}

// wireStoryMedia is one entry of `args.media`: `id` is `<pk>_<owner igid>`,
// the same composite shape posts.go's `sameIdentity` already splits.
type wireStoryMedia struct {
	ID        string `json:"id"`
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
// selfUsername is the account's OWN handle (Bridge.selfSnapshot().Username):
// the structural token every comment-mention this route classifies must
// carry ("@" + that username). An empty selfUsername — identity not yet
// known — classifies nothing rather than guessing; see mentionsAccount.
func readMentions(ctx context.Context, issuer RESTIssuer, selfUsername string) ([]domain.CommentMention, map[int]int, error) {
	_, raw, err := rest(ctx, issuer, newsInboxEndpoint, "news_inbox")
	if err != nil {
		return nil, nil, err
	}

	var envelope wireNewsInbox
	if jsonErr := json.Unmarshal(raw, &envelope); jsonErr != nil {
		// rest() already refuses a non-JSON 200 before returning bytes, so
		// this is reachable only if the envelope decodes as JSON but not as
		// this shape (e.g. a bare JSON array). Refused the same way, with a
		// fixed text.
		return nil, nil, domain.Classify(domain.ErrUpstreamRejected,
			&mentionsReadError{msg: "news_inbox did not return the expected shape", cause: jsonErr})
	}

	stories := make([]wireStory, 0, len(envelope.NewStories)+len(envelope.OldStories))
	stories = append(stories, envelope.NewStories...)
	stories = append(stories, envelope.OldStories...)

	mentions := make([]domain.CommentMention, 0)
	skipped := make(map[int]int)
	for _, st := range stories {
		mention, ok := commentMentionOf(st, selfUsername)
		if !ok {
			skipped[st.StoryType]++
			continue
		}
		mentions = append(mentions, mention)
	}
	return mentions, skipped, nil
}

// commentMentionOf classifies one story.
func commentMentionOf(st wireStory, selfUsername string) (domain.CommentMention, bool) {
	commentID := st.Args.CommentID
	if commentID == "" && len(st.Args.CommentIDs) > 0 {
		commentID = st.Args.CommentIDs[0]
	}
	if commentID == "" || len(st.Args.Media) == 0 || !mentionsAccount(st.Args, selfUsername) {
		return domain.CommentMention{}, false
	}
	mediaID, shortcode := mediaIdentity(st.Args.Media[0])
	return domain.CommentMention{
		StoryType:      st.StoryType,
		At:             int64(st.Args.Timestamp),
		AuthorIGID:     st.Args.ProfileID,
		AuthorUsername: st.Args.ProfileName,
		MediaID:        mediaID,
		MediaShortcode: shortcode,
		CommentID:      commentID,
		CommentText:    commentTextOf(st.Args),
	}, true
}

// mentionsAccount is the structural check this file's doc comment describes:
// an "@" token naming THIS account's own username, in either text field.
func mentionsAccount(args wireStoryArgs, selfUsername string) bool {
	if selfUsername == "" {
		// Identity not yet known: fail closed rather than guess at who "the
		// account" is.
		return false
	}
	marker := "@" + strings.ToLower(selfUsername)
	return strings.Contains(strings.ToLower(args.Text), marker) ||
		strings.Contains(strings.ToLower(args.RichText), marker)
}

// mediaIdentity is the numeric media pk (the `<pk>` half of `<pk>_<owner>`,
// the same composite id posts.go's `sameIdentity` splits) and its shortcode:
// the wire's own, when the payload carries one, otherwise derived from the
// pk with domain's own encoder (the inverse of Shortcode.MediaID).
func mediaIdentity(m wireStoryMedia) (id, shortcode string) {
	pk, _, _ := strings.Cut(m.ID, "_")
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

// commentTextOf prefers the plain text Instagram sent; rich_text is a
// fallback for a story that carries only the decorated form.
func commentTextOf(args wireStoryArgs) string {
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
	raw, err := s.state.DumpState()
	if err != nil {
		return "", err
	}
	var dumped struct {
		Configs *struct {
			WebSessionID string `json:"WebSessionID"`
		} `json:"Configs"`
	}
	if err := json.Unmarshal(raw, &dumped); err != nil {
		return "", err
	}
	if dumped.Configs == nil || dumped.Configs.WebSessionID == "" {
		return "", errors.New("no web session id in the dumped state")
	}
	return dumped.Configs.WebSessionID, nil
}
