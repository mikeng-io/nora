---
type: design
title: "Nora's computer — a coding agent's toolkit, executed in a microVM"
description: "Early study. Nora stays a trusted container; the computer-use tools run in one persistent Docker Sandboxes (sbx) microVM that serves a small HTTP API (computerd) with Nora as its only client. A host unit owns lifecycle. Smith and the make_artifact tool are to be retired; the deferred-job outbox stays for delegate_task (decision 4, 2026-09-30): artifacts become a runtime skill over the computer tools, published by computer_export(to=link) through the existing ArtifactIngest port to Drop, revised in place from a persistent workspace. Threat model follows content, not users. Includes the live exposure and two sbx spike rounds, all measured 2026-09-30."
tags: [nora, compute, sandbox, sbx, computer-use, smith, drop, rustfs]
timestamp: '2026-09-30'
status: draft
---

# Nora's computer

Status: **early study for computer use; direction set, open decisions listed at the end.** Operator direction
(2026-09-30):

- **Nora remains a container** and stays the trusted control plane. She is
  not moved into a VM (§1 says why that would protect the wrong thing).
- **Only the computer-use tools run in the microVM.** Nora gets a coding
  agent's toolkit (run commands, read, write and edit files, drive a browser,
  build and share an artifact), executed somewhere she can afford to lose.
- **Smith is deprecated, and `make_artifact` becomes a skill** (`document-creation`)
  over the computer tools, with Drop behind `computer_export` (§6).
- **Target runtime: Docker Sandboxes (sbx)**, behind a `SandboxRuntime` port;
  spike round 2 passed every gate it tested, three untested (§9); the login
  was accepted as a dependency (ADR 0019).
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
what it is and where it came from (ADR 0012; ADR 0013 `a-block-says-what-it-is-and-its-type-says-it`), the same risk class
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
- **Published port:** `sbx ports --publish 172.17.0.1:P:8080` (docker0, the
  address measured in §9 round 2), so it is reachable from the nora container
  but not from the LAN or Tailscale. It
  is not bound on `0.0.0.0` (§0 shows what that exposes).
- **Lifecycle unit** (host systemd, the only thing that runs `sbx`):
  `ensure` (create from image if absent, start if stopped), stop after N idle
  minutes, and recreate on a failed health check or an image upgrade. Nora
  asks it to ensure over a unix socket and never holds the sbx or Docker
  socket.
- **`computerd` is started by the unit, not the image.** sbx's `shell` agent
  overrides the image `ENTRYPOINT`, and `sbx exec` **auto-starts** a stopped
  sandbox. So a start the unit did not make still happens, and the unit
  (re)starts `computerd` with `sbx exec` whenever it finds the VM running
  without it.
- **The health check reads an HTTP body** from `computerd`, never a TCP
  connect, which succeeds to any address under sbx (§9 round 2).

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
    async def files(self, scope: Scope, pattern: str = "**/*") -> ListResult: ...   # Nora's code only (state, export); no model tool
    async def fetch(self, scope: Scope, path: str, *, max_bytes: int) -> BytesResult: ...  # out of the VM
    async def put(self, scope: Scope, path: str, data: bytes) -> OpResult: ...            # into the VM
    # S4: the browser is reached over a CDP proxy through computerd; the
    # sub-agent's steps travel there, not through a port method per action (§14).
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

**Decided 2026-09-30 (operator), after checking Pi, Hermes Agent and Prime
Agent:** four shell and file tools, Pi's core set. All bash, no language
restriction: a language restriction is not a capability restriction, and the
VM is the boundary.

