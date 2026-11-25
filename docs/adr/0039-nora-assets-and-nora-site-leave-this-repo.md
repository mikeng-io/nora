# ADR 0039: Her art and the public site leave this repository

**Status:** accepted
**Date:** 2026-11-22
**Index:** none. This is a repository layout, not a runtime rule. The public
repository's CI cannot read a private one, so a public build that needs one
fails on its own.

## Context

The project has two homes that have drifted into one. heynora.bot is the
project's official site, for anyone who meets Nora. The operator's own
instance, on its own hostname, has its portal for the operator's circle. Today the
public site (`site/`) is a Compose service in this repository and answers `/`
on the instance's hostname.

The repository is public and MIT-licensed, and the operator wants it to read
as an open-source project. Her art is most of its weight:

- `assets/` is 57 MB;
- the 3D model in `app/public/models/` is 65 MB;
- the site's art is 11 MB;
- the packed history is 884 MB.

Her likeness is currently MIT along with the code. The operator rules that her
art and the official site stay private, with all rights reserved.

GitHub's per-job token can read only the repository that runs the workflow
([automatic token authentication](https://docs.github.com/en/actions/security-for-github-actions/security-guides/automatic-token-authentication)).
A public repository that fetches from a private one at build time therefore
needs a stored credential, and a contributor's fork has no such credential.

## Decision

Three repositories, all owned by `mikeng-io`:

| repository | visibility | holds |
|---|---|---|
| `nora` | public; MIT except the reserved art below | the runtime, the portal (`app/`), deployment, `design/`, DESIGN.md, the docs: everything a self-hosted Nora runs |
| `nora-assets` | private, all rights reserved | her identity art: the canonical and Mini masters, the reference sheets, the cinematic set, the clasp emblem, the 3D model's source, the showreel, and the visual-asset inventory and its checks |
| `nora-site` | private | heynora.bot: the journey, its art and rig, served as static files from Cloudflare |

- **`nora` keeps only the art it runs, reserved.** That is the portal's
  shipped art (icons, the emblem copy, her cut-out, portrait and poses, the 3D
  model) and the canonical self-image the runtime reads and pins. Each copy
  records the `nora-assets` version it came from, and a refresh is a PR that
  copies new files in. LICENSE names these paths as all rights reserved; MIT
  covers everything else. Everything else of hers moves out.
- **`nora` builds without the private repositories,** because those copies
  are committed.
- **DESIGN.md stays in `nora`.** It governs the portal, which is public; the
  files it names live in `nora-assets`.
- **The operator's instance serves the portal at `/`**, so `/` shows the sign-in. The
  `site` Compose service and the tunnel routes to it are removed.
- **The operator's instance stays out of `nora`.** Its hostname, accounts and
  contact addresses are configuration. Examples use the reserved
  `example.com` names (RFC 2606), and the portal's legal contact is instance
  configuration like its public origin.
- **The site moves with its history** (a subdirectory split of `site/`).
  `nora-assets` takes its files' history the same way.
- **This repository's history is not rewritten.** Clearing old binaries from
  it is a separate decision.

## Consequences

- A contributor clones the code without her source art. The public
  repository's licence names exactly which files are reserved.
- heynora.bot deploys independently of a Nora release, and an instance no
  longer carries the marketing site.
- Her likeness is no longer offered under MIT from this change on, including
  the copies `nora` keeps. Copies already published under MIT stay MIT.

**Worse:**

- A change to her design takes two steps: a release of `nora-assets`, then a PR
  in each consumer. #863 changed the emblem everywhere in one PR; that is no
  longer possible.
- The portal's committed derivatives can drift from their source; only the
  version each one records shows that it is stale.
- The visual-asset check no longer sees the portal's and the site's consumers
  in one tree. Each repository checks only its own files.
- The 884 MB history stays in `nora` until a separate rewrite.

## Rejected

- **One repository, as today.** It keeps one-PR design changes, but it leaves
  her likeness under MIT and the official site inside every instance.
- **Git submodules for the assets.** A fork cannot fetch a private submodule,
  so the public build breaks.
- **A private npm package consumed at build time.** It needs a stored token in
  the public repository's CI, and forks cannot build.
- **Splitting the portal out as `nora-app`.** The portal shares Nora's API,
  auth and roster, and its CI tests them together. It moves only if it gains
  its own API contract and deployment.
