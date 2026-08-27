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
        "nora_tool_call_log_writes_total",
        "ToolCallLog write attempts (R-48), by tool and outcome (ok / failed).",
        ("tool", "outcome"),
    ),
    _CounterSpec(
        "nora_leak_blocks_total", "Replies blocked by the doctrine/canary leak guard.",
    ),
    _CounterSpec(
        "nora_egress_blocks_total",
        "Replies blocked for carrying somebody's identifiers, by kind.",
        ("kind",),
    ),
    _CounterSpec("nora_doctrine_reload_failures_total", "Doctrine hot-reload failures."),
    # The Dreamer's own instruction corpus (nora/dreamer/), counted separately
    # from the doctrine's on purpose: they are different files with different
    # editors and different blast radii, and one number covering both would make
    # "did my objective edit load" unanswerable from the metric.
    _CounterSpec(
        "nora_dreamer_instruction_reload_failures_total",
        "Dreamer instruction (doctrine + objectives) hot-reload failures.",
    ),
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
        "Room-scoped tool calls, by tool and how the tool answered. FIVE values, "
        "and two pairs of them are easy to conflate: "
        "`ok`; "
        "`not_found` — a NAME resolved to nobody in this room (lookup_person, "
        "where_is, saved_places), and it deliberately cannot be told apart from "
        "«that person was never written down», because a behaviour difference "
        "would disclose membership (design/12 §3); "
        "`nothing_found` — a SEARCH over what was said matched nothing "
        "(search_memory). A different question from `not_found`, hence a "
        "different label; it was recorded as `ok` until 2026-08-11, which made "
        "hit-rate unreadable on the one series that would show her searching "
        "constantly and finding nothing; "
        "`ambiguous`; "
        "`withheld` — it FOUND things the egress guard will not let her repeat, "
        "which is emphatically not the same as finding nothing; "
        "`unavailable` — it could not be checked at all. "
        "NEVER sum `not_found`/`nothing_found` with `unavailable`: the first two "
        "are answers and the third is a failure to answer, and averaging them "
        "hides an outage inside a normal-looking miss rate. Worth alerting on: a "
        "sustained `unavailable` is Honcho or signal-cli rather than the model, "
        "and a climbing `withheld` is the DEC-30 shape — she is being asked "
        "things she can see and cannot say.",
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
        "whereis disclosures refused (R-21 fail-closed seam, design/19 §5). "
        "`reason` names WHICH of the three conditions fired so a refusal is "
        "diagnosable: `degraded` (roster unverifiable — answers `unavailable` "
        "rather than `refused`, R-27, but still counted here), `unnamed` (an "
        "account in the room maps to no person_id), `not_subset` (a mapped member "
        "is outside the circle and the room is not location-trusted).",
        ("reason",),
    ),
    # The WAIVER counter — the other half of the pair above, and the reason DEC-79
    # is auditable rather than merely permissive. When `location = true` overrides
    # a guard, the override is recorded; without this, relaxing `unnamed` would
    # have retired the very instrument that diagnosed DEC-79
    # (`…refusals_total{reason="unnamed"} 12`), leaving a zero refusal counter
    # unable to distinguish "the flag is working" from "the room filled up with
    # strangers". The startup pre-flight warns once at boot and never again, so
    # for an account that joins later this is the ONLY runtime signal.
    # DEC-93. A reply the model WROTE without calling `send_reply`, recovered
    # and delivered rather than discarded. The honest read of a non-zero value
    # is «the LLM route is not honouring `tool_choice=required`» — not «this
    # feature is working». It went from 0 to 2-in-4 the day the deployment moved
    # to a provider that accepts `required` and ignores it, and it is the
    # cheapest signal that would catch the same substitution again.
    _CounterSpec(
        "nora_recovered_replies_total",
        "Replies the model wrote as prose instead of calling send_reply, "
        "recovered and delivered (DEC-93). Non-zero means the route is not "
        "honouring tool_choice=required.",
        ("platform",),
    ),
    _CounterSpec(
        "nora_presence_disclosure_waived_total",
        "whereis disclosures that PASSED only because the conversation is "
        "location-trusted (DEC-79). `reason` names which guard was overridden: "
        "`unnamed` (the room holds account(s) resolving to no person_id). A "
        "non-zero value is not an error — it is the operator's standing decision "
        "being exercised — but a RISING one means the room gained people Nora "
        "cannot name. The count of unmapped accounts is on the log line, not a "
        "label, so cardinality stays fixed.",
        ("reason",),
    ),
    # ── the analytics rollup (DEC-115) ────────────────────────────────────
    # A separate process (`python -m nora.analytics`); spec §5.
    _CounterSpec(
        "nora_analytics_stays_total",
        "Stays written to presence_stays, by subject.",
        ("subject",),
    ),
    _CounterSpec(
        "nora_analytics_places_total",
        "Derived places upserted, by subject and kind (home/work).",
        ("subject", "kind"),
    ),
    # A rise is the tell that Life360's behaviour or the cadence changed
    # (spec §5) — nothing here should trend upward under a stable population.
    _CounterSpec(
        "nora_analytics_unlabeled_total",
        "Times a cluster resolved to unlabeled instead of home/work, or an "
        "existing place expired to unlabeled on a freshness recheck.",
        ("subject",),
    ),
    _CounterSpec(
        "nora_analytics_occupancy_bins_total",
        "Hour-of-week occupancy bins updated (Tier 3), by subject.",
        ("subject",),
    ),
    # A rise here is EXPECTED as the corpus grows — unlike
    # nora_analytics_unlabeled_total, this is not a health signal, it is
    # the anomaly detector doing its job (design/27 §4, issue #6).
    _CounterSpec(
        "nora_analytics_anomalies_total",
        "Stay durations scoring |z| above anomaly_z_threshold against the "
        "subject's own history at that place, by subject.",
        ("subject",),
    ),
    _CounterSpec(
        "nora_analytics_trips_total",
        "Trips written to presence_trips (Tier 5), by subject.",
        ("subject",),
    ),
    # `outcome` is the axis that makes "no corridor has EVER been created"
    # readable. Without it this counted attach OPERATIONS, so a flat-but-
    # nonzero line could equally mean "one corridor, reconfirmed daily" or
    # "corridors are being born" — and the Tier 5 defect where none could
    # ever be born (final whole-branch review, Critical #1) was invisible
    # except by graphing this against nora_analytics_trips_total. Counted
    # once per DISTINCT corridor per clustering pass, not once per trip
    # attached to it.
    _CounterSpec(
        "nora_analytics_corridors_total",
        "Corridors touched by clustering (Tier 5), by subject and outcome "
        "(created = a brand-new corridor row; reconfirmed = an existing one "
        "gained samples).",
        ("subject", "outcome"),
    ),
    # The READ half's counter (DEC-56). `cached` is the axis that makes "MiMo is
    # never actually called" impossible to read from silence: a cache hit and a
    # miss are the SAME status=ok, and only this label tells them apart. Its own
    # series rather than `nora_tool_calls_total` for the room-tools reason — a
    # second series counting the same calls would double any tool-volume sum.
    #   ok            — described (see `cached` for hit/miss).
    #   nothing_found — no such image in view here.
    #   ambiguous     — two in-scope blobs share the handle prefix.
    #   not_an_image  — the handle resolved to a blob NO describer reads (a
    #                   file). Since DEC-114 this no longer covers voice notes
    #                   and videos: those are describable modalities, so an
    #                   unconfigured one is `unavailable`, not this.
    #   unavailable   — custody off, the blob was absent/corrupt, the modality
    #                   has no describer configured (`no_<modality>_describer`,
    #                   the ordinary state while audio/video ship inert), the
    #                   payload could not be built for that modality, or the
    #                   model did not answer within the derived vision timeout.
    _CounterSpec(
        "nora_media_fetch_total",
        "media_fetch calls, by outcome (ok / nothing_found / ambiguous / "
        "not_an_image / unavailable) and whether the description was served "
        "from the content-hash cache (cached=true/false).",
        ("status", "cached"),
    ),
    # DEC-73 (was DEC-57/R-34, retired). A person's alias appearing in a
    # `web_search` query — COUNTED, not blocked (design/14 §2.2: unlike a routing
    # origin, a search query has a benign reading, and a guess encoded as a block
    # fails closed on legitimate use). The query still reaches Tavily; this counter
    # is the only visibility into how often it happens, so the block-vs-count call
    # can be revisited against real data rather than a guess. Identifiers in a
    # query ARE blocked, and are counted as an ordinary `web_search`/`nothing_found`
    # on `nora_tool_calls_total`, not here.
    _CounterSpec(
        "nora_search_alias_total",
        "Search queries containing a known person's name or alias, counted (not "
        "blocked) so the design/14 §2.2 count-vs-block call can be revisited "
        "against real queries.",
    ),
    # Reminders, the two halves. CREATED is the `remind` tool's own answer —
    # `status` separates an accepted row from each way the tool refuses one,
    # which is the only visibility into a cap that is set too tight; a refusal
    # is a sentence to a human, and sentences do not aggregate.
    #
    # The help text lists ALL NINE values `nora.agent.tools._record_created` can
    # emit, not just the caps. An earlier version named four, so an operator
    # reading the metric's own documentation could not tell whether a status it
    # omitted (`fire_turn`, `unmapped`, `unparseable`, `past`, `unavailable`)
    # was a value this series can take or a typo somewhere — and `unavailable`
    # in particular means reminders are not wired at all, which is the one
    # status here that is an outage rather than a policy decision. Keep this
    # list in step with `_record_created`'s call sites.
    _CounterSpec(
        "nora_reminders_created_total",
        "Reminder creation attempts, by status: created; the caps' refusals "
        "(too_soon / too_far / too_many); the turn-provenance refusal "
        "(fire_turn); the input refusals (unmapped / unparseable / past); the "
        "recurrence refusals (bad_repeat, a repeat interval that is not one of "
        "the five; bad_bound, a times/until that cannot be honoured or was "
        "given with no repeat at all); and unavailable, which means no "
        "reminders store is wired, the write failed, or [reminders].timezone "
        "does not resolve.",
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
    # DEFERRALS are the third reading, and the one neither of the two above can
    # give. A tick that finds the conversation slot held leaves its rows PENDING
    # and moves on (§5.3) — nothing fires, nothing fails, and the tick itself
    # completes, so `nora_reminders_fired_total` never moves and
    # `nora_heartbeat_last_tick_age_seconds` stays at zero. A busy group can
    # therefore starve a reminder for hours with every other signal reading
    # healthy. Counted ONCE PER DEFERRED TURN, not per reminder and not per
    # distinct row: the same rows are deferred again on the next tick, so this
    # is a measure of CONTENTION over time (alert on a rate), never a count of
    # how many reminders are late — `nora reminders list` answers that, by
    # naming the overdue rows.
    _CounterSpec(
        "nora_heartbeat_deferrals_total",
        "Fire turns the heartbeat deferred to a later tick because the "
        "conversation slot was held by a tag. One per deferred turn per tick, "
        "so a sustained rate means a group is starving its own reminders.",
    ),
    # The version announcement (DEC-97/R-42), one per room per boot ATTEMPT.
    # `already_announced` is the ordinary reading on every boot after the first
    # and is what proves R-42 holding — a deployment restarting all day shows it
    # climbing while `sent` stays where it was. The other statuses are each a
    # reason the group heard nothing: `unavailable` (the build carries no
    # readable stamp — a wiring gap, never "no versions exist"), `no_note` (the
    # version was never annotated, so there is nothing anybody wrote to relay),
    # `deferred` (a tag held the conversation), and the fire path's own
    # failed_policy / failed_timeout / failed_error. R-5 keeps every one of those
    # out of the room, so this counter is their only trace.
    _CounterSpec(
        "nora_version_announcements_total",
        "Version-announcement attempts, by status: sent; already_announced (the "
        "R-42 claim already existed — a restart staying quiet); unavailable (no "
        "readable build stamp); no_note (the version carries no release note); "
        "deferred (the conversation slot was held); and the fire path's "
        "failed_policy / failed_timeout / failed_error.",
        ("status",),
    ),
    # `ComputeJobPoller`'s own delivery outcome (Nora Compute spec §4), the
    # same `sent` / `failed_policy` / `failed_timeout` / `failed_error`
    # vocabulary `speak()` returns (§8.1) that `nora_reminders_fired_total`
    # and `nora_version_announcements_total` already use this label for.
    # `failed_error` is also what `nora.app.compute_poller.recover` reports
    # for a `FIRING` row swept at boot (a crash mid-delivery) — the exact
    # `nora_reminders_fired_total` precedent for a row that "vanished" without
    # this counter moving too.
    _CounterSpec(
        "nora_compute_jobs_delivered_total",
        "Compute jobs (Nora Compute spec §4) resolved by the poller, by "
        "outcome: the fire path's own sent / failed_policy / failed_timeout; "
        "failed_error (the turn raised, or boot recovery resolved a row left "
        "FIRING by a crash or left PENDING by any restart, whose backend "
        "handle is in-memory and did not survive); and deferred (the "
        "conversation slot was held — the ONLY non-terminal value here, the "
        "row stays PENDING and the next tick tries again).",
        ("outcome",),
    ),
    _CounterSpec(
        "nora_loop_exhausted_fallbacks_total",
        "Turns where the agent loop was cut off by an R-8 cap before the model "
        "sent a reply, and Nora delivered the never-silent fallback line instead "
        "of going silent (operator 2026-08-09). `reason` is the stop cap that "
        "fired (limit_total_tokens/limit_turns/limit_output_tokens) — a sustained "
        "rate on one reason is the model flailing into that cap, the tell to "
        "revisit budgets or the query shape. (A timeout/llm_error is NOT covered "
        "here — that stays a silent_failure; see the pipeline's `if not ok`.)",
        ("reason",),
    ),
    # The OUTBOUND half of `nora_unmapped_mentions_total`, and the pair reads
    # as a sentence: that one says "somebody was talked about and she does not
    # know who", this one says "she tried to tag somebody and could not".
    #
    # `outcome` is what makes it worth having, because the four failures need
    # four different fixes and are indistinguishable in the group (every one of
    # them looks like an ordinary untagged message): `unresolved` = the name is
    # not in identity.toml, or its only id form is one signal-cli's RECIPIENT
    # grammar would not take — edit identity.toml AND RESTART (it is read once
    # at startup, the same trap `nora_unmapped_senders_total` has);
    # `not_in_room` = she named somebody who is not a member of this
    # conversation, which is usually correct behaviour and not a fault;
    # `roster_unavailable` = signal-cli could not be asked who is here, so
    # nothing was tagged (R-22: "I could not check" is never "they are here"),
    # and a sustained rate is a wedged or stale daemon, not a naming problem;
    # `ambiguous` = two people in identity.toml answer to the same handle, so
    # neither was tagged. `sent` is the denominator that makes the others
    # readable — without it a flat `unresolved` cannot be told from a Nora who
    # never tries to tag anyone.
    _CounterSpec(
        "nora_outbound_mentions_total",
        "@-mentions Nora wrote in a reply, by outcome: sent; unresolved (no "
        "usable id in identity.toml); not_in_room (not a member of this "
        "conversation); roster_unavailable (the membership list could not be "
        "read, so nothing was tagged); ambiguous (two people answer to the "
        "handle).",
        ("outcome",),
    ),
    # Link previews, and the reason this is not a bare success counter: three
    # of its four values mean something different to an operator. `attached` =
    # a card went with the message; `unavailable` = the fetch produced nothing
    # (dead host, non-HTML, no og:title, a refused SSRF check) and the message
    # went out plain, which is the DESIGNED degradation and only interesting as
    # a rate; `blocked` = the fetched card carried an identifier and was
    # dropped at the egress screen, which is a real R-18 event and should be
    # ~0. A message with no link in it increments nothing at all — the series
    # counts decisions, not messages.
    _CounterSpec(
        "nora_link_previews_total",
        "Outbound link-preview cards, by outcome: attached; unavailable (the "
        "fetch yielded no usable card, so the message went out plain); blocked "
        "(the fetched title/description carried an identifier, R-18).",
        ("outcome",),
    ),
    # DEC-89. The series exists to answer one question an operator cannot ask
    # any other way: is the model quoting the links `place_info` mints, or is
    # the egress carry the only reason the group ever sees one? A reply that
    # already carried its link increments NOTHING — like `nora_link_previews_
    # total`, this counts DECISIONS, not messages — so a rising rate is the
    # model dropping links and a flat zero (with `place_info` calls happening)
    # is the model behaving, which is the direction the doctrine is aimed at.
    # `outcome` is a label rather than a bare counter because the one-valued
    # version cannot grow the value that will matter next (a link EARNED but
    # suppressed) without a rename, and metric names here are append-only.
    _CounterSpec(
        "nora_reply_links_carried_total",
        "Replies that left with a link the model had dropped, by outcome: "
        "appended (a place the reply named had a minted maps link the text did "
        "not carry).",
        ("outcome",),
    ),
    # DEC-105. `ok` is a message that reached another room; every other value is
    # a refusal, and WHICH one it is matters — `other_workspace` and
    # `not_allowed_there` are the two that mean somebody tried to speak past a
    # trust boundary, and they are worth watching in a way that `unknown_room`
    # (a model guessing at a name) is not.
    # Somebody was told «I'm working on it» and the job was then lost across a
    # restart. This counts the CLOSING of that promise, by outcome — `sent` is
    # the only value that means the person waiting actually heard back, and a
    # rising `busy`/`failed_error` means they did not.
    _CounterSpec(
        "nora_compute_lost_told_total",
        "Lost-compute-job notices by outcome: sent / busy / failed_* .",
        ("outcome",),
    ),
    _CounterSpec(
        "nora_relay_total",
        "Cross-room relays by outcome: ok / empty / unmapped / unknown_room / "
        "ambiguous_room / unknown_source / same_room / other_workspace / "
        "not_allowed_there / egress_blocked / no_adapter / failed.",
        ("outcome",),
    ),
    # Task 14 fix round 1: what makes the dedup ladder's 41%-and-rising hit
    # rate an OBSERVATION rather than a belief. `status` is `ok` when the
    # sticker branch produced a description, else the `StickerOutcome.reason`
    # that stopped it (unavailable / store_failed / blob_missing / vision /
    # error / not_found / no_id / too_large / unreachable). `level` is
    # `StickerReader`'s own ladder-level label — identity / content / none.
    _CounterSpec(
        "nora_sticker_descriptions_total",
        "Sticker-branch outcomes in the media custody stage, by platform, "
        "status (ok / the StickerOutcome reason), and which ladder level "
        "answered — level=identity/content/none.",
        ("platform", "status", "level"),
    ),
    # ── the speech (STT) lane (spec 2026-08-21) ─────────────────────────────
    # Voice-note transcription outcomes in the media custody stage, by
    # platform and status (ok / error / timeout / unsupported / empty).
    # `empty` is a legitimate 200 with no words (a silent/wordless voice
    # note) — kept distinct from `error` (a transport/server failure) so an
    # operator can tell an outage from an ordinary silent note. An ERROR is
    # never a PASS: a failed transcription is counted here with its reason
    # and the voice note is still recorded with a bare handle.
    _CounterSpec(
        "nora_speech_transcriptions_total",
        "Voice-note transcription outcomes in the media custody stage, by "
        "platform and status (ok / error / timeout / unsupported / empty).",
        ("platform", "status"),
    ),
    # The Dreamer's one LLM call (nora.language.observer.LexemeObserver,
    # design/2026-08-15-room-language-dreamer §Task 6). A model can name a
    # `message_id` that is not in the batch it was given — the model's own
    # words are the only thing that makes a claim like that possible to
    # check, since nothing else names a row that was never asked about. Such
    # a candidate is dropped rather than kept, because a quote rendered under
    # a gloss is what makes a wrong gloss visible, and fabricated provenance
    # is worse than a missing entry. `reason="no_evidence"` was the only
    # value at Task 6; Task 11 added a second, `reason="not_found"`, at the
    # OTHER hop the same evidence-grounding rule applies to —
    # `SqlLanguageStore.append_observations`'s own join, when a candidate's
    # `message_id` WAS in the batch (so it passed the observer's check) but
    # no longer resolves against a real `messages` row by the time the
    # INSERT runs (pruned in the window between `RecordStore.since` reading
    # it and the append landing). Without this second value the two states
    # were indistinguishable from a bare `appended` count: "the batch had
    # nothing new" and "the evidence vanished between read and append" both
    # read as zero.
    _CounterSpec(
        "nora_language_candidates_dropped_total",
        "Lexeme candidates the extractor dropped before they reached the "
        "store, by reason. reason=\"no_evidence\": the model named a "
        "message_id that is not in the batch it was given. "
        "reason=\"not_found\": the message_id WAS in the batch but no "
        "longer resolved against a real messages row by append time. "
        "reason=\"known_entity\": [language].verify_known_entities found the "
        "candidate already has an established public meaning (e.g. a real "
        "game) via web_search, rather than being coined by this room.",
        ("reason",),
    ),
    # The known-entity verification step's OWN outcome (2026-08-20 design
    # doc), independent of whether it led to a drop — without this an
    # operator watching only the drop counter above cannot tell "verification
    # is running and finding nothing to drop" from "verification is silently
    # never running at all," the same liveness gap `416d68a` fixed for
    # `dream_loop` itself, one level down at this sub-step. Only emitted when
    # `[language].verify_known_entities` is on; structurally zero otherwise.
    _CounterSpec(
        "nora_language_verification_total",
        "The Dreamer's known-entity check (web_search over a candidate's "
        "canonical_form), by outcome. status=\"ok\": Tavily returned "
        "results and the classification call ran. status=\"nothing_found\": "
        "no results, so the candidate was kept unverified. "
        "status=\"unavailable\": the search call itself failed, kept "
        "unverified (fail-open).",
        ("status",),
    ),
    # `dream_loop`'s own instrument for the OTHER Task-6-carry-forward
    # defect (Task 11): a model that degrades into returning prose forever
    # is INDISTINGUISHABLE from one correctly finding nothing — neither a
    # counter nor a log line separated them, and the visible symptom of
    # both is a lexicon that stops growing. This counts ONLY the first
    # case (the raw response could not be parsed as the expected
    # `{"observations": [...]}` shape at all); a well-formed, genuinely
    # empty response increments nothing here, which is what makes the two
    # states distinguishable at last.
    _CounterSpec(
        "nora_language_parse_failures_total",
        "Lexeme-extraction model responses that could not be parsed as "
        "the expected JSON shape at all, distinct from a well-formed "
        "response that correctly reported no candidates.",
    ),
    # Dreamer Plan 2's own pair, mirroring the two above exactly —
    # `nora.language.exchange_observer`'s "no_evidence"/"single_participant"
    # drop reasons and its own parse-failure counter. `reason=
    # "single_participant"` is the second structural withhold-rule guarantee
    # made observable: a candidate whose evidence resolves to only ONE
    # distinct person (two message ids from the same speaker) is dropped,
    # not merely disallowed by policy.
    _CounterSpec(
        "nora_exchange_candidates_dropped_total",
        "Exchange-pattern candidates the extractor dropped before they "
        "reached the store, by reason. reason=\"no_evidence\": the model "
        "named message_ids not (all) in the batch it was given. "
        "reason=\"single_participant\": the resolved evidence names only "
        "one distinct person — no exchange, and the withhold rule forbids "
        "a one-person row. reason=\"not_found\": the message_ids WERE in "
        "the batch but no longer resolved against real messages rows by "
        "append time.",
        ("reason",),
    ),
    _CounterSpec(
        "nora_exchange_parse_failures_total",
        "Exchange-pattern-extraction model responses that could not be "
        "parsed as the expected JSON shape at all, distinct from a "
        "well-formed response that correctly reported no patterns.",
    ),
    # dream_loop's per-scope lock (Task 11's first non-negotiable): a slow
    # extraction still running when the next tick comes round is SKIPPED,
    # never re-entered — re-entering would read the same un-advanced
    # watermark and append the same evidence twice, and the rendered
    # "12x, 3 people" would be a fabricated number. reason="in_flight" is
    # the only value today.
    _CounterSpec(
        "nora_dream_skipped_total",
        "Dream-loop ticks skipped, by reason. reason=\"in_flight\": the "
        "previous cycle for this SCOPE had not finished yet. "
        "reason=\"in_flight_adaptor\": the previous cycle for this "
        "(adaptor, key) had not — kept apart because one adaptor wedged and "
        "one scope wedged are different diagnoses on the one series that "
        "answers «is the Dreamer stuck?».",
        ("reason",),
    ),
    # `SqlLanguageStore.watermark_is_behind_the_record`'s tell, read by
    # `dream_loop`: a watermark sitting below the oldest SURVIVING message
    # means rows were pruned before this consolidator ever read them —
    # R-27's `unavailable` ("I could not see those"), never `nothing_found`
    # ("nothing happened"). Labelled by scope so a specific room's loss is
    # findable rather than merged into one process-wide number; structurally
    # zero while `store.prune_enabled` is false.
    _CounterSpec(
        "nora_dream_evidence_lost_total",
        "Dream-loop scopes where messages were pruned before this "
        "consolidator ever read them, by scope.",
        ("scope",),
    ),
    # The three-state verdict, visible. A high `not_covered` rate is the
    # HONEST read — most sources do not address most claims — and reading it
    # as a failure is the same mistake R-27 exists to prevent one layer down.
    _CounterSpec(
        "nora_fact_verdicts_total",
        "Fact adjudications by verdict: supported, contradicted, not_covered.",
        ("status",),
    ),
    _CounterSpec(
        "nora_facts_proposed_total",
        "Facts written from a supported or contradicted verdict.",
    ),
    _CounterSpec(
        "nora_facts_superseded_total",
        "Facts stamped superseded_at by the one verb that may do so.",
    ),
    # Candidate selection is a COST decision, so what it dropped is the thing
    # an operator needs to see; a silent drop reads as "nothing was there".
    _CounterSpec(
        "nora_fact_candidates_total",
        "Fact candidates by what selection did with them: adjudicated, "
        "already_known, noise, deferred.",
        ("outcome",),
    ),
    # HELP TEXT widened 2026-08-21, name unchanged (names are append-only;
    # help text is not). It was written for one `reason` — the per-tick cap —
    # and the adjudicator legitimately counts seven more: a verdict dropped as
    # malformed, one that broke `FactVerdict`'s own invariants, one naming a
    # fact the model was never shown, an id correspondence that did not line
    # up, a supersession that failed or matched no row, a verdict matching no
    # candidate, and a retirement that failed. All are "a candidate this tick
    # did not settle", which is what the series measures; a help text naming
    # only the cap would make the other seven read as a mislabelled cap.
    _CounterSpec(
        "nora_fact_adjudications_skipped_total",
        "Candidates a tick did not settle, by reason: cap (the per-tick "
        "ceiling), or a verdict dropped before it could be written "
        "(malformed, invariant, unknown_supersedes, id_mismatch, "
        "supersede_failed, supersede_missed, unmatched_candidate, "
        "retire_failed). A silent drop reads as 'we checked everything'.",
        ("reason",),
    ),
    _CounterSpec(
        "nora_fact_entity_unresolved_total",
        "Candidate subjects that resolved to no fact_entity — the alias "
        "problem, observed rather than assumed.",
    ),
    # ---- the Dreamer runtime (substrate plan Task 2.7) --------------------
    #
    # R-27's three states, at the run. A run that FAILED is `unavailable` and
    # is counted — never silently absent — because the Dreamer has nobody
    # waiting on it: an absent metric and a healthy-but-idle loop look
    # identical from outside, and that is precisely how a hung provider went
    # unnoticed long enough to need `composition.py:145-164` written about it.
    _CounterSpec(
        "nora_dream_run_total",
        "Dreamer adaptor runs by outcome: ok (wrote something), "
        "nothing_found (looked, found nothing), unavailable (could not "
        "check — an ERROR is never a PASS).",
        ("adaptor", "status"),
    ),
    _CounterSpec(
        "nora_dream_written_total",
        "Rows written through an adaptor's sink, by adaptor.",
        ("adaptor",),
    ),
    # Screening is the second path into the prompt, and it must never be
    # silent: a proposal dropped for carrying a phone number or a coordinate
    # is a real event about a model reading attacker-influenceable pages, and
    # a drop nobody counts is indistinguishable from a model that never
    # produced one.
    _CounterSpec(
        "nora_dream_screened_total",
        "Envelope items dropped before reaching a sink, by adaptor and "
        "reason: identifier or coordinate (the R-18/R-28 screen), or cap "
        "(the per-run item ceiling — a silent cap reads as 'we wrote "
        "everything it found').",
        ("adaptor", "reason"),
    ),
    # Strands raises nothing and logs nothing when a budget cap binds — the
    # `AgentResult.stop_reason` string is the only channel there is — so a
    # runaway loop spending its full ceiling every tick is invisible without
    # this. Four scopes every 300s with nobody watching is exactly the shape
    # that stays invisible longest.
    _CounterSpec(
        "nora_dream_stop_reason_total",
        "How a Dreamer loop ended, by adaptor: end_turn, limit_turns, "
        "limit_total_tokens, limit_output_tokens, timeout.",
        ("adaptor", "reason"),
    ),
    # R-7 default-deny in the background loop. A veto here means the model
    # asked for a tool its adaptor never granted — which under a read-only
    # ceiling is not a leak, but IS the tell that an objective and its tool
    # subset disagree about what the job needs.
    _CounterSpec(
        "nora_dream_guard_vetoes_total",
        "Tool calls refused by DreamGuard, by adaptor and rule.",
        ("adaptor", "rule"),
    ),
    _CounterSpec(
        "nora_tagtrace_truncations_total",
        "TagTrace content dropped or cut before retention, by reason.",
        ("what",),
    ),
    # 15-roadmap §6.2b, and it was named there as a PRECONDITION of Phase 2
    # rather than a nice-to-have. Phase 1's whole justification is «record
    # before you fold»: retention exists so that a lossy fold is recoverable,
    # which means a recording layer that silently stops writing fails in the
    # one way nobody notices — the discovery point is the first fold, and by
    # then the missing traces cannot be reconstructed.
    #
    # The denominator is `nora_tag_latency_seconds`' own COUNT: that histogram
    # observes exactly once per admitted tag, on every exit path, so
    # `nora_tagtrace_writes_total / nora_tag_latency_seconds_count` is the
    # written-versus-turns-taken ratio. `tag_trace` is written in a `finally`
    # on every exit path too, so the expected value is 1.0 and any sustained
    # departure is a defect rather than a judgement call.
    #
    # The `outcome` label is the cheap second reading §6.2b asks for: the
    # column is already written, so counting it is a label on a counter that
    # had to exist anyway — and it is the only way to see whether R-5 silences
    # are being retained (and therefore reviewed) at all.
    _CounterSpec(
        "nora_tagtrace_writes_total",
        "TagTrace rows handed to the retention store, by turn outcome. The "
        "denominator is nora_tag_latency_seconds_count (one per admitted "
        "tag); the expected ratio is 1.0.",
        ("outcome",),
    ),
    # 26-standing §6. Every debrief lands on exactly one label, including the
    # ones that wrote nothing:
    #   written           — the position was revised
    #   refused_overflow  — it would not fit even after consolidating; the
    #                       prior stands (§4.0's refuse-never-truncate)
    #   screened          — an identifier or coordinate reached the fold (§4.3)
    #   malformed         — the model answered, in the wrong shape
    #   unavailable       — the prior could not be read, or the call was cut
    #                       off at the output ceiling. «I could not check» —
    #                       never folded in with `malformed` (R-27)
    #   timeout           — the fold did not finish inside its own budget
    #   cancelled         — the process is going away mid-fold. NOT a fault,
    #                       and deliberately not `error`: shutdown cancels an
    #                       in-flight tag routinely, and a counter that fires
    #                       on ordinary shutdown is one nobody reads
    #   error             — anything else, genuinely unexpected
    _CounterSpec(
        "nora_debrief_total",
        "Debrief folds by disposition: written, refused_overflow, screened, "
        "malformed, unavailable, timeout, cancelled, error.",
        ("outcome",),
    ),
    # §4's non-negotiable, made OBSERVABLE rather than assumed. A fold that
    # read nothing is the failure D19 exists to prevent — two contradictory
    # positions coexisting, neither able to correct the other — and the live
    # evidence is that it happened one tier over and nobody could see it.
    # `prior="absent"` is legitimate exactly once per conversation (the first
    # fold); a sustained absent rate is the tell that the read is broken.
    _CounterSpec(
        "nora_debrief_prior_total",
        "Debrief folds by whether the current Standing was read first "
        "(prior=read|absent). A sustained absent rate is a defect.",
        ("prior",),
    ),
    # §4.0/§6: the refuse-and-consolidate path firing is HEALTHY. Never firing
    # means the cap is not binding — the tier is meant to be lossy, so a cap
    # that never binds means it stopped being lossy. Firing constantly means it
    # is too small. Both are readings, which is why this is a counter and not a
    # log line.
    #
    # `stage="first"` is the refusal the model then FIXED; `stage="final"` is
    # the one that wrote nothing. Two very different healths wearing one name
    # would make the series unreadable: a high `first` with a low `final` is
    # the mechanism working.
    # 26 §3's second output. The label is the VERDICT, not the write's success,
    # because the reading that matters is the grounding mix — a room where
    # every turn comes back `false` is telling you something about the doctrine
    # or the tools, not about the store.
    #
    #   true      — the fold judged the turn grounded
    #   false     — ungrounded; `unsupported` names which claims
    #   absent    — the fold revised the position but produced no usable
    #               verdict. A sustained rate means the objective is not
    #               asking for one clearly enough — a reading about the PROMPT
    #   unwritten — the verdict parsed but the save failed, including the
    #               duplicate-key error that IS the never-rewritten property
    #               (a turn debriefed twice). Never folded in with `absent`:
    #               "the model gave nothing" and "the database refused" send
    #               the next investigation to different places (R-27)
    # 26 §6's named reading, and the LABEL is the whole point: "forced-on-
    # ungrounded versus ceiling. If the ceiling dominates, the trigger is a
    # clock wearing a condition." So `ungrounded` wins whenever both conditions
    # hold — labelling a turn that actually went wrong as `ceiling` manufactures
    # the very degradation this series exists to detect.
    #
    # Never firing at all is also a reading: it means positions are never being
    # rebuilt, so a bad fold carries itself forward indefinitely.
    # 26 §7's read-set, reaching the debrief. Counted before anything consumes
    # it ON PURPOSE: the note-proposal output is still blocked on the store's
    # create-shaped payload, and a wiring that is built but unread is exactly
    # the kind that rots unnoticed until the feature it serves arrives and
    # quietly does nothing. A moving counter proves the set is threaded; a flat
    # one at zero while notes are being read means it is not.
    # 26 §3's fourth output. Every proposal lands on exactly one label, and
    # the refusals are the interesting half:
    #
    #   proposed     — reached the sink
    #   already_read — §7: the turn READ that note and chose not to touch it.
    #                  A sustained rate means the two paths are arguing, which
    #                  is the failure §7 exists to prevent, caught rather than
    #                  suffered
    #   refused      — the domain type refused it (over-long title, blank
    #                  closing condition, no anchor). The boundary WORKING, not
    #                  a fault — but a high rate is a reading about the
    #                  objective text, not about the store
    #   over_cap     — notes discarded past MAX_NOTES_PER_FOLD. A fold that
    #                  wants to raise six has started narrating; the note index
    #                  is uncapped, so those would cost every later prompt
    #   malformed    — an entry that was not even a mapping
    #   unwritten    — the sink refused the whole batch (TypeError,
    #                  StaleNoteHeadError, ValueError). Never folded in with
    #                  `refused`: one is our boundary, the other is the store's
    _CounterSpec(
        "nora_debrief_note_proposals_total",
        "Notes the debrief raised, by disposition: proposed, already_read, "
        "refused, over_cap, malformed, unwritten (the propose did not "
        "complete; whether it committed is unknown), no_workspace (policy "
        "names no notebook for the room — a refusal, not a fault) and "
        "no_workspace_unavailable (the resolver itself failed). The last two "
        "are separate because an unmapped room and a broken resolver are "
        "different readings, and one bucket for both is a fault reported as a "
        "policy decision.",
        ("outcome",),
    ),
    _CounterSpec(
        "nora_debrief_notes_read_total",
        "Notes the tag path READ this turn, as seen by the debrief (R-55's "
        "latch). A proposal naming one of these is dropped by the debrief "
        "before it reaches the sink, so "
        "the two paths cannot argue over the same note every turn.",
    ),
    _CounterSpec(
        "nora_standing_rederivations_total",
        "Re-derivations TRIGGERED, by trigger: ungrounded (an unconsumed "
        "ungrounded verdict) or ceiling (fold_count reached [standing] "
        "rebuild_after_folds). Ungrounded wins when both hold. Read against "
        "nora_standing_rederivations_written_total: a fold that refuses "
        "writes nothing and advances no watermark, so the trigger fires again "
        "next turn, and on this series alone that is indistinguishable from a "
        "trigger latched on.",
        ("trigger",),
    ),
    _CounterSpec(
        "nora_standing_rederivations_written_total",
        "Re-derivations that LANDED — the rebuilt position was written and, "
        "for an ungrounded trigger, the watermark advanced with it. Same "
        "labels as nora_standing_rederivations_total; a persistent gap "
        "between the two is a fold that keeps failing, not a latch.",
        ("trigger",),
    ),
    _CounterSpec(
        "nora_turn_verdict_total",
        "Per-turn debrief verdicts by grounding: true, false, absent (no "
        "usable verdict from the fold), unwritten (parsed but the save "
        "failed).",
        ("grounded",),
    ),
    _CounterSpec(
        "nora_standing_overflow_refusals_total",
        "Standing writes refused for exceeding [standing] max_chars, by "
        "stage: first (the model was asked to consolidate) or final "
        "(nothing was written and the prior position stands).",
        ("stage",),
    ),
    # ---- the note tier (2026-08-21 note-tier design §11) -------------------
    #
    # THREE SERIES, NOT THE TEN THE SPEC NAMES, and the count is the decision.
    # Four of the ten already have a home: `note_read`/`note_search`/
    # `note_create`/`note_update` all reach `_record` in `nora.agent.tools`, so
    # `nora_tool_calls_total{tool,status}` already carries reads-by-result,
    # searches-by-result and writes-by-kind (the tool name IS the kind). A
    # second series over those same calls is the double-count `_record_room`
    # and `_record_media` both refuse in their own docstrings. The tier's named
    # diagnostic survives the omission: a climbing `nora_note_candidates_total`
    # against a flat `nora_tool_calls_total{tool=~"note_create|note_update",
    # status="ok"}` is «she recognises and does not act», in two series that
    # both exist.
    #
    # The other three — `curator_total`, `curator_bodies_read`,
    # `revisit_total` — plus `strikes_total` measure adaptors that are UNBUILT.
    # `note.strikes` is a column with a CHECK constraint and no writer; the
    # strike is the revisitor's wake pass (spec §9.6). Declaring them here
    # would ship a name whose call site does not exist, which is
    # `nora_fact_entity_unresolved_total` again — instance 7 in
    # `design/dreamer-agent-loop/02-instrument-blindness.md`'s own tally, and
    # the exact defect this block was written to close. They ship with their
    # adaptor.
    #
    # `trigger` carries one value today (`miss`, the negative cache at
    # `tools.py::note_search`). The label is here anyway: a second trigger is
    # what §4.5 anticipates, and names are append-only while labels are not,
    # so the axis has to be right the first time.
    _CounterSpec(
        "nora_note_candidates_total",
        "Note candidates recorded by what raised them. trigger=\"miss\": a "
        "note_search that matched nothing, written to the negative cache so "
        "the SECOND ask on the same ground is evidence rather than a guess.",
        ("trigger",),
    ),
    # A DELIBERATE second series over calls already counted, on
    # `_record_created`'s precedent (`nora.agent.tools`) rather than against
    # `_record_room`'s rule — and the difference is that the REASON is the
    # whole reading, and today it survives only in a log line.
    #
    # THREE reasons, and they are two different diagnoses wearing one verb.
    # `not_read` is a MODEL-DISCIPLINE reading — it tried to rewrite a body it
    # had not seen, and a healthy rate means the latch is doing its job.
    # `title_moved` and `stale_head` are CONTENTION readings — the note it
    # reached was not the note it read, or the head moved under the call. A
    # rising contention rate on a tier with no curator running is a defect
    # report; the same rate once the curator ships is the cost of having one.
    #
    # No existing series can separate them, and today they are not even in the
    # same one: the guard's `not_read` is `nora_guard_vetoes_total{rule=
    # "R-55"}`, while `title_moved` and `stale_head` land in
    # `nora_tool_calls_total{tool="note_update"}` under `refused` and
    # `unavailable`, with the reason surviving only in a log line.
    #
    # `not_read` and `title_moved` are kept apart rather than folded, though
    # both mean «no head recorded», because the tool-layer backstop is keyed on
    # the resolved note ID and the guard on the title (see `note_update`'s own
    # comment). The one case they differ in is the one worth counting: a title
    # that resolved to a DIFFERENT note between the read and the write.
    # ---------------------------------------------------------------------
    # The REVISITOR (`nora.language.note_revisitor`) — the note tier's negative
    # measurement, and the tier's only retiring writer. Everything here exists
    # to keep one collapse visible: a note that was left standing because the
    # pass COULD NOT TELL must never read the same as a notebook with nothing
    # due in it.
    # ---------------------------------------------------------------------
    _CounterSpec(
        "nora_note_revisit_candidates_total",
        "Notes the revisitor selected as due, by whether it had anything to "
        "judge them against. FOUR values, and a reader who sees only three has "
        "an out-of-date copy of this text. evidence=\"present\": the "
        "conversation has "
        "spoken since the note was written. evidence=\"absent\": it has not, "
        "so NEITHER retirement is reachable and the note stays — silence is "
        "not an answer. evidence=\"self_only\": the conversation HAS spoken "
        "since the note and every row in the window was Nora's own — the cap "
        "is applied before the is_nora filter, so her own traffic can exhaust "
        "the window by itself. Counted apart from \"absent\" because calling "
        "that a quiet room is false, and the prompt says so in different words "
        "too. evidence=\"unavailable\": the record read itself "
        "failed, which lands on the same safe path but for a different reason "
        "and must not be counted as an honestly quiet room.",
        ("evidence",),
    ),
    _CounterSpec(
        "nora_note_revisit_verdicts_total",
        "Verdicts the revisitor ACCEPTED, by kind. verdict=\"closed\": the "
        "closing condition was met and the citation was grounded in the "
        "evidence shown. verdict=\"expired\": the anchor date is behind the "
        "clock and real evidence showed nothing answered it. "
        "verdict=\"unresolved\": the model was asked and said it could not "
        "tell — a REAL answer and often the right one, counted apart from the "
        "refusals so a model that has stopped engaging cannot hide among them.",
        ("verdict",),
    ),
    _CounterSpec(
        "nora_note_revisit_refused_total",
        "Verdicts the revisitor REFUSED, by reason — every one of these leaves "
        "the note standing. reason=\"unknown_note\": the item named a handle "
        "outside the set this tick asked about. The handle is a per-tick "
        "ORDINAL, never the note id — a note id in an envelope item is "
        "dropped wholesale by dream_screen before accept ever runs. "
        "Retiring an unrelated note is the worst thing this pass can do. "
        "reason=\"unmeasured\": the verdict was absent or outside the three "
        "states, i.e. the question was not answered. reason=\"no_evidence\": "
        "a retirement was proposed for a note with nothing to judge it "
        "against. reason=\"citation_missing\" / \"citation_ungrounded\": a "
        "closed verdict with no cited message, or one citing a message it was "
        "never shown. reason=\"anchor_not_passed\": an expired verdict on a "
        "note whose moment has not gone by — the clock decides that, not the "
        "model. reason=\"anchor_not_covered\": the moment HAS gone by but the "
        "evidence window does not span it — the room fell silent before the "
        "anchor, or is busier than the window — so \u00abnothing answered "
        "it\u00bb would be a claim about conversation this pass never read. "
        "reason=\"contradictory\": the model gave one note both a closed and "
        "an expired verdict, which is the definition of \u00abcannot tell "
        "which\u00bb, and both answers throw the note away.",
        ("reason",),
    ),
    _CounterSpec(
        "nora_note_revisit_deferred_total",
        "Due notes the revisitor did NOT judge this tick because "
        "[notes].max_revisits_per_tick capped the batch. A backlog stated "
        "rather than silently truncated: without this, a tick that judged ten "
        "of forty due notes reads identically to a notebook with ten due.",
    ),
    _CounterSpec(
        "nora_note_retirements_total",
        "Retirements the revisitor attempted, by reason and by whether they "
        "MOVED anything. result=\"applied\": supersede affected a row. "
        "result=\"no_rows\": it affected none — the note was already retired "
        "or the id no longer resolves. That split is the point: supersede "
        "returns 0 for an unknown target and never raises, so without it a "
        "retirement that silently did nothing is indistinguishable from one "
        "that was never attempted. result=\"refused\" with because="
        "\"unknown\" is a foreign payload reaching commit, which is a wiring "
        "fault rather than a note.",
        ("because", "result"),
    ),
    _CounterSpec(
        "nora_note_update_refused_total",
        "note_update calls refused, by reason: not_read (the R-55 latch — the "
        "model had not read a note by that title this turn), title_moved (the "
        "title resolved to a different note than the one read), or stale_head "
        "(the compare-and-set lost; the note moved under the call).",
        ("reason",),
    ),
)

