# Open questions and measurements

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
