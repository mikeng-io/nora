package instameow

// `GET /accounts/{igid}/items`: one page of an account's posts and reels,
// newest first, over the web app's profile timeline document
// (`PolarisProfilePostsTabContentQuery_connection`,
// design/cognition-loop/22-social-stream.md §1a, §7).
//
// Before S0 no mechanism for this list was known anywhere (P13's
// investigation; the social stream's back-fill waited on it). The document is
// the one the web client sends when a profile's posts tab scrolls: 12 items a
// page, paged by an opaque `after` cursor, posted to `/graphql/query` (not
// `/api/graphql`; docs.go says why that needs its own request).
//
// # Keyed on the username, checked against the igid
//
// The document takes a USERNAME, unlike the profile document, which takes the
// igid (social_profile.go's corrected mistake). The caller passes it, or the
// gateway resolves it through the profile read. A username is renameable and
// a freed one can be taken, so it could name somebody else: every item must
// name its owner (the `<pk>_<owner igid>` id, `user.pk`, `owner.pk` or
// `owner.id`), and the igid asked about must be that owner or one of the
// item's co-authors (`coauthor_producers`), or the page is refused rather
// than recorded under the wrong person.
//
// A collaboration post is on the co-author's grid under its owner's id:
// measured 2026-10-16, 11 of one member's first 12 items were collabs whose
// owner was another account. Those are the member's posts too, and are kept,
// marked `Collab`.
//
// # What crosses into a span or an error
//
// Nothing of the page: the route is PatternOnlyRoute, graphqlDoc's span
// carries the document name only, and every error here has a fixed text. A
// caption is people's words and a media url names somebody's post; neither
// leaves the 200 body.

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"strings"

	"instagram-bridge/internal/domain"
)

// timelinePageSize is the web client's own page size (§1a). Not tuned: a
// read that asks for a different count is a request the web client never
// sends.
const timelinePageSize = 12

// timelineDoc is the timeline document (§1a), posted to `/graphql/query`.
func timelineDoc(docID string) webDoc {
	return webDoc{
		friendlyName: "PolarisProfilePostsTabContentQuery_connection",
		endpoint:     graphqlQueryEndpoint,
		docID:        docID,
		env:          EnvTimelineDocID,
		label:        "timeline query",
		root:         "xdt_api__v1__feed__user_timeline_graphql_connection",
		// Measured 2026-10-16: every page, in the web client too, answers its
		// 12 edges AND a `field_exception` error beside them. The web app reads
		// the edges; so does the bridge.
		partialOK: true,
	}
}

// timelineVars is the document's variables in the captured order and with
// the captured values (§1a). `after` and `before` are sent as null, not
// omitted, as the web client sends them.
type timelineVars struct {
	After  *string           `json:"after"`
	Before *string           `json:"before"`
	Data   timelineVarsInner `json:"data"`
	First  int               `json:"first"`
	// The relay flags and counts below are the web client's own values. None
	// is a choice this bridge makes.
	IncludeMultiCaptions bool   `json:"include_multi_captions"`
	Last                 *int   `json:"last"`
	Username             string `json:"username"`
	MultiCaptionCarousel bool   `json:"__relay_internal__pv__PolarisMultiCaptionCarouselEnabledrelayprovider"`
	ShortDrama           bool   `json:"__relay_internal__pv__PolarisShortDramaEnabledrelayprovider"`
	ThreadsStyleComments bool   `json:"__relay_internal__pv__PolarisThreadsStyleCommentsEnabledrelayprovider"`
	ReelsRecoDebug       bool   `json:"__relay_internal__pv__PolarisReelsRecoDebugOverlayEnabledrelayprovider"`
}

type timelineVarsInner struct {
	Count                         int  `json:"count"`
	IncludeReelMediaSeenTimestamp bool `json:"include_reel_media_seen_timestamp"`
	IncludeRelationshipInfo       bool `json:"include_relationship_info"`
	LatestBestiesReelMedia        bool `json:"latest_besties_reel_media"`
	LatestReelMedia               bool `json:"latest_reel_media"`
}