_GAUGES: tuple[_GaugeSpec, ...] = (
    _GaugeSpec("nora_last_event_age_seconds", "Seconds since the last ingest event."),
    # The per-platform answer to the same question, added rather than folded
    # into the line above as a label: that series is UNLABELLED on purpose —
    # it is the Signal-era liveness series, and adding a label changes a
    # series' IDENTITY, so anything selecting it by bare name stops matching
    # with no error anywhere. This one covers every enabled platform, so a
    # wedged Discord Gateway is alertable instead of invisible.
    _GaugeSpec(
        "nora_adapter_last_event_age_seconds",
        "Seconds since the last ingest event, per platform.",
        ("platform",),
    ),
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
    # The climbing-age liveness shape design/19 §4 uses, applied to the
    # rollup: set after every successful run_once, so a stalled daemon
    # shows a climbing age rather than a frozen derivation count.
    _GaugeSpec(
        "nora_analytics_rollup_age_seconds",
        "Seconds since this subject's last successful analytics rollup run. "
        "Climbing means the daemon is up but not deriving.",
        ("subject",),
    ),
    # The R-21 startup invariant (design/19 §5): 1 when the Signal group is a
    # SUBSET of the Life360 circle (disclosure holds by construction), 0 the day
    # it is not — the alarm that «group ⊄ circle» is now true. Published at
    # startup and whenever it is checked; alert on `== 0`.
    _GaugeSpec(
        "nora_presence_circle_subset_ok",
        "1 when the conversation's people are a subset of the circle (R-21 holds "
        "by construction), 0 the day someone outside the circle is in the group. "
        "Labelled by CONVERSATION as well as circle: the pre-flight checks every "
        "allowlisted room, so a circle-only series was last-writer-wins across "
        "them — a failing room could be overwritten by a passing one and the "
        "alarm silently lost.",
        ("circle", "platform", "conversation"),
    ),
    # THERE IS DELIBERATELY NO `nora_reminders_pending` HERE, and the name is
    # left free rather than reserved. Design §13 asked for one ("runaway visible
    # without a query"); it was declared, never published, and therefore rendered
    # `0.0` — which reads as "no backlog" and is indistinguishable from "nobody is
    # measuring". Declared-and-unpublished is the worse of the two states, so the
    # declaration went rather than the honesty.
    #
    # **Why it cannot be published, verified rather than assumed.** The backlog is
    # a row count, and every `ReminderStore` read is a coroutine
    # (`all_pending`, `count_pending`). `Metrics.gauge_function` binds a
    # SYNCHRONOUS callable that prometheus-client invokes inside
    # `CollectorRegistry.collect()` — and `/metrics` is an aiohttp handler
    # (`nora.obs.health`), so that callback runs ON the event loop that would have
    # to run the query. There is no await to be had there and no second loop to
    # hand it to.
    #
    # **And publishing it from the tick is worse than absent.** The shipped
    # default is `[heartbeat] enabled = false` while `remind` still writes rows
    # unconditionally (the store is wired outside the flag) — so a tick-published
    # gauge exports `0.0` forever on precisely the deployment where the backlog
    # only grows. Deriving it from `created_total - fired_total` drifts on the
    # first cancel and resets on every restart while the rows survive.
    #
    # The backlog is a DATABASE question and `nora reminders list` (read-only,
    # `nora.app.cli`) is what answers it, flagging overdue rows — the query §13
    # hoped to avoid, kept because a gauge that means "unknown" while printing
    # "fine" is not a substitute for one. The liveness signal below is the metric
    # that makes a stuck tick visible without a query.
    #
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
    # `config.toml`'s [notes] section calls this one "the tell" by name: with
    # no curator, the notebook only grows, and this is the series that says
    # whether it grows faster than it earns.
    #
    # LABELLED BY WORKSPACE, which the spec's own name is not, and the label is
    # load-bearing rather than decoration. A note index is keyed on the
    # WORKSPACE (§3/DEC-105) and this deployment has more than one — `friends`
    # and `ridgeline` are separate notebooks that policy says are never paired.
    # An unlabelled gauge is written by whichever room tagged last, so two
    # notebooks would overwrite each other's reading and the exported number
    # would mean «the size of whichever notebook was consulted most recently»,
    # which is not a quantity anybody can alert on. Same argument as
    # `nora_dream_evidence_lost_total{scope}` above: a specific unit's reading
    # has to stay findable rather than merging into one process-wide number.
    #
    # Published from `TranscriptInjection._notes` on the read that already
    # happens, NOT from a new `gather` leaf — `assemble`'s nested gather has
    # distinctly-typed overloads only to six positional awaitables and both
    # live branches sit at five, so a seventh joins every return type down to
    # `object` under mypy.
    #
    # ABSENT rather than zero when the tier is off, and that distinction is the
    # point: `_notes` returns `()` before this line when no store is wired, so
    # a missing series means «not wired» while `0` means «wired, and this
    # workspace has written nothing». Those are the two states the tier has
    # been unable to tell apart since it was switched on, and publishing `0`
    # for both would preserve exactly the blindness this closes.
    #
    # **CORRECTION, 2026-08-27, found by reading this series on the first
    # deployment that carried it: absence has a THIRD meaning, and it is the
    # common one.** A prometheus series does not exist until something writes
    # it, and this is written per TAG — so between a restart and the first
    # tagged turn the series is absent on a perfectly healthy process. Observed
    # directly: container up at 15:21:18Z, last tag 15:17:53Z, metric declared
    # in the running image, series absent. Nothing was wrong.
    #
    # So **absence is not alertable on its own** — an alert on it pages on every
    # deploy. It is readable only against a denominator that says whether this
    # process has handled a tag at all (`nora_tags_total`), and the honest
    # reading is:
    #
    #   absent + no tags yet      -> nothing has been asked of the tier
    #   absent + tags handled     -> off, or the read failed (see
    #                                `nora_errors_total{stage="notes"}`)
    #   0                         -> wired, reached, and this workspace is empty
    #
    # Recorded here rather than fixed by publishing `0` at startup, which was
    # the tempting fix and is wrong: a zero written before any read is a
    # measurement of nothing, and it would collapse the first two rows above
    # into the third — the exact `unavailable`-as-`nothing_found` failure this
    # gauge was built to prevent, reintroduced by the attempt to tidy it.
    _GaugeSpec(
        "nora_note_index_entries",
        "Standing notes rendered into <notes> for a workspace on the last tag "
        "it handled. Absent when [notes] is off; 0 means the notebook is "
        "wired and empty.",
        ("workspace",),
    ),
    # The revisitor's own view of the same notebook, and deliberately a SECOND
    # series rather than a reuse of the one above: that gauge is written on the
    # tag path and says what a TURN saw, this one is written on the Dreamer's
    # clock and says what the retiring pass saw. They diverge exactly when it
    # matters — a pass running against a notebook that has changed since the
    # last turn — and a single series would average the two into a number that
    # answers neither question.
    _GaugeSpec(
        "nora_note_revisit_standing",
        "Standing notes the revisitor read for a workspace on its last tick. "
        "0 means the pass ran and the notebook was empty; ABSENT means the "
        "pass has not run for that workspace at all, which is the distinction "
        "that says whether the revisitor is wired.",
        ("workspace",),
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
    # Task 14 fix round 1, sticker spec §5.2: the sticker vision timeout
    # (`media.sticker_timeout_seconds`, shipped at 20.0s — the 2026-08-07
    # probe refuted the original 6s guess) must be MEASURED, not assumed.
    # Observes only the interpreter
    # call itself (`StickerReader._read`'s `self._interpreter.describe(...)`)
    # — never the identity/content cache lookups around it, which would dilute
    # the model's own latency with near-zero cache hits and hide a slow
    # interpreter behind a fast ladder. The default buckets already top out at
    # 30s, past any sane sticker timeout, so they are used as-is.
    # The Dreamer's wall clock, per adaptor. The default buckets top out at
    # 30s and a research loop is budgeted in minutes, so this carries its own
    # set — a histogram whose every observation lands in the last bucket
    # measures nothing.
    _HistogramSpec(
        "nora_dream_run_seconds",
        "Wall-clock duration of one Dreamer adaptor run, by adaptor.",
        (1, 5, 15, 30, 60, 120, 300),
        ("adaptor",),
    ),
    # 26-standing §5. **Deliberately NOT folded into
    # `nora_tag_latency_seconds`**, and that is the decision this entry exists
    # to record: the debrief holds the slot, so its cost IS user-visible, but
    # adding it to an existing SLO series would change what that series
    # measures — a broken gate wearing a passing number. Two series, two
    # questions.
    #
    # The gate is **p95 <= 5s**, and the case it names explicitly is the
    # SECOND-QUEUED tag rather than an average: the person whose turn is being
    # reviewed already has their reply, so the cost lands entirely on whoever
    # is queued behind a busy conversation.
    #
    # Observed on EVERY exit including the failures. A latency series that only
    # records successes describes the path that already works — which is §2's
    # own argument, applied to this stage's instrument instead of its input.
    _HistogramSpec(
        "nora_debrief_seconds",
        "Wall-clock duration of one debrief fold, on every exit path. "
        "Excluded from nora_tag_latency_seconds on purpose. Gate: p95 <= 5s.",
        (0.5, 1, 2, 3, 5, 8, 13, 21),
    ),
    _HistogramSpec(
        "nora_sticker_describe_seconds",
        "Sticker interpreter (vision model) call latency, measured around "
        "the describe() call alone — not the dedup ladder around it.",
    ),
)

# The full set of declared metric names, independent of kind — what the
# name-preservation test (tests/test_metrics_export.py) enumerates against.
DECLARED_NAMES: frozenset[str] = (
    frozenset(spec.name for spec in _COUNTERS)
    | frozenset(spec.name for spec in _GAUGES)
    | frozenset(spec.name for spec in _HISTOGRAMS)
)