| tool | does |
|---|---|
| `computer_run(command, timeout?)` | bash in the room's workspace: python/uv, node, git, ffmpeg, curl. Listing and search (`ls`, `find`, `rg`) go here too |
| `computer_read(path, offset?, limit?)` | text as a tagged, bounded block; instead of `cat` for a file you will act on. Later: images to her vision path |
| `computer_write(path, content)` | a whole file, byte for byte, with no shell quoting; instead of a heredoc or `echo` |
| `computer_edit(path, old, new)` | one exact replacement, refused when `old` is missing or ambiguous; instead of `sed` |
| `computer_browse(objective, session?)` | S4: one call delegates a whole browser task to a sub-agent (§14) |
| `computer_import(ref)` | S3: chat attachment, media ref or blob key → `inbox/` |
| `computer_export(path, to, title?, artifact_id?)` | S3: `to="link"` → Drop (new link, or the same link revised); `to="chat"` → sent as an attachment to the room; `to="keep"` → private blob + manifest (§6, §7) |

Why the file tools are separate from `computer_run` (bearing from the three
agents, not copied): writing through bash means escaping (quotes, `$`,
backticks, a stray `EOF`), and editing through bash means `sed`, which
"succeeds" when it matches nothing. Pi's author gives the other reason:
models are trained on read, write and edit tools with these schemas. Prime
Agent's single persistent-kernel tool was rejected for Nora: a kernel that
outlives turns is hidden state, which §15 exists to make visible.

The **`computer-use` runtime skill** carries the judgement across these
tools (the write, run, read-the-error, edit loop; `projects/<slug>/` with a
lockfile; tmux for long jobs; when not to use the computer at all). Its
must-have rules also live in the tool descriptions and doctrine, because an
optional skill gets skipped (image-generation: 3 of 6 live turns). It lands
after the tools, written against the failures real tasks show (ADR 0016).
**Per-command limits** are a reserved config slot, deliberately not applied:
the operator judged the environment semi-trusted, and the audit log (§12) is
the required control.

`make_artifact` is **to stop being a tool** (S3). The deferred-job outbox and its poller stay, as the delivery path for a future `delegate_task` (decision 4). It becomes the `document-creation` runtime
skill (§6).

- **Stay as they are:** `web_fetch`, `calculate`, `generate_image` and
  `media_*`. They are cheaper, and none of them needs a computer.
- **`computer_browse`** is for pages that are JavaScript-heavy, interactive
  or behind a login.
- **Measurement:** every tool is a routing cost (ADR 0015), so the
  `tool_selection` measurement runs before and after each group lands.

## 5. The VM image

