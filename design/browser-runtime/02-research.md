# How others do it — research, 2026-10-06, re-verified 2026-10-07

Status: research record. The first pass (2026-10-06) ran behind a proxy that
blocked most project sites, so much of it came from search-result snippets.
**Re-verified 2026-10-07 against primary pages** (the repo, the model card,
the vendor's docs or spec sheet). Each claim now carries one of:

- **(V)**: read on a primary page on 2026-10-07;
- **(S)**: still only secondary (snippets, a third-party blog);
- **(U)**: could not be checked.

Corrections from the re-check are marked **Corrected**. VRAM figures in §5
are arithmetic (weights plus about 1 GB for cache and a 3–4k-token page),
not measurements.

## 1. Hermes Agent (Nous Research)

- Backends: Browser Use Cloud, Browserbase, Firecrawl (cloud); Camofox,
  Lightpanda, local CDP, local Chromium. **Corrected:** the default when
  `browser.backend` is unset is **Browser Use mode**, one `browser_exec`
  tool over Browser Use CLI 3.0, not agent-browser. (V)
  <https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/browser.md>
- Headless by default; `browser.headed: true` (or `AGENT_BROWSER_HEADED=1`)
  for a visible window a human can take over. (V)
- Snapshots with `@eN` refs; a separate `browser_vision` screenshot tool for
  CAPTCHAs and odd layouts. (V)
- Anti-bot: Camofox, a self-hosted wrapper around Camoufox, merged
  2026-03-30. (V) <https://github.com/NousResearch/hermes-agent/pull/4008>
- Credentials: a password-blind credential vault filling from 1Password,
  Bitwarden or a local store; Hermes generates the TOTP codes. (V)
  `browser_vault_fill` is confirmed; the other tool names are (U).
  <https://hermes-agent.nousresearch.com/docs/user-guide/features/credential-vault>
- **The leak:** `browser_exec` read a filled password back in plaintext into
  `state.db` (issue #112693, closed, fixed by PR #113261). **Corrected
  detail:** the hole was the Browser Use CLI path
  (`tools/browser_use_cli.py`, no redaction), while the sibling
  `browser_cdp_tool.py` did redact. That is this design's point exactly:
  redaction placed per tool missed the one tool that lacked it. (V)
  <https://github.com/NousResearch/hermes-agent/issues/112693>
- **Corrected:** "iron-proxy" is an **API-key** proxy (opaque tokens in the
  sandbox, the host swaps in the real provider key), not a browser-credential
  mechanism. It is the same pattern as sbx's `secret set-custom`. (V)
  <https://hermes-agent.nousresearch.com/docs/user-guide/egress/iron-proxy>

## 2. pi (pi-mono) and OpenClaw

- pi: four tools (Read, Write, Edit, Bash), a short system prompt, no MCP by
  design. (V) <https://lucumr.pocoo.org/2026/1/31/pi/>
- pi's browser-tools skill: seven small Node scripts over CDP on `:9222`
  (start, nav, eval, screenshot, pick, cookies, content). Prefer DOM to
  screenshots; batch into one JS call. (V)
  <https://github.com/badlogic/pi-skills/blob/main/browser-tools/SKILL.md>
- OpenClaw (built on pi): an isolated agent-only `openclaw` profile, a
  `user` profile attaching to the real signed-in Chrome via Chrome DevTools
  MCP, a loopback-only control service, Playwright-backed profiles. (V)
  ARIA snapshots with numeric refs are (U): not on the index page.
  <https://github.com/openclaw/openclaw/blob/main/docs/tools/browser.md>
- **Neither is more sophisticated than this design;** both are CDP + ref
  snapshots + logged-in profiles.

## 3. CUA and the wider field

- **OpenAI computer use:** returns `{type: "click", x, y}`-style actions with
  screenshots fed back. (V; "screenshot-only" is not stated.)
- **trycua/cua**: MIT infrastructure, not a model; sandboxes, screenshots
  plus accessibility, bring your own model; ships cua-bench and the cua-s1
  models. <https://github.com/trycua/cua>
- Open CUA models: OpenCUA-72B 45.0% OSWorld-Verified at 100 steps (V);
  UI-TARS-1.5-7B 94.2% ScreenSpot-V2, Apache-2.0 (V). State of the art is
  vision-first and needs a strong multimodal model.
- **Token cost of snapshot formats: (U).** The agent-browser README carries
  no token figures. Third-party figures are about 1.2k tokens for a Hacker
  News snapshot against about 14.7k (Playwright MCP) and 9.8k (Chrome
  DevTools MCP) (S). **Corrected:** the first pass's "200–400 tokens per page,
  7k vs 114k vs 50k" is withdrawn. B3 measures our own.
- **Stagehand:** v3 is raw CDP, Playwright dropped, "44%+ faster" than v2
  (V). Action caching is a separate February 2026 feature, validated by DOM
  hash, "**up to** 2x faster, ~30% cost reduction on repeat workflows" (V).
  <https://www.browserbase.com/changelog/stagehand-caching>
- **Agent Workflow Memory** (ICML 2025). **Corrected:** the gains are in
  success rate, +24.6% (Mind2Web) and +51.1% (WebArena) relative; fewer
  steps is shown on WebArena only, 5.9 vs 7.9 on successful tasks (about
  25%). (V) <https://arxiv.org/abs/2409.07429>
- **browser-use:** **Corrected:** moved from Playwright to direct CDP
  (`cdp-use`) on 2025-08-20 (V). Its own model `bu-2-0` (V).
  `sensitive_data` placeholders scoped per domain by URL-pattern keys;
  `allowed_domains` is a recommended complement (V).
- **Anti-detect:** Patchright avoids `Runtime.enable` by running JS in
  isolated contexts, Chromium only (V); nodriver talks DevTools directly (V);
  Camoufox runs on Juggler (V). A 2026-05-13 benchmark over 31 targets
  scored nodriver 28 OK / 0 blocked, Patchright, CloakBrowser and Camoufox
  25–26, and only nodriver passed the Cloudflare gate (V, one blog's test).
  <https://ianlpaterson.com/blog/anti-detect-browser-benchmark-patchright-nodriver-curl-cffi/>
- **Credentials for agents:**
  - 1Password Secure Agentic Autofill: early access since 2025-10-08,
    human-approved, first through Browserbase. (V)
  - Bitwarden Agent Access SDK: early alpha, Apache-2.0, just-in-time and
    human-approved. (V)
  - **Corrected:** HashiCorp Vault's agentic IAM went GA on 2026-09-01, in
    Vault **Enterprise** 2.1, so not in OpenBao. (V)
  - Infisical Agent Vault: a credential-injecting forward proxy (V); preview
    status (S).

  The shared pattern: the model holds a handle, a trusted layer fills or
  injects, each use is approved, and every read is redacted.

## 4. Decision models for the step-picker

A new family (autumn 2026): read a state plus options, return a choice and a
confidence in one pass, without generating text.

| model | maker | weights | shape | evidence |
|---|---|---|---|---|
| Jev | TypeSafe AI | **hosted only** (also listed on OpenRouter) | action + element index over an element table | Mind2Web about 43.5%, a figure from kev's author, not TypeSafe (S) |
| Laya | Convai | Apache, 421M (ModernBERT-large), 512 ctx (S) | option scores | 0.362 zero-shot (S); needs fine-tune. Card unreadable (U) |
| Tev1-4B-Experimental | Together AI | MIT, LoRA on Qwen3.5-4B | one letter of 2–24 | own dev set only. **Corrected:** no 0.8B is documented (V). <https://github.com/togethercomputer/tev1> |
| Strands Decider 2B | AWS Strands Labs | Apache, Qwen3.5-2B-Base + pointer head, recipe and data open | any number of options; 4096 ctx | JevBench 0.762; **corrected:** 115 ms median, 299 ms p95 on an RTX 3090; own `strands-decider serve`, no GGUF; no browser eval (V). <https://github.com/strands-labs/strands-decider> |
| cua-s1-4b | trycua | Apache LoRA adapters on Qwen3.5-4B (base keeps Qwen's terms) | text and multimodal adapters; ≤26 candidates incl. `reobserve`, `abstain` | own evals 0.875 / 0.929 (U); README asks ≥16 GB free memory (V) |
| Holo3.1-4B, Holo2-4B | H Company | **Apache-2.0** both (V) | screenshot → coordinates | Holo2-4B WebVoyager 80.2%, with GGUF (V); a 3.1-4B GGUF (U) |
| Fara1.5 4B / 9B / 27B | Microsoft | MIT, Qwen3.5-based | screenshot → coordinates | 9B WebVoyager 86.6%, Online-Mind2Web 63.4%; 4B 80.8% and 57.3% (V). <https://github.com/microsoft/fara> |
| UI-TARS-1.5-7B | ByteDance | Apache | screenshot → coordinates | ScreenSpot-V2 94.2% (V) |
| kev-0.6b-browser-use | community | Apache | **corrected:** 10 candidates → element + action | Mind2Web 32.2%; 80 ms on an L4 (V) |
| BU-30B-A3B-Preview | Browser Use | licence not on the card (U) | DOM + screenshot; Qwen3-VL-30B-A3B base | about 18 GB Q4: does not fit |

Not checked: OpenCUA-7B, GUI-Actor, ShowUI-2B, UI-Venus, OS-Atlas, Aguvis,
AgentCPM-GUI, WebRL/WebDancer, AgentTrek, OpenWebVoyager, Gemma-3n,
SmolVLM agents.

**Fit for the §5.1 contract (text-a11y first, refs, optional Set-of-Marks):**
Strands Decider 2B best (refs as options, calibrated confidence as the
escalate signal, Apache with an open recipe to fine-tune on Nora's own
traces), then cua-s1-4b (the only one trained on computer-use element
choice, both modalities). Holo/Fara are the vision reference and need the
coordinate→mark adapter.

## 5. GPU

- **norty cannot take an internal card** (`00-design.md` §0, read
  2026-10-07: a GMKtec NucBox M7 Ultra mini PC with USB4). A card means an
  external enclosure; a mobile part (the RTX A3000 is one) is out entirely.
- RTX A3000 Laptop, for the record: 6 GB at 264 GB/s or 12 GB at 336 GB/s
  (S, Notebookcheck; NVIDIA's page did not load). **Corrected:** the first
  pass gave 336 GB/s for both.
- Desktop cards: RTX A2000 in 6 GB and 12 GB, 70 W, low profile (V); RTX
  A4000 16 GB GDDR6 ECC (V).
- **Take 12 GB or more.** Every recommended arm fits in 12 GB; at 6 GB the 4B
  vision models with image input are tight and 7–9B do not fit (arithmetic).
- Strands Decider's own figure is 115 ms median on a 3090 (V). On a slower
  card, multiply by the bandwidth ratio as a first guess only; B3 times it.
- Runtimes: vLLM or llama.cpp server; Strands Decider has its own PyTorch
  server. **Corrected:** Ollama's official `qwen3.5` library lists image
  input (V); what breaks is imported third-party GGUFs with a separate
  mmproj file (ollama#16282, closed as not planned).

## 6. OpenBao and the model

- **OpenBao:** MPL-2.0, an OpenSSF sandbox project under LF Projects;
  transit, KV v2 and TOTP engines (V). HashiCorp's BSL change was announced
  2023-08-10 (V).
- **Seals: no TPM seal** (V, <https://openbao.org/docs/configuration/seal/>).
  The list is AliCloud, AWS, Azure, GCP, KMIP, OCI, OVHcloud, PKCS#11,
  Static Key, Transit and T Cloud, and from v2.7.0 many are plugins only.
  Auto-unseal from norty's TPM would go through PKCS#11 (for example
  `tpm2-pkcs11`), which is untested here. A Static Key on norty's disk is the
  option `01-vault.md` §2 already rejects.
- **DeepSeek Flash vision:** DeepSeek's API docs say `deepseek-flash` accepts
  images (V, <https://api-docs.deepseek.com/guides/vision>). Ollama's library
  page says `deepseek-v4-flash` "was retired on August 27, 2026" (V), yet
  Nora's live config still routes `ollama-cloud/deepseek-v4-flash:0731`
  through OmniRoute. **What Nora's route does with an image is S1's to
  measure** (`04-s1-runbook.md` §5), and no "v4.1" name was found on either
  page.
