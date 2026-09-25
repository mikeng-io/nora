package main

import (
	"testing"
	"time"
)

func envOf(m map[string]string) func(string) string { return func(k string) string { return m[k] } }

// Writes are off unless the switch is exactly `on`, the defaults are 10 and
// 90s, and a value that does not parse turns writes OFF rather than falling
// back to a default.
func TestCommentPolicyFromEnv(t *testing.T) {
	for name, tc := range map[string]struct {
		env      map[string]string
		enabled  bool
		cap      int
		interval time.Duration
		problems int
	}{
		"unset":                {map[string]string{}, false, 10, 90 * time.Second, 0},
		"on":                   {map[string]string{envCommentWrites: "on"}, true, 10, 90 * time.Second, 0},
		"ON is not on":         {map[string]string{envCommentWrites: "ON"}, false, 10, 90 * time.Second, 0},
		"true is not on":       {map[string]string{envCommentWrites: "true"}, false, 10, 90 * time.Second, 0},
		"set values":           {map[string]string{envCommentWrites: "on", envCommentDailyCap: "3", envCommentMinInterval: "5m"}, true, 3, 5 * time.Minute, 0},
		"bad cap":              {map[string]string{envCommentWrites: "on", envCommentDailyCap: "ten"}, false, 10, 90 * time.Second, 1},
		"negative cap":         {map[string]string{envCommentWrites: "on", envCommentDailyCap: "-1"}, false, 10, 90 * time.Second, 1},
		"bad interval":         {map[string]string{envCommentWrites: "on", envCommentMinInterval: "90"}, false, 10, 90 * time.Second, 1},
		"negative interval":    {map[string]string{envCommentWrites: "on", envCommentMinInterval: "-1s"}, false, 10, 90 * time.Second, 1},
		"interval below floor": {map[string]string{envCommentWrites: "on", envCommentMinInterval: "0s"}, false, 10, 90 * time.Second, 1},
		"zero cap is allowed":  {map[string]string{envCommentWrites: "on", envCommentDailyCap: "0"}, true, 0, 90 * time.Second, 0},
	} {
		t.Run(name, func(t *testing.T) {
			p, problems := commentPolicyFromEnv(envOf(tc.env))
			if p.WritesEnabled != tc.enabled || p.DailyCap != tc.cap || p.MinInterval != tc.interval || len(problems) != tc.problems {
				t.Errorf("policy %+v problems %v, want enabled=%v cap=%d interval=%v problems=%d",
					p, problems, tc.enabled, tc.cap, tc.interval, tc.problems)
			}
		})
	}
}
