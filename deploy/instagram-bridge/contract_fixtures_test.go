package main

// The account and story routes' response bodies, pinned as files BOTH sides
// read (design/instagram-adoption/17 P4).
//
// Nora's Python client (`nora/infra/social/instagram_client.py`) is tested
// against `tests/fixtures/instagram_bridge/*.json`, and this file is what
// makes those bytes the bridge's: each fixture is GENERATED here by the real
// handler, through the real router, over the same fakes the rest of this
// package's tests use. A change to `profileBody`, `storyBody`, a status
// mapping or an error envelope therefore fails here first, and the committed
// fixture it forces is what the Python tests then read. Neither side can
// drift alone.
//
// Every value is SYNTHETIC — made-up igids, usernames, bios and CDN paths in
// the placeholder ranges `identity.example.toml` uses. Nothing is copied
// from the live account (AGENTS.md: fixtures are generated, never pasted).
//
// Regenerate after an intended contract change, then review the diff:
//
//	go test -run TestContractFixtures -update ./...

import (
	"bytes"
	"encoding/json"
	"flag"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"

	gateway "instagram-bridge/internal/gateway/instameow"

	"go.mau.fi/mautrix-meta/pkg/instameow/slidetypes"
	"go.mau.fi/mautrix-meta/pkg/messagix/httpclient"
	"go.mau.fi/mautrix-meta/pkg/messagix/responses"
)

var updateFixtures = flag.Bool("update", false,
	"rewrite tests/fixtures/instagram_bridge from the handlers")

// fixtureDir is the repository's shared fixture directory, relative to this
// package.
const fixtureDir = "../../tests/fixtures/instagram_bridge"

// skipOutsideTheRepo skips the fixture checks in ONE place only: the image
// build, whose context is this directory, so `tests/fixtures` is not in it.
// The Dockerfile says so explicitly with IG_BRIDGE_IMAGE_BUILD=1; anywhere
// else a missing fixture directory is still a failure, never a skip.
func skipOutsideTheRepo(t *testing.T) {
	t.Helper()
	if *updateFixtures || os.Getenv("IG_BRIDGE_IMAGE_BUILD") != "1" {
		return
	}
	if _, err := os.Stat(fixtureDir); err != nil {
		t.Skipf("image build: %s is outside the build context", fixtureDir)
	}
}

// contractFixture is the on-disk shape. `route` and `path` document what was
// asked; `status` and `body` are what the bridge answered.
type contractFixture struct {
	Route  string          `json:"route"`
	Path   string          `json:"path"`
	Status int             `json:"status"`
	Body   json.RawMessage `json:"body"`
}

const (
	synthIGID     = "10000001"
	synthFBID     = 100000000000001
	synthUsername = "example_friend"
)

func synthProfileUser() *slidetypes.User {
	return &slidetypes.User{
		InteropMessagingUserFBID: synthFBID,
		ID:                       synthIGID,
		Username:                 synthUsername,
		FullName:                 "Example Friend",
		LatestReelMedia:          1700000000,
		FriendshipStatus: struct {
			IsRestricted bool `json:"is_restricted"`
			Blocking     bool `json:"blocking"`
			Following    bool `json:"following"`
		}{Following: true},
	}
}

// synthSocialEnvelope is a `PolarisProfilePageContentQuery` answer in the
// shape `parseSocialProfile` reads.
const synthSocialEnvelope = `{"data":{"user":{
	"is_private": true,
	"biography": "Synthetic bio for contract tests.",
	"external_url": "https://example.invalid/",
	"follower_count": 120,
	"following_count": 80,
	"mutual_followers_count": 3,
	"media_count": 42,
	"total_clips_count": 2,
	"is_business": false,
	"is_professional_account": null,
	"category": null,
	"profile_pic_url": "https://scontent.cdninstagram.com/v/t51/synthetic-pic.jpg",
	"friendship_status": {"followed_by": true}
}}}`

// synthGraphQLErrors is the 200-with-`errors` shape the social half refuses.
const synthGraphQLErrors = `{"errors":[{"message":"synthetic field exception","code":1675030,"summary":"Query error"}],"data":null}`

