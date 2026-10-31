package instameow

// These tests are the executable half of
// design/instagram-adoption/03-transport-contract.md's inbound section.
//
// The fixtures are the SHAPES of deltas captured live on 2026-09-21, replayed
// through instameow's own decoder rather than through a hand-written parser —
// so a library upgrade that moves a field breaks a test here instead of
// quietly emitting `media: null` in production. Signed CDN tokens and
// profile-picture urls from the capture are replaced with synthetic values of
// the same shape; the ids that the design documents already record are kept,
// because the point of several of these tests is that a specific number is
// treated a specific way.

import (
	"encoding/json"
	"strings"
	"testing"

	"go.mau.fi/mautrix-meta/pkg/instameow/slidetypes"
)

// noraFBID is client.GetOwnFBID() for the bot account, per
// 02-identity-and-policy.md. mikengFBID is another member's
// interop_messaging_user_fbid. The two are here together because the single
// most consequential test in this file is that they are told apart.
const (
	noraFBID   = 18247361241432477
	mikengFBID = 186217604552756
	otherFBID  = 18457536310970624
	roomShort  = "1183780976338188"
)

// cdnURL is shaped like a real Instagram CDN url: query parameters joined by
// bare `&`, and a percent-encoded `=` inside one of them. A regex scrape that
// stops at the first `&`, or that mangles the escape, produces a url Instagram
// answers with `403 Bad URL hash` — a failure that reads like an expired
// session. The tests below assert byte-identical round-tripping.
const cdnURL = "https://scontent.example.net/v/t1.15752-9/598830808_155260.png?stp=cp0_dst-png&_nc_cat=108&ccb=1-7&efg=eyJ2ZW5jb2RlIjoiMSJ9%3D&oh=03_Q7cD6QG4&oe=6AD8AB71"

func decodeDelta(t *testing.T, raw string) *slidetypes.Delta {
	t.Helper()
	var d slidetypes.Delta
	if err := json.Unmarshal([]byte(raw), &d); err != nil {
		t.Fatalf("delta did not decode: %v", err)
	}
	return &d
}

func messageOf(t *testing.T, raw string) *slidetypes.Message {
	t.Helper()
	d := decodeDelta(t, raw)
	ev, ok := d.Data.(*slidetypes.NewMessageEvent)
	if !ok {
		t.Fatalf("delta decoded as %T, wanted *NewMessageEvent", d.Data)
	}
	if ev.Message == nil {
		t.Fatal("NewMessageEvent carried no message")
	}
	return ev.Message
}

// --- the identity test this whole adaptor exists to get right -----------------

// TestMentionDiscriminatesNoraFromAnotherMember replays the two messages that
// R-12's discrimination was exercised on: same room, same sender, seconds
// apart, one tagging Nora and one tagging somebody else.
//
// Picking the wrong id space here produces NO error anywhere — Nora simply
// never notices she was tagged, in every room, forever. That is the Discord
// plaintext-tag bug asked in advance rather than found in production, and it
// is the reason `mentions[].fbid` is emitted as `interop_messaging_user_fbid`
// and nothing else.
func TestMentionDiscriminatesNoraFromAnotherMember(t *testing.T) {
	tagsSomeoneElse := messageOf(t, `{
	  "__typename":"SlideUQPPNewMessage","uq_seq_id":"63","thread_fbid":"1183780976338188",
	  "message":{"thread_fbid":"1183780976338188","message_id":"mid.$a",
	    "sender_fbid":"18457536310970624","text_body":"@member_b GH","timestamp_ms":"1789995788303",
	    "content":{"__typename":"SlideMessageText","text_body":"@member_b GH"},
	    "content_type":"TEXT",
	    "mentions":[{"user_fbid":"186217604552756","length":11,"profile_range_type":"PROFILE","offset":0}],
	    "sender":{"name":"Member B","id":"18457536310970624","igid":"79425202704",
	      "user_dict":{"username":"member_b","interop_messaging_user_fbid":"18457536310970624","id":"79425202704"}}}}`)

	tagsNora := messageOf(t, `{
	  "__typename":"SlideUQPPNewMessage","uq_seq_id":"64","thread_fbid":"1183780976338188",
	  "message":{"thread_fbid":"1183780976338188","message_id":"mid.$b",
	    "sender_fbid":"18457536310970624","text_body":"@not_____nora love I","timestamp_ms":"1789995792632",
	    "content":{"__typename":"SlideMessageText","text_body":"@not_____nora love I"},
	    "content_type":"TEXT",
	    "mentions":[{"user_fbid":"18247361241432477","length":13,"profile_range_type":"PROFILE","offset":0}],
	    "sender":{"name":"Member B","id":"18457536310970624","igid":"79425202704",
	      "user_dict":{"username":"member_b","interop_messaging_user_fbid":"18457536310970624","id":"79425202704"}}}}`)

	a := normalizeMessage(roomShort, tagsSomeoneElse, noraFBID)
	b := normalizeMessage(roomShort, tagsNora, noraFBID)

	if len(a.Mentions) != 1 || a.Mentions[0].FBID != "186217604552756" {
		t.Fatalf("mention of another member: got %+v", a.Mentions)
	}
	if len(b.Mentions) != 1 || b.Mentions[0].FBID != "18247361241432477" {
		t.Fatalf("mention of Nora: got %+v", b.Mentions)
	}
	// The negative half. A capability recorded only from what worked is
	// decoration: this asserts Nora is NOT matched by the other message.
	if a.Mentions[0].FBID == b.Mentions[0].FBID {
		t.Fatal("the two mentions compared equal — the id space collapsed")
	}
	if b.Mentions[0].Offset != 0 || b.Mentions[0].Length != 13 {
		t.Fatalf("mention range lost: %+v", b.Mentions[0])
	}
}

