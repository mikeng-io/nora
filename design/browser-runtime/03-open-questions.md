# Open questions and measurements

Status (2026-10-07): open. Each is either an operator ruling or a number to
take; none is decided by argument. Each ruling carries a recommendation and
the fact it rests on; the recommendation is not the ruling.

## 1. Rulings for the operator

1. **Accept ADR 0022?** It supersedes sandbox decision 3 (no browser logins)
   for Nora's own accounts, in `nora-browser` only.
   *Recommended:* accept at B0, after S1. The egress rebinding reading
   (00 §3) can change Decision 2, and the TLS reading can change where
   browsing egresses at all.
2. **Browsing egress.** The draft's "public web with a private-range deny
   list" cannot be written in sbx as it stands: a CIDR deny is not checked
   against an allowed hostname's resolved address (00 §0, §3).
   *Recommended:* `nora-computer` (anonymous) gets `**` over TCP only if S1
   shows a name resolving to a private or loopback address is refused, or
   after a host-side fence makes it so. `nora-browser` (logged in) gets only
   its accounts' hosts (00 §4).
3. **Two sandboxes or one** (`nora-computer` anonymous, `nora-browser` logged
   in)? *Recommended:* two. One puts session cookies on the disk of a VM
   whose shell the model drives, which is decision 3's own reason. The
   second VM's cost is memory: 15 GiB was available on norty 2026-10-07,
   and S1 measures what Chrome needs. `nora-browser` is single and Nora's,
   whatever #678 does to the anonymous grain (00 §4).
4. **Approval default** for credential use: `ask` for everything at first,
   or `auto` for read-only sites? *Recommended:* `ask` for every credential
   at first. Move one credential to `auto` per flow, after its runs are
   seen in the audit rows. A login is rarely typed at all (sessions first,
   `01-vault.md` §4), so `ask` costs little.
5. **Recordings** of browser sessions for audit: none, frames, or rrweb?
   *Recommended:* none at first. Steps are already rows (`computer_events`),
   and a recording of a logged-in page is personal data with a retention
   question attached. Revisit when a failure cannot be explained from the
   step rows.
6. **GPU.** norty is a mini PC with no internal slot (00 §0, §5.3). The
   "12 GB A3000 if norty takes a mobile part" option is withdrawn; a mobile
   MXM card cannot go in it. *Recommended:* CPU and iGPU arms first. Buy a
   desktop 12 GB-or-larger card in a USB4 enclosure only if B3 shows the
   picker tier wins on fit and CPU latency is what stops it.
7. **OpenBao unseal:** manual, or TPM auto-unseal? norty has a TPM 2.0
   (`/dev/tpm0`), but **OpenBao has no TPM seal** (`02-research.md` §6); a
   TPM would be reached through its PKCS#11 seal and `tpm2-pkcs11`, which is
   untested. *Recommended:* manual, since every fill fails closed while
   sealed and reboots are rare. Revisit only if unseal turns out to be what
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
3. cua-s1-4b (text, then text + Set-of-Marks), escalating to Flash
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
