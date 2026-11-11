# Native platform-observation fixtures — Instagram (mautrix-meta/instameow)

GENERATED payloads documenting what the pinned private transport actually
hands this bridge, per `design/platform-observations/observations.md`. No production
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

## Capture route (post-binding)

`emitDelta` switches on the delta's Go type. Utterance events —
`NewMessageEvent`, `AdminMessageEvent`, `ParticipantJoinEvent`,
`ParticipantLeaveEvent`, `CreateReactionEvent`, `DeleteReactionEvent`,
`EditMessageEvent`, `DeleteMessageEvent` — emit v1 message/touch events as
before (`mid:` / `edit:` / `del:` / `react:` / `summons:` upstream
identities). Object events now ALSO emit typed `kind:"observation"` events,
built in `internal/gateway/instameow/observations.go`:

- `PinMessageEvent` → `pin` add/remove per `pinned_messages`/`unpinned_messages`
  id, object `pins:{room}`, transition ledger keyed `room|pin|{mid}`.
- `UpdateThreadNameEvent` / `UpdateThreadImageEvent` → `room` revise
  `fields{title}`/`fields{image}`; the embedded admin message is emitted
  too (deduped by mid).
- `ParticipantJoinEvent` → `membership` snapshot of `thread.users[]`, the
  POST-ACTION covered member set (subjects are `interop_messaging_user_fbid`).
- `ParticipantLeaveEvent` → `membership` remove; `subject_ref` is
  `left_participant_fbid`, `actor_id` `actor_fbid`, `state:"left"` only
  when actor==subject.
- `AdminChangeEvent` → `membership` snapshot on `admins:{room}` — IGID
  space, a different object from `members:{room}`.
- A `shared` XMA message → `artifact` observation as a SIDECAR on the same
  v2 envelope (kind stays `message`; the app accepts a typed sidecar).

`internal/bridge.Normalize` stamps `scope.account_key`, binds scope to the
envelope room, validates the dict (`observationProblem`), and envelopes it
as v2 (`message:null`, `observation` set, `sent_ms` null when the delta
carried no clock). Still dropped by design: `PinThreadEvent` (inbox-level),
read receipts/markers, folder/mute/thread-delete deltas, `UnknownEvent`
(IG polls have no typed route — explicit unavailability, never a guessed
typename).

## File map

| File | Native surface | Today |
|---|---|---|
| `new-message-shared-xma.json` | `SlideUQPPNewMessage` + `SlideMessageXMAContent` | captured `message` + `shared` + `artifact` sidecar (v2) + thumbnail |
| `create-reaction.json` | `SlideUQPPCreateReaction` | captured `reaction` add (ts + seq) |
| `delete-reaction-clockless.json` | `SlideUQPPDeleteReaction`, no reaction ts | captured via `uq_seq_id` transition ledger |
| `reaction-unknown-actor.json` | `sender_fbid:"0"` | captured, sender_id '' |
| `reaction-add-remove-add.json` | 3 deltas, rising seq | 3 transitions; replay dedupes |
| `pin-unpin-message.json` | `SlideUQPPPinUnpinMessage` | captured: `pin` add/remove observations, `pins:{room}`, seq-grounded |
| `update-thread-name.json` | `SlideUQPPThreadName` + embedded admin msg | captured: `room` revise `{title}` + admin msg |
| `update-thread-image.json` | `SlideUQPPThreadImage` + embedded admin msg | captured: `room` revise `{image}` + admin msg |
| `participant-join.json` | `SlideUQPPParticipantsAddedToGroupThread` + member snapshot | captured: `membership` snapshot (covered AFTER set, fbid subjects) + admin msg |
| `participant-join-empty-users.json` | same, `users:[]` | captured: snapshot with empty covered set |
| `participant-leave.json` | `SlideUQPPParticipantLeftGroupThread` | captured: `membership` remove, typed actor/subject + admin msg |
| `admin-change.json` | `SlideUQPPGenericMapAdminsKeyMutation` | captured: `membership` snapshot on `admins:{room}` (IGID space) |
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
every delta fixture through the pinned `slidetypes.Delta.UnmarshalJSON`,
asserts the decoded event type, AND routes each through `emitDelta` to a
recording sink — decode AND emission are both asserted per fixture.
