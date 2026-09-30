---
type: design
title: "Nora's computer — a coding agent's toolkit, executed in a microVM"
description: "Early study. Nora stays a trusted container; the computer-use tools run in one persistent Docker Sandboxes (sbx) microVM that serves a small HTTP API (computerd) with Nora as its only client. A host unit owns lifecycle. Smith and the make_artifact tool are retired: artifacts become a runtime skill over the computer tools, published by computer_export(to=link) through the existing ArtifactIngest port to Drop, revised in place from a persistent workspace. Threat model follows content, not users. Includes the live exposure and the first sbx spike, both measured 2026-09-30."
tags: [nora, compute, sandbox, sbx, computer-use, smith, drop, rustfs]
timestamp: '2026-09-30'
status: draft
---

# Nora's computer

Status: **early study for computer use; nothing decided.** Operator direction
(2026-09-30):

- **Nora remains a container** and stays the trusted control plane. She is
  not moved into a VM (§1 says why that would protect the wrong thing).
- **Only the computer-use tools run in the microVM.** Nora gets a coding
  agent's toolkit (run commands, read, write and edit files, drive a browser,
  build and share an artifact), executed somewhere she can afford to lose.
- **Smith is deprecated, and `make_artifact` becomes a skill** (`document-creation`)
  over the computer tools, with Drop behind `computer_export` (§6).
- **Target runtime: Docker Sandboxes (sbx)**, behind a `SandboxRuntime` port,
  pending the spike (§9).
- **Files: RustFS is reused** through Drop for published artifacts and
  through the `BlobStore` port for private exports. The VM's disk is the
  working disk.

## 0. Live exposure (measured on norty, 2026-09-30)

This stands regardless of the runtime. `[compute] enabled = true` is live. A
container started the way `LocalDockerBackend` starts Smith (`--network
nora-compute`, `host.docker.internal:host-gateway`) got an **unauthenticated
200 with 5,248 bytes of workspace data from Honcho**
(`:8000/v3/workspaces/list`). It also reached OmniRoute, Langfuse, Ollama,
Neo4j, sshd, norty's Tailscale address, the LAN router and the internet.
`nora-compute` is `Internal=false`, and Smith is handed Nora's own OmniRoute
key. S0 (§13) closes this before anything else is built: PR #631, with #632 (Smith holds Nora's key) and #633 (Honcho open on the LAN) tracked separately.

## 1. Threat model: content, not users

Nora lives in a social unit, not a workforce. Friends are not the adversary.
**Content** is untrusted: links people post, attachments, public social
pages, and anything a browser loads. Trust follows where a byte came from,
not who asked.

It keeps these rules:

- The VM reaches **nothing of Nora's**: no Honcho, Postgres, Valkey, RustFS,
  Drop, OmniRoute, LAN or Tailscale. Egress is the public internet under a
  policy.
- **No master credential in the VM.** Third-party API keys enter as sbx
  placeholders (§8).
- **Persistence is data, not environment.** The VM can be recreated from its
  image at any time without losing anything Nora remembers.

It drops these, compared with the first draft: lease fencing, host btrfs
snapshots, a mandatory worker between Nora and every command, and a home-made
egress proxy. Tool output enters Nora's context as a tagged block that says
what it is and where it came from (ADR 0012/0013), the same risk class
`web_fetch` and `x_read` already carry.

**Why not run Nora herself in the microVM?** A VM around Nora protects norty
*from* Nora. The threat here is content reaching Nora. In one VM,
model-written code would sit next to her model key, database credentials,
Honcho and the Signal/WhatsApp/Instagram bridges, so it could post as her.
Her job is being connected, so that VM's egress policy would have to allow
exactly the services it exists to block. It would also move Postgres onto a
VM disk (sbx local volumes are cloud-only, §9), rework every deploy path, and
make all of Nora depend on a Docker login.

## 2. Topology: a microservice with one client

```text
nora container                     norty host                        microVM "nora-computer"
──────────────                     ──────────                        ───────────────────────
computer_* tool
  → SandboxRuntime port
  → HTTP + bearer token ─────────→ sbx published port ────────────→ computerd (HTTP API)
                                    (docker-bridge IP only)            /workspace/<room>/...
  ← typed result ←──────────────────────────────────────────────────  Chromium (kept warm)

computer_export(to="link")
  → ArtifactIngest (existing port) → drop:8081 (nora network) → RustFS + artifacts row

lifecycle (never on the data path)
  computer-lifecycle.service ── sbx create / start / stop / rm  (ensure, idle-stop, recreate)
```

