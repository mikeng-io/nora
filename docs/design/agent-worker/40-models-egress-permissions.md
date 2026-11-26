---
type: design
title: "Models, egress and permissions: no key in the VM, a public relay for model calls, policy in code, residual shell capabilities stated"
description: "How a worker inside the sandbox calls a model without holding a key: a per-session relay token on the public side, with no private egress rule changed. What the egress policy does and does not isolate. The permission policy Nora's code uses to answer a harness, and the shell capabilities that remain."
tags: [nora, agent-worker, model-relay, omniroute, egress, permissions, secrets]
timestamp: '2026-10-25'
edges:
  - {dst: 10-worker-port, rel: constrained_by, provenance: asserted}
---

# 40 · Models, egress and permissions

## 1. The model relay

A harness calls a model itself. Inside the VM it must not hold a provider key:
the VM is the less-trusted side of the threat model the sandbox design
follows (threat follows content, not users), and a key the worker can read is
a key a prompt injection in a fetched page can read.

- **A relay on Nora's side** accepts the harness's model calls on an
  OpenAI-compatible endpoint, checks a **per-session token** issued at
  `start` and revoked at the session's end, counts tokens against the
  session's limit, and forwards to OmniRoute with Nora's own credentials.
- The harness is configured with the relay's address and the session token as
  its "key". A leaked token is good for one session's remaining budget.
- **Which model** is the brief's, from the models the relay allows. The
  relay's allowlist, not the harness, decides; a request for another model is
  refused.

## 2. Egress

computerd's VM allows `**` over TCP and denies six private ranges
(`deploy/computer/README.md`, «Network egress»). Two facts about that policy
shape the relay:

- **Deny beats allow** (`deploy/computer/nora-computer:56`). An allow rule for
  the relay's private address and port would lose to the private-range deny,
  so "one allow rule" cannot work without removing a deny.
- **The private-range deny is not complete isolation.** sbx applies a CIDR
  deny to IP literals only. A public name that resolves to a private address
  (`127-0-0-1.sslip.io`) still reaches the host's services, including
  OmniRoute's port. The operator accepted this limit for `computer_run`
  (`README.md:63`); a worker inherits it. So the worker **can** reach
  OmniRoute by that path today. What keeps it from calling a model unpaid is
  that OmniRoute requires a key the VM does not hold, not the network.

**So the relay is reached on the public side, and no private rule changes.**
The relay listens behind the host's existing public HTTPS entry (Caddy, or
the Cloudflare tunnel), on one path, and accepts only a valid per-session
token. The VM reaches it the way it reaches any public site. No private range
is opened, and `egress apply` is unchanged.

- **The positive check is new, and it gates step 4.** From inside the VM, an
  authenticated request through the relay with a live session token must get a
  model response. The same request with a revoked or forged token must be
  refused by the relay. `egress check` tests only fixed public destinations
  and private denials (`nora-computer:292`), so it cannot prove the worker can
  call its model.
- **The negative checks stay**, and are worded as what they prove: IP-literal
  private denies hold. They do **not** prove that no private service is
  reachable by name.

## 3. Permissions

A harness asks before acting (ACP's permission requests, or the harness's own
approval hook). Nora's policy answers in code, from the request's kind:

| request | answer |
|---|---|
| read or write a file inside the scope | allow |
| run a command inside the VM | allow. The VM and the scope's own Unix user are the boundary, as for `computer_run` today; see below |
| reach the network | allow what the egress policy allows; the request is logged, the egress rule is what enforces |
| read or write outside the scope | deny |
| anything that publishes or sends (a git push, an upload, a message) | deny: files leave the VM only through `computer_export`, which the sub-agent calls after, under its own rules |
| a credential, a secret, a login | deny |
| anything unrecognised | deny, with the kind named, so the policy can be extended by a reviewed change |

**What a permission answer does not control.** The answers above apply to
actions the harness *declares*. A shell command can do anything the VM lets
that user do. commands run as `bash -c` in the scope directory under the
scope's own user (`deploy/computer/computerd.py:481`), with no
network-operation filter, and public TCP is allowed. So a prompt-injected worker
can upload a file with `curl` without ever asking for "upload", and an allowlist
containing `bash` has the same limit. These are the **residual capabilities**,
stated rather than hidden:

| a worker's shell can | enforced by | not enforced |
|---|---|---|
| read and write its own scope | the scope's own user, `0700` directories | - |
| read another unit's scope | **refused**: other scopes belong to other users | - |
| reach any public host, including uploading files | nothing | accepted, as for `computer_run` today |
| reach a host service through a public name that resolves privately | nothing (the `README.md:63` limit) | accepted. Services still need their own credentials, which the VM does not hold |
| read a provider key | **impossible**: none is in the VM (§1) | - |

So "deny uploads" means the harness's own upload or publish action is refused
and logged. It does **not** mean data cannot leave through a shell command. The
protection for the data in a scope is that the scope holds only what that
unit's computer was given. The acceptance tests (§3 of
[50](50-verification-and-migration.md)) include **wrapped shell operations**
(`curl -T`, `git push` run inside a shell script), and assert what each one
actually does, not what the table hopes.

Every request and answer is in the session's record. No request waits on a
person: a run that needs a human decision stops and says so in its result,
and the sub-agent reports that to the room.