func synthReel() *responses.ReelInfoResponse {
	image := &responses.ReelItem{ExpiringAt: 1700086400}
	image.Pk = "900000000000000001"
	image.TakenAt = 1700000000
	image.MediaType = 1
	// The author's words and Instagram's description of the frame. The
	// bridge forwards both; the Python client must not (a story result is a
	// reference, never content), and these are the values it asserts against.
	image.Caption.Text = "synthetic caption text"
	image.AccessibilityCaption = "synthetic alt text"
	image.ImageVersions2.Candidates = []responses.Candidates{
		{Width: 1080, Height: 1920, URL: "https://scontent.cdninstagram.com/v/t51/synthetic-1.jpg?oh=00_A&oe=0"},
	}

	video := &responses.ReelItem{ExpiringAt: 1700086400}
	video.Pk = "900000000000000002"
	video.TakenAt = 1700003600
	video.MediaType = 2
	video.ImageVersions2.Candidates = []responses.Candidates{
		{Width: 1080, Height: 1920, URL: "https://scontent.cdninstagram.com/v/t51/synthetic-2-cover.jpg?oh=00_B&oe=0"},
	}
	video.VideoVersions = []responses.VideoVersions{
		{Width: 1080, URL: "https://instagram.fxxx1-1.fbcdn.net/v/t50/synthetic-2.mp4?oh=00_C&oe=0"},
	}

	return &responses.ReelInfoResponse{
		Reels: map[string]responses.ReelInfo{
			synthIGID: {
				ID:         synthIGID,
				ExpiringAt: 1700086400,
				Seen:       0,
				User:       &responses.User{Username: synthUsername},
				Items:      []*responses.ReelItem{image, video},
			},
		},
	}
}

// contractCase is one fixture: how to set the bridge up, and what to ask.
type contractCase struct {
	file   string
	route  string
	path   string
	fake   func() *fakeIG
	social func() gateway.GraphQLIssuer // nil: the default issuer resolution
	opts   []bridgeOpt                  // optional session adjustments
}

func contractCases() []contractCase {
	return []contractCase{
		{
			file: "account_ok.json", route: "GET /accounts/{igid}", path: "/accounts/" + synthIGID,
			fake:   func() *fakeIG { return &fakeIG{ProfileUserSet: true, ProfileUser: synthProfileUser()} },
			social: func() gateway.GraphQLIssuer { return &fakeGraphQLIssuer{body: []byte(synthSocialEnvelope)} },
		},
		{
			// GetProfile answered; the second, social call did not. The read
			// still succeeds, and `social_available: false` says what is
			// missing — an empty bio here is NOT "this account has no bio".
			file: "account_social_unavailable.json", route: "GET /accounts/{igid}", path: "/accounts/" + synthIGID,
			fake:   func() *fakeIG { return &fakeIG{ProfileUserSet: true, ProfileUser: synthProfileUser()} },
			social: func() gateway.GraphQLIssuer { return &fakeGraphQLIssuer{body: []byte(synthGraphQLErrors)} },
		},
		{
			// 200 with no user: the igid names no account (`errNoProfile`).
			file: "account_not_found_404.json", route: "GET /accounts/{igid}", path: "/accounts/" + synthIGID,
			fake: func() *fakeIG { return &fakeIG{ProfileUserSet: true, ProfileUser: nil} },
		},
		{
			// The session needs a human; retrying hammers a challenged account.
			file: "account_checkpointed_409.json", route: "GET /accounts/{igid}", path: "/accounts/" + synthIGID,
			fake: func() *fakeIG { return &fakeIG{ProfileErr: httpclient.ErrCheckpointRequired} },
		},
		{
			// Between sessions: no client to read with.
			file: "account_reconnecting_503.json", route: "GET /accounts/{igid}", path: "/accounts/" + synthIGID,
			fake: func() *fakeIG { return &fakeIG{} },
			opts: []bridgeOpt{withNoClient()},
		},
		{
			file: "story_found.json", route: "GET /accounts/{igid}/story", path: "/accounts/" + synthIGID + "/story",
			fake: func() *fakeIG { return &fakeIG{ReelSet: true, ReelResp: synthReel()} },
		},
		{
			// No reel for the igid: no story up, OR not permitted to see it.
			// The route has no "no such account" answer; this is it too.
			file: "story_not_found.json", route: "GET /accounts/{igid}/story", path: "/accounts/" + synthIGID + "/story",
			fake: func() *fakeIG { return &fakeIG{ReelSet: true, ReelResp: &responses.ReelInfoResponse{}} },
		},
		{
			file: "story_checkpointed_409.json", route: "GET /accounts/{igid}/story", path: "/accounts/" + synthIGID + "/story",
			fake: func() *fakeIG { return &fakeIG{ReelErr: httpclient.ErrCheckpointRequired} },
		},
	}
}

