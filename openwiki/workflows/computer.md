---
type: workflow
title: Persistent computer and browser work
description: Room-scoped workspaces and process state, bounded browser tasks, audit trails and the sandbox's implemented limits.
tags: [computer, sandbox, browser, persistence]
verified:
  - by: openwiki/0.7.2
    at: 2026-10-20T00:28:42.613Z
sources:
  - id: openwiki-source-7aa209ee4f993345d7092214
    resource: repo://config.example.toml
  - id: openwiki-source-2e4fad429e73644210829d1c
    resource: repo://deploy/computer/README.md
  - id: openwiki-source-04ee2892a4ed6d63e5ce9014
    resource: repo://nora/agent/browse.py
generated: { by: "codex", at: "2026-10-20T00:28:42.613Z" }
---

Computer use gives Nora somewhere to leave work between speaking turns. Files, processes and browser state belong to a room-scoped workspace served by computerd in a sandbox VM; they do not live only in an agent's prompt.

## From a tool call to continuing work

The `computer_*` tools send authenticated requests to computerd. The feature is off by default and requires the daemon, a configured bearer-token environment variable and the relevant room permission. Each computer call has an audit record.

A task session tracks work in one room. Later calls can continue it, while tmux jobs can outlive an individual call. Session expiry is bookkeeping: it does not kill a running job or delete files, profiles or audit rows. A subsequent call opens a new session rather than pretending an expired one continued.

On later speaking occasions, the bounded `<computer>` context is rebuilt from machine state and the audit for that room. Recent work can therefore re-enter the prompt without replaying every shell result. If the machine read exceeds its timeout, database state supplies the reduced context.

## Browser tasks have their own runner

`computer_browse` delegates a whole objective to a bounded agent in the room's Chrome through computerd. This is separate from fetching the text of one URL. The runner owns page actions and returns a task outcome to the caller.

The browser task has page-action, wall-clock and model-loop limits. Timeout or browser failure becomes `unavailable`, not a successful empty result. Browser enablement is separate from the main computer switch and needs both paths configured.

Files can be imported into the workspace and exported through governed delivery paths. An export has its own custody and destination record; the existence of a local file does not mean a recipient received it.

## Boundaries and limits

The sandbox is a semi-trusted environment, not an assertion of perfect isolation. Network policy permits public browsing and denies private-address ranges, with a documented ceiling: the current CIDR deny applies to IP literals and does not prevent a public hostname resolving to a private address. Operators must assess that actual boundary rather than infer a stronger one from the word “sandbox”.

The `[computer.limits]` per-command resource fields are reserved and currently ignored by computerd. Tool timeouts and output caps must not be presented as enforced CPU or process quotas.

## Verify a computer change

Check daemon availability separately from the returned task status. Follow the room scope through request, process/file access, audit and export destination. A successful shell command is not evidence that a browser task finished or that an artifact was delivered.

`tests/unit/test_computerd_processes.py` checks process visibility, and `tests/unit/test_computer_export_tools.py` includes cross-room artifact revision refusal. Real browser tests include reconnecting to the same room browser. Keep deterministic checks separate from live model task acceptance.

See [continuity](../concepts/continuity.md), [configuration](../operations/configuration.md) and [contributing](../operations/contributing.md).
