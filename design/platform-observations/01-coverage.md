# Coverage: source capability is not model visibility

Research date: 2026-10-30. Implementation snapshot:
`02ef3966f601a624d7b2db4a517a6c9e8db7214f`. The inspected parser/audit paths
are unchanged from the earlier investigation head `a535a6960`. Live configuration,
private rows, unmerged bridge branches and installed collector were not inspected.
This is source evidence, not a deployment capability declaration.
The census below is the historical baseline. The candidate mapping at the end
records subsequent implementation; neither section attests to a deployed version.

## Stage vocabulary

Track each object/field through **upstream / capture / Record / projection /
transcript / context / tool**. At each stage distinguish implemented, not implemented,
unsupported, disabled and unavailable; unknown means not inspected, not unsupported.
Permission, account mode, room mode, source version and last successful observation
are separate columns. A partial field or time window carries its coverage.

## Source-grounded census

| Platform/object | Upstream / prerequisites | Inspected Nora boundary | Next missing contract |
|---|---|---|---|
| Signal message metadata | Pinned signal-cli JSON exposes quote, mentions, previews, expiry, view-once and contacts [S] | `signal/parser.py:parse_event` keeps quote/mentions/media/edit/touch; `_body_parts` does not normalize every JSON field | Preserve meaningful source fields as typed metadata; never treat raw_json custody as exposure |
| Signal polls/pins | **Pinned v0.14.7** `JsonDataMessage` has pollCreate/pollVote/pollTerminate and pinMessage/unpinMessage [S] | Parser has no typed poll/pin branch; contentless input can return None | Capture known native fields; synthetic pinned-version vectors before claiming vote/close semantics |
| Signal room/DM | groupInfo on data messages; separate directory/group reads exist | `parse_event` requires groupId, so DM is excluded in this snapshot | Keep admission scope explicit; group ID is not full membership history |
| Discord responses | Gateway reaction add/remove/clear and poll vote add/remove [D] | `discord/parser.py:_body_parts` renders only a poll marker, or forwarded/embed marker, for otherwise contentless messages | Native poll/options/tally and response transitions must survive beyond markers |
| Discord room | Channel/thread/member events [D]; intent/permission and cache readiness matter | `discord/roster.py:DiscordRoster` reads guild members, not exact channel access | Label roster scope; do not claim guild membership equals channel audience |
| WhatsApp responses | Baileys declares messages.reaction, messages.update and poll update utilities [W] | `whatsapp/parser.py:parse_event` handles message/reaction/delete; empty text/media returns None | Capture poll definitions/decrypted updates from pinned dependency; source key needed for decryption cannot be invented |
| WhatsApp room/history | Baileys has groups.update, group-participants.update, messaging-history.set [W] | Bridge header explicitly excludes polls/location/typing/receipts; no app whatsapp/roster.py at inspected head | Native callbacks -> typed room events and coverage; verify composition rather than infer a roster from platform ability |
| Instagram response/artifact | Current integration uses its own bridge/private transport, not the Business messaging contract | `instagram/parser.py:parse_event` handles edit/reaction/unsend, reply/mention/shared item; at most one media dict | Multiplicity and additional events need transport fixtures; shared thumbnail is not full artifact content |
| Instagram room | Existing bridge member route | `instagram/roster.py:InstagramRoster` uses thread members' messaging fbid | Current membership read does not establish historical join/leave events; igid and fbid are not interchangeable |
| Telegram bot reactions | Admin + explicit allowed_updates; user transitions and anonymous count updates differ [T] | No adapter audited in this snapshot | Mode/permission mapping before capture; aggregate counts cannot supply actors |
| Telegram bot polls | Poll updates limited by poll source/state; poll_answer only non-anonymous bot-sent polls [T] | Not implemented here | No claim to observe all human-created polls or all voters |
| Typing | Various transports can emit it | Explicit operator exclusion | No Record, wake or model block |

Across audited platforms `SqlAuditLog.append_reaction` stores target-linked
MESSAGE_REACTION and reprojects the target message. `append_edit` stores MESSAGE_EDIT.
These are existing owners to extend, not replace. Neither an append nor raw_json
proves current reaction state or model visibility. The schema's SOCIAL_POLL is a
social polling record; do not reuse it as a group-choice poll merely because of
its name. Inspect its actual fields/consumers before considering reuse.

## Primary sources and limits

