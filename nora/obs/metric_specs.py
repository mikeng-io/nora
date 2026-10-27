"""Every metric Nora declares — the names, labels and help text, nothing else.

WHAT is measured is a slow-moving registry the whole codebase reads; HOW it is
recorded is `nora.obs.metrics.Metrics`, one class with a prometheus-client
registry inside it.

**Metric names are append-only.** A rename breaks dashboards and alerts
silently, and `tests/test_metrics_export.py` enumerates `DECLARED_NAMES` to
enforce it. Add to the end of a tuple; never edit a name.
"""

from __future__ import annotations

from typing import NamedTuple

from nora.domain.assess import DQ_CHECKS
from nora.domain.cues import CUES
from nora.domain.culture import CHARACTER_KINDS, CHARACTER_OUTCOMES, CULTURE_OUTCOMES
from nora.domain.doctrine_check import CHECK_OUTCOMES
from nora.domain.lessons import LESSONS_OUTCOMES
from nora.domain.media import MEDIA_FETCH_CALLERS
from nora.domain.media_join import JOIN_PATHS
from nora.domain.memory_index import (
    EMBED_OUTCOMES,
    EMBEDDED_KINDS,
    NODE_KINDS,
    PROJECTION_OUTCOMES,
    SEARCH_STATUSES,
)
from nora.domain.otp import CHECK_OUTCOMES as OTP_CHECK_OUTCOMES
from nora.domain.otp import REQUEST_OUTCOMES as OTP_REQUEST_OUTCOMES
from nora.domain.profile import PROFILE_OUTCOMES
from nora.domain.reasoning_strip import (
    PATHS_HELP,
    STRIP_OUTCOMES,
    WRITE_OUTCOMES,
)
from nora.domain.social_stream import (
    BACKFILL_ENDS,
    BACKFILL_ITEM_OUTCOMES,
    BYTE_DELETION_REASONS,
    CUSTODY_OUTCOMES,
    PERCEPTION_OUTCOMES,
    PERCEPTION_SOURCES,
    POLL_OUTCOMES,
    POLL_STAGES,
    SKIP_REASONS,
)
from nora.domain.speech_markup import FORMAT_OUTCOMES
from nora.domain.stimuli import (
    LIMIT_STOP_REASONS,
    STIMULUS_KINDS,
    WAKE_BRAKES,
    WAKE_MODES,
    WAKE_REASONS,
)
from nora.domain.subagent import SUBAGENT_OUTCOMES
from nora.domain.summons import (
    FOLLOW_UP_OUTCOMES,
    INTAKE_OUTCOMES,
    SUMMONS_MODES,
    SUMMONS_OUTCOMES,
)
from nora.domain.video import CLIP_KEEP_OUTCOMES, VIDEO_OUTCOMES

_DEFAULT_BUCKETS: tuple[float, ...] = (0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30)


class _CounterSpec(NamedTuple):
    name: str
    help_text: str
    labelnames: tuple[str, ...] = ()


class _GaugeSpec(NamedTuple):
    name: str
    help_text: str
    labelnames: tuple[str, ...] = ()


#: DERIVED from `nora.domain.media_join.JOIN_PATHS`, never hand-copied: one
#: named constant the code increments against and the help text reads from
#: (CLAUDE.md's label-vocabulary rule), so the help text cannot name a different
#: set of paths than the series carries.
_JOIN_PATHS = " / ".join(JOIN_PATHS)

#: DERIVED for `_JOIN_PATHS`' reason. The set comes from
#: `nora.domain.audience.SPEAKING_AUDIENCES`, which is what the call sites pass.
_STRIP_PATHS = PATHS_HELP

_WRITE_OUTCOMES = " / ".join(WRITE_OUTCOMES)


class _HistogramSpec(NamedTuple):
    name: str
    help_text: str
    buckets: tuple[float, ...] = _DEFAULT_BUCKETS
    # The prometheus histogram carries only the labels declared here;
    # `Metrics.observe` records `**labels` in its test-readback dict regardless, so
    # this field is what makes a labelled histogram real.
    labelnames: tuple[str, ...] = ()


