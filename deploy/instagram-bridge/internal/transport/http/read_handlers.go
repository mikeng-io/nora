package httptransport

import "net/http"

// handleRoster answers GET /threads/{thread_fbid}/members.
//
// A GET on the thread's own path, because it is a read with no side effect.
// The room key is the SHORT thread id, exactly as every other route takes it;
// the long id never crosses this boundary in either direction. Fail closed:
// never a 200 with a partial answer.
func (s *Server) handleRoster(w http.ResponseWriter, r *http.Request) {
	roomID := r.PathValue("thread_fbid")
	if roomID == "" {
		badRequest(w, "thread_fbid is required")
		return
	}
	room, thread, err := s.app.Members(r.Context(), roomID)
	if err != nil {
		fail(r.Context(), w, "roster", roomID, err)
		return
	}
	writeJSON(w, http.StatusOK, rosterOf(room.String(), thread))
}

// handleProfile answers GET /accounts/{igid}: the account, and the outcome of
// its independent social half.
//
// The IGID, not the fbid — this is the social id space. A messaging fbid is
// also a decimal string, so it validates, and then names a different account
// or none at all.
func (s *Server) handleProfile(w http.ResponseWriter, r *http.Request) {
	igid := r.PathValue("igid")
	if igid == "" {
		badRequest(w, "igid is required")
		return
	}
	read, err := s.app.ReadAccount(r.Context(), igid)
	if err != nil {
		fail(r.Context(), w, "profile", igid, err)
		return
	}
	writeJSON(w, http.StatusOK, profileOf(read.Account, read.Social))
}

// handleStory answers GET /accounts/{igid}/story.
func (s *Server) handleStory(w http.ResponseWriter, r *http.Request) {
	igid := r.PathValue("igid")
	if igid == "" {
		badRequest(w, "igid is required")
		return
	}
	story, err := s.app.ReadStory(r.Context(), igid)
	if err != nil {
		fail(r.Context(), w, "story", igid, err)
		return
	}
	writeJSON(w, http.StatusOK, storyOf(story))
}
