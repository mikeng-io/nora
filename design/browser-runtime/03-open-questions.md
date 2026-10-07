# Open questions and measurements

Status (2026-10-07): open. Each is either an operator ruling or a number to
take; none is decided by argument.

## 1. Rulings for the operator

1. **Accept ADR 0022?** It supersedes sandbox decision 3 (no browser logins)
   for Nora's own accounts, in `nora-browser` only.
2. **Browsing egress:** public web from the VMs with a private-range and
   host deny list, or a narrower allowlist per task?
3. **Two sandboxes or one** (`nora-computer` anonymous, `nora-browser` logged
   in)? Two costs a second VM's memory; one puts cookies next to the shell.
4. **Approval default** for credential use: `ask` for everything at first,
   or `auto` for read-only sites?
5. **Recordings** of browser sessions for audit: none, frames, or rrweb?
6. **GPU:** a 12 GB card (A2000 12 GB desktop, or a 12 GB A3000 if norty
   takes a mobile part), or CPU-only arms first?
7. **OpenBao unseal:** manual (default) or TPM auto-unseal?

## 2. Measurements (S1 and the B3 spike)

**S1, before any build:**

- Chrome + Xvfb RSS at 1, 3, 5 tabs inside the 3 GB VM.
- TLS fingerprint (JA3/JA4) seen by a server through the sbx proxy, vs Chrome
  direct. If they differ, the proxy terminates TLS and browsing needs a
  different egress path.
- norty's exit IP: residential or datacenter, and its reputation.
- Is norty x86-64 (Google Chrome) or ARM (Chromium only)?
- Does OmniRoute → Ollama Cloud pass images to DeepSeek v4.1 Flash?

**B3 spike.** Same six tasks as sandbox §14 (availability, form with
validation errors, a table behind a login with the credential never shown,
prices across two sites, a prompt-injection trap, a re-run after a layout
change), each at least 5 runs, deterministic checkers. Arms:

1. Flash alone (snapshot, vision on demand)
2. Strands Decider 2B + pre-ranker, escalating to Flash
3. cua-s1-4b (text, then text + Set-of-Marks), escalating to Flash
4. Tev1-0.8B, and Laya + ranker on CPU (no-GPU baselines)
5. Holo3.1-4B or Fara1.5-4B, screenshot + coordinate→mark adapter
6. Plain Qwen3.5-4B prompted for a ref (control: do the special heads win?)
7. browser-use, vision off and on (the comparison)
8. Camoufox engine under the best arm (anti-detect)

Measured, fail-closed: success rate; model calls, tokens and cost per task;
wall time; **escalation rate; wrong-ref rate**; second-run cost (tier 2);
zero unexpected egress; **zero canary-credential hits** in any read, trace or
row (`01-vault.md` §5).
