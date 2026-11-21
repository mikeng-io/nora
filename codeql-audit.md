# CodeQL audit — 122 open alerts (2026-11-09)

Verdicts: **FP** = false positive / deliberate design, dismiss with reason ·
**Low** = technically correct pattern, negligible real-world impact ·
**Fix** = worth a small change if you want the board clean.

Bottom line: **no exploitable vulnerability found.** The single "critical"
(partial SSRF in roster.py) is real in shape but reaches only discord.com's
own API with the bot's own token. The 66 log-injection alerts are the same
pattern repeated: platform-assigned IDs written to a self-hosted service's
container logs.

## 1. Needs a decision (legitimate, low impact)

| # | Alert(s) | Rule | Location | Finding | Verdict |
|---|----------|------|----------|---------|---------|
| 1 | 137 | py/partial-ssrf (critical) | `nora/platform/discord/roster.py:93` | `guild_id` parsed from `conversation_id` flows into `f"{base_url}/guilds/{guild_id}/members"`. Host is fixed to Discord's API and auth is the bot's own token — an attacker can only make the bot query a guild the bot is already in. Not reachable to internal addresses. | **Low** — real pattern, no real impact. Optional one-line hardening: `guild_id.isdigit()` (snowflake) before use. |
| 2 | 61–62, 70–80, 88–136 | py/log-injection (×62) | `web/voice.py`, `signal/send.py`, `signal/react.py`, `discord/{bridge_app,music_manager,music_tool_bridge,roster}.py`, `infra/voice/hub.py`, `app/voice_events.py` | Every flagged line logs platform-assigned identifiers — `guild_id`, `channel_id`, `conversation_id`, `playback_id`, `event_id`, `request_id`, `op` — plus a few internal `reason` strings. Discord snowflakes are server-assigned, not user-chosen. `send.py:436` is explicitly documented as R-18-aware (identifier at rest, operator-grepped). | **Low** — the pattern is real (request-derived values reach log calls), impact is forged log lines / ANSI noise in journald for a self-hosted bot. If you want them gone, strip `\n`/control chars on the handful of free-text fields (`reason`), not all 62 sites. |
| 3 | 15, 83, 84 | go/log-injection (×3) | `deploy/instagram-bridge/.../errors.go:235,246`, `outbound.go:460` | Logs `route` (method+path) and numeric sender FBIDs into slog → container logs. Same class as above. | **Low** — dismiss with the log-injection batch or leave. |
| 4 | 3, 4, 5 | js/bad-tag-filter ×2, js/incomplete-multi-character-sanitization | `site/scripts/csp-hashes.mjs:38,44` | Build-time script scanning the site's own generated HTML to emit CSP hashes. Regex misses `</script >` (whitespace before `>`) — worst case a missed hash, which CSP handles fail-closed (script blocked), not an XSS hole. Input is own build output, not user input. | **Low** — fix is one char: `<\/script\s*>`. |
| 5 | 10 | js/missing-origin-check | `app/public/sw.js:85` | Service worker `message` listener (`skip-waiting`, `clear-caches`). Only same-origin pages that control the SW can postMessage it; worst case is a page deleting its own caches. | **Low** — ignore or add `event.origin === self.location.origin` to silence it. |
| 6 | 12, 13 | js/file-system-race (×2) | `app/src/server/session.ts:26`, `site/scripts/fonts.mjs:36` | `session.ts`: statSync→readFileSync mtime cache on operator-owned `identity.toml` — a torn read means the allowlist change is picked up one request late. `fonts.mjs`: existsSync cache check in a build script. | **Low** — no attacker-writable file, no meaningful race. |
| 7 | 11 | js/regex/missing-regexp-anchor | `app/src/server/downloads/store.ts:264` | `TRANSIENT = /\.f\d+\.|\.converting\.|\.(part|ytdl)$/` — the mixed anchoring is deliberate (two anywhere-match alternatives, one end-anchored); it *excludes* yt-dlp partials from a directory listing. Over-broad exclusion is not a vuln. | **Low** — cosmetic; add a non-capturing group for clarity if desired. |
| 8 | 30–33 | py/insecure-protocol (×4) | `voice-lab/tts-cmp/{cost,gen_samples,latency_pinned,voices}.py` | `ssl.create_default_context()` flagged for "allowing TLSv1" — stale heuristic; modern Python/OpenSSL floor at TLS 1.2. One-off lab scripts hitting a pinned IP for api.minimaxi.com; no server, no cert verification disabled. | **Low / FP-ish** — dismiss. |

## 2. False positives — dismiss outright

