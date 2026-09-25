# WhatsApp Transport — Implementation Notes

Implemented 2026-09-04 on branch `design/whatsapp-adoption` (this worktree
only; master untouched). Scope: the TRANSPORT phase of
`design/whatsapp-adoption/` — groups admitted to the friends unit, 1:1
deny-by-default until the DM study lands.

## What was built

**Bridge daemon — `deploy/whatsapp-bridge/`** (Node 20, adapted from Hermes'
`~/.hermes/hermes-agent/scripts/whatsapp-bridge/`, one direct dependency:
`@whiskeysockets/baileys` 7.0.0-rc13, exact pin + lockfile):

- `bridge.js` + `bridge_helpers.js`: Baileys socket, built-in `http` server
  (no express), SSE push on `GET /events` (replaces Hermes' long-poll
  `/messages`), `POST /send` `/send-media` `/edit` `/react`, `GET /self`,
  `GET /health`. Inbound emits only `messages.upsert` type `notify` —
  history sync never reaches the audit trail. MESSAGE_EDIT arrives as a
  message event with `edit_of`.
- **Drop-and-count, not Hermes' silent queue** (review M1): while no SSE
  client is connected, a 500-deep buffer holds events; overflow drops the
  OLDEST, increments `dropped_events` (permanent on `/health`), and the next
  connecting client gets a one-shot `{"type":"dropped"}` event, which Nora's
  listener logs at WARNING.
- **LID↔phone maps in BOTH directions** from the session's
  `lid-mapping-*.json` files (Hermes pattern, extended): rebuilt on
  `creds.update` plus a 30s rescan. Every event carries `sender_phone` /
  `sender_lid` (and both forms for each mention / quoted participant) so the
  adapter's membership tests work whichever JID spelling Baileys delivered.
- Pairing: `docker compose run --rm whatsapp-bridge --pair --phone <digits>`
  prints a **pairing code** (Baileys `requestPairingCode`). Deviation from
  design 01's qrcode-terminal sketch, taken to honour the "no dependencies
  beyond Baileys + minimal HTTP" invariant — the QR string is still printed
  as a fallback. `loggedOut` never deletes the session and never
  reconnect-loops: HTTP keeps serving with `paired:false`.
- `Dockerfile`: node:20-slim + `apt-get install curl` (load-bearing —
  review B1; the compose healthcheck curls `/health`), `npm ci --omit=dev`.

**Adapter — `nora/platform/whatsapp/`** (third `PlatformAdapter`
realization, mirroring the Signal/Discord module split):

- `jid.py` — pure normalization: phone form `"+<digits>"` (Signal's E.164
  shape), lid form bare digits (the `lid` kind already in
  `nora/domain/identity.py:54` — not re-added), group `conversation_id` =
  `<id>@g.us` verbatim, 1:1 = `<phone>@s.whatsapp.net` when resolved else
  the raw `@lid` JID.
- `parser.py` — bridge SSE event → `NormalizedMessage`. Drops non-message
  types, `from_me` (R-12 first pass), broadcast/newsletter JIDs, empty
  turns. Mentions carry BOTH normalized forms; `quote.author_id` normalized
  the same way with `quoted_ts=0` as the pinned no-parent-pointer
  degradation; media events become `MediaRef`s (voice notes included,
  `ptt` flag) with empty `fetch_token` — custody is a later phase, and
  nothing is dropped on the floor.
- `listener.py` — SSE client mirroring `signal/listener.py`; heartbeats
  advance `last_event_at` (that is what feeds `/health`'s per-platform
  `last_event_age_seconds`, the real socket-death detector).
- `send.py` — refuses `conversation_type != "group"` without an HTTP call
  (1:1 deny-by-default); 503/429 map to retryable; attachments go through
  `/send-media` with the caption on the first.
- `react.py` — addresses by stanza id (`to_message_id`); no id →
  `supported=False` (never guess at a message).
