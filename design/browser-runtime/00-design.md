---
type: design
title: "Nora's browser — anonymous headed Chrome in a scoped microVM"
description: "Current target NOT BUILT: one microVM per policy unit or canonical private-DM scope, with one on-demand anonymous headed Chrome, reusable in-memory tabs and direct screenshots. Preserves the accepted ADR 0022 design and historical B1/B2 delivery below; vault, login, streaming and tier optimizations are outside the current slice."
tags: [nora, compute, sandbox, sbx, browser, computer-use, credentials, openbao, vault, cdp]
timestamp: '2026-11-02'
status: partly-built
---

# Nora's browser

## Current target — scoped anonymous browser (2026-11-02)

**NOT BUILT.** Operator-directed scope amendment, tracked with the Pi/sandbox
outcome in [issue #520](https://github.com/mikeng-io/nora/issues/520).
The baseline is one shared microVM with unit-scoped workspaces, not one VM per
unit. The [unit migration](../computer-unit-scope/01-migration.md) is already
shipped; B1/B2 below record browser delivery, not delivery of this topology.
No host or runtime state was probed by this documentation revision.

This section owns the current slice and takes precedence over the historical
sequence below. ADR 0022 stays accepted and unchanged; its broader login/vault
and display plans are retained history and deferred work, not this slice's
acceptance. This target holds unless the operator changes scope or measured
isolation, resource or concurrency results require revisiting its mechanics.

### Scope and topology

- One microVM per **policy unit** for group work. Consecutive turns and the
  same unit's admitted transports resolve the same VM and browser session.
- A private DM gets its own VM using the canonical unique
  `(person_id, anchor_unit)` / `dm_scope_id` binding and opaque DM hash in
  [private-DM scope](../private-dm/05-cross-platform.md#3-computer-browser-and-export-are-in-the-first-slice).
  Never collapse by person alone, use the anchor unit's VM, or infer a scope
  from a display name. A default-unit change selects a different partition.
  DM admission/publication prerequisites remain required; this does not enable DMs.
- Nora resolves authority once from the admitted turn/job. Native tools and
  Pi (later ACP) workers use that **same scoped VM**, workspace and browser;
  the worker cannot choose a different scope or management/CDP endpoint.
  Scope identity is independent of the sandbox-provider adapter.
- Nora stays in the trusted container; her Strands subagent remains Nora and
  the Pi/ACP worker remains an external harness inside the scoped VM. The
  browser model loop stays outside the VM. No identity or model-key relocation.
- Management calls, lifecycle/state reads, process handles and computerd/CDP
  connections must route to the bound VM, with scope checked on reconnect and
  by-ID lookups. Never fall back to the shared VM, another endpoint or another
  scope when resolution fails. No unauthenticated CDP port is exposed to peers.

Each new unit or admitted DM VM must apply and read back the existing
DEC-289 public-web TCP allow plus private-range denies, scoped to that exact
sandbox; the shared VM's policy does not transfer. Apply and verify every deny
before enabling the wildcard allow, withholding it if any check is unavailable
or fails. Preserve the accepted hostname-to-private-address limitation; this
split does not introduce a filtering proxy or silently strengthen that claim.

### Browser lifetime and operations

One native headed Google Chrome per VM starts **on demand**, with its virtual
display inside that VM. Anonymous browsing only: no account login, credential
fill, vault or existing authenticated profile import. Anonymous cookies and
page state may be reused in memory across consecutive turns and same-unit
transports while the browser lives. Each task owns separate tabs; a continuation
may resume its own tabs, while concurrent tasks must not navigate or close each
other's tabs. A task/session handle is bound to scope, VM instance and tab owner.

Idle cleanup closes unused task tabs and stops an idle browser after active
owners release it; it must not kill an active native/worker task. There is no
promise that sessions, tabs or cookies survive browser/VM/service restart,
recreation or idle expiry. An old handle returns explicit `expired` detail under
`unavailable`; it never silently attaches to a replacement tab/session. A new
session is reported as new. Files retain their separate workspace contract.

The accessibility snapshot remains the default model input, with screenshot on
demand. A **direct screenshot operation** supports viewport and full-page capture
without requiring an objective-driven browser loop. Both direct and loop capture
bind scope and task tab, return the actual image with capture provenance, and
report capture failure as `unavailable`, never an empty successful image. A
full-page capture says when a page cannot be fully captured. This adds no live
screen stream, recording, noVNC or interactive takeover surface.

Use one consistent fingerprint baseline (Chrome build, display/viewport, locale,
timezone, fonts and proxy configuration), and record it with challenge-rate
measurements on a fixed anonymous task set. Report challenged visits / attempted
visits, failures and sample size, alongside task success. Headed Chrome is no
guarantee against bot detection; a challenge is an explicit unavailable outcome,
not a solver, fabricated success or automatic switch to a stealth engine.

### Control and model selection — design review (2026-11-04)

**Design first; runtime implementation has not started.** The operator asked to
reconcile this bundle before building. The scoped topology and session behavior
above are requirements; the model/driver recommendation below is not an accepted
selection. The historical four-tier architecture is not a requirement to build
all tiers before the first usable browser.

Separate the environment, action executor and decision model:

| Layer | Existing baseline | Selection for the scoped target |
|---|---|---|
| Browser environment | Headed Chrome on Xvfb inside the shared sbx VM | Keep native Chrome, move to one VM per admitted unit/DM scope |
| Action executor | Nora CDP client through computerd's authenticated websocket and Chrome debugging pipe | Recommend reuse; trycua driver is an alternative executor, not a model |
| Task reasoning and step decisions | Strands browser subagent, configured browse model; historical B2 Flash baseline | Recommend baseline first; Jev and Cua-S1 remain candidates pending the selection below |
| Observation | Accessibility snapshot and element refs; marked screenshot on request | Keep snapshot-first as the baseline; evaluate additional visual grounding only for a demonstrated gap |
| Direct capture | Screenshot exists inside the agent loop | Add scoped task-tab viewport/full-page capture without a model loop |

“CUA or Jev” is not one binary choice. Computer use names a capability;
trycua supplies drivers/infrastructure, while Cua-S1 supplies specialist decision
models. Jev supplies typed decisions over supplied state/questions. Neither a
Jev decision nor a Cua-S1 selection executes a click: a scoped executor still
has to validate and apply it. See the refreshed
[research](02-research.md#design-refresh-2026-11-04) and
[selection questions](03-open-questions.md#current-selection-2026-11-04).

**Recommendation, not a ruling:** reuse the existing executor and browser
subagent for the first scoped version. It already provides the necessary page
operations; changing VM/session routing does not itself require another driver
or model. Cua-S1 could later replace a narrow step decision; Jev could serve
that role only after its endpoint, data route and permission fit are resolved.
The existing ADR rejects hosted page-content processing, so a hosted Jev arm is
not admitted merely because it appears in this comparison. No credentials,
model calls, new dependencies or experiments are authorized by this document.

Reopen the recommendation if a candidate demonstrates better task completion
or lower end-to-end cost at comparable completion on the selected task set,
within the existing data boundary and available resources. Vendor latency and
benchmark claims are not Nora measurements. Before implementation, record the
accepted executor/model, evidence limits and exact scope in issue #520; leave
unmeasured optimizations deferred rather than building every candidate.

### Concrete browser setup — researched recommendation (2026-11-04)

Read the [current setup research](02-research.md#microvm-browser-setup-research-2026-11-04)
before choosing packages or implementing this slice. Existing image baseline is
Google Chrome Stable (`google-chrome-stable`), Xvfb and distro libraries/fonts;
existing controller is Python `aiohttp` plus Nora's CDP client, not Playwright.
Recommend retaining it pending source mapping; compare Playwright if it avoids
substantial new custom mechanics. Agent-browser, browser-use and Cua Driver are
explicit alternatives with different lifecycle/guest costs, not missing installs.

Headed Chrome is the accepted baseline; modern headless shares Chrome code and
can capture screenshots without Xvfb. Neither headed mode nor Chrome branding
establishes better challenge rates. Historical fingerprint assertions below
must not be treated as measurements. Verify browser sandboxing, guest resources,
TLS activation, proxy and per-task cookie/context ownership before cutover.

### Adapter boundary and ownership (2026-11-04)

The architecture supports multiple decision backends over one scoped browser
execution path. First use the existing configured LLM/Flash runner; later add
one selected Jev or Cua-S1 backend when its fit and data route are established.
This is extensibility of the selected solution, not a requirement to ship three
backends or a per-step automatic fallback cascade in the first version.

Reuse the existing `BrowserRunner` task-level port (`nora/domain/ports.py`) and
`BrowseAgentRunner` implementation (`nora/agent/browse.py`). Extend their task
binding for the session/tab requirements rather than creating a parallel public
tool per model. Both native calls and workers use this same scoped path.

| Responsibility | Owner | Adapter may change |
|---|---|---|
| Scope, VM instance, task/tab ownership, admission and expiry | Shared session/runtime code outside the decision backend | Nothing; models do not choose a scope or endpoint |
| Observe, validate refs/actions and execute via authenticated computerd/CDP | Shared browser executor | A future driver replacement must preserve the same contract |
| Reason over objective and observations; propose steps; decide when done | Selected runner/decision backend | Flash agent now; later Jev/Cua-S1 integration |
| Limits, cancellation, shared execution audit and artifact publication | Common task/execution path | No backend-specific bypass or success reporting |
| Final task result | Existing typed `BrowseOutcome` boundary | Backend internals differ; outcomes remain grounded and fail-closed |

Jev/Cua-S1 are not necessarily drop-in replacements for the LLM's model name.
A typed decision model can select an action/ref from prepared candidates; its
adapter must assemble the supported observation, decode decisions, handle
abstention and maintain task progression. Open-ended extraction, text generation
and final answer synthesis may still need the LLM. Make those remaining calls
explicit in the adapter and total cost; do not claim the whole browser became
LLM-free because clicks use a decision model. Only introduce a shared internal
step-decision interface when a second backend is actually being built; the
existing runner is sufficient for the first implementation. When introducing
another runner, establish one shared browser-runner contract suite under the
existing contract-test directory (not present today), covering
`BrowseAgentRunner`, its fake and each additional runner.

Select a backend at task/session creation and record the actual selection.
Do not silently switch on failure or reconnect. An explicit later fallback is
part of that backend's declared behavior and budget, retaining the same VM/tab
binding and recording each attempted model. Unknown/unsupported selection is
unavailable. A later trycua driver changes the execution layer independently;
it does not require replacing the sandbox provider or the export pipeline.

### Screenshot artifacts and delivery (2026-11-04)

Use one path: capture the owned tab, write an image to that scope's workspace,
then reuse `computer_export(path, to="chat" | "link" | "keep")` when delivery
or retention is requested. No separate screenshot publisher, blob store or
artifact endpoint is introduced. A request to see the current page can capture
and export directly without asking the browser decision backend to browse again.

The current `BrowserPage.screenshot` returns viewport JPEG bytes to a model;
it does not establish a workspace file, full-page capture or delivery. The new
capture operation must securely write the actual image in the bound VM/workspace
through existing file operations and return its relative path, media type,
size/digest and scope/task/tab/capture-time provenance. User-facing capture is
unmarked by default; Set-of-Marks remains an observation aid for models.
Do not embed base64 image bodies in normal logs or audit rows.

Existing export checks remain the authority for byte/type/size validation,
requester/destination permissions, publication and export provenance. Keep DM
publication rules. A successful capture is not a successful export: if delivery
fails, report the saved capture and failed delivery separately, preserving the
file for an explicitly requested retry. Full-page failure/partial coverage is
explicit; it must not be labelled complete. Model-only observation screenshots
do not automatically become published artifacts or a continuous recording.

### Audit, logging, tracing and operational evidence (2026-11-04)

Follow [the observability contract](../../docs/observability.md); reuse its
SigNoz/OTel pipeline and Sentry correlation, not another browser monitoring
stack. These signals answer different questions:

| Signal | Existing owner / required extension |
|---|---|
| Durable audit: what was attempted and what happened? | Reuse `computer_events` started/finished operations and `computer_exports`. Existing `computer_browse` records the whole task; shared executor step coverage is still required for the new target, including rejected actions and captures. |
| Trace: where did time and model cost go? | Keep one execution root with child browser/model/action/capture/export spans. Separate consecutive turns remain separate traces linked by stable task/session identity; no indefinitely open browser trace. Strands owns LLM spans; future raw model calls use the existing telemetry boundary. |
| Logs/errors: what broke operationally? | Existing structured logging, trace correlation and Sentry. Record stage, reason and identifiers needed to find the audit; no duplicated full page contents, screenshots or per-step debug prose. |
| Metrics: how often, how long and at what cost? | Reuse computer call/health/audit-failure metrics; add only missing lifecycle/capture/expiry/challenge and adapter-cost measurements needed by acceptance, with declarations and call sites together. No URLs, task IDs or scope IDs as metric labels. |

Correlate audit and trace using operation/task/session, scope, VM instance/tab,
originating turn/job, selected backend and actual served model. Record requested
action/ref against its observation revision, execution outcome and timing;
capture/export records link the image file/digest and export artifact. Retain
payloads under the existing content/storage rules by reference, rather than
copying them into every signal. The backend's proposed action is not evidence
that the executor performed it. A decision model's confidence is not task success.

Preserve the existing audit failure semantics: refusal to record a required
start prevents execution; a failed finish append after an operation ran is
logged/counted and must not erase its result. Cancellation/crash leaves an
explicit incomplete operation, not a fabricated success. Model call counts and
usage count actual invocations once, including fallback/answer synthesis;
unknown usage or unpriced cost remains unknown, not zero. Declare new payloads
through context assembly; adapter code does not build ad-hoc model prompts.

Acceptance includes one complete generated journey: open/filter a page, resume
on the next turn, capture the same task tab and export the image. Check the
actual file/digest and authorized delivery, then reconstruct task and action
outcomes from existing audit records and linked traces. Include a refused
cross-scope operation, expired handle, model/driver error and export failure;
positive controls prove execution/capture ran. Source checks do not establish
that the deployed collector retained a trace or that a chat received a file.

### Implementation seams and lane ownership (2026-11-04)

Source mapping confirms the following bounded changes; no runtime code has
been changed. The Compose/Deployment session owns image packaging, lifecycle
installation, Redis ACLs and host operations; coordinate required interfaces
there before editing those files. This lane owns application routing, queue,
BrowserRunner/session/tab behavior and capture/export integration.

| Existing seam inspected | Required change |
|---|---|
| `nora/app/composition.py` wires computer runtime, probe and browser runner to one `cfg.computer.base_url` | Bind all three through the same admitted scope-to-VM resolution; no separate browser endpoint selection |
| `nora/infra/computer/client.py` stores one base URL; methods already take `scope` | Preserve existing sandbox verbs/results while routing to the bound VM/instance |
| `nora/infra/computer/browser.py` `_attach` selects the first page and closes every extra page | Replace with explicit task-owned tab attachment; never adopt first/active tab or close sibling tasks' tabs |
| `nora/agent/browse.py` opens a connection per objective; screenshot tool returns image bytes to the model | Reconnect to the same task binding, and add direct capture-to-file through the same executor |
| `nora/domain/computer_leases.py` and tool closures use unit locks or separate job locks | Join one scope queue/admission; nested worker calls inherit admission |
| `nora/app/bridge_queue.py` demonstrates durable stream handoff; `nora/app/compute_poller.py` delivers completed backend jobs | Reuse patterns, not the bridge payload/consumer or completion state as pre-launch execution state |

For the small anonymous version, use one transient profile per VM browser
lifetime and separate owned tabs per task. Same-unit tasks share anonymous
cookies/storage; tab ownership prevents navigation interference, not storage
privacy. The unit is already the data boundary. Do not build per-task cookie
isolation unless a later requirement calls for it. Preserve profile state while
the browser lives; cleanup/expiry and no restart-persistence promise still apply.

### Implementation sequence — after design selection

One integrated scoped-browser change, built in this order:

1. Trace the current scope resolver, supervisor, runtime client, computerd and
   browser runner; pin source revision and enumerate callers before editing.
   Refresh private source inventory before planning migration; source inspection
   does not establish host state or authorize cutover.
2. Bind native computer operations and worker routing to the admitted scope's
   VM and instance. Carry that binding through lifecycle/state, files, process
   handles and CDP; unresolved scope is unavailable, with no shared-VM fallback.
3. Add scope-wide FIFO execution admission with durable intent, Redis Streams
   handoff/recovery and existing result delivery; native/worker calls share it.
   Give that VM one on-demand Chrome and each task its own tab ownership.
   Preserve continuation across turns/transports, serialize same-tab operations,
   and make cancellation, idle cleanup and expired handles explicit.
4. Expose direct screenshot-to-file capture on the same scoped task tab and
   reuse `computer_export` for requested delivery. Keep model
   reasoning outside the VM and use the selected baseline, without adding the
   historical cache, ranker or model cascade to this slice.
5. Wire shared audit/trace/log/metric coverage for all backends; verify scope
   routing, continuity, concurrency, expiry, capture/export and evidence against the
   acceptance below. Use generated scopes and focused checks, hosted checks and
   the applicable independent review. No full local suite before the PR.
6. Prepare data-preserving migration and rollback from the refreshed inventory.
   Notify the operator before deployment; only authorized runtime readback can
   establish resource cost, cold/warm behavior and actual challenge rate.

Pi's full adapter, ACP and another VM provider retain their own delivery scope;
the routing must agree with them, but implementing every adapter is not browser
acceptance. Design reconciliation and roadmap updates are the current work;
per-scope VMs, new tab lifetime and direct capture remain NOT BUILT.

### Per-scope computer queue (operator scope addition, 2026-11-04)

Two admitted rooms of one unit target the same VM. Their computer tasks must
queue instead of colliding or spending a live turn waiting. First version:
one active computer execution segment per scope/VM, FIFO among admitted ready
segments; independent scopes may progress concurrently. A segment is one
bounded browser objective, worker job or native operation, not the browser
session's whole lifetime. Release on completion/cancellation; a later turn's
continuation queues again while retaining its own tab/context. Status and
cancellation remain available while the VM is busy. This serial baseline
replaces concurrent same-VM execution as a first-version requirement; task-owned
tabs still prevent one task adopting another task's page. Revisit parallel
execution only when a measured queue delay warrants finer resource ownership.

Reuse the existing Redis service, client and Streams producer/consumer recovery
patterns, with a separate computer-work stream per opaque scope and a dedicated
consumer group. Do not put computer commands into platform-account bridge
streams or change their ingest ACK obligations. Redis `XREADGROUP` distributes
entries; consumer-group membership alone is not per-VM execution exclusion.
The existing bridge reader elects an owner with a database advisory lock;
reuse that established ownership pattern for each computer scope, with one
serial executor and shared admission for native tools and workers. If ownership
is lost, stop dispatch, cancel/drain managed operations and reconcile actual VM
process state before admitting a successor. An idle timeout/claim is not proof
that an old command or worker has stopped. A successor must not dispatch until
old execution is confirmed stopped; inability to confirm blocks that scope.

The existing `ComputerLeases` only covers non-job calls by unit; worker calls
currently use `job:<id>`. Both paths must join scope-wide admission. A queued
worker passes its admitted execution ownership to nested native/browser calls;
those calls must not enqueue behind their own parent and deadlock. Models may
not supply that ownership. Intentional detached processes remain scope-owned:
a segment may not release the VM while work it owns can still mutate the shared
computer, unless a separately specified managed handoff preserves exclusion.

Queue lifecycle:

1. Resolve admitted scope and originating actor/room/job once. Retain a stable
   request/job ID, payload reference, enqueue time/deadline, task/tab binding and
   trace link. Commit durable execution intent before publishing; reconcile a
   publish gap using existing outbox/retry patterns, not another task database.
2. Use journaled/deduplicated enqueue and report queued only after durable
   acceptance. Reuse deferred job completion/delivery for work that exceeds a
   live turn; do not leave the conversation slot held while it waits. Re-check
   current admission and expiry when it reaches the head. A queued item must
   not start under revoked permissions.
3. The elected scope executor claims the next item, checks prior completion and
   actual ownership, then executes under the shared VM admission. Persist actual
   terminal result before ACK, or commit complete durable downstream ownership
   before ACK if handing off. Reclaim handles lost delivery, not exactly-once
   execution. Never blindly repeat an uncertain click, command or export after
   a crash; reconcile it or return an explicit interrupted/unavailable outcome.
4. Pending cancellation prevents start; running cancellation stops and drains
   the managed work before releasing the slot. A cancelled/expired or definitively
   failed task becomes terminal so the next ready task can run. Preserve its
   result and originating room for delivery; no shared active-room pointer.

Existing `ComputeJob`/poller is completion delivery after backend launch, not
already a pre-execution FIFO. Its readiness/payload integration must be designed
before implementation; do not reinterpret delivery `PENDING` as queued execution.
The new execution admission must have one durable owner linked to that job,
without introducing a competing result/delivery store. Existing turn/worker
budgets apply; queue wait and execution time are measured separately. Redis or
intent-store failure is unavailable, never "queued" or an unqueued direct run.

Reuse audit/OTel with queue/claim/start/finish/cancel and outcome timestamps,
origin/task/VM correlation, wait duration and oldest pending age; no per-scope
metric labels or duplicate page payloads. Acceptance: two rooms of one unit
submit together and run in enqueue order with maximum one active segment;
another unit progresses; continuation resumes the original tab; nested worker
tools do not deadlock; pending/running cancellation, revocation, expiry,
restart/lost-ACK and publish-gap recovery neither lose work nor blindly repeat
uncertain effects. Prove positive execution as well as refused overlap. No new
queue daemon, runtime implementation or host operation ships in this document.

### Migration prerequisites and acceptance

The next implementation must first refresh the current shared-VM inventory and
scope routing. Preserve the shipped room-to-unit mapping, existing files,
private browser data and session/audit provenance. Pause admission and drain
jobs/leases before taking verified private backups; reconcile source-to-archive
inventory and prove restore before moving data. Migrate each existing unit
workspace without flattening legacy room prefixes or merging profiles. Preserve
old profile bytes as private data, without reusing their logins in the new
anonymous browser. Unknown scope ownership or a conflicting destination blocks
cutover with source data intact. Keep rollback mapping and backups until readback.
The [DM owner-map and backup prerequisites](../private-dm/04-build-and-acceptance.md)
remain open; a VM split does not declare them satisfied. This is a prerequisite
specification, not a new host runbook or permission to run the old cutover again.

Acceptance for #520 uses generated scopes and the existing sandbox/worker
contracts, plus later explicitly authorized runtime readback:

- Two transports of one unit resolve one VM and reuse a live anonymous session;
  a different unit and a DM resolve distinct VMs. Two anchor units for the same
  person resolve different DM VMs; admitted linked DM transports share only the
  same canonical DM scope. Unknown/unadmitted scope is unavailable before work.
- Native tools and Pi/ACP agree on workspace and browser identity for the same
  scope. Cross-scope management, state, session/process/tab handles and CDP are
  refused, including forged IDs, reconnects and stale-instance handles.
- Native and worker browser tasks in one scope retain separate tabs;
  same-VM execution segments serialize through the scope queue/admission, even
  across separate task tabs. Existing `job:<id>` leases alone are not browser
  exclusion. Different scoped VMs progress independently; cancellation/idle cleanup leaves active tasks intact.
- Consecutive-turn snapshot and direct viewport/full-page image captures are
  verified against the intended tab. Restart/idle expiry returns unavailable
  with expired detail; no hidden persistence or cross-task tab substitution.
- Migrated files, modes, ownership, digests and session bindings reconcile;
  interrupted/conflicting migration preserves sources and refuses enablement.
  DM private-to-unit publication remains governed by the existing DM contract;
  VM isolation is not permission to copy private bytes into a unit workspace.
- Measure cold start, warm reuse, idle resource release, concurrent-scope cost,
  task success and challenge rate against the recorded fingerprint baseline.
  No unmeasured anti-bot guarantee or runtime acceptance from docs checks.

**Outside this slice:** streaming/noVNC, vault/credentials/login implementation,
site/script tier cache, small decision models, GPU and stealth/alternative
engines. Existing host social browsing remains outside this change. No runtime,
config, deployment or host operation is authorized by this planning update.

## Historical accepted design and delivery

Status (2026-10-07): **accepted (ADR 0022); B1a built** (Chrome in the image, a per-room
browser in computerd over the pipe; `deploy/computer/README.md` «The browser»). B1b built (egress `**` with the private ranges denied, DEC-289); B2 built, off by default (`computer_browse`: a Flash sub-agent over an accessibility snapshot with refs, a marked screenshot on request, and a `needs_human` hand-off; `[computer.browse]`). B3 on are not built. This bundle is the design for
S4 of `design/sandbox-runtime/00-design.md` (§14) plus the credential store
that S4 needs and the sandbox design deferred (§8, decision 3). It is written
from an operator conversation on 2026-10-06/07 and three research passes
(`02-research.md`). The decision it asks for is
`docs/adr/0022-noras-browser-is-headed-chrome-with-a-vault-fill-broker.md`
(accepted 2026-10-07).

Files:

| file | holds |
|---|---|
| `00-design.md` | this: the shape, the tiers, display and streaming, the sequence |
| `01-vault.md` | OpenBao, the credential record, the fill broker, redaction |
| `02-research.md` | how Hermes, pi/OpenClaw, CUA and others do it; decision models; GPU |
| `03-open-questions.md` | what the operator still has to rule on, and what to measure |
| `04-s1-runbook.md` | S1 on norty: the commands, the pass/fail reading of each, and the results |

## 0. What exists today (read 2026-10-06)

- **The computer** is one persistent sbx microVM, `nora-computer`, with
  computerd as its only API and Nora as its only client (ADR 0019, DEC-243).
  Per-room folders `/workspace/<scope>` and a per-room system user.
  3 GB / 2 CPUs. Egress is deny-all plus PyPI and the Ubuntu archive
  (DEC-260; superseded 2026-10-07 by DEC-289, the public web with the
  private ranges denied). Image: `deploy/computer/Dockerfile`.
- **No browser in it.** The only Chromium is Puppeteer's, for mermaid-cli.
  No Xvfb, noVNC, Playwright or CDP proxy. The model's tools are
  `computer_run/read/write/edit/export/import/session_list`
  (`nora/agent/tools.py`); `computer_browse` is not built.
- **The one live browser** is on the host: headed Chrome on TigerVNC `:2`
  with the X/LinkedIn/Instagram logins, driven by OpenCLI through its
  Browser Bridge extension, behind `nora/infra/social/bridge_server.py`'s
  read-only allowlist (DEC-201, R-86). `nora/infra/social/session_probe/`
  reads that Chrome's cookie file to watch the sessions.
- **No credential store.** Secrets live in the host `.env` and reach services
  key by key through `docker-compose.yml`. sbx's `secret set-custom`
  (placeholder substituted by the host proxy) is designed but unused (§8).
- **Decision 3 (2026-10-01): no browser logins for now.** This bundle asks
  for it to be superseded (ADR 0022).
- **The host (read on norty 2026-10-07):** a GMKtec NucBox M7 Ultra mini PC,
  AMD Ryzen 7 PRO 6850U (x86-64, 16 threads), 27 GiB RAM with 15 GiB
  available, a Radeon 680M iGPU, two USB4 ports, a TPM 2.0 (`/dev/tpm0`).
  x86-64 means Google Chrome stable installs. There is **no internal slot
  for a GPU** (§5.3). `nora-computer` runs at 3 GiB / 2 CPUs.
- **The sbx proxy (read in `nora-computer` 2026-10-07):** HTTP(S) leaves
  through `gateway.docker.internal:3128` as `CONNECT`. For the allowlisted
  hosts probed (`pypi.org`, `archive.ubuntu.com`) the VM saw the sites' own
  certificates (GlobalSign, Let's Encrypt), so **the proxy tunnels TLS
  rather than terminating it** for a plain host rule. The VM also trusts a
  proxy CA (`PROXY_CA_CERT_B64`, `proxy-ca.crt` in the system store), which
  is what placeholder secrets (`secret set-custom`) need: a host with a
  secret is intercepted (round 2's httpbin probe). HTTP `--method`/`--path`
  rules need it too (inferred from the help text, not probed).
- **sbx policy language (`sbx policy allow network --help`, v0.46.0):**
  hostnames, `*.` and `**.` wildcards, `**` for everything, IPs and CIDRs,
  ports, TCP by default; deny wins over allow for the same host or CIDR,
  but **"an allowed hostname isn't checked against CIDR rules for its
  resolved IP address"** (§3, egress).

## 1. Operator direction (2026-10-06/07)

- Build browser use next, inside the sandbox.
- It must not be blocked by anti-bot systems on every visit: install real
  Chrome; decide headless vs a virtual display.
- Nora may log in to real accounts, including a Google account prepared for
  her.
- OpenCLI should be able to move to that browser in time.
- Faster and cheaper than browser-use; the present S4 plan felt primitive, so
  survey what others (Hermes Agent, pi/OpenClaw, CUA) do.
- Credentials cannot scale through a file or env vars: a vault. **OpenBao**
  preferred over HashiCorp Vault.
- DeepSeek v4.1 Flash supports vision, so the model itself may read a
  screenshot, besides a vision tool.
- A small decision model (Jev-like: Laya, Tev1, Strands Decider 2B or any
  other) may pick steps; fitness first, speed second. An RTX A3000-class GPU
  may be added.
- The portal should be able to stream Nora's screen, view-only.

## 2. The shape

```text
 Nora container (trusted)                       nora-computer VM (sbx)
 ┌───────────────────────────────┐   CDP over   ┌──────────────────────────────┐
 │ main loop (DeepSeek Flash)    │   computerd  │ Xvfb :99 ── Google Chrome    │
 │   computer_browse(objective)  │◀────────────▶│   profile per scope / login  │
 │ browse sub-agent (Strands)    │   proxy      │ x11vnc → noVNC (view/take)   │
 │   tier 1 adapter              │              │ computerd: CDP proxy,        │
 │   tier 2 cached script        │              │   screencast relay           │
 │   tier 3 decision model ──┐   │              └──────────────────────────────┘
 │   tier 4 Flash + vision   │   │
 │ fill broker ──── OpenBao  │   │   host: OpenBao (transit, TOTP), GPU server
 └───────────────────────────┼───┘   for the decision model (llama.cpp / vLLM)
                             └── localhost / OmniRoute; page content stays on norty
```

The sandbox design's rule holds: **the model loop is outside the VM**, so no
model key and no vault token enters it. What enters the VM is page traffic,
and, at the moment of a fill, the one credential value the page needs.

## 3. Chrome: headed on Xvfb, real Google Chrome

- **Headed on a virtual display, not `--headless`.** `--headless=new` is the
  real engine but carries fingerprint differences (screen and window metrics,
  GPU stack, some API surfaces). More decisive: a display is what makes a
  human hand-off (§6) and a stream (§7) possible. Xvfb costs tens of MB; S1
  measures the whole.
- **Google Chrome stable, not Chromium,** for the fingerprint closest to a
  real user. Linux Chrome is x86-64 only, and norty is x86-64 (§0). Pin the version in the Dockerfile and update it on
  a schedule, since a stale Chrome is itself a signal.
- **What actually decides blocks, in order:**
  1. **Exit IP reputation.** norty exits on a residential HKT Netvigator
     address (AS4760, S1 step 6), the best case; Google still challenged a
     fresh profile once.
  2. **CDP automation leaks** (`Runtime.enable`, `navigator.webdriver`).
     Every CDP driver has them; Patchright and nodriver patch them. The
     extension-based control OpenCLI uses avoids the class.
  3. **The sbx egress proxy.** If it terminated TLS, the site would see the
     proxy's TLS fingerprint, not Chrome's: a strong bot signal. **It does
     not:** S1 measured the same JA4 for one Chrome build in the VM through
     the proxy and on the host directly (`04-s1-runbook.md` step 3). Two rules follow: **never put an sbx secret or an HTTP
     method/path rule on a host the browser visits** (that host would be
     intercepted), and launch Chrome with `--proxy-server` pointing at the
     proxy rather than relying on env vars. QUIC is UDP and the rules are
     TCP, so Chrome falls back to HTTP/2 over TCP.
  4. **No GPU in the VM:** WebGL reports a software renderer, headed or not.
  5. Behaviour (timing, mouse paths); least important for our volume.
- **Not every block is avoidable.** Turnstile, hCaptcha and DataDome will
  still challenge. The answer is the hand-off (§6), not a solver service.
- **An anti-detect backend is an option, not the default:** Camoufox
  (Firefox, Juggler, not CDP) as a second engine for sites that block Chrome,
  measured as a spike arm (`03-open-questions.md`).
- **Egress (operator, 2026-10-07): `**` over TCP for `nora-computer`,
  with the private ranges denied.** No extra proxy. S1 measured the one
  gap this leaves (`04-s1-runbook.md` step 4): a CIDR deny applies to IP
  literals only, so a public name that resolves to a private address
  (`127-0-0-1.sslip.io`) still reached host services. The operator accepted
  that risk rather than add a guard. The network audit is `sbx policy log`.
- **The Google challenge.** One search from a fresh profile on norty's
  residential IP got `/sorry/` (S1 step 6, one page). Anti-bot is a real
  cost here even with a good IP; B3's anti-detect arms measure it.

## 4. Where logged-in browsers live

**Ruled 2026-10-07 (operator): one sandbox.** Logged-in profiles live in
`nora-computer` beside the anonymous ones; there is no `nora-browser` VM.
The second VM was the recommendation (`03-open-questions.md` §1.3) and was
declined. A logged-in profile is still a credential store next to a shell
the model drives, so the boundary that a second kernel would have given
becomes a boundary of Unix users and file descriptors inside the one VM, the
way computerd already separates rooms (ADR 0019 Decision 2):

| domain | what | where, and who can touch it |
|---|---|---|
| **anonymous** | per-scope profiles, no logins, wiped freely | `/workspace/<scope>/browser/<profile>`, owned by the scope's user |
| **logged in** | Nora's own accounts (Google first; X/LinkedIn via OpenCLI at B7, conditionally) | `/var/lib/nora-browser/<account>`, mode 0700, owned by a dedicated system user `browser` that no `computer_run` command runs as |

What makes that boundary hold, each a B1/B4 test, not a hope:

- **No TCP debugging port.** `--remote-debugging-port` binds localhost with
  no auth, so any room's shell could connect and drive or read any Chrome,
  logged in or not. Every Chrome is started by computerd (root) with
  `--remote-debugging-pipe`, and computerd holds the pipe. The test: a
  `computer_run` in any scope finds no listening CDP port and cannot attach.
- **One X display per domain,** each Xvfb with `-nolisten tcp` and an
  `-auth` cookie readable only by its owner, so a room's shell cannot
  screenshot or key-log the logged-in display. x11vnc for the logged-in
  display listens on a socket only computerd relays.
- **Chrome's password manager is off by policy** (`PasswordManagerEnabled`
  false) in every profile, so a filled password is never saved into the
  profile's `Login Data`.
- **What the one VM costs:** a local privilege escalation from any room's
  shell to root reads every logged-in cookie. A second VM would have made
  that a VM escape. ADR 0022 Worse records it.
- **Egress is per sandbox in sbx,** so the logged-in profiles share
  `nora-computer`'s egress and cannot have an allowlist of their own at the
  network. Their own fence is Chrome's `URLAllowlist`/`URLBlocklist`
  policy on the logged-in profiles, listing exact hosts only (no
  `*.google.com`; `script.google.com`, `sites.google.com` and
  `*.googleusercontent.com` serve content an attacker controls). That fence
  is enforced by the browser, not the network, and it is the weaker one.
- **Against sandbox decision 2** (one VM per social unit, #678, not built):
  when #678 lands, Nora's accounts have to live in one unit's VM, beside
  that unit's shells, or be signed in once per VM. The ruling is reopened
  then.
- **OpenCLI's daemon** listens on localhost with no auth, so in the one VM
  a room's shell could call it, bypass the bridge's allowlist and post as
  the account. **OpenCLI moves only if** a firewall rule inside the VM
  (owner match: only `browser` may connect to the daemon's port) makes a
  room's shell get refused, measured from a `computer_run`. Otherwise it
  stays on the host, and so does the social Chrome. If it moves, the bridge
  stays on the host and only its runner changes: today it spawns `opencli`
  locally (`_spawn_opencli`); then it runs the same argv in the VM as
  `browser` (through `sbx exec`, 0.67 s warm in the sandbox spike). The
  allowlist still builds the argv host-side, so R-86's three layers hold
  unchanged. `session_probe` then reads the cookie file inside the VM.
- Profiles are never in the nightly tar backup (sandbox §7; DEC-263 leaves
  out each room's `browser/`, and `/var/lib/nora-browser` is outside
  `/workspace`). A logged-in profile's cookies may be snapshotted encrypted
  through OpenBao transit (`01-vault.md` §5), so a recreate is a restore,
  not a re-login.

## 5. The driver: `computer_browse` and four tiers

The unit stays coarse (sandbox §14): one `computer_browse(objective,
session?)` call from Nora, one typed result back (`ok` / `nothing_found` /
`unavailable`, R-27), whatever happens inside. Inside, the sub-agent climbs a
ladder and pays model calls only where the cheaper tier failed:

1. **Site adapter, no model.** Deterministic code per site and task, the way
   OpenCLI's X and LinkedIn adapters are. For flows Nora runs constantly.
2. **Cached script, no model.** A successful run is compiled into a replay
   keyed by `(origin, task signature)`. Steps store role, accessible name and
   a selector, never the `@eN` ref (refs change per load). On replay a step
   that fails calls tier 3/4 for that step only, and the script is updated
   (self-healing, Stagehand's pattern; Agent Workflow Memory's reuse). The
   first run of a flow pays; later runs do not.
3. **Decision model per step.** A small local model receives the goal, a
   pre-ranked candidate list and the last result, and returns one typed
   action with a confidence (§5.1).
4. **DeepSeek v4.1 Flash, with vision.** For planning and judgement (compare
   prices, what is "best", an injection-shaped page), when tier 3 escalates,
   or fails twice on a step.
5. **A human** (§6), for CAPTCHA, 2FA, consent and anything ambiguous.

What the model sees is the **accessibility snapshot with `@eN` refs**
(agent-browser), diffed between steps. Extraction is DOM/JS, not the model.
Nora's own script tier (a Playwright script through `computer_run`) stays,
for anonymous profiles only: it runs as the room's user, which cannot reach
a logged-in profile (§4).

### 5.1 The step-picker contract (any decision model)

The slot is model-agnostic. A candidate qualifies on fitness first, then
speed (`02-research.md` §4):

- **In:** objective; the pre-ranked candidates (ref, role, name, value
  redacted, a little context); the last action and its result; steps left;
  optionally a Set-of-Marks screenshot.
- **Out, exactly one of:** `click @e`, `type @e "text"`, `select @e
  "option"`, `scroll dir`, `press key`, `fill_credential cred:<handle> @e`,
  `done(result)`, `escalate(reason)`; plus a confidence.
- **Fail closed:** output that does not parse, or a ref not in the
  candidates, is a failed step; below the confidence floor is `escalate`.
  Never a guess.
- **The picker names a credential handle, never a value.** The broker
  (`01-vault.md`) fills.
- **Pre-ranker in front.** Every small decision model sees at most 10–26
  options or a 4k-token context (`02-research.md` §4); a real page has
  140–580 interactive elements.
  First filter (visible, in viewport, interactive role), then rank (a small
  text ranker in the MindAct style, CPU-cheap) to the top K.

### 5.2 Vision

- The snapshot is the default; a screenshot is a tool the sub-agent calls,
  or attaches when the snapshot is ambiguous, the page is canvas/map-heavy,
  or a check needs eyes.
- **Set-of-Marks:** each `@eN` drawn as a numbered box on the screenshot, so
  a vision answer is a ref, not a pixel coordinate. A coordinate-native model
  (Holo, Fara, UI-TARS) gets an adapter that maps a click to the mark under it.
- DeepSeek v4.1 Flash reads it directly, if OmniRoute and the provider pass
  images through. **Measured, S1 step 5:** through the `nora` alias, 5/5
  random numbers read exactly, an image costing about 200 prompt tokens at
  400×120, and the upstream names itself `deepseek-v4.1-flash`.

### 5.3 Where the decision model runs

norty has no internal slot for a GPU (§0). Three options, in order of
cost:

1. **CPU, 16 threads.** Tev1-0.8B, Laya and the pre-ranker are small
   enough. Strands Decider 2B is the question; B3 times it.
2. **The Radeon 680M iGPU** through llama.cpp's Vulkan backend, for GGUF
   models. Strands Decider ships its own PyTorch server, and ROCm does not
   officially support the 680M, so this option may not cover it.
3. **A desktop card in an external enclosure** on USB4 (present). The
   weights stay in VRAM, so the tunnel's lower bandwidth costs load time
   and little per step (reasoning, not measured). 12 GB or more
   (`02-research.md` §5).

Buy the card only if B3 shows the picker tier wins on fit and CPU latency
is what stops it.

## 6. Human hand-off

- The sub-agent returns `unavailable` with reason `needs_human` and a portal
  link; Nora tells the room or the operator.
- The operator opens noVNC through the portal in **interactive** mode on the
  right sandbox and display, solves the challenge or signs in, and releases.
- First sign-in to every account is by hand this way: Google refuses sign-in
  from a browser it reads as automated. Chrome is launched normally on Xvfb;
  automation attaches afterwards.

## 7. Streaming the screen to the portal

Two paths, both built eventually:

| path | what | for |
|---|---|---|
| **CDP screencast** (`Page.startScreencast`) | JPEG frames on change, relayed computerd → Nora → portal websocket; works headless too; one tab | the view-only "watch Nora browse" panel |
| **noVNC over x11vnc** | the whole Xvfb display, view-only or interactive | the hand-off (§6) |

- Access: portal sign-in. An anonymous profile's stream is limited to its
  room's owner and the operator. A logged-in profile serves every room its
  credential's `scopes` name, so its stream is shown only for the task the
  viewer's own room started, and only while that task runs. **noVNC on a
  logged-in display is the operator's only**: interactive control of it is
  control of Nora's accounts.
- **The stream blurs or pauses while the broker fills** a credential field.
- **No recordings** (operator, 2026-10-07). Steps are audited as
  `computer_events` rows. Revisit when a failure cannot be explained from
  them.

## 8. Sequence

| step | what | gate |
|---|---|---|
| **S1** | the runbook in `04-s1-runbook.md`: Chrome + Xvfb RSS, TLS fingerprint through the proxy, rebinding under a `**` rule, exit IP, Flash image passthrough | numbers recorded in `04-s1-runbook.md` §Results |
| **B0** | ADR 0022 accepted | operator |
| **B1** | the `**` egress rule (§3); CDP over `--remote-debugging-pipe` in computerd, scoped per room; screencast relay | a scope reaches only its own browser; no CDP port listens |
| **B2** | `computer_browse` + sub-agent, tiers 4 and 5 only (Flash, hand-off) | nora-tool routing measurement |
| **B3** | spike (`03-open-questions.md` §2): decision-model arms, Camoufox arm | measured, fail-closed |
| **B4** | OpenBao + fill broker + redaction (`01-vault.md`); the `browser` user and its display; Nora's Google account | a room's shell cannot read `/var/lib/nora-browser` or its display; a credential reaches only a bound field on its bound origin; zero plaintext in traces |
| **B5** | tier 2 cache + tier 3 picker as chosen by B3 | second-run cost |
| **B6** | portal stream view-only, then noVNC hand-off | access test |
| **B7** | only if the owner-match rule holds (§4): OpenCLI into the VM as `browser`, behind the bridge; retire host social Chrome | a room's shell is refused by the daemon; R-86 still holds at three layers |
