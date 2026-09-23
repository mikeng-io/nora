package main

// The JSON shapes these end-to-end tests decode, written from the OUTSIDE:
// field names and tags only, the way the Python side reads them. They are
// deliberately not the HTTP layer's own DTO types, so a renamed tag in
// internal/transport/http fails here instead of being decoded by the same
// struct that encoded it.

type healthBody struct {
	OK        bool   `json:"ok"`
	Session   string `json:"session"`
	Connected bool   `json:"connected"`
	Seq       int64  `json:"seq"`
	Self      struct {
		FBID     string `json:"fbid"`
		IGID     string `json:"igid"`
		Username string `json:"username"`
	} `json:"self"`
}

type rosterMember struct {
	FBID     string `json:"fbid"`
	IGID     string `json:"igid"`
	Username string `json:"username"`
	FullName string `json:"full_name"`
}

type rosterBody struct {
	OK      bool           `json:"ok"`
	RoomID  string         `json:"room_id"`
	Title   string         `json:"title"`
	IsGroup bool           `json:"is_group"`
	Members []rosterMember `json:"members"`
}

type profileBody struct {
	OK              bool           `json:"ok"`
	IGID            string         `json:"igid"`
	Username        string         `json:"username"`
	FullName        string         `json:"full_name"`
	FBID            string         `json:"fbid"`
	Following       bool           `json:"following"`
	Blocking        bool           `json:"blocking"`
	IsRestricted    bool           `json:"is_restricted"`
	IsVerified      bool           `json:"is_verified"`
	HasStory        bool           `json:"has_story"`
	LatestReelMedia int            `json:"latest_reel_media"`
	Extra           map[string]any `json:"extra"`
	SocialAvailable bool           `json:"social_available"`
	SocialError     string         `json:"social_error"`
	Social          map[string]any `json:"social"`
}

type storyItem struct {
	ID           string `json:"id"`
	Kind         string `json:"kind"`
	URL          string `json:"url"`
	ThumbnailURL string `json:"thumbnail_url"`
	TakenAt      int    `json:"taken_at"`
	ExpiringAt   int    `json:"expiring_at"`
	Caption      string `json:"caption"`
	AltText      string `json:"alt_text"`
}

type storyBody struct {
	OK         bool        `json:"ok"`
	IGID       string      `json:"igid"`
	Found      bool        `json:"found"`
	Username   string      `json:"username"`
	ExpiringAt int         `json:"expiring_at"`
	Seen       int         `json:"seen"`
	Items      []storyItem `json:"items"`
}

// maxJSONBody is the HTTP layer's bound on a control route's body (1 MiB).
const maxJSONBody = 1 << 20