// TestSenderCarriesBothIDSpaces pins the rule that an Instagram person needs
// TWO identifiers. Messaging binds on the interop fbid, social reads bind on
// the igid, and neither is derivable from the other — so emitting one would
// silently disable whichever subsystem needed the other.
func TestSenderCarriesBothIDSpaces(t *testing.T) {
	msg := messageOf(t, `{
	  "__typename":"SlideUQPPNewMessage","thread_fbid":"1183780976338188",
	  "message":{"thread_fbid":"1183780976338188","message_id":"mid.$c","sender_fbid":"186217604552756",
	    "text_body":"hello","timestamp_ms":"1789985625166","content_type":"TEXT",
	    "content":{"__typename":"SlideMessageText","text_body":"hello"},
	    "sender":{"name":"Member A","id":"186217604552756","igid":"246967358",
	      "user_dict":{"username":"member_a","interop_messaging_user_fbid":"186217604552756","id":"246967358"}}}}`)

	ev := normalizeMessage(roomShort, msg, noraFBID)
	if ev.Sender.FBID != "186217604552756" {
		t.Errorf("sender.fbid = %q, want the interop messaging fbid", ev.Sender.FBID)
	}
	if ev.Sender.IGID != "246967358" {
		t.Errorf("sender.igid = %q, want the public instagram id", ev.Sender.IGID)
	}
	if ev.Sender.Username != "member_a" {
		t.Errorf("sender.username = %q", ev.Sender.Username)
	}
	if ev.FromMe {
		t.Error("from_me set for a message from somebody else")
	}
	if ev.RoomID != roomShort {
		t.Errorf("room_id = %q, want the SHORT thread_fbid", ev.RoomID)
	}
	if ev.TimestampMS != 1789985625166 {
		t.Errorf("timestamp_ms = %d", ev.TimestampMS)
	}
}

func TestFromMeIsSetForOurOwnEcho(t *testing.T) {
	msg := messageOf(t, `{
	  "__typename":"SlideUQPPNewMessage","thread_fbid":"1183780976338188",
	  "message":{"thread_fbid":"1183780976338188","message_id":"mid.$d","sender_fbid":"18247361241432477",
	    "text_body":"mine","timestamp_ms":"1789996158355","content_type":"TEXT",
	    "content":{"__typename":"SlideMessageText","text_body":"mine"}}}`)
	if ev := normalizeMessage(roomShort, msg, noraFBID); !ev.FromMe {
		t.Fatal("our own echo was not flagged from_me")
	}
	// And with no own-fbid known, from_me must be FALSE rather than
	// accidentally true for sender_fbid 0 — fail closed, so an unknown
	// identity never causes Nora to ignore a real message.
	if ev := normalizeMessage(roomShort, msg, 0); ev.FromMe {
		t.Fatal("from_me claimed while own fbid is unknown")
	}
}

// --- the four media shapes ----------------------------------------------------

func TestImageMediaComesFromAttachments(t *testing.T) {
	msg := messageOf(t, `{
	  "__typename":"SlideUQPPNewMessage","thread_fbid":"1183780976338188",
	  "message":{"thread_fbid":"1183780976338188","message_id":"mid.$e","sender_fbid":"186217604552756",
	    "text_body":null,"timestamp_ms":"1789996158355","content_type":"IMAGES",
	    "igd_snippet":"Sent a photo.",
	    "content":{"__typename":"SlideMessageImageContent","attachments":[
	      {"__typename":"XFBSlideAttachment","attachment_fbid":"977490213779166","attachment_type":2,
	       "preview_width":720,"preview_height":960,
	       "preview_cdn_url":"`+cdnURL+`","attachment_cdn_url":"`+cdnURL+`"}]}}}`)

	ev := normalizeMessage(roomShort, msg, noraFBID)
	if ev.Media == nil {
		t.Fatal("IMAGES produced no media")
	}
	if ev.Media.Kind != mediaImage {
		t.Errorf("kind = %q", ev.Media.Kind)
	}
	if ev.Media.URL == nil || *ev.Media.URL != cdnURL {
		t.Fatalf("url was not round-tripped byte-for-byte:\n got %v\nwant %s", ev.Media.URL, cdnURL)
	}
	// The specific thing a scrape gets wrong.
	if !strings.Contains(*ev.Media.URL, "&_nc_cat=108") || !strings.Contains(*ev.Media.URL, "%3D") {
		t.Error("url lost a query separator or an escape — this is the 403 Bad URL hash failure")
	}
	if ev.Media.AttachmentFBID != "977490213779166" {
		t.Errorf("attachment_fbid = %q", ev.Media.AttachmentFBID)
	}
	if ev.Media.Width != 720 || ev.Media.Height != 960 {
		t.Errorf("dimensions = %dx%d", ev.Media.Width, ev.Media.Height)
	}
	// A photo carries no text body. Emitting the snippet ("Sent a photo.") as
	// `text` would put Instagram's UI prose into Nora's transcript as if a
	// human had typed it.
	if ev.Text != "" {
		t.Errorf("text = %q, want empty for a photo", ev.Text)
	}
}

