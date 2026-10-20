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

Configuration:

- Secret `OPENWIKI_API_KEY`: provider key, never committed or printed.
- Existing `RELEASE_APP_ID` variable and `RELEASE_APP_PRIVATE_KEY` secret:
  repository App with Contents and Pull requests write access.
- Optional variables `OPENWIKI_BASE_URL` and `OPENWIKI_MODEL_ID` override the
  default `https://api.minimax.cn/v1` and `MiniMax-M3.1-Flash`.
- For the same model on Ollama Cloud, use `https://ollama.com/v1` and
  `minimax-m3.1-flash`, with its API key. No local Ollama server is required.

The default uses one page worker, medium reasoning and a 32,768-token output
ceiling per model response; the job has a 45-minute wall clock. These are not a
currency budget. Native generation and each provider's multi-turn tool replay
must be checked in a bounded manual run before enabling unattended updates.
MiniMax requires reasoning content to survive tool-call replay; endpoint
compatibility alone does not prove this. No output-quality ranking between
MiniMax and Ollama is claimed.

Provider references: [MiniMax OpenAI API](https://platform.minimax.cn/docs/api-reference/text-openai-api),
[Ollama compatibility](https://docs.ollama.com/api/openai-compatibility),
[Ollama's M3.1 Flash](https://ollama.com/library/minimax-m3.1-flash:cloud).

## Retrieval later

OpenWiki's local MCP search/read tools already retrieve wiki sections with
full-text search. Public visitor answers and embeddings are not implemented.
The same reviewed Markdown and stable heading URLs can feed a separate answer
service later; the static reader does not need that service to run.
