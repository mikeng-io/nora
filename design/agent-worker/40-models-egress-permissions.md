---
type: design
title: "Models, egress and permissions: no key in the VM, one door out for model calls, policy in code"
description: "How a worker inside the sandbox calls a model without holding a key (a per-session relay token through one allowlisted egress rule), the egress change that needs, and the permission policy Nora's code answers a harness's requests with."
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

computerd's VM denies private address ranges and allows the public web
(`deploy/computer/README.md`, «Network egress»). OmniRoute is on the host, so
a worker cannot reach it, which is correct and stays so. The relay is reached
through **one allow rule for exactly its address and port**, applied with the
same `nora-computer egress apply` mechanism, checked by `egress check`, and
nothing else private opens.

## 3. Permissions

A harness asks before acting (ACP's permission requests, or the harness's own
approval hook). Nora's policy answers in code, from the request's kind:

| request | answer |
|---|---|
| read or write a file inside the scope | allow |
| run a command inside the VM | allow: the VM is the boundary, as it is for `computer_run` today |
| reach the network | allow what the egress policy allows; the request is logged, the egress rule is what enforces |
| read or write outside the scope | deny |
| anything that publishes or sends (a git push, an upload, a message) | deny: files leave the VM only through `computer_export`, which the sub-agent calls after, under its own rules |
| a credential, a secret, a login | deny |
| anything unrecognised | deny, with the kind named, so the policy can be extended by a reviewed change |

Every request and answer is in the session's record. No request waits on a
person: a run that needs a human decision stops and says so in its result,
and the sub-agent reports that to the room.
