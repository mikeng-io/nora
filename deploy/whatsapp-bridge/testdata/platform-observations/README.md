# Native platform-observation fixtures — WhatsApp (Baileys)

GENERATED payloads documenting what the pinned upstream transport actually
hands this bridge, per `design/platform-observations/observations.md` (the capture mapping
and fixture proving what the adapter emits"). No production rows, no captured
deltas, no credentials: every JID, stanza id, timestamp and secret below is
synthetic placeholder-shaped data (`55501` fiction range, repeated/zero digits,
`1234567`/`0123456` sequences).

## Pinned source

- **SDK**: `@whiskeysockets/baileys` `7.0.0-rc13` (`deploy/whatsapp-bridge/package.json`;
  `package-lock.json` resolved tarball sha is the integrity pin).
- **Event union**: `node_modules/@whiskeysockets/baileys/lib/Types/Events.d.ts`
  — `BaileysEventMap`. All upstream evidence enters through `sock.ev.on(...)`.
- **Payload shapes**: `WAProto/WAProto.proto` — `Message.pollCreationMessage`
  (V2/V3/V5 aliases), `pollUpdateMessage`, `pollResultSnapshotMessage` (V3
  alias), `reactionMessage`, `pinInChatMessage`, `eventMessage`,
  `encEventResponseMessage`; `WebMessageInfo.reactions` (41), `.pollUpdates`
  (45), `.pollAdditionalMetadata` (46), `.messageSecret` (49), `.pinInChat`
  (54), `.messageStubType`/`messageStubParameters` (24/26).
- **Vote decryption**: `lib/Utils/process-message.js` — `decryptPollVote`
  (exported, live) needs `{pollCreatorJid, pollMsgId, pollEncKey, voterJid}`;
  `pollEncKey` = the creation message's `messageContextInfo.messageSecret`.
  The analogous `pollUpdateMessage → messages.update{pollUpdates}` emit block
  is COMMENTED OUT in rc13, so live votes are decrypted at the stanza by
  `observations.js`; `encEventResponseMessage` decryption is live and works
  now that `getMessage` answers from the recent-message cache.
- **Group surfaces**: `groups.update` → `Partial<GroupMetadata>[]`,
  `group-participants.update` → `{id, author, participants, action}`,
  `groups.upsert` → `GroupMetadata[]`, `messaging-history.set` →
  `{chats, contacts, messages, pastParticipants, isLatest, progress, syncType}`
  (`lib/Types/Events.d.ts`, `lib/Types/GroupMetadata.d.ts`, stub enum in
  `WAProto.proto` `WebMessageInfo.StubType`).

## Capture route (post-binding)

`bridge.js` subscribes `messages.upsert` (type `notify` only),
`connection.update`, `creds.update`, and the observation surfaces
`messages.update`, `messaging-history.set` (secret custody only — never
emitted), `groups.upsert`, `groups.update`, `group-participants.update`,
`group.join-request`. Upsert stanzas → `extractMessageEvent` (ordinary
turns) AND `observations.js:stanzaObservations` (typed object reports:
poll/vote/snapshot, pin, stub room/membership, shared artifacts). An event
callback payload routes through the same `observations.js` extractors.
Every path feeds `pipeline.capture` → `normalize.js:parseEvent` (wire
validation) → journal → enqueue: ordinary turns stay v1 envelopes; a typed
observation (alone, or as a sidecar next to an admitted message) is a v2
envelope with `observation` set and `message` null for pure observations.
Not subscribed: `messages.reaction` (the same stanza already arrives via
upsert — subscribing would double-report one transition),
`group.member-tag.update` (a member label is not a role/state — no typed
field carries it), receipts/presence/newsletter families.

## File map

| File | Native surface | Today |
|---|---|---|
| `upsert-message.json` | upsert WAMessage, text | captured `message` |
| `upsert-reaction-add.json` | `reactionMessage` add | captured `reaction` add |
| `upsert-reaction-remove-unresolved-target.json` | `reactionMessage` `text:""` | captured `reaction` remove; target unresolved |
| `upsert-reaction-add-remove-add.json` | 3 stanzas, one reaction stanza id | 3 stanzas -> 2 upstreams today: re-add dedupes (identity limit, see file) |
| `upsert-reaction-unknown-actor.json` | reaction stanza, no `key.participant` | captured; actor unresolved to room JID |
| `upsert-poll-creation.json` | `pollCreationMessage` + `messageContextInfo.messageSecret` | dropped |
| `upsert-poll-update-encrypted.json` | `pollUpdateMessage` sealed vote | dropped; decrypt prerequisites in file |
| `upsert-poll-result-snapshot.json` | `pollResultSnapshotMessage` aggregate | dropped; anonymous counts |
| `upsert-poll-result-snapshot-empty.json` | empty `pollVotes` | dropped; complete-empty set |
| `upsert-pin-unpin.json` | `pinInChatMessage` PIN/UNPIN_FOR_ALL | dropped |
| `upsert-group-stub-subject.json` | `messageStubType` 21 | dropped at `!msg.message` |
| `upsert-group-stub-participant-add.json` | `messageStubType` 27 | dropped at `!msg.message` |
| `upsert-shared-objects.json` | contacts / location / event | dropped |
| `upsert-enc-event-response.json` | `encEventResponseMessage` | dropped; upstream decrypt also blocked by `getMessage` stub |
| `event-groups-update.json` | `groups.update` payload | not subscribed |
| `event-group-participants-update.json` | `group-participants.update` payloads incl. empty `author` | not subscribed |
| `event-groups-upsert-snapshot.json` | `groups.upsert` full + partial | not subscribed |
| `event-messaging-history-set.json` | `messaging-history.set` payload | not subscribed; `append` upserts also dropped |
| `event-messages-update-poll.json` | `messages.update` `{pollUpdates}` | not subscribed; rc13 live-vote emit commented out |

## Identity and revision constraints (for the binding lane)

- **Event vs object identity**: `upstream` per transition (`r:` carries
  add/remove because stanza ids are reused); object identity is the stanza id
  / target key. Replay of identical bytes reproduces the identical source
  key → journal dedupe. MEASURED LIMIT: a re-add that reuses the reaction
  stanza id produces the identical `r:` identity as the first add and dedupes
  away — today's scheme cannot express add/remove/add on a reused id; the
  binding seam needs a further discriminator or an upstream check that each
  transition actually reuses the id.
- **Revisions**: there is NO global upstream ordering token on individual
  upserts — only per-field source clocks (`senderTimestampMs`, whole-second
  `messageTimestamp`). `group-participants.update` and `groups.update` carry
  no sequence at all; `messaging-history.set` has `progress`/`chunkOrder`/
  `isLatest` for its own window only. Arrival order is the bridge's only
  cross-family order.
- **Decryption boundaries**: poll votes need the creation's
  `messageContextInfo.messageSecret` cached at capture time; a vote whose
  creation predates capture is permanently undecryptable (record
  `unavailable`, never a guessed selection). `PollResultSnapshotMessage`
  counts are anonymous: no voter identity, no unique-voter total.
- **Actor semantics**: group actor = `key.participant` (or `author` on
  participants.update); absent = unknown, never invented. `remove` does not
  imply voluntary departure.
- **Snapshots**: `participants[]`/`admin[]`/`pollVotes[]` are covered sets;
  an omitted member is unknown, not absent.

The check `platform_observations.test.mjs` (next to `normalize.js`) asserts
today's outcome per fixture — decode shape and the current drop/capture
result. It pins current behavior only; it makes no claim that the mapped
families are already captured.