// TestVideoMediaComesFromVideosNotAttachments is the single test that proves
// the four-shapes rule is implemented rather than assumed: this payload has an
// EMPTY `attachments` and its media under `videos`. A generic attachment path
// passes every image test and fails here.
func TestVideoMediaComesFromVideosNotAttachments(t *testing.T) {
	msg := messageOf(t, `{
	  "__typename":"SlideUQPPNewMessage","thread_fbid":"1183780976338188",
	  "message":{"thread_fbid":"1183780976338188","message_id":"mid.$f","sender_fbid":"186217604552756",
	    "content_type":"VIDEOS","timestamp_ms":"1789996158355",
	    "content":{"__typename":"SlideMessageVideosContent","attachments":[],"videos":[
	      {"__typename":"XFBSlideAttachment","attachment_fbid":"555","preview_width":480,"preview_height":854,
	       "attachment_cdn_url":"`+cdnURL+`","dash_manifest":"<MPD/>"}]}}}`)

	ev := normalizeMessage(roomShort, msg, noraFBID)
	if ev.Media == nil {
		t.Fatal("VIDEOS produced no media — the attachments[] path was used")
	}
	if ev.Media.Kind != mediaVideo {
		t.Errorf("kind = %q, want video", ev.Media.Kind)
	}
	if ev.Media.URL == nil || *ev.Media.URL != cdnURL {
		t.Fatalf("video url = %v", ev.Media.URL)
	}
}

func TestAnimatedMediaComesFromAnimatedMedia(t *testing.T) {
	msg := messageOf(t, `{
	  "__typename":"SlideUQPPNewMessage","thread_fbid":"1183780976338188",
	  "message":{"thread_fbid":"1183780976338188","message_id":"mid.$g","sender_fbid":"186217604552756",
	    "content_type":"INSTAGRAM_MESSAGING_ANIMATED_IMAGE","timestamp_ms":"1789996158355",
	    "content":{"__typename":"SlideMessageAnimatedMediaContent","animated_media":[
	      {"attachment_mp4_url":"`+cdnURL+`","attachment_webp_url":"https://example.net/x.webp",
	       "preview_width":320,"preview_height":240,"is_sticker":true}]}}}`)

	ev := normalizeMessage(roomShort, msg, noraFBID)
	if ev.Media == nil || ev.Media.Kind != mediaAnimated {
		t.Fatalf("animated media = %+v", ev.Media)
	}
	if ev.Media.URL == nil || *ev.Media.URL != cdnURL {
		t.Fatalf("animated url = %v", ev.Media.URL)
	}
	// An animated sticker lives on an external CDN, not in the attachment
	// store, so there is no attachment_fbid to report and inventing one would
	// be a fetch handle that resolves to nothing.
	if ev.Media.AttachmentFBID != "" {
		t.Errorf("attachment_fbid = %q, want empty for animated media", ev.Media.AttachmentFBID)
	}
}

// TestOneTimeMediaAnnouncesItselfAndCarriesNothing pins the contract's v1
// posture. view_mode 0 and 1 arrive with `attachment: null`; fetching those
// bytes IS the act of viewing on Instagram and is visible to the sender, so
// the bridge does not. `url: null` says "these exist and we chose not to look",
// which is a different claim from "we looked and found nothing".
func TestOneTimeMediaAnnouncesItselfAndCarriesNothing(t *testing.T) {
	for _, viewMode := range []int{0, 1} {
		msg := messageOf(t, `{
		  "__typename":"SlideUQPPNewRavenMessage","thread_fbid":"1183780976338188",
		  "message":{"thread_fbid":"1183780976338188","message_id":"mid.$h","sender_fbid":"186217604552756",
		    "content_type":"RAVEN_IMAGE","timestamp_ms":"1789996158355",
		    "content":{"__typename":"SlideMessageRavenImageContent","view_mode":`+string(rune('0'+viewMode))+`,"attachment":null}}}`)

		ev := normalizeMessage(roomShort, msg, noraFBID)
		if ev.Media == nil {
			t.Fatalf("view_mode %d: RAVEN_IMAGE produced no media at all — Nora would not "+
				"even learn that something was sent", viewMode)
		}
		if ev.Media.Kind != mediaOneTime {
			t.Errorf("view_mode %d: kind = %q, want one_time", viewMode, ev.Media.Kind)
		}
		if ev.Media.URL != nil {
			t.Errorf("view_mode %d: url = %q, want null — the bridge must not fetch", viewMode, *ev.Media.URL)
		}
		if !ev.Media.OneTime || ev.Media.ViewMode == nil || *ev.Media.ViewMode != viewMode {
			t.Errorf("view_mode %d: flags lost: %+v", viewMode, ev.Media)
		}
	}
}

