---
type: design
title: "WhatsApp Transport — Bridge ↔ Adapter Wire Contract"
description: "The exact JSON shapes on the whatsapp-bridge HTTP/SSE surface and the JID normalization rules the Nora adapter applies. Both sides are built against this document; drift here is a contract-test failure, not a judgment call."
tags: [nora, whatsapp, contract, sse, jid]
timestamp: '2026-09-04'
edges:
  - {dst: 00-overview.md, rel: parent, provenance: asserted}
  - {dst: 01-bridge-container.md, rel: details, provenance: asserted}
  - {dst: 02-identity-and-policy.md, rel: implements, provenance: asserted}
---

# WhatsApp Transport — Bridge ↔ Adapter Wire Contract

Pinned at implementation time (2026-09-04). The bridge
(`deploy/whatsapp-bridge/bridge.js`, Baileys/Node) owns the WhatsApp wire;
the adapter (`nora/platform/whatsapp/`, Python) owns normalization into
Nora's vocabulary. This document is the only place either side's shapes are
defined; `tests/test_whatsapp_parser.py` and friends are its executable half.

## HTTP surface (bridge, bound 0.0.0.0:18081 on the private `nora` net)

| Route | Purpose |
|---|---|
| `GET /health` | Process liveness + drop counter (below). Always 200 while the HTTP server serves — pairing state is a FIELD, never a status code. |
| `GET /events` | SSE push stream (replaces Hermes' long-poll `/messages`). Replays the bounded offline buffer on connect, then streams live. |
| `GET /self` | The bot's own id forms (below). 503 `{"error": "not_connected"}` until the socket is open. |
| `POST /send` | `{jid, text}` → `200 {ok: true, id, timestamp_ms}`. 400 invalid input; 503 socket down; 500 send failure. |
| `POST /send-media` | `{jid, media_type, data_base64, filename?, caption?}` → same result shape as `/send`. Kind derived from the MIME top-level type. |
| `POST /edit` | `{jid, id, text}` — edit one of the bot's own messages. `200 {ok: true}`. |
| `POST /react` | `{jid, message_id, emoji, remove?}` → `200 {ok: true}`; 404 `{"error": "unknown_message"}` when the id is not in the recent-message cache. |

`GET /health` body:

```json
{"ok": true, "connected": true, "paired": true,
 "dropped_events": 0, "buffered_events": 0, "uptime_seconds": 123}
```

`connected` = the WhatsApp socket is open; `paired` = usable auth state
exists. `dropped_events` is the drop-and-count counter (below) — the R-5
fix Hermes' silent `shift()` never had.

## SSE event shapes

One JSON object per `data:` line, discriminated by `type`.

### `message`

```json
{
  "type": "message",
  "id": "3EB0F1…",
  "chat_jid": "176569000000000000@g.us",
  "is_group": true,
  "from_me": false,
  "sender_jid": "60000000000001@lid",
  "sender_phone": "85290000100",
  "sender_lid": "60000000000001",
  "push_name": "Alice",
  "text": "hello",
  "timestamp_ms": 1756963200000,
  "mentioned": [{"jid": "85202411020@s.whatsapp.net", "phone": "85202411020", "lid": null}],
  "quoted": {
    "id": "3EB0AA…", "participant_jid": "85202411020@s.whatsapp.net",
    "participant_phone": "85202411020", "participant_lid": null,
    "timestamp_ms": 1756963100000, "text": "earlier message"
  },
  "media": [{"kind": "audio", "mimetype": "audio/ogg; codecs=opus",
             "voice_note": true, "seconds": 4, "size": 12345,
             "caption": null, "filename": null}],
  "edit_of": null
}
```

Field rules:

- `chat_jid` is Baileys' `key.remoteJid`, unmodified. **This is the field
  that feeds `conversation_id`** (after the group/1:1 normalization below).
- `sender_jid` is `key.participant` in groups, `key.remoteJid` in 1:1 —
  raw, unmodified.
- `sender_phone` / `sender_lid` are digit strings (no `@`, no `+`, no
  device suffix), either null. The bridge resolves LID→phone from the
  session's `lid-mapping-*.json` files (Hermes bridge.js:252,606 pattern),
  refreshed as Baileys persists new mappings.
- `text` is the message's own text (`conversation`,
  `extendedTextMessage.text`) or the media caption. Never synthesized.
- `mentioned` entries come from `extendedTextMessage.contextInfo.mentionedJid`,
  each resolved to phone/lid digits where the map knows them.
- `quoted` comes from `contextInfo` (`stanzaId`, `participant`,
  `quotedMessage`). `timestamp_ms` is resolved from the bridge's
  recent-message cache (stanza id → timestamp); **null when the parent
  predates the cache** — the reply relation survives, the parent pointer
  does not.