func newTimelineVars(username, after string) timelineVars {
	v := timelineVars{
		Data: timelineVarsInner{
			Count:                         timelinePageSize,
			IncludeReelMediaSeenTimestamp: true,
			IncludeRelationshipInfo:       true,
			LatestBestiesReelMedia:        true,
			LatestReelMedia:               true,
		},
		First:      timelinePageSize,
		Username:   username,
		ShortDrama: true,
	}
	if after != "" {
		v.After = &after
	}
	return v
}

// timelineNode is one edge's node, decoded only as far as the route reads it.
type timelineNode struct {
	Code    string `json:"code"`
	PK      flexID `json:"pk"`
	ID      flexID `json:"id"`
	TakenAt int64  `json:"taken_at"`
	Caption *struct {
		Text string `json:"text"`
	} `json:"caption"`
	MediaType          int    `json:"media_type"`
	ProductType        string `json:"product_type"`
	CarouselMediaCount int    `json:"carousel_media_count"`
	// AccessibilityCaption is Instagram's own description of the image.
	AccessibilityCaption string `json:"accessibility_caption"`
	timelineMedia
	// CarouselMedia are a carousel's children, each of the same shape.
	CarouselMedia []*timelineMedia `json:"carousel_media"`
	// The owner, from whichever of these the node carries; ownersOf reads
	// all of them.
	User *struct {
		PK flexID `json:"pk"`
	} `json:"user"`
	Owner *struct {
		PK flexID `json:"pk"`
		ID flexID `json:"id"`
	} `json:"owner"`
	// CoauthorProducers are a collaboration post's co-authors.
	CoauthorProducers []struct {
		PK flexID `json:"pk"`
		ID flexID `json:"id"`
	} `json:"coauthor_producers"`
}

// timelineMedia is the renditions one image or video carries, in the shape
// `GET /posts/{shortcode}` reads (posts.go's postItemOf).
type timelineMedia struct {
	ImageVersions2 struct {
		Candidates []mediaCandidate `json:"candidates"`
	} `json:"image_versions2"`
	VideoVersions []mediaCandidate `json:"video_versions"`
	VideoDuration float64          `json:"video_duration"`
}

// item reduces the renditions to one fetchable image or video.
func (m *timelineMedia) item() (domain.PostItem, bool) {
	return pickMedia(m.VideoVersions, m.ImageVersions2.Candidates, m.VideoDuration)
}

// items is the node's media: every carousel child in order, else the node's
// own image or video. A child with no url is skipped, logged by position
// only.
func (n *timelineNode) items(ctx context.Context) []domain.PostItem {
	out := []domain.PostItem{}
	if len(n.CarouselMedia) == 0 {
		if pi, ok := n.timelineMedia.item(); ok {
			out = append(out, pi)
		}
		return out
	}
	for i, child := range n.CarouselMedia {
		if child == nil {
			continue
		}
		pi, ok := child.item()
		if !ok {
			slog.WarnContext(ctx, "timeline carousel item had no media url; skipped", "index", i)
			continue
		}
		out = append(out, pi)
	}
	return out
}

// coauthoredBy says whether igid is one of the node's co-authors.
func (n *timelineNode) coauthoredBy(igid string) bool {
	for _, c := range n.CoauthorProducers {
		if c.PK.String() == igid || c.ID.String() == igid {
			return true
		}
	}
	return false
}

// ownersOf is every owner igid the node names: the `<pk>_<owner>` suffix of
// its id, `user.pk`, `owner.pk`, `owner.id`.
func (n *timelineNode) ownersOf() []string {
	var out []string
	if _, owner, ok := strings.Cut(n.ID.String(), "_"); ok && owner != "" {
		out = append(out, owner)
	}
	if n.User != nil && n.User.PK != "" {
		out = append(out, n.User.PK.String())
	}
	if n.Owner != nil {
		for _, id := range []flexID{n.Owner.PK, n.Owner.ID} {
			if id != "" {
				out = append(out, id.String())
			}
		}
	}
	return out
}

var (
	// errNoTimeline is a 200 with no timeline connection and no errors
	// either: refused, never read as "this account has posted nothing".
	errNoTimeline = domain.Classify(domain.ErrUpstreamRejected,
		errors.New("timeline query returned no timeline"))
	// errOtherOwner is a page carrying an item owned by somebody other than
	// the igid asked about (the username renamed between the two calls, or a
	// caller's username naming someone else), or an item that names no owner
	// at all, which cannot be shown to be this account's.
	errOtherOwner = domain.Classify(domain.ErrUpstreamRejected,
		errors.New("timeline query returned an item not shown to be this account's"))
	// errNoEndCursor is `has_next_page: true` with no cursor to follow: a
	// next_cursor of null would claim the end of a timeline that has more.
	errNoEndCursor = domain.Classify(domain.ErrUpstreamRejected,
		errors.New("timeline query said there is a next page and gave no cursor"))
)