// view_mode 2 ("keep in chat") is different in kind: the attachment is already
// in the delta, so there is nothing to fetch and nothing to consent to. The
// bytes are surfaced — with one_time still set, because the sender DID choose
// an ephemeral mode and erasing that would misreport what happened.
func TestKeepInChatRavenSurfacesItsBytes(t *testing.T) {
	msg := messageOf(t, `{
	  "__typename":"SlideUQPPNewRavenMessage","thread_fbid":"1183780976338188",
	  "message":{"thread_fbid":"1183780976338188","message_id":"mid.$i","sender_fbid":"186217604552756",
	    "content_type":"RAVEN_IMAGE","timestamp_ms":"1789996158355",
	    "content":{"__typename":"SlideMessageRavenImageContent","view_mode":2,
	      "attachment":{"attachment_fbid":"777","preview_width":100,"preview_height":200,
	        "attachment_cdn_url":"`+cdnURL+`"}}}}`)

	ev := normalizeMessage(roomShort, msg, noraFBID)
	if ev.Media == nil || ev.Media.URL == nil || *ev.Media.URL != cdnURL {
		t.Fatalf("keep-in-chat media = %+v", ev.Media)
	}
	if ev.Media.Kind != mediaImage {
		t.Errorf("kind = %q, want image", ev.Media.Kind)
	}
	if !ev.Media.OneTime || ev.Media.ViewMode == nil || *ev.Media.ViewMode != 2 {
		t.Errorf("ephemeral provenance erased: %+v", ev.Media)
	}
}

// Audio is the fifth shape. 04-capability-evidence.md lists it as
// structurally supported and UNMEASURED; it is emitted rather than dropped
// because in Nora a voice note engages unconditionally, so swallowing one is a
// lost engagement rather than a cosmetic gap.
func TestAudioMediaCarriesDuration(t *testing.T) {
	msg := messageOf(t, `{
	  "__typename":"SlideUQPPNewMessage","thread_fbid":"1183780976338188",
	  "message":{"thread_fbid":"1183780976338188","message_id":"mid.$j","sender_fbid":"186217604552756",
	    "content_type":"AUDIO","timestamp_ms":"1789996158355",
	    "content":{"__typename":"SlideMessageAudiosContent","audio_attachments":[
	      {"attachment_fbid":"888","playable_duration_ms":4200,"attachment_cdn_url":"`+cdnURL+`"}]}}}`)

	ev := normalizeMessage(roomShort, msg, noraFBID)
	if ev.Media == nil || ev.Media.Kind != mediaAudio {
		t.Fatalf("audio media = %+v", ev.Media)
	}
	if ev.Media.DurationMS != 4200 {
		t.Errorf("duration_ms = %d", ev.Media.DurationMS)
	}
}

func TestTextMessageHasNoMedia(t *testing.T) {
	msg := messageOf(t, `{
	  "__typename":"SlideUQPPNewMessage","thread_fbid":"1183780976338188",
	  "message":{"thread_fbid":"1183780976338188","message_id":"mid.$k","sender_fbid":"186217604552756",
	    "text_body":"plain","content_type":"TEXT","timestamp_ms":"1789996158355",
	    "content":{"__typename":"SlideMessageText","text_body":"plain"}}}`)
	ev := normalizeMessage(roomShort, msg, noraFBID)
	if ev.Media != nil {
		t.Fatalf("text message grew media: %+v", ev.Media)
	}
	if ev.Mentions == nil {
		t.Fatal("mentions serialized as null rather than []")
	}
}

// --- reply structure ----------------------------------------------------------

func TestReplyCarriesTheParentPointer(t *testing.T) {
	msg := messageOf(t, `{
	  "__typename":"SlideUQPPNewMessage","thread_fbid":"1183780976338188",
	  "message":{"thread_fbid":"1183780976338188","message_id":"mid.$l","sender_fbid":"186217604552756",
	    "text_body":"answering","content_type":"TEXT","timestamp_ms":"1789996158355",
	    "content":{"__typename":"SlideMessageText","text_body":"answering"},
	    "replied_to_message_id":"mid.$parent",
	    "replied_to_message":{"message_id":"mid.$parent","sender_fbid":"18247361241432477",
	      "text_body":"the question","timestamp_ms":"1789996100000"}}}`)

	ev := normalizeMessage(roomShort, msg, noraFBID)
	if ev.ReplyTo == nil {
		t.Fatal("reply_to was null on a reply")
	}
	if ev.ReplyTo.MessageID != "mid.$parent" {
		t.Errorf("reply_to.message_id = %q", ev.ReplyTo.MessageID)
	}
	if ev.ReplyTo.SenderFBID != "18247361241432477" {
		t.Errorf("reply_to.sender_fbid = %q — this is what ReplyToNora binds on", ev.ReplyTo.SenderFBID)
	}
	if ev.ReplyTo.TimestampMS != 1789996100000 {
		t.Errorf("reply_to.timestamp_ms = %d", ev.ReplyTo.TimestampMS)
	}
}