- **[S]** [signal-cli v0.14.7 JsonDataMessage](https://github.com/AsamK/signal-cli/blob/v0.14.7/src/main/java/org/asamk/signal/json/JsonDataMessage.java):
  pinned serializer establishes available fields; it does not prove Nora parses
  them or establish installed runtime version. Poll payload detail remains fixture work.
- **[D]** [Discord Gateway events](https://docs.discord.com/developers/events/gateway-events):
  poll multi-select produces one add/remove per answer. Complete reaction clears
  require a target/set operation, not a fabricated per-user deletion history.
- **[W]** [Baileys event types](https://github.com/WhiskeySockets/Baileys/blob/master/src/Types/Events.ts)
  and [message utilities](https://github.com/WhiskeySockets/Baileys/blob/master/src/Utils/messages.ts):
  upstream main establishes candidate APIs only. Resolve the installed package
  revision and poll decryption path before adopting exact payload mappings.
- **[T]** [Telegram Bot API Update](https://core.telegram.org/bots/api#update):
  admin/allowed_updates and bot-sent poll restrictions limit available evidence.
  [MTProto reactions](https://core.telegram.org/api/reactions) are a different
  account/API mode; do not apply client capabilities to the bot adapter.

Upstream pages are research evidence as of this date, not frozen protocol fixtures.
No public API research was substituted for Instagram's actual private transport.
No live accounts were queried. Re-check revision and fill unknown cells during build.

## Pinned native-source evidence (implementation cluster addendum)

The generated [Python fixtures](../../tests/fixtures/platform_observations/README.md),
[WhatsApp fixtures](../../deploy/whatsapp-bridge/testdata/platform-observations/README.md)
and [Instagram fixtures](../../deploy/instagram-bridge/internal/bridge/testdata/platform-observations/README.md)
pin the discovery inputs. Distinguish their original capture outcome from the
subsequent binding changes below.

- **[W] WhatsApp transport pinned at `@whiskeysockets/baileys 7.0.0-rc13`.**
  The discovery snapshot subscribed `messages.upsert` (type `notify`),
  `connection.update` and `creds.update` only; poll definitions, poll votes, poll snapshots, pin
  transitions, room changes, membership events and shared artifacts are
  upstream-supported but reach the typed path only through the capture lane's
  explicit bindings. Poll votes require creation-message secret material: a
  vote without its creation secret is unavailable — recorded as a coverage
  gap, never a guessed tally. WhatsApp has no global ordering
  token; per-field clocks plus arrival order are the honest ordering
  evidence. Snapshot payloads are covered sets — an omitted member is
  `unknown`, never a deletion. The conditional WhatsApp stanza reuse noted
  in the generated fixtures is a fixture property, not a live incident.
- **[IG] Instagram transport pinned at `go.mau.fi/mautrix-meta v0.2609.0`.**
  `uq_seq_id` orders deltas within a stream; clockless deltas are ambiguous
  and journaled, not ordered by guess. Polls are explicitly unavailable at
  this version — an `unavailable` answer, never a confident `nothing_found`.
  The hypothetical `UnknownEvent` shape in the findings is a placeholder
  sketch, not an observed upstream poll payload.
- **[S] Signal (post-addendum source lane).** Poll `object_ref` now carries
  target creator + timestamp; an unknown creator is an explicitly unresolved
  ref with a `target_author` coverage gap. Pin add/remove addressing is
  `(timestamp, target_author_id)` so two authors' pins on the same timestamp
  cannot collapse.
- **[D] Discord (post-addendum source lane).** Guild membership/role events
  retain their guild `object_ref` and guild-grained coverage; the bridge
  binds scoped copies only to configured guild/channel conversations — guild
  grain is labelled source grain, not channel audience, and a new member is
  the SUBJECT, never the invented actor.
- Typing notifications remain excluded by decision.
- Those discovery fixtures alone establish neither capture nor model visibility.

## Integrated implementation candidate (2026-11-02)

Source mapping inspected through `93a12364d`; integrated acceptance is incomplete.
This supersedes the baseline's missing-capture cells for the selected operations,
not its platform/account-mode limitations. The final PR owns acceptance receipts.

| Source | Candidate capture | Coverage and remaining verification |
|---|---|---|
| Signal groups | `parse_observations` and the bridge wrapper carry poll definition/vote/close, pin/unpin, room fields, contacts/previews and expiry/view-once metadata alongside messages or as observation-only envelopes | Target creator remains separate from actor; absent creator stays unresolved. Existing group-only admission remains. Generated parser/capture checks do not establish live signal-cli delivery |
| Discord configured rooms | Poll definitions/votes/finalization, reaction clears, pins, thread/channel/member/role observations reach the existing bridge journal | Guild-grained observations bind only to configured child conversations. Intent/cache restrictions remain. Reaction clear capture still requires reconciliation with the shipped target-count reader before this family is complete |
| WhatsApp admitted conversations | Bound stanza and group callbacks produce poll, pin, membership, room and shared-artifact observations through the existing capture pipeline | Creation-secret lookup is room/creator-bound and private. Clockless recurrence is retained as ambiguous, never guessed replay; unavailable selections do not become withdrawals. Native focused checks cover generated inputs, not live source completeness |
| Instagram admitted conversations | Pinned typed deltas feed room/pin/member/admin observations and shared artifacts; supplied attachment multiplicity reaches normalization and custody | Polls remain unsupported by the pinned transport; member FBIDs and admin IGIDs stay distinct. Missing member sets are partial rather than complete empty rosters. The native Go suite passes; this does not establish live source completeness |

The common candidate path is envelope v2 → typed observation history/current head
→ durable stimulus obligation → enriched transcript, existing Standing and scoped
task reads. `record_query` retains message compatibility and adds typed reads;
`poll_list`, `poll_read` and `room_state_read` serve bounded tasks. No new context
block, feed crawler or trace-to-social collector is introduced.

Source merge, deployment, live platform delivery and model-routing measurement
are separate obligations. None is established by the generated fixtures or the
current implementation mapping.