- **Direction:** Nora calls the VM; the VM can call nothing of Nora's.
  `computerd` is a server with no client credentials of its own.
- **`computerd`:** about 300 lines (FastAPI + Playwright), baked into the
  image. It keeps Chromium alive across calls, which is why a long-lived
  server beats one `sbx exec` per call. Its bearer token is set at `sbx
  create` and held by Nora. A token inside the VM is acceptable, because it
  only authorises calls *into* the VM.
- **Published port:** `sbx ports --publish <docker-bridge-ip>:P:8080`, so it
  is reachable from the nora container but not from the LAN or Tailscale. It
  is not bound on `0.0.0.0` (§0 shows what that exposes).
- **Lifecycle unit** (host systemd, the only thing that runs `sbx`):
  `ensure` (create from image if absent, start if stopped), stop after N idle
  minutes, and recreate on a failed health check or an image upgrade. Nora
  asks it to ensure over a unix socket and never holds the sbx or Docker
  socket.

## 3. `SandboxRuntime` port

It has the same shape as `ComputeBackend`. It **never raises**, and results
keep R-27's states: `ok` / `nothing_found` / `unavailable` / `denied`. A
non-zero exit is `ok` carrying the exit code, and a timeout is `unavailable`.

```python
class SandboxRuntime(Protocol):
    async def run(self, scope: Scope, command: str, *, timeout_s: int = 120) -> RunResult: ...
    async def read(self, scope: Scope, path: str, *, offset: int = 0, limit: int = 2000) -> FileResult: ...
    async def write(self, scope: Scope, path: str, data: bytes) -> OpResult: ...
    async def edit(self, scope: Scope, path: str, old: str, new: str) -> OpResult: ...
    async def files(self, scope: Scope, pattern: str = "**/*") -> ListResult: ...
    async def fetch(self, scope: Scope, path: str, *, max_bytes: int) -> BytesResult: ...  # out of the VM
    async def put(self, scope: Scope, path: str, data: bytes) -> OpResult: ...            # into the VM
    async def browse(self, scope: Scope, action: BrowserAction) -> BrowserResult: ...
    async def status(self) -> RuntimeStatus: ...
```

- `Scope = (room_id, turn_id)`. **Nora's code picks the room from the turn,
  never the model.** `computerd` resolves paths under `/workspace/<room>/` and
  refuses any that escape. That is disclosure hygiene (one room's files do
  not surface in another), not a security boundary, because everything shares
  one VM.
- Output is bounded (head/tail, reusing `LOG_TAIL_LINES` /
  `MAX_ERROR_LOG_CHARS`), and the full stream is kept by key.
- Implementations: `ComputerdRuntime` (HTTP client, sbx-agnostic: the same
  client works against any VM or container running `computerd`) and
  `FakeSandboxRuntime`, with one shared contract suite. **Swapping sbx for
  Firecracker, Kata or plain Docker changes the lifecycle unit, not the
  port.**

## 4. Tools Nora gets

This is Claude Code's shape, as a **deferred group** loaded through
`load_tools` when a task needs a computer, so her other ~80 tools do not pay
the routing cost.

| tool | does |
|---|---|
| `computer_run(command, timeout?)` | bash in the room's workspace: python/uv, node, git, ffmpeg, curl. One tool, not one per language |
| `computer_read(path, offset?, limit?)` | text as a tagged, bounded block; images and PDFs go through her existing vision path |
| `computer_write(path, content)` / `computer_edit(path, old, new)` | create or patch files; edit makes multi-step work cheap |
| `computer_files(pattern?)` | list or glob the workspace |
| `computer_browse(action, …)` | `goto` / `snapshot` (accessibility tree) / `click` / `type` / `screenshot`, the Playwright-MCP shape, tree first |
| `computer_import(ref)` | chat attachment, media ref or blob key → `inbox/` |
| `computer_export(path, to, title?, artifact_id?)` | `to="link"` → Drop (new link, or the same link revised); `to="chat"` → sent as an attachment to the room; `to="keep"` → private blob + manifest (§6, §7) |

`make_artifact` is **no longer a tool**. It becomes the `document-creation` runtime
skill (§6).

- **Stay as they are:** `web_fetch`, `calculate`, `generate_image` and
  `media_*`. They are cheaper, and none of them needs a computer.
- **`computer_browse`** is for pages that are JavaScript-heavy, interactive
  or behind a login.
