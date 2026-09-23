package instameow

import (
	"context"

	"go.mau.fi/mautrix-meta/pkg/instameow"
	"go.mau.fi/mautrix-meta/pkg/instameow/slidetypes"
	"go.mau.fi/mautrix-meta/pkg/messagix/httpclient"
	"go.mau.fi/mautrix-meta/pkg/messagix/responses"
	"go.mau.fi/mautrix-meta/pkg/messagix/types"
)

// Client is the slice of *instameow.Client the request paths use. It exists
// so everything above the socket can be exercised without a live Instagram
// session: the account is real and in use, and "run it and see" is not an
// available test.
//
// It is deliberately ONE interface for reads and writes: the fake that stands
// in for a live session has to be one object, or a test could have the
// roster, the profile and a send disagree about which threads and accounts
// exist.
type Client interface {
	SendMessage(context.Context, *slidetypes.SendTextRequest) (*slidetypes.SendTextResponse, error)
	SendMedia(context.Context, *slidetypes.SendMediaRequest) (*slidetypes.SendMediaResponse, error)
	SendReaction(context.Context, *slidetypes.CreateReactionRequest) (*slidetypes.SendReactionResponse, error)
	UnsendMessage(context.Context, *slidetypes.UnsendMessageRequest) (*slidetypes.UnsendMessageResponse, error)
	SetTyping(ctx context.Context, threadID string, typing bool) error
	FetchThreadID(ctx context.Context, threadFBID int64) (*instameow.ThreadIGIDs, error)
	GetThread(context.Context, *slidetypes.GetThreadInfoRequest) (*slidetypes.ThreadInfoResponse, error)
	GetProfile(ctx context.Context, igid string) (*slidetypes.ProfilePageResponse, error)
	// FetchReel is the ONLY path to a story anywhere — OpenCLI's `story`
	// verb posts one rather than reading one, verified against the live
	// tool. See social.go's story section.
	FetchReel(ctx context.Context, reelIDs []string, mediaID string) (*responses.ReelInfoResponse, error)
}

// Uploader is SendMercuryUploadRequest, extracted as a function value for the
// same reason: it hangs off *httpclient.HTTPClient, which cannot be faked.
type Uploader func(context.Context, int64, *httpclient.MercuryUploadMedia) (*types.MercuryUploadResponse, error)

// rawSource is what hands the raw GraphQL path its HTTP client.
// `*instameow.Client` has it; a fake need not.
type rawSource interface {
	GetHTTP() *httpclient.HTTPClient
}