// readTimeline reads one page of `username`'s timeline, checking every
// owner it names against igid.
func readTimeline(
	ctx context.Context, issuer DocIssuer, docID string,
	igid domain.IGID, username domain.Username, after string,
) (domain.TimelinePage, error) {
	referer := restOrigin + "/" + username.String() + "/"
	_, raw, err := graphqlDoc(ctx, issuer, timelineDoc(docID), referer, newTimelineVars(username.String(), after))
	if err != nil {
		return domain.TimelinePage{}, err
	}
	return parseTimeline(ctx, raw, igid)
}

// parseTimeline reads the page out of a document graphqlDoc has already
// accepted, with no network underneath it.
func parseTimeline(ctx context.Context, raw []byte, igid domain.IGID) (domain.TimelinePage, error) {
	var envelope struct {
		Data struct {
			Conn *struct {
				Edges []struct {
					Node *timelineNode `json:"node"`
				} `json:"edges"`
				PageInfo struct {
					EndCursor   *string `json:"end_cursor"`
					HasNextPage bool    `json:"has_next_page"`
				} `json:"page_info"`
			} `json:"xdt_api__v1__feed__user_timeline_graphql_connection"`
		} `json:"data"`
	}
	if err := json.Unmarshal(raw, &envelope); err != nil {
		// Fixed text: a decode error can quote a byte of a caption.
		return domain.TimelinePage{}, domain.Classify(domain.ErrUpstreamRejected,
			&fixedTextError{msg: fmt.Sprintf("timeline query did not decode (%d bytes)", len(raw)), cause: err})
	}
	conn := envelope.Data.Conn
	if conn == nil {
		return domain.TimelinePage{}, errNoTimeline
	}

	page := domain.TimelinePage{Items: []domain.TimelineItem{}}
	for _, edge := range conn.Edges {
		n := edge.Node
		if n == nil {
			continue
		}
		owners := n.ownersOf()
		if len(owners) == 0 {
			return domain.TimelinePage{}, errOtherOwner
		}
		// The owner fields must agree with each other: an item whose id
		// suffix and user.pk name different accounts is refused, whoever the
		// co-authors are.
		for _, owner := range owners[1:] {
			if owner != owners[0] {
				return domain.TimelinePage{}, errOtherOwner
			}
		}
		owned := owners[0] == igid.String()
		collab := !owned && n.coauthoredBy(igid.String())
		if !owned && !collab {
			return domain.TimelinePage{}, errOtherOwner
		}
		pk := n.PK.String()
		if pk == "" {
			pk, _, _ = strings.Cut(n.ID.String(), "_")
		}
		if n.Code == "" {
			// No shortcode: nothing can read the item (`GET /posts/{shortcode}`)
			// and nothing can key it. Counted in the log, never fatal to the
			// page, which is still a true list of what else is there.
			slog.WarnContext(ctx, "timeline item had no code; skipped", "igid", igid.String())
			continue
		}
		item := domain.TimelineItem{
			Code:          n.Code,
			PK:            pk,
			TakenAt:       n.TakenAt,
			Kind:          "post",
			MediaType:     n.MediaType,
			CarouselCount: n.CarouselMediaCount,
			Collab:        collab,
			Items:         n.items(ctx),
			AltText:       n.AccessibilityCaption,
		}
		// A reel is `product_type: clips` (§1a); a video posted to the feed
		// is still a post.
		if n.ProductType == "clips" {
			item.Kind = "reel"
		}
		if n.Caption != nil {
			item.Caption = n.Caption.Text
		}
		page.Items = append(page.Items, item)
	}

	if conn.PageInfo.HasNextPage {
		if conn.PageInfo.EndCursor == nil || *conn.PageInfo.EndCursor == "" {
			return domain.TimelinePage{}, errNoEndCursor
		}
		cursor := *conn.PageInfo.EndCursor
		page.NextCursor = &cursor
	}
	return page, nil
}
