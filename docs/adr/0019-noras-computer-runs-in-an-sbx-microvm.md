# 0019 · Nora's computer runs in an sbx microVM, behind computerd

- **Status:** accepted (2026-09-30, operator); Decision 4 superseded by [0025](0025-group-computer-workspaces-belong-to-units.md)
- **Date:** 2026-09-30
- **Replaces, in stages:** Smith's per-job containers (`LocalDockerBackend`,
  `nora/compute/smith/`). This ADR ships the computer; the tools and Smith's
  retirement follow in design/sandbox-runtime §13 (S2b, S3).
- **Index:** DEC-243

## Context

`design/sandbox-runtime/00-design.md` studies giving Nora a coding agent's
toolkit (run commands, read and write files, and later browse and export)
executed on a machine she can afford to lose. Four facts measured on
2026-09-30 decide the shape:

- **Containers on the host network were not isolated.** A container started
  the way `LocalDockerBackend` starts Smith read Honcho unauthenticated, and
  reached the host, the LAN and Tailscale. #631 fixed Smith's network. It did
  not add a kernel boundary, and Smith is still a fresh container per job with
  no persistent disk.
- **Docker Sandboxes (sbx) passed the spike on norty (§9).**
  - Each sandbox has its own guest kernel (7.0.14; the host runs 7.0.0).
  - Deny-all holds at the data level for every address probed.
  - Placeholder secrets are substituted on the host: the real value reached
    the upstream, and 0 copies were found in the VM's env or filesystem.
  - Files survive a stop, and `exec` auto-starts a stopped sandbox.
  - A port published to docker0 is reachable from the nora container but not
    from the LAN.
  - A non-Docker-derived image boots as a template.
- **sbx needs a Docker account login, even for local sandboxes.** Every local
  verb returns `401 not authenticated to Docker` until `sbx login`, and the
  policy governor fetches from Docker at start. Docker states it as a
  requirement ("every sandbox gets a verified identity"). An open issue
  (docker/sbx-releases#5, 2026-04-02) reports that it broke air-gapped use.
- **Nora herself stays out of the VM.** Her job is being connected (Postgres,
  Honcho, the bridges, OmniRoute), so a VM around her would have to allow
  exactly the traffic it exists to block (§1).

Published convention: agent sandboxes separate the loop that decides from
the machine that executes (E2B, Daytona, Docker Sandboxes, Claude's computer
use). Where the agent loop runs varies; the tool-executing environment is
always the disposable side. That is the split taken here.

## Decision

1. **The computer is one persistent sbx microVM**, `nora-computer`, booted
   from `deploy/computer/Dockerfile`. Its disk is the working disk
   (`/workspace/<scope>/`). It is not canonical storage: anything that must
   last is exported (§7).
2. **computerd** (`deploy/computer/computerd.py`) is the VM's only interface:
   an HTTP API on a port published to docker0, with a bearer token, and Nora
   as its only client. Calls go one way: Nora calls in, and the VM's deny-all
   egress means it can call nothing of Nora's. **computerd runs as root and
   every command as `computer`**, and it listens on port 80. A command
   therefore cannot read computerd's token, kill it, or bind its port with an
   impostor, which is what makes its results and digests computerd's own
   rather than whatever the code it ran wanted to report. The file operations
   never follow a symlink: every path is walked from the scope directory with
   `O_NOFOLLOW`.
3. **`SandboxRuntime` is the port** (`nora/domain/ports.py`).
   `ComputerdRuntime` speaks computerd and knows nothing about sbx; it never
   raises, and every result carries `ok` / `nothing_found` / `denied` /
   `unavailable`. `tests/contract/test_sandbox_runtime_contract.py` holds it
   and `FakeSandboxRuntime` to one contract, run against the real computerd.
4. **A scope is a hash of the room**, derived by Nora's code, never by the
   model (`computer_scope`). No platform id reaches the VM.
5. **`deploy/computer/nora-computer` is the only thing that runs `sbx`.** A
   user systemd timer runs `ensure` every minute. Nora never holds the sbx or
   Docker socket. Health is an HTTP body, never a TCP connect, which succeeds
   to any address under sbx.
6. **The Docker login is an accepted dependency** (operator, 2026-09-30).

## Consequences

**Better**

- A kernel boundary between LLM-written code and the host, where a shared
  kernel and a network fix were all there was before.
- Persistent working state without a persistent LLM session: tomorrow's turn
  finds today's files.
- The runtime is replaceable at one seam. computerd runs in any VM or
  container; swapping sbx for Kata, Firecracker or E2B changes
  `nora-computer`, not the port, the client or the tools.
- Secrets can be kept out of the VM entirely, through sbx's host-side
  placeholder substitution.

**Worse**

- **A hosted dependency for a local capability.** If the Docker login lapses
  or Docker's auth service is down, `nora-computer ensure` cannot create or
  list sandboxes, and the tools answer `unavailable`. Whether a running
  sandbox survives an expired token is **untested** (design §13 S1). `sbx
  logout` is documented to stop running sandboxes.
- **A young CLI with no documented API.** `nora-computer` parses
  `sbx ls --json` and shells out. A breaking sbx release breaks the lifecycle
  unit; pin `docker-sbx` on the host if that bites.
- **A headless quirk.** sbx wants an unlocked desktop keyring; the unit points
  `DBUS_SESSION_BUS_ADDRESS` at nothing so it falls back to a file store,
  which is a file on disk protected only by permissions.
- **Memory.** The VM holds 3 GiB while running, on a host with about 10 GiB
  free (2026-09-30). There is no idle stop yet.
- **Destroying the VM deletes `/workspace`.** Recreate is not free until
  export and backup land (§7).
- **Package installs fail under deny-all** until mirrors are allowed. That is
  a policy decision for S2b, not a default.

## Rejected

- **Nora herself in the VM:** protects the host from Nora, not Nora from
  content (§1).
- **Hardened containers only (#631's network, plus gVisor):** programmable
  today with no account, but a user-space kernel rather than a VM, and the
  egress proxy and secret injection would be ours to build. It stays the
  fallback, and computerd makes the switch cheap.
- **Firecracker or Kata directly:** VM isolation with no account, but more
  of our own code (networking, images, lifecycle) for the same result. It is
  the fallback if the login becomes unacceptable.
- **E2B or Daytona (hosted):** moves room data off the host.
