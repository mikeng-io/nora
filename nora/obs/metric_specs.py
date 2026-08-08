"""Every metric Nora declares — the names, labels and help text, nothing else.

Split out of `nora.obs.metrics` when that module hit 299 of the 300-line hard
cap (`tests/architecture/test_module_size.py`) with three features queued
behind it. The seam is real regardless of the line count: WHAT is measured is
a slow-moving registry the whole codebase reads, while HOW it is recorded is
one class with a prometheus-client registry inside it.

**Metric names are append-only.** This is an SRE's repo — a rename breaks
dashboards and alerts silently, and `tests/test_metrics_export.py` enumerates
`DECLARED_NAMES` to enforce it. Add to the end of a tuple; never edit a name.
"""

from __future__ import annotations

from typing import NamedTuple

_DEFAULT_BUCKETS: tuple[float, ...] = (0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30)


class _CounterSpec(NamedTuple):
    name: str
    help_text: str
    labelnames: tuple[str, ...] = ()


class _GaugeSpec(NamedTuple):
    name: str
    help_text: str
    labelnames: tuple[str, ...] = ()


class _HistogramSpec(NamedTuple):
    name: str
    help_text: str
    buckets: tuple[float, ...] = _DEFAULT_BUCKETS
    # Histograms were label-free until `nora_llm_latency_seconds` gained
    # `audience` (see its entry below). `Metrics.observe` had accepted `**labels`
    # and recorded them only in its test-readback dict all along, so a call site
    # passing one got a silently unlabelled sample — which is why this field, not
    # the keyword, is what makes a labelled histogram real.
    labelnames: tuple[str, ...] = ()