# Names are append-only (renames break dashboards).
# `platform` is on `nora_messages_total`, nora_tags_total, nora_replies_total,
# nora_silent_failures_total and nora_errors_total, so a stuck or failing
# platform is visible per series.
_COUNTERS: tuple[_CounterSpec, ...] = (
    # The OAuth keepalive (DEC-308, `nora.app.oauth_keepalive`). The alert reads
    # the timestamp gauges below, never these: a rare counter reads empty after a
    # restart.
    _CounterSpec(
        "nora_oauth_refresh_failures_total",
        "OAuth refresh attempts that failed, by lane (xai / openai_codex) and "
        "reason (auth: the issuer refused the refresh token, a new login is "
        "needed; state_unwritable; down; rate_limited; cloudflare). Counts the "
        "keeper's attempts and live calls' alike.",
        ("lane", "reason"),
    ),
    _CounterSpec(
        "nora_oauth_keepalive_restarts_total",
        "Times a lane's OAuth keepalive loop raised and was restarted by its "
        "supervisor. Any increment is a bug; the traceback is in the log.",
        ("lane",),
    ),
    # Music liveness. `verdict` is the decided answer, not a raw state: ok |
    # wedged | ended | paused | unknown. `unknown` is its own value and must never be
    # read as healthy: it means nora could not reach the controller at all.
    _CounterSpec(
        "nora_music_liveness_total",
        "Music liveness polls, by decided verdict "
        "(ok|wedged|ended|paused|unknown). A wedged verdict means nora "
        "believes a track is playing and the controller is provably "
        "producing no sound; unknown means she could not read it.",
        ("guild_id", "verdict"),
    ),
    _CounterSpec(
        "nora_music_stream_failed_total",
        "Streams that failed after nora believed they were playing, by "
        "reason. Carries the controller's own reason string, plus the "
        "liveness-* reasons the watch raises itself.",
        ("guild_id", "reason"),
    ),
    _CounterSpec(
        "nora_messages_total",
        "Ambient messages ingested (edits included), by platform. Reactions "
        "and deletes/unsends are NOT counted here: they are Record items, "
        "not messages, and are counted on nora_ingest_outcomes_total "
        "(reaction_* / delete_*).",
        ("platform",),
    ),
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
        "every call site names its own — but the stages the AMBIENT "
        "loop's memory reads raise are declared here by name, because they are "
        "the only way an operator can see a block go missing: "
        "stage=\"fire_people_in_window\" "
        "(one speaker's <people_in_window> read did, counted once per failed "
        "speaker), stage=\"fire_memory\" (the whole concurrent batch hit "
        "[honcho] read_timeout_seconds — a port that never answers, which is "
        "not the same failure as one that raises). Both are issue #210 "
        "and both are "
        "fail-soft: the block is lost, never the turn, so absence is the only "
        "symptom and this series is the only tell. The fire_* stages "
        "sit beside the pre-existing fire_peer_card / fire_standing / "
        "fire_notes / fire_unit for that set's own stated reason — an "
        "operator asking WHICH half of the context went blind must not have "
        "to guess. The <culture> read (DEC-207) is counted per occasion, one "
        "stage each, for the same reason: stage=\"tag_culture\", "
        "and stage=\"fire_culture\" — a room's account that exists and could "
        "not be read (a raise, or the bounded read timing out). A room never "
        "folded is not an error and moves nothing. The <self> read "
        "(design/self-growth/20) is stage=\"tag_self\", "
        "stage=\"fire_self\" and stage=\"cognition_self\", on the same terms. "
        "The <recalled> pre-fetch (design/unified-memory/00 step 3b) is "
        "stage=\"tag_prefetch\": a memory search that failed, timed out or "
        "could not be rendered, so the turn went on without the block.",
        ("stage", "platform"),
    ),
    _CounterSpec(
        "nora_silent_failures_total",
        "Failures kept silent from the group, by reason and by which path was "
        "speaking (audience=\"tag\" | \"heartbeat\" | \"participation\" | "
        "\"proactive\" — a kind=proactive row's act turn, design 39 D12 — | "
        "\"summons\" — a public Instagram summons, whose reply is held and "
        "published by its own flush, never a room adapter — | "
        "\"participation-shadow\" — the §10.6 mirror — | "
        "\"relevance-gate-shadow\" — the Relevance Gate's review post, which "
        "has no turn behind it at all — | \"one-time-code\" — a sign-in code "
        "sent in a direct message, a fixed template with no turn behind it. "
        "Both shadow audiences are "
        "operator-review traffic, never room speech, and a refusal under "
        "either means the reviewer is missing a determination they are "
        "tuning the flip on: read them against "
        "nora_errors_total{stage=\"participation_shadow\"} and "
        "{stage=\"relevance_gate_shadow\"}, which now move on a guard "
        "REFUSAL as well as on an exception).",
        # `audience` is the ONE label the heartbeat adds to an existing series,
        # rather than the fire path getting its own silent-failure series: `Deliverer`,
        # which the fire path must use as the only caller of `adapter.send`, increments
        # this on several branches of its own.
        #
        # A8 is `audience="tag"`-filtered (`docs/runbook.md`), so every call site must
        # pass the label: an unlabelled increment records `audience=""`
        # (`Metrics._full_labels`) and is excluded from that filter, most damagingly at
        # the `handler_error` site, which is R-5's last-resort signal.
        ("reason", "platform", "audience"),
    ),
    _CounterSpec("nora_guard_vetoes_total", "ReplyGuard vetoes, by rule.", ("rule",)),
    # A `computer_events` append that failed. The tool still answers (the audit
    # must not cost the turn), so this counter is the only place a missing audit row
    # shows up. Should be zero.
    _CounterSpec(
        "nora_computer_audit_failures_total",
        "computer_events audit writes that failed, by computer tool and stage "
        "(blob: a full input or output could not be put in the blob store, the "
        "row says so; row: the audit row itself was not appended; session: the "
        "call's computer session could not be continued or opened; lease: a "
        "lease's started row was not appended; export: a computer_export that "
        "reached its destination could not be recorded in computer_exports, so "
        "that link cannot be revised; media: a kept image could not be indexed in the "
        "media store, so it has no code). The tool call itself still answered.",
        ("tool", "stage"),
    ),
    # A `<computer>` block rendered from the database alone because the machine
    # read missed its budget (timeout) or the computer answered unavailable. The
    # reply was not held for it.
    _CounterSpec(
        "nora_computer_context_machine_unread_total",
        "<computer> blocks rendered without the live machine state, by reason "
        "(timeout: past [computer.context] read_timeout_seconds; unavailable: "
        "the computer answered unavailable).",
        ("reason",),
    ),
    # Every call `ComputerdRuntime` makes, by operation and the status it
    # answered (`nora.domain.computer.COMPUTER_OPS`, `SandboxStatus`).
    # Created at ZERO for every (op, status) when `[computer]` is on
    # (`nora.app.computer_metrics`), because `increase()` over a series whose
    # first sample is already 1 reads 0: a rare `unavailable` would otherwise be
    # invisible to the rule written for it. Carries `status`, so the generic
    # `UnavailableOutcomes` rule covers it too.
    _CounterSpec(
        "nora_computer_calls_total",
        "Calls to computerd by operation (run, read, write, edit, files, fetch, "
        "put, state) and the status computerd answered (ok, nothing_found, "
        "denied, unavailable). `unavailable` includes a call that got no answer "
        "at all; nora_computer_up says which.",
        ("op", "status"),
    ),
    _CounterSpec(
        "nora_computer_session_expiries_total",
        "Computer sessions the sweeper expired, by reason (idle, "
        "jobs_ended, running_past_limit: a job still running past "
        "max_running_days, flagged and not killed).",
        ("reason",),
    ),
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
    # Each doctrine, Dreamer, config and deploy load is recorded so "what was in
    # force at T" is answerable. A failed write loses that load's row and nothing
    # else (the process runs on); this is how the gap shows.
    _CounterSpec(
        "nora_revision_record_failures_total",
        "Revision writes that failed (kind: deploy, config, doctrine, dreamer, "
        "history_size); each is retried on the next 30s tick.",
        ("kind",),
    ),
    # The Dreamer's own instruction corpus (nora/dreamer/), counted separately
    # from the doctrine's: they are different files with different editors and
    # different blast radii, and one number covering both would make "did my
    # objective edit load" unanswerable from the metric.
    _CounterSpec(
        "nora_dreamer_instruction_reload_failures_total",
        "Dreamer instruction (doctrine + objectives) hot-reload failures.",
    ),
    # RETAINED frozen at 0 by the append-only metric-name policy
    # (PRESERVED_METRIC_NAMES): a dropped name breaks any dashboard summing it. It
    # counted SQLite WAL SQLITE_BUSY, which Postgres MVCC cannot produce, so nothing
    # increments it.
    _CounterSpec(
        "nora_store_busy_total",
        "SQLite-era store-contention counter (frozen; MVCC cannot produce it).",
    ),
    # An unmapped sender is an EXPECTED outcome (D6), so it is not an error, but
    # its symptom set is empty: the message is still audited, the gate still runs,
    # the prefill still shows the sender's platform display name, and nothing
    # reaches Honcho. Counting it separates "nobody talked to her" from "her
    # identity file is wrong, or was edited without a restart" (mapping is
    # startup-only, Q4).
    _CounterSpec(
        "nora_unmapped_senders_total",
        "Ambient messages whose sender resolved to no person.",
        ("platform",),
    ),
    # Deny-by-default has to be visible. A denied ROOM is refused before the
    # audit write (nothing is recorded); a denied SENDER is refused only at the
    # point of answering (the message IS recorded, which is what
    # `nora persons pending` reads).
    _CounterSpec(
        "nora_denied_conversations_total",
        "Ambient messages from a conversation absent from policy.toml.",
        ("platform",),
    ),
    # The ingest trace's replacement (R-76). An ingest contains no model call,
    # so its trace is plumbing and never reaches Langfuse; «how did this message
    # end» is a Prometheus question and this is where it is answered. The
    # `outcome` label's vocabulary is `nora.ingest.pipeline.IngestOutcome`,
    # derived there with `get_args` and checked at runtime before every
    # increment; deliberately NOT restated in this help text, because a
    # hand-copied vocabulary drifts.
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
    # decoration and every failure of one is swallowed so it can never touch the
    # reply, which is why they need a counter. One name, three labels, because the
    # questions are cross-cuts of the same series: `phase`
    # (ack/done/gave_up/clear) says WHICH indicator, `status`
    # (sent/failed/unsupported) says how it went. `unsupported` is not a failure:
    # it is the steady state of any platform without reactions, and summing it into
    # `failed` would make a correctly-degrading adapter look broken.
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
    # R-17. Nora's own-identity enrichment degraded. R-12 ("never treat my own
    # message as a tag") then collapses to a plain equality check against the phone
    # number, so a self-send arriving under her ACI would be treated as somebody
    # else's tag. `reason` is the useful cut: `unreachable` is an ops problem,
    # `no_self_contact_row` and `bad_payload` mean the daemon answered but not with
    # what we parse (a signal-cli upgrade breaking the shape).
    _CounterSpec(
        "nora_identity_enrichment_degraded_total",
        "Nora's own-identity derivation fell back to fewer id forms than asked "
        "for, by platform and reason.",
        ("platform", "reason"),
    ),
    # The three room-scoped tools (`group_members`, `lookup_person`, `about`),
    # by how they ANSWERED. `unavailable` means Nora could not read the group's
    # membership at all, and must never be summed with `not_found`: one says "I
    # could not look", the other "I looked and nobody here is called that".
    # `not_found` is deliberately identical for a person who is in `identity.toml`
    # but not in this room and for one who does not exist anywhere: distinguishing
    # them in the answer would disclose membership by behaviour. Alert on a
    # sustained `unavailable`; that is signal-cli, not the model.
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
    # Honcho's `representation` endpoint specifically, which cannot be trusted
    # to fail loudly: an unknown peer answers **200 with an empty string**, not 404,
    # so "asked and nothing known" (`empty`) and "could not ask" (`unavailable`) are
    # indistinguishable at the HTTP layer and only the call site knows which it got.
    # Alert on `ok == 0` while the series is being written at all.
    #
    # **The name keeps `recall` after the tool became `about` (DEC-39)**: metric
    # names are append-only, and renaming one starts a new empty series beside a
    # frozen one. The tool LABEL on `nora_room_lookups_total` does follow the tool
    # name, because it must agree with the name in `data/nora.log` for `_log_call`
    # to stay greppable.
    _CounterSpec(
        "nora_recall_total",
        "Durable-profile lookups against Honcho by the `about` tool, "
        "by outcome (ok / empty / unavailable). Named for that tool's former "
        "name, `recall`; metric names are append-only.",
        ("status",),
    ),
    # R-23. HOW the agent loop ended, every turn, straight off Strands'
    # `AgentResult.stop_reason`. The SDK reports a turn that ran out of budget by
    # returning normally (`_check_limits` yields an `EventLoopStopEvent` with
    # `stop_reason="limit_total_tokens"`): no exception, no log line. Without this
    # series a turn cut off by the token cap is byte-identical to a model that chose
    # not to call `send_reply`; both surface as
    # `nora_silent_failures_total{reason="no_send_call"}`.
    #
    # Recorded on EVERY completed invocation, not only the failing ones, so the
    # healthy shape is visible too: `end_turn` and `tool_use` are the normal values
    # and a series that only carried `limit_*` could not say what fraction of turns
    # are cut off. Alert on any sustained `limit_*`.
    #
    # `audience` is here for the reason `nora_silent_failures_total` has one: without
    # it an alarm on `{reason=~"limit_.*"}` cannot say whether a tag or a reminder
    # ran out of budget, and the two have different caps, timeouts and fixes. Names
    # are append-only; labels are not (see this module's header).
    _CounterSpec(
        "nora_llm_stop_reason_total",
        "How the agent loop terminated, by Strands stop_reason "
        "(end_turn / tool_use / limit_turns / limit_total_tokens / ...) and by "
        "which path was speaking (audience=\"tag\" | \"heartbeat\").",
        ("reason", "audience"),
    ),
    # The tools that have NO counter of their own: `now`, `load_transcript`,
    # `send_reply`, and `calculate` (DEC-35), whose two statuses are `ok` and
    # `refused` (a refusal counted as `ok` would make "is the model writing
    # expressions she can actually evaluate" unanswerable from /metrics). Same
    # `{tool, status}` shape as `nora_room_lookups_total`, and deliberately NOT a
    # superset of it: a second series counting the same three calls would double
    # every sum of tool volume. The two together are every tool call Nora makes;
    # neither alone is.
    #
    # Counters are the half of DEC-28 an operator can alert on; the log line in
    # `nora.agent.tools._log_call` is the half they can grep.
    _CounterSpec(
        "nora_tool_calls_total",
        "Native tool calls that have no counter of their own, by tool and how "
        "the tool answered (ok / not_found / nothing_known / unavailable / "
        "refused). The three room-scoped tools are counted by "
        "nora_room_lookups_total instead, never here.",
        ("tool", "status"),
    ),
    # DEC-34. An @-mention Nora could not put a name to. It renders `@someone`
    # (never the number, never the ACI), so the model's view is honest and
    # uninformative, and this counter is the ONLY place the loss is visible. It is
    # `nora_unmapped_senders_total`'s other half: that one says "somebody talked to
    # her and she does not know who", this one "somebody was TALKED ABOUT and she
    # does not know who". Both have the same fix and the same trap: `identity.toml`
    # is loaded once at startup, so editing it without a restart moves neither
    # number.
    _CounterSpec(
        "nora_unmapped_mentions_total",
        "@-mentions rendered as an anonymous handle because the mentioned "
        "account resolved to no person.",
        ("platform",),
    ),
    # DEC-42. A turn that sent SOME of its messages and then stopped for a
    # reason that was not "she was finished".
    #
    # Invisible to every other series: a turn cut off after message 2 of 4 is not
    # silent. `delivered` is True, `check_outcome` returns True, the group gets the
    # check mark, Honcho is fed, and `nora_silent_failures_total` never moves.
    # `nora_messages_per_tag` cannot answer it either: a 2 is a 2 whether she meant
    # to stop there or was stopped.
    #
    # `reason` reuses Strands' `limit_*` vocabulary where the loop reported one, so
    # it joins against `nora_llm_stop_reason_total`, and adds two of its own for
    # paths that produce no stop reason: `invocation_failed` (timeout or
    # `llm_error`; the invocation never returned) and `pipeline_error` (the outer
    # `BaseException`). Alert on any of it: every increment is a message the group
    # was meant to receive.
    _CounterSpec(
        "nora_partial_replies_total",
        "Tags that delivered at least one message and then stopped early, by "
        "why the turn ended (limit_turns / limit_total_tokens / "
        "limit_output_tokens / invocation_failed / pipeline_error).",
        ("reason", "platform"),
    ),
    # DEC-43. This is the counter that can measure absence: it increments on
    # every attachment SEEN, before any custody is attempted, so `0` means "none has
    # arrived" and a non-zero `label` cut says what kind did. Without it, a custody
    # counter at zero would be indistinguishable from custody being switched off,
    # uninstalled, or never called.
    _CounterSpec(
        "nora_media_attachments_total",
        "Attachments seen on ingested messages, by platform and the label they "
        "rendered as (image / cover image / sticker / voice note / video / audio / "
        "document / file).",
        ("platform", "label"),
    ),
    # What happened to the bytes. `stored` is the only outcome that produces a
    # handle in the transcript; every other value is a message recorded WITHOUT
    # custody, and each names a different thing to fix:
    #   not_configured - the composition root wired no blob store, so nothing was
    #                    attempted.
    #   no_id          - signal-cli described an attachment with no id to fetch.
    #   not_found      - the daemon has no such attachment; its own retention, or a
    #                    receive that never downloaded it.
    #   unreachable    - the daemon did not answer. Ops.
    #   bad_payload    - it answered in a shape this adapter does not read; a
    #                    signal-cli upgrade changing `getAttachment`'s result.
    #   too_large      - over the configured ceiling. A policy, not a failure.
    #   store_failed   - the disk refused. Full, read-only, or gone.
    _CounterSpec(
        "nora_media_custody_total",
        "Attachment byte-copies into Nora's own custody, by platform and "
        "outcome (stored / not_configured / no_id / not_found / unreachable / "
        "bad_payload / too_large / store_failed).",
        ("platform", "status"),    ),
    # The presence collector (DEC-54/DEC-55). A separate process
    # (`python -m nora.presence`) with its own metrics endpoint; these names are
    # declared in the shared registry so both the collector and Nora (which
    # increments only the disclosure counter) speak them from one source.
    # Append-only, like every name above.
    #
    # DISTINCT fixes WRITTEN. The collector is a change-recorder (writes only when
    # `ts` advances), so this counts DISTINCT positions, not polls. `wire_state` is
    # the state that fix carried (live / stale).
    _CounterSpec(
        "nora_presence_fixes_total",
        "Distinct location fixes written to presence_fixes, by circle and the "
        "wire_state the fix carried (live / stale). A change-recorder count: a "
        "still member does not increment it (design/19 §1).",
        ("circle", "wire_state"),
    ),
    # Every circle READ, by how it resolved. `unavailable` (transport / non-200)
    # must never be summed with `ok`; `banned` (a fresh token refused) and
    # `challenge` (a non-JSON body) are the fragile paths, held apart so a rising
    # `banned` is legible as a ban risk.
    _CounterSpec(
        "nora_presence_reads_total",
        "Life360 circle reads, by circle and outcome "
        "(ok / unavailable / challenge / banned).",
        ("circle", "status"),
    ),
    # The saved-places refresh: at most hourly per circle, riding the member
    # cycle. Counted separately from `nora_presence_reads_total` so a places-only
    # breakage (endpoint moved, shape changed) is visible on its own.
    _CounterSpec(
        "nora_presence_place_reads_total",
        "Life360 saved-place reads, by circle and outcome "
        "(ok / unavailable / challenge / banned).",
        ("circle", "status"),
    ),
    # Per member per cycle, so «sharing off» is a KNOWN STATE, not a gap. A member
    # who turned sharing off writes no fix row, so WITHOUT this series they would be
    # indistinguishable from a member the daemon failed to read, the ambiguity R-5
    # forbids for a location.
    _CounterSpec(
        "nora_presence_member_states_total",
        "Members classified per collection cycle, by wire_state "
        "(live / stale / sharing_off / disconnected / no_fix / unavailable).",
        ("wire_state",),
    ),
    # The Life360 client's auth alarms. `banned` = a fresh token was refused
    # (ban risk); `challenge` = a non-JSON body; `auth_403` = the token endpoint
    # itself refused the credential. Any sustained increment is an operator alert:
    # the client will not re-auth-loop, so this rising while reads fail is how a ban
    # surfaces instead of a silent 0-fix daemon.
    _CounterSpec(
        "nora_presence_auth_alarms_total",
        "Life360 auth/ban alarms from the client, by reason "
        "(banned / challenge / auth_403).",
        ("reason",),
    ),
    # The collector's own canary: a read whose body was not JSON, or a circle
    # that came back unexpectedly empty. Both signal app-update breakage: a required
    # UA/host/client-token changing turns 200s into challenge pages or empty
    # envelopes.
    _CounterSpec(
        "nora_presence_canary_total",
        "Presence collector canary trips, by reason (non_json / empty_circle).",
        ("reason",),
    ),
    # R-21 disclosure, incremented by NORA (not the collector): a `whereis` that
    # REFUSED because the conversation held someone outside the circle. A refusal is
    # fail-closed and loud: the group-not-subset-of-circle event made observable,
    # never a silent omission.
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
    # `nora_recovered_replies_total` (DEC-93): a reply the model WROTE without
    # calling `send_reply`, recovered and delivered rather than discarded. A
    # non-zero value means «the LLM route is not honouring `tool_choice=required`»,
    # not «this feature is working»; it is the cheapest signal that catches a
    # provider that accepts `required` and ignores it.
    _CounterSpec(
        "nora_recovered_replies_total",
        "Replies the model wrote as prose instead of calling send_reply, "
        "recovered and delivered (DEC-93). Non-zero means the route is not "
        "honouring tool_choice=required.",
        ("platform",),
    ),
    # Salvage-partial-reply: a `max_tokens` cut-off whose partial message carried
    # real prose, delivered rather than dropped. Distinct from
    # `nora_recovered_replies_total` (DEC-93): that one is a provider not honouring
    # `tool_choice=required`, this one is the model running out of output budget
    # mid-answer. A rising share here means the tier's `max_tokens` is too tight for
    # the questions being asked.
    _CounterSpec(
        "nora_salvaged_replies_total",
        "Partial replies cut off at max_tokens, salvaged and delivered "
        "(2026-09-03). Non-zero means the model hit its output ceiling "
        "mid-answer.",
        ("platform",),
    ),
    # Pure observability, no behavioural rule. ONE turn, ONE increment, for every
    # admitted tag `_run_admitted`'s `finally` reaches: the standing replacement for
    # a manual Langfuse `nora.outcome` query. The vocabulary is
    # `nora.domain.tag_trace.VALID_OUTCOMES` plus `"unattributed"` (see
    # `nora.engagement.pipeline._ENGAGEMENT_METRIC_OUTCOMES`), never restated here
    # (as for `nora_ingest_outcomes_total`). Deliberately NOT gated on the retention
    # store the way `nora_tagtrace_writes_total` is: this answers "what did the turn
    # end as", which must stay visible even if retention is unwired. `recovered` vs
    # `send_reply` is DEC-93's own split: a rising `recovered` share means the route
    # is not honouring `tool_choice=required`.
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
    # `nora_presence_disclosure_waived_total` is the WAIVER counter, the other
    # half of the pair above. When `location = true` overrides a guard, the override
    # is recorded; without it, relaxing `unnamed` would leave a zero refusal counter
    # unable to distinguish "the flag is working" from "the room filled up with
    # strangers". The startup pre-flight warns once at boot only, so for an account
    # that joins later this is the ONLY runtime signal.
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
    # The analytics rollup (DEC-115). A separate process
    # (`python -m nora.analytics`).
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
    # A rise is the tell that Life360's behaviour or the cadence changed; nothing
    # here should trend upward under a stable population.
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
    # A rise here is EXPECTED as the corpus grows. Unlike
    # nora_analytics_unlabeled_total, this is not a health signal; it is the anomaly
    # detector doing its job.
    _CounterSpec(
        "nora_analytics_anomalies_total",
        "Newly closed stays that are unusual against the subject's own history "
        "(duration/arrival/departure robust |z| above anomaly_z_threshold at "
        "that place, a never-visited place, or somewhere other than where the "
        "hour-of-week forecast put them), by subject and kind.",
        ("subject", "kind"),
    ),
    _CounterSpec(
        "nora_analytics_trips_total",
        "Trips written to presence_trips (Tier 5), by subject.",
        ("subject",),
    ),
    # `outcome` makes "no corridor has EVER been created" readable. A series
    # counting attach OPERATIONS alone could not tell "one corridor, reconfirmed
    # daily" from "corridors are being born". Counted once per DISTINCT corridor per
    # clustering pass, not once per trip attached to it.
    _CounterSpec(
        "nora_analytics_corridors_total",
        "Corridors touched by clustering (Tier 5), by subject and outcome "
        "(created = a brand-new corridor row; reconfirmed = an existing one "
        "gained samples).",
        ("subject", "outcome"),
    ),
    # Each Rollup stimulus the unit rollups tried to raise. `duplicate` is the
    # steady state for a co-location still starting on the next run (the stimulus
    # table keeps one per row); `unhealthy` is a deviation withheld because a source
    # it rests on was not proved healthy for its span (D3): a rise there is an
    # outage, not a quiet week; `capped` is a deviation past the per-person per-day
    # cap (D3, DEC-245).
    _CounterSpec(
        "nora_rollup_stimuli_total",
        "Rollup stimuli the unit rollups tried to raise, by kind "
        "(colocation_start, arrival, deviation) and outcome (raised, "
        "duplicate, unhealthy, capped).",
        ("kind", "outcome"),
    ),
    # The READ half's counter (DEC-56). `cached` is the axis that makes "MiMo is
    # never actually called" readable: a cache hit and a miss are the SAME
    # status=ok, and only this label tells them apart. Its own series rather than
    # `nora_tool_calls_total`, for the room-tools reason: a second series counting
    # the same calls would double any tool-volume sum.
    #   ok            - described (see `cached` for hit/miss).
    #   nothing_found - no such image in view here.
    #   ambiguous     - two in-scope blobs share the handle prefix, or a blob
    #                   and a shared file's link handle do.
    #   refused       - a shared file the source refused (not shared with her,
    #                   too large, download turned off).
    #   not_an_image  - the handle resolved to a blob NO describer reads (a
    #                   file), or a shared file that is not a picture or video.
    #                   Voice notes and videos are describable modalities (DEC-114),
    #                   so an unconfigured one is `unavailable`, not this.
    #   unavailable   - custody off, the blob was absent/corrupt, the modality
    #                   has no describer configured (`no_<modality>_describer`),
    #                   the payload could not be built for that modality, or the
    #                   model did not answer within the derived vision timeout.
    _CounterSpec(
        "nora_media_fetch_total",
        "media_fetch calls, by outcome (ok / nothing_found / ambiguous / "
        "refused / not_an_image / unavailable), whether the description was served "
        "from the content-hash cache (cached=true/false), and who asked "
        f"(caller={' / '.join(MEDIA_FETCH_CALLERS)}).",
        ("status", "cached", "caller"),
    ),
    # DEC-73. A person's alias appearing in a `web_search` query is COUNTED, not
    # blocked: unlike a routing origin, a search query has a benign reading, and a
    # guess encoded as a block fails closed on legitimate use. The query still
    # reaches Tavily; this counter is the only visibility into how often it happens.
    # Identifiers in a query ARE blocked, and are counted as an ordinary
    # `web_search`/`nothing_found` on `nora_tool_calls_total`, not here.
    _CounterSpec(
        "nora_search_alias_total",
        "Search queries containing a known person's name or alias, counted (not "
        "blocked) so the design/14 §2.2 count-vs-block call can be revisited "
        "against real queries.",
    ),
    # Reminders, the two halves. CREATED is the `remind` tool's own answer:
    # `status` separates an accepted row from each way the tool refuses one, which
    # is the only visibility into a cap that is set too tight.
    #
    # The help text lists ALL NINE values `nora.agent.tools._record_created` can
    # emit, not just the caps, so a status such as `fire_turn`, `unmapped`,
    # `unparseable`, `past` or `unavailable` is not mistaken for a typo.
    # `unavailable` means reminders are not wired at all: an outage rather than a
    # policy decision. Keep this list in step with `_record_created`'s call sites.
    _CounterSpec(
        "nora_reminders_created_total",
        "Reminder creation attempts, by status: created; the caps' refusals "
        "(too_soon / too_far / too_many); the turn-provenance refusal "
        "(fire_turn); the input refusals (unmapped / unparseable / past); "
        "screened, a proactive row whose text failed the Dreamer's "
        "identifier/coordinate screen (design/dreamer-agent-loop/39 D3); "
        "refused_origin_unresolvable / refused_origin_wrong_unit / "
        "refused_origin_not_proactive, a note-linked proactive row whose "
        "origin room cannot be resolved, drifted across a unit, or is "
        "not proactive-trusted — design/dreamer-agent-loop/40 (#248), where "
        "the row's destination is the note's own room, never a fallback to "
        "home; duplicate_note, a proactive row about a note that already has "
        "one waiting or firing (issue #158: one reminder per thing, held at "
        "the write and by uq_reminders_proactive_open_note); the "
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
    # group never sees: `failed_policy` (trust withdrawn between setting and firing;
    # R-5 keeps that silent in the room, so this is the only trace) and
    # `failed_error` (the fire turn raised, or a crash left the row FIRING and boot
    # recovery resolved it).
    _CounterSpec(
        "nora_reminders_fired_total",
        "Reminders resolved by the heartbeat, by outcome: the fire path's own "
        "status; failed_policy (trust withdrawn between setting and firing); "
        "failed_error (the fire turn raised, or a crash left the row FIRING); "
        "and the fire-time screens' outcomes. A kind=proactive row adds silent "
        "(DONE, the act turn declined) and cancelled_note_retired / "
        "cancelled_note_moved (design 39 D11/D12).",
        ("outcome",),
    ),
    # DEFERRALS: a tick that finds the conversation slot held leaves its rows
    # PENDING and moves on. Nothing fires, nothing fails, and the tick itself
    # completes, so `nora_reminders_fired_total` never moves and
    # `nora_heartbeat_last_tick_age_seconds` stays at zero. Counted ONCE PER
    # DEFERRED TURN, not per reminder and not per distinct row: the same rows are
    # deferred again on the next tick, so this is a measure of CONTENTION over time
    # (alert on a rate), never a count of how many reminders are late
    # (`nora reminders list` names the overdue rows).
    _CounterSpec(
        "nora_heartbeat_deferrals_total",
        "Fire turns the heartbeat deferred to a later tick because the "
        "conversation slot was held by a tag. One per deferred turn per tick, "
        "so a sustained rate means a group is starving its own reminders.",
    ),
    # `ComputeJobPoller`'s own delivery outcome, with the same `sent` /
    # `failed_policy` / `failed_timeout` / `failed_error` vocabulary `speak()`
    # returns that `nora_reminders_fired_total` uses for this label.
    # `failed_error` is also what `nora.app.compute_poller.recover` reports for a
    # `FIRING` row swept at boot (a crash mid-delivery).
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
        "nora_subagent_jobs_total",
        "Sub-agent worker jobs (design/subagent) by how they ended: done, cancelled, "
        "or failed for a reason (" + " / ".join(SUBAGENT_OUTCOMES[2:]) + "). The limit "
        "reasons are the loop's own bounds and timeout the wall clock; a rising rate "
        "of them is work that cannot finish inside its bounds (DEC-247, amended).",
        ("outcome",),
    ),
    _CounterSpec(
        "nora_video_jobs_total",
        "Video clips (`video_create`, design/video-generation) by outcome: at create "
        + " / ".join(VIDEO_OUTCOMES[:3]) + "; how a started clip ended "
        + " / ".join(VIDEO_OUTCOMES[3:]) + ". A terminal outcome may count again when "
        "delivery is deferred by a held room and the next tick polls the same clip.",
        ("outcome",),
    ),
    _CounterSpec(
        "nora_video_clip_keep_total",
        "Finished video clips fetched from the provider's expiring link and kept in Nora's "
        "own storage, by outcome: " + " / ".join(CLIP_KEEP_OUTCOMES) + ". retry leaves the "
        "row pending for the next tick; too_large, not_mp4 and gave_up deliver the link "
        "instead of the clip; unstored still attaches the clip but it is not in the library.",
        ("outcome",),
    ),
    _CounterSpec(
        "nora_subagent_checkpoint_unwritten_total",
        "Sub-agent checkpoint updates that found no pending job row to write to (design/subagent "
        "§4d): the row is written after launch, so an early one is expected; the terminal "
        "write of the delivery still stores the final checkpoint.",
        (),
    ),
    # Paired with `nora_loop_exhausted_fallbacks_total` below and read AGAINST
    # it: a healthy deployment moves THIS one and leaves that one flat.
    # `outcome="drafted"` is a cut-off turn that answered from what it had;
    # `outcome="empty"` is one where the drafting call itself returned nothing
    # (timeout, exception, or a model that said nothing) and the canned line went
    # out after all, so a rising `empty` rate is the drafting timeout being too
    # tight.
    #
    # `reason` carries the same three caps as the fallback counter so the two series
    # line up: `drafted{reason=limit_total_tokens}` and
    # `fallbacks{reason=limit_total_tokens}` sum to every turn that hit that cap
    # before replying.
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
    # The DELIVERY half of `nora_budget_drafts_total` above: that one counts what
    # the RUNNER produced, this one what the room actually READ. They differ
    # whenever `Deliverer` refuses (R-2/R-4/R-6/R-18). Same `reason` vocabulary as
    # its two neighbours so all three series line up on the cap that fired.
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
    # The OUTBOUND half of `nora_unmapped_mentions_total`: that one says
    # "somebody was talked about and she does not know who", this one "she tried to
    # tag somebody and could not".
    #
    # The four failures need four different fixes and look identical in the group
    # (an ordinary untagged message): `unresolved` = the name is not in
    # identity.toml, or its only id form is one signal-cli's RECIPIENT grammar would
    # not take (edit identity.toml AND RESTART; it is read once at startup, as for
    # `nora_unmapped_senders_total`); `not_in_room` = she named somebody who is not a
    # member of this conversation, usually correct behaviour and not a fault;
    # `roster_unavailable` = signal-cli could not be asked who is here, so nothing
    # was tagged (R-22: "I could not check" is never "they are here"), and a
    # sustained rate is a wedged or stale daemon; `ambiguous` = two people in
    # identity.toml answer to the same handle, so neither was tagged. `sent` is the
    # denominator that makes the others readable.
    _CounterSpec(
        "nora_outbound_mentions_total",
        "@-mentions Nora wrote in a reply, by outcome: sent; unresolved (no "
        "usable id in identity.toml); not_in_room (not a member of this "
        "conversation); roster_unavailable (the membership list could not be "
        "read, so nothing was tagged); ambiguous (two people answer to the "
        "handle).",
        ("outcome",),
    ),
    # Link previews. Three of the four values mean something different to an
    # operator. `attached` = a card went with the message; `unavailable` = the
    # fetch produced nothing (dead host, non-HTML, no og:title, a refused SSRF
    # check) and the message went out plain, which is the DESIGNED degradation and
    # only interesting as a rate; `blocked` = the fetched card carried an identifier
    # and was dropped at the egress screen, a real R-18 event that should be ~0. A
    # message with no link in it increments nothing: the series counts decisions,
    # not messages.
    _CounterSpec(
        "nora_link_previews_total",
        "Outbound link-preview cards, by outcome: attached; unavailable (the "
        "fetch yielded no usable card, so the message went out plain); blocked "
        "(the fetched title/description carried an identifier, R-18).",
        ("outcome",),
    ),
    # DEC-89. Answers: is the model quoting the links `place_info` mints, or is
    # the egress carry the only reason the group ever sees one? A reply that already
    # carried its link increments NOTHING (like `nora_link_previews_total`, this
    # counts DECISIONS, not messages), so a rising rate is the model dropping links
    # and a flat zero (with `place_info` calls happening) is the model behaving.
    # `outcome` is a label rather than a bare counter so a further value (a link
    # EARNED but suppressed) can be added without a rename; names are append-only.
    _CounterSpec(
        "nora_reply_links_carried_total",
        "Replies that left with a link the model had dropped, by outcome: "
        "appended (a place the reply named had a minted maps link the text did "
        "not carry), document_added (a document was published this turn and its "
        "link was added), stripped (the reply carried a link to one of our "
        "documents that no tool had handed her), media_attached (a message reached "
        "the room carrying media code attached for the turn: a finished video clip).",
        ("outcome",),
    ),
    # DEC-105. `ok` is a message that reached another room; every other value is
    # a refusal, and WHICH one matters: `other_unit` and `not_allowed_there` mean
    # somebody tried to speak past a trust boundary, and are worth watching in a way
    # that `unknown_room` (a model guessing at a name) is not.
    # Somebody was told «I'm working on it» and the job was then lost across a
    # restart. This counts the CLOSING of that promise, by outcome: `sent` is the
    # only value that means the person waiting actually heard back, and a rising
    # `busy`/`failed_error` means they did not.
    _CounterSpec(
        "nora_compute_lost_told_total",
        "Lost-compute-job notices by outcome: sent / busy / failed_* .",
        ("outcome",),
    ),
    _CounterSpec(
        "nora_relay_total",
        "Cross-room relays by outcome: ok / empty / unmapped / unknown_room / "
        "ambiguous_room / unknown_source / same_room / other_unit / "
        "not_allowed_there / egress_blocked / no_adapter / failed.",
        ("outcome",),
    ),
    # What makes the dedup ladder's hit rate an OBSERVATION rather than a
    # belief. `status` is `ok` when the sticker branch produced a description, else
    # the `StickerOutcome.reason` that stopped it (unavailable / store_failed /
    # blob_missing / vision / error / not_found / no_id / too_large / unreachable).
    # `level` is `StickerReader`'s own ladder-level label: identity / content /
    # none.
    _CounterSpec(
        "nora_sticker_descriptions_total",
        "Sticker-branch CUSTODY outcomes in the media stage, by platform, "
        "status (ok = the bytes are held / the StickerOutcome reason), and "
        "which ladder level answered — level=identity/content/none. Since "
        "issue #116 the description itself is nora_media_comprehension_total's; "
        "this series is the identity ladder's hit rate.",
        ("platform", "status", "level"),
    ),
    # Media comprehension. The SPAWNED read: one model pass per medium into
    # title/description/content. R-27's three states and nothing else:
    #   ok            - a comprehension was written for these bytes.
    #   nothing_found - a final answer ABOUT the bytes: `unreadable` (a PDF, a
    #                   lottie sticker; nothing here will ever read it) or
    #                   `already_read` (this sha256 was comprehended before; a
    #                   description is a pure function of the bytes). Neither is a
    #                   failure and neither is an `ok`: counting a cache hit as ok
    #                   would make the ok rate say the describer is working when it
    #                   may not have run for days.
    #   unavailable   - every wiring gap and every failure: no describer configured
    #                   for that modality (`no_<top>_describer`), the blob could not
    #                   be opened (`blob`), the model did not answer
    #                   (`vision`/`timeout`/`empty`), ffmpeg refused a video
    #                   (`ffmpeg`), or the job raised (`error`). NEVER
    #                   nothing_found: «I could not look» is not «there was nothing
    #                   there».
    # R-27 on the join itself. `ok` rendered at least one detail; `nothing_found`
    # read the index and nothing in the window had been comprehended yet, a real
    # answer about those bytes; `unavailable` is the index refusing to be read
    # (`MediaObjectStore.get` answers `None` for a miss and for an outage alike,
    # which is why `lookup` exists).
    #
    # Container logs are not on the docker log driver, so without this a
    # permanently failing join would be silent on the deployment.
    #
    # `reason="capped"` on an `ok` is also worth seeing: the window held more
    # distinct blobs than `DEFAULT_MAX_BLOBS`, so some rendered bare.
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
    # The speech (STT) lane. Voice-note transcription outcomes in the media
    # custody stage, by platform and status (ok / error / timeout / unsupported /
    # empty). `empty` is a legitimate 200 with no words (a silent/wordless voice
    # note), kept distinct from `error` (a transport/server failure) so an operator
    # can tell an outage from an ordinary silent note. An ERROR is never a PASS: a
    # failed transcription is counted here with its reason and the voice note is
    # still recorded with a bare handle.
    _CounterSpec(
        "nora_speech_transcriptions_total",
        "Voice-note transcription outcomes in the media custody stage, by "
        "platform and status (ok / error / timeout / unsupported / empty).",
        ("platform", "status"),
    ),
    # `nora_dream_batch_shrunk_total`: `unavailable` holds the watermark on purpose,
    # so the next fire re-reads the IDENTICAL range, and for a failure caused BY that
    # range the retry fails the same way, forever, paced only by the backoff cap.
    # `nora_dream_run_total{status="unavailable"}` cannot separate that from a
    # provider outage. This series says «something about THIS range does not fit»,
    # and a unit healing itself still shows up on it.
    _CounterSpec(
        "nora_dream_batch_shrunk_total",
        "Dream cycles that read FEWER rows than their configured batch "
        "because the previous attempt on this (adaptor, key) reported "
        "unavailable. Rising means a unit is retrying with a smaller batch "
        "rather than re-sending the range that just failed; sustained rising "
        "on one adaptor means its batch or its per-call ceiling is mis-sized, "
        "not that the provider is down.",
        ("adaptor",),
    ),
    # `nora_dream_skipped_total` `reason="in_flight"`: dream_loop's per-scope
    # lock. A slow extraction still running when the next tick comes round is
    # SKIPPED, never re-entered, because re-entering would read the same un-advanced
    # watermark and append the same evidence twice, and the rendered "12x, 3 people"
    # would be a fabricated number.
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
    # `dream_loop`: a watermark below the oldest SURVIVING message means rows were
    # pruned before this consolidator ever read them. R-27's `unavailable` ("I could
    # not see those"), never `nothing_found` ("nothing happened"). Labelled by scope
    # so a specific room's loss is findable; structurally zero while
    # `store.prune_enabled` is false.
    _CounterSpec(
        "nora_dream_evidence_lost_total",
        "Dream-loop scopes where messages were pruned before this "
        "consolidator ever read them, by scope.",
        ("scope",),
    ),
    # The Dreamer runtime. R-27's three states, at the run. A run that FAILED is
    # `unavailable` and is counted, never silently absent: the Dreamer has nobody
    # waiting on it, and an absent metric and a healthy-but-idle loop look identical
    # from outside.
    _CounterSpec(
        "nora_dream_run_total",
        "Dreamer adaptor runs by outcome: ok (wrote something), "
        "nothing_found (looked, found nothing), unavailable (could not "
        "check — an ERROR is never a PASS).",
        ("adaptor", "status"),
    ),
    # Per-fire sampling (AdaptorSchedule.samples): one run is N model calls
    # unioned, and a run's `unavailable` means «no sample was readable». This is the
    # per-sample reading, so an operator can tell «one of five samples timed out»
    # from «the run failed».
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
    # The fail-closed branch in `DreamerToolSource.build`: the adaptor's grant
    # names a home room, the unit declares none in policy.toml, and the acts are
    # withheld along with the room. This is a CONFIG state, true on every pass until
    # a file is edited, so the log line beside it is emitted once per (adaptor,
    # unit); this counter carries the signal that deduplicating the log removes,
    # «an adaptor has been inert in this unit for a week».
    _CounterSpec(
        "nora_dream_home_room_missing_total",
        "Dreamer builds that wanted a home room and found none declared for "
        "the unit: the adaptor ran with no acts and no room. A standing "
        "config gap, counted every pass; the log says it once.",
        ("adaptor", "unit"),
    ),
    # `CultureAdaptor` calls an injected `resolve_names`
    # (`Persons.name_transcript`) before rendering a sender's name, and a raising
    # resolver must not take the whole occasion down with it: the occasion still
    # renders, on the `speaker_name` floor (the profile name, or "unknown"), rather
    # than being marked `unavailable` and skipped. This is how that degraded read is
    # observed. It should be structurally zero, since `Persons.name_transcript` does
    # dict lookups only; a non-zero reading means the injected resolver itself is
    # failing.
    _CounterSpec(
        "nora_dream_name_resolution_failed_total",
        "Batches or evidence rows a Dreamer adaptor rendered on the "
        "pre-resolution speaker_name floor because the injected "
        "resolve_names call raised, by adaptor. Structurally zero; a "
        "non-zero reading is the resolver itself breaking, not a room with "
        "no mapped senders (which costs nothing here — resolution is only "
        "attempted, never required).",
        ("adaptor",),
    ),
    # Screening is the second path into the prompt, and it must never be silent:
    # a proposal dropped for carrying a phone number or a coordinate is a real event
    # about a model reading attacker-influenceable pages, and a drop nobody counts is
    # indistinguishable from a model that never produced one.
    _CounterSpec(
        "nora_dream_screened_total",
        "Envelope items dropped before reaching a sink, by adaptor and "
        "reason: identifier or coordinate (the R-18/R-28 screen), or cap "
        "(the per-run item ceiling — a silent cap reads as 'we wrote "
        "everything it found').",
        ("adaptor", "reason"),
    ),
    # An adaptor whose objective asks the model to echo a row id back has EVERY
    # item dropped by `dream_screen` (`_UUID` matches any hyphenated uuid): the tick
    # spends a model call, writes nothing, and reports `ok`. The CI half of this
    # check is `tests/contract/test_dream_adaptor_contract.py`.
    # `nora_dream_screened_total` counts the drops per item and cannot answer «was
    # that all of them», because a run's item count is not on that series; this
    # series can. Structurally zero while every adaptor keeps identifiers out of its
    # envelope, which is what makes a non-zero reading worth an alert.
    _CounterSpec(
        "nora_dream_empty_after_screen_total",
        "Dreamer runs where the model returned items and the R-18/R-28 "
        "screen kept none, by adaptor: a model call spent, nothing written, "
        "and the run still `ok` (R-27 — «there was nothing» is a claim a run "
        "whose model DID answer may not make). Non-zero means an adaptor is "
        "putting an identifier or a coordinate in its envelope.",
        ("adaptor",),
    ),
    # A store call inside `adaptor.inputs` or `adaptor.commit` that never returns
    # would leave `dream_loop` holding `<adaptor>:<key>` in `running` forever; the
    # timeout releases the unit and lands HERE. That is a different diagnosis from
    # the `unavailable` a 429 or a prose reply produces, and from
    # `nora_dream_skipped_total{reason="in_flight_adaptor"}`, which cannot separate a
    # long run from a wedged one.
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
    # Strands raises nothing and logs nothing when a budget cap binds; the
    # `AgentResult.stop_reason` string is the only channel, so a runaway loop
    # spending its full ceiling every tick is invisible without this.
    _CounterSpec(
        "nora_dream_stop_reason_total",
        "How a Dreamer loop ended, by adaptor: end_turn, limit_turns, "
        "limit_total_tokens, limit_output_tokens, timeout.",
        ("adaptor", "reason"),
    ),
    # TWO findings on one series, separated by `rule`
    # (`nora.agent.dream_guard.GuardRule`); read them separately.
    #
    #   rule="R-7"           - the model asked for a tool its adaptor never granted.
    #                          Under a read-only ceiling that is not a leak, but it
    #                          is the tell that an objective and its tool subset
    #                          disagree about what the job needs. A CONFIG fault: it
    #                          should be near zero, and a steady rate is something
    #                          to fix.
    #   rule="tool-budget"   - the model spent one tool's own per-pass ceiling and
    #                          called it again. Split off `R-7` so it can answer
    #                          whether a ceiling is binding. This is the instrument
    #                          for `[dreamer.budgets.<adaptor>] tool_calls`: a
    #                          sustained rate on a READ verb says the number is too
    #                          low for the work, the same reading `memory-budget`
    #                          carries below.
    #   rule="memory-budget" - the pass spent its whole memory ration and asked again
    #                          (R-91). Ordinary model behaviour that the ceiling
    #                          absorbs, not a fault; the pass is answered
    #                          `unavailable` and carries on. A NON-ZERO rate is the
    #                          ceiling doing its job. What is worth an alert is the
    #                          pathological arm, a pass that spends its whole `turns`
    #                          budget on refusals: this series climbing alongside
    #                          `nora_dream_stop_reason_total{reason="limit_turns"}`
    #                          and a `run_total` `unavailable` for the same adaptor.
    #   rule="pass-budget"   - the pass made its whole `max_tool_calls` over every
    #                          verb together and asked again. `memory-budget`'s
    #                          reading, one ration wider: a sustained rate says the
    #                          cap binds.
    _CounterSpec(
        "nora_dream_guard_vetoes_total",
        "Tool calls refused by DreamGuard, by adaptor and rule: R-7 is a tool "
        "never granted, tool-budget is that tool's own per-pass ceiling spent, "
        "memory-budget is the pass's memory ration spent, pass-budget is the "
        "pass's whole tool-call ration spent.",
        ("adaptor", "rule"),
    ),
    _CounterSpec(
        "nora_tagtrace_truncations_total",
        "TagTrace content dropped or cut before retention, by reason.",
        ("what",),
    ),
    # Written-versus-turns-taken for the tag-trace record. The denominator is
    # `nora_tag_latency_seconds`' own COUNT: that histogram observes exactly once per
    # admitted tag, on every exit path, so
    # `nora_tagtrace_writes_total / nora_tag_latency_seconds_count` is the ratio.
    # `tag_trace` is written in a `finally` on every exit path too, so the expected
    # value is 1.0 and any sustained departure is a defect, not a judgement call. A
    # recording layer that silently stops writing leaves traces that cannot be
    # reconstructed later.
    #
    # The `outcome` label is a second reading off a column that is already written,
    # and the only way to see whether R-5 silences are being retained (and therefore
    # reviewed) at all.
    _CounterSpec(
        "nora_tagtrace_writes_total",
        "TagTrace rows handed to the retention store, by turn outcome. The "
        "denominator is nora_tag_latency_seconds_count (one per admitted "
        "tag); the expected ratio is 1.0.",
        ("outcome",),
    ),
    # Every debrief lands on exactly one label, including the ones that wrote
    # nothing:
    #   written           - the position was revised
    #   refused_overflow  - it would not fit even after consolidating; the prior
    #                       stands (refuse, never truncate)
    #   screened          - an identifier or coordinate reached the fold
    #   malformed         - the model answered, in the wrong shape
    #   unavailable       - the prior could not be read, or the call was cut off at
    #                       the output ceiling. «I could not check», never folded in
    #                       with `malformed` (R-27)
    #   timeout           - the fold did not finish inside its own budget
    #   cancelled         - the process is going away mid-fold. NOT a fault, and
    #                       deliberately not `error`: shutdown cancels an in-flight
    #                       tag routinely, and a counter that fires on ordinary
    #                       shutdown is one nobody reads
    #   error             - anything else, genuinely unexpected
    _CounterSpec(
        "nora_debrief_total",
        "Debrief folds by disposition: written, refused_overflow, screened, "
        "malformed, unavailable, timeout, cancelled, error.",
        ("outcome",),
    ),
    # Makes the "fold reads the prior Standing" requirement OBSERVABLE. A fold
    # that read nothing leaves two contradictory positions coexisting, neither able
    # to correct the other. `prior="absent"` is legitimate exactly once per
    # conversation (the first fold); a sustained absent rate is the tell that the
    # read is broken.
    _CounterSpec(
        "nora_debrief_prior_total",
        "Debrief folds by whether the current Standing was read first "
        "(prior=read|absent). A sustained absent rate is a defect.",
        ("prior",),
    ),
    # The refuse-and-consolidate path firing is HEALTHY. Never firing means the
    # cap is not binding (the tier is meant to be lossy); firing constantly means it
    # is too small. Both are readings, which is why this is a counter and not a log
    # line.
    #
    # `stage="first"` is the refusal the model then FIXED; `stage="final"` is the one
    # that wrote nothing. A high `first` with a low `final` is the mechanism working.
    #
    # `nora.engagement.debrief_render._clip` cuts one block at that module's bound
    # (600, or 4000 for a tool result or a thinking step) before the fold sees it;
    # this counts how often, and which kind of block.
    #
    # `block` is `ClipKind` (`nora.engagement.debrief_render`):
    #   reasoning   - the model's own narration was cut (at 600)
    #   thinking    - a step of private reasoning was cut (at 4000); its own label so
    #                 the two bounds read apart
    #   tool_call   - the model's own request to a tool was cut (what she asked, not
    #                 what she was told)
    #   tool_result - what a tool RETURNED was cut: the evidence half. A claim
    #                 supported by the tail of a long tool result reads as
    #                 unsupported to a grader shown only the head
    _CounterSpec(
        "nora_debrief_clips_total",
        "Blocks the debrief render cut at its bound before the fold saw "
        "them, by which kind of block (reasoning = her own narration / "
        "thinking / tool_call / tool_result).",
        ("block",),
    ),
    # A declared metric with no call site renders a series that is always zero,
    # which reads as «never fired» rather than «no longer exists», so retired
    # Standing series are not declared here.
    _CounterSpec(
        "nora_standing_overflow_refusals_total",
        "Standing writes refused for exceeding [standing] max_chars, by "
        "stage: first (the model was asked to consolidate) or final (nothing "
        "was written and the prior position stands). (stage=rollup, the "
        "event-path fold's, was retired 2026-10-03, DEC-277.)",
        ("stage",),
    ),
    # A dropped item never reaches `screen` and is never a candidate for the
    # overflow refusal below.
    _CounterSpec(
        "nora_standing_working_set_unsourced_dropped_total",
        "Working-set items the fold's write path dropped because their "
        "`source` did not name a step this turn's <steps> actually showed, "
        "EXACTLY (a carried-forward item, unchanged from its prior, is not "
        "re-checked). A rising series says the objective or the model is "
        "naming sources that are not exact tool names — a description or a "
        "paraphrase, not the tool itself.",
    ),
    _CounterSpec(
        "nora_standing_working_set_expired_dropped_total",
        "Working-set items the fold's write path dropped because they were "
        "a carry-forward past [standing]'s working-set TTL — the model "
        "re-stated an item whose found_at is too old to keep, and the write "
        "path refused it deterministically rather than writing it back and "
        "resetting nothing. Distinct from the render-time TTL filter "
        "(render_standing_working_set): this is the same rule enforced "
        "where the row is WRITTEN, not just where it is read.",
    ),
    _CounterSpec(
        "nora_standing_working_set_overflow_refusals_total",
        "Working-set writes refused for exceeding [standing] "
        "working_set_max_items or working_set_max_chars. The whole new set "
        "is refused and the PRIOR working set is kept — never a silent "
        "truncation, no consolidate retry (unlike the body's own overflow).",
    ),
    _CounterSpec(
        "nora_note_update_refused_total",
        "note_update calls refused, by reason: not_read (the R-55 latch — the "
        "model had not read a note by that title this turn), title_moved (the "
        "title resolved to a different note than the one read), or stale_head "
        "(the compare-and-set lost; the note moved under the call).",
        ("reason",),
    ),
    # Separates «her card is genuinely empty» from «the read failed»
    # (`nora_errors_total{stage="peer_card"}`). Every name incremented in
    # `engagement/context.py` must be declared here: `Metrics.inc` falls through its
    # counter branch to the bare `self._gauges[name]` lookup and raises `KeyError`
    # for an undeclared name, which `_peer_card`'s own `except Exception` would
    # count as a read failure.
    # `tests/architecture/test_every_recorded_metric_is_declared.py` checks call
    # sites against this tuple.
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
    # `nora.domain.bounds.BoundSite` and is deliberately NOT restated here (as for
    # `nora_ingest_outcomes_total`).
    #
    # It counts the BIT, not the remainder: one increment per truncated read, never
    # "how many were left". Over-fetching by one is the only probe that is cheap and
    # correct at every site, so a series carrying a magnitude from the sites that can
    # compute one and a 1 from the sites that cannot would be lying about its own
    # unit. Sites with a real depth to report keep their own gauges; this does not
    # replace `nora_note_revisit_deferred_total`.
    #
    # Read it AGAINST the call rate for the same site. Sustained truncation on every
    # call is a backlog this series cannot size (see `nora.domain.bounds`' reversal
    # condition).
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
    # The participation occasion. Silence is the HEALTHY outcome of a
    # participation turn (the model was offered the floor and declined), so it gets
    # its own series and NEVER lands in `nora_silent_failures_total`, whose every
    # reason is a malfunction. The gate is
    # `ok=True and stop_reason not in BUDGET_STOP_REASONS`: a turn the budget cut off
    # before it could call the send tool is a malfunction of the ordinary kind and
    # goes to `nora_silent_failures_total` with audience="participation", never
    # here.
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
    # The proactive occasion (DEC-162), participation's pair: a
    # `kind="proactive"` row's act turn that concluded «say nothing» (the healthy
    # default; the row is DONE with outcome="silent") and one that spoke. Budget-cut
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
    # The Relevance Gate has TWO series, and the split is the point: this one
    # moves only when the gate actually decided, the one below only when it could
    # not. Both outcomes are a `no_go` at the trigger, and folding them together
    # would make «the room was quiet» and «the gate is down» the same reading (R-23).
    #
    # NEVER counted in any tag, heartbeat or participation series: the gate decides
    # whether a turn happens and is not itself a turn, so a gate verdict landing in
    # `nora_engagement_outcomes_total` or `nora_participation_silence_total` would
    # corrupt the measurement the flip decision reads.
    #
    # `nora_reaction_total`: one per TAG turn that reached `send_reply` with a picker
    # wired (`nora.agent.reaction`). `with`/`alone` are GIFs that went out with the
    # reply; `none` is the model deciding against one; the rest are the ways a pick
    # fails, every one of which sends the reply as written. The GIF rate is
    # (with+alone)/all.
    _CounterSpec(
        "nora_reaction_total",
        "Reaction-picker outcomes on a tag turn's first reply: with, alone "
        "(a GIF judged, picked AND downloaded), none, truncated, bad_query, "
        "no_candidates, no_pick, no_bytes, no_instruction, timeout, error. "
        "Every outcome but with/alone sends her words unchanged.",
        ("outcome",),
    ),
    # The speech formatter. The vocabulary is
    # `nora.domain.speech_markup.FORMAT_OUTCOMES`, derived from the Literal the
    # formatter counts against. formatted/(every outcome but short and long) is how
    # often the model's pacing is actually heard; words_changed and bad_markup are
    # the check refusing it.
    _CounterSpec(
        "nora_speech_format_total",
        "Speech-formatter outcomes on a spoken reply: "
        + " / ".join(FORMAT_OUTCOMES)
        + ". Only `formatted` speaks the model's text; every other outcome "
        "speaks her reply normalised, and none drops it.",
        ("outcome",),
    ),
    _CounterSpec(
        "nora_relevance_gate_total",
        "Relevance Gate verdicts the MODEL returned, by verdict and platform "
        "(design/dreamer-agent-loop/36). A fail-closed no_go is NOT here — it "
        "is on nora_relevance_gate_errors_total — so go/(go+no_go) here is a "
        "true admission rate and not a rate diluted by outages. In shadow "
        "mode only the go verdicts are delivered anywhere; this series is the "
        "only place the no_go rate is observable at all, which is what keeps "
        "an undelivered verdict from being an unmeasured one.",
        # `conversation_id` makes a REFUSAL JOINABLE TO A ROOM. Without it a `no_go`
        # says only «somewhere on Signal», and the two measures that grade the gate
        # against something other than its own opinion, HOLDING refusal (it refused room
        # R while she demonstrably held something for R) and override (a human tagged her
        # in R within minutes of a `no_go`), are uncomputable. The gate has the id in
        # hand at every emit site.
        #
        # Cardinality is bounded by `Policy.conversations`, a configured roster and not
        # user input, the same population every per-room store keys on. It does NOT touch
        # DEC-179: the gate neither reads nor writes `decision_log`, and a counter cannot
        # give per-verdict timing, so `override` stays approximate until something
        # durable carries it.
        ("verdict", "platform", "conversation_id"),
    ),
    _CounterSpec(
        "nora_relevance_gate_errors_total",
        "Gate calls that produced no usable verdict and therefore FAILED "
        "CLOSED to no_go, by kind, platform and conversation. The vocabulary "
        "is `nora.ingest.relevance_gate.GATE_ERROR_KINDS` and is deliberately "
        "not restated here — a hand-copied vocabulary is what drifts. A "
        "SUSTAINED count here is the gate being down while looking exactly "
        "like a quiet room, which is the one failure this series exists to "
        "make visible.",
        # Labelled for the same reason as the verdict series above: a fail-closed
        # no_go REFUSES THE ROOM just as a chosen one does, so a HOLDING measure blind to
        # this series under-counts refusals by the outage rate. The two series stay
        # SEPARATE (R-23); giving both the same join key does not weaken that.
        ("kind", "platform", "conversation_id"),
    ),
    # The culture tier's only counter. The vocabulary is
    # `nora.language.culture.CULTURE_OUTCOMES` and is deliberately not restated here.
    #
    # `unchanged` is the one worth reading and the one that looks like a failure: the
    # adaptor declines to append an account byte-identical to the head, because each
    # link in the chain must differ from the one before. A sustained run of it is a
    # room that has stopped changing, a real reading and not an error.
    #
    # The outcomes are about what a session did with its room: `refused` counts one
    # refused `character_revise` call, `unsettled` a session that ended with the room
    # neither revised nor kept (its batch is held), `kept` a «no change» close.
    _CounterSpec(
        "nora_culture_fold_total",
        "Culture folds by what the session did with the room's account, by "
        "outcome (" + " / ".join(sorted(CULTURE_OUTCOMES))
        + "). refused: one write was refused with a reason the model read. "
        "unsettled: the session ended with the room neither revised nor kept, "
        "and its batch is held. abandoned: the same batch was unsettled three "
        "times in a row and the fold advanced past it.",
        ("outcome",),
    ),
    # The Character sessions that are not a room: one per (unit, entity) key, by
    # entity kind and by the same outcomes.
    _CounterSpec(
        "nora_character_session_total",
        "Character sessions (person, unit, Nora) by kind ("
        + " / ".join(sorted(CHARACTER_KINDS)) + ") and outcome ("
        + " / ".join(sorted(CHARACTER_OUTCOMES)) + ").",
        ("kind", "outcome"),
    ),
    # The Lessons session: one per unit, by what it did; `decayed` is code
    # retiring a lesson at its horizon.
    # The Profile sessions, in shadow (design/unified-memory/45 §4).
    _CounterSpec(
        "nora_profile_session_total",
        "Profile sessions by outcome (" + " / ".join(sorted(PROFILE_OUTCOMES)) + "). "
        "written a session's one write landed rows; kept it settled with none; "
        "refused one verb call was refused; unsettled a session that did neither "
        "(its batch is held); abandoned the third unsettled run of one batch.",
        ("outcome",),
    ),
    # The Profile verdict judge (45 §3): one per hypothesis judged, by verdict
    # and by judge (code for a countable observable, the model otherwise).
    # `unknown` is never confirmed's side: it is the observable unread (R-27).
    _CounterSpec(
        "nora_profile_verdict_total",
        "Hypothesis verdicts by verdict (confirmed / refuted / expired / "
        "unknown) and judge (code / model).",
        ("verdict", "judge"),
    ),
    _CounterSpec(
        "nora_lessons_session_total",
        "Lessons sessions and their writes by outcome ("
        + " / ".join(sorted(LESSONS_OUTCOMES)) + "). written / retired count "
        "verb calls that landed; kept a session closed unchanged; unsettled a "
        "session that did none (its batch is held); abandoned the third "
        "unsettled run of one batch; decayed a lesson retired at its horizon.",
        ("outcome",),
    ),
    # The doctrine check and the tension rows it writes. An `error` refused the
    # lesson and wrote nothing.
    _CounterSpec(
        "nora_doctrine_check_total",
        "Doctrine checks of a lesson by outcome (" + " / ".join(sorted(CHECK_OUTCOMES))
        + "). conflict wrote a tension row; error refused the lesson and wrote "
        "nothing (an instrument error is never a pass).",
        ("outcome",),
    ),
    _CounterSpec(
        "nora_lessons_correction_wake_total",
        "Lessons sessions woken by the correction fast path (L4): a message "
        "that spoke to her and read as an instruction about how she acts.",
        (),
    ),
    _CounterSpec(
        "nora_self_tension_total",
        "Tension rows: a lesson refused because it would loosen or contradict a "
        "law rule, by the doctrine file of that rule. Operator-only.",
        ("doctrine_file",),
    ),
    # The OmniRoute Chat Completions transport drops `reasoningContent` in a
    # multi-turn position; it does not raise, the loop runs on and the turn speaks
    # with its reasoning gone.
    #
    # **THE DENOMINATOR IS IN THE SAME SERIES.** A counter carrying only the strips
    # cannot separate «no strips» from «no observation»: both read as an absent
    # series, and this process restarts two or three times a day with
    # prometheus-client creating label values lazily. Recording `clean` as well means
    # an absent series says «nothing multi-turn has run since the last restart» and a
    # present `clean` with no `stripped` says a real zero.
    #
    # It is NOT the record: this resets on every deploy, and the question it answers
    # («how often, per opportunity, on which path, and did it move when the model
    # did») spans deploys. `reasoning_strip_log` in Postgres is the record; see
    # docs/adr/0013-the-reasoning-strip-is-counted-in-postgres.md.
    _CounterSpec(
        "nora_reasoning_strip_total",
        "CONTINUATION model calls (a call after the first of a turn — the only "
        "calls the Chat Completions transport can strip reasoning from), by "
        f"path ({_STRIP_PATHS}) and outcome ({' / '.join(STRIP_OUTCOMES)}). "
        "Both outcomes are recorded so that an absent series means NO "
        "OBSERVATION and never a zero. Resets on every restart — "
        "reasoning_strip_log in Postgres is the durable record (issue #325, "
        "ADR 0013).",
        ("path", "outcome"),
    ),
    _CounterSpec(
        "nora_reasoning_strip_writes_total",
        f"reasoning_strip_log row writes, by outcome ({_WRITE_OUTCOMES}). A "
        "failed write never costs the turn, so this is the only place the "
        "record going blind is visible; `impossible` is a row the loop could "
        "not have produced and is deliberately NOT `failed`, which a database "
        "blip also produces.",
        ("outcome",),
    ),
    # `[dreamer] max_unit_hold_seconds` fired: `_run_adaptor`'s own wall clock
    # around the WHOLE call to `_dream_adaptor_once`, covering the model budget, the
    # two bounded store phases AND the cursor reads around them
    # (`clock.last_fired`/`record_fire`, every `language.*` watermark call,
    # `record.since`, `media_join.enrich`). A DISTINCT series from
    # `nora_dream_skipped_total{reason="in_flight_adaptor"}`: that one counts a tick
    # that found the unit still running and cannot say whether the run in progress is
    # long or wedged; this one fires only when the wall clock cuts a unit off. Also
    # distinct from `nora_dream_store_timeout_total`, the timeout on one store phase:
    # this fires only when the OUTER clock expires, which for a store-phase hang
    # means the inner one did not (a collaborator swallowing `CancelledError`, or a
    # synchronous blocking call).
    _CounterSpec(
        "nora_dream_unit_timeout_total",
        "Dreamer (adaptor, key) units released by [dreamer] "
        "max_unit_hold_seconds rather than by the run finishing — the unit "
        "is a WEDGE, distinct from an ordinary long run that "
        "nora_dream_skipped_total{reason=\"in_flight_adaptor\"} alone cannot "
        "tell apart from one wedged since Tuesday.",
        ("adaptor",),
    ),
    # The public Instagram summons (`nora.app.summons`). Both label vocabularies
    # are DERIVED from `nora.domain.summons`, which is what the increments pass.
    _CounterSpec(
        "nora_summons_total",
        "Public Instagram summons handled, by outcome ("
        + " / ".join(SUMMONS_OUTCOMES)
        + ") and mode (" + " / ".join(SUMMONS_MODES) + "). staged in "
        "dry_run is the whole turn with nothing posted; staged in live is a "
        "reply waiting out the bridge's too_soon, retried by its flush alone. "
        "unresolved is a card whose post the bridge could not name. "
        "ambiguous is a write whose result is unknown and is never retried.",
        ("outcome", "mode"),
    ),
    _CounterSpec(
        "nora_summons_follow_ups_total",
        "Public summons turns that ended on their own with prose and no "
        "send_reply and were asked once, in the same conversation, whether to "
        "send it; by what that follow-up ended in ("
        + " / ".join(FOLLOW_UP_OUTCOMES)
        + ") and mode (" + " / ".join(SUMMONS_MODES) + "). replied is a reply "
        "held for the flush; silent is nothing held (the summons is silent); "
        "timed_out and failed are nothing held because the invocation timed "
        "out or raised during the follow-up.",
        ("outcome", "mode"),
    ),
    _CounterSpec(
        "nora_summons_intake_total",
        "summons events from the instagram-bridge's SSE stream, by outcome ("
        + " / ".join(INTAKE_OUTCOMES)
        + "). queued reached the consumer; disabled arrived while "
        "[social.instagram.summons] is off and was dropped; malformed failed "
        "to parse (no row, no answer); overflow is an older queued card "
        "pushed out of the full inbox by a newer one.",
        ("outcome",),
    ),
    # The cognition loop's stimuli and its wake evaluator. Both vocabularies are
    # DERIVED from `nora.domain.cues` / `nora.domain.stimuli`. The `stimulus` name
    # (rather than `signal`) avoids clashing with Signal the platform (DEC-234); the
    # one stated exception to metric names being append-only.
    _CounterSpec(
        "nora_stimulus_total",
        "Stimuli appended to a unit's stimulus table, by kind ("
        + " / ".join(STIMULUS_KINDS)
        + ") and cue ("
        + " / ".join((*CUES, "none"))
        + "). ONE INCREMENT PER (stimulus, cue): a stimulus carrying two cues "
        "counts twice, one carrying none counts once under cue=none, so the "
        "sum over cue is not the stimulus count. A person's item raises one "
        "(kind=message); a daily floor raises one (kind=time); Nora's own "
        "items raise none.",
        ("kind", "cue"),
    ),
    _CounterSpec(
        "nora_cognition_wake_total",
        "Wakes the evaluator recorded, by reason ("
        + " / ".join(WAKE_REASONS)
        + ") and mode ("
        + " / ".join(WAKE_MODES)
        + "). mode is live: a wake starts a Cognition session, recorded with "
        "its reason and stimulus range in cognition_wake.",
        ("reason", "mode"),
    ),
    _CounterSpec(
        "nora_cognition_wake_reclaimed_total",
        "Wakes a previous process left open (a tracked wake with no outcome row, "
        "recorded before this process started) that this process released at "
        "boot, recorded `cancelled` with a restart detail. Each one is a session "
        "a restart killed that would otherwise have held its unit single-flight "
        "for the rest of session_timeout_seconds and then counted as a failure. "
        "Written once at start, so a value above 0 reads «the previous process "
        "ended with sessions in flight».",
    ),
    _CounterSpec(
        "nora_cognition_session_total",
        "Cognition sessions started: one per wake whose agenda was non-empty, "
        "session row recorded before the model call.",
        ("mode",),
    ),
    _CounterSpec(
        "nora_cognition_session_skipped_total",
        "Wakes on which no Cognition session ran, by reason "
        "(no_home_room, also recorded failed so its stimuli stay pending / "
        "empty_agenda). No model call is made on either.",
        ("reason",),
    ),
    _CounterSpec(
        "nora_cognition_session_ended_total",
        "Tracked Cognition wakes whose outcome this process wrote, by outcome "
        "(closed / failed / timeout / cancelled). Only closed advances the "
        "unit's watermark; the other three return the wake's stimuli to "
        "pending (20 §3). A wake that expires with no outcome (a crash, a "
        "failed write) is not counted here: it is read as failed.",
        ("outcome",),
    ),
    _CounterSpec(
        "nora_cognition_session_limit_total",
        "Cognition sessions whose model loop stopped at one of its bounds ("
        + " / ".join(LIMIT_STOP_REASONS)
        + "), by which. Each is recorded as a FAILED wake with cause infra "
        "(nothing closed or struck, the stimuli stay pending, the failure "
        "backoff applies), so it also counts in "
        "nora_cognition_session_ended_total{outcome=failed}. A rising rate is "
        "a session that cannot finish inside its bounds (DEC-247).",
        ("limit",),
    ),
    _CounterSpec(
        "nora_cognition_carry_total",
        "Stimuli a closed Cognition session returned to the next agenda, by "
        "state (carried / released / struck): carried is an unclosed, waiting "
        "or deferred item's stimulus with a strike; struck is code closing it "
        "at max_strikes (30 §2).",
        ("state",),
    ),
    _CounterSpec(
        "nora_cognition_stimuli_skipped_total",
        "Wakes that skipped stimuli a session could not read, by reason: "
        "unreadable_rollup is a Rollup stimulus whose ref did not parse (consumed); "
        "roster_miss_rollup names a person the roster does not know yet (retained "
        "as a waiting carry, DEC-245). It counts the wakes, and the skipped ids "
        "are on the session row (agenda.skipped_stimulus_ids, retained_stimulus_ids).",
        ("reason",),
    ),
    _CounterSpec(
        "nora_cognition_stimuli_struck_total",
        "Stimuli code struck (retracted) so a unit stops re-waking on input it "
        "cannot process, by reason: failed is the stimuli the failing wakes "
        "claimed, struck once a unit has failed max_consecutive_failures "
        "sessions in a row (#637 review, major 1). A count of stimuli, not of "
        "wakes; one strike event increments by however many it struck.",
        ("reason",),
    ),
    _CounterSpec(
        "nora_cognition_wake_deferred_total",
        "Unit-ticks where a wake trigger held and a brake stopped it, by "
        "brake ("
        + " / ".join(WAKE_BRAKES)
        + "). Counted once per unit per ~30s tick while it holds, so it "
        "measures how long brakes hold, not how many wakes they stopped.",
        ("brake",),
    ),
    # The session's own outcomes. None of the four carries a room, a person, a
    # unit or any text as a label: each label is a closed vocabulary the code
    # owns.
    _CounterSpec(
        "nora_cognition_item_total",
        "Agenda items a Cognition session closed, by the item's kind and by how "
        "it closed (written / no_change / covered / waiting / struck / unclosed). "
        "written, no_change, covered and waiting are what the MODEL said, "
        "self-attested, and none is evidence the item was handled; struck is code "
        "closing an item at max_strikes; unclosed is code closing an item the "
        "model never closed (the same `waiting` row as a model's, counted apart "
        "here so the two are not one number).",
        ("kind", "outcome"),
    ),
    _CounterSpec(
        "nora_cognition_refused_total",
        "Cognition verb calls the code refused (or could not check: an "
        "`unavailable_<reason>` rule), by verb and by the rule that refused it. "
        "The rule is the refusal reason the model was told, a code constant "
        "(`stale_base`, `duplicate_title`, `pending_ceiling`, `past`, `daily_cap` "
        "of a self-wake, a note-body rule, ...). A note head that moved reads to the "
        "model as `unavailable` and counts as `unavailable_stale_head`. "
        "Counted at the verb, so an early return cannot refuse unseen.",
        ("verb", "rule"),
    ),
    _CounterSpec(
        "nora_cognition_unit_tokens_total",
        "Tokens a Cognition session spent over its whole loop (the model's "
        "accumulated usage), by what the unit's agenda was made of: one item "
        "kind (message / time / rollup / self / social / play), or `mixed` when "
        "it held several. NOT by unit, room or person. A session whose runner "
        "could not read its usage adds nothing, so the series is absent for it "
        "rather than zero.",
        ("unit_kind",),
    ),
    _CounterSpec(
        "nora_self_wake_total",
        "Self-wake calls and firings, by outcome: created / moved / cancelled "
        "(a `wake_at`, `wake_move` or `wake_cancel` the code accepted), refused "
        "(it did not), and fired (a closed session picked a due self-wake up "
        "onto its agenda).",
        ("outcome",),
    ),
    # The Assess adaptor's nightly data-quality checks. ONE INCREMENT PER
    # (run, check) when a check's status is `fail`, never per offending row, so this
    # counts how many nightly runs found a check red, not how many rows it found.
    # `nora.domain.assess.DQ_CHECKS` is the label vocabulary; `not_applicable` never
    # increments this (a check that could not run is not a failure).
    _CounterSpec(
        "nora_dq_failures_total",
        "Nightly data-quality check runs (nora.app.assess_run) whose check "
        "came back status=fail, by check ("
        + " / ".join(DQ_CHECKS)
        + "). status=not_applicable (the tier the check reads has not "
        "shipped yet) never increments this, and neither does status=error "
        "(nora_dq_errors_total is that series) — a check that never reached "
        "a verdict is not the same fact as one that reached a verdict and "
        "found a violation.",
        ("check",),
    ),
    # A check's own function raised instead of returning a verdict (a query
    # error, a connection drop, a programming bug); `nora.app.assess_dq._isolated`
    # catches it so the rest of the night's checks still run, and counts it here
    # rather than in nora_dq_failures_total: an ERROR is never a PASS, and it must
    # never read as the SAME kind of red as a check that ran clean to a `fail`
    # verdict (AGENTS.md's Measure spine).
    _CounterSpec(
        "nora_dq_errors_total",
        "Nightly data-quality check runs (nora.app.assess_run) whose check "
        "raised instead of returning a verdict, by check ("
        + " / ".join(DQ_CHECKS)
        + "). Distinct from nora_dq_failures_total on purpose: this is a "
        "check that never finished, not one that finished and found a "
        "violation.",
        ("check",),
    ),
    # One-time codes (`nora.app.otp`). The API answers every request the same
    # way, so these are where "sent", "nobody by that name" and "rate limited" can be
    # told apart.
    _CounterSpec(
        "nora_otp_requests_total",
        "Requests for a one-time code, by platform and outcome ("
        + " / ".join(OTP_REQUEST_OUTCOMES) + ").",
        ("platform", "outcome"),
    ),
    _CounterSpec(
        "nora_otp_checks_total",
        "Checks of a one-time code, by outcome (" + " / ".join(OTP_CHECK_OUTCOMES) + ").",
        ("outcome",),
    ),
    # Nora's one Google identity (`nora.infra.google.auth`): the shared refresh
    # token refused as `invalid_grant` (revoked, expired, or a Testing-mode consent
    # screen's 7-day expiry). Counted on its own so a dead sign-in is one alert
    # rather than a week of `unavailable` from every Google service at once.
    _CounterSpec(
        "nora_google_invalid_grant_total",
        "Refreshes of Nora's one Google refresh token refused as invalid_grant "
        "(the sign-in is revoked or expired: run scripts/google_auth.py again).",
        (),
    ),
    # The memory index's projection (`nora.infra.sql.memory_index`): one per
    # projection a store write made. `queued` is a projection that failed inside the
    # store's transaction while the store's own write stood; its source waits on
    # `memory_projection_queue`. `lost` failed to queue as well, so only a rebuild
    # finds it. `ok` is counted too, so a `queued` rate has a denominator and an
    # absent series is not read as «never failed».
    _CounterSpec(
        "nora_memory_projection_total",
        "Memory index projections made by store writes, by node kind ("
        + " / ".join(NODE_KINDS) + ") and outcome ("
        + " / ".join(PROJECTION_OUTCOMES) + "). queued = failed in the "
        "store's transaction and put on memory_projection_queue, the store "
        "write kept; lost = the queue write failed too.",
        ("kind", "outcome"),
    ),
    # The memory index's read (`nora.agent.memory_search`): one per search, by
    # the status the caller was given. `partial` is an answer from an index still
    # being rebuilt or with sources waiting on its queue; `unavailable` an error.
    _CounterSpec(
        "nora_memory_search_total",
        "Memory index searches, by status (" + " / ".join(SEARCH_STATUSES) + "). "
        "partial = the index is not complete, its projection queue is not "
        "empty, the seeds fell back to lexical (no embedder, or it failed), or "
        "nodes in scope have waited past a few worker ticks for a vector of the "
        "current model (a backlog); unavailable = the "
        "search failed (fail closed).",
        ("status",),
    ),
    # The memory index's embedding worker (`nora.infra.sql.memory_embed`): one
    # per node it tried. `ok` is counted, so a failure rate has a denominator.
    _CounterSpec(
        "nora_memory_embed_total",
        "Memory index nodes the embedding worker tried, by outcome ("
        + " / ".join(EMBED_OUTCOMES) + "). unavailable = the embedder or the "
        "blob store did not answer (the tick stops); rejected = the input was "
        "refused or would not decode; empty = nothing to embed; error = any "
        "other failure of that node. Every failure backs the node off; "
        "given_up is counted in addition when a failure of the input itself "
        "(rejected, empty, error) was the node's last allowed attempt; an "
        "unavailable attempt never gives a node up.",
        ("outcome",),
    ),
    # The dense seed's HNSW scan coming back with fewer nodes than the search's
    # scope holds vectors (and fewer than it asked for): the scan hit
    # `hnsw.max_scan_tuples` first (`nora.infra.sql.memory_read`). No label, no
    # content: a count of searches.
    _CounterSpec(
        "nora_memory_dense_short_total",
        "Memory index searches whose dense seed returned fewer nodes than it "
        "asked for while the search's scope held more vectors than it returned: "
        "the HNSW scan stopped at hnsw.max_scan_tuples first.",
        (),
    ),
    # The embedding worker's texts cut to fit the embedder's micro-batch:
    # counted in addition to `ok`, by node kind, so a rise says which kind needs
    # chunking. Bounded: the embedded kinds only.
    _CounterSpec(
        "nora_memory_embed_truncated_total",
        "Memory index texts embedded from their head only, cut to fit the "
        "embedder's micro-batch, by node kind (" + " / ".join(EMBEDDED_KINDS) + "). "
        "Counted in addition to nora_memory_embed_total{outcome=ok}; the "
        "trigger for chunking long nodes.",
        ("kind",),
    ),
    # The social stream's poller (design/cognition-loop/22-social-stream.md §4).
    # Every vocabulary is DERIVED from `nora.domain.social_stream`. No person,
    # account or item is a label.
    _CounterSpec(
        "nora_social_polls_total",
        "Social stream probes and reads, one per social_poll row, by network, "
        "stage (" + " / ".join(POLL_STAGES) + ") and outcome ("
        + " / ".join(POLL_OUTCOMES) + ").",
        ("network", "stage", "outcome"),
    ),
    _CounterSpec(
        "nora_social_upstream_calls_total",
        "Upstream calls the social poller spent against the login's hourly budget, "
        "by network and stage: a probe counts its source's probe cost, a read one.",
        ("network", "stage"),
    ),
    _CounterSpec(
        "nora_social_poll_skipped_total",
        "Social poller ticks that stopped before every due member was probed, by "
        "network and reason (" + " / ".join(SKIP_REASONS) + ").",
        ("network", "reason"),
    ),
    # The back-fill of past posts and reels (22 §7). Its pages are
    # nora_social_polls_total{stage="backfill"}.
    _CounterSpec(
        "nora_social_backfill_items_total",
        "Items on back-fill pages, by network and outcome ("
        + " / ".join(BACKFILL_ITEM_OUTCOMES) + "): past_limit is older than "
        "backfill_max_days or past backfill_max_items, and is not recorded.",
        ("network", "outcome"),
    ),
    _CounterSpec(
        "nora_social_backfill_ended_total",
        "Members whose back-fill ended, by network and reason ("
        + " / ".join(BACKFILL_ENDS) + ").",
        ("network", "reason"),
    ),
    # A post's or reel's counts (22 §5): rows appended where they moved.
    _CounterSpec(
        "nora_social_counts_recorded_total",
        "social_item_count rows appended, by network: one per listed item whose "
        "likes, comments, reposts or hidden-likes switch differ from its latest "
        "row, on any list, back-fill, re-walk or recount page.",
        ("network",),
    ),
    _CounterSpec(
        "nora_social_custody_total",
        "Media items of newly recorded social items, by network and what became "
        "of their bytes (" + " / ".join(CUSTODY_OUTCOMES) + "). Only stored "
        "names bytes in custody.",
        ("network", "outcome"),
    ),
    # A story frame's bytes deleted at their bound (22 §5,
    # `nora.app.social_expiry`), one per content address tombstoned.
    _CounterSpec(
        "nora_social_story_bytes_deleted_total",
        "Story frame media deleted at their bound, one per content address "
        "tombstoned, by reason (" + " / ".join(BYTE_DELETION_REASONS) + "): embedded "
        "went story_media_keep_days after expiry once perceived and embedded, "
        "hard_limit at story_media_keep_max_days whatever existed.",
        ("reason",),
    ),
    _CounterSpec(
        "nora_social_story_unembedded_total",
        "Story frames whose bytes went at the hard limit with no embedding: they "
        "stay unembedded for good. Counted, not an error.",
        (),
    ),
    _CounterSpec(
        "nora_perception_total",
        "Items whose media a perception pass took up, by source ("
        + " / ".join(PERCEPTION_SOURCES) + ") and outcome ("
        + " / ".join(PERCEPTION_OUTCOMES) + "). capped is an item left "
        "unperceived because a unit's daily cap was spent; it is tried again "
        "on a later day, as is unavailable. failed is never read again (a "
        "permanent reason, or its third failed read).",
        ("source", "outcome"),
    ),
    _CounterSpec(
        "nora_extraction_calls_total",
        "Transport attempts for extraction, including retries.",
    ),
    _CounterSpec(
        "nora_extraction_input_tokens_total",
        "Reported input tokens for every extraction response, including rejected output.",
    ),
    _CounterSpec(
        "nora_extraction_output_tokens_total",
        "Reported output tokens for every extraction response, including rejected output.",
    ),
    _CounterSpec(
        "nora_extraction_failures_total",
        "Failed extraction attempts or storage operations; durable work is retained.",
    ),
    _CounterSpec(
        "nora_extraction_claims_written_total",
        "New claims committed by the extraction writer.",
    ),
    _CounterSpec(
        "nora_extraction_usage_missing_total",
        "Extraction requests whose usage is unavailable; measured cost is incomplete.",
    ),
    # Retired message-reading counters stay declared at zero (append-only contract).
    _CounterSpec(
        "nora_message_reading_calls_total",
        "Retired message-reading counter; frozen at zero. Use nora_extraction_*.",
    ),
    _CounterSpec(
        "nora_message_reading_input_tokens_total",
        "Retired message-reading counter; frozen at zero. Use nora_extraction_*.",
    ),
    _CounterSpec(
        "nora_message_reading_output_tokens_total",
        "Retired message-reading counter; frozen at zero. Use nora_extraction_*.",
    ),
    _CounterSpec(
        "nora_message_reading_failures_total",
        "Retired message-reading counter; frozen at zero. Use nora_extraction_*.",
    ),
    _CounterSpec(
        "nora_message_reading_claims_written_total",
        "Retired message-reading counter; frozen at zero. Use nora_extraction_*.",
    ),
    _CounterSpec(
        "nora_message_reading_usage_missing_total",
        "Retired message-reading counter; frozen at zero. Use nora_extraction_*.",
    ),
)

