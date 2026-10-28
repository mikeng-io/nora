---

> Operational amendment, 2026-10-28: the recreate advice below records the old
> implementation. Valid identity/policy edits now hot-reload within three seconds
> when copied in place; workspace-binding edits still require recreation.
> See [the reload design](../policy-reload/00-design.md).

type: review
title: "WhatsApp Transport Implementation — Independent Audit"
description: "Security/correctness audit of commit cb4f38ae against design/whatsapp-adoption/ (bundle 1febd507..1841f87d) and against the prior delegate review's claimed-fixed findings."
tags: [nora, whatsapp, review, security, implementation]
timestamp: '2026-09-04'
---

# WhatsApp Transport Implementation — Independent Audit

**Date:** 2026-09-04
**Commit reviewed:** `cb4f38ae` ("feat(whatsapp): transport phase — Baileys bridge daemon + platform adapter"), branch `design/whatsapp-adoption`, worktree `nora-wt-whatsapp-adoption`.

**Reviewed:**
- Design baseline: `design/whatsapp-adoption/00-overview.md`, `01-bridge-container.md`, `02-identity-and-policy.md`, `03-transport-contract.md`
- Prior review: `reviews/whatsapp-adoption-delegate-review.md`
- Self-report: `design/whatsapp-adoption/04-transport-implementation-notes.md`
- Bridge: `deploy/whatsapp-bridge/{bridge.js,bridge_helpers.js,Dockerfile,package.json,bridge.test.mjs,.dockerignore}`
- Adapter: `nora/platform/whatsapp/{adapter,jid,parser,listener,send,react,self_id}.py`
- Wiring: `nora/platform/registry.py`, `nora/config.py` (`WhatsAppPlatformConfig`/`PlatformsConfig`), `docker-compose.yml` (`whatsapp-bridge` service), `config.toml`/`config.example.toml`, `policy.example.toml`, `identity.example.toml`, `docs/runbook.md` §4b, `release-fragments/released/V5/whatsapp-transport.json`
- Cross-cutting: `nora/policy.py`, `nora/ingest/pipeline.py`, `nora/ingest/stages/scope.py`, `nora/ingest/stages/media.py`, `nora/ingest/policy.py`, `nora/app/composition.py`, `nora/engagement/stages/admission.py`, `nora/engagement/stages/indicate.py`, `nora/domain/identity.py`, `nora/domain/media.py`
- Tests: `tests/test_whatsapp_*.py`, `tests/test_platform_registry.py`, `tests/contract/test_port_conformance.py`, `tests/contract/test_send_contract.py`, `tests/contract/test_reaction_contract.py`

**Live verification performed** (not just reading): built the bridge image with `docker build --network=host`, ran the unpaired container and curled `/health` (200, `paired:false`), `/self` (503), `/send` (503); inspected the running container's user (`root`) and filesystem; ran `npm audit`; confirmed the pinned Baileys version in the lockfile; ran the full Python test suite and the Node test suite myself (not trusting the self-report's counts) — see §G.

---

## Prior delegate-review findings — verification of "fixed"

All six substantive findings in `reviews/whatsapp-adoption-delegate-review.md` are **verified actually fixed** in the current tree, with evidence:

| Finding | Verified fix |
|---|---|
| B1 (no curl in image) | `deploy/whatsapp-bridge/Dockerfile:7-8` installs curl; **live-verified**: built image's `/health` answers via curl from the host, `curl -fsS` in the compose healthcheck matches. |
| B2 (stale text-trigger framing) | `03-transport-contract.md` §Engagement and `02-identity-and-policy.md` §Engagement both describe the 2026-09-01 voice-note+mention/reply posture; `parser.py`/tests implement mention/reply via structural `mentionedJid` and quote, never a text-trigger. |
| H1 (stale "only SignalAdapter"/"add whatsapp vocabulary" framing) | `registry.py` and `adapter.py` docstrings correctly describe WhatsApp as the **third** adapter; `identity.py:54` already had the vocabulary and nothing in this change re-adds it. |
| H2 (LID handling omitted) | `bridge_helpers.js` builds LID→phone **and** phone→LID maps from session files; `jid.py`/`parser.py` carry both forms through `sender_id`, mentions, and quote author; `test_whatsapp_parser.py` exercises LID-only senders/mentions/quotes explicitly. |
| H3 ("restart" vs "recreate") | `docs/runbook.md:351-353` and `02-identity-and-policy.md:58-60` both say recreate, citing `nora/obs/health.py:14-21`. |
| H4 (JID format unpinned) | `03-transport-contract.md` §Normalization pins group=`<id>@g.us` verbatim, 1:1=`<phone>@s.whatsapp.net`/raw `@lid`; `jid.py::conversation_id_for` implements it exactly, tested in `test_whatsapp_parser.py`. |
| M1 (Hermes silent queue) | `bridge_helpers.js::createEventBuffer` drops oldest + counts, never silent; `dropped_events` on `/health`, one-shot `{"type":"dropped"}` SSE event, `listener.py` logs it at WARNING. Tested in `bridge.test.mjs` and `test_whatsapp_listener.py`. |
| M2 (inbound media unaddressed) | `parser.py::_media_ref` builds a `MediaRef` for every media entry (never dropped) — but see **H-1** below: the "voice-note engagement gap" this finding asked to be stated explicitly IS stated in three places, and is nonetheless not what the wired code does. |
| M3 (DEC-105 misquote) | `02-identity-and-policy.md:66-67,162-164` now explicitly say DEC-105 is the `send_to` relay rule, "not a room-count rule — do not cite it as one." |

Eight of nine needed no re-reporting. M2's documentation half is fixed; its underlying behavioral claim is not — see H-1.

---

## HIGH

### H-1. WhatsApp voice notes DO trigger a full engagement turn today — contradicting the transport contract, `design/whatsapp-adoption/04-transport-implementation-notes.md`, and the release fragment, all three of which state they don't

**What:** `03-transport-contract.md` §Engagement, `design/whatsapp-adoption/04-transport-implementation-notes.md` ("Voice notes are carried to the ambient tier ... but cannot engage until media custody + transcription lands — stated, not silent"), and `release-fragments/released/V5/whatsapp-transport.json` ("voice-note engagement does not trigger yet") all assert that a WhatsApp voice note is audited but does not engage Nora. This is **not what the wired code does.**

`nora/ingest/policy.py::VoiceNotePolicy.should_engage` — appended unconditionally to `engage_policies` for every platform in `nora/app/composition.py:1120-1124` ("Any voice note engages her, independent of trigger text ... it fires on any note regardless") — engages on `any(ref.voice_note for ref in message.media)` **with no platform check and no dependency on whether a transcript exists**. Its own docstring is explicit that this is deliberate: "It is gated on the parser's `voice_note` flag ... NOT on the rendered body containing a transcript: a failed transcription still leaves the handle and the note is still content she should see." This is an operator directive dated 2026-08-26 (`Miles: 「總之任何 Voice Note 都會直入上 Nora 度先囉」` — "ANY voice note goes straight to Nora first") — a pre-existing, deliberately platform-agnostic policy that predates this WhatsApp bundle and that the bundle's authors evidently did not check against.