- **Measurement:** eight tools is a real routing cost (ADR 0015), so the
  `tool_selection` measurement runs before and after they are added.

## 5. The VM image

It is a Dockerfile, like Smith's today. Whether it must start `FROM
docker/sandbox-templates:*` is untested (§9).

- Ubuntu base, Python 3.12 + uv, Node, bash/coreutils/git/curl/jq, sqlite.
- ffmpeg, imagemagick, pandoc, poppler (PDF to text).
- Chromium + Playwright + Xvfb, **Noto CJK fonts** (otherwise every Cantonese
  page renders as boxes).
- **Smith's renderers, carried over as CLIs:** `nora-render
  mermaid|markdown|map|report IN OUT`, so Nora or a plain script
  can all use them through `computer_run`. `render_map` needs the Google Maps
  key, which enters as an sbx placeholder (§8).
- `computerd`, started by the image's entrypoint.

## 6. `make_artifact` ↔ Drop

**Today:**

- Smith runs inside the sandbox holding a per-job Drop token (minted by
  `LocalDockerBackend` through `POST /api/tokens`).
- Smith POSTs or PATCHes `/api/artifacts` on Drop directly, over
  `nora-compute`.
- It writes `url.txt`, which Nora reads back on trust.
- Drop stores the bytes in RustFS and the row in the `artifacts` table.

So the sandbox needs a network path to Drop and a credential for it, and
Nora never sees the bytes she is linking to.

**Proposed: `make_artifact` becomes a skill, and Drop sits behind
`computer_export(to="link")`. The VM only produces files; Nora publishes.**

`make_artifact` stops being a tool. Building an artifact is a *procedure*
over the computer tools, so it becomes the `document-creation` runtime skill (ADR
0016). The skill covers:

- open or create `projects/<slug>/`;
- write the source compactly (markdown, data, a small template), not
  hand-written HTML;
- render with `nora-render`;
- check the output (`computer_read` / a screenshot);
- `computer_export(path, to="link")`.

Revising an artifact is the same skill with `artifact_id`.

- **Drop stays a separate service; only its tool face moves.** Drop is the
  public server with its own hardening, and it must never live in the VM,
  because a public-facing renderer inside the untrusted machine is exactly
  the exposure this design removes. `computer_export(to="link")` is the only
  way Nora reaches it.
- **Must-have rule, not only in the skill.** A skill that is optional gets
  skipped: `image-generation` was skipped 3 times in 6 live turns. So
  `computer_export`'s description says "to give people a file they open by
  link, follow the `document-creation` skill", and doctrine carries the one-line rule.
- **It fits in one turn: measured, but small n.**
  - 15 artifacts have ever been made (2026-08-12 → 09-04; none in the 26 days
    since).
  - 4 of them outran the 15 s sync window and finished a mean 43 s later (max
    80 s done, 151 s failed).
  - The tag turn cap is 360 s and 20 model turns.
  - So the async path is not justified by evidence. Removing it retires
    `ComputeBackend`, `ComputeJobPoller`, `compute_jobs` and the sync-window
    logic, because `make_artifact` is their only user.
  - If long builds appear, a generic background task (not artifact-specific)
    is the thing to add.
- **The ceiling that binds is output tokens, not time.** The live loop budget
  is 12,000 output tokens per tag (`[llm.budgets]`), and that ceiling binds
  silently. Nora writing a full HTML report inline would spend most of it.
  This is why the skill says "write markdown or data, render with
  `nora-render`". Measure `output_tokens` on real artifact turns before
  deciding whether that budget needs a computer-specific allowance.

The export path:

```text
computer_export(path, to="link", title, artifact_id?)
  1. SandboxRuntime.fetch(scope, path, max_bytes)   bytes out of the VM
  2. validate on Nora's side:
       size cap · MIME sniffed from bytes (not the extension) · type allowlist
       (html, svg, png, jpeg, pdf, csv, md, json) · sha256 computed HERE
  3. ArtifactIngest.register(data, title, media_type)      new link
     ArtifactIngest.update(artifact_id, data, …)           same link, new bytes
  4. manifest row: room, path, sha256, artifact_id, url, turn
  5. return {status: ok, url, artifact_id}