| # | Alert(s) | Rule | Location | Why it's a false positive |
|---|----------|------|----------|---------------------------|
| 9 | 16–23 | py/clear-text-logging-sensitive-data (×8, high) | `nora/app/composition.py:404,916,1016,2024,2285,3227,3344,5809` | Every line logs the **name** of the env var (`api_key_env`, `secret_key_env`, `refresh_token_env`, `key_env`) — e.g. `route enabled but %s is unset`. No secret value is logged; CodeQL matched the identifier name. |
| 10 | 25–27 | py/clear-text-logging-sensitive-data (×3, high) | `nora/agent/whereis.py:482,501` | Logs `circle_id` (a config group name like "family"), a refusal reason, a count, and booleans. None are credentials. |
| 11 | 28, 29 | py/clear-text-logging-sensitive-data (×2, high) | `nora/infra/image_gen/xai_oauth.py:158,309` | :158 prints `user_code` to the operator's console — that IS the OAuth device flow's purpose (the human types it into the browser). :309 logs a fallback URL. |
| 12 | 24 | py/clear-text-logging-sensitive-data | `voice-lab/scripts/voices.py:6` | Lab script printing a voice list; flagged on a name heuristic. |
| 13 | 35–38 | py/overly-large-range (×4) | `nora/documents/check.py:209`, `scripts/pii_scan.py:207` | `pii_scan.py` uses `\Uxxxxxxxx` 8-digit astral escapes which CodeQL mis-parses as `\u`+literal text, inventing overlapping ranges — verified the pattern compiles and matches only the intended invisible chars. `check.py`'s `\x0e-\x1f` is exactly the documented "C0 controls minus tab/LF/CR". |
| 14 | 39–51 | py/incomplete-url-substring-sanitization (×12, high) | `tests/` only: `test_file_links_render_as_handles.py`, `test_instagram_parser.py`, `test_media_fetch.py`, `test_site_csp_*.py`, `test_video_generation.py`, `test_web_fetch_tool.py` | All inside test assertions (`assert "instagram.com" in url`), not sanitization. Production URL checks deliberately use exact-host matching (`INSTAGRAM_HOSTS` frozenset, linkedin host rules) — the module docstrings even cite the substring attack as what they avoid. |
| 15 | 1 | js/incomplete-url-substring-sanitization | `deploy/voice-sidecar/test/redact.test.mjs:27` | Test asserting redaction output — the substring check is the assertion, not a sanitizer. |
| 16 | 52 | py/bad-tag-filter | `tests/unit/test_cognition_render_goldens.py:68` | Regex helper in a test matching `<tag ...>` shapes in renderer goldens — test-only, and the tags it matches aren't `<script>` anyway. |
| 17 | 53, 54 | py/overly-permissive-file (×2, high) | `deploy/computer/computerd.py:249,382` | :249 chmods a **directory** to 0755 — traversal intended and commented (`# noqa: S103`); per-scope subdirs are 0700. :382 creates a 0644 file **inside a 0700 scope dir** — unreachable by other local users. Both deliberate. |
| 18 | 55–60 | py/overly-permissive-file (×6, high) | `tests/unit/test_config_cli.py`, `tests/contract/test_oauth_lane_contract.py`, `tests/infra/test_snapshot_export_private.py` | Tests deliberately create loose-permission fixtures: the oauth test's whole point is proving the code narrows a restored 0644 file to 0600 before reading its token; the snapshot test makes a non-private dir to prove it's refused. Working as designed. |
| 19 | 6 | js/user-controlled-bypass | `app/src/server/actions.ts:36` | The "bypassable" guard is the intended optimistic-concurrency check on `signOut` — a mismatched `expectedSessionId` skips signing out the caller's *own* newer session. It can only ever affect the caller's session. |
| 20 | 7 | js/user-controlled-bypass | `deploy/voice-sidecar/src/controller.mjs:575` | `if (!await authorizeGuild(guildId)) return 403` IS the authorization check — flagging "a user value controls a guard" is flagging how authz works. The whole server additionally requires a bearer token (line 568). |
| 21 | 63 | py/jinja2/autoescape-false | `evals/retrieval_bakeoff/wemm_normaliser_golden.py:47` | Renders a tokenizer chat template to produce eval **text**, not HTML — explicitly marked `# noqa: S701`. |
| 22 | 8 | js/log-injection | `app/dev/mock-nora.mjs:2281` | Dev mock server, not shipped. |
| 23 | 14 | js/http-to-file-access | `site/scripts/fonts.mjs:36` | Build script writing a font fetched from a hardcoded `https://github.com/google/fonts/...` URL into `.cache/`. No user input anywhere. |

## Suggested dismissals (GitHub UI → Dismiss alert)

- **False positive**: alerts 16–29 (sensitive-data logging), 35–38 (regex ranges), 52, 63.
- **Used in tests**: 39–51, 55–60, 1, 8, 24.
- **Won't fix / low impact**: 53, 54 (commented deliberate perms), 3–5, 10–15, 30–33, 61–62, 70–84, 88–136, 137, 6, 7 — i.e. everything left; or fix the four cheap ones (§1 items 1, 4, 5, 7) first so the board reads cleaner.

## To reduce future noise

`.github/codeql/config.yml` runs `security-extended` with no path filters.
Adding a `paths-ignore` for `tests/**`, `voice-lab/**`, `evals/**`,
`site/scripts/**`, `app/dev/**`, `deploy/**/test/**` would have prevented
~40 of these 122.