`nora/platform/whatsapp/parser.py::_media_ref` sets `voice_note=bool(entry.get("voice_note"))` for any WhatsApp `ptt` audio message — exactly the flag `VoiceNotePolicy` reads. WhatsApp has no `MediaSource` registered this phase (`design/whatsapp-adoption/04-transport-implementation-notes.md`'s own "BLOCKED / not done" section confirms this), so `nora/ingest/stages/media.py`'s custody stage never fetches bytes and never transcribes (transcription requires bytes already fetched — `media.py`'s own docstring: transcription runs "when present AND a ref is a voice note" off the custody result) — the message body stays the parser's `"[voice note]"` placeholder, permanently. But nothing stops the message from reaching the gate: `gate_stage.run` calls the same shared `CompositePolicy` for every platform, and `VoiceNotePolicy` fires on the flag alone, with **zero transcript content**.

**Net effect:** the moment `[platforms.whatsapp] enabled = true` and a WhatsApp room is admitted, the first voice note anyone sends will make Nora attempt a real reply having seen only the literal string `"[voice note]"` in her context — not "no engagement," but engagement on no information at all, which is arguably worse than the documented "does not work" state (a confusing or fabricated-sounding reply to a message she never heard, versus visibly nothing happening).

**Evidence:**
- `nora/app/composition.py:1120-1124` — unconditional `engage_policies.append(VoiceNotePolicy())`, comment: "it fires on any note regardless."
- `nora/ingest/policy.py:97-117` — `VoiceNotePolicy`, no platform gate; docstring states firing even on a failed transcription is intentional.
- `nora/platform/discord/parser.py:84` sets the same `voice_note` flag for Discord — this is not new machinery this diff introduced, it is a pre-existing cross-platform policy the WhatsApp bundle plugged straight into without checking its actual firing condition.
- `tests/test_whatsapp_parser.py::test_a_voice_note_only_turn_survives_as_a_placeholder_body` (line 205) only asserts the parser's `MediaRef`/placeholder output — **no test in this diff exercises `VoiceNotePolicy`/`should_engage` against a WhatsApp message**, so the "does not engage" claim was never actually verified end-to-end, only asserted in prose three times (contract doc, implementation notes, release fragment).

**Resolution:** Either (a) gate `VoiceNotePolicy` (or wrap it) so it only fires for a platform whose `MediaSourceRegistry` actually has a fetcher/transcriber wired — the cleanest fix, and it makes the documented behavior true — or (b) if firing on a contentless placeholder is judged acceptable for v1, correct the three places that currently claim otherwise so the shipped behavior and the shipped documentation agree. Given the policy's own stated precondition ("the transcription is conversation content in the body by the time the gate runs," true for Signal/Discord today), option (a) is more consistent with the policy's own intent than leaving WhatsApp as the one platform where that precondition silently doesn't hold. This should be fixed (or the docs corrected) before this is called a complete, self-consistent transport phase — it is not a security leak and the feature ships `enabled = false`, so it is not BLOCKING, but it is a genuine, verified contradiction between three explicit written claims and the actual runtime path.

## MEDIUM

### M-1. `whatsapp-bridge` container runs as root — the one service in this compose file without a dropped-privilege `USER`

**What:** `deploy/whatsapp-bridge/Dockerfile` never sets `USER`, so the bridge process — which holds the full Baileys multi-device session (encryption keys, device creds) for a live WhatsApp account — runs as root inside its container.

**Evidence:** Built the image and ran it:
```
$ docker exec whatsapp-bridge-review sh -c "id"
uid=0(root) gid=0(root) groups=0(root)
```
`node:20-slim` already ships a non-root `node` user (confirmed in the running container's `/etc/passwd`: `node:x:1000:1000::/home/node:/bin/bash`) but the Dockerfile never switches to it. Every other Dockerfile in this repo drops privileges explicitly:
```
$ grep -n "^USER" Dockerfile deploy/signal-cli/Dockerfile deploy/whatsapp-bridge/Dockerfile deploy/drop/Dockerfile
Dockerfile:52:USER nora
deploy/signal-cli/Dockerfile:40:USER 1000:1000
deploy/drop/Dockerfile:29:USER drop
(whatsapp-bridge/Dockerfile: no match)
```
`docker-compose.yml`'s `whatsapp-bridge` service also has no `user:` override.

**Resolution:** Add `USER node` (or `USER 1000:1000`, matching signal-cli's shape) after `npm ci`, and `chown` `/app` accordingly before the switch (the bind-mounted `/app/session` volume will need to be writable by that uid too — `./state/whatsapp` on the host should be `chown 1000:1000`). This is a real, if narrow, deviation from the repo's own defense-in-depth convention on the one container whose entire purpose is holding a third-party credential; Docker's container boundary is still the primary control, so this does not rise to BLOCKING, but it should not ship as-is beside three sibling Dockerfiles that all got this right.

### M-2. `react()` does not enforce the 1:1 deny-by-default posture that `send()` explicitly implements

**What:** `nora/platform/whatsapp/send.py:115-118` explicitly refuses to send to a non-group target ("Deny-by-default this phase, exactly like Signal v2 and Discord v1"). `nora/platform/whatsapp/react.py::WhatsAppReactor.react` has **no equivalent check** — it will happily place (and later retract/replace) a reaction on any target it is given, 1:1 or group. `nora/engagement/stages/indicate.py::begin`/`resolve` call `adapter.react()` unconditionally for every tag, with no `conversation_type` gate of their own either.

**Why this is reachable on WhatsApp specifically, and wasn't before:** Signal's parser drops every non-group message before it reaches the ambient/engagement pipeline at all (`nora/app/composition.py:687`'s comment: "the Signal parser still drops every non-group message"), and Discord v1 excludes DMs the same way — so `indicate.begin()`/`react()` were never actually invoked against a 1:1 target for either existing platform. `nora/platform/whatsapp/parser.py` **does not drop 1:1 messages** — this is deliberate (`00-overview.md`: "The transport must carry 1:1 and group traffic alike"). So if an operator ever adds a `[[conversations]] platform="whatsapp" id="...@s.whatsapp.net"` entry to `policy.toml` — against the explicit advice in `policy.example.toml:90-92`, but not blocked by any code — a mention/reply tag in that 1:1 chat would still get a 👀 progress reaction and then a ⚠️ ("gave up") resolution reaction (since `send()` refuses the actual text reply), even though the design's stated intent is that 1:1 stays fully deny-by-default until the DM study lands.

**Evidence:** `nora/platform/whatsapp/react.py:43-88` (no `target.conversation_type` check anywhere in `react()`); compare `nora/platform/whatsapp/send.py:115-118` which has exactly this check. `tests/test_discord_send.py::test_send_refuses_a_non_group_conversation_type` exists for Discord's `send()`; no equivalent `test_react_refuses_a_non_group_conversation_type` exists for any adapter.

**Impact:** Content never leaks (no reply text, no message body in the reaction), but a 1:1 sender in a misconfigured deployment would see visible evidence Nora "read" and "gave up" on their DM — a small but real inconsistency with the stated all-or-nothing 1:1 posture, and specifically a WhatsApp-introduced exposure of a pattern that was previously unreachable by construction.

**Resolution:** Either add the same `conversation_type != "group"` guard to `WhatsAppReactor.react()` (cheapest, mirrors `send.py`), or gate `indicate.begin()` itself on `conversation_type == "group"` for platforms that carry 1:1 traffic (more central, protects any future adapter with the same shape). Low cost either way; recommend doing it before any operator could plausibly admit a WhatsApp 1:1 room.

## LOW

### L-1. `composition.py`'s `dm_persons` warning is now scoped incorrectly

**What:** `nora/app/composition.py:685-690` warns "policy.toml lists N DM principal(s), but **the Signal parser** still drops every non-group message — this section does nothing yet." `dm_persons` is in fact never consulted by `Policy.is_allowed`/`scope_stage` for **any** platform (confirmed by reading `nora/policy.py` and `nora/ingest/stages/scope.py` in full — the room gate is decided purely by `(platform, conversation_id)` membership), so the warning's *conclusion* ("this section does nothing yet") still holds. But its stated *reason* is now WhatsApp-specific-wrong: WhatsApp's parser does not drop 1:1 messages the way Signal's does, so an operator reading this warning after enabling WhatsApp could reasonably (and incorrectly) infer that `dm_persons` governs WhatsApp DMs once WhatsApp is on, when in fact the mechanism that actually keeps a WhatsApp 1:1 inert is the ordinary room-scope gate (§M-2 discusses the one place that gate's effect isn't fully mirrored).

**Resolution:** Reword the warning to state the platform-agnostic fact ("`dm_persons` is validated but not yet wired into any admission check") rather than naming Signal's parser as the specific reason — a one-line comment fix, no behavior change.

### L-2. `conversation_id_for` reuses `lid_form()` to normalize a phone number

**What:** `nora/platform/whatsapp/jid.py:108-123`'s `conversation_id_for`, for a resolved 1:1 phone, builds the id as `f"{lid_form(resolved_phone)}@{PHONE_SERVER}"` — i.e. it calls the function whose own docstring says "A LID is NOT a phone number and never gets a `+`: conflating the two would let one account's LID match another's phone" on a value that is, at that call site, a phone number, purely to strip a stray `+`/whitespace (`lid_form` is `digits.strip().lstrip("+")`, which happens to do exactly that). Functionally correct — `resolved_phone` here is always digit-string input from `sender_phone`, so the result is right — but it is the exact naming confusion the module's own docstring warns against three lines above the function.

**Resolution:** NIT-adjacent, but worth a one-line fix: a local `_digits(value)` helper (or reusing `phone_form(...).removeprefix("+")`) instead of calling `lid_form` on a phone value.

## NIT

### N-1. Session directory permissions rely on operator discipline, not enforcement

`01-bridge-container.md` and the compose comments say "chmod 700 on the directory; never commit," but nothing in the Dockerfile, compose file, or bridge code enforces this — combined with M-1 (root inside the container), files Baileys writes under `/app/session` will land on the host owned by root with whatever default mode `mkdirSync`/Baileys itself picks (not `0700`). Not a new gap introduced by this change (`./state/signal-cli` has the identical posture, verified: no chmod anywhere in `deploy/signal-cli/`), so this is inherited convention rather than a regression — flagged for completeness given M-1 makes root ownership concrete rather than hypothetical.

---

## G. Test commands run and actual output

```
$ .venv/bin/pytest tests/ -q -k whatsapp
73 passed, 6472 deselected, 1 warning in 2.23s

$ .venv/bin/pytest tests/contract/ -q
976 passed, 2 skipped, 21 warnings in 69.05s

$ .venv/bin/pytest tests/test_factory.py::test_r29_survives_activation_only_the_operator_dir_loads -q
FAILED tests/test_factory.py::test_r29_survives_activation_only_the_operator_dir_loads
1 failed, 1 warning in 0.14s
  AssertionError: assert ['navigation', ...'personalization'] == ['navigation']
```
Confirms `design/whatsapp-adoption/04-transport-implementation-notes.md`'s claim that this failure is pre-existing and unrelated to WhatsApp (it is about `AgentSkills`/skill-loading isolation — nothing this commit touches). The failure surfaced again identically in the full-suite run below.

```
$ cd deploy/whatsapp-bridge && node --test
# tests 25
# pass 25
# fail 0
```

```
$ uv run ruff check nora/platform/whatsapp/ tests/test_whatsapp_*.py
All checks passed!

$ uv run mypy nora/platform/whatsapp/ nora/config.py
Success: no issues found in 9 source files
```
Note: `nora/platform` is **not** in the gate's own scoped mypy invocation (`uv run mypy nora/domain nora/config.py nora/app nora/infra nora/presence nora/engagement` — no `nora/platform`), so this module was never going to fail `make gate`'s mypy step either way; I ran mypy against it anyway as an extra check and it is clean regardless.

```
$ uv run lint-imports
Contracts: 6 kept, 0 broken.
```

```
$ docker build --network=host -q -t whatsapp-bridge:test-review deploy/whatsapp-bridge
(succeeded)
$ docker run --rm -d -p 127.0.0.1:38081:18081 whatsapp-bridge:test-review
$ curl http://127.0.0.1:38081/health
{"ok":true,"connected":false,"paired":false,"dropped_events":0,"buffered_events":0,"uptime_seconds":1}
$ curl -o /dev/null -w "%{http_code}" http://127.0.0.1:38081/self
503
$ curl -o /dev/null -w "%{http_code}" -X POST .../send -d '{...}'
503
```
All match the contract exactly (unpaired bridge: `/health` 200 with `paired:false`, `/self` and `/send` both 503).

```
$ cd deploy/whatsapp-bridge && npm audit --omit=dev
found 0 vulnerabilities
```

```
$ uv run pytest -q   (full untargeted suite, ran to completion)
FAILED tests/test_factory.py::test_r29_survives_activation_only_the_operator_dir_loads
1 failed, 6525 passed, 2 skipped, 16 deselected, 1 xfailed, 62 warnings in 413.19s (0:06:53)
```
The full suite is green except for the same pre-existing `test_r29_survives_activation_only_the_operator_dir_loads` failure, confirmed identical to the targeted run above — no other regressions anywhere in the ~6,500-test suite.

**Test counts against `design/whatsapp-adoption/04-transport-implementation-notes.md`'s claims:** "73 whatsapp tests" ✅ exact match. "976 contract tests" ✅ exact match (2 skipped, consistent with pre-existing skips unrelated to this change). "25 node tests" ✅ exact match. "One pre-existing failure (test_factory R-29)" ✅ confirmed failing here (both targeted and full-suite runs), and the failure content is unrelated to any file this commit touches. "`make gate` clean" — ruff/mypy/lint-imports scoped checks all clean, and the full pytest suite is green modulo the disclosed pre-existing failure; this substantiates the self-report's claim.

---

## VERDICT

**Not quite a clean merge — one HIGH finding (H-1) should be resolved or the documentation corrected first; the two MEDIUM findings are cheap and worth doing in the same change. Nothing here is BLOCKING.**

What must change (or be explicitly re-decided) before this is genuinely done, in priority order:

1. **H-1** — `VoiceNotePolicy` fires on any WhatsApp voice note today, with zero transcript content, directly contradicting three explicit written claims (the transport contract, `design/whatsapp-adoption/04-transport-implementation-notes.md`, the release fragment) that it doesn't. Either gate it per-platform on `MediaSourceRegistry` availability, or correct the documentation to say what actually happens. Not BLOCKING — the feature ships `enabled = false` and this leaks no data — but it is a genuine, verified gap between the stated and actual contract that should not ride into master unexamined.
2. **M-1** — add `USER node` to `deploy/whatsapp-bridge/Dockerfile`, matching every other container in this compose project.
3. **M-2** — add the same `conversation_type != "group"` guard to `WhatsAppReactor.react()` that `WhatsAppSender.send()` already has.

Everything else holds. Every BLOCKING/HIGH finding from the prior delegate review is verified genuinely fixed with live evidence, not just doc claims — JID/LID normalization, drop-and-count buffering, fail-fast on an unpaired bridge (verified live: unpaired container serves `/health` 200 / `paired:false`, `/self` and `/send` both 503, exactly per contract), and the room/sender fail-closed gates (`scope_stage` runs before any audit or Honcho write, for every platform, confirmed by reading the pipeline in full) all hold up under direct inspection and live testing, not just the self-report's word. The full test suite (6,525 passed, 1 pre-existing unrelated failure, 413s) and the bridge's own Node suite (25/25) both ran clean. L-1 and L-2 are one-line comment/naming cleanups, safe to fold into this change or a fast follow-up.
