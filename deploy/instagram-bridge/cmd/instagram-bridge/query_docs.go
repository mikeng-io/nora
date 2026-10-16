package main

import (
	"fmt"
	"regexp"
	"strings"

	gateway "instagram-bridge/internal/gateway/instameow"
)

// The two GraphQL doc_ids the story and timeline reads send
// (design/cognition-loop/22-social-stream.md §1a; compose.fragment.yml):
//
//	INSTAGRAM_STORY_DOC_ID     PolarisStoriesV3ReelPageStandaloneQuery
//	INSTAGRAM_TIMELINE_DOC_ID  PolarisProfilePostsTabContentQuery_connection
//
// Configuration, never constants: a doc_id rotates when Instagram ships a new
// web build, and the value is captured from the logged-in web client. Unset,
// that read answers 501 `unsupported`, naming the variable. A value that is
// not a decimal id is reported and treated as unset, for comment_policy.go's
// reason: a typo must not quietly become a different request.
var docIDPattern = regexp.MustCompile(`^[0-9]+$`)

// queryDocsFromEnv reads both ids through getenv. problems names each
// variable that was set and did not parse.
func queryDocsFromEnv(getenv func(string) string) (gateway.QueryDocs, []string) {
	var problems []string
	read := func(name string) string {
		v := strings.TrimSpace(getenv(name))
		if v != "" && !docIDPattern.MatchString(v) {
			problems = append(problems, fmt.Sprintf("%s must be a decimal doc_id", name))
			return ""
		}
		return v
	}
	docs := gateway.QueryDocs{
		Story:    read(gateway.EnvStoryDocID),
		Timeline: read(gateway.EnvTimelineDocID),
	}
	// Both reads then answer 501 (QueryDocs.Problem); said here too, at
	// start, where an operator reads it.
	if err := docs.Problem(); err != nil {
		problems = append(problems, err.Error())
	}
	return docs, problems
}
