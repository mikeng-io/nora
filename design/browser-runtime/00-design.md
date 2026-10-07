---
type: design
title: "Nora's browser — headed Chrome in the computer, a tiered driver, and a vault she never reads"
description: "Not built. Extends design/sandbox-runtime §14 (S4). Real Google Chrome runs headed on Xvfb inside the sbx computer; a sub-agent outside the VM drives it over a CDP proxy through computerd, through four tiers (site adapter, cached script, small decision model, DeepSeek Flash with vision) and a human hand-off over noVNC. Credentials live in OpenBao and reach a page only through a fill broker, bound to an origin; the model holds handles. The portal can watch the screen read-only. Proposes ADR 0022 (number taken at merge). Research from 2026-10-06, much of it from search snippets and marked so."
tags: [nora, compute, sandbox, sbx, browser, computer-use, credentials, openbao, vault, cdp]
timestamp: '2026-10-07'
status: proposed
---

# Nora's browser

Status (2026-10-07): **proposed, not built.** This bundle is the design for
S4 of `design/sandbox-runtime/00-design.md` (§14) plus the credential store
that S4 needs and the sandbox design deferred (§8, decision 3). It is written
from an operator conversation on 2026-10-06/07 and three research passes
(`02-research.md`). The decision it asks for is
`docs/adr/0022-noras-browser-is-headed-chrome-with-a-vault-fill-broker.md`
(proposed; the number is re-taken from origin/master at merge).

Files:

| file | holds |
|---|---|
| `00-design.md` | this: the shape, the tiers, display and streaming, the sequence |
| `01-vault.md` | OpenBao, the credential record, the fill broker, redaction |
| `02-research.md` | how Hermes, pi/OpenClaw, CUA and others do it; decision models; GPU |
| `03-open-questions.md` | what the operator still has to rule on, and what to measure |

## 0. What exists today (read 2026-10-06)

- **The computer** is one persistent sbx microVM, `nora-computer`, with
  computerd as its only API and Nora as its only client (ADR 0019, DEC-243).
  Per-room folders `/workspace/<scope>` and a per-room system user.
  3 GB / 2 CPUs. Egress is deny-all plus PyPI and the Ubuntu archive
  (DEC-260). Image: `deploy/computer/Dockerfile`.
- **No browser in it.** The only Chromium is Puppeteer's, for mermaid-cli.
  No Xvfb, noVNC, Playwright or CDP proxy. The model's tools are
  `computer_run/read/write/edit/export/import/session_list`
  (`nora/agent/tools.py`); `computer_browse` is not built.
- **The one live browser** is on the host: headed Chrome on TigerVNC `:2`
  with the X/LinkedIn/Instagram logins, driven by OpenCLI through its
  Browser Bridge extension, behind `nora/infra/social/bridge_server.py`'s
  read-only allowlist (DEC-201, R-86). `nora/infra/social/session_probe/`
  reads that Chrome's cookie file to watch the sessions.
- **No credential store.** Secrets live in the host `.env` and reach services
  key by key through `docker-compose.yml`. sbx's `secret set-custom`
  (placeholder substituted by the host proxy) is designed but unused (§8).
- **Decision 3 (2026-10-01): no browser logins for now.** This bundle asks
  for it to be superseded (ADR 0022).

## 1. Operator direction (2026-10-06/07)

- Build browser use next, inside the sandbox.
- It must not be blocked by anti-bot systems on every visit: install real
  Chrome; decide headless vs a virtual display.
- Nora may log in to real accounts, including a Google account prepared for
  her.
- OpenCLI should be able to move to that browser in time.
- Faster and cheaper than browser-use; the present S4 plan felt primitive, so
  survey what others (Hermes Agent, pi/OpenClaw, CUA) do.
- Credentials cannot scale through a file or env vars: a vault. **OpenBao**
  preferred over HashiCorp Vault.
- DeepSeek v4.1 Flash supports vision, so the model itself may read a
  screenshot, besides a vision tool.
- A small decision model (Jev-like: Laya, Tev1, Strands Decider 2B or any
  other) may pick steps; fitness first, speed second. An RTX A3000-class GPU
  may be added.
- The portal should be able to stream Nora's screen, view-only.

## 2. The shape

```text
 Nora container (trusted)                       nora-computer VM (sbx)
 ┌───────────────────────────────┐   CDP over   ┌──────────────────────────────┐
 │ main loop (DeepSeek Flash)    │   computerd  │ Xvfb :99 ── Google Chrome    │
 │   computer_browse(objective)  │◀────────────▶│   profile per scope / login  │
 │ browse sub-agent (Strands)    │   proxy      │ x11vnc → noVNC (view/take)   │
 │   tier 1 adapter              │              │ computerd: CDP proxy,        │
 │   tier 2 cached script        │              │   screencast relay           │
 │   tier 3 decision model ──┐   │              └──────────────────────────────┘
 │   tier 4 Flash + vision   │   │
 │ fill broker ──── OpenBao  │   │   host: OpenBao (transit, TOTP), GPU server
 └───────────────────────────┼───┘   for the decision model (llama.cpp / vLLM)
                             └── localhost / OmniRoute; page content stays on norty
```

