# Nora's public implementation wiki

OpenWiki owns the source-grounded Markdown, Claims and update state in
`openwiki/`. Astro Starlight renders factual pages at `/wiki/`; the source
brief, logs, Claims and run metadata are not web routes. Its optional graph
is exported with OpenWiki at `/wiki/graph/` and uses the upstream pinned CDN
rendering libraries. Ordinary documentation and search are static. Mermaid fences
in the Starlight reader use the bundled, lazily loaded renderer and follow its
light/dark theme. Flowcharts, sequence diagrams and state diagrams retain readable
intrinsic sizing with contained horizontal scrolling; loading uses a static background. The build checks every factual page against its native Mermaid
block count so missing rendering integration fails publication. Mermaid’s KaTeX
dependency is pinned to the patched version in the package override.

```bash
npm ci --prefix docs-site
npm run build --prefix docs-site
npm run dev --prefix docs-site
```

The site Dockerfile includes this build in the existing public site image.
Caddy serves `/wiki/` from `/srv/wiki/`. Route `/wiki` and `/wiki/…` to the
existing site port 3401 in the Cloudflare Tunnel, before the portal catch-all.
`SITE_ORIGIN` changes canonical URLs, not the fixed `/wiki/` path.

## Generate and review

The technical wiki was initialized locally by native OpenWiki 0.7.2 using
`openwiki code --init --print`, through OmniRoute and Ollama Cloud's
`deepseek-v4.1-flash:1017`, with high reasoning and one page worker. Its 29-page
queue completed after resuming the durable local checkpoint. The generated
prose, Claims and completion metadata are copied together without rewriting
their recorded source revision. This replaces the initial seven-page seed.

The wiki root is a Starlight splash page with existing Mini Nora art and
curated reading paths. It renders directly at `/wiki/`, retaining the built-in
search and theme controls; quickstart remains the technical routing guide.

The reader discovers pages from the generated topic directories. Previous
seed-page URLs redirect to their technical replacements. Repository source
references remain readable labels; source files are private and are not public
routes. The same adaptation applies to the exported graph. Build rejects an
active or incomplete native run before rendering.

For native generation, set the provider environment and run
`npm run wiki:update --prefix docs-site`. Always use `openwiki/INSTRUCTIONS.md`
and `.openwikiignore`; do not edit managed Claims or update metadata manually.
Review against current source and tests. A successful run verifies its evidence
bookkeeping, not semantic accuracy or the live deployment.

The install hook applies `patches/openwiki-0.7.2.patch` to the pinned native
dependency using Node's standard library. It checks the package version and
every input/output file's SHA-256, refuses unexpected bytes before writing, and
is safe to rerun. After an install with `--ignore-scripts`, run
`npm run postinstall --prefix docs-site` explicitly (the site Dockerfile does).
Reassess or remove the patch when upgrading OpenWiki.

On resume, a completed job with valid verified Claims but changed Markdown is
reopened in its existing queue. Its content, Claims IDs, run ID and source
checkpoint are retained; its Claims require explicit confirmation, revision or
retraction through native `submit_page`. Malformed Claims, missing Markdown
and I/O errors still fail. No hash is refreshed merely to allow resume.
Unresolved drift outside a saved plan also retains its last verified sidecar
hash across other submissions and failed finishes. Retrying cannot publish
that page without explicit Claim reconciliation; the saved queue stays intact.
Each page worker orders its tool calls and serializes reads and writes with
submission.
Successful submission revokes its writes, including queued operations, and
ends the worker before another model call; rejection still permits repair and
retry. Ordinary backends retain the native shared path queue and its read/write
admission order. Separate page workers remain concurrent. This is process-local
worker coordination; it does not lock out external editors or other CLI processes.

`node --test docs-site/scripts/openwiki-native.test.mjs` exercises the native
stores and worker with synthetic data and an in-process scripted model. It
checks recovery and submission behavior, not model acceptance or wiki accuracy.

## GitHub Actions

`ci.yml` selects relevant PR and master-push changes and calls the reusable
`wiki.yml` to build the wiki and public homepage and upload the combined static
site. `wiki.yml` also supports manual dispatch. No model credentials or inference
are used by that workflow. Image deployment stays the existing operator process.

