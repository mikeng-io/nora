package instameow

import "instagram-bridge/internal/gateway/instameow/fakeig"

// The measured-wire fake, under the names this package's tests have always
// used for it.
type fakeIG = fakeig.IG

const (
	threadShort = fakeig.ThreadShort
	threadLong  = fakeig.ThreadLong
	selfFBID    = fakeig.SelfFBID
)

var (
	measuredUsers         = fakeig.MeasuredUsers
	measuredProfile       = fakeig.MeasuredProfile
	measuredSearchResults = fakeig.MeasuredSearchResults
)
