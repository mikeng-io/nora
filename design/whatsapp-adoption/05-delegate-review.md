---

> Operational amendment, 2026-10-28: the recreate advice below records the old
> implementation. Valid identity/policy edits now hot-reload within three seconds
> when copied in place; workspace-binding edits still require recreation.
> See [the reload design](../policy-reload/00-design.md).

type: review
title: "WhatsApp Adoption Bundle — Delegate Review"
description: "Adversarial review of design/whatsapp-adoption/ against the live repo. Grounded in file:line evidence verified 2026-09-04."
tags: [nora, whatsapp, review, design]
timestamp: '2026-09-04'
---

# WhatsApp Adoption Bundle — Delegate Review

**Reviewed:** `design/whatsapp-adoption/` (00-overview, 01-bridge-container,
02-identity-and-policy, viz) on branch `design/whatsapp-adoption` (worktree
`nora-wt-whatsapp-adoption`, commits 1febd507 / 92e31db2 / 3b11d425 / 05b7e956).

**Method:** file-by-file cross-check against `/home/<deploy-user>/workspace/nora`
(master) — CLAUDE.md, docker-compose.yml, nora/domain/identity.py,
nora/platform/registry.py, nora/obs/health.py, design/09-decision-index.md,
config.toml, deploy/signal-cli/Dockerfile, Dockerfile — plus live probes
(node:20-slim curl, GitHub API, Hermes bridge source). Every finding cites
the evidence that grounds it. Findings below are ordered by severity.

---

## BLOCKING

### B1. Dockerfile sketch produces a container whose healthcheck cannot run

**What:** The bundle's Dockerfile sketch (`01-bridge-container.md:115-122`)
is `FROM node:20-slim` and installs nothing, but the compose spec
(`01:60-74`) healthchecks with `curl -fsS http://127.0.0.1:18081/health`.

**Evidence:** Live probe: `docker run --rm --entrypoint sh node:20-slim -c
'which curl wget'` → `NO_CURL_NO_WGET`. The two precedents the design claims
to mirror both install curl explicitly: `deploy/signal-cli/Dockerfile:22`
(`apt-get install -y --no-install-recommends curl gnupg`) and the Nora
`Dockerfile:12` (`apt-get install -y --no-install-recommends curl ffmpeg`),
which also healthchecks with curl (`Dockerfile:59`).

**Resolution:** Either add `RUN apt-get update && apt-get install -y curl`
(and the standard slim `ca-certificates` if the bridge calls external
endpoints) to the Dockerfile sketch, or use a Node-native healthcheck
(`node -e "fetch('http://127.0.0.1:18081/health').then(r=>process.exit(r.ok?0:1))"`).
The compose healthcheck line must match whatever the image actually has.
As written the container would boot, report `unhealthy` forever (default
`start_period`/`retries` exhausted), and `depends_on`-style consumers would
misread a healthy bridge as down — an ERROR reading as a PASS, the exact
class R-27 exists to prevent.

### B2. Engagement posture in the bundle is stale — text trigger is disabled in the live config

**What:** `00-overview.md:111` says "Tag-to-reply: quoted trigger token →
Strands agent → reply via bridge" and the architecture diagram labels the
agent path "Tag detection → Strands agent". The repo has **no** text-trigger
engagement today.

**Evidence:** `config.toml:243-249`:
`[engagement.trigger] # Text trigger phrase DISABLED 2026-09-01 (operator):
the base text trigger fired on a plain URL ("nora.67.zone") — substring match
catches the word inside domains. Engagement is now voice-note + @mention/
reply only. (MentionPolicy/ReplyToNoraPolicy, unaffected by this flag)`
with `enabled = false`.

**Resolution:** The v1 scope's engagement story must be rewritten around the
current posture. WhatsApp has no native @mention in the way Discord does;
the transport must decide how "mention/reply" maps to WhatsApp semantics
(presence of the bot number in a group message? reply-to-a-Nora-message?),
and voice notes are now a **first-class engagement path** — which means the
bundle's silence on inbound WhatsApp voice (media custody + transcription)
is a gap in the engagement model itself, not an out-of-scope media nicety.
At minimum the doc must delete or supersede "quoted trigger token" and pin
what triggers Nora on this platform.