```

- **The publishing code already exists:**
  - The `ArtifactIngest` port and `nora.infra.drop.DropArtifactIngest`
    (register + update, contract-tested against `FakeArtifactIngest`) were
    built for Compute.
  - Their docstring records **no caller yet**, because Smith talks to Drop
    directly instead.
  - This design is that caller. Nora already reaches `drop:8081` over the
    `nora` network for the link shortener.
- **Drop stays the single writer** of the artifacts bucket in RustFS and of
  the `artifacts` table, and the only public server (`/drop/{slug}` with its
  existing hardening). Nothing else writes RustFS for published artifacts.
- **Retired:** `POST /api/tokens` (its only caller is
  `LocalDockerBackend._mint_token`), Drop's membership of `nora-compute`, and
  `url.txt`/`summary.txt`. The sha256 Nora computed is the provenance, so the
  VM's claim about what it built is never trusted.
- **Revision gets better.** `artifact_id` → manifest → the workspace path and
  project that built it. "Make the chart blue" becomes: open the same project,
  edit, re-share with the same `artifact_id`, and the link stays the same.
  Smith rebuilt every revision from scratch in a fresh container, with only
  the brief.
- **Provenance in Drop (optional):** add `origin`, `room` and `sha256`
  columns to `artifacts` through Nora's alembic chain, which owns that table.

## 7. Files: the VM disk for work, RustFS at the edges

- **Working disk:** the VM's own disk under `/workspace/<room>/`. It needs
  real POSIX for pip, sqlite, git and Chromium profiles; no FUSE.
- **Published artifacts:** go to Drop, and so to RustFS (§6).
- **Private exports** (kept, not linked): `fetch` → `BlobStore.put`, keyed by
  content hash, with a manifest row. **This is RustFS already.**
  `RustfsBlobStore` exists (DEC-136) and is live on norty
  (`[media.rustfs] enabled = true`, bucket `nora-media`, read 2026-09-30).
  So published and private files both land in RustFS, through Drop and
  through `BlobStore` respectively, and Nora needs no new client.
- **Import:** `computer_import(ref)` → `BlobStore.get` or a media ref →
  `SandboxRuntime.put` into `inbox/`. The VM never needs a route to RustFS.
- **Backup:** a nightly job tars each room's `projects/` (not `browser/`,
  which holds cookies) through `fetch` into the blob store. It is on the same
  host, so this is rollback, not off-host backup.
- **Manifest:** a small table (room, path, sha256, origin, artifact_id, url,
  time). "Continue with `data.csv` tomorrow" resolves through it. A file
  missing from the VM is `unavailable`, never an empty answer.

```text
/workspace/<room>/
├── inbox/              imports (read-only)
├── projects/<slug>/    code + data + out; .venv with a lockfile persists here
└── browser/<profile>/  Chromium user-data-dir (a credential store; never backed up)
```

## 8. Credentials and network (from sbx)

- **Network:** `sbx policy init deny-all`, then `sbx policy allow network`
  for package mirrors, and the public web for browsing. `sbx policy log
  --json` supplies the network half of the audit.
- **Credentials:** `sbx secret set-custom --sandbox nora-computer --host
  maps.googleapis.com --env GOOGLE_MAPS_API_KEY --value …`. The VM sees a
  placeholder, and sbx's proxy substitutes the real key only on requests to
  that host.
- **Browser logins** are the exception: cookies live in the VM. Use Nora's
  own low-value accounts only, one profile per room.

## 9. sbx spike, 2026-09-30 (norty, sbx v0.46.0)

Method: `apt-get download docker-sbx` then `dpkg -x` into `~/sbx-spike` (no
sudo from this session), running as user `norty`.

| gate | result | evidence |
|---|---|---|
| installable | **yes**: `0.46.0-1~ubuntu.26.04~resolute` is in the configured Docker apt repo | `apt-cache policy` |
| how it runs an image | an OCI image through containerd's `nerdbox` shim, booted in a microVM with its own bundled kernel (`nerdbox-kernel`, 37 MB) and an EROFS guest rootfs; the same model as Kata | package contents |
| KVM access | **blocked**: `/dev/kvm` is `root:kvm 0660`; `norty` is not in `kvm` | `getfacl` |
| headless daemon | **works around**: fails on the locked gnome-keyring (`the OS keychain is locked`); starts with `DBUS_SESSION_BUS_ADDRESS` pointed at nothing (file-store fallback). A systemd unit needs that | `daemon-stderr.log` |
| **Docker account required** | **yes, even locally**: `ls`, `policy ls` and `create` all return `401 … not authenticated to Docker … try: sbx login`; the policy governor fetches from Docker at start | CLI, `daemon.log` |
| non-interactive control | on paper yes: `create --name`, `exec`, `cp`, `ports --publish`, `stop`, `rm -f`, `ls --json`, `policy log --json`, `secret set-custom`, `login --password-stdin` | `--help` |
| custom image | yes: `create -t`, `template load`/`save`; built-in defaults are `docker/sandbox-templates:*`, and whether a non-derived image works is untested | `--help`, binary strings |
| caps | CPU and memory yes; **no local disk cap found** | `create --help` |
| persistent volumes | **cloud-only** | `--help` |
| deny host/LAN, placeholder substitution, stop/start keeps disk, boot time, Chromium RSS, token revoked mid-run | untested (needs a VM) | — |

To finish (operator, on norty):

```bash
sudo apt install docker-sbx
sudo usermod -aG kvm norty          # then a fresh login session
echo "$DOCKER_PAT" | sbx login --username <docker-user> --password-stdin
```

**Fallback** if the Docker-account dependency is unacceptable: the same
`computerd` image under Kata, Firecracker, or Docker + gVisor, with a small
egress proxy doing what §8 gets from sbx. The port (§3) and the tools (§4) do
not change, and only the lifecycle unit does. That costs roughly one more
week.

## 10. Effort (sbx path, if the remaining gates pass)

| step | estimate |
|---|---|
| finish spike | 0.5 day |
| image + `computerd` (run/read/write/edit/files/fetch/put/browse) | 2 days |
| lifecycle unit (ensure, idle-stop, recreate, health) | 0.5 day |
| `SandboxRuntime` port, `ComputerdRuntime`, fake + contract suite | 1 day |
| tools + manifest table + `tool_selection` before/after | 2 days |
| `computer_export` (link/chat/keep) on `ArtifactIngest`; `document-creation` skill + doctrine line | 1 day |
| retire Smith, `make_artifact`, `LocalDockerBackend`, `/api/tokens`, `nora-compute`, `docker.sock`, and the ComputeJob path. The last one reaches further than the tool: `ComputeBackend`, `compute_poller.py`, `compute_jobs` + migrations, `fire.py`'s compute occasions, `agent/relay.py` and `registry.py`, `subject/knowledge.py`, the `nora_compute_jobs_delivered_total` metric, and their tests | 2 days |

This totals about 9 days. The figures are estimates, not measured.

## 11. Inside vs outside

| where | what |
|---|---|
| **nora container** | intent, governance, memory, identity, secrets, the manifest, publishing (ArtifactIngest), composing replies |
| **Drop** | the only writer of published artifacts (RustFS + `artifacts`), the public renderer |
| **lifecycle unit** | `sbx` create/start/stop/rm; no data path |
| **sbx daemon** | microVM, egress policy, placeholder secrets |
| **nora-computer VM** | `computerd`, files under `/workspace/<room>`, processes, toolchains, Chromium + profiles |
| **never in the VM** | Nora's model key, a Drop credential, any route to Honcho/Postgres/RustFS/Drop, the docker or sbx sockets |

## 12. Audit

`computer_events` in Postgres: `ts, room, turn, tool, command or path, status,
exit_code, duration, bytes, output_key, sha256`. It is written by Nora's
port, not by `computerd`. sbx's `policy log --json` is pulled in for network
events, and each Langfuse span carries the event id. Any claim about what ran
or what was published re-derives from the table plus Drop's `artifacts` row,
never from the model's account.

## 13. Sequence

- **S0: close §0 now, independent of all of this.** Make `nora-compute`
  `internal: true` behind an allowlist (OmniRoute, OTLP, Drop,
  `maps.googleapis.com`), put auth on Honcho or bind it off `0.0.0.0`, and
  give Smith its own key. Falsifier: rerun the §0 probe, and every
  non-allowlisted row is refused.
- **S1:** finish the spike.
- **S2:** image + `computerd` + lifecycle unit + port; `computer_run` and the
  file tools behind `[computer] enabled`.
- **S3:** `computer_export` on `ArtifactIngest` + the `document-creation` skill;
  `make_artifact`, Smith, `LocalDockerBackend`, the ComputeJob machinery,
  `/api/tokens` and `docker.sock` removed. Measure `output_tokens` on the
  first real artifact turns.
- **S4:** `computer_browse`, then persistent browser profiles per room.

## Open decisions (operator)

1. Is a Docker account login acceptable as a dependency of Nora's computer?
2. One VM with a directory per room (proposed), or one VM per room?
3. Which accounts, if any, may stay logged in inside the browser?
4. Retire the ComputeJob async path with `make_artifact` (proposed, per the
   measured durations), or keep it for a future background task?