# Every metric name below already existed before this module's prometheus-client
# rewrite — names are append-only (an SRE's repo; renames break dashboards).
# `platform` is new on the four series spec §6 calls out (today only
# `nora_messages_total` had one, so with N adapters a stuck/failing platform
# was invisible in the other three): nora_tags_total, nora_replies_total,
# nora_silent_failures_total, nora_errors_total.
_COUNTERS: tuple[_CounterSpec, ...] = (
    _CounterSpec("nora_messages_total", "Ambient messages ingested, by platform.", ("platform",)),
    _CounterSpec(
        "nora_tags_total", "Tags detected and accepted for handling.", ("platform",),
    ),
    _CounterSpec("nora_tags_debounced_total", "Tags dropped by the debounce window."),
    _CounterSpec(
        "nora_tags_queued_total",
        "Tags placed in a conversation's FIFO queue behind an in-flight turn (R-29).",
    ),
    _CounterSpec(
        "nora_tags_dropped_total",
        "Tags dropped for a reason other than debounce (today: reason=\"queue_full\", "
        "a per-conversation queue at its flood bound).",
        ("reason",),
    ),
    _CounterSpec(
        "nora_replies_total", "Delivery attempts, by outcome status.", ("status", "platform"),
    ),
    _CounterSpec(
        "nora_errors_total", "Internal errors, by processing stage.", ("stage", "platform"),
    ),
    _CounterSpec(
        "nora_silent_failures_total",
        "Failures kept silent from the group, by reason and by which path was "
        "speaking (audience=\"tag\" | \"heartbeat\").",
        # `audience` is the ONE label the heartbeat adds to an existing series
        # (design spec §6.2, §13), and it is added rather than the fire path
        # getting its own silent-failure series, because isolation-by-omission
        # was tried twice and undercounted twice: `Deliverer` — which the fire
        # path MUST use, being the only caller of `adapter.send` — increments
        # this on five branches of its own, so any "the fire path just doesn't
        # call the counting code" scheme is wrong by construction.
        #
        # **A8 is now `audience="tag"`-filtered** (`docs/runbook.md`). Without
        # the filter a fire-path failure spends a <1% budget sized for
        # conversation; without the LABEL at every call site, an unlabelled
        # increment records `audience=""` (`Metrics._full_labels`) and is
        # excluded from that filter — silently, and most damagingly at the
        # `handler_error` site, which is R-5's last-resort signal.
        ("reason", "platform", "audience"),
    ),
    _CounterSpec("nora_guard_vetoes_total", "ReplyGuard vetoes, by rule.", ("rule",)),
    _CounterSpec(
        "nora_leak_blocks_total", "Replies blocked by the doctrine/canary leak guard.",
    ),
    _CounterSpec(
        "nora_egress_blocks_total",
        "Replies blocked for carrying somebody's identifiers, by kind.",
        ("kind",),
    ),
    _CounterSpec("nora_doctrine_reload_failures_total", "Doctrine hot-reload failures."),
    # RETAINED frozen at 0 by the append-only metric-name policy
    # (PRESERVED_METRIC_NAMES), not deleted — a dropped name breaks any dashboard
    # summing it. It counted SQLite WAL SQLITE_BUSY, which MVCC cannot produce,
    # so nothing has incremented it since the 2026-08-07 Postgres migration.
    _CounterSpec(
        "nora_store_busy_total",
        "SQLite-era store-contention counter (frozen; MVCC cannot produce it).",
    ),
    # An unmapped sender is an EXPECTED outcome (D6), so it is not an error —
    # but it was previously unobservable, and its symptom set is empty: the
    # message is still audited, the gate still runs, the prefill still shows
    # the sender's platform display name, and nothing reaches Honcho. Nora
    # looks like she is working and remembers nothing. Counting it is what
    # separates "nobody talked to her" from "her identity file is wrong, or
    # you edited it and never restarted" (mapping is startup-only, Q4).
    _CounterSpec(
        "nora_unmapped_senders_total",
        "Ambient messages whose sender resolved to no person.",
        ("platform",),
    ),
    # Deny-by-default has to be visible. "She stopped answering in that group"
    # must be answerable without a debugger: a denied ROOM is refused before
    # the audit write (nothing is recorded), a denied SENDER is refused only
    # at the point of answering (the message IS recorded, which is what
    # `nora persons pending` reads).
    _CounterSpec(
        "nora_denied_conversations_total",
        "Ambient messages from a conversation absent from policy.toml.",
        ("platform",),
    ),
    _CounterSpec(
        "nora_denied_senders_total",
        "Tags from a sender not allowed in that conversation.",
        ("platform",),
    ),
    _CounterSpec(
        "nora_adapter_restarts_total",
        "Adapter listen() loop restarts after a failure, by platform.",
        ("platform",),
    ),
    # Lazy adoption. A backfill that silently does nothing is an R-5 failure;
    # nora/app/adoption.py documents why these two are not redundant (a failed
    # feed is left unmarked on purpose, so it retries on the next start).
    _CounterSpec(
        "nora_lazy_adoptions_total",
        "Unattributed audit rows fed and marked by the startup backfill.",
        ("platform",),
    ),
    _CounterSpec(
        "nora_adoption_failures_total",
        "Adoptable rows whose Honcho feed failed; left unmarked to retry.",
        ("platform",),
    ),
    # Progress reactions (nora/engagement/stages/indicate.py). Reactions are
    # decoration and every failure of one is swallowed so it can never touch
    # the reply — which is exactly why they need a counter, or "she stopped
    # showing the eyes" becomes another unobservable silence. One name, three
    # labels, because the interesting questions are all cross-cuts of the same
    # series: `phase` (ack/done/gave_up/clear) says WHICH indicator, `status`
    # (sent/failed/unsupported) says how it went. `unsupported` is not a
    # failure — it is the steady state of any platform without reactions, and
    # summing it into `failed` would make a correctly-degrading adapter look
    # broken forever.
    _CounterSpec(
        "nora_reactions_total",
        "Progress-indicator reactions attempted, by lifecycle phase and outcome.",
        ("platform", "phase", "status"),
    ),
    _CounterSpec(
        "nora_tool_scope_violations_total",
        "A model-supplied tool argument disagreed with the scope the tool was "
        "bound to (e.g. load_transcript asked for a conversation_id other than the "
        "tagged one) and was ignored rather than honoured, by tool.",
        ("tool",),
    ),
    # R-17. Nora's own-identity enrichment degraded. This one exists because it
    # was ALREADY degrading, on every single start, for the whole life of the
    # deployment: `derive_aliases` read `/api/v1/accounts`, an endpoint
    # signal-cli has never served (its `--http` mode exposes exactly three:
    # /api/v1/rpc, /api/v1/events, /api/v1/check), so `aliases` fell back to
    # `{primary}` with one log line and no series to alert on. R-12
    # ("never treat my own message as a tag") then collapsed to a plain
    # equality check against the phone number, and a self-send arriving under
    # her ACI would have been treated as somebody else's tag. `reason` is the
    # useful cut: `unreachable` is an ops problem, `no_self_contact_row` and
    # `bad_payload` mean the daemon answered but not with what we parse — a
    # signal-cli upgrade breaking the shape, which is the failure mode that
    # would otherwise reproduce the original silence exactly.
    _CounterSpec(
        "nora_identity_enrichment_degraded_total",
        "Nora's own-identity derivation fell back to fewer id forms than asked "
        "for, by platform and reason.",
        ("platform", "reason"),
    ),
    # The three room-scoped tools (`group_members`, `lookup_person`, `about`),
    # by how they ANSWERED. `status` is the whole point: `unavailable` means
    # Nora could not read the group's membership at all, and it must never be
    # summed together with `not_found` — one says "I could not look", the other
    # says "I looked and nobody here is called that", and a tool that cannot
    # tell an operator which of those it did is the `peer_card` failure again.
    # `not_found` is deliberately identical for a person who is in
    # `identity.toml` but not in this room and for one who does not exist
    # anywhere: distinguishing them in the answer would disclose membership by
    # behaviour (design/12 §3). Alert on a sustained `unavailable` — that is
    # signal-cli, not the model.
    _CounterSpec(
        "nora_room_lookups_total",
        "Room-scoped tool calls, by tool and how the tool answered "
        "(ok / not_found / ambiguous / unavailable).",
        ("tool", "status"),
    ),
    # Honcho's `representation` endpoint specifically, and it exists because
    # that endpoint cannot be trusted to fail loudly: an unknown peer answers
    # **200 with an empty string**, not 404, so "asked and nothing known"
    # (`empty`) and "could not ask" (`unavailable`) are indistinguishable at
    # the HTTP layer and only the call site knows which it got. `peer_card`
    # returned `[]` for both for the entire life of the deployment and nobody
    # could see it. Alert on `ok == 0` while the series is being written at all.
    #
    # **The NAME keeps `recall` after the tool was renamed to `about`
    # (DEC-39), and that is the append-only rule, not an oversight.** A metric
    # name is a dashboard, an alert and a retention window someone else owns;
    # renaming it does not move history, it starts a new empty series beside a
    # frozen one, and R-17's argument is that a series which can never
    # increment is indistinguishable from a deleted one. The tool LABEL on
    # `nora_room_lookups_total` did move, because that one must agree with the
    # name in `data/nora.log` or `_log_call` stops being greppable — a split
    # there is meaningful (the tool genuinely changed) rather than a lie.
    _CounterSpec(
        "nora_recall_total",
        "Durable-profile lookups against Honcho by the `about` tool, "
        "by outcome (ok / empty / unavailable). Named for that tool's former "
        "name, `recall`; metric names are append-only.",
        ("status",),
    ),
    # R-23. HOW the agent loop ended, every turn, straight off Strands'
    # `AgentResult.stop_reason`. This exists because the SDK reports a turn
    # that ran out of budget by returning normally: `_check_limits` yields an
    # `EventLoopStopEvent` with `stop_reason="limit_total_tokens"` and stops —
    # no exception, no log line, no warning anywhere in it. Nothing in `nora/`
    # read `stop_reason` at all, and `LiveAgentRunner.invoke` discarded the
    # `AgentResult` outright, so a turn cut off by the token cap was
    # BYTE-IDENTICAL to a model that simply chose not to call `send_reply`:
    # both surfaced as `nora_silent_failures_total{reason="no_send_call"}`
    # plus a ⚠️. The single most likely cause of a silent turn was
    # indistinguishable from the most benign one.
    #
    # Recorded on EVERY completed invocation, not only the failing ones, so
    # the healthy shape is visible too — `end_turn` and `tool_use` are the
    # normal values and a series that only ever carries `limit_*` cannot tell
    # an operator what fraction of turns are being cut off. Alert on any
    # sustained `limit_*`; raising a cap without this counter just moves the
    # cliff somewhere nobody is looking.
    #
    # **`audience` for the reason `nora_silent_failures_total` has one**, and it
    # was missed when the heartbeat's fire path shipped. Design §13 named the
    # tag-path series a fire turn must not silently move and this was not on the
    # list, so a reminder's turn began sharing it unlabelled. The consequence is
    # specific rather than tidiness: `2026-08-08-turn-budget-finding.md:224`
    # recommends alarming on `{reason=~"limit_.*"}`, and without the label that
    # alarm cannot say whether a tag or a reminder ran out of budget — the two
    # have different caps, different timeouts and different fixes. Names are
    # append-only; labels are not (see this module's header).
    _CounterSpec(
        "nora_llm_stop_reason_total",
        "How the agent loop terminated, by Strands stop_reason "
        "(end_turn / tool_use / limit_turns / limit_total_tokens / ...) and by "
        "which path was speaking (audience=\"tag\" | \"heartbeat\").",
        ("reason", "audience"),
    ),
    # The tools that had NO counter of their own — `now`, `load_transcript`,
    # `send_reply`, and now `calculate` (DEC-35), whose two
    # statuses are `ok` and `refused`: a refusal counted as `ok` would make
    # "is the model writing expressions she can actually evaluate" unanswerable
    # from /metrics, which is this counter's own failure in a new place. Same
    # `{tool, status}` shape as
    # `nora_room_lookups_total`, and deliberately NOT a superset of it: names
    # are append-only, `nora_room_lookups_total` is already on dashboards, and
    # a second series counting the same three calls would double every sum of
    # tool volume. The two together are every tool call Nora makes; neither
    # alone is.
    #
    # This exists for the same reason DEC-28 does. On 2026-08-06
    # `grep lookup_person data/nora.log` returned zero and that was read as
    # "the tool was never called" — but no tool logged its own name on any
    # success path, so the grep returned zero whether it ran every turn or
    # never. Counters are the half of that fix an operator can alert on; the
    # log line in `nora.agent.tools._log_call` is the half they can grep.
    _CounterSpec(
        "nora_tool_calls_total",
        "Native tool calls that have no counter of their own, by tool and how "
        "the tool answered (ok / not_found / nothing_known / unavailable / "
        "refused). The three room-scoped tools are counted by "
        "nora_room_lookups_total instead, never here.",
        ("tool", "status"),
    ),
    # DEC-34. An @-mention Nora could not put a name to. It renders `@someone`
    # — never the number, never the ACI — so the model's view is honest and
    # completely uninformative, and this counter is the ONLY place the loss is
    # visible. It reads like `nora_unmapped_senders_total`'s other half: that
    # one says "somebody talked to her and she does not know who", this one
    # says "somebody was TALKED ABOUT and she does not know who", and both have
    # the same fix and the same trap — `identity.toml` is loaded once at
    # startup, so editing it without a restart moves neither number.
    _CounterSpec(
        "nora_unmapped_mentions_total",
        "@-mentions rendered as an anonymous handle because the mentioned "
        "account resolved to no person.",
        ("platform",),
    ),
    # DEC-42. A turn that sent SOME of its messages and then stopped for a
    # reason that was not "she was finished".
    #
    # This is the failure multi-message introduces, and it is invisible to
    # every series that already exists. R-23's work made a SILENT turn's cause
    # legible — `no_send_call` versus `limit_total_tokens` — but a turn cut off
    # after message 2 of 4 is not silent: `delivered` is True,
    # `check_outcome` returns True, the group gets ✅, Honcho is fed, and
    # `nora_silent_failures_total` never moves. The group simply reads half a
    # thought, ending mid-sentence, with nothing anywhere saying so.
    #
    # `nora_messages_per_tag` cannot answer it either: a 2 is a 2 whether she
    # meant to stop there or was stopped, and once the histogram legitimately
    # spans 1-4 there is no shape to read a truncation out of.
    #
    # `reason` reuses Strands' `limit_*` vocabulary where the loop reported
    # one, so it joins directly against `nora_llm_stop_reason_total`, and adds
    # two of its own for the paths that produce no stop reason at all:
    # `invocation_failed` (timeout or `llm_error` — the invocation never
    # returned, so there is nothing to ask) and `pipeline_error` (the outer
    # `BaseException`). Alert on any of it: unlike a cap on a SILENT turn,
    # every increment here is a message the group was meant to receive.
    _CounterSpec(
        "nora_partial_replies_total",
        "Tags that delivered at least one message and then stopped early, by "
        "why the turn ended (limit_turns / limit_total_tokens / "
        "limit_output_tokens / invocation_failed / pipeline_error).",
        ("reason", "platform"),
    ),
    # DEC-43, and the FIRST of these two is the one that can measure absence.
    # No attachment has ever arrived on this deployment — signal-cli's store
    # has no `attachments/` directory at all — so every claim about media here
    # is a claim about fixtures. `nora_media_attachments_total` is what turns
    # that from a belief into a reading: it increments on every attachment
    # SEEN, before any custody is attempted, so `0` means "none has arrived"
    # and a non-zero `label` cut says exactly what kind did. Without it, a
    # custody counter sitting at zero would be indistinguishable from custody
    # being switched off, uninstalled, or never called — the null-result trap
    # CLAUDE.md's `lookup_person` grep fell into.
    _CounterSpec(
        "nora_media_attachments_total",
        "Attachments seen on ingested messages, by platform and the label they "
        "rendered as (image / voice note / video / audio / document / file).",
        ("platform", "label"),
    ),
    # And the second says what happened to the bytes. `stored` is the only
    # outcome that produces a handle in the transcript; every other value is a
    # message recorded WITHOUT custody, which is honest but lossy, and each
    # names a different thing to fix:
    #   not_configured — the composition root wired no blob store (the state on
    #                    the day this shipped), so nothing was even attempted.
    #   no_id          — signal-cli described an attachment with no id to fetch.
    #   not_found      — the daemon has no such attachment; its own retention,
    #                    or a receive that never downloaded it.
    #   unreachable    — the daemon did not answer. Ops.
    #   bad_payload    — it answered in a shape this adapter does not read; a
    #                    signal-cli upgrade changing `getAttachment`'s result.
    #   too_large      — over the configured ceiling. A policy, not a failure.
    #   store_failed   — the disk refused. Full, read-only, or gone.
    _CounterSpec(
        "nora_media_custody_total",
        "Attachment byte-copies into Nora's own custody, by platform and "
        "outcome (stored / not_configured / no_id / not_found / unreachable / "
        "bad_payload / too_large / store_failed).",
        ("platform", "status"),    ),
    # ── the presence collector (design/19, DEC-54/DEC-55) ────────────────────
    # A separate process (`python -m nora.presence`) with its own metrics
    # endpoint; these names are declared in the shared registry so both the
    # collector and Nora (which increments only the disclosure counter) speak
    # them from one source. Append-only, like every name above.
    #
    # DISTINCT fixes WRITTEN. Because the collector is a change-recorder (writes
    # only when `ts` advances), this counts DISTINCT positions, not polls — a
    # still member polled every 90 s increments this at most when it actually
    # moves. `wire_state` is the state that fix carried (live / stale).
    _CounterSpec(
        "nora_presence_fixes_total",
        "Distinct location fixes written to presence_fixes, by circle and the "
        "wire_state the fix carried (live / stale). A change-recorder count: a "
        "still member does not increment it (design/19 §1).",
        ("circle", "wire_state"),
    ),
    # Every circle READ, by how it resolved. `unavailable` (transport / non-200)
    # must never be summed with `ok`; `banned` (a fresh token refused) and
    # `challenge` (a non-JSON body) are the two the study named as the fragile
    # paths, held apart so a rising `banned` is legible as a ban risk.
    _CounterSpec(
        "nora_presence_reads_total",
        "Life360 circle reads, by circle and outcome "
        "(ok / unavailable / challenge / banned).",
        ("circle", "status"),
    ),
    # The saved-places refresh (design/23 §3): at most hourly per circle, riding
    # the member cycle. Counted separately from `nora_presence_reads_total` so a
    # places-only breakage (endpoint moved, shape changed) is visible on its own
    # rather than diluted into the member-read series.
    _CounterSpec(
        "nora_presence_place_reads_total",
        "Life360 saved-place reads, by circle and outcome "
        "(ok / unavailable / challenge / banned).",
        ("circle", "status"),
    ),
    # Per member per cycle, so «sharing off» is a KNOWN STATE, not a gap
    # (design/19 §5, §7.3). A member who turned sharing off writes no fix row, so
    # WITHOUT this series they would be indistinguishable from a member the
    # daemon failed to read — the exact ambiguity R-5 forbids for a location.
    _CounterSpec(
        "nora_presence_member_states_total",
        "Members classified per collection cycle, by wire_state "
        "(live / stale / sharing_off / disconnected / no_fix / unavailable).",
        ("wire_state",),
    ),
    # The Life360 client's auth alarms (design/19 §4.1). `banned` = a fresh token
    # was refused (ban risk); `challenge` = a non-JSON body; `auth_403` = the
    # token endpoint itself refused the credential. Any sustained increment is an
    # operator alert — the client will not re-auth-loop, so this rising while
    # reads fail is how a ban surfaces instead of a silent 0-fix daemon.
    _CounterSpec(
        "nora_presence_auth_alarms_total",
        "Life360 auth/ban alarms from the client, by reason "
        "(banned / challenge / auth_403).",
        ("reason",),
    ),
    # The collector's own canary (design/19 §4): a read whose body was not JSON,
    # or a circle that came back unexpectedly empty. Both are the app-update
    # breakage the study named — a required UA/host/client-token changing turns
    # 200s into challenge pages or empty envelopes.
    _CounterSpec(
        "nora_presence_canary_total",
        "Presence collector canary trips, by reason (non_json / empty_circle).",
        ("reason",),
    ),
    # R-21 disclosure, incremented by NORA (not the collector): a `whereis` that
    # REFUSED because the conversation held someone outside the circle (design/19
    # §5, the new fail-closed seam). A refusal is fail-closed and loud — this is
    # the day-group-⊄-circle event made observable, never a silent omission.
    _CounterSpec(
        "nora_presence_disclosure_refusals_total",
        "whereis disclosures refused because the conversation was not a subset "
        "of the circle (R-21 fail-closed seam, design/19 §5).",
    ),
    # The READ half's counter (DEC-56). `cached` is the axis that makes "MiMo is
    # never actually called" impossible to read from silence: a cache hit and a
    # miss are the SAME status=ok, and only this label tells them apart. Its own
    # series rather than `nora_tool_calls_total` for the room-tools reason — a
    # second series counting the same calls would double any tool-volume sum.
    #   ok            — described (see `cached` for hit/miss).
    #   nothing_found — no such image in view here.
    #   ambiguous     — two in-scope blobs share the handle prefix.
    #   not_an_image  — the handle resolved to a voice note / video / file.
    #   unavailable   — custody off, the blob was absent/corrupt, or MiMo did
    #                   not answer within the derived vision timeout.
    _CounterSpec(
        "nora_media_fetch_total",
        "media_fetch calls, by outcome (ok / nothing_found / ambiguous / "
        "not_an_image / unavailable) and whether the description was served "
        "from the content-hash cache (cached=true/false).",
        ("status", "cached"),
    ),
    # DEC-57 / R-34. A person's alias appearing in a `search` query — COUNTED,
    # not blocked (design/14 §2.2: unlike a routing origin, a search query has a
    # benign reading, and a guess encoded as a block fails closed on legitimate
    # use). The query still reaches SearXNG; this counter is the only visibility
    # into how often it happens, so the block-vs-count call can be revisited
    # against real data rather than a guess. Identifiers in a query ARE blocked,
    # and are counted as an ordinary `search`/`nothing_found` on
    # `nora_tool_calls_total`, not here.
    _CounterSpec(
        "nora_search_alias_total",
        "Search queries containing a known person's name or alias, counted (not "
        "blocked) so the design/14 §2.2 count-vs-block call can be revisited "
        "against real queries.",
    ),
    # Reminders, the two halves. CREATED is the `remind` tool's own answer —
    # `status` separates an accepted row from each way the caps refuse one
    # (too_soon / too_far / too_many), which is the only visibility into a cap
    # that is set too tight; a refusal is a sentence to a human, and sentences
    # do not aggregate.
    _CounterSpec(
        "nora_reminders_created_total",
        "Reminder creation attempts, by status (created / too_soon / too_far / "
        "too_many).",
        ("status",),
    ),
    # FIRED is the tick's outcome per reminder, and it counts the FAILURES the
    # group never sees: `failed_policy` (trust withdrawn between setting and
    # firing — R-5 keeps that silent in the room, so this is the only trace) and
    # `failed_error` (the fire turn raised, or a crash left the row FIRING and
    # boot recovery resolved it). A reminder that vanishes must never vanish
    # from the metrics too.
    _CounterSpec(
        "nora_reminders_fired_total",
        "Reminders resolved by the heartbeat, by outcome (the fire path's own "
        "status, or failed_policy / failed_error).",
        ("outcome",),
    ),
)