// A reply whose parent body did not come down still reports the relation. The
// pointer survives, the content is not invented — the same honest degradation
// whatsapp-bridge takes on a quote older than its cache.
func TestReplyWithoutParentBodyKeepsTheRelation(t *testing.T) {
	msg := messageOf(t, `{
	  "__typename":"SlideUQPPNewMessage","thread_fbid":"1183780976338188",
	  "message":{"thread_fbid":"1183780976338188","message_id":"mid.$m","sender_fbid":"186217604552756",
	    "text_body":"answering","content_type":"TEXT","timestamp_ms":"1789996158355",
	    "content":{"__typename":"SlideMessageText","text_body":"answering"},
	    "replied_to_message_id":"mid.$gone","replied_to_message":null}}`)

	ev := normalizeMessage(roomShort, msg, noraFBID)
	if ev.ReplyTo == nil || ev.ReplyTo.MessageID != "mid.$gone" {
		t.Fatalf("reply_to = %+v", ev.ReplyTo)
	}
	if ev.ReplyTo.Text != "" || ev.ReplyTo.SenderFBID != "" {
		t.Error("parent content was invented")
	}
}

// --- the zero-timestamp trap --------------------------------------------------

// A zero jsontime.UnixMilliString is the zero time.Time, and UnixMilli() on
// that is a large NEGATIVE number rather than 0. Emitting it would date the
// message to the year 1754 and quietly poison anything that orders or ages
// messages — including the last_event_age detector.
func TestMissingTimestampIsZeroNotNegative(t *testing.T) {
	msg := messageOf(t, `{
	  "__typename":"SlideUQPPNewMessage","thread_fbid":"1183780976338188",
	  "message":{"thread_fbid":"1183780976338188","message_id":"mid.$n","sender_fbid":"186217604552756",
	    "text_body":"no clock","content_type":"TEXT",
	    "content":{"__typename":"SlideMessageText","text_body":"no clock"}}}`)
	ev := normalizeMessage(roomShort, msg, noraFBID)
	if ev.TimestampMS != 0 {
		t.Fatalf("timestamp_ms = %d, want 0 for an absent timestamp", ev.TimestampMS)
	}
}

// --- serialization ------------------------------------------------------------

// TestInboundJSONMatchesTheContract serializes the event and checks the
// document's exact field names. The adapter is written against those names;
// a rename here is invisible to the Go compiler and total to the consumer.
func TestInboundJSONMatchesTheContract(t *testing.T) {
	msg := messageOf(t, `{
	  "__typename":"SlideUQPPNewMessage","thread_fbid":"1183780976338188",
	  "message":{"thread_fbid":"1183780976338188","message_id":"mid.$o","sender_fbid":"186217604552756",
	    "text_body":"@not_____nora","content_type":"TEXT","timestamp_ms":"1789985625166",
	    "content":{"__typename":"SlideMessageText","text_body":"@not_____nora"},
	    "mentions":[{"user_fbid":"18247361241432477","length":13,"offset":0,"profile_range_type":"PROFILE"}],
	    "sender":{"name":"Member A","id":"186217604552756","igid":"246967358",
	      "user_dict":{"username":"member_a","interop_messaging_user_fbid":"186217604552756","id":"246967358"}}}}`)

	blob, err := json.Marshal(normalizeMessage(roomShort, msg, noraFBID))
	if err != nil {
		t.Fatalf("event did not serialize: %v", err)
	}
	var got map[string]any
	if err := json.Unmarshal(blob, &got); err != nil {
		t.Fatal(err)
	}
	for _, key := range []string{
		"room_id", "message_id", "sender", "timestamp_ms",
		"text", "mentions", "reply_to", "media", "kind",
	} {
		if _, ok := got[key]; !ok {
			t.Errorf("contract field %q missing from the serialized event", key)
		}
	}
	if got["kind"] != kindMessage {
		t.Errorf("kind = %v", got["kind"])
	}
	// reply_to and media must be present-and-null, not absent: the contract
	// shows them as explicit nulls, and a consumer doing `event["media"]`
	// should get None rather than a KeyError.
	if got["reply_to"] != nil || got["media"] != nil {
		t.Errorf("reply_to/media were not explicit nulls: %v / %v", got["reply_to"], got["media"])
	}
	sender, _ := got["sender"].(map[string]any)
	for _, key := range []string{"fbid", "igid", "username"} {
		if _, ok := sender[key]; !ok {
			t.Errorf("sender.%s missing", key)
		}
	}
}

func TestNilMessageNormalizesToNothing(t *testing.T) {
	if ev := normalizeMessage(roomShort, nil, noraFBID); ev != nil {
		t.Fatalf("a nil message produced an event: %+v", ev)
	}
}

