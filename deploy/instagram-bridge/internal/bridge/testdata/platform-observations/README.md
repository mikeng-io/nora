# Native platform-observation fixtures — Instagram (mautrix-meta/instameow)

GENERATED payloads documenting what the pinned private transport actually
hands this bridge, per `design/platform-observations/` (00 §3). No production
rows: every fbid/igid/message id is synthetic placeholder-shaped data, and the
`SlideUQPPPollCreated` typename below is deliberately hypothetical — it exists
only to exercise the library's unmapped-typename fallback, not to claim any
observed IG poll shape.

## Pinned source

- **SDK**: `go.mau.fi/mautrix-meta v0.2609.0` (`deploy/instagram-bridge/go.mod`),
  private transport — NOT the Business messaging API; no equivalence assumed.
- **Callback union**: `pkg/instameow/slidetypes/event.go` —
  `Delta{TypeName, UQSeqID, ThreadIGID(json:"thread_fbid"), Data, Raw}`;
  `UnmarshalJSON` dispatches on `__typename`. Unmapped typenames decode to
  `UnknownEvent` (raw map kept on `Delta.Raw`); `SlideUQPPNotYetImplementedMutation`
  /`NoOpMutation`/`Create1To1Thread`/`UserReachabilityStatus` decode to
  `IgnoredEvent`.
- **Dispatch point**: `pkg/instameow/socket.go handleOperation` —
  `Operation_Delta` unwraps `data.slide_delta_processor[]`; each decoded
  `*slidetypes.Delta` goes to the client's `EventHandler` (this bridge's
  `onEvent` → `emitDelta`). `uq_seq_id` is the upstream monotonic revision;
  the client persists `seqID` = max seen.
- **Message/thread shapes**: `slidetypes/message.go` (`Message`,
  `Reaction{reaction, sender_fbid, reaction_timestamp_ms}`,
  `Mention{profile_range_type, user_fbid}`, `IsPinned`, `RepliedToMessage*`),
  `slidetypes/messagecontent.go` (`MessageContentWrapper` typename map —
  text/admin-text/image/video/audio/animated/raven/multimedia/sticker/XMA/
  music/AI; unknown → `MessageContentUnknown`), `slidetypes/thread.go`
  (`ThreadInfo`: `users`, `admin_user_ids`, `pinned_messages`,
  `thread_subtype`, `is_group`; `TinyThread`, `ParticipantsOnlyThread`).

## Today's capture route (base c537267a)

`emitDelta` switches on the delta's Go type: `NewMessageEvent`,
`AdminMessageEvent`, `ParticipantJoinEvent`, `ParticipantLeaveEvent` (only
their embedded `*Message` is normalized), `CreateReactionEvent`,
`DeleteReactionEvent`, `EditMessageEvent`, `DeleteMessageEvent`. Everything
else — `UpdateThreadNameEvent`, `UpdateThreadImageEvent`, `PinMessageEvent`,
`PinThreadEvent`, `AdminChangeEvent`, `MuteThreadEvent`, `DeleteThreadEvent`,
`UpdateThreadFolderEvent`, `MarkRead/MarkUnread/ReadReceipt`, `UnknownEvent`,
`IgnoredEvent` — falls through with NO emit. Emitted events → `Sink.Emit` →
`internal/bridge.Normalize` → envelope kinds `message` / `edit` / `delete` /
`reaction` / `summons` with upstream identities `mid:` / `edit:` / `del:` /
`react:` / `summons:`.

## File map

| File | Native surface | Today |
|---|---|---|
| `new-message-shared-xma.json` | `SlideUQPPNewMessage` + `SlideMessageXMAContent` | captured `message` + `shared` + thumbnail |
| `create-reaction.json` | `SlideUQPPCreateReaction` | captured `reaction` add (ts + seq) |
| `delete-reaction-clockless.json` | `SlideUQPPDeleteReaction`, no reaction ts | captured via `uq_seq_id` transition ledger |
| `reaction-unknown-actor.json` | `sender_fbid:"0"` | captured, sender_id '' |
| `reaction-add-remove-add.json` | 3 deltas, rising seq | 3 transitions; replay dedupes |
| `pin-unpin-message.json` | `SlideUQPPPinUnpinMessage` | dropped (no branch) |
| `update-thread-name.json` | `SlideUQPPThreadName` + embedded admin msg | dropped |
| `participant-join.json` | `SlideUQPPParticipantsAddedToGroupThread` + member snapshot | admin msg only; snapshot lost |
| `participant-join-empty-users.json` | same, `users:[]` | same; partial snapshot covers nothing |
| `participant-leave.json` | `SlideUQPPParticipantLeftGroupThread` | admin msg only; actor/subject fields lost |
| `admin-change.json` | `SlideUQPPGenericMapAdminsKeyMutation` | dropped; admin_igids are IGID space |
| `edit-message-no-sender.json` | `SlideUQPPEditMessage` | captured `edit`; actor unavailable |
| `delete-message.json` | `SlideUQPPDeleteMessage` | captured `delete`; actor/time unavailable |
| `unknown-delta-typename.json` | unmapped `__typename` | dropped (UnknownEvent never emitted) |
| `read-receipt-and-pinned-thread.json` | `SlideUQPPReadReceipt`, `SlideUQPPPinnedThread` | dropped by design / by fall-through |
| `multimedia-two-attachments.json` | `SlideMessageMultiMediaContent` ×2 | first attachment only (multiplicity gap) |
| `thread-info-snapshot.json` | `GetThread` response shape | on-demand read only (roster route) |

## Identity and revision constraints (for the binding lane)

- **Revisions**: `uq_seq_id` is the only upstream-ordered revision, per-delta;
  reaction transitions use it when `reaction_timestamp_ms` is absent.
  Clockless deltas with no seq and no ts hit the transition ledger's
  ambiguous verdict — journaled, never queued, never guessed.
- **Id spaces**: `sender_fbid`/`user_fbid` are `interop_messaging_user_fbid`
  (messaging identity — mentions, from_me); `igid`/`admin_igids`/`admin_user_ids`
  are the public Instagram id — NOT interchangeable; `thread_fbid` on deltas is
  the SHORT thread id despite the field name (long thread ids appear in
  `GetThread`/`SendData`); `message_id` (`mid.$…`) is the only message handle.
- **Polls**: NO poll delta or message-content typename exists at v0.2609.0.
  An IG DM poll arrives — if at all — as `UnknownEvent`/`MessageContentUnknown`:
  unsupported by the pinned transport. Do not import Business-API semantics.
- **Actors**: edit/unsend deltas carry no sender (unavailable, not "a member").
  `ParticipantLeaveEvent` alone types actor vs subject; join/leave subjects are
  otherwise only in admin text or by diffing member snapshots.
- **Snapshots**: `thread.users[]`, `admin_igids`, `pinned_messages[]` and the
  `GetThread` read are covered-set snapshots — partial lists erase nobody.

The check `internal/gateway/instameow/platform_observations_test.go` decodes
every delta fixture through the pinned `slidetypes.Delta.UnmarshalJSON` and
asserts the decoded event type — current library behavior only; it makes no
claim the bridge emits these events today.
