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

## Design refresh (2026-11-04)

This refresh separates product layers; the October survey below is historical,
not a current model recommendation or a claim that its measurements were rerun.

- **Jev:** TypeSafe's official [introduction](https://docs.typesafe.ai/introduction)
  describes supplied state and typed questions returning structured decisions.
  It is a decision component, not an action executor. API availability, Nora's
  route and the hosted-data restriction remain unresolved for this design.
- **trycua:** the [repository](https://github.com/trycua/cua) supplies computer-use
  drivers/infrastructure; adopting that driver is separate from choosing a model.
- **Cua-S1:** its [model card](https://github.com/trycua/cua/blob/main/libs/cua-s1/MODEL_CARD.md)
  describes a family of specialist research checkpoints with different task
  contracts. The [README](https://github.com/trycua/cua/tree/main/libs/cua-s1)
  explicitly scopes it below a general computer-use agent. An exact checkpoint,
  input/action contract and resource fit are required before comparison.
- **Nora source baseline inspected:** `nora/agent/browse.py` gives a Strands
  subagent page tools and a typed outcome; `nora/infra/computer/browser.py`
  implements snapshots/refs and actions over computerd CDP. This establishes
  the existing source path, not current host enablement or target VM delivery.

Primary pages above were read on 2026-11-04. No model or host was called.
Current recommendation and unresolved selection are owned by
[00](00-design.md#control-and-model-selection--design-review-2026-11-04) and
[03](03-open-questions.md#current-selection-2026-11-04); no new benchmark score,
latency, cost or broad capability is inferred here.

## MicroVM browser setup research (2026-11-04)

Research scope: browser binary, automation package, guest display/sandbox,
profiles, transport and export for anonymous public-web agent tasks. Primary
documentation and the current repository were read; no packages were installed,
no VM/browser was started and no performance or challenge-rate test was run.
The October survey below is historical. This section corrects its general
headless/fingerprint assumptions; no claim of lower challenge rate is measured.

### Browser binary: Chrome, Chromium and Chrome for Testing

- [Playwright browser documentation](https://playwright.dev/docs/browsers)
  distinguishes its bundled open-source Chromium from installed branded Chrome
  (`channel="chrome"`). A package named `chromium` in a Python/JS API does not
  identify the binary it will actually launch. Record executable and version.
- [Google's Chrome for Testing documentation](https://developer.chrome.com/docs/automation-and-testing/chrome-for-testing)
  describes a versioned Chrome build without auto-update, intended for testing
  and automation; that page also limits its recommendation to trustworthy content.
  Reproducible testing is a useful arm, not sufficient evidence to replace Nora's
  general public-web browser. Its security-update ownership must be explicit.
- [Chrome headless documentation](https://developer.chrome.com/docs/automation-and-testing/headless)
  says modern headless and headful share Chrome code; old headless is the separate
  `chrome-headless-shell`. Headed is retained as Nora's accepted baseline, not
  because a different engine or anti-bot advantage was proved. Screenshots alone
  do not require Xvfb/headed. A headless alternative remains possible if selected.
- [Chrome remote-debugging changes](https://developer.chrome.com/blog/remote-debugging-port)
  require a non-default `--user-data-dir` for ordinary Chrome debugging switches
  since Chrome 136. Never attach an agent to a person's ordinary profile.

**Recommendation:** keep the already packaged Google Chrome Stable for Nora,
with explicit version/digest and a deliberate security-update/rebuild process.
Chromium is a valid alternative, not a demonstrated inferior agent engine.
Neither Chrome branding, headed mode nor a webdriver flag guarantees challenge
avoidance. Existing renderer Chromium is a different consumer and not implicitly
Nora's browsing executable.

### Automation library/package options

| Candidate | What official documentation supplies | Fit and cost for Nora (engineering assessment) |
|---|---|---|
| Existing Python `aiohttp` + Nora CDP client | Already used by the client and computerd relay; source path inspected | Least migration for current scoped work, but Nora owns protocol/event/ref handling and must verify it. Reuse is a recommendation based on existing code, not a speed benchmark. |
| Python `playwright` / Node `playwright-core` | [Browser APIs](https://playwright.dev/docs/api/class-browsertype), locators, contexts and [full-page screenshot](https://playwright.dev/docs/screenshots); installed Chrome supported | Strong maintained high-level alternative if replacing custom browser mechanics. `connectOverCDP` is documented as lower fidelity than Playwright protocol, with launch-argument compatibility caveats. Verify the current authenticated relay, headers, target events and cancellation before claiming drop-in support; no open CDP port workaround. |
| `agent-browser` | [Official repository](https://github.com/vercel-labs/agent-browser): native CLI, accessibility refs, resident daemon, CDP attachment and sessions; default browser is Chrome for Testing | Useful for shell-oriented workers. Its default per-session browser/daemon differs from one browser per Nora VM. An attached/shared-browser integration must preserve task-tab binding and stop behavior; it is not automatically another decision model. |
| Python `browser-use` | [Official browser parameters](https://docs.browser-use.com/open-source/customize/browser/all-parameters): existing CDP endpoint, executable/profile/proxy controls and `keep_alive` | Candidate whole browser-agent runner, not just a screenshot library. Overlaps Nora's existing Strands browser loop; compare end-to-end integration and cost before replacing it. Do not nest a second agent loop merely to obtain page operations. |
| `cua-driver` / Python `cua_driver` | [Driver documentation](https://cua.ai/docs/cua-driver) provides native apps and browser control; separate from Cua-S1 model checkpoints | Candidate when real desktop operations are needed. [Linux support](https://cua.ai/docs/cua-driver/concepts/platform-support) depends on window system/toolkit; not every input route is supported. More guest environment and binding work than browser-only CDP. |

These are alternatives, not dependencies to install together. No package version
is selected from a search snippet; implementation must pin the actual compatible
release and its lock/artifact integrity after choosing a path. First-version
recommendation remains existing CDP + Flash; Playwright is the principal
high-level library alternative if the source mapping demonstrates that reuse
would leave substantial custom mechanics to build. A bounded authorized
integration check, not documentation alone, resolves that trade-off.

### Guest setup and ownership

[Docker Sandboxes isolation](https://docs.docker.com/ai/sandboxes/security/isolation)
puts a Linux kernel in each microVM. This does not tell us whether the browser's
own renderer sandbox is active. [Playwright's container guidance](https://playwright.dev/docs/docker)
and [Puppeteer troubleshooting](https://pptr.dev/troubleshooting) call out user,
namespace/sandbox and process management requirements; ordinary Docker examples
must not be copied as sbx host flags.

| Component | Concrete proposed setup / evidence still required |
|---|---|
| Guest image | Existing digest-pinned Ubuntu image, Google Chrome Stable `.deb` with version/hash verification, Xvfb and distro runtime libraries, CA certificates and Noto CJK/emoji fonts. Install at image build, not first user task. |
| Processes | computerd remains the management process; Chrome and its Xvfb run as the admitted scope's non-root user. Retain browser renderer sandboxing; do not add `--no-sandbox` to make a failed launch pass. Verify it in the actual sbx guest. |
| Display | Retain Xvfb with an authenticated local display, `-nolisten tcp`, consistent viewport/locale/timezone/fonts. Browser-only CDP needs no full XFCE desktop, VNC or streaming server. Measure RAM and `/dev/shm` availability and process reaping inside the guest. Do not use host IPC or broad capabilities as an unmeasured fix. |
| Profile/session | Fresh non-default private anonymous profile for each browser lifetime, held while tasks continue, no imported personal/authenticated profile. Retain tab/context handles across turns and remove ephemeral state at expiry; keep workspace files under their separate contract. Preserve old profiles privately during migration rather than reusing logins. Decide explicitly whether task contexts share cookies; tab separation alone does not isolate storage. |
| Control | computerd owns `--remote-debugging-pipe` and exposes only its authenticated scoped relay. Agent/model loop remains outside the VM; worker access uses the same admitted VM/task binding. No model/master key or unauthenticated debugging listener is added. |
| Network | Apply/read back accepted egress per created sandbox, set Chrome's proxy deliberately, preserve TLS startup checks and known hostname/private-IP limitation. A new package must not bypass those checks by launching its own unmanaged Chrome. |
| Screenshot | Capture actual tab bytes, save a scoped image, then existing `computer_export`; verify full-page semantics and size bounds. PNG or JPEG as appropriate, no base64 logs. |

If Cua Driver is selected instead, its [VM guide](https://cua.ai/docs/cua-driver/guides/vms-and-remote)
places the driver in the guest whose desktop it controls. Its own Linux sandbox
test environment includes Xvfb, XFCE and AT-SPI; that is not proof that Nora's
current Xvfb-only image supplies all prerequisites. Its [browser guide](https://cua.ai/docs/cua-driver/guides/browsers)
binds exact native window/tab identity and documents Linux trusted-background
input refusal. A dedicated VM may permit foreground delivery, but that route
must be selected and measured; do not silently accept unverifiable synthetic
clicks as task completion. Installing Cua-S1 alone supplies none of this desktop.

### Repository observations and implementation checks

At source candidate `d126a5ca736cc90f1a0b17890b2906341e9ed5ba`:
`deploy/computer/Dockerfile` already installs version/hash-pinned
`google-chrome-stable` and `xvfb`, runtime libraries and Noto fonts. A separate
Puppeteer download serves Mermaid rendering. `deploy/computer/computerd.py`
launches `google-chrome` non-root on Xvfb with a private profile and debugging
pipe; no `--no-sandbox` flag is in its managed launch. Its launch includes
`--disable-blink-features=AutomationControlled`; that is not challenge-rate proof.
`nora/infra/computer/browser.py` uses `aiohttp` CDP and accessibility refs.
`deploy/computer/README.md` owns the current native TLS baseline and the
verification required before replacing it. None of this is current host readback.

Before building, close executable/package selection and map the actual new
work: scoped lifecycle/endpoint routing, task tabs/contexts and cookie policy,
expiry, capture-to-file/export and audit correlation. If replacement is proposed,
compare the existing path and one alternative on the same finite continuation,
concurrency, dynamic-page, capture/export and failure journeys. Report integration
changes, completion, calls, latency, RAM and challenge counts; no anti-bot or
performance conclusion follows from vendor feature lists. Runtime probes and
model comparisons require separately authorized scope/cost; this research ran none.

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
  through OmniRoute. **Measured 2026-10-07 (S1 step 5):** the route reads
  images (5/5), and the upstream answers as `deepseek-v4.1-flash`.