The sandbox design's rule holds: **the model loop is outside the VM**, so no
model key and no vault token enters it. What enters the VM is page traffic,
and, at the moment of a fill, the one credential value the page needs.

## 3. Chrome: headed on Xvfb, real Google Chrome

- **Headed on a virtual display, not `--headless`.** `--headless=new` is the
  real engine but carries fingerprint differences (screen and window metrics,
  GPU stack, some API surfaces). More decisive: a display is what makes a
  human hand-off (§6) and a stream (§7) possible. Xvfb costs tens of MB; S1
  measures the whole.
- **Google Chrome stable, not Chromium,** for the fingerprint closest to a
  real user. Linux Chrome is x86-64 only; if norty is ARM, Chromium and the
  difference is recorded. Pin the version in the Dockerfile and update it on
  a schedule, since a stale Chrome is itself a signal.
- **What actually decides blocks, in order:**
  1. **Exit IP reputation.** norty's IP. A residential IP is the largest
     single factor; a datacenter exit is the one we cannot fix in code.
  2. **CDP automation leaks** (`Runtime.enable`, `navigator.webdriver`).
     Every CDP driver has them; Patchright and nodriver patch them. The
     extension-based control OpenCLI uses avoids the class.
  3. **The sbx egress proxy.** If it terminates TLS for general traffic, the
     site sees the proxy's TLS fingerprint, not Chrome's: a strong bot signal.
     **Unknown; S1 measures it** against a TLS fingerprint echo service.
  4. **No GPU in the VM:** WebGL reports a software renderer, headed or not.
  5. Behaviour (timing, mouse paths); least important for our volume.
- **Not every block is avoidable.** Turnstile, hCaptcha and DataDome will
  still challenge. The answer is the hand-off (§6), not a solver service.
- **An anti-detect backend is an option, not the default:** Camoufox
  (Firefox, Juggler, not CDP) as a second engine for sites that block Chrome,
  measured as a spike arm (`03-open-questions.md`).
- **Egress.** Browsing needs the public web. That is a new allowance on the
  VM that also holds every room's files, so it is a decision (ADR 0022 §2):
  the browser's traffic goes through sbx's policy with a deny list for
  private ranges, Nora's own services and the host, and the network half of
  the audit stays `sbx policy log`.

## 4. Where logged-in browsers live

Two trust domains, because a logged-in profile is a credential store and the
VM has a shell the model drives:

| domain | what | where |
|---|---|---|
| **anonymous** | per-scope profiles, no logins, wiped freely | `nora-computer` VM, `/workspace/<scope>/browser/<profile>`, owned by the scope's user |
| **logged in** | Nora's own accounts (Google, later X/Instagram/LinkedIn via OpenCLI) | a second sbx sandbox, `nora-browser`, which no `computer_run` shell can reach; only computerd's CDP proxy and the broker talk to it |

- The OpenCLI daemon listens on localhost with no auth. Put it in a VM that a
  room's shell shares and that shell can call it, bypass the bridge's
  allowlist and post as the account. **So OpenCLI moves last, and only into
  `nora-browser`**, with `bridge_server.py` still the one door. Until then
  the host social Chrome stays as it is, and `session_probe` keeps reading it.
- Profiles are never in the nightly tar backup (sandbox §7). A logged-in
  profile's cookies may be snapshotted encrypted through OpenBao transit
  (`01-vault.md` §5), so a recreate is a restore, not a re-login.

## 5. The driver: `computer_browse` and four tiers

The unit stays coarse (sandbox §14): one `computer_browse(objective,
session?)` call from Nora, one typed result back (`ok` / `nothing_found` /
`unavailable`, R-27), whatever happens inside. Inside, the sub-agent climbs a
ladder and pays model calls only where the cheaper tier failed:

1. **Site adapter, no model.** Deterministic code per site and task, the way
   OpenCLI's X and LinkedIn adapters are. For flows Nora runs constantly.
2. **Cached script, no model.** A successful run is compiled into a replay
   keyed by `(origin, task signature)`. Steps store role, accessible name and
   a selector, never the `@eN` ref (refs change per load). On replay a step
   that fails calls tier 3/4 for that step only, and the script is updated
   (self-healing, Stagehand's pattern; Agent Workflow Memory's reuse). The
   first run of a flow pays; later runs do not.