_GAUGES: tuple[_GaugeSpec, ...] = (
    _GaugeSpec("nora_last_event_age_seconds", "Seconds since the last ingest event."),
    # `Gauge`, not `Counter`, on purpose: prometheus-client's exposition format
    # unconditionally appends `_total` to every counter-typed series' rendered
    # name (`exposition.py::generate_latest`, hardcoded, not configurable —
    # verified directly, including via a hand-rolled custom Collector, which
    # hits the same unconditional munging). These two names predate that
    # OpenMetrics convention and don't end in `_total`; using `Counter` would
    # silently rename them to `honcho_feed_success_total` /
    # `honcho_feed_failure_total` — exactly the kind of rename this repo's
    # append-only metric-name contract forbids. A `Gauge` only ever
    # incremented (see `Metrics.inc`) renders under its exact declared name
    # and is otherwise indistinguishable from a counter for these call sites.
    _GaugeSpec("honcho_feed_success", "Successful Honcho feed calls."),
    _GaugeSpec("honcho_feed_failure", "Failed Honcho feed calls."),
    # R-17's other half, and the more important half. A failure COUNTER alone
    # cannot distinguish "0 because enrichment is healthy" from "0 because the
    # enrichment code was deleted, or never runs" — which is the exact trap
    # that let the original degradation hide. This gauge is always published,
    # on every path including total failure, so `nora_identity_aliases == 1`
    # says "R-12 is down to equality against the primary id" out loud and
    # `>= 2` says the derivation worked. Alert on `== 1`, not on the counter.
    _GaugeSpec(
        "nora_identity_aliases",
        "Distinct id forms Nora recognises as her own, by platform. 1 means "
        "enrichment produced nothing beyond the configured primary.",
        ("platform",),
    ),
    # Presence liveness (design/19 §4): the heartbeat. Set after every SUCCESSFUL
    # collection to the age (seconds) of that circle's last success, so a stalled
    # daemon shows a climbing age rather than a frozen fix count. Alert on it
    # crossing a few cadences — that is «the collector is up but not collecting»,
    # which R-5 would otherwise hide (a quiet circle looks identical).
    _GaugeSpec(
        "nora_presence_last_collection_age_seconds",
        "Seconds since this circle's last SUCCESSFUL collection. Climbing means "
        "the daemon is up but not collecting.",
        ("circle",),
    ),
    # The R-21 startup invariant (design/19 §5): 1 when the Signal group is a
    # SUBSET of the Life360 circle (disclosure holds by construction), 0 the day
    # it is not — the alarm that «group ⊄ circle» is now true. Published at
    # startup and whenever it is checked; alert on `== 0`.
    _GaugeSpec(
        "nora_presence_circle_subset_ok",
        "1 when the conversation's people are a subset of the circle (R-21 holds "
        "by construction), 0 the day someone outside the circle is in the group.",
        ("circle",),
    ),
    # The backlog. Set from a count, not derived from the two counters above:
    # created - fired silently drifts the day a row is cancelled, and a backlog
    # that climbs forever is exactly what a stuck tick looks like.
    _GaugeSpec(
        "nora_reminders_pending",
        "Reminders currently PENDING — the backlog waiting on the tick.",
    ),
    # The heartbeat's LIVENESS, and the one gauge in this file that must not be
    # written from inside the loop it measures. It is bound to a function
    # evaluated at SCRAPE time (`Metrics.gauge_function`), so a loop that has
    # STOPPED shows a climbing age rather than freezing at its last reading —
    # the failure this signal exists to make visible. Alert on it crossing a few
    # ticks. Contrast `nora_presence_last_collection_age_seconds` above, which
    # IS published from inside its collector's loop and therefore only detects a
    # collector that is up and failing, not one that is gone.
    _GaugeSpec(
        "nora_heartbeat_last_tick_age_seconds",
        "Seconds since the heartbeat's last completed tick, computed at scrape "
        "time. Climbing past a few ticks means the loop has stopped.",
    ),
)

