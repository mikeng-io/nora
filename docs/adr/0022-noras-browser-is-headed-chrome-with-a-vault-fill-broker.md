# 0022 · Nora's browser is headed Chrome in sbx, driven in tiers, with credentials only through a vault fill broker

- **Status:** proposed (2026-10-07)
- **Date:** 2026-10-07
- **Extends:** [0019](0019-noras-computer-runs-in-an-sbx-microvm.md) and
  `design/sandbox-runtime/00-design.md` §14 (S4, the browser), which this
  makes concrete.
- **Supersedes, in part:** sandbox-runtime decision 3 (2026-10-01, "no
  browser logins are allowed for now"), for Nora's own accounts only, held
  by a dedicated `browser` user inside `nora-computer`.
- **Design:** `design/browser-runtime/` (00 shape, 01 vault, 02 research,
  03 open questions and rulings, 04 the S1 runbook).
- **Rulings folded in (operator, 2026-10-07):** egress opens after S1's
  probe; one sandbox; `auto` approval for read-only credentials, `ask` for
  the rest; no recordings; decision models on CPU first; manual unseal.
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
2. **Browsing egress opens only after S1's rebinding probe** (operator,
   2026-10-07). `nora-computer` reaches the public web over TCP once a
   name resolving to a private or loopback address is shown refused, or
   after a host-side fence makes it so. Because sbx's own help says a CIDR
   deny is not checked against an allowed name's address, the fence is the
   expected answer, and S1 decides whether it comes first. Logged-in
   profiles share this egress, and Chrome's URL policy limits them to
   exact hosts.
3. **Credentials live in OpenBao and reach a page only through the fill
   broker.** Transit encrypts credential records stored in Postgres, TOTP
   generates 2FA codes, and the broker (outside the VM) fills by CDP after
   checking scope, approval, both the top-level and the field's frame
   origin, and that the element is a login field of the credential's kind,
   and only into a logged-in profile. `auto` approval is only for
   credentials the operator marks read-only. Models
   hold handles only. Every path that reads out of the browser (snapshot,
   eval, console, network, cookies, screenshot, stream, traces, rows)
   passes a scrubber, and eval is disabled on a page holding a filled field.
   A canary credential test over every read path gates the build.
4. **One sandbox, two trust domains by Unix user** (operator, 2026-10-07).
   Anonymous profiles belong to each room's user. Logged-in profiles
   (Nora's own Google account first) belong to a dedicated `browser` user
   that no `computer_run` command runs as. Every Chrome is driven over
   `--remote-debugging-pipe` held by computerd, never a TCP port, and each
   domain has its own X display. OpenCLI moves into the VM only if a
   room's shell is measured refused by its daemon; otherwise it stays on
   the host.
5. **`computer_browse` climbs four tiers and a human:** site adapter →
   cached script (self-healing) → a pluggable small decision model behind a
   pre-ranker, fail-closed on a bad ref or low confidence → DeepSeek v4.1
   Flash with vision (Set-of-Marks, refs not pixels) → `needs_human` over
   noVNC. Which decision model fills the slot is the B3 spike's to measure,
   not this ADR's.
6. **The portal may stream the screen view-only,** paused during a fill:
   an anonymous profile to its room's owner and the operator, a logged-in
   profile only for the viewer's own room's running task. noVNC on the
   logged-in display is the operator's only. No recordings.

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
- **Logged-in cookies share a kernel with every room's shell.** The fence
  is Unix users and file descriptors; a local privilege escalation in the
  VM reads every session. A second VM would have made that a VM escape.
  Its cost, memory on a host with 15 GiB available, was declined.
- **The logged-in profiles have no network allowlist of their own,** since
  sbx egress is per sandbox. Chrome's URL policy is the fence there, enforced
  by the browser, not the network.
- **#678 (one VM per social unit) reopens this:** Nora's accounts would sit
  in one unit's VM, or be signed in once per VM.
- **OpenBao is new infrastructure to run:** unseal after every reboot (fills
  are `unavailable` until the operator unseals), upgrades, its own audit log.
- **Credential values do enter the VM** at fill time, inside the page; a
  compromised page or VM sees what it is given. Origin binding limits which.
- **More moving parts in one tool:** the cache, the ranker, a local model
  server (CPU first; a GPU only if B3 shows it is needed), and escalation logic all have to stay measured, or
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
- **A second sandbox for logged-in profiles** (`nora-browser`): a kernel
  between room shells and Nora's sessions, and its own egress allowlist.
  Recommended, and declined by the operator on 2026-10-07 for its memory
  and its second lifecycle.
- **A tool call per click from Nora's main loop:** does not fit her turn
  budget (sandbox §14).
- **Vision-first CUA as the main loop:** needs a strong multimodal model per
  step; kept as the escalation tier.