- `self_id.py` — one `GET /self`, hard-fail (Discord's shape); an unpaired
  bridge fails startup via `AdapterRegistryError`, it never runs deaf.

**Wiring**: `nora/config.py` (`WhatsAppPlatformConfig{enabled=false,
base_url}`, defaulted), `nora/platform/registry.py` (one more
`if platforms.<x>.enabled:` block + `_WhatsAppPlatformConfig` protocol),
`docker-compose.yml` (`whatsapp-bridge` service mirroring signal-cli:
private `nora` net, no published ports, `./state/whatsapp:/app/session`,
curl healthcheck, `network: host` build), `config.toml` /
`config.example.toml` (`[platforms.whatsapp]`, off), `identity.example.toml`
(phone+lid account example), `policy.example.toml` (group JID room example +
1:1 deny note), `docs/runbook.md` §4b (pairing, admission-by-recreate,
failure-mode table), release fragment
`release-fragments/released/V5/whatsapp-transport.json`.

**The contract**: `design/whatsapp-adoption/03-transport-contract.md` pins
every wire shape and normalization rule; both sides were built against it.

## Engagement (the 2026-09-01 posture)

No new `[engagement]` keys. WhatsApp HAS structural mentions
(`contextInfo.mentionedJid`), so @mention and reply-to-Nora ride the
EXISTING `MentionPolicy`/`ReplyToNoraPolicy` against
`registry.self_aliases()` — which now includes the bot's phone AND lid from
`GET /self`. The text trigger stays off. **Voice notes DO engage today**:
`VoiceNotePolicy` is global (composition.py:1124, operator directive
2026-08-26) and fires on any `voice_note` MediaRef regardless of transcript.
Media custody/transcription (fetch_token) is the later phase, but the note
engages her now — the transport carries the `ptt` flag so the parser marks
it correctly.

## What was tested

- Bridge: `node --test` — 25/25 (JID parsing, both-direction LID maps,
  drop-and-count buffer, event extraction incl. edits/quotes/media,
  reconnect backoff). Host smoke: unpaired boot serves `/health` per
  contract, `/self`→503, `/send`→503. `docker build --network=host`
  succeeded and the image's `/health` answers via the curl the Dockerfile
  installs.
- Python: 134 targeted tests green (parser contract — group/dm JID forms,
  sender fallback chain, both-form mentions matching `derive_identity`'s
  alias values, reply-to-Nora, voice-note survival, all drop rules; send /
  react / listener / self_id suites; registry tests incl. unpaired-bridge
  fail-fast). Shared contract suites extended and green:
  `test_port_conformance.py`, `test_send_contract.py`,
  `test_reaction_contract.py` (WhatsApp added as a third arm each).
- Gate: `make gate` — ruff clean, mypy clean, lint-imports 6/6 kept,
  pytest full suite green. (Three PRE-EXISTING ruff errors — present on
  master, in `tests/test_factory.py` and `tests/web/test_skills.py` — were
  fixed in passing because the acceptance bar is a clean gate; two
  `ClassVar` annotations and one line wrap, no behaviour change.)

## Contract mismatches caught in review (fixed before commit)

- Bridge `/self` returned the lid as a JID under `lid` with digits under
  `lid_digits`; the contract says digits. Bridge now answers
  `{phone: digits, lid: digits, jid, lid_jid, name}`.
- The LID map was one-directional; phone-form senders never carried their
  lid. Both directions now ride every event.

## BLOCKED / not done (by design)

- **No real pairing or WhatsApp connection attempted** — the number and
  pairing are operator steps; the daemon is built so `--pair` works when
  run.
- No `depends_on` from `nora` to `whatsapp-bridge` in compose: the service
  is optional (`enabled=false` default), and an enabled-but-unpaired bridge
  fails Nora's startup fast (restart loop until paired) rather than running
  deaf. If that boot ordering ever annoys, `depends_on: service_healthy` is
  a one-stanza change.
- No WhatsApp `RosterSource` and no `MediaSource` this phase: room-scoped
  tools answer `unavailable` on WhatsApp rooms (fail-closed), and media
  custody/transcription is the follow-on phase that makes voice-note
  engagement real.
- `send-media` outbound audio is sent as a file (no `ptt` voice bubble) —
  one-line addition if wanted later.
