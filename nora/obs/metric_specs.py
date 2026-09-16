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

from nora.domain.media_join import JOIN_PATHS

_DEFAULT_BUCKETS: tuple[float, ...] = (0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30)


class _CounterSpec(NamedTuple):
    name: str
    help_text: str
    labelnames: tuple[str, ...] = ()


class _GaugeSpec(NamedTuple):
    name: str
    help_text: str
    labelnames: tuple[str, ...] = ()


#: DERIVED from `nora.domain.media_join.JOIN_PATHS`, never hand-copied — one
#: named constant the code increments against and the help text reads from
#: (CLAUDE.md's label-vocabulary rule). A list written out here goes stale the
#: first time a ninth transcript builder lands, and a help text naming seven
#: paths for an eight-path series is a metric that documents itself wrongly.
_JOIN_PATHS = " / ".join(JOIN_PATHS)


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
        "nora_errors_total",
        "Internal errors, by processing stage. The stage vocabulary is open — "
        "every call site names its own — but the four stages the AMBIENT "
        "loop's memory reads raise are declared here by name, because they are "
        "the only way an operator can see a block go missing: "
        "stage=\"fire_self_card\" (the participation turn's <self_card> read "
        "failed or Honcho was unreachable), stage=\"fire_people_in_window\" "
        "(one speaker's <people_in_window> read did, counted once per failed "
        "speaker), stage=\"fire_memory\" (the whole concurrent batch hit "
        "[memory] read_timeout_seconds — a port that never answers, which is "
        "not the same failure as one that raises), and "
        "stage=\"proactive_roster_index\" (a <people> roster-index recall on "
        "the decide pass). All four are issue #210 and all four are "
        "fail-soft: the block is lost, never the turn, so absence is the only "
        "symptom and this series is the only tell. The three fire_* stages "
        "sit beside the pre-existing fire_peer_card / fire_standing / "
        "fire_notes / fire_workspace for that set's own stated reason — an "
        "operator asking WHICH half of the context went blind must not have "
        "to guess.",
        ("stage", "platform"),
    ),
    _CounterSpec(
        "nora_silent_failures_total",
        "Failures kept silent from the group, by reason and by which path was "
        "speaking (audience=\"tag\" | \"heartbeat\" | \"participation\" | "
        "\"proactive\" — a kind=proactive row's act turn, design 39 D12 — | "
        "\"participation-shadow\" — the §10.6 mirror — | "
        "\"relevance-gate-shadow\" — the Relevance Gate's review post, which "
        "has no turn behind it at all. Both shadow audiences are "
        "operator-review traffic, never room speech, and a refusal under "
        "either means the reviewer is missing a determination they are "
        "tuning the flip on: read them against "
        "nora_errors_total{stage=\"participation_shadow\"} and "
        "{stage=\"relevance_gate_shadow\"}, which now move on a guard "
        "REFUSAL as well as on an exception).",
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
    # THE INGEST TRACE'S REPLACEMENT (R-76). An ingest contains no model call,
    # so its trace is plumbing and never reaches Langfuse; «how did this message
    # end» is a Prometheus question and this is where it is answered. The
    # `outcome` label's vocabulary is `nora.ingest.pipeline.IngestOutcome`,
    # derived there with `get_args` and checked at runtime before every
    # increment — deliberately NOT restated in this help text, because a
    # hand-copied vocabulary is exactly what drifts.
    _CounterSpec(
        "nora_ingest_outcomes_total",
        "Ambient messages by how the ingest ended (see IngestOutcome).",
        ("platform", "conversation_type", "outcome"),
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
    # Salvage-partial-reply (2026-09-03): a `max_tokens` cut-off whose partial
    # message carried real prose, delivered rather than dropped. Distinct from
    # `nora_recovered_replies_total` (DEC-93) on purpose: that one is a
    # provider not honouring `tool_choice=required`, this one is the model
    # running out of output budget mid-answer — different causes, different
    # fixes, and a rising share here means the tier's `max_tokens` is too
    # tight for the questions being asked.
    _CounterSpec(
        "nora_salvaged_replies_total",
        "Partial replies cut off at max_tokens, salvaged and delivered "
        "(2026-09-03). Non-zero means the model hit its output ceiling "
        "mid-answer.",
        ("platform",),
    ),
    # Pure observability, no new behavioural rule (no DEC/R row — see the
    # comment beside its call site). ONE turn, ONE increment, every admitted
    # tag `_run_admitted`'s `finally` reaches — the standing replacement for
    # a manual Langfuse `nora.outcome` query. The vocabulary is
    # `nora.domain.tag_trace.VALID_OUTCOMES` plus `"unattributed"` (see
    # `nora.engagement.pipeline._ENGAGEMENT_METRIC_OUTCOMES`), never restated
    # here — same precedent as `nora_ingest_outcomes_total`. Deliberately NOT
    # gated on the retention store the way `nora_tagtrace_writes_total` is:
    # this answers "what did the turn end as", which must stay visible even
    # if retention is unwired. `recovered` vs `send_reply` is DEC-93's own
    # split — a rising `recovered` share means the route is not honouring
    # `tool_choice=required`, not that the safety net is working.
    _CounterSpec(
        "nora_engagement_outcomes_total",
        "Every admitted tag, by how the turn ended (see TagTrace.VALID_OUTCOMES "
        "plus \"unattributed\"), and by audience. recovered vs send_reply is "
        "DEC-93's tell: a rising recovered share means the route is not "
        "honouring tool_choice=required. `audience` was added before the "
        "participation occasion shipped (design/dreamer-agent-loop/35 §6): the "
        "series named only tag turns when it was created, and a participation "
        "outcome landing here unlabelled would be indistinguishable from one.",
        ("outcome", "audience"),
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
        "(fire_turn); the input refusals (unmapped / unparseable / past); "
        "screened, a proactive row whose text failed the Dreamer's "
        "identifier/coordinate screen (design/dreamer-agent-loop/39 D3); "
        "refused_origin_unresolvable / refused_origin_wrong_workspace / "
        "refused_origin_not_proactive, a note-linked proactive row whose "
        "origin room cannot be resolved, drifted across a workspace, or is "
        "not proactive-trusted — design/dreamer-agent-loop/40 (#248), where "
        "the row's destination is the note's own room, never a fallback to "
        "home; the "
        "recurrence refusals (bad_repeat, a repeat unit or every_n I do not "
        "accept; bad_bound, a times/until that cannot be honoured or was "
        "given with no repeat at all; too_often, a sub-day repeat faster than "
        "[reminders].min_interval_seconds — #109's floor, where a sustained "
        "rate means people want it finer than a room running one turn at a "
        "time can actually serve); and unavailable, which means no "
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
        "status, or failed_policy / failed_error; a kind=proactive row adds "
        "silent — DONE, the act turn declined — and the fire-time screen's "
        "cancelled_note_retired / cancelled_note_moved, design 39 D11/D12).",
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
    # #110. Paired with `nora_loop_exhausted_fallbacks_total` below and read
    # AGAINST it: a healthy deployment moves THIS one and leaves that one flat.
    # `outcome="drafted"` is a cut-off turn that answered from what it had;
    # `outcome="empty"` is one where the drafting call itself returned nothing
    # (timeout, exception, or a model that said nothing) and the canned line
    # went out after all — so a rising `empty` rate is the drafting timeout
    # being too tight, NOT the feature working.
    #
    # `reason` carries the same three caps as the fallback counter so the two
    # series line up: `drafted{reason=limit_total_tokens}` and
    # `fallbacks{reason=limit_total_tokens}` sum to every turn that hit that
    # cap before replying.
    _CounterSpec(
        "nora_budget_drafts_total",
        "Turns cut off by an R-8 cap before a reply, where Nora made one more "
        "tool-less completion over the context she had already gathered rather "
        "than sending the canned apology (#110, operator 2026-09-06). "
        "`outcome` is drafted (an answer went to the room) or empty (the "
        "drafting call produced nothing and the canned line was delivered "
        "instead — a sustained rate here means the draft timeout is too "
        "tight). `reason` is the cap that fired.",
        ("outcome", "reason"),
    ),
    # The DELIVERY half of #110, and deliberately separate from
    # `nora_budget_drafts_total` above: that one counts what the RUNNER
    # produced, this one counts what the room actually READ. They differ
    # whenever `Deliverer` refuses (R-2/R-4/R-6/R-18) — a draft that was
    # written and then screened out is a real event and must not read as a
    # delivered one. Same `reason` vocabulary as its two neighbours so all
    # three series line up on the cap that fired.
    _CounterSpec(
        "nora_budget_drafts_delivered_total",
        "Budget-cut-off turns where a drafted answer (#110) actually reached "
        "the room, as opposed to being produced and then refused by the "
        "delivery guards. Read against nora_budget_drafts_total{drafted}: a "
        "gap between them is the guards screening drafts, not the drafting "
        "failing. `reason` is the cap that fired.",
        ("reason",),
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
        "Sticker-branch CUSTODY outcomes in the media stage, by platform, "
        "status (ok = the bytes are held / the StickerOutcome reason), and "
        "which ladder level answered — level=identity/content/none. Since "
        "issue #116 the description itself is nora_media_comprehension_total's; "
        "this series is the identity ladder's hit rate.",
        ("platform", "status", "level"),
    ),
    # ── media comprehension (issue #116) ────────────────────────────────────
    # The SPAWNED read: one model pass per medium into title/description/
    # content. R-27's three states and nothing else:
    #   ok            — a comprehension was written for these bytes.
    #   nothing_found — a final answer ABOUT the bytes: `unreadable` (a PDF, a
    #                   lottie sticker — nothing here will ever read it) or
    #                   `already_read` (this sha256 was comprehended before; a
    #                   description is a pure function of the bytes). Neither
    #                   is a failure and neither is an `ok`: counting a cache
    #                   hit as ok would make the ok rate say the describer is
    #                   working when it may not have run for days.
    #   unavailable   — every wiring gap and every failure: no describer
    #                   configured for that modality (`no_<top>_describer`),
    #                   the blob could not be opened (`blob`), the model did
    #                   not answer (`vision`/`timeout`/`empty`), ffmpeg refused
    #                   a video (`ffmpeg`), or the job raised (`error`).
    #                   NEVER nothing_found — «I could not look» is not «there
    #                   was nothing there».
    # R-27 on the join itself. `ok` rendered at least one detail;
    # `nothing_found` read the index and nothing in the window had been
    # comprehended yet — a real answer about those bytes; `unavailable` is the
    # index refusing to be read, which the old shape could not express at all
    # because `MediaObjectStore.get` answers `None` for a miss and for an
    # outage alike (that is why `lookup` exists).
    #
    # It replaces a `nora_errors_total{stage="media_join"}` that could never
    # fire for exactly that reason. Container logs are not on the docker log
    # driver, so without this a permanently failing join is silent on the
    # deployment — every prompt on eight paths quietly losing its media.
    #
    # `reason="capped"` on an `ok` is the OTHER thing worth seeing: the window
    # held more distinct blobs than `DEFAULT_MAX_BLOBS`, so some rendered bare.
    _CounterSpec(
        "nora_media_join_total",
        "Media join outcomes, by path and R-27 status (ok / nothing_found / "
        f"unavailable) with the reason that produced it (store / error). "
        f"Paths: {_JOIN_PATHS}.",
        ("path", "status", "reason"),
    ),
    _CounterSpec(
        "nora_media_comprehension_total",
        "Spawned media comprehension outcomes, by platform, media label, "
        "R-27 status (ok / nothing_found / unavailable) and the reason that "
        "produced it.",
        ("platform", "label", "status", "reason"),
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
        "answers «is the Dreamer stuck?». reason=\"schedule\": this "
        "(adaptor, key) already fired in the current slot of its own "
        "AdaptorSchedule — the ORDINARY outcome for a daily adaptor on a "
        "5-minute tick, and the only series that distinguishes a schedule "
        "holding correctly from one that has stopped firing at all.",
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
    # Per-fire sampling (AdaptorSchedule.samples, design 37 / issue #118):
    # one run is N model calls unioned, and a run's `unavailable` is now
    # «no sample was readable». This is the per-sample reading, so an operator
    # can tell «one of five samples timed out» from «the run failed».
    _CounterSpec(
        "nora_dream_sample_total",
        "Model calls a Dreamer run made, by adaptor and by whether the "
        "sample's envelope was readable: ok, or unavailable (timeout, "
        "exception, or no envelope). A run unions its readable samples and "
        "is unavailable only when this counts none as ok for it.",
        ("adaptor", "status"),
    ),
    _CounterSpec(
        "nora_dream_written_total",
        "Rows written through an adaptor's sink, by adaptor.",
        ("adaptor",),
    ),
    # The fail-closed branch in `DreamerToolSource.build` (39 D2/D3): the
    # adaptor's grant names a home room, the workspace declares none in
    # policy.toml, and the acts are withheld along with the room. This is a
    # CONFIG state, true on every pass until somebody edits a file, so the log
    # line beside it is emitted once per (adaptor, workspace) — that is what
    # this counter is for. Without it, deduplicating the log would have traded
    # a stream of identical errors for no signal at all, and «the proactive
    # adaptor has been inert in this workspace for a week» is exactly the
    # reading an operator needs and cannot get from a single startup line.
    _CounterSpec(
        "nora_dream_home_room_missing_total",
        "Dreamer builds that wanted a home room and found none declared for "
        "the workspace: the adaptor ran with no acts and no room. A standing "
        "config gap, counted every pass; the log says it once.",
        ("adaptor", "workspace"),
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
    # Issue #63's alarm, and it is the production half of a defect whose CI
    # half is `tests/contract/test_dream_adaptor_contract.py`. An adaptor
    # whose objective asks the model to echo a row id back has EVERY item
    # dropped by `dream_screen` (`_UUID` matches any hyphenated uuid): the
    # tick spends a model call, writes nothing, and reports `ok`. Three
    # adaptors shipped that way. `nora_dream_screened_total` counts the drops
    # per item and cannot answer «was that all of them», because a run's item
    # count is not on that series — this is the series that can. Structurally
    # zero while every adaptor keeps identifiers out of its envelope, which is
    # exactly what makes a non-zero reading worth an alert.
    _CounterSpec(
        "nora_dream_empty_after_screen_total",
        "Dreamer runs where the model returned items and the R-18/R-28 "
        "screen kept none, by adaptor: a model call spent, nothing written, "
        "and the run still `ok` (R-27 — «there was nothing» is a claim a run "
        "whose model DID answer may not make). Non-zero means an adaptor is "
        "putting an identifier or a coordinate in its envelope.",
        ("adaptor",),
    ),
    # Issue #60. `DreamerRuntime` bounded only `factory.invoke`, so a store
    # call inside `adaptor.inputs` or `adaptor.commit` that never returned
    # left `dream_loop` holding `<adaptor>:<key>` in `running` FOREVER — that
    # unit never ticked again, and the skip landed on
    # `nora_dream_skipped_total{reason="in_flight_adaptor"}`, which cannot
    # separate a long run from one wedged since Tuesday. The timeout now
    # releases the unit and lands HERE, where it is a different diagnosis
    # from the `unavailable` a 429 or a prose reply produces.
    _CounterSpec(
        "nora_dream_store_timeout_total",
        "Adaptor store phases cut off by the runtime's OWN wall clock, by "
        "adaptor and phase: inputs (the render's reads) or commit (the "
        "tier's writes). The run is `unavailable` and the unit is released "
        "as soon as the phase can be cancelled — a collaborator that "
        "swallows CancelledError, or a synchronous blocking call, can still "
        "outlive it. A collaborator's OWN timeout (an asyncpg "
        "command_timeout, an aiohttp read) is NOT counted here: it is the "
        "same TimeoutError class and would put a false reading on the one "
        "series that says a store wedged.",
        ("adaptor", "phase"),
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
    # TWO findings on one series, separated by `rule`
    # (`nora.agent.dream_guard.GuardRule`), and reading them as one is how this
    # help text was wrong for a day:
    #
    #   rule="R-7"           — the model asked for a tool its adaptor never
    #                          granted, or spent that tool's own ceiling. Under
    #                          a read-only ceiling that is not a leak, but it IS
    #                          the tell that an objective and its tool subset
    #                          disagree about what the job needs. A CONFIG
    #                          fault: it should be near zero, and a steady rate
    #                          is something to go and fix.
    #   rule="memory-budget" — the pass spent its whole memory ration and asked
    #                          again (R-91, issue #160). Ordinary model
    #                          behaviour that the ceiling absorbs, not a fault;
    #                          the pass is answered `unavailable` and carries
    #                          on. A NON-ZERO rate here is the ceiling doing its
    #                          job. What is worth an alert is the pathological
    #                          arm — a pass that spends its whole `turns` budget
    #                          on refusals — which shows as this series climbing
    #                          alongside `nora_dream_stop_reason_total
    #                          {reason="limit_turns"}` and a `run_total`
    #                          `unavailable` for the same adaptor.
    _CounterSpec(
        "nora_dream_guard_vetoes_total",
        "Tool calls refused by DreamGuard, by adaptor and rule: R-7 is a tool "
        "never granted or past its own ceiling, memory-budget is the pass's "
        "memory ration spent.",
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
    #   broken_turn  — issue #199: the turn ended `llm_error` or `timeout`, so
    #                  the fold was refused the proposal path before anything
    #                  was parsed. ONE sample per fold, not one per note —
    #                  parsing first would have counted `already_read` and
    #                  `over_cap` for proposals that could never be made
    _CounterSpec(
        "nora_debrief_note_proposals_total",
        "Notes the debrief raised, by disposition: proposed, already_read, "
        "refused, over_cap, malformed, unwritten (the propose did not "
        "complete; whether it committed is unknown), broken_turn (the turn "
        "ended llm_error or timeout — a dead turn raises no notes), "
        "no_workspace (policy "
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
    # Issue #199's deterministic half, and it exists because the check it
    # counts would otherwise make `nora_turn_verdict_total` unreadable.
    # #189's finding — the judge has never once returned false — is read off
    # that series, and from now on a `false` there may be code-authored. The
    # `model_verdict` label says what the MODEL had said at the moment the
    # code overrode it: `grounded` is the incident's own shape (the judge
    # blessed a contradicted position), `ungrounded` means both halves agreed,
    # `absent` means the judge produced nothing usable and this is the only
    # verdict the turn has. A rising `grounded` is the reading that says the
    # prompt half is still not seeing what the record does.
    _CounterSpec(
        "nora_debrief_room_scope_overrides_total",
        "Turns the debrief's deterministic check forced to ungrounded — the "
        "turn broke without sending while a reply of hers was delivered "
        "contemporaneously — by what the model's own verdict had said: "
        "grounded, ungrounded, or absent. Subtract nothing from "
        "nora_turn_verdict_total; read the two together, because a false "
        "there no longer implies the judge convicted.",
        ("model_verdict",),
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
        "stage: first (the model was asked to consolidate), final (nothing "
        "was written and the prior position stands) or rollup (an event-path "
        "fold refused the batch; the watermark did not advance and the next "
        "message's check retries).",
        ("stage",),
    ),
    _CounterSpec(
        "nora_standing_event_folds_total",
        "Event-triggered standing fold ROUTINES (design/standing-watermark/10): "
        "the accumulator reached [standing] fold_batch and an event fold was "
        "enqueued through ConversationSlots. One per trigger firing that led "
        "to a fold — read against nora_standing_rollup_folds_total: the gap "
        "(rollup > event) is the backlog a cold start or a burst had to drain "
        "in batches.",
    ),
    _CounterSpec(
        "nora_standing_rollup_folds_total",
        "Rollup folds — the BATCHES folded by the event path's rollup loop "
        "(design/standing-watermark/30). Each increment is one fold of up to "
        "[standing] fold_batch rows that wrote a position and advanced the "
        "watermark. Rollup folds do NOT increment the row's fold_count (D3): "
        "that counter stays the tag path's ceiling feed; this series is where "
        "rollup work is counted instead.",
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
    # `nora_fact_entity_unresolved` again — instance 7 in
    # `design/dreamer-agent-loop/02-instrument-blindness.md`'s own tally, and
    # the exact defect this block was written to close. They ship with their
    # adaptor.
    #
    # `trigger` carried one value until issue #98 (the create path):
    # `"create"` is the second one this comment anticipated — a FRESH
    # `kind='create'` row staged by `nora.language.note_candidates`, counted
    # only on the first sighting of an occasion (a re-observation refreshes
    # the row without being a second "recording", exactly as a repeated miss
    # is not a second miss).
    #
    # **UNTIL 2026-09-13 `trigger="create"` OVER-COUNTED, and the code had
    # been contradicting the sentence above since #98 shipped (fixed in
    # #228).** The guard was `row.attempts == 0` alone, and `attempts` counts
    # GATE attempts, not sightings: a candidate handed back a row the gate had
    # already SETTLED comes back with `attempts == 0` every time, so every
    # re-observation of a settled occasion counted as a fresh arrival. Not a
    # double-count with a factor — unbounded in the number of ticks the room
    # keeps mentioning the same thing. The guard is now `row.attempts == 0 and
    # row.status == CANDIDATE_PENDING`. Anything read off this series BEFORE
    # that date is an upper bound on arrivals and nothing more; in particular
    # `nora_note_candidates_total` minus `nora_note_candidate_gate_total` was
    # not the count of proposals that never reached a decision, which is what
    # it looks like and what `nora_note_candidate_staging_total` below now
    # actually answers. Prometheus keeps ~4 days on this deployment, so the
    # contaminated window closes on its own; the note is here because a reader
    # of a longer-range export would have no other way to know. No in-repo
    # consumer read it — no recording rule, no alert, no dashboard — which is
    # why this is a spec comment rather than a release fragment.
    _CounterSpec(
        "nora_note_candidates_total",
        "Note candidates recorded by what raised them. trigger=\"miss\": a "
        "note_search that matched nothing, written to the negative cache so "
        "the SECOND ask on the same ground is evidence rather than a guess. "
        "trigger=\"create\": a full note proposal the note-candidates "
        "adaptor staged for the deterministic gate — see "
        "nora_note_candidate_gate_total for what the gate then decided.",
        ("trigger",),
    ),
    # ---- note-candidates (§4, issue #98): the create path -------------------
    #
    # The gate's OWN outcome, separate from the staging count above: a row can
    # be staged (and counted there) many times across ticks before the gate
    # ever decides it, so this is the reading that answers "did the tier
    # actually write anything", not "how many proposals arrived".
    _CounterSpec(
        "nora_note_candidate_gate_total",
        "The note-candidates adaptor's deterministic gate, by outcome: "
        "created (the model named no standing note, so a brand new one was "
        "written), updated (APPENDED as a revision to the standing note the "
        "model NAMED in its updates field — resolved by title since "
        "2026-09-13, the anchor date having been the key from 2026-09-08 "
        "until it merged two unrelated occasions twice out of two firings, "
        "and appending rather than replacing since the same day, issue "
        "#228), unresolved (the model named a title NO standing note answers "
        "to — mistyped or invented — so a new note was written; its own "
        "action rather than part of created because this is the rate the "
        "whole design rests on being low, and it was invisible inside "
        "created, with no log either, until 2026-09-13), ambiguous (the "
        "named title answers to MORE than one standing note, so a NEW note "
        "was written rather than a guess at which one was meant — a "
        "duplicate overwrites nothing and a reader can see both, appending "
        "to the wrong note is neither; nothing MERGES the duplicate "
        "afterwards, and issue #236 carries that bound), refused (a "
        "duplicate with nothing new, or a malformed proposal the domain type "
        "itself rejected), full (the appended body would pass the note body "
        "ceiling, so the revision was refused rather than truncated — a rate "
        "here reads as the curator not retiring, never as a model fault), "
        "or deferred (a transient failure — a raced CAS, a store exception, "
        "or a note that moved or retired between the read and the write — "
        "left PENDING for the next tick to retry, never a decision). A "
        "candidate handed back a row an EARLIER tick already settled reaches "
        "no action here at all; nora_note_candidate_staging_total is where "
        "that is counted.",
        ("action",),
    ),
    # The SETTLED-ROW branch of `_settle`, which the gate's own vocabulary
    # deliberately does not cover — nothing is decided there, so folding it in
    # would stop the gate's sum being the number of candidates decided.
    #
    # It gets a series at all because the two things that reach it are a
    # benign no-op and a DESTROYED proposal, and until 2026-09-13 they were
    # indistinguishable from outside: measured against the real adaptor, two
    # different same-day candidates with no people named and one occasion
    # re-observed a tick later produced the SAME arrivals count, the same gate
    # total and the same note count. `nora_note_candidates_total` minus
    # `nora_note_candidate_gate_total` therefore could not say whether a
    # proposal had been destroyed — R-27's `unavailable`-reported-as-
    # `nothing_found` one layer down, and the reason this was declared before
    # the collision itself was fixed (issue #235).
    #
    # THE COLLISION IS FIXED as of 2026-09-16: the staging key gained
    # `lower(btrim(title))`, so two arrangements on one day are two staged rows
    # and the second reaches the gate. `displaced` is therefore expected to
    # read ZERO from here on, and that expectation is the reading — a non-zero
    # sample now means the key widened again, or that the index's `lower()`
    # and the branch's `casefold()` disagreed on a title. The series is kept
    # for exactly that: a loss counter that cannot fire is the right state for
    # a loss that no longer happens, and deleting it would let a regression
    # land as silence, which is what it was declared to prevent.
    _CounterSpec(
        "nora_note_candidate_staging_total",
        "A note candidate handed back a staging row the gate had ALREADY "
        "settled, by what that means. reobserved: the same arrangement seen "
        "again across ticks — the benign case the staging table exists to "
        "absorb, and nothing is lost. displaced: a DIFFERENT arrangement "
        "that collided on the staging key (workspace, anchor date, named "
        "people, normalised title) and was dropped before the gate ever saw "
        "it — one proposal destroyed, no note written, no gate action "
        "counted, issue #235. Since 2026-09-16 the title is part of that key, "
        "so displaced is expected to read zero and a sample on it means the "
        "key no longer separates two arrangements on one day; reobserved is "
        "the ordinary steady-state value here.",
        ("outcome",),
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
    # The revisitor's OWN negative-measurement series, folded into
    # `nora_note_curator_*` by the note-curator merge
    # (design/dreamer-agent-loop/34, issue #99): `NoteRevisitor` is gone, its
    # due-selection/evidence/grounding logic lives in
    # `nora.language.note_curator.NoteCuratorAdaptor` now, and the series
    # below are its renamed replacements. A stale `nora_note_revisit_*` series
    # left publishing would double-count the retirement readings this merge
    # exists to unify — see `nora_note_retirements_total` further down, which
    # is the one series this merge does NOT rename.
    # ---------------------------------------------------------------------
    _CounterSpec(
        "nora_note_curator_candidates_total",
        "Notes the curator gathered evidence for, by whether it had anything "
        "to judge them against — every anchored_before/due_for_revisit note "
        "(the set that inherits the former revisitor's grounding), NOT a "
        "merge-group member, which is judged on its body alone (deliberately "
        "exempt — see nora.language.note_curator's own module docstring). "
        "FOUR values, and a reader who sees only three has an out-of-date "
        "copy of this text. evidence=\"present\": the conversation has "
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
        "nora_note_curator_deferred_total",
        "Ticks where [notes] max_revisits_per_tick capped the due-by-"
        "revisit_at selection — a BIT, not a magnitude "
        "(nora.domain.bounds' own over-fetch-by-one convention): "
        "SqlNoteStore takes no metrics collaborator, so the curator "
        "over-fetches by one and counts a tick where more were due than it "
        "judged, rather than the exact backlog size the pre-merge revisitor "
        "could see from an unbounded scan. A backlog stated rather than "
        "silently truncated either way: without this, a tick that judged ten "
        "of forty due notes reads identically to a notebook with ten due.",
    ),
    _CounterSpec(
        "nora_note_retirements_total",
        "Retirements the curator attempted, by reason (because="
        "\"retired-expired\" or \"retired-closed\" — nora.domain.notes."
        "NoteSupersedeReason's own vocabulary) and by whether they MOVED "
        "anything — emitted by nora.language.note_curator."
        "NoteCuratorAdaptor.commit since the note-curator merge (design 34, "
        "issue #99; previously the revisitor's own). KEPT under this EXACT "
        "name through the merge — it is the real retirement series and must "
        "not fold into a renamed one. result=\"applied\": supersede affected "
        "a row. result=\"no_rows\": it affected none — the note was already "
        "retired or the id no longer resolves. That split is the point: "
        "supersede returns 0 for an unknown target and never raises, so "
        "without it a retirement that silently did nothing is "
        "indistinguishable from one that was never attempted.",
        ("because", "result"),
    ),
    # ---- the curator's readings, shipped WITH the curator ------------------
    #
    # Declared now and not earlier, which was the whole argument of the block
    # above: a name with no call site exports a flat zero and a flat zero reads
    # as a measurement. These three have call sites as of the same commit that
    # declares them.
    # The tier's OWN named justification, finally readable: 27 §6 says the
    # curator's whole case rests on "notes retired versus notes grown", and
    # `nora_note_index_entries` was only ever half of that. This is the other
    # half — without it a flat index means either "nothing needed retiring" or
    # "the curator retired one and somebody wrote one", and those are opposite
    # healths. (It sat above the REFUSALS counter until 2026-08-28, two specs
    # further down, describing neither of the things next to it.)
    _CounterSpec(
        "nora_note_curator_total",
        "Curator actions by what it did: retire_failed, retire_stale, closed, "
        "moot, expired, dateless_stale. **retire_stale is NOT an error** — "
        "the note moved under the pass, which is the tag path winning a race "
        "the head check exists to arbitrate, and the work is re-selected "
        "next tick because the selection read is live state rather than a "
        "cursor. retire_failed IS a fault. (merge, merge_refused and "
        "merge_failed were here until the merge half was removed on "
        "2026-09-08.) expired is the "
        "note-curator merge's addition (design 34, issue #99) — the former "
        "revisitor's word for the moment passing with nothing answering it, "
        "now grounded the same way moot is. dateless_stale is OBSERVED, not a "
        "retirement: a dateless note's strikes crossed the ceiling after a "
        "wake with no tag-path progress, which makes it ELIGIBLE for dateless "
        "retirement without this merge acting on it (issue #101's job). The "
        "vocabulary is derived from nora.language.note_curator.CURATOR_ACTIONS "
        "and a test asserts this text names exactly that set.",
        ("action",),
    ),
    _CounterSpec(
        "nora_note_curator_dropped_total",
        "Model answers the curator refused, by reason: malformed (a field "
        "missing or a verdict outside the four the objective defines), "
        "unknown_note (a handle this run did not render), or "
        "conflicting_verdict (two contradictory answers about one note — not a "
        "tie to break but «I cannot tell», so the note stays standing). The "
        "four merged-body refusals (body_too_long, shorter_than_inputs, "
        "not_shown_in_group, conflicting_merged_body) went with the merge "
        "half on 2026-09-08. A drop is "
        "never a guess — a retirement is a permanent removal from an index "
        "nobody can see the inside of. Five more reasons folded in by the "
        "note-curator merge (design 34, issue #99), the former revisitor's own "
        "grounding refusals — applied to a closed/expired/moot verdict about "
        "an anchored_before/due_for_revisit note, NEVER to a merge-group "
        "member, which stays exempt (judged on its body alone, unchanged "
        "from the pre-merge curator): "
        "no_evidence (nothing in the note's evidence window to judge a "
        "retirement against — silence is not an answer), citation_missing / "
        "citation_ungrounded (a closed verdict with no usable cited message, "
        "or one citing a message this run never showed for that note), "
        "anchor_not_passed (an expired/moot verdict on a note whose anchor "
        "has not gone by, or has none at all — the clock decides that, not "
        "the model), anchor_not_covered (the anchor HAS passed but the "
        "evidence window does not span it — the room fell silent before the "
        "anchor, or is busier than the window), or discharge_malformed (a "
        "closed verdict's discharge field, or one entry in it, did not fit "
        "the three warrant shapes issue #101's discharge step accepts).",
        ("reason",),
    ),
    # Discharge (design/dreamer-agent-loop/33-note-dreamer-schedule.md §7,
    # 19-between-tiers.md §1, issue #101) — what a CLOSED note's settled
    # content becomes on its way out of the notebook, riding the curator's
    # own tick rather than a new adaptor. Two series for two different
    # questions: whether discharge ran at all for a note (below), and what
    # happened to each item it classified (this one).
    _CounterSpec(
        "nora_note_discharge_total",
        "One classified discharge item's write, by kind (commitment -> the "
        "Gathering/Event service, person_claim -> Honcho's conclusion lane) "
        "and outcome: written (ok), refused (a code-level "
        "decision — malformed, or the closing citation's sender maps to no "
        "resolvable person to attach it to — never a guess), self_cited "
        "(issue #199: the note was closed by citing one of Nora's OWN "
        "delivered rows, so no person's warrant carries the item — kept apart "
        "from refused because an unmapped sender is a roster gap an operator "
        "can close and this boundary never closes), unavailable "
        "(discharge_enabled is true but the owning tier's collaborator is "
        "missing, or its write raised). Emitted by nora.language."
        "note_curator.NoteCuratorAdaptor's discharge step, which runs only "
        "for a GROUNDED closed verdict that actually retired — an "
        "expired/moot note answered nothing, so it never reaches this "
        "metric at all. The kind vocabulary is nora.language.note_curator's "
        "DISCHARGE_KINDS, which the writer validates against before "
        "incrementing — a third kind was routed to the knowledge tier until "
        "it was deleted on 2026-09-07, and this text is what said so after "
        "the route was gone.",
        ("kind", "outcome"),
    ),
    _CounterSpec(
        "nora_note_discharge_notes_total",
        "Whether a closed, retired note had anything to discharge, by "
        "result: items (its classification named at least one commitment "
        "or person-claim — regardless of whether each one then "
        "wrote or was refused), empty (the ordinary, common answer — most "
        "closed notes settle one question with nothing else worth routing, "
        "and that is R-27's nothing_found, not a wiring gap), or lost "
        "(discharge STARTED and did not finish — the dream pass was "
        "cancelled by its wall clock part-way through, and because the note "
        "is already retired its content is gone with nothing to re-derive it "
        "from: issue #195, R-27's unavailable, counted from a finally "
        "because CancelledError is a BaseException). Absence of a "
        "sample for a note is different from all three: it means discharge "
        "never ran at all (discharge_enabled is false, or the verdict was "
        "expired/moot/ungrounded). A non-zero lost once discharge_enabled is "
        "on is the stated condition for building the durable, resumable "
        "version instead.",
        ("result",),
    ),
    _CounterSpec(
        "nora_note_update_refused_total",
        "note_update calls refused, by reason: not_read (the R-55 latch — the "
        "model had not read a note by that title this turn), title_moved (the "
        "title resolved to a different note than the one read), or stale_head "
        "(the compare-and-set lost; the note moved under the call).",
        ("reason",),
    ),
    # DECLARED LATE, and the gap was not inert. `engagement/context.py` has
    # incremented this name since the peer-card blindness was found on
    # 2026-08-15, but it was never added here — so `Metrics.inc` fell through
    # its counter branch to the bare `self._gauges[name]` lookup and raised
    # `KeyError`. That raise lands INSIDE `_peer_card`'s own `except
    # Exception`, which logs "peer-card read failed" and increments
    # `nora_errors_total{stage="peer_card"}`.
    #
    # So the one counter whose entire purpose is to separate «her card is
    # genuinely empty» from «the read failed» was reporting every genuinely
    # empty card as a transport failure — the exact confusion it was added to
    # end, inverted. The instance is one line; the reason it survived is that
    # nothing checked call sites against this tuple, which is what
    # `tests/architecture/test_every_recorded_metric_is_declared.py` now does.
    _CounterSpec(
        "nora_peer_card_empty_total",
        "Peer-card reads that came back empty, by platform — i.e. tagged turns "
        "where her card was NOT rendered into the prompt. It cannot say why: "
        "`HonchoClient._get` catches every transport failure and returns None, "
        "so a dead endpoint, a timeout, a 4xx, an unknown peer and a genuinely "
        "empty card all arrive here as the same `[]`. Do NOT read this against "
        "`nora_errors_total{stage=\"peer_card\"}` expecting the two to "
        "partition the space — an outage lands in THIS series, not that one, "
        "which is why that stage label stays near zero even while Honcho is "
        "down. Separating the four needs `Recollection`'s two-axis shape on "
        "the port, which is a port change; until then a RISING count here is "
        "«her card stopped appearing» and nothing narrower.",
        ("platform",),
    ),
    # Bounded reads where the LIMIT actually bit, by site. The vocabulary is
    # `nora.domain.bounds.BoundSite` and is deliberately NOT restated here —
    # `nora_ingest_outcomes_total`'s own help text sets that precedent, because
    # a hand-copied vocabulary is exactly what drifts.
    #
    # It counts the BIT, not the remainder: one increment per truncated read,
    # never "how many were left". Over-fetching by one is the only probe that
    # is cheap and correct at every site, so a series carrying a magnitude from
    # the sites that can compute one and a 1 from the sites that cannot would
    # be lying about its own unit. Sites with a real depth to report keep their
    # own gauges — this does not replace `nora_note_revisit_deferred_total`.
    #
    # Read it AGAINST the call rate for the same site. Sustained truncation on
    # every call is not "the cap is working", it is a backlog this series
    # cannot size — see `nora.domain.bounds`' reversal condition.
    _CounterSpec(
        "nora_bound_truncated_total",
        "Bounded reads where the limit actually bit — one per truncated read, "
        "by site. A read that returned exactly `limit` rows and a read that "
        "stopped at `limit` with more behind it are otherwise identical, and "
        "the second is `unavailable` wearing `nothing_found`'s face. Sites are "
        "named for where the bound is APPLIED (`store.method`), never for the "
        "caller; the vocabulary is `nora.domain.bounds.BoundSite`.",
        ("site",),
    ),
    # The participation occasion (design/dreamer-agent-loop/35 §6). Silence is
    # the HEALTHY outcome of a participation turn — the model was offered the
    # floor and declined — so it gets its own series and NEVER lands in
    # `nora_silent_failures_total`, whose every reason is a malfunction. The
    # gate is `ok=True and stop_reason not in BUDGET_STOP_REASONS`: a turn the
    # budget cut off before it could call the send tool is a malfunction of
    # the ordinary kind and goes to `nora_silent_failures_total` with
    # audience="participation", never here.
    _CounterSpec(
        "nora_participation_silence_total",
        "Participation determinations that concluded 'say nothing' (the "
        "healthy default — design/dreamer-agent-loop/35 §6), by platform. "
        "Budget-cut turns are NOT here; those are silent failures.",
        ("platform",),
    ),
    _CounterSpec(
        "nora_participation_spoke_total",
        "Participation determinations that concluded 'speak' and delivered, "
        "by platform. Read against nora_participation_silence_total: the "
        "ratio is how often the occasion finds grounded value.",
        ("platform",),
    ),
    # The proactive occasion (design/dreamer-agent-loop/39 D12, DEC-162) —
    # participation's pair, one occasion later: a `kind="proactive"` row's
    # act turn that concluded «say nothing» (the healthy default, and the
    # row is DONE with outcome="silent") and one that spoke. Budget-cut
    # turns are silent failures under audience="proactive", never here.
    _CounterSpec(
        "nora_proactive_silence_total",
        "Proactive act turns that read the room again and declined their "
        "own earlier intention (the healthy default — design/dreamer-agent-"
        "loop/39 D12), by platform. Budget-cut turns are NOT here.",
        ("platform",),
    ),
    _CounterSpec(
        "nora_proactive_spoke_total",
        "Proactive act turns that spoke and delivered, by platform. Read "
        "against nora_proactive_silence_total.",
        ("platform",),
    ),
    # The decide turn's own reading (39 D4): what the proactive adaptor's
    # envelope said it did, per item — `remind` (a row was written by the
    # tool call this item records) or `decline` (considered and left). The
    # rows themselves are counted by `nora_reminders_created_total`; this is
    # the decision record beside them.
    # The deterministic check before the decide turn (issue #136): why an
    # hourly tick rendered a prompt, or why it did not. `daily` and `changed`
    # are the ticks that cost a model call; `unchanged` and `held_quiet` cost
    # one store read; `unclocked` is a wiring gap (no pass clock) that
    # renders every tick. The reversal condition on the change-driven shape
    # is `changed` runs far outnumbering remind decisions.
    _CounterSpec(
        "nora_proactive_pass_total",
        "Proactive adaptor ticks, by what the tick DID — not by what made it "
        "due, which is why a due tick that rendered nothing is not counted as "
        "a pass. RENDERED: daily (the floor), changed (notes/Standing moved "
        "and the HOME ROOM is quiet), unclocked (no pass clock wired — every tick "
        "renders). DID NOT: held_quiet (moved but the HOME ROOM is still "
        "talking — the room a row fires into, and so the only one a pass can "
        "interrupt; every room of the workspace until 2026-09-10), "
        "unchanged, nothing_shown (due, but no open notes and no room has "
        "written a position), no_home_room (the operator has not said where "
        "she stands — a wiring gap, and it is here so it does not read as "
        "silence).",
        ("reason",),
    ),
    _CounterSpec(
        "nora_proactive_decisions_total",
        "Proactive decide-turn decisions, by action: remind or decline.",
        ("action",),
    ),
    _CounterSpec(
        "nora_proactive_reconciled_total",
        "Reminder rows `remind` wrote inside the decide loop that the pass's "
        "own envelope never reported, found by reading the rows back in "
        "`commit` and charged a wake there. `remind` writes during the model "
        "loop, so a screened item, an unknown handle, or a run with no "
        "readable sample loses the account of a write that already happened — "
        "and an uncharged wake lets the next pass raise the same note again. "
        "Expected to sit at zero: any sustained rate here says a screen or a "
        "sample is dropping decisions, and this series is the only place that "
        "is visible.",
        (),
    ),
    # The Relevance Gate (design/dreamer-agent-loop/36). TWO series, and the
    # split is the point: this one moves only when the gate actually decided,
    # the one below only when it could not. Both outcomes are a `no_go` at the
    # trigger, and folding them together would make «the room was quiet» and
    # «the gate is down» the same reading — R-23's bug, which this repo has
    # already paid for once.
    #
    # NEVER counted in any tag, heartbeat or participation series: the gate
    # decides whether a turn happens and is not itself a turn, so a gate
    # verdict landing in `nora_engagement_outcomes_total` or
    # `nora_participation_silence_total` would corrupt the very measurement
    # the flip decision reads.
    _CounterSpec(
        "nora_relevance_gate_total",
        "Relevance Gate verdicts the MODEL returned, by verdict and platform "
        "(design/dreamer-agent-loop/36). A fail-closed no_go is NOT here — it "
        "is on nora_relevance_gate_errors_total — so go/(go+no_go) here is a "
        "true admission rate and not a rate diluted by outages. In shadow "
        "mode only the go verdicts are delivered anywhere; this series is the "
        "only place the no_go rate is observable at all, which is what keeps "
        "an undelivered verdict from being an unmeasured one.",
        ("verdict", "platform"),
    ),
    _CounterSpec(
        "nora_relevance_gate_errors_total",
        "Gate calls that produced no usable verdict and therefore FAILED "
        "CLOSED to no_go, by kind and platform. The vocabulary is "
        "`nora.ingest.relevance_gate.GATE_ERROR_KINDS` and is deliberately "
        "not restated here — a hand-copied vocabulary is what drifts. A "
        "SUSTAINED count here is the gate being down while looking exactly "
        "like a quiet room, which is the one failure this series exists to "
        "make visible.",
        ("kind", "platform"),
    ),
)

_GAUGES: tuple[_GaugeSpec, ...] = (
    _GaugeSpec(
        "nora_standing_watermark_gap",
        "Rows behind a conversation's standing watermark, as the event "
        "trigger's accumulator read measured them (design/standing-watermark/"
        "10). Capped at [standing] fold_batch by the read's limit: a value "
        "of fold_batch means AT LEAST a batch remains, not exactly one. Zero "
        "or near-zero is caught up.",
    ),
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
    # A GAUGE against its per-tick cap, not a counter, and the distinction is
    # 27 §5.2's: the curator reads whole bodies and `note_revision.body` has no
    # upper bound, so ONE long body can consume a tick's whole allowance. The
    # reading that matters is "how much of the budget did this tick spend",
    # which is a level; a running total cannot answer it.
    #
    # When this sits at the cap, the tier reports "nothing to do" for the notes
    # it never reached — the `unavailable`-as-`nothing_found` failure at the
    # pass level, which the tier's own spec names in advance.
    _GaugeSpec(
        "nora_note_curator_bodies_read",
        "Note bodies the curator FETCHED on its last tick for a workspace — "
        "every expired note it read ([notes] max_expired_per_tick, one body "
        "each) and every due-by-revisit_at note it read ([notes] "
        "max_revisits_per_tick, one body each — folded in by the note-curator "
        "merge, design 34/issue #99). The merge-group read and its cap went "
        "with the merge half on 2026-09-08. Written on every tick including "
        "an idle one, where it is 0; absent means the pass did not run. A "
        "note selected by both reads is "
        "fetched once per read that selected it and counted that many times, "
        "because it spends a slot in each cap it occupies. Sitting at a cap "
        "means the pass is reporting 'nothing to do' about notes it never "
        "reached.",
        ("workspace",),
    ),
    _GaugeSpec(
        "nora_note_index_entries",
        "Standing notes rendered into <notes> for a workspace on the last tag "
        "it handled. Absent when [notes] is off; 0 means the notebook is "
        "wired and empty.",
        ("workspace",),
    ),
    # The curator's own view of the same notebook, and deliberately a SECOND
    # series rather than a reuse of the one above: that gauge is written on the
    # tag path and says what a TURN saw, this one is written on the Dreamer's
    # clock and says what the retiring pass saw. They diverge exactly when it
    # matters — a pass running against a notebook that has changed since the
    # last turn — and a single series would average the two into a number that
    # answers neither question. Renamed from `nora_note_revisit_standing` by
    # the note-curator merge (design 34, issue #99) — same reading, same
    # writer's clock, now the curator's own rather than a second adaptor's.
    _GaugeSpec(
        "nora_note_curator_standing",
        "Standing notes the curator read for a workspace on its last tick. "
        "0 means the pass ran and the notebook was empty; ABSENT means the "
        "pass has not run for that workspace at all, which is the distinction "
        "that says whether the curator is wired.",
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
    # REPLACES `nora_sticker_describe_seconds`, which was removed with the
    # model rung it measured (issue #116): the sticker ladder is custody now
    # and makes no model call, so that name would have been a declared metric
    # with no call site — CLAUDE.md's "a lie", rendering as a series that is
    # genuinely always zero.
    #
    # Measured around the READ ALONE — the interpreter call, or the whole chunk
    # run for a video — not the job around it. A `nothing_found` never reaches
    # this line, so the dedup that skips a re-read cannot dilute what this says
    # about a describer's own latency. The buckets reach 300s deliberately:
    # a chunked video read (issue #117) is ten model calls, not one, and a
    # histogram whose top bucket is 21s would report every video as +Inf.
    # The COST of the render join, not its result (CLAUDE.md: "instruments
    # measure the RESULT; something must measure the COST"). Design 19 §10
    # names this join as the condition that would reopen the decision — one
    # sequential `get` per distinct blob in the window, on the critical path of
    # every tagged turn — and a reopening condition with no way to read it is
    # not a condition, it is a hope. Buckets are sub-second because anything
    # above that is already the answer.
    _HistogramSpec(
        "nora_media_join_seconds",
        "Wall-clock cost of one media join, by the PATH that paid it "
        f"({_JOIN_PATHS}). Excludes calls with no media in the window.",
        # Reaching 30s, and labelled, because the eight paths have nothing in
        # common but the code. A tag window is a handful of blobs under a
        # reply's latency; adoption hands this a 1000-row backfill scan at
        # process start. One unlabelled series with a 2s ceiling put both in
        # one histogram, where the batch paths — the exact ones where N is not
        # 1 — saturate `+Inf` and are unmeasurable, and the tag path is the
        # only thing anybody can read.
        (0.005, 0.025, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30),
        ("path",),
    ),
    _HistogramSpec(
        "nora_media_blobs_joined",
        "Distinct blobs looked up in one media join, by path — the count the "
        "latency above is a function of, and the reading that says whether "
        "`DEFAULT_MAX_BLOBS` is binding.",
        (1, 3, 8, 21, 50, 100, 200, 500),
        ("path",),
    ),
    _HistogramSpec(
        "nora_media_comprehension_seconds",
        "One media comprehension read's latency — the model call alone for an "
        "image, the whole chunk run for a video. Excludes cache hits.",
        (1, 2, 5, 10, 20, 40, 80, 160, 300),
    ),
    # Issue #163's missing half. `nora_relevance_gate_errors_total{kind=
    # "timeout"}` says HOW OFTEN the ceiling fired; only a distribution says
    # whether the ceiling is the wrong number. The question that stalled — «is
    # nora-gate genuinely taking >20s on a 40-row transcript, or is the route
    # falling through a slow arm?» — is unanswerable from a counter, and 19.5%
    # of calls were arriving at it.
    #
    # The samples are **right-censored at `[relevance_gate] timeout_seconds`**:
    # a call cut at 20s may have wanted 45, and is recorded as 20. So this
    # measures how long the gate WAITS, not how long the model takes, and the
    # top bucket is the population `{kind="timeout"}` counts. Buckets are the
    # default set (to 30) because the ceiling is 20 and nothing above it can be
    # observed; widening them would draw buckets that can never fill.
    #
    # Observed on every exit including the failures, `nora_debrief_seconds`'s
    # rule: a latency series that records only successes describes the path
    # that already works.
    _HistogramSpec(
        "nora_relevance_gate_seconds",
        "One Relevance Gate model call's latency, on every exit including the "
        "fail-closed ones (design/dreamer-agent-loop/36, issue #163). "
        "RIGHT-CENSORED at [relevance_gate] timeout_seconds — a timed-out call "
        "is recorded at the ceiling, so read the top bucket against "
        "nora_relevance_gate_errors_total{kind=\"timeout\"}.",
    ),
)

# The full set of declared metric names, independent of kind — what the
# name-preservation test (tests/test_metrics_export.py) enumerates against.
DECLARED_NAMES: frozenset[str] = (
    frozenset(spec.name for spec in _COUNTERS)
    | frozenset(spec.name for spec in _GAUGES)
    | frozenset(spec.name for spec in _HISTOGRAMS)
)
