package domain

import "testing"

// The boundary rule is shared with Nora (nora/domain/summons.py
// _handle_pattern); these rows are the same on both sides.
func TestMentionsHandleBoundaryMatchesNora(t *testing.T) {
	for text, want := range map[string]bool{
		"is this true @noraexample":   true,
		"is this true @noraexample.":  true,
		"@noraexample. yes":           true,
		"@NoraExample what":           true,
		"@noraexample2 hi":            false,
		"@noraexample.x hi":           false,
		"@noraexample_x hi":           false,
		"me@noraexample":              false,
		"x.@noraexample":              false,
		"(@noraexample)":              true,
		"@noraexample, is this true?": true,
		// ASCII-only case folding: a non-ASCII letter never folds into
		// (or out of) the handle, and is not a username byte.
		"@noraruntıme":  false,
		"ı@noraexample": true,
		"@noraexampleı": true,
		"@noraruntİme":  false,
		"@NORAEXAMPLE":  true,
	} {
		if got := MentionsHandle(text, "noraexample"); got != want {
			t.Errorf("MentionsHandle(%q) = %v, want %v", text, got, want)
		}
	}
}
