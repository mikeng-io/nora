# Browser and execution

## Native Chrome

One native Google Chrome starts on demand in each owned VM with a private
virtual display. Anonymous browsing only: no account login, credential fill,
vault or imported personal profile. Build, display, locale, timezone, fonts,
proxy and TLS behavior form one recorded fingerprint baseline.

The browser manager reuses in-memory tabs and anonymous cookies while the
browser lives. Each task owns separate tabs and may resume its own tabs;
concurrent tasks never navigate or close each other's pages. Popup and new
targets are assigned and counted before exposure.

Idle cleanup retires expired idle tabs and then Chrome/Xvfb. Active work is not
killed by sweeps or budget expiry. Explicit close and cancellation close owned
tabs after execution stops. A new browser session is fresh; an old handle
returns `unavailable` with expired detail rather than attaching to a
replacement.

## Observation and action

Accessibility snapshots and element references are the default model input;
screenshots are requested when visual grounding or direct capture is needed.
References and page content are observed fresh before acting, even when a
context summary is cached.

The shared executor validates references and performs actions through
authenticated computerd/CDP. Scope and task tab are checked on reconnect and by
ID. No unauthenticated debugging listener or cross-scope relay exists.

A task-level browser runner reasons over the objective and proposes steps while
code owns admission, execution, audit and outcome. Another decision backend can
implement the same contract later, but the first version needs no fallback
cascade, model tier or second agent loop. Selection is recorded and does not
change silently on failure.

A typed decision model may choose among prepared candidates, including
abstention. It does not execute a click, synthesize every answer or remove the
cost of the remaining model calls. Its confidence is not task success.

## Capture and export

Direct viewport and full-page capture operate on the owned tab without an
objective-driven browsing loop. The operation writes the actual image into the
scoped workspace and returns its relative path, type, size, digest, scope, task,
tab and capture time. Full-page coverage failure is explicit.

`computer_export` handles requested delivery and retention under its existing
permission and validation rules. Capture success and export success are separate
outcomes, and a failed export preserves the file for a requested retry.
Observation screenshots do not automatically become public artifacts or a
continuous recording.

The typed computer context exposes availability, active task, queue and owned
tabs with observation age and expiry detail. Mutations invalidate that cached
view; a later turn assembles fresh state while current tool results carry the
change immediately. Context rendering never navigates to a retained URL.

## Evidence

One execution root joins native/worker actions, capture and export with task,
scope, VM, tab, backend and served-model identity. Consecutive turns are linked
but separate traces, not one indefinitely open browser trace.

Durable audit distinguishes attempted and actual actions, rejected operations,
capture and export. A failed required start prevents execution; a failed finish
append does not erase a real result. Incomplete cancellation is not fabricated
success. Payloads stay by reference rather than copied into every signal.