`openwiki-update.yml` generates from reviewed `master`, builds the result and opens
or updates one documentation PR. It never auto-merges. The existing repository
GitHub App supplies the write token after the model exits, so the PR triggers
normal checks. Failure stops before a PR is created; partial runs are not
presented as completed updates. A weekly run is off until the operator sets
`OPENWIKI_AUTO_UPDATE=true`; manual dispatch works once credentials exist.
For a scope expansion without runtime source changes, supply the manual
dispatch's optional `request` field. OpenWiki uses it as planning context and
forces a fresh plan. Ordinary scheduled updates leave it empty.
The manual `diagnostics` switch defaults to true; scheduled runs also enable
structural diagnostics. It reports model finish reasons and
tool-call counts from JSON provider responses. It logs no prompts, response
text, tool arguments, credentials or arbitrary error/metadata values.
It also reports malformed argument-JSON counts, repeated-ID booleans/counts
without the IDs, and closed tool/error classifications when tool invocation
throws. These observations do not change responses, retry tools or repair calls.
Print mode now emits native stage/tool start, end and error metadata immediately,
including elapsed milliseconds and counts, even when generation throws. The
workflow leaves broad debug mode unset; structured stage/tool metadata remains
available. Authored CLI output and original exceptions retain their handling.

Native generation preloads `provider-compat.mjs`. Successful JSON chat-completion
responses receive a fresh completion ID before LangGraph consumes them: reused
provider IDs otherwise replace earlier assistant messages in the conversation.
The match is the configured `OPENAI_COMPATIBLE_BASE_URL` origin and full
`chat/completions` path, including a base path, plus the direct Ollama default.
Tool-call IDs, content and arguments remain intact; other endpoints and SSE
responses are unchanged. This shim covers the current non-streaming setup.
Optional diagnostics still observe the original response before normalization.
See [Ollama's small completion-ID pool](https://github.com/ollama/ollama/issues/18655).

For a local gateway, export its `OPENAI_COMPATIBLE_BASE_URL`,
`OPENAI_COMPATIBLE_API_KEY` and gateway-qualified `OPENWIKI_MODEL_ID` privately,
then run from the repository root:

```bash
OPENWIKI_PROVIDER=openai-compatible OPENWIKI_PROVIDER_DIAGNOSTICS=true \
  OPENWIKI_TELEMETRY_DISABLED=1 \
  NODE_OPTIONS='--import=./docs-site/scripts/provider-compat.mjs' \
  npm run wiki:update --prefix docs-site
```

The hosted default calls Ollama directly with an unqualified model ID; a local
gateway uses its own routing and may transform responses. The offline gateway
regression proves the endpoint-match fix, not live causation or a fix for every
hosted failure. Keep unattended updates disabled until a manual hosted generation
succeeds.

Generation has a 35-minute step timeout inside the 45-minute job. An always-run
finalizer writes only `summary.json` under the runner's private
`$RUNNER_TEMP/openwiki-safe-summary` directory and retains that single artifact
for three days. It contains outcome/exit code when available, fixed phase/status
values, checkpoint/plan presence and page queue counts. Missing or unreadable
state is explicitly unavailable; unavailable counts and exit codes are null.
Native `.run.json` may contain private planning context and instructions: it,
raw logs, source state and configuration are never uploaded. Failure or timeout
still skips completion checking, building and creation of a write token or PR.

Configuration:

- Secret `OPENWIKI_API_KEY`: Ollama Cloud key, never committed or printed.
- Existing `RELEASE_APP_ID` variable and `RELEASE_APP_PRIVATE_KEY` secret:
  repository App with Contents and Pull requests write access.
- Optional variables `OPENWIKI_BASE_URL` and `OPENWIKI_MODEL_ID` override the
  default `https://ollama.com/v1` and `deepseek-v4.1-flash:1017`.
  Reasoning effort is `high`, one of this model tag's advertised thinking levels.
- No local Ollama server is required. When overriding the provider endpoint,
  replace the secret with a key for that provider as well.

The default uses one page worker, high reasoning and a 32,768-token output
ceiling per model response; the job has a 45-minute wall clock. These are not a
currency budget. Native generation and each provider's multi-turn tool replay
must be checked in a bounded manual run before enabling unattended updates.
Endpoint compatibility alone does not prove successful generation or accurate
technical content.

Provider references: [Ollama compatibility](https://docs.ollama.com/api/openai-compatibility),
[Ollama's DeepSeek V4.1 Flash](https://ollama.com/library/deepseek-v4.1-flash).

## Retrieval later

OpenWiki's local MCP search/read tools already retrieve wiki sections with
full-text search. Public visitor answers and embeddings are not implemented.
The same reviewed Markdown and stable heading URLs can feed a separate answer
service later; the static reader does not need that service to run.