_HISTOGRAMS: tuple[_HistogramSpec, ...] = (
    _HistogramSpec(
        "nora_tag_latency_seconds", "End-to-end tag latency, including the model call.",
    ),
    # The one LABELLED histogram, and the label is `audience` for the same
    # reason `nora_llm_stop_reason_total` gained it — except that here the two
    # populations are not merely hard to tell apart, they are bounded by
    # DIFFERENT NUMBERS. A tag's invocation is cut at `[llm] timeout_seconds`
    # (60); a fire turn's at `[heartbeat] timeout_seconds` (120), deliberately,
    # because nobody is waiting on it. Mixed into one histogram the tail is a
    # blend of two distributions with two different ceilings, so neither a p95
    # nor a bucket count means anything about either path.
    _HistogramSpec(
        "nora_llm_latency_seconds",
        "Model invocation latency, by which path was speaking "
        "(audience=\"tag\" | \"heartbeat\"). The two carry different timeouts.",
        _DEFAULT_BUCKETS,
        ("audience",),
    ),
    _HistogramSpec("nora_overhead_seconds", "Tag latency excluding the model call."),
    # A COUNT, not a duration, so it carries its own buckets — the default set
    # is calibrated for seconds and would put every possible value in the first
    # bucket.
    #
    # **It is flat at 1 today and that is the point.** R-2 latches after one
    # delivery, so every admitted tag observes 0 or 1 and this histogram says
    # nothing interesting yet. It is a prerequisite for multi-message (step 5
    # of the grounding plan), whose predicted failure is that the cap becomes a
    # quota always spent in full — a shape that is only legible against a
    # BEFORE. Established now, while the answer is known, rather than after the
    # behaviour changes and every reading is confounded.
    #
    # Recorded once per ADMITTED tag on every exit path (`EngagementPipeline.
    # _run_admitted`'s `finally`), so a turn that delivered nothing observes 0
    # rather than being absent: "how often does she say nothing at all" is the
    # same question, and a histogram that only records successes cannot answer
    # it.
    _HistogramSpec(
        "nora_messages_per_tag",
        "Messages actually delivered to the group per admitted tag.",
        (0, 1, 2, 3, 4, 5, 10),
    ),
)

# The full set of declared metric names, independent of kind — what the
# name-preservation test (tests/test_metrics_export.py) enumerates against.
DECLARED_NAMES: frozenset[str] = (
    frozenset(spec.name for spec in _COUNTERS)
    | frozenset(spec.name for spec in _GAUGES)
    | frozenset(spec.name for spec in _HISTOGRAMS)
)