// --- @everyone ----------------------------------------------------------------

// TestEveryoneMentionIsLabelledNotDisguisedAsAPerson.
//
// For `profile_range_type: "THREAD"` — Instagram's @everyone — `user_fbid`
// carries the CHAT id, not a person's; the library says so on the field
// itself. Emitting that number unlabelled means it can never equal Nora's own
// fbid, so a message that names the whole room reads as a mention of somebody
// who is not her and she silently never answers it. There is no error and no
// counter: the only symptom is a bot that ignores @everyone forever.
//
// Both halves are asserted. The PROFILE mention beside it must still be a
// person, or "label the thread one" would have been bought by breaking the
// case that already worked.
func TestEveryoneMentionIsLabelledNotDisguisedAsAPerson(t *testing.T) {
	msg := messageOf(t, `{
	  "__typename":"SlideUQPPNewMessage","thread_fbid":"1183780976338188",
	  "message":{"thread_fbid":"1183780976338188","message_id":"mid.$e","sender_fbid":"186217604552756",
	    "text_body":"@everyone and @not_____nora","content_type":"TEXT","timestamp_ms":"1789985625166",
	    "content":{"__typename":"SlideMessageText","text_body":"@everyone and @not_____nora"},
	    "mentions":[
	      {"user_fbid":"1183780976338188","length":9,"offset":0,"profile_range_type":"THREAD"},
	      {"user_fbid":"18247361241432477","length":13,"offset":14,"profile_range_type":"PROFILE"}],
	    "sender":{"name":"Member A","id":"186217604552756","igid":"246967358",
	      "user_dict":{"username":"member_a","interop_messaging_user_fbid":"186217604552756","id":"246967358"}}}}`)

	ev := normalizeMessage(roomShort, msg, noraFBID)
	if len(ev.Mentions) != 2 {
		t.Fatalf("%d mentions survived, want 2", len(ev.Mentions))
	}
	everyone, person := ev.Mentions[0], ev.Mentions[1]
	if everyone.RangeType != "THREAD" {
		t.Fatalf("@everyone is emitted as %q — indistinguishable from a person", everyone.RangeType)
	}
	// The number is the ROOM, and that is why it must be labelled: compared to
	// an fbid it can only ever be a miss.
	if everyone.FBID != roomShort {
		t.Errorf("@everyone fbid = %q, want the chat id passed through", everyone.FBID)
	}
	if everyone.FBID == person.FBID {
		t.Fatal("the thread id and the person's fbid compared equal")
	}
	if person.RangeType != "PROFILE" || person.FBID != "18247361241432477" {
		t.Fatalf("the ordinary mention was damaged: %+v", person)
	}
	// And it survives serialization under the name the adapter reads.
	blob, err := json.Marshal(ev)
	if err != nil {
		t.Fatal(err)
	}
	var got struct {
		Mentions []map[string]any `json:"mentions"`
	}
	if err := json.Unmarshal(blob, &got); err != nil {
		t.Fatal(err)
	}
	if got.Mentions[0]["range_type"] != "THREAD" {
		t.Fatalf("range_type did not reach the wire: %v", got.Mentions[0])
	}
}

// --- from_me on the non-message events ----------------------------------------

// The contract says EVERY event carries from_me. A reaction names its sender,
// so the answer is a real boolean; an edit and an unsend do not name anyone at
// all, so the answer is an explicit null. `false` there would be a claim — that
// a member did it — and the account is also used from a browser and a phone,
// so it is a claim that can be wrong.
func TestFromMeIsAnsweredOrExplicitlyUnknownOnEveryEvent(t *testing.T) {
	mine := normalizeReaction(roomShort, slidetypes.Reaction{
		Reaction: "👍", SenderFBID: noraFBID}, "mid.$m", false, noraFBID, 0)
	if mine.FromMe == nil || !*mine.FromMe {
		t.Fatalf("our own reaction reported from_me = %v", mine.FromMe)
	}
	theirs := normalizeReaction(roomShort, slidetypes.Reaction{
		Reaction: "👍", SenderFBID: mikengFBID}, "mid.$m", false, noraFBID, 0)
	if theirs.FromMe == nil || *theirs.FromMe {
		t.Fatalf("another member's reaction reported from_me = %v", theirs.FromMe)
	}
	// Own fbid unknown: "I cannot tell", not "no". A false here is how a bot
	// answers its own echo.
	unknown := normalizeReaction(roomShort, slidetypes.Reaction{
		Reaction: "👍", SenderFBID: mikengFBID}, "mid.$m", false, 0, 0)
	if unknown.FromMe != nil {
		t.Fatalf("from_me = %v with no own fbid, want null", *unknown.FromMe)
	}

	// Every event kind serializes the key, whatever the value.
	for name, ev := range map[string]any{
		"reaction": mine,
		"edit":     &editEvent{Kind: kindEdit, RoomID: roomShort, MessageID: "mid.$m"},
		"unsend":   &unsendEvent{Kind: kindUnsend, RoomID: roomShort, MessageID: "mid.$m"},
		"typing":   &typingEvent{Kind: kindTyping, ThreadID: roomShort},
	} {
		blob, err := json.Marshal(ev)
		if err != nil {
			t.Fatal(err)
		}
		var got map[string]any
		if err := json.Unmarshal(blob, &got); err != nil {
			t.Fatal(err)
		}
		if _, ok := got["from_me"]; !ok {
			t.Errorf("%s: from_me is absent, not null — the contract says every event carries it", name)
		}
	}
	// The unknown cases are null, not false: a consumer must be able to tell
	// "nobody knows" from "somebody else".
	blob, err := json.Marshal(&editEvent{Kind: kindEdit, MessageID: "mid.$m"})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(blob), `"from_me":null`) {
		t.Fatalf("edit serialized from_me as something other than null: %s", blob)
	}
}