---

## HIGH

### H1. "Only `SignalAdapter` exists today" — factually wrong; the identity claim is also already implemented

**What:** `00-overview.md:76` states "Nora's platform layer … only
`SignalAdapter` exists today. The WhatsApp adapter is the second
realization" and `02-identity-and-policy.md` describes adding
`whatsapp: {phone}` as a new vocabulary entry. Both are stale against master.

**Evidence:**
- `nora/platform/registry.py:36-39` imports `DiscordAdapter` **and**
  `SignalAdapter`; `nora/platform/discord/` exists. The registry comment
  (:96) already describes moving past "Signal-only".
- `nora/domain/identity.py:51-55` contains the LIVE vocabulary:
  `"signal": {phone, uuid}`, `"telegram": {user_id}`, `"discord": {user_id}`,
  **`"whatsapp": {phone, lid}`** — the WhatsApp entry the bundle proposes to
  add already exists on master, with `lid` already anticipated.

**Resolution:** Rewrite the identity section: the work is not "add the
vocabulary entry" (done), it is implementing the adapter, LID handling, and
policy wiring against an already-planned platform. The "second realization"
framing should be "third adapter" (Signal → Discord → WhatsApp). This is
exactly the class of drift that must be caught before implementation so the
implementation brief doesn't duplicate existing state.

### H2. LID (Linked Identity Device) handling is omitted from the identity/parser design

**What:** The bundle treats WhatsApp identities as phone numbers only
(`02: "whatsapp: {phone}"`; parser not specified). Baileys in 2025+ delivers
many senders as LID JIDs, not phone JIDs — especially in groups.

**Evidence:**
- Hermes' production bridge (the design's own precedent) explicitly handles
  this: `scripts/whatsapp-bridge/bridge.js:252` "Build LID → phone reverse
  map from session files (lid-mapping-{phone}.json)" and `:606` "WhatsApp
  now uses LID (Linked Identity Device) format: 60000000000001@lid".
- The live vocabulary already includes `lid` (`identity.py:55`), but the
  bundle never mentions it.

**Resolution:** The adapter contract must include a LID→phone resolution
step (the Hermes bridge's `lid-mapping-{phone}.json` approach is the proven
shape), and `persons.yaml` / identity matching must accept both `phone` and
`lid` forms. Without this, group messages where the sender surfaces as a LID
would fail person resolution → R-31-style refusal for people who are
actually known.

### H3. Policy change applies with recreate, not restart — bundle says restart

**What:** `02-identity-and-policy.md:45` says adding a room →
"restart → admitted". Master's config staleness machinery says restart is
**not** sufficient for the single-file bind-mount inode gotcha.

**Evidence:** `nora/obs/health.py:14-21` (config_staleness docstring): "the
single-file bind-mount inode gotcha that silently served a stale
identity.toml/policy.toml TWICE on 2026-08-08 (**a recreate is needed, not a
restart**)." The two files are exactly the bind-mounted `:ro` files this
admission flow edits.

**Resolution:** Change "restart" to "recreate" (`docker compose up -d` /
force-recreate) in the admission verification path, and verify via
`/health` `config.policy.disk_mtime` matching the file's mtime (the
bundle's verification step itself is correct — keep it, fix the verb).

### H4. WhatsApp JID room-admission format is unspecified

**What:** `02:44-45` says admission uses "the WhatsApp JID" but never pins
the format the policy `conversation_id` must store: 1:1 = `phone@s.whatsapp.net`? group = `id@g.us`? LID-form? The bundle greps show no JID format anywhere.

**Evidence:** The only occurrences of JID in the bundle are the two lines
above (grep `@s.whatsapp.net|g.us|LID` in the bundle → 0 hits). Nora's
policy matching is exact-string on `conversation_id`; a wrong or
un-normalized form silently denies every WhatsApp room (fail-closed, but
also silently inert — the 2026-08-05 signal-cli lesson the repo already
logged).

**Resolution:** Pin the JID normalization contract in the design (which
Baileys field feeds `conversation_id`, how LID/phone forms normalize, group
vs 1:1), and add it to the adapter contract tests.

---

