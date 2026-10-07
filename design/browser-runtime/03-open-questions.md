# Open questions and measurements

Status (2026-10-07): six of seven rulings made; ADR acceptance and every
measurement open. Nothing here is decided by argument.

## 1. Rulings for the operator

Ruled 2026-10-07 unless marked open. Each keeps the fact it rests on, so a
later reading can reopen it.

1. **Accept ADR 0022?** **Open.** It is accepted at B0, after S1, because
   S1's rebinding and TLS readings can still change Decision 2.
2. **Browsing egress: ruled, open after S1's probe.** `nora-computer` gets
   `**` over TCP only once S1 shows names resolving to private or loopback
   addresses are refused, or a host-side fence makes it so. sbx's help says
   a CIDR deny is not checked against an allowed name's address (00 §0,
   §3), so a PASS shows only that those names were refused, and the fence
   remains the expected answer.
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

**S1, before any build:** the commands and their pass/fail readings are in
`04-s1-runbook.md`. Answered already (2026-10-07, 00 §0): norty is x86-64,
so Google Chrome; the sbx proxy tunnels TLS for a plain host rule (two hosts
read; S1 confirms with a fingerprint echo).

- Chrome + Xvfb RSS at 1, 3, 5 tabs inside the 3 GB VM.
- JA4 seen by a server through the sbx proxy, vs Chrome on the host.
- Rebinding: a public name resolving to `172.17.0.1`, the LAN IP and
  `127.0.0.1`, fetched through the proxy under `**`.
- norty's exit IP: residential or datacenter, and its reputation.
- Does OmniRoute → Ollama Cloud pass images to DeepSeek v4.1 Flash?

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
