# How others do it — research, 2026-10-06

Status: research record. **The research proxy blocked most project sites;
GitHub pages were read, the rest came from search-result snippets and is
marked (S).** VRAM figures in §4 are arithmetic (weights plus about 1 GB for
cache and a 3–4k-token page), not measurements. Re-verify any number before
a decision rests on it.

## 1. Hermes Agent (Nous Research)

- Backends: Browser Use Cloud, Browserbase, Firecrawl (cloud); own Chrome
  over CDP, **agent-browser (default)**, Camofox, Lightpanda (local).
  Headless by default; `headed: true` for a visible window a human can take
  over. <https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/browser.md>
- Model sees agent-browser accessibility snapshots with `@eN` refs; a
  separate `browser_vision` screenshot tool for CAPTCHAs and odd layouts.
- Anti-bot: Camofox, a self-hosted wrapper around Camoufox (fingerprint-
  spoofing Firefox). <https://github.com/NousResearch/hermes-agent/pull/4008>
- Logins: copies the user's real profile and drives the copy.
- Credentials: a password-blind vault (`browser_vault_list`,
  `browser_vault_fill`, `browser_vault_enter_code`) filling from 1Password,
  Bitwarden or a local vault, with an output redactor (S).
  **The redactor was bypassed:** `browser_exec` read the filled password
  back in plaintext into `state.db`.
  <https://github.com/NousResearch/hermes-agent/issues/112693>
- Egress: "iron-proxy", opaque tokens in the sandbox, real credential
  substituted by a host daemon (S).

## 2. pi (pi-mono) and OpenClaw

- pi: four tools (read, write, edit, bash), a short system prompt, no MCP by
  design (S: <https://lucumr.pocoo.org/2026/1/31/pi/>).
- pi's browser-tools skill: small Node scripts over CDP on `:9222`
  (`browser-start.js [--profile]`, nav, eval, screenshot, pick, cookies,
  content-as-markdown). Prefer DOM to screenshots; batch into one JS call.
  <https://github.com/badlogic/pi-skills/blob/main/browser-tools/SKILL.md>
- OpenClaw (built on pi): an isolated agent-only profile, a "user" profile
  attaching to the real signed-in Chrome via Chrome DevTools MCP, remote-CDP
  providers; Playwright behind a loopback control service; AI/ARIA snapshots
  with numeric refs; screenshots as fallback.
  <https://github.com/openclaw/openclaw/blob/main/docs/tools/browser.md>
- **Neither is more sophisticated than this design;** both are CDP + ref
  snapshots + logged-in profiles.

## 3. CUA and the wider field

- **OpenAI CUA** (Operator): screenshot-only, emits `click(x,y)` (S).
- **trycua/cua**: MIT infrastructure, not a model; gVisor/VM sandboxes,
  screenshots plus accessibility, bring your own model; ships cua-bench and
  the cua-s1 models. <https://github.com/trycua/cua>
- Open CUA models: OpenCUA-72B (45.0% OSWorld-Verified), UI-TARS-1.5 (S).
  State of the art is vision-first and needs a strong multimodal model.
- **Token cost of snapshot formats:** agent-browser `snapshot -i` about
  200–400 tokens per page; a claimed 10-step flow about 7k tokens vs about
  114k (Playwright MCP) and 50k (Chrome DevTools MCP) (S).
- **Action caching:** Stagehand v3, raw CDP, caches actions by DOM hash and
  calls the model only when a cached action fails; about 2x faster, about 30%
  cheaper on repeats (S: <https://www.browserbase.com/blog/stagehand-v3>).
- **Agent Workflow Memory** (ICML 2025): induced reusable workflows, 30–50%
  fewer steps on WebArena/Mind2Web (S).
- **browser-use:** direct CDP since early 2026; its own BU 2.0 model;
  `sensitive_data` placeholders scoped per domain with `allowed_domains` (S).
- **Anti-detect:** Patchright (no `Runtime.enable` leak), nodriver (raw
  DevTools websocket), Camoufox (Juggler, not CDP); one 2026 benchmark ranked
  nodriver best (S).
- **Credentials for agents:** 1Password Secure Agentic Autofill
  (human-approved injection, Browserbase only); Bitwarden Agent Access SDK
  (alpha, just-in-time); HashiCorp Vault AI-agent support (preview); Infisical
  Agent Vault (egress proxy); placeholder-substitution proxies are common (S).
  The shared pattern: the model holds a handle, a trusted layer fills, approve
  per use, redact every read.

## 4. Decision models for the step-picker

A new family (autumn 2026): read a state plus options, return a choice and a
confidence in one pass, without generating text.

| model | maker | weights | shape | evidence |
|---|---|---|---|---|
| Jev | TypeSafe AI | **hosted only** | action + element index over an element table | Mind2Web step success about 43.5% (community, S) |
| Laya | Convai | Apache, 421M (ModernBERT) | option scores; 512 tokens | weak zero-shot; needs fine-tune |
| Tev1 0.8B / 4B | Together AI | MIT (Qwen3.5 LoRA) | one letter of 2–24 | own dev set only. <https://github.com/togethercomputer/tev1> |
| Strands Decider 2B | AWS Strands Labs | Apache, recipe and data open | Qwen3.5-2B + pointer head; any number of options; 3–4k ctx | JevBench 0.76; 106 ms on RTX 3090; no browser eval. <https://github.com/strands-labs/strands-decider> |
| cua-s1-4b | trycua | Apache LoRA on Qwen3.5-4B | text and screenshot adapters; ≤26 options | own evals 0.875 / 0.929 |
| Holo3.1-4B, Holo2 | H Company | open (licence unverified for 3.1) | screenshot → coordinates | Holo2 WebVoyager 80.2% (S); official GGUF |
| Fara1.5-4B / 9B | Microsoft | MIT | screenshot → coordinates | 9B WebVoyager 86.6%, Online-Mind2Web 63.4% (S). <https://github.com/microsoft/fara> |
| UI-TARS-1.5-7B | ByteDance | Apache | screenshot → coordinates | ScreenSpot-V2 94.2% (S) |
| kev-0.6b-browser-use | community | open | about 20 candidates → element + action | Mind2Web 32.2% |
| BU-30B-A3B | Browser Use | open (licence unverified) | DOM + screenshot | about 18 GB Q4: does not fit |

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

- The **RTX A3000 is a laptop part** (Ampere, 6 GB or 12 GB, 336 GB/s) (S);
  roughly a third to half of an RTX 3090. Desktop near-equivalents: RTX A2000
  12 GB (low profile, about 70 W), A4000 16 GB.
- **Take 12 GB or more.** Every recommended arm fits in 12 GB; at 6 GB the 4B
  vision models with image input are tight and 7–9B do not fit.
- Expected: Strands Decider about 250–400 ms/step on A3000-class (an
  extrapolation from 106 ms on a 3090).
- Runtimes: vLLM or llama.cpp server. Strands Decider has its own PyTorch
  server (not llama.cpp). Qwen3.5 vision GGUFs reportedly do not work in
  Ollama (S).
