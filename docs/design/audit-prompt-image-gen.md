# Kimi External Audit — Nora image-generation lane (DEC-137)

You are performing an independent adversarial audit of a code change in the Nora repository
(`~/workspace/nora`). Nora is a production Strands-based AI assistant agent (Signal + Discord)
running in Docker. The change adds an image-generation capability via the xAI API using the
operator's X Premium subscription OAuth device flow (quota-bounded, not metered billing).

## Scope

Review the implementation reachable on `master` (worktree at `~/workspace/nora-wt-imagegen`,
same tree). The relevant commits, newest first:

- `f5082c7` feat(image-gen): enable the image generation lane
- `dd62731` fix(image-gen): drop duplicate /v1 in xAI API base URL
- `12eaedc` fix(image-gen): poll the OIDC token endpoint, not a hardcoded /device/token
- `f716775` fix(image-gen): xAI token endpoint empty body crashes xai-login polling
- `38ebe3c` feat(image-gen): pass XAI_OAUTH_REFRESH_TOKEN through compose; .env.example docs
- `f21e947` feat(image-gen): xAI image generation via X Subscription OAuth lane (DEC-137)

Files to audit (all under `~/workspace/nora` unless noted):

1. `nora/infra/image_gen/xai_oauth.py` — OAuth device flow: discovery, poll loop, refresh,
   state file (`./state/xai-oauth.json`), error semantics
2. `nora/infra/image_gen/xai_adapter.py` — xAI API client: generate/edits, 3-state
   `ImageResult`, 403/429 classification, b64/url artifact handling, drop hosting
3. `nora/infra/image_gen/__init__.py` — provider factory
4. `nora/domain/ports.py` — `ImageGenerator` port, `ImageResult`, `ImageReference`, `ImageSize`
5. `nora/config.py` + `config.toml [image_gen]` — config model (ships enabled=true now)
6. `nora/app/composition.py` (~line 1139-1167) — lane wiring gate
7. `nora/app/cli.py` — `xai-login` command (device flow writes refresh token to host `.env`)
8. `nora/agent/tools.py` — `generate_image` NativeTool
9. `docker-compose.yml` — env passthrough, `./state` bind mount
10. `tests/contract/test_image_generator_contract.py` — contract suite

## What to check (be adversarial, not polite)

1. **OAuth correctness**: Does the device flow follow RFC 8629? Poll interval/expiry
   handling; error classification (`authorization_pending`, `slow_down`, `expired_token`,
   `access_denied`); is the token endpoint correctly resolved from OIDC discovery? Any
   path where credentials could leak into logs, exceptions, or stdout?
2. **Refresh/expiry**: `refresh_token()` — does it handle concurrent refresh (lock)? Is the
   state file atomic (partial write = corrupt token)? Is `expires_at` computed correctly?
   What happens on refresh failure — fail-closed?
3. **Adapter correctness**: 403 classification by body code (quota vs entitlement); 429;
   empty body handling; base64 decode; the `/v1` URL construction (this was a real bug:
   `XAI_API_BASE` used to include `/v1` AND the adapter appended `/v1/...` again → double
   path; fixed in dd62731 — check no other place repeats this); the `edits` multipart path.
4. **Security**:
   - Refresh token never logged, never printed, never committed. Check `.env.example`,
     `config.example.toml`, `docker-compose.yml`, README, design doc.
   - Access token stored ONLY in `./state/xai-oauth.json` (container-writable); `.env` holds
     only the refresh token. Verify no code path writes the access token to `.env`.
   - Reference images: `allow_reference_images = false` default — the privacy gate. Verify
     the adapter cannot be tricked into uploading an arbitrary external URL (it resolves
     only via the injected `reference_resolver`).
   - The CLI `xai-login` writes `XAI_OAUTH_REFRESH_TOKEN=<token>` to `.env` — verify it
     does not print the token, and that `_rewrite_env` is idempotent (no duplicated lines).
5. **Wiring**: `composition.py` builds the lane only when `enabled` AND env var present;
   otherwise the tool answers `unavailable` (never a raise, never `nothing_found`). Is
   there any path where a missing token crashes boot? What happens if the refresh token is
   present but invalid (expired) — does boot survive?
6. **Tool integration**: `generate_image` — budget, act-verb docstring (no provider names),
   privacy gate, reference-image marker codes (hex handle extraction). Does the tool accept
   model-facing inputs that could leak the refresh token or cause prompt injection?
7. **Regression risk on production Nora**: the lane is now `enabled = true` (f5082c7) on a
   production agent. Is there any path where a generation failure could crash the agent loop
   (unhandled exception, non-ImageResult return)? Memory/leak concerns with the aiohttp
   session in the long-running container? Lock contention in refresh?

## Output format

Return findings as a list with, for each:
- **Severity**: critical / major / minor / nit
- **file:line** (or file range)
- **What** — the concrete issue
- **Why it matters** — blast radius in the production agent
- **Suggested fix** — one actionable fix

End with a verdict line: **VERDICT: APPROVE** or **VERDICT: REQUEST CHANGES**.

Do NOT run `make gate` or modify any file. Read-only audit. You are verifying; the operator
decides. Be concise — findings only, no preamble.