// --- shared posts and reels (XMA) ------------------------------------------
//
// The failure this fixes is the quiet kind: `MessageContentXMA` had no case in
// extractMedia's switch, so a shared reel produced no text and no media, and
// the Python parser dropped the turn for having neither. Nora did not fail to
// READ the reel — she never learned one had been posted. Nothing logged it and
// nothing counted it.
//
// XMA is unmeasured, so these tests assert the two things that hold whatever
// the live shape turns out to be: the message must SURVIVE, and the bridge
// must not invent a classification it cannot observe.

func xmaMessage(x *slidetypes.XMAContent, textBody string) *slidetypes.Message {
	return &slidetypes.Message{
		MessageID:   "mid.$xma",
		ThreadFBID:  threadShort,
		SenderFBID:  186217604552756,
		ContentType: "XMA",
		Content: slidetypes.MessageContentWrapper{
			Content: &slidetypes.MessageContentXMA{XMA: x, XMATextBody: textBody},
		},
	}
}

// Caption and author, passed through with no fetch and no model call, when the
// XMA carries them. This fixture is the fullest shape the library declares; a
// live share measured 2026-09-24 carried the author and TargetURL and NO
// caption, so the caption is not the free depth
// (design/instagram-adoption/13, the dated section).
func TestSharedReelCarriesCaptionAndAuthorForFree(t *testing.T) {
	ev := normalizeMessage(threadShort, xmaMessage(&slidetypes.XMAContent{
		HeaderTitleText: "someaccount",
		CaptionBodyText: "the caption as its author wrote it",
		SubtitleText:    "original audio",
		TargetURL:       "https://www.instagram.com/reel/ABC123/",
		Typename:        "XMAMessageExtensibleAttachment",
	}, "look at this"), 0)

	if ev == nil {
		t.Fatal("a shared reel normalized to nil — this is the invisible drop")
	}
	if ev.Shared == nil {
		t.Fatal("shared was nil for an XMA message")
	}
	if ev.Shared.Caption != "the caption as its author wrote it" {
		t.Errorf("caption = %q", ev.Shared.Caption)
	}
	if ev.Shared.Author != "someaccount" {
		t.Errorf("author = %q; this is who POSTED the reel, not who shared it",
			ev.Shared.Author)
	}
	if ev.Shared.TargetURL != "https://www.instagram.com/reel/ABC123/" {
		t.Errorf("target_url = %q", ev.Shared.TargetURL)
	}
	// The sharer and the author are different people and must stay different
	// fields. Attributing a stranger's reel to a member of the room is the one
	// wrong answer here that reads as a right one.
	if ev.Sender.FBID != "186217604552756" {
		t.Errorf("sender = %q, want the person who shared it", ev.Sender.FBID)
	}
}

// The bridge must classify NOTHING. `__typename` and `preview_layout_type`
// ride verbatim: their value sets have never been observed, and a guessed
// `kind: "reel"` would read as measured to every consumer downstream.
func TestSharedPostIsNotClassifiedByTheBridge(t *testing.T) {
	ev := normalizeMessage(threadShort, xmaMessage(&slidetypes.XMAContent{
		XMATitle:          "someaccount",
		Typename:          "SomeTypenameNobodyHasSeen",
		PreviewLayoutType: "SOME_LAYOUT",
	}, ""), 0)

	if ev == nil || ev.Shared == nil {
		t.Fatal("a shared post normalized to nothing")
	}
	if ev.Shared.Typename != "SomeTypenameNobodyHasSeen" {
		t.Errorf("typename = %q, want it passed through uninterpreted",
			ev.Shared.Typename)
	}
	if ev.Shared.Layout != "SOME_LAYOUT" {
		t.Errorf("layout = %q, want it passed through uninterpreted", ev.Shared.Layout)
	}
	// XMATitle is the only name present, so it must reach `author` — reading
	// only header_title_text would leave the author empty on this shape, and
	// silently.
	if ev.Shared.Author != "someaccount" {
		t.Errorf("author = %q; both title field families must be read, because "+
			"no probe has shown which one a live delta populates", ev.Shared.Author)
	}
}

