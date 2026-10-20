# Nora's public implementation wiki

OpenWiki owns the source-grounded Markdown, Claims and update state in
`openwiki/`. Astro Starlight renders factual pages at `/wiki/`; the source
brief, logs, Claims and run metadata are not web routes. Its optional graph
is exported with OpenWiki at `/wiki/graph/` and uses the upstream pinned CDN
rendering libraries. Ordinary documentation and search are static.

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

The initial wiki was written by the host coding agent using OpenWiki 0.7.2's
begin → plan → next-page → submit-page → finish lifecycle. The host supplies
research and prose; OpenWiki owns Claims, evidence versions and finalization.
No separate provider key is needed for that host-driven path. Install the
upstream Codex/Claude integration at user scope to repeat it; avoid installing
extra project skills into Nora's agent-skill directory.

For native generation, set the provider environment and run
`npm run wiki:update --prefix docs-site`. Always use `openwiki/INSTRUCTIONS.md`
and `.openwikiignore`; do not edit managed Claims or update metadata manually.
Review against current source and tests. A successful run verifies its evidence
bookkeeping, not semantic accuracy or the live deployment.

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
  default `https://ollama.com/v1` and `deepseek-v4.1-flash`.
- No local Ollama server is required. When overriding the provider endpoint,
  replace the secret with a key for that provider as well.

The default uses one page worker, medium reasoning and a 32,768-token output
ceiling per model response; the job has a 45-minute wall clock. These are not a
currency budget. Native generation and each provider's multi-turn tool replay
must be checked in a bounded manual run before enabling unattended updates.
Endpoint compatibility alone does not prove successful generation or accurate
technical content.

Provider references: [Ollama compatibility](https://docs.ollama.com/api/openai-compatibility),
[Ollama's DeepSeek V4.1 Flash](https://ollama.com/library/deepseek-v4.1-flash:cloud).

## Retrieval later

OpenWiki's local MCP search/read tools already retrieve wiki sections with
full-text search. Public visitor answers and embeddings are not implemented.
The same reviewed Markdown and stable heading URLs can feed a separate answer
service later; the static reader does not need that service to run.
