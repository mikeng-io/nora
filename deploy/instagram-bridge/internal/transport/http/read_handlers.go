package httptransport

import (
	"net/http"
	"strings"
	"unicode"

	"instagram-bridge/internal/domain"
)

// Every path id is parsed HERE, at the edge, before the use case runs, so a
// malformed one is 400 whatever the session's state (16 §Resources: "a
// malformed id is 400 before any use case runs"). Before P14 the parse sat
// behind the session gate and a checkpointed session answered 409 first; that
// was the reorder debt accounts_search.go's header named.
//
// The refusal messages are FIXED: the parsers' own error text quotes the raw
// input, and a path segment is whatever a caller typed.

const (
	invalidThreadMessage = "thread_fbid must be a positive decimal thread id (the SHORT id)"
	invalidIGIDMessage   = "igid must be a positive decimal instagram account id"
)

// threadFromPath parses `{thread_fbid}`, answering 400 itself when it does not
// parse. The LONG thread id is all digits and is refused here too: it
// overflows int64, and a room key in the wrong id space is exactly the
// mistake ThreadFBID exists to stop.
func threadFromPath(w http.ResponseWriter, r *http.Request) (domain.ThreadFBID, bool) {
	room, err := domain.ParseThreadFBID(r.PathValue("thread_fbid"))
	if err != nil {
		invalidRequest(w, invalidThreadMessage)
		return domain.ThreadFBID{}, false
	}
	return room, true
}

func igidFromPath(w http.ResponseWriter, r *http.Request) (domain.IGID, bool) {
	igid, err := domain.ParseIGID(r.PathValue("igid"))
	if err != nil {
		invalidRequest(w, invalidIGIDMessage)
		return domain.IGID{}, false
	}
	return igid, true
}

// handleRoster answers GET /threads/{thread_fbid}/members.
//
// A GET on the thread's own path, because it is a read with no side effect.
// The room key is the SHORT thread id, exactly as every other route takes it;
// the long id never crosses this boundary in either direction. Fail closed:
// never a 200 with a partial answer.
func (s *Server) handleRoster(w http.ResponseWriter, r *http.Request) {
	room, ok := threadFromPath(w, r)
	if !ok {
		return
	}
	thread, err := s.app.Members(r.Context(), room)
	if err != nil {
		failEnvelope(r.Context(), w, "roster", err, "thread_fbid", room.String())
		return
	}
	writeJSON(w, http.StatusOK, rosterOf(room.String(), thread))
}

// handleProfile answers GET /accounts/{igid}: the account, and the outcome of
// its independent social half.
//
// The IGID, not the fbid: this is the social id space. A messaging fbid is
// also a decimal string, so it validates, and then names a different account
// or none at all.
func (s *Server) handleProfile(w http.ResponseWriter, r *http.Request) {
	igid, ok := igidFromPath(w, r)
	if !ok {
		return
	}
	read, err := s.app.ReadAccount(r.Context(), igid)
	if err != nil {
		failEnvelope(r.Context(), w, "profile", err, "igid", igid.String())
		return
	}
	writeJSON(w, http.StatusOK, profileOf(read.Account, read.Social))
}

// handleStory answers GET /accounts/{igid}/story.
func (s *Server) handleStory(w http.ResponseWriter, r *http.Request) {
	igid, ok := igidFromPath(w, r)
	if !ok {
		return
	}
	story, err := s.app.ReadStory(r.Context(), igid)
	if err != nil {
		failEnvelope(r.Context(), w, "story", err, "igid", igid.String())
		return
	}
	writeJSON(w, http.StatusOK, storyOf(story))
}

// maxCursorLength bounds `?after=`. The cursor is Instagram's opaque
// `end_cursor`, handed back unchanged; nothing longer than this has a reason
// to be one, and an unbounded query value would ride into the request body.
const maxCursorLength = 1024

// handleItems answers GET /accounts/{igid}/items?after=<cursor>&username=<u>:
// one page of the account's posts and reels, newest first. `next_cursor` is
// null at the end of the timeline. `username` is optional: given, the bridge
// skips the profile read and pages that username, refusing the page unless
// every item's owner is the igid; it must be a bare handle (no `@`).
func (s *Server) handleItems(w http.ResponseWriter, r *http.Request) {
	igid, ok := igidFromPath(w, r)
	if !ok {
		return
	}
	after := r.URL.Query().Get("after")
	if len(after) > maxCursorLength || strings.ContainsFunc(after, unicode.IsControl) {
		invalidRequest(w, "after must be the next_cursor a previous page returned")
		return
	}
	var username domain.Username
	if raw := r.URL.Query().Get("username"); raw != "" {
		u, err := domain.ParseUsername(raw)
		// Strict: the bare handle only. ParseUsername also accepts a leading
		// `@`, which a caller holding the profile's own value never sends.
		if err != nil || u.String() != raw {
			invalidRequest(w, "username must be an instagram username, without @")
			return
		}
		username = u
	}
	page, err := s.app.ReadItems(r.Context(), igid, username, after)
	if err != nil {
		failEnvelope(r.Context(), w, "items", err, "igid", igid.String())
		return
	}
	writeJSON(w, http.StatusOK, itemsOf(page))
}
