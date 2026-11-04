# Open questions and measurements

Current scope (2026-11-02): the
[scoped anonymous browser target](00-design.md#current-target--scoped-anonymous-browser-2026-11-02)
is NOT BUILT and owns today's acceptance in issue #520. The rulings and B3
measurement programme below are retained historical context. Login/vault,
streaming/noVNC, tier cache, small models, GPU and stealth-engine arms are
outside the current slice; challenge rate is measured on the consistent Chrome
baseline without an anti-bot guarantee. ADR 0022 remains unchanged.

## Current selection (2026-11-04)

The operator requested a coherent design before implementation. Topology,
anonymous use, continuation and direct screenshots are settled requirements.
The design supports the existing Flash runner first and additional decision
backends later through the same task-level port; it does not require every
backend for first delivery. Shared executor audit, scoped screenshot files and
`computer_export` delivery belong to the integrated slice.
The executor and decision-model choice is still a design selection, not a
completed benchmark or authorization to build every historical tier.

Queue scope is now explicit: one active computer execution segment per unit/DM
VM, reusing Redis Streams infrastructure with scope-wide admission. Source
mapping must close durable pre-launch intent and existing completion-job
integration, owner-loss process reconciliation, and worker nested-call handling
before build. Existing bridge streams and their ACK contract stay separate.

| Question | Current evidence / recommendation | What closes it |
|---|---|---|
| Existing CDP executor or trycua driver? | Existing Nora executor is built. Recommend reuse for browser-only scope; no demonstrated missing desktop operation is supplied by the current brief. | Accept reuse, or name a required action the existing executor cannot perform and verify candidate Linux/VM integration. |
| Existing browse model or Cua-S1? | Existing browser subagent is baseline; Cua-S1 has checkpoint-specific specialist contracts, not evidence of complete Nora browser journeys. | Accept baseline for first delivery, or select one exact checkpoint and bounded task comparison; include invalid/stale refs, abstention, continuation and whole-task results. |
| Jev? | Typed-decision candidate; not a browser driver. The accepted ADR rejects hosted page-content processing. No current authorized endpoint, route, credential or Nora-task receipt is established. | Resolve the data boundary and actual endpoint/protocol/OmniRoute fit first. Hosted use needs an explicit change to the accepted restriction; catalogue membership is insufficient. |
| Is a model comparison required before first delivery? | Recommendation: no paid spike by default; keep the existing model while VM/session routing changes. This remains a recommendation for design acceptance. | Record the accepted baseline and deferred alternatives in #520; if a comparison is requested, name its finite tasks, arms and spending limit before running it. |

A requested comparison uses anonymous tasks matching this slice: navigate/read,
search/filter, two-turn continuation, independent concurrent task tabs, dynamic
page/stale refs, and an explicit challenge/failure outcome. Direct screenshot
has an image/tab check and no model arm. Report attempted/completed/error counts,
wrong actions, latency and total model/adapter cost; include ranker/escalation
cost if present. Local resource fit is measured, not copied from a model card.
Do not reuse historical logged-in/vault tasks as prerequisites for this anonymous
slice. A missing candidate is unavailable, not a loss or a pass.

The historical rulings and broad B3 programme below are not current dispatch
instructions. Accepted ADRs stay unchanged; any accepted reversal follows the
ADR amendment procedure.

Status (2026-10-07): every ruling made, ADR 0022 accepted, S1 run. Nothing here is decided by argument.

## 1. Rulings for the operator

Ruled 2026-10-07 unless marked open. Each keeps the fact it rests on, so a
later reading can reopen it.

1. **Accept ADR 0022: ruled, accepted** (2026-10-07). B0 is done; B1 is
   next.
2. **Browsing egress: ruled, `**` with private ranges denied, no extra
   proxy.** S1 showed a name resolving to a private address still gets
   through (`04-s1-runbook.md` step 4); the operator accepted that risk.
3. **One sandbox or two: ruled, one.** The recommendation was two. Logged-in
   profiles live in `nora-computer` under a dedicated `browser` user, with
   the fences in 00 §4. What the choice costs is in ADR 0022 Worse. Reopen if
   a room's shell is measured reaching a logged-in profile, its display or
   its CDP, or when #678 splits the VM per unit.
4. **Approval default: ruled, `auto` for read-only, `ask` for the rest.**
   The operator marks a credential `read_only` when the record is made
   (`01-vault.md` §3), and a model never sets it. Anything that can post,
   buy or send is `ask`.
5. **Recordings: ruled, none.** Steps are `computer_events` rows. Reopen when
   a failure cannot be explained from them.
6. **GPU: ruled, CPU first.** norty is a mini PC with no internal slot (00
   §0, §5.3); a mobile card is out. Buy a desktop card of 12 GB or more in
   a USB4 enclosure only if B3 shows the picker tier wins on fit and CPU
   latency is what stops it.
7. **OpenBao unseal: ruled, manual.** OpenBao has no TPM seal
   (`02-research.md` §6); PKCS#11 through `tpm2-pkcs11` is untested. Every
   fill fails closed while sealed. Reopen if unseal turns out to be what
   keeps fills `unavailable`.

## 2. Measurements (S1 and the B3 spike)

**S1: run 2026-10-07** (`04-s1-runbook.md` §Results). Chrome fits (5 tabs
leave 2.2 GiB); the proxy keeps Chrome's TLS; rebinding FAILED; Flash reads
images; residential exit, one Google challenge.

**Still to measure:** RSS with a logged-in Chrome beside an anonymous one and room work
(B4); Google's challenge rate across the B3 arms.

**B3 spike.** Same six tasks as sandbox §14 (availability, form with
validation errors, a table behind a login with the credential never shown,
prices across two sites, a prompt-injection trap, a re-run after a layout
change), each at least 5 runs, deterministic checkers. Arms:

1. Flash alone (snapshot, vision on demand)
2. Strands Decider 2B + pre-ranker, escalating to Flash
3. cua-s1-4b (text, then text + Set-of-Marks), escalating to Flash.
   Sandbox §14 excluded it for memory: its README asks for 16 GB free, and
   norty had 15 GiB available on 2026-10-07. It runs only if that fits
   at the time, or on a GPU.
4. Tev1-0.8B, and Laya + ranker on CPU (no-GPU baselines); Strands Decider
   2B on CPU, timed
5. Holo3.1-4B or Fara1.5-4B, screenshot + coordinate→mark adapter
6. Plain Qwen3.5-4B prompted for a ref (control: do the special heads win?)
7. browser-use, vision off and on (the comparison)
8. Camoufox engine under the best arm (anti-detect)

Arms 3 and 5 need a GPU, or are timed on the 680M iGPU where a GGUF runs
(00 §5.3).

Measured, fail-closed: success rate; model calls, tokens and cost per task;
wall time; **escalation rate; wrong-ref rate**; second-run cost (tier 2);
zero unexpected egress; **zero canary-credential hits** in any read, trace or
row (`01-vault.md` §5).