_GAUGES: tuple[_GaugeSpec, ...] = (
    # The OAuth keepalive (DEC-308). Published at registration from the state
    # file, so a restart does not blank them; absent while no lane is kept (image
    # generation off, or no token): not applicable, never 0.
    _GaugeSpec(
        "nora_oauth_last_refresh_timestamp_seconds",
        "Unix time of the lane's last SUCCESSFUL token refresh (0: none under "
        "the current login). `time() - this` past twice the interval means the "
        "keepalive is not keeping the login alive.",
        ("lane",),
    ),
    _GaugeSpec(
        "nora_oauth_access_expiry_timestamp_seconds",
        "Unix time the lane's current access token expires (0: unknown).",
        ("lane",),
    ),
    _GaugeSpec(
        "nora_oauth_refresh_interval_seconds",
        "The lane's configured keepalive interval: the longest it goes without "
        "a refresh. The staleness alert compares against twice this.",
        ("lane",),
    ),
    _GaugeSpec(
        "nora_oauth_refresh_refused",
        "1 when the lane's last refresh failed for a reason no retry fixes (the "
        "issuer refused the refresh token, or the state file is unwritable); 0 "
        "after a success or at registration.",
        ("lane",),
    ),
    # The divergence itself, which IS the failure rather than a proxy for it:
    # nora's belief on one side, the controller's own report on the other. Both 1 =
    # agreement. expected=1 with controller=0 sustained is the failure shape.
    _GaugeSpec(
        "nora_cognition_session_units",
        "Units by whether a Cognition session can run for them: state=runnable "
        "(a home_room = true room in policy.toml, woken by the evaluator) or "
        "no_home_room (never woken). Set at boot with [cognition] enabled; a "
        "non-zero no_home_room is a unit the loop does not cover.",
        ("state",),
    ),
    _GaugeSpec(
        "nora_music_expected_playing",
        "1 when nora believes a track is currently playing for the guild.",
        ("guild_id",),
    ),
    _GaugeSpec(
        "nora_music_controller_playing",
        "1 when the audio controller reports audio actually moving for the "
        "guild (status playing, position advancing, not autopaused). 0 when "
        "it reports otherwise. ABSENT, never 0, when nora could not read it "
        "— a gauge that cannot be measured must not assert a value.",
        ("guild_id",),
    ),
    _GaugeSpec("nora_last_event_age_seconds", "Seconds since the last ingest event."),
    # The per-platform answer to the question the line above answers, as a
    # separate series rather than a label: that series is UNLABELLED on purpose (the
    # Signal-era liveness series), and adding a label changes a series' IDENTITY, so
    # anything selecting it by bare name would stop matching with no error. This one
    # covers every enabled platform, so a wedged Discord Gateway is alertable.
    #
    # DEC-232: what system-time history costs, per covered table, measured hourly by
    # `nora.app.revisions`. The decision's reversal condition is this growth on the
    # hot tables.
    _GaugeSpec(
        "nora_row_history_rows",
        "Versions held in row_history, per covered table (hourly).",
        ("table",),
    ),
    _GaugeSpec(
        "nora_row_history_bytes",
        "Bytes of row_history rows, per covered table (hourly).",
        ("table",),
    ),
    _GaugeSpec(
        "nora_adapter_last_event_age_seconds",
        "Seconds since the last ingest event, per platform.",
        ("platform",),
    ),
    # `Gauge`, not `Counter`, on purpose: prometheus-client's exposition format
    # unconditionally appends `_total` to every counter-typed series' rendered name
    # (`exposition.py::generate_latest`, not configurable). These two names do not end
    # in `_total`; a `Counter` would silently rename them to `honcho_feed_success_total`
    # / `honcho_feed_failure_total`, which the append-only metric-name contract
    # forbids. A `Gauge` only ever incremented (see `Metrics.inc`) renders under its
    # exact declared name and is otherwise indistinguishable from a counter for these
    # call sites.
    _GaugeSpec("honcho_feed_success", "Successful Honcho feed calls."),
    _GaugeSpec("honcho_feed_failure", "Failed Honcho feed calls."),
    # R-17's other half. A failure COUNTER alone cannot distinguish "0 because
    # enrichment is healthy" from "0 because the enrichment code never runs". This
    # gauge is always published, on every path including total failure, so
    # `nora_identity_aliases == 1` says "R-12 is down to equality against the primary
    # id" and `>= 2` says the derivation worked. Alert on `== 1`, not on the
    # counter.
    _GaugeSpec(
        "nora_identity_aliases",
        "Distinct id forms Nora recognises as her own, by platform. 1 means "
        "enrichment produced nothing beyond the configured primary.",
        ("platform",),
    ),
    # Presence liveness: the heartbeat. Set after every SUCCESSFUL collection to
    # the age (seconds) of that circle's last success, so a stalled daemon shows a
    # climbing age rather than a frozen fix count. Alert on it crossing a few
    # cadences: «the collector is up but not collecting», which R-5 would otherwise
    # hide (a quiet circle looks identical).
    _GaugeSpec(
        "nora_presence_last_collection_age_seconds",
        "Seconds since this circle's last SUCCESSFUL collection. Climbing means "
        "the daemon is up but not collecting.",
        ("circle",),
    ),
    # The climbing-age liveness shape of `nora_presence_last_collection_age_seconds`,
    # applied to the rollup: set after every successful run_once, so a stalled daemon
    # shows a climbing age rather than a frozen derivation count.
    _GaugeSpec(
        "nora_analytics_rollup_age_seconds",
        "Seconds since this subject's last successful analytics rollup run. "
        "Climbing means the daemon is up but not deriving.",
        ("subject",),
    ),
    # The R-21 startup invariant: 1 when the Signal group is a SUBSET of the
    # Life360 circle (disclosure holds by construction), 0 when it is not. Published
    # at startup and whenever it is checked; alert on `== 0`.
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
    # THERE IS DELIBERATELY NO `nora_reminders_pending` HERE. A declared but
    # unpublished gauge renders `0.0`, which reads as "no backlog" and is
    # indistinguishable from "nobody is measuring".
    #
    # **It cannot be published.** The backlog is a row count, and every
    # `ReminderStore` read is a coroutine (`all_pending`, `count_pending`).
    # `Metrics.gauge_function` binds a SYNCHRONOUS callable that prometheus-client
    # invokes inside `CollectorRegistry.collect()`, and `/metrics` is an aiohttp
    # handler (`nora.obs.health`), so that callback runs ON the event loop that would
    # have to run the query.
    #
    # **Publishing it from the tick is worse than absent.** With `[heartbeat]
    # enabled = false`, `remind` still writes rows (the store is wired outside the
    # flag), so a tick-published gauge exports `0.0` forever on exactly the
    # deployment where the backlog only grows. Deriving it from
    # `created_total - fired_total` drifts on the first cancel and resets on every
    # restart while the rows survive.
    #
    # The backlog is a DATABASE question; `nora reminders list` (read-only,
    # `nora.app.cli`) answers it and flags overdue rows.
    #
    # The heartbeat's LIVENESS is the one gauge in this file that must not be written
    # from inside the loop it measures. It is bound to a function evaluated at SCRAPE
    # time (`Metrics.gauge_function`), so a loop that has STOPPED shows a climbing
    # age rather than freezing at its last reading. Alert on it crossing a few ticks.
    # Contrast `nora_presence_last_collection_age_seconds` above, which IS published
    # from inside its collector's loop and therefore only detects a collector that is
    # up and failing, not one that is gone.
    _GaugeSpec(
        "nora_heartbeat_last_tick_age_seconds",
        "Seconds since the heartbeat's last completed tick, computed at scrape "
        "time. Climbing past a few ticks means the loop has stopped.",
    ),
    _GaugeSpec(
        "nora_note_index_entries",
        "Standing notes rendered into <notes> for a unit on the last tag "
        "it handled. Absent when [notes] is off; 0 means the notebook is "
        "wired and empty.",
        ("unit",),
    ),
    # 1 once a summons write came back `writes_blocked`: Instagram answered a
    # comment with an action block and the bridge latched writes off until an
    # operator clears it. 0 after a write publishes. ABSENT before any write.
    _GaugeSpec(
        "nora_summons_writes_blocked",
        "1 when the last public summons write was refused writes_blocked "
        "(the bridge latched comment writes off after an action block; an "
        "operator must clear it), 0 after a write published.",
    ),
    _GaugeSpec(
        "nora_stimulus_pending_age_seconds",
        "Age of a unit's oldest pending stimulus that could wake it (a reply "
        "to Nora inside reply_quiet_seconds does not count), set by the wake "
        "evaluator each tick; 0 when nothing is pending "
        "(design/cognition-loop/20-observe.md §4). ABSENT until the "
        "evaluator's first tick for the unit.",
        ("unit",),
    ),
    # Nora's computer, as Nora sees it (DEC-261). LABELLED on purpose, with one
    # value (`computerd`): an unlabelled gauge is exported at 0 from the moment it is
    # declared, so a deployment with `[computer] enabled = false` would read as a
    # computer that is DOWN. A labelled gauge has no sample until it is set, so "not
    # applicable" is the absence of the series and ComputerDown (`== 0`) can never
    # fire on it.
    _GaugeSpec(
        "nora_computer_up",
        "1 when Nora's last contact with computerd got an answer, 0 when it "
        "did not (refused, timed out, or a non-200 such as a 401 after a token "
        "rotation). Set by the timed probe AND by every computer call. ABSENT "
        "while [computer] is off and until the first probe: not applicable, "
        "never 0.",
        ("target",),
    ),
)

