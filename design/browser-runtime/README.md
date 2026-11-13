# Browser runtime

Nora's browser is anonymous, headed Chrome inside a scoped microVM. A policy
unit or canonical private-DM scope owns the VM, workspace and browser; native
tools and external workers inherit that binding instead of choosing another
endpoint.

**Status:** building

[Open issues](https://github.com/mikeng-io/nora/issues?q=is%3Aopen+%22design%2Fbrowser-runtime%2F%22)

## Parts

- **Scope resolution and admission** map an admitted turn or job to its VM and
  execution segment. Failure never falls back to a shared VM or another scope.
- **The lifecycle controller** is the only host component allowed to provision,
  restart and stop owned VMs. It offers a narrow authenticated lifecycle API,
  not a Docker or shell proxy.
- **The execution queue and leases** serialize same-scope work, preserve
  ownership across nested calls and prevent new work during revocation,
  transition or ownership loss.
- **The browser manager** starts one native Chrome on demand, binds task tabs
  and expires idle tabs, browser and VM while files persist.
- **The executor and runner** observe accessibility structure first, validate
  references and act through authenticated computerd/CDP. Decision backends
  implement the same task-level contract; no model controls scope or endpoint.
- **Capture and export** write an actual task-tab image into the scoped
  workspace and reuse `computer_export` for delivery, preserving tab and capture
  provenance.
- **Optional credential brokerage** is separate design work for a later
  credentialed browser. Anonymous use needs no vault, login or personal profile.

The subjects are [scope and lifecycle](scope.md), [browser and
execution](browser.md), and [security and evidence](security.md).

## Invariants

- One scope owns persistent state, not an always-running VM. Quiet stop saves
  running resources while disk remains available; restart preserves files and
  expires old boot handles.
- Capacity admission reserves before creation, counts starting and running
  guests and refuses unknown inventory or insufficient headroom. It never kills
  active work to make room.
- No guest effect is retried blindly. Partial provisioning, uncertain ownership,
  lost responses and crash recovery reconcile against actual state first.
- Tabs belong to tasks, not to the first or active page. Same-scope segments
  serialize even across tabs; different scopes progress independently.
- Anonymous profiles share state only within their scope and only while the
  browser lives. No previous profile, login or URL is restored on restart.
- A screenshot is a real captured image, not placeholder bytes or a DOM text
  layout. A failed or partial capture is unavailable, never an empty success.
- The model loop and model credentials stay outside the VM. Browser control
  requires authenticated, scoped transport and never an open debugging port.
