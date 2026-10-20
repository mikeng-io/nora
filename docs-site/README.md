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

`wiki.yml` builds the wiki and public homepage on relevant PRs and master
pushes, then uploads the combined static site. No model credentials or inference
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
The optional manual `diagnostics` switch reports model finish reasons and
tool-call counts from JSON provider responses. It logs no prompts, response
text, tool arguments, credentials or arbitrary error/metadata values.
It also reports malformed argument-JSON counts, repeated-ID booleans/counts
without the IDs, and closed tool/error classifications when tool invocation
throws. These observations do not change responses, retry tools or repair calls.

Native generation preloads `provider-compat.mjs`. Ollama's JSON chat-completion
responses receive a fresh completion ID before LangGraph consumes them: reused
provider IDs otherwise replace earlier assistant messages in the conversation.
Tool-call IDs, content and arguments remain intact; other providers and SSE
responses are unchanged. This shim covers the current non-streaming CI setup.
Optional diagnostics still observe the original response before normalization.
See [Ollama's small completion-ID pool](https://github.com/ollama/ollama/issues/18655).

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
