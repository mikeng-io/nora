# 0022 · Nora's browser is headed Chrome in sbx, driven in tiers, with credentials only through a vault fill broker

- **Status:** proposed (2026-10-07)
- **Date:** 2026-10-07
- **Extends:** [0019](0019-noras-computer-runs-in-an-sbx-microvm.md) and
  `design/sandbox-runtime/00-design.md` §14 (S4, the browser), which this
  makes concrete.
- **Supersedes, in part:** sandbox-runtime decision 3 (2026-10-01, "no
  browser logins are allowed for now"), for Nora's own accounts in a
  separate logged-in sandbox only.
- **Design:** `design/browser-runtime/` (00 shape, 01 vault, 02 research,
  03 open questions).
- **Index:** a DEC number is taken at merge, when a behaviour lands. This ADR
  number is re-taken from origin/master at merge as well.

## Context

S4 has been designed since 2026-09-30 and not built: no browser in the
computer image, no CDP proxy in computerd, no `computer_browse`. The one
live browser is the host's headed social Chrome, driven read-only by OpenCLI
behind `nora/infra/social/bridge_server.py` (DEC-201, R-86). No credential
store exists; secrets are lines in the host `.env`.

The operator asked (2026-10-06/07) for browsing inside the sandbox that is
not blocked by anti-bot systems on every visit, real logins including a
Google account prepared for Nora, a path for OpenCLI into that browser, a
driver faster and cheaper than browser-use, a vault for many credentials
(OpenBao), vision from DeepSeek v4.1 Flash, a small decision model for
steps, and a view-only stream to the portal.

The research (`design/browser-runtime/02-research.md`, 2026-10-06,
re-verified against primary pages 2026-10-07, each claim marked) and a
read of norty (`00-design.md` §0, 2026-10-07) found:

- **The core of S4 is what the field does.** Hermes Agent offers
  agent-browser-style `@eN` snapshots (its default is a Browser Use CLI
  tool); pi drives CDP from small scripts; OpenClaw drives Playwright behind
  a loopback service. None is ahead on the core loop.
- **Where they are ahead is around it:** a password-blind credential fill,
  action caching (Stagehand, "up to 2x faster" on repeats), a headed
  mode for a human hand-off, and a vision fallback.
- **The failure to design against is Hermes's:** its vault filled a
  password the model never saw, and its default `browser_exec` tool, the
  one path its redactor did not cover, read it back out of the page into
  the state database (issue #112693, fixed by #113261). Redaction per tool
  is not a boundary.
- **Blocks are decided mostly by exit IP and CDP leaks,** not headless mode.
  The sbx proxy tunnels TLS for a plain host rule (two hosts read), so
  Chrome's own fingerprint should reach the site; S1 confirms it.
- **sbx cannot express "the web minus private ranges":** a CIDR deny is not
  checked against an allowed hostname's resolved address (its own help
  text), and Ollama, Langfuse, Neo4j, Prometheus, OmniRoute and the social
  bridge listen where a rebinding name would land.
- **Small decision models** now exist that return a choice and a confidence
  in one pass (Strands Decider 2B, Tev1, Laya, cua-s1; Jev is hosted only).
  Each sees at most 10–26 options or a 4k-token context; a page has
  hundreds of elements, so a pre-ranker goes in front.
- **norty is a mini PC** (Ryzen 7 PRO 6850U, x86-64, 27 GiB, no internal GPU
  slot), so Google Chrome installs, and a GPU means an external enclosure.

## Decision

1. **Real Google Chrome, headed on Xvfb, inside sbx.** Not `--headless`.
   noVNC and a CDP screencast make the display watchable and takeable.
2. **Browsing egress is allowed only where it is fenced.** The logged-in
   sandbox reaches only its accounts' hosts. The anonymous sandbox reaches
   the public web only once S1 shows that a name resolving to a private or
   loopback address is refused, or a host-side fence makes it so. The
   exact policy is the B0 ruling (`03-open-questions.md` §1).
3. **Credentials live in OpenBao and reach a page only through the fill
   broker.** Transit encrypts credential records stored in Postgres, TOTP
   generates 2FA codes, and the broker (outside the VM) fills by CDP after
   checking scope, the target frame's top-level origin and approval. Models
   hold handles only. Every path that reads out of the browser (snapshot,
   eval, console, network, cookies, screenshot, stream, traces, rows)
   passes a scrubber, and eval is disabled on a page holding a filled field.
   A canary credential test over every read path gates the build.
4. **Two trust domains.** Anonymous per-room profiles stay in
   `nora-computer`. Logged-in profiles (Nora's own Google account first,
   OpenCLI's later) live in a second sandbox, `nora-browser`, which no
   `computer_run` shell reaches. OpenCLI moves there last, still behind the
   bridge's allowlist.
5. **`computer_browse` climbs four tiers and a human:** site adapter →
   cached script (self-healing) → a pluggable small decision model behind a
   pre-ranker, fail-closed on a bad ref or low confidence → DeepSeek v4.1
   Flash with vision (Set-of-Marks, refs not pixels) → `needs_human` over
   noVNC. Which decision model fills the slot is the B3 spike's to measure,
   not this ADR's.
6. **The portal may stream the screen view-only** to the room's owner and
   the operator, paused during a fill.

## Consequences

**Better:**

- Nora can use the web, including her own logged-in accounts, without a
  model ever holding a password.
- Cost falls with use: a flow's first run pays model calls, later runs
  replay.
- A human can watch and step in, which is the honest answer to CAPTCHAs.
- One credential store, audited per decrypt, replaces adding lines to `.env`.

**Worse:**

- **Public-web egress from a sandbox** is new attack surface: page content is
  untrusted input to a model with tools, and the VM can now reach the
  internet.
- **A second VM** (`nora-browser`) costs memory on a host the design notes has
  15 GiB available (2026-10-07), plus its own lifecycle and backup story.
- **OpenBao is new infrastructure to run:** unseal after every reboot (fills
  are `unavailable` until the operator unseals), upgrades, its own audit log.
- **Credential values do enter the VM** at fill time, inside the page; a
  compromised page or VM sees what it is given. Origin binding limits which.
- **More moving parts in one tool:** the cache, the ranker, a local model
  server (likely a GPU), and escalation logic all have to stay measured, or
  the tiers rot into an unmeasured guess.
- **Anti-bot is mitigated, not solved;** an IP or proxy fingerprint we cannot
  change may still block sites.

## Rejected

- **Headless only:** cheaper, but no hand-off, no stream, and a more
  fingerprintable browser.
- **Hosted computer-use APIs, Jev, Browserbase, Browser Use Cloud:** page
  content leaves norty (sandbox §14's rule).
- **Credentials as env vars or a mounted file:** no per-use policy, audit or
  origin binding; values sit in process environments.
- **HashiCorp Vault:** BSL licence since 2023; OpenBao is the MPL fork with
  the same API.
- **Logged-in profiles in `nora-computer`:** a room's shell could read the
  cookies or call an unauthenticated OpenCLI daemon and bypass the bridge.
- **A tool call per click from Nora's main loop:** does not fit her turn
  budget (sandbox §14).
- **Vision-first CUA as the main loop:** needs a strong multimodal model per
  step; kept as the escalation tier.
