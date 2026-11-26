---
class: design
subject: the scoped anonymous browser, its tasks, capture and security
owns:
  - "browser task: one objective with its own tabs in a scope's browser, observed and acted on step by step"
area: computer
links:
  - {to: docs/design/computer/README.md, rel: uses}
  - {to: docs/design/computer/sandbox.md, rel: uses}
---

# Browser

Nora's browser is anonymous, headed Chrome inside the scope's computer. The scope owns the computer,
workspace and browser; native tools and external workers inherit that binding and choose no other
endpoint.

## Parts

- **Admission** resolves the scope's computer and execution segment; a failure never falls back to a
  shared computer or another scope.
- **The browser manager** starts one browser on demand, binds tabs to tasks and expires idle tabs,
  then the browser, while files persist.
- **The executor and runner** observe a page's text and a numbered accessibility snapshot, validate a
  reference, and act on the matching live control through an authenticated channel. No model controls
  an endpoint or an executable control.
- **Capture** writes an actual image of the owned tab into the scope's workspace and reuses the export
  path for delivery.

## Rules

- **One browser tool,** for pages whose data appears only after interaction. A page the caller
  already has is fetched as a page ([tools](../agent/tools.md)); an ordinary read never needs the
  browser.
- **A task owns its tabs.** Tabs belong to tasks, not to the first or active page; concurrent tasks
  never navigate or close each other's. A task continues on its own tab; a stale handle is
  unavailable, never attached to a replacement.
- **Each step observes fresh.** The runner gives the decision model one observation, a bounded list
  of numbered actions and the actions already taken; the model returns one id, the runner validates it
  against that observation and acts, then observes again. A reply naming no id finishes from the
  evidence in hand, and a page that needs a person ends the task for one.
- **A report cites observed page evidence** and keeps unpublished facts unconfirmed. A run cut off at
  a limit has not covered its range: what it read is unavailable, never nothing found.
- **Anonymous only.** No login, credential fill, vault or imported personal profile. Cookies and
  tabs live only while the browser does, within one scope, and nothing is restored on restart.
- **Same-scope work serializes** even across tabs; different scopes progress independently.
- **A screenshot is a real captured image.** A failed or partial capture is unavailable. Capture and
  export are separate outcomes, and a failed export keeps the file for a retry.
- **A bot challenge is unavailable,** never a fabricated success, a solver or a silent switch to a
  stealth engine.
- **The model loop and its credentials stay outside the computer.** Browser control needs an
  authenticated, scoped transport and never an open debugging port.
- **Context never navigates.** The browser block shows availability, the active task and its tabs
  with observation age; rendering it opens no URL.

## Evidence

One execution root joins native and worker actions, capture and export with task, scope, tab and
the model that served it. Audit distinguishes attempted from actual actions and rejected operations;
a failed required start prevents execution, and a failed finish append never erases a real result.
Payloads stay by reference. Incomplete cancellation is not reported as success.

Capacity is measured on whole-computer cost, and challenge rate on a fixed anonymous task set with
its sample size; a vendor claim, a real browser brand or a residential exit proves neither.

## A credentialed browser

A browser that logs in is a separate design. Its vault keeps ciphertext in a broker and returns
handles, never values, to the model. A fill is checked for origin, frame, field, scope and approval
immediately before it types into a validated login field; anything else is unavailable and a sealed
vault fails closed. Every later read path is redacted, password storage is off and a credentialed
field never permits arbitrary script. An anonymous scope needs none of it, and a personal profile is
never attached for convenience.
