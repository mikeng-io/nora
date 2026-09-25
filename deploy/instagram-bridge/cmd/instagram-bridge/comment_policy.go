package main

import (
	"fmt"
	"strconv"
	"time"

	"instagram-bridge/internal/app"
)

// The comment write's three switches (design/instagram-adoption/19 §Account
// safety, rule 4; compose.fragment.yml):
//
//	INSTAGRAM_COMMENT_WRITES        exactly `on` enables the write. Anything
//	                                else, unset included, is off.
//	INSTAGRAM_COMMENT_DAILY_CAP     replies per rolling 24 hours, default 10.
//	INSTAGRAM_COMMENT_MIN_INTERVAL  a Go duration between two replies,
//	                                default 90s.
//
// A value that does not parse does NOT fall back to its default: it turns the
// write OFF and is reported, because a typo in a safety limit must never
// quietly become a different limit.
const (
	envCommentWrites      = "INSTAGRAM_COMMENT_WRITES"
	envCommentDailyCap    = "INSTAGRAM_COMMENT_DAILY_CAP"
	envCommentMinInterval = "INSTAGRAM_COMMENT_MIN_INTERVAL"
)

// commentPolicyFromEnv reads the policy through getenv. problems names each
// variable that did not parse; any problem leaves writes disabled.
func commentPolicyFromEnv(getenv func(string) string) (app.CommentPolicy, []string) {
	policy := app.DefaultCommentPolicy()
	var problems []string
	if v := getenv(envCommentDailyCap); v != "" {
		n, err := strconv.Atoi(v)
		if err != nil || n < 0 {
			problems = append(problems, fmt.Sprintf("%s must be a non-negative integer", envCommentDailyCap))
		} else {
			policy.DailyCap = n
		}
	}
	if v := getenv(envCommentMinInterval); v != "" {
		d, err := time.ParseDuration(v)
		if err != nil || d < 0 {
			problems = append(problems, fmt.Sprintf("%s must be a non-negative Go duration", envCommentMinInterval))
		} else {
			policy.MinInterval = d
		}
	}
	policy.WritesEnabled = getenv(envCommentWrites) == "on" && len(problems) == 0
	return policy, problems
}