It is a Dockerfile, like Smith's today. It does **not** need to start `FROM
docker/sandbox-templates:*`: Smith's own image booted as a template (§9
round 2).

- Ubuntu base, Python 3.12 + uv, Node, bash/coreutils/git/curl/jq, sqlite.
- ffmpeg, imagemagick, pandoc, poppler (PDF to text).
- Chromium + Playwright + Xvfb, **Noto CJK fonts** (otherwise every Cantonese
  page renders as boxes).
- **Smith's renderers, carried over as CLIs:** `nora-render
  mermaid|markdown|map|report IN OUT`, so Nora or a plain script
  can all use them through `computer_run`. `render_map` needs the Google Maps
  key, which enters as an sbx placeholder (§8).
- `computerd`, started by the lifecycle unit through `sbx exec` (§2), since
  the entrypoint does not run.

## 6. `make_artifact` ↔ Drop

> **Superseded in part, 2026-10-01 (operator).** S3a is computer-use only.
> A document people open is the artifact work's (ADR 0020,
> `design/artifact/00-design.md`: a JSON spec the portal renders), with its
> own skill; there is no `document-creation` skill over the computer. What
> survives here: `computer_export` sends FILES (images, charts, CSV, PDF,
> data), never a web page; an SVG is attached or kept, never linked; and
> `nora-render` keeps `chart`, `mermaid` and `map`, which make pictures.

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
- check the output (`computer_read`; a rendered screenshot once S4 lands);
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
  - So artifacts do not need the async path (measured, small n = 15). The
    sync-window logic goes with `make_artifact`.
  - The job outbox, `ComputeJobPoller` and `compute_jobs` **stay**
    (decision 4): a future `delegate_task`, a generic background sub-agent,
    needs exactly that launch-now, deliver-later, survive-a-restart path.
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
- **To retire in S3:** `POST /api/tokens` (its only caller is
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
| custom image | yes: `create -t`, `template load`/`save`; built-in defaults are `docker/sandbox-templates:*`, and whether a non-derived image works was untested (answered in round 2: it does) | `--help`, binary strings |
| caps | CPU and memory yes; **no local disk cap found** | `create --help` |
| persistent volumes | **cloud-only** | `--help` |
| deny host/LAN, placeholder substitution, stop/start keeps disk, boot time, Chromium RSS, token revoked mid-run | untested in round 1 (needs a VM); round 2 measured all but Chromium RSS and the token | — |

### Round 2, after the operator's Docker login (same day)

Each result below was read from a command's output on norty. The spike's
sandboxes, allow rule, secret and template were removed afterwards, which
leaves norty at `default-deny-all`.

| gate | result | reading |
|---|---|---|
| login | `sbx ls --json` → `{"sandboxes": []}` | CLI |
| create, non-interactive | `sbx create --name spike1 -m 2g --cpus 2 -q shell </dev/null` → rc 0, `running`; runs over plain `ssh` with no TTY | `ls --json` |
| isolation | the guest runs its own kernel, `7.0.14` (the host runs `7.0.0-31`); Ubuntu 26.04 guest; 20 GB overlay root; 2 vCPU / 1,987 MiB as asked | `uname`, `df`, `free` |
| exec latency | 0.67 s for a warm `sbx exec` | timed |
| deny-all, data level | **held for every address probed.** Raw TCP `connect()` "succeeds" to every address (the host's LAN IP :22 and :8000, Tailscale :22/:443, the router, 1.1.1.1), but sbx's transparent stack closes it with no data: no SSH banner, and `curl --noproxy` gives `rc=52` (empty reply). Each attempt is logged as denied with `no applicable policies`. **A TCP-connect probe reads as OPEN here and is a false positive**; probe with a banner or an HTTP body | `sbx policy log` |
| DNS | an outside name does not resolve in the VM; HTTP(S) goes through sbx's proxy (`gateway.docker.internal:3128`), and a non-allowed host gets 403 | `getent`, `curl` |
| allow rule | `sbx policy allow network httpbin.org:443` → 200 for that host; `example.com` stays 403 | `curl` |
| placeholder secret | `secret set-custom --sandbox spike1 --host httpbin.org`: a request that carried the placeholder arrived upstream with the **real value** in `Authorization`, and a grep for the real value across the VM's env, `/etc`, `/home`, `/run` and `/tmp` found **0** hits (process memory and `/proc` not searched). The env var is set only in sandboxes created after the secret; an existing one needs the printed placeholder | httpbin `/headers` echo |
| file copy | `sbx cp` in and out, sha256 identical on both sides | `sha256sum` |
| persistence | a file written, `sbx stop` (0.6 s), then `sbx exec` **auto-starts** the sandbox (2.0 s) and the file is intact | timed |
| published port | `sbx ports --publish 172.17.0.1:18099:8080` → 200 from the host, from a container, and **from the `nora` container itself**; not reachable on the host's LAN IP | `curl`, urllib |
| custom image | `docker save smith:latest` → `sbx template load` → `create -t smith:latest --pull never` → rc 0 in 14 s, Strands importable. **A non-Docker-derived image works.** It ran as root (the image's user), and sbx's `shell` agent **overrides the image's entrypoint**, so `computerd` must be started by an exec (a `nohup … &` via `sbx exec` stayed up and served) or by a kit | `exec` |
| login lapse | **untested.** `sbx logout`'s help says it stops running local sandboxes; what an *expired* token or a Docker outage does to a running sandbox is unknown | `--help` |
| Chromium RSS, a disk cap beyond the 20 GB root | untested | — |

**What round 2 changes in this study:**

- §2's path is confirmed for a published port: Nora's container reached a
  stub HTTP server in the VM on the Docker bridge IP. A real `computerd` with
  its bearer token is not yet built.
- §8 behaved as described for what was probed: deny-all, per-host allow, and
  host-side placeholder secrets.
- §5 needs one change: the image's `ENTRYPOINT` does not run, so the
  lifecycle unit starts `computerd` with `sbx exec` after `create` and after
  every auto-start (or a kit is written).
- **A health check must not treat a TCP connect as proof of anything**, in
  either direction.

The one open risk is the login: decision 1 below, plus a deliberate
token-expiry test before anything depends on it.

**Fallback** if the Docker-account dependency is unacceptable: the same
`computerd` image under Kata, Firecracker, or Docker + gVisor, with a small
egress proxy doing what §8 gets from sbx. The port (§3) and the tools (§4) do
not change, and only the lifecycle unit does. That costs roughly one more
week.

## 10. Effort (sbx path; round 2 passed all gates tested, three untested)

| step | estimate |
|---|---|
| token-expiry test + Chromium RSS | 0.5 day |
| image + `computerd` (run/read/write/edit/files/fetch/put; browse is S4) | 2 days |
| lifecycle unit (ensure, idle-stop, recreate, health) | 0.5 day |
| `SandboxRuntime` port, `ComputerdRuntime`, fake + contract suite | 1 day |
| tools + manifest table + `tool_selection` before/after | 2 days |
| `computer_export` (link/chat/keep) on `ArtifactIngest`; `document-creation` skill + doctrine line | 1 day |
| retire Smith, `make_artifact`, `LocalDockerBackend`, `/api/tokens`, `nora-compute` and `docker.sock`. The job outbox, poller and `compute_jobs` STAY for `delegate_task` (decision 4); only the Smith-specific `ComputeBackend` adapter goes | 1 day |

This totals about 8 days for the core (S2a to S3). It excludes the audit
trail (§12), sessions (§15) and the browser (S4), which are sized when they
are built. The figures are estimates, not measured.

## 11. Inside vs outside

| where | what |
|---|---|
| **nora container** | intent, governance, memory, identity, secrets, the manifest, publishing (ArtifactIngest), composing replies |
| **Drop** | the only writer of published artifacts (RustFS + `artifacts`), the public renderer |
| **lifecycle unit** | `sbx` create/start/stop/rm; no data path |
| **sbx daemon** | microVM, egress policy, placeholder secrets |
| **nora-computer VM** | `computerd`, files under `/workspace/<room>`, processes, toolchains, Chromium + profiles |
| **never in the VM** | Nora's model key, a Drop credential, any route to Honcho/Postgres/RustFS/Drop, the docker or sbx sockets |

## 12. Audit: a complete trail, Postgres as the record

**Operator requirement (2026-09-30): every operation is recorded in the DB,
with who, when, how and what, as a full trail for auditing.**

- **Audit is not tracing.** Traces (OTel into Langfuse, or Jaeger) can be
  sampled, dropped by a failing exporter, and purged after days. Audit rows
  are complete, append-only (a trigger refuses UPDATE and DELETE), kept, and
  written in the same code path as the operation. Each row carries the OTel
  `trace_id` and `span_id`, so a row opens its trace. Nothing extra is
  deployed: tracing is what exists today, and the audit is one table.
- **`computer_events`, one row per operation:**
  - **who:** actor (`tag`, `fire`, `cognition`, `dreamer:<adaptor>`,
    `operator`), requesting person, platform, room, turn;
  - **when:** started and finished;
  - **how:** tool, path (`direct`, or `subagent` from S4), trace and span ids;
  - **what:** the full request (the whole command, the whole edit text); the
    result (status, exit code, reason, sizes, sha256s); the full output and
    read content in RustFS through `BlobStore`, referenced by key and sha256,
    so the row stays small and nothing is dropped; and a write's or edit's
    file sha256 before and after.
- **Written by Nora's side,** never by computerd: the VM is the less-trusted
  party, and its own logs are evidence, not the record.
- **Coverage by stage.** S2b-1 covers the four tools. S2b-2 adds session
  transitions, lease acquire, pre-emption and cancel, and lifecycle events
  (create, recreate, computerd restart, token rotation), plus sbx's
  `policy log --json` egress records. S3 adds import and export. S4 adds
  every browser sub-agent step (action, element, URL, screenshot key). An
  operation with no row is a gap in the trail, and is treated as a defect.

## 13. Sequence

- **S0: close §0 now, independent of all of this.** Make `nora-compute`
  `internal: true` behind an allowlist (OmniRoute, OTLP, Drop,
  `maps.googleapis.com`), put auth on Honcho or bind it off `0.0.0.0`, and
  give Smith its own key. Falsifier: rerun the §0 probe, and every
  non-allowlisted row is refused.
- **S1:** the token-expiry test and Chromium RSS (the spike's untested rows).
- **S2a:** image + `computerd` + lifecycle unit + port, inert (landed, #636).
- **S2b-1:** the four tools, `[computer]` config, `computer_events` (the
  audit), the per-command limits slot.
- **S2b-2:** sessions (§15): the observed-state read, `computer_sessions`, the
  `<computer>` block, leases, pre-emption and concurrency.
- **S3:** `computer_export` on `ArtifactIngest` + the `document-creation` skill.
  Then retire `make_artifact`, Smith, `LocalDockerBackend`, `/api/tokens` and
  `docker.sock`. The job outbox, its poller and `compute_jobs` stay as the
  delivery path for a future `delegate_task` (background sub-agent,
  decision 4).
  Measure `output_tokens` on the first real artifact turns.
- **S4:** the browser spike (§14), then `computer_browse`, then persistent
  browser profiles per room.

## 14. Browser: agent-as-tool, not a tool per click

A tool call per click would send Nora's whole prompt through a model round
trip for every action. Her turn allows 20 model turns and 12,000 output
tokens, so a 30-action checkout does not fit, and it would be slow and costly
anyway. The unit is coarser, in two tiers:

1. **A script.** For a flow she can describe, Nora writes a Playwright script
   and runs it with one `computer_run`: dozens of actions, one call.
2. **A delegated objective.** `computer_browse(objective, session?)` hands the
   whole task to a **Strands sub-agent** with its own cheap model, step budget
   and browser tools (snapshot, act, extract, screenshot). Nora sees one call
   and gets one typed result back (§15). The sub-agent's loop runs in Nora's
   process, outside the VM, and drives Chromium inside the VM over CDP
   through computerd, so no model key enters the VM. It has no memory, no
   `send_reply` and no Nora tools.

**Decided 2026-09-30 (operator): the sub-agent drives the browser through
Vercel agent-browser** (Apache-2.0; CDP attach; compact accessibility
snapshots with `@eN` element refs, so several actions batch into one model
turn; no model of its own and nothing sent out), with **browser-use** as the
comparison arm in the spike.

A survey of about 18 options (2026-09-30) excluded:

- **Page content leaves norty:** Jev (TypeSafe's hosted action picker), Nova
  Act, the OpenAI, Anthropic and Gemini computer-use APIs, Browserbase's
  hosted MCP.
- **Licence:** Skyvern (AGPL, and heavy), Notte (SSPL).
- **Vision-only or TypeScript:** Magnitude.
- **Maturity:** cua-s1 (research; the 4B needs about 16 GB free, norty has
  about 10), UI-TARS, LaVague (dormant).

Stagehand (DOM mode, action caching) is the third candidate; its standalone
Python SDK repo was archived on 2026-09-01. The pattern Jev and cua-s1 share,
a small specialist model picking each action, is worth keeping as a
pluggable step-picker behind the sub-agent. It is not adopted now.

**S4 spike.** Every arm uses the same DeepSeek Flash model and the same CDP
proxy into the VM. Arms: agent-browser plus the Strands sub-agent;
browser-use with vision off and on; Stagehand if it attaches cleanly. Six
tasks, each with a deterministic checker, each run at least 5 times:

1. availability on a booking page;
2. a form with validation errors;
3. a table behind a login, with the credentials never shown to the model;
4. prices compared across two sites;
5. a page carrying a prompt-injection trap;
6. task 1 or 3 again after the page layout changes.

Measured, fail-closed: success rate, model calls, tokens and cost per task,
wall time, second-run cost, and zero unexpected egress.

## 15. Sessions: a stateless Nora on a persistent computer

The computer keeps files, running processes and browser tabs between turns.
Nora's turns keep nothing. So each turn has to be *told* what the computer
holds. Not remembering is the default, and a turn that is not told acts as if
nothing is there.

**Why not a Standing-style fold.** A model-written fold over the history is
how earlier Standing folds dropped open plans (at least 10 of 206 folds,
`design/standing-compact/40-open-plans.md`), and half-finished work is
exactly what a session exists to keep. So what is *observed* is kept apart
from what is *intended*, and neither is rewritten by a model.

1. **Observed state: code, no model, always current.** At render time, read
   the computer itself through computerd:
   - files changed recently, with ages;
   - tmux sessions still running (long jobs use tmux, Pi's pattern, not a
     process registry of our own);
   - open browser tabs, from CDP;
   - the room's last few `computer_events`.

   It is read fresh each turn, so it cannot go stale.
2. **Intended state: `computer_sessions`, rows written by code from a typed
   result.**
   - **Revised 2026-09-30 (operator), as built in S2b-2: sessions are
     DERIVED BY CODE, and no model writes one.** An optional update tool
     would be skipped (a must-have skill was skipped in 3 of 6 live turns).
     A computer call in a room with no open session opens one before its
     `started` audit row; any later call there continues it; after expiry
     the next call opens a new one (no resume verb). The objective is the
     triggering message, cut to 80 characters, plus its tag id. Progress is
     derived when read from the session's own `computer_events` rows, never
     stored. Stored status is `open` or `expired`; shown status is
     `active`, `idle`, `lapsed` or `expired`; `paused`, `blocked`, `done`
     and `failed` are dropped, as is `next_step`. `computer_session_list`
     is the only session tool. Where the bullets below say the model or a
     sub-agent returns a status, that is superseded; the browser
     sub-agent's typed result (S4) is still to be designed against this.
   - When a `computer_browse` task (or a long run) ends, the sub-agent returns
     `status` (done / paused / blocked / failed), `objective`, `progress`,
     `next_step`, evidence (screenshot keys, file sha256s) and where it
     stopped (tab URL, tmux name).
   - Code writes that into a row keyed by session id (the room is an
     attribute; a room can hold several sessions), with provenance (tag id,
     time). The model fills in one task's result and never folds the history.
   - The rows live in Nora's Postgres, not the VM, where a hostile page could
     rewrite them.
   - **What a session is** (decision 5): one task in progress across turns,
     like a ticket. It is not the machine, a room's workspace, or a browser
     profile, which all persist. It holds a project folder, a tmux name and
     a browser tab or context while it is open.
   - **States:** `active`, `paused`, `blocked` (on a person), `done`, `failed`
     and `expired`. `done` and `failed` are terminal.
   - **Expiry** is done by a sweeper on Nora's side, the prune/recover loop
     shape.
     - A session expires when it has had no audit event for
       `idle_expiry_hours` **and** nothing it owns is running. A live tmux job
       keeps it open, up to `max_running_days`, after which the job is
       flagged, not killed. It also expires when every resource it held is
       gone (checked against computerd's live state).
     - On expiry, a compare-and-set moves it to `expired` with a reason, and
       its lease is released. Its browser tab or context is closed, and its
       tmux session is killed only if nothing is running in it. A final audit
       row records `actor = sweeper`.
     - **Files, project folders, logged-in profiles and audit rows are never
       deleted.**
   - **Lapsed, not lost.** For `lapsed_visible_hours` after expiry, the
     `<computer>` block still shows it as lapsed, with where it stopped, so
     Nora can answer about it. It stays resumable from its saved progress and
     next step. **No automatic message** is sent; a nudge would be proactive
     speech, a separate decision on its own path.
   - **Configurable** (operator, 2026-09-30): `[computer.sessions]` in
     `config.toml`: `idle_expiry_hours = 24`, `max_running_days = 7`,
     `lapsed_visible_hours = 24`, and `sweep_interval_seconds`.
3. **The `<computer>` block**, one renderer (ADR 0012; ADR 0013 `a-block-says-what-it-is…`), with an age on
   every line:
   ```text
   <computer room-workspace>
   open session  "table for 4 at X, Fri 7pm" · paused 2h ago at payment · next: needs card confirmation
   running       tmux "scrape-prices" · started 40m ago
   recent files  projects/trip/prices.csv (12m ago) · report.html (1h ago)
   </computer>
   ```
   Nora resumes by passing the session id to `computer_browse`.
4. **Concurrency: the live turn first, then the background loop.** Two
   callers want the computer: the *conscious* path (a tag turn, someone waiting
   on a reply) and the *subconscious* one (the Cognition and Dreamer passes,
   which run with nobody waiting). The rules:
   - **Every call carries its actor** (`tag`, `fire`, `cognition`, `dreamer:<adaptor>`),
     both in `computer_events` and on `computer_sessions`, so what ran on whose
     behalf re-derives from the rows.
   - **Lock the thing being changed, not the room.** Each session owns its
     `projects/<slug>/`, its tmux name and its browser context. A lease is
     per session (a lease id, a TTL and a heartbeat). Reads and observed-state
     reads take no lock. A free-form `computer_run` with no session takes a
     short room lease.
   - **The live turn never queues behind the background.** The subconscious
     side takes a lease with `try_acquire` and, when busy, defers to its next
     pass. That is the same rule the heartbeat and compute poller already
     follow for the conversation slot, so a reminder never waits behind a tag.
     The conscious side waits briefly. If a background holder is in the way, it
     signals a cancel: the browser sub-agent checks between steps, and a
     background run is killed at its next step boundary. It then marks its
     session `paused` with where it stopped, so the work resumes rather than
     being lost.
   - **Parallel where nothing is shared.** Different sessions run at once:
     separate project directories, and separate CDP browser contexts (their
     own cookies) in one Chromium. A logged-in browser profile is one shared
     context, so work on it is serialised per profile. A cap of 2 concurrent
     runs matches the VM's 2 vCPUs. Background runs are `nice`d below live
     ones.
   - **What the subconscious may do: the line is containment, not
     capability** (decision 6, operator 2026-09-30). Technically the
     background loops can do anything a live turn can, through the same
     tools.
     - **Inside the VM, allowed on their own initiative:** run code, write
       files, install from the allowed mirrors, continue or finish a
       session. Deny-all egress keeps those effects on the machine.
     - **Outside the VM, only when a person asked in that turn:** browsing
       the live web, using a logged-in profile, exporting to a room or a Drop
       link. This follows DEC-201's refusal of LinkedIn reads in fire and
       Dreamer turns: an action with an outside effect waits for a live
       request.
     - Every call is audited with its actor, so what the background did is
       reviewable.

The block renders only in rooms that have used the computer, so every other
room's prompt is unchanged. How to measure it: a turn that follows an open
session names it, and does not restart the task from scratch.

## Decisions (operator)

1. **Accepted, 2026-09-30:** a Docker account login is a dependency (ADR 0019,
   DEC-243).
4. **Decided, 2026-09-30:** retire `make_artifact`, Smith, `LocalDockerBackend`,
   `/api/tokens` and `docker.sock`. **Keep** the job outbox, poller and
   `compute_jobs` as the delivery path for a future `delegate_task`.
5. **Decided, 2026-09-30:** a session is one task across turns (§15.2). It
   expires on inactivity or on its resources being gone, and never deletes
   work. The numbers live in `[computer.sessions]` in `config.toml`.
6. **Decided, 2026-09-30:** the containment line (§15.4).

## Open decisions (operator)

2. One VM with a directory per room (proposed), or one VM per room?
3. Which accounts, if any, may stay logged in inside the browser?