_HISTOGRAMS: tuple[_HistogramSpec, ...] = (
    _HistogramSpec(
        "nora_tag_latency_seconds", "End-to-end tag latency, including the model call.",
    ),
    # Labelled by `audience` because the two
    # populations are bounded by DIFFERENT NUMBERS: a tag's invocation is cut at
    # `[llm] timeout_seconds` (60); a fire turn's at `[heartbeat] timeout_seconds`
    # (120), because nobody is waiting on it. Mixed into one histogram the tail is a
    # blend of two distributions with two ceilings, so neither a p95 nor a bucket
    # count means anything about either path.
    _HistogramSpec(
        "nora_llm_latency_seconds",
        "Model invocation latency, by which path was speaking "
        "(audience=\"tag\" | \"heartbeat\"). The two carry different timeouts.",
        _DEFAULT_BUCKETS,
        ("audience",),
    ),
    _HistogramSpec("nora_overhead_seconds", "Tag latency excluding the model call."),
    # A COUNT, not a duration, so it carries its own buckets: the default set is
    # calibrated for seconds and would put every possible value in the first
    # bucket.
    #
    # Recorded once per ADMITTED tag on every exit path
    # (`EngagementPipeline._run_admitted`'s `finally`), so a turn that delivered
    # nothing observes 0 rather than being absent: "how often does she say nothing at
    # all" is the same question, and a histogram that only records successes cannot
    # answer it.
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
    # Time the speech formatter adds before she is heard
    # (`nora_speech_format_seconds`), on every path that called the model (a short or
    # long reply calls none and observes nothing). Its bound is
    # `[speech.tts.format] timeout_seconds`.
    #
    # Observed on EVERY exit including the failures: a latency series that only
    # records successes describes the path that already works.
    _HistogramSpec(
        "nora_speech_format_seconds",
        "Wall-clock duration of one speech-formatter model call, on every exit "
        "that made one, refusals and timeouts included.",
        (0.25, 0.5, 0.75, 1, 1.5, 2, 3, 5),
    ),
    # `nora_debrief_seconds`: deliberately NOT folded into
    # `nora_tag_latency_seconds`. The debrief holds the slot, so its cost IS
    # user-visible, but adding it to an existing SLO series would change what that
    # series measures. Two series, two questions.
    #
    # The gate is **p95 <= 5s**, and the case it names is the SECOND-QUEUED tag
    # rather than an average: the person whose turn is being reviewed already has
    # their reply, so the cost lands on whoever is queued behind a busy conversation.
    _HistogramSpec(
        "nora_debrief_seconds",
        "Wall-clock duration of one debrief fold, on every exit path. "
        "Excluded from nora_tag_latency_seconds on purpose. Gate: p95 <= 5s.",
        (0.5, 1, 2, 3, 5, 8, 13, 21),
    ),
    # `nora_media_join_seconds`: the COST of the render join, not its result
    # (CLAUDE.md: "instruments measure the RESULT; something must measure the
    # COST"). The join does one sequential `get` per distinct blob in the window, on
    # the critical path of every tagged turn. Buckets are sub-second because anything
    # above that is already the answer.
    #
    _HistogramSpec(
        "nora_media_join_seconds",
        "Wall-clock cost of one media join, by the PATH that paid it "
        f"({_JOIN_PATHS}). Excludes calls with no media in the window.",
        # Reaching 30s, and labelled, because the paths have nothing in common but
        # the code. A tag window is a handful of blobs under a reply's latency; adoption
        # hands this a 1000-row backfill scan at process start. One unlabelled series
        # with a 2s ceiling would put both in one histogram, where the batch paths
        # saturate `+Inf` and are unmeasurable.
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
    # `nora_media_comprehension_seconds`: measured around the READ ALONE (the
    # interpreter call, or the whole chunk run for a video), not the job around it. A
    # `nothing_found` never reaches this line, so the dedup that skips a re-read
    # cannot dilute what this says about a describer's own latency. The buckets reach
    # 300s deliberately: a chunked video read is ten model calls, not one, and a
    # histogram whose top bucket is 21s would report every video as +Inf.
    _HistogramSpec(
        "nora_media_comprehension_seconds",
        "One media comprehension read's latency — the model call alone for an "
        "image, the whole chunk run for a video. Excludes cache hits.",
        (1, 2, 5, 10, 20, 40, 80, 160, 300),
    ),
    # `nora_relevance_gate_errors_total{kind="timeout"}` says HOW OFTEN the
    # ceiling fired; only a distribution says whether the ceiling is the wrong
    # number.
    #
    # The samples are **right-censored at `[relevance_gate] timeout_seconds`**: a call
    # cut at 20s may have wanted 45, and is recorded as 20. So this measures how long
    # the gate WAITS, not how long the model takes, and the top bucket is the
    # population `{kind="timeout"}` counts. Buckets are the default set (to 30)
    # because the ceiling is 20 and nothing above it can be observed; widening them
    # would draw buckets that can never fill.
    #
    # Observed on every exit including the failures, `nora_debrief_seconds`'s rule:
    # a latency series that records only successes describes the path that already
    # works.
    _HistogramSpec(
        "nora_relevance_gate_seconds",
        "One Relevance Gate model call's latency, on every exit including the "
        "fail-closed ones (design/dreamer-agent-loop/36, issue #163). "
        "RIGHT-CENSORED at [relevance_gate] timeout_seconds — a timed-out call "
        "is recorded at the ceiling, so read the top bucket against "
        "nora_relevance_gate_errors_total{kind=\"timeout\"}.",
    ),
)

# The full set of declared metric names, independent of kind: what the
# name-preservation test (tests/test_metrics_export.py) enumerates against.
DECLARED_NAMES: frozenset[str] = (
    frozenset(spec.name for spec in _COUNTERS)
    | frozenset(spec.name for spec in _GAUGES)
    | frozenset(spec.name for spec in _HISTOGRAMS)
)