- `media` entries: `kind` ∈ image|video|audio|sticker|document;
  `voice_note` is Baileys' `ptt` flag on `audioMessage`. No bytes — custody
  is a later phase; the event carries the FACT of media, never the blob.
- `edit_of` is the target stanza id when Baileys delivers a
  `protocolMessage` of type MESSAGE_EDIT; `text`/`media` then describe the
  NEW content. Nora records it BESIDE the message it edits, never over it —
  the audit trail is append-only — and the transcript shows the latest text
  at the original's position (`design/message-edits/`, 2026-09-23; before
  that it was ingested as its own row).

### `heartbeat`

```json
{"type": "heartbeat", "ts_ms": 1756963200000, "connected": true, "paired": true}
```

Every ~30s. Feeds the adapter's `last_event_at` so Nora `/health`'s
per-platform `last_event_age_seconds` (the real socket-death detector,
design 01 §Health) has something to measure in a quiet room.

### `dropped`

```json
{"type": "dropped", "dropped_total": 17}
```

Drop-and-count (design 01, review M1): while NO SSE client is connected,
inbound messages sit in a bounded in-memory buffer (cap 500). Overflow
drops the OLDEST event and increments `dropped_events`. When a client
connects with `dropped_events > 0`, this event is sent once before the
replay, and the counter stays on `/health` permanently.

## Normalization (adapter side — `nora/platform/whatsapp/`)

These rules are the policy-matching contract (design 02 §JID): policy.toml
matching is exact-string on `conversation_id`, and person resolution is
exact-string on `sender_id` against `identity.toml`.

- **conversation_id, group:** `chat_jid` verbatim (`<id>@g.us`).
- **conversation_id, 1:1:** `<phone>@s.whatsapp.net` when the chat's phone
  is known (direct phone JID, or LID JID resolved through the map);
  otherwise the raw `@lid` JID. Deny-by-default either way this phase.
- **conversation_type:** `"group"` iff `is_group`, else `"dm"`.
- **phone form:** `"+" + digits` everywhere a phone surfaces (`sender_id`,
  mention values, quote author, self aliases) — deliberately the same shape
  Signal's E.164 values take, so `identity.toml` reads the same on both
  platforms.
- **lid form:** bare digits (`"60000000000001"`), matching the `lid` kind
  already in `nora/domain/identity.py:54`.
- **sender_id:** phone form when known, else lid form, else the raw JID
  (last resort; matches nothing, fails closed).
- **mentions:** every normalized form of every `mentioned[]` entry —
  phone form AND lid form when both are known, so the MentionPolicy
  membership test works regardless of which form the self-alias set
  carries.
- **quote.author_id:** same normalization as `sender_id` off the quoted
  participant. `quote.quoted_ts` is `quoted.timestamp_ms`, or `0` when the
  bridge could not resolve one — a `by_ts` lookup misses on 0 and renders
  no parent, which is the honest degradation.
- **Dropped before `NormalizedMessage` is built:** non-`message` types
  (heartbeat/dropped are listener-level), `from_me` (R-12's first pass),
  `status@broadcast`/newsletter JIDs, and turns with neither text nor
  media.

## Engagement (design 02 §Engagement, the 2026-09-01 posture)

No new engagement config: the deterministic signals ride the EXISTING
policies, and the parser fills exactly what they read.

- **@mention** — WhatsApp HAS structural mentions
  (`contextInfo.mentionedJid`); the parser puts the normalized forms in
  `NormalizedMessage.mentions`, and `MentionPolicy` matches them against
  `registry.self_aliases()` (the bot's phone AND lid, from `GET /self`).
- **Reply-to-Nora** — the parser fills `quote.author_id` from the quoted
  participant; `ReplyToNoraPolicy` matches it against the same aliases.
- **Voice notes** — carried as `MediaRef(voice_note=True)` to the ambient
  tier (audit + `[voice note]` placeholder), never dropped. **Engagement
  consequence (corrected per Claude audit H-1):** `VoiceNotePolicy`
  (`nora/ingest/policy.py:97-117`) is GLOBAL — appended unconditionally in
  `nora/app/composition.py:1124` under the operator's 2026-08-26 directive
  「總之任何 Voice Note 都會直入上 Nora 度先囉」 — so a WhatsApp voice note
  DOES engage Nora today, even with no transcript (a failed transcription
  still engages; the policy is gated on the `voice_note` flag, not on
  transcript presence). Media custody/transcription (the `fetch_token`
  pipeline) is a later phase, but the note itself engages her now.
- The text trigger stays off (`config.toml:245-251`); nothing here
  re-enables it.