func runContractCase(t *testing.T, tc contractCase) contractFixture {
	t.Helper()
	opts := append([]bridgeOpt{}, tc.opts...)
	if tc.social != nil {
		opts = append(opts, withIssuer(tc.social()))
	}
	b := newTestBridge(t, tc.fake(), opts...)
	req := httptest.NewRequest(http.MethodGet, tc.path, nil)
	rec := httptest.NewRecorder()
	b.routes().ServeHTTP(rec, req)
	return contractFixture{
		Route: tc.route, Path: tc.path, Status: rec.Code,
		Body: json.RawMessage(bytes.TrimSpace(rec.Body.Bytes())),
	}
}

// TestContractFixtures regenerates (with -update) or checks every shared
// fixture against what the handlers answer today.
func TestContractFixtures(t *testing.T) {
	skipOutsideTheRepo(t)
	for _, tc := range contractCases() {
		t.Run(tc.file, func(t *testing.T) {
			got := runContractCase(t, tc)
			path := filepath.Join(fixtureDir, tc.file)
			if *updateFixtures {
				blob, err := json.MarshalIndent(got, "", "  ")
				if err != nil {
					t.Fatal(err)
				}
				if err := os.MkdirAll(fixtureDir, 0o755); err != nil {
					t.Fatal(err)
				}
				if err := os.WriteFile(path, append(blob, '\n'), 0o644); err != nil {
					t.Fatal(err)
				}
				return
			}
			raw, err := os.ReadFile(path)
			if err != nil {
				t.Fatalf("missing fixture %s (run with -update): %v", path, err)
			}
			var want contractFixture
			if err := json.Unmarshal(raw, &want); err != nil {
				t.Fatalf("fixture %s does not decode: %v", path, err)
			}
			if got.Route != want.Route || got.Path != want.Path || got.Status != want.Status {
				t.Fatalf("%s: route/path/status = %s %s %d, fixture has %s %s %d",
					tc.file, got.Route, got.Path, got.Status, want.Route, want.Path, want.Status)
			}
			// Compared as decoded JSON, not bytes: indentation is the
			// fixture's, key order and spacing are the encoder's.
			var gotBody, wantBody any
			if err := json.Unmarshal(got.Body, &gotBody); err != nil {
				t.Fatalf("handler body is not JSON: %v (%s)", err, got.Body)
			}
			if err := json.Unmarshal(want.Body, &wantBody); err != nil {
				t.Fatalf("fixture body is not JSON: %v", err)
			}
			if !reflect.DeepEqual(gotBody, wantBody) {
				t.Errorf("%s: the handler's body no longer matches the shared fixture — "+
					"Nora's client is tested against the fixture, so this is a contract "+
					"change on both sides.\n got: %s\nwant: %s", tc.file, got.Body, want.Body)
			}
		})
	}
}

// TestContractFixturesHaveNoOrphans: every committed fixture is produced by a
// case above. A fixture nothing regenerates is a shape the bridge is no longer
// held to while the Python tests still read it.
func TestContractFixturesHaveNoOrphans(t *testing.T) {
	skipOutsideTheRepo(t)
	entries, err := os.ReadDir(fixtureDir)
	if err != nil {
		t.Fatalf("cannot read %s: %v", fixtureDir, err)
	}
	made := map[string]bool{}
	for _, tc := range contractCases() {
		made[tc.file] = true
	}
	var orphans []string
	for _, e := range entries {
		if strings.HasSuffix(e.Name(), ".json") && !made[e.Name()] {
			orphans = append(orphans, e.Name())
		}
	}
	sort.Strings(orphans)
	if len(orphans) > 0 {
		t.Errorf("fixtures no case generates: %v", orphans)
	}
}