## MEDIUM

### M1. Copying the Hermes bridge queue wholesale inherits silent message loss

**What:** The bundle proposes adapting Hermes' bridge. Hermes' in-memory
queue silently drops message 101+ during a burst/outage-replay.

**Evidence:** `scripts/whatsapp-bridge/bridge.js:272,379-380,774-775`:
`MAX_QUEUE_SIZE = 100`; on overflow `messageQueue.shift()` with no counter,
no log, no durable spill.

**Resolution:** For a durability-sensitive transport feeding an audit trail
(R-5 shape), the adapted bridge must either drop-and-count (surface the drop
via the SSE/health channel) or spill to disk. Silent loss of audit rows is
the failure class Nora's own design history treats as a bug, not a feature.

### M2. Inbound WhatsApp media is unaddressed (and voice notes are now an engagement path)

**What:** The bundle's v1 scope covers text traffic only and is silent on
images/files/voice inbound. With the text trigger disabled
(B2 evidence), voice notes are one of exactly two engagement signals in
Nora's current posture.

**Evidence:** Signal path already gates voice: `nora/platform/signal/
parser.py:42-55` marks `ref.voice_note` narrowly (`audio/*`), and
`nora/ingest/stages/media` gates transcription on it. The WhatsApp adapter
will need the equivalent media custody (R-21/R-33) + transcription path or
every WhatsApp voice note is either dropped or an undefined interaction.

**Resolution:** Add a media section to the v1 scope or explicitly defer it
with the engagement consequence stated ("voice-note engagement does not work
until media ingestion lands").

Historical review: room-topology migration step 3 supersedes the room-count
convention discussed below; unique labels within a unit are now the invariant.

### M3. DEC-105 is misquoted — the bundle's "one-room-per-platform-per-unit" rule is not what DEC-105 says

**What:** `02:108` claims reuse of `friends` "satisfies DEC-105's one-room-
per-platform-per-unit rule". The decision index defines DEC-105 as
something else entirely.

**Evidence:** `design/09-decision-index.md:232` — "**DEC-105** | **`send_to`:
one room may speak into another, bounded by the unit** (operator,
2026-08-14)." It is the relay/`send_to` bounded-by-unit rule, not a
room-count rule. The "one room per platform per unit" framing lives in
a Nora skill reference (`adding-conversation-to-policy.md`), plausibly a
misattribution that the bundle inherited.

**Resolution:** Either the one-room rule has its own governing decision —
cite that — or drop the DEC-105 citation. Sequencing resting on a misread
rule is exactly the "claim not anchored to file:line" failure class. (The
decision index also cross-references DEC-105 in DEC-129/DEC-134 §(1) as the
unit-scope rule — further confirmation it is not a room-count rule.)

---

## LOW / NIT

### L1. 14-day phone-liveness framing

Reasonable and consistent with WhatsApp multi-device behavior, but it is
operational lore, not a documented guarantee. Label it as such in the
runbook table so nobody reads it as an SLA.

### L2. Healthcheck two-layer story holds

The bundle's "container healthcheck proves process, Nora /health proves
websocket" split matches the repo's own signal-cli lesson exactly
(`docker-compose.yml` comment on the signal-cli healthcheck — "It still
CANNOT prove the websocket to Signal is alive"). Verified correct.

### L3. Compose shape mirrors signal-cli accurately

Private `nora` network, no published ports, `./state/` volume, restart
policy, `network: host` build constraint: all verified against
`docker-compose.yml`. The container anatomy is the soundest part of the
bundle.

---

## VERDICT

**Not ready for implementation as-is.** Two BLOCKING defects (healthcheck
curl in the proposed image; stale text-trigger engagement posture that is
actually disabled in live config) and one HIGH staleness defect (identity
already has `whatsapp: {phone, lid}`; DiscordAdapter already exists) mean
the bundle describes a repo state from before 2026-08. The container
anatomy and security posture are sound and well-precedented — the fixes are
mostly re-syncing the bundle to master + pinning three contracts the
implementation will otherwise guess at (LID resolution, JID normalization,
media/voice-note inbound). Recommended order: B1 → B2 (must), H1 → H4
(should), then re-review before implementation begins.