3. **Decision model per step.** A small local model receives the goal, a
   pre-ranked candidate list and the last result, and returns one typed
   action with a confidence (§5.1).
4. **DeepSeek v4.1 Flash, with vision.** For planning and judgement (compare
   prices, what is "best", an injection-shaped page), when tier 3 escalates,
   or fails twice on a step.
5. **A human** (§6), for CAPTCHA, 2FA, consent and anything ambiguous.

What the model sees is the **accessibility snapshot with `@eN` refs**
(agent-browser), diffed between steps. Extraction is DOM/JS, not the model.
Nora's own script tier (a Playwright script through `computer_run`) stays
for the anonymous VM.

### 5.1 The step-picker contract (any decision model)

The slot is model-agnostic. A candidate qualifies on fitness first, then
speed (`02-research.md` §4):

- **In:** objective; the pre-ranked candidates (ref, role, name, value
  redacted, a little context); the last action and its result; steps left;
  optionally a Set-of-Marks screenshot.
- **Out, exactly one of:** `click @e`, `type @e "text"`, `select @e
  "option"`, `scroll dir`, `press key`, `fill_credential cred:<handle> @e`,
  `done(result)`, `escalate(reason)`; plus a confidence.
- **Fail closed:** output that does not parse, or a ref not in the
  candidates, is a failed step; below the confidence floor is `escalate`.
  Never a guess.
- **The picker names a credential handle, never a value.** The broker
  (`01-vault.md`) fills.
- **Pre-ranker in front.** Every small decision model sees about 20–26
  options or 3–4k tokens; a real page has 140–580 interactive elements.
  First filter (visible, in viewport, interactive role), then rank (a small
  text ranker in the MindAct style, CPU-cheap) to the top K.

### 5.2 Vision

- The snapshot is the default; a screenshot is a tool the sub-agent calls,
  or attaches when the snapshot is ambiguous, the page is canvas/map-heavy,
  or a check needs eyes.
- **Set-of-Marks:** each `@eN` drawn as a numbered box on the screenshot, so
  a vision answer is a ref, not a pixel coordinate. A coordinate-native model
  (Holo, Fara, UI-TARS) gets an adapter that maps a click to the mark under it.
- DeepSeek v4.1 Flash reads it directly, if OmniRoute and the provider pass
  images through: **verify before building on it** (the configured model is
  `ollamacloud/deepseek-v4-flash:0731`).

## 6. Human hand-off

- The sub-agent returns `unavailable` with reason `needs_human` and a portal
  link; Nora tells the room or the operator.
- The operator opens noVNC through the portal in **interactive** mode on the
  right sandbox and display, solves the challenge or signs in, and releases.
- First sign-in to every account is by hand this way: Google refuses sign-in
  from a browser it reads as automated. Chrome is launched normally on Xvfb;
  automation attaches afterwards.

## 7. Streaming the screen to the portal

Two paths, both built eventually:

| path | what | for |
|---|---|---|
| **CDP screencast** (`Page.startScreencast`) | JPEG frames on change, relayed computerd → Nora → portal websocket; works headless too; one tab | the view-only "watch Nora browse" panel |
| **noVNC over x11vnc** | the whole Xvfb display, view-only or interactive | the hand-off (§6) |

- Access: portal sign-in, limited to the room's owner and the operator; it
  shows logged-in pages.
- **The stream blurs or pauses while the broker fills** a credential field.
- Recording (rrweb or frames) for audit is a separate decision: storage, and
  recordings hold personal data.

## 8. Sequence

| step | what | gate |
|---|---|---|
| **S1** | Chrome + Xvfb in the image; RSS at 1/3/5 tabs; TLS fingerprint through the sbx proxy; IP reputation of norty's exit | numbers recorded here |
| **B0** | ADR 0022 accepted; egress policy for browsing | operator |
| **B1** | CDP proxy in computerd, scoped per room; screencast relay | tests: a scope reaches only its own browser |
| **B2** | `computer_browse` + sub-agent, tiers 4 and 5 only (Flash, hand-off) | nora-tool routing measurement |
| **B3** | spike (`03-open-questions.md` §2): decision-model arms, Camoufox arm | measured, fail-closed |
| **B4** | OpenBao + fill broker + redaction (`01-vault.md`); `nora-browser` sandbox; Nora's Google account | a credential reaches only its bound origin; zero plaintext in traces |
| **B5** | tier 2 cache + tier 3 picker as chosen by B3 | second-run cost |
| **B6** | portal stream view-only, then noVNC hand-off | access test |
| **B7** | OpenCLI into `nora-browser` behind the bridge; retire host social Chrome | R-86 still holds at three layers |
