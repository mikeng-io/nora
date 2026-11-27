---
class: design
subject: the worker, a coding harness that is not Nora, driven through a session port inside the computer
owns:
  - worker: "a coding harness inside the computer that does multi-step work for a sub-agent and is never Nora"
  - worker port: "start, prompt, cancel and result, shaped on the agent client protocol"
  - model relay: "the per-session endpoint through which a worker reaches a model without holding a key"
area: subagents
links:
  - {to: docs/design/subagents/README.md, rel: uses}
  - {to: docs/design/computer/README.md, rel: uses}
  - {to: docs/design/computer/sandbox.md, rel: uses}
---

# Worker

A coding task of thirty steps is thirty model rounds across the computer's boundary when Nora's own
loop is the harness, and that loop is general-purpose where coding harnesses are built for this job.
So the multi-step computer work can go to a **worker**.

| layer | is | does |
|---|---|---|
| **Nora**, a pass | herself | decides; talks to the room |
| **her sub-agent** | still Nora, behind the scenes | holds the objective, reads, plans, reports |
| **the worker** | not Nora | does the computer work inside the computer |

The worker never speaks to a room, holds no memory of people and sees only the brief and the
workspace it is given. Its result returns to the sub-agent. Single operations stay on the native
computer tools, where a session's start-up costs more than the work; the sub-agent chooses, and the
split is measured, not assumed.

## The port

The worker port is shaped on the agent client protocol, a client driving a coding agent in a working
directory, so a harness that speaks it needs no adapter of its own and one that does not needs an
adapter onto four verbs:

- **start** a session in the job's scope with a brief, limits and a policy;
- **prompt** it, receiving typed updates: plan, tool call, tool result, file diff, message,
  permission request and stop;
- **cancel** it, idempotently;
- **result** of the session.

Only the sub-agent calls it, through one tool, and the worker inherits the job's scope; a model
never names it. Limits are the sub-agent's and are enforced by cancelling, not by asking the worker
to stop. The port never raises: a harness that will not start, a computer that is down or a protocol
error is a typed unavailable result.

## What comes back

A worker's claims and what code observed stay apart. The summary, what is done and what is next are
the worker's claims, rendered as such. What changed is a content manifest of the scope before and
after, by path, size and digest; the commands and their exit codes come from the stream; the check the
brief named is a command whose exit code is its verdict; rounds, tokens and seconds are counted by
the adapter. A claim never becomes an output: "all tests pass" is a claim until the named check says
so. The whole stream is kept as the session's record, readable by the operator and not citable by
the graph.

## Adapters

One adapter per harness, each mapping the four verbs and nothing more: launch inside the computer as a
long-lived process in the scope's directory; turn the harness's events into typed updates; route its
approval requests to Nora's policy; stop the process on cancel. The first harness is a minimal coding
agent built to be driven programmatically; any harness that speaks the protocol follows through one
adapter. A harness's interface changes faster than a design does, so what each one provides is
established when its adapter is built: its programmatic mode, how it is pointed at a model, whether it
has an approval hook or only an allowlist, and what it writes outside the working directory.

Every adapter passes one shared contract suite: a session starts in its scope and cannot write
outside it, updates arrive typed and in order, a permission request reaches the policy and its denial
reaches the harness, a limit cancels, a crash is unavailable, and the observed fields match the
computer and not the harness's claims.

## The computer's side

The runtime port gains one verb for the worker: **spawn**, a long-lived process with streamed input
and output, bound to its job, killed when its session ends or its limits are reached, scoped like every
other verb, with an environment filtered by the computer. The computer serves it over the same
streamed channel it already serves the browser, so no new transport kind exists.

The computer is switchable behind the port. A provider must give a persistent workspace per scope,
exec with streamed stdio, file read and write, an egress policy Nora sets, and a lifecycle of create,
stop, delete and health. A new provider is an adapter that passes the runtime contract suite.

## Models, egress and permissions

- **A relay, not a key.** The computer is the less-trusted side, and a key a worker can read is a key
  a prompt injection in a fetched page can read. A relay on Nora's side accepts the harness's model
  calls on a compatible endpoint, checks a per-session token issued at start and revoked at the end,
  counts tokens against the session's limit and forwards with Nora's own credentials. A leaked token
  is good for one session's remaining budget, and the relay's allowlist, not the harness, decides
  which model answers.
- **The relay is reached on the public side,** behind the host's existing public entry, so no private
  range is opened; the computer's egress rules are [computer](../computer/README.md)'s. A worker
  cannot call a model unpaid because the gateway needs a key the computer does not hold.
- **Permissions are answered by code,** by request kind, never by a model and never by a person
  mid-run: a read or write inside the scope is allowed; a command inside the computer is allowed, the
  computer and the scope's owner being the boundary; the network is allowed as far as the egress
  policy allows; a read or write outside the scope, anything that publishes or sends, and anything
  that asks for a credential are denied; an unrecognised kind is denied and named. A run that needs a
  human decision stops and says so, and the sub-agent reports that.
- **What an answer does not control.** The answers apply to actions the harness declares. A shell
  command can do whatever the computer lets its owner do, so a worker's own publish action is refused
  while a command that uploads through a public host is not. The protection for a scope's data is that
  the scope holds only what that unit's computer was given. These residual capabilities are stated, not
  hidden, and the acceptance checks include wrapped shell operations and assert what each actually does.

## Why

- **A port shaped on the protocol** so a harness is a choice, not a rewrite.
- **Claims apart from observations** because a worker's own account of success is the failure this
  design exists to catch.
- **Code answers permissions** because a person mid-run would put a latency and a confusion into a
  background job, and a model would be the thing the permission is for.