// The thumbnail is the middle depth: an image, taken at ingest, enough to say
// what a reel looks like without fetching the video. It is an IMAGE even for a
// shared video, because a still is what these bytes are.
func TestSharedReelThumbnailIsAnImageNotAVideo(t *testing.T) {
	for _, tc := range []struct {
		name string
		xma  *slidetypes.XMAContent
	}{
		{"preview_image", &slidetypes.XMAContent{
			CaptionBodyText: "c",
			PreviewImage:    &slidetypes.XMAPreviewImage{URL: "https://cdn/thumb.jpg", Width: 640, Height: 640},
		}},
		{"xmaPreviewImage", &slidetypes.XMAContent{
			CaptionBodyText: "c",
			XMAPreviewImage: &slidetypes.XMAPreviewImage{URL: "https://cdn/thumb.jpg", Width: 640, Height: 640},
		}},
		{"fallback_url only", &slidetypes.XMAContent{
			CaptionBodyText: "c",
			PreviewImage:    &slidetypes.XMAPreviewImage{FallbackURL: "https://cdn/thumb.jpg"},
		}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			ev := normalizeMessage(threadShort, xmaMessage(tc.xma, ""), 0)
			if ev == nil || ev.Media == nil {
				t.Fatal("no media for a shared post carrying a preview")
			}
			if ev.Media.Kind != mediaImage {
				t.Errorf("kind = %q, want image — these bytes are a still, and "+
					"calling them a video would have her describe one frame as "+
					"if she had watched the clip", ev.Media.Kind)
			}
			if ev.Media.URL == nil || *ev.Media.URL != "https://cdn/thumb.jpg" {
				t.Errorf("url = %v", ev.Media.URL)
			}
		})
	}
}

// A shared post with no preview still survives on its caption alone. Returning
// an empty-url media would be a handle naming nothing; dropping the message
// would be the original bug.
func TestSharedPostSurvivesWithNoThumbnail(t *testing.T) {
	ev := normalizeMessage(threadShort, xmaMessage(&slidetypes.XMAContent{
		CaptionBodyText: "no preview on this one",
	}, ""), 0)

	if ev == nil {
		t.Fatal("dropped")
	}
	if ev.Media != nil {
		t.Errorf("media = %+v, want nil rather than a url-less handle", ev.Media)
	}
	if ev.Shared == nil || ev.Shared.Caption != "no preview on this one" {
		t.Errorf("shared = %+v", ev.Shared)
	}
}

// An XMA carrying nothing readable must answer nil, not an empty object. An
// all-empty `shared` would tell the adapter "a post was shared here" on no
// evidence, which is a claim; nil is the truth.
func TestEmptyXMAIsNotReportedAsASharedPost(t *testing.T) {
	ev := normalizeMessage(threadShort, xmaMessage(&slidetypes.XMAContent{}, ""), 0)
	if ev != nil && ev.Shared != nil {
		t.Errorf("shared = %+v, want nil for an XMA with nothing in it", ev.Shared)
	}
}

// The shared post's own text lives on the XMA wrapper, not in `text_body`.
// Without this the message arrives with a thumbnail and no words and reads as
// somebody having sent a picture.
func TestSharedPostTextComesOffTheXMAWrapper(t *testing.T) {
	ev := normalizeMessage(threadShort, xmaMessage(&slidetypes.XMAContent{
		CaptionBodyText: "c",
	}, "what the sharer typed"), 0)

	if ev == nil {
		t.Fatal("dropped")
	}
	if ev.Text != "what the sharer typed" {
		t.Errorf("text = %q, want the XMA text body", ev.Text)
	}
}

// A post mentioning the account in its caption arrives as an inline-share XMA
// with no target_url (measured 2026-09-25). target_id and the CTA action URLs
// are the fields that may name the post, so both ride through uninterpreted,
// and a card carrying ONLY them is still a shared post, not an empty XMA.
func TestSharedCardCarriesTargetIDAndActionsThrough(t *testing.T) {
	ev := normalizeMessage(threadShort, xmaMessage(&slidetypes.XMAContent{
		XMATitle:     "someaccount",
		SubtitleText: "@noraexample can u see",
		TargetID:     "3500000000000000001",
		Typename:     "SlideMessageLayeredXMA",
		CTAButtons:   []*slidetypes.CTAButton{{ActionURL: "https://www.instagram.com/p/ABC123/", CTAType: "xma_web_url"}, nil, {ActionURL: ""}},
	}, ""), 0)
	if ev == nil || ev.Shared == nil {
		t.Fatal("the card was dropped")
	}
	if ev.Shared.TargetID != "3500000000000000001" {
		t.Errorf("target_id = %q", ev.Shared.TargetID)
	}
	if len(ev.Shared.Actions) != 1 || ev.Shared.Actions[0] != "https://www.instagram.com/p/ABC123/" {
		t.Errorf("actions = %v, want the one non-empty action url", ev.Shared.Actions)
	}

	only := normalizeMessage(threadShort, xmaMessage(&slidetypes.XMAContent{TargetID: "3500000000000000002"}, ""), 0)
	if only == nil || only.Shared == nil || only.Shared.TargetID != "3500000000000000002" {
		t.Fatal("a card carrying only target_id was reported as an empty XMA")
	}
}
