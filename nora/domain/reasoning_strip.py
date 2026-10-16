"""The reasoning-strip port — one row per MULTI-TURN agent invocation, so that
a turn whose reasoning the transport threw away is distinguishable from a turn
that reasoned fine.

WHAT IS BEING COUNTED, AND WHY IT NEEDS A DENOMINATOR
-----------------------------------------------------
OmniRoute's Chat Completions transport cannot carry `reasoningContent` in a
multi-turn position. Strands' `OpenAIModel._format_regular_messages` therefore
DROPS every `reasoningContent` block out of the request it is about to send,
logs one WARNING from `strands.models.openai`, and continues. It does not
raise: the loop runs on to the next completion and the turn reaches TTS.

A raw count of those drops answers nothing. Two in two hours is a catastrophe
if the loop only went multi-turn twice, and a rounding error if it went
multi-turn two hundred times. So the unit of record here is the OPPORTUNITY,
not the event:

    continuation_calls  — model calls in this TURN after its first one. These
                          are exactly the calls the transport can strip: a
                          `reasoningContent` block can only be in the list if a
                          model answer THIS LOOP RECEIVED put it there.
    strip_calls         — of those, how many carried reasoning that the
                          formatter then discarded.

**«after the first call», not «with a prior assistant message in the list».**
`nora.agent.prefill` puts an assistant message into every tag prefill
(`prefill.py`, the rendered conversation), so by the message-shape reading the
FIRST call of every tag turn would be a continuation. It carries no
`reasoningContent` (a prefill is rendered text), so counting it would inflate
the denominator by one on every tag turn and dilute the rate. The loop position
is the grounded predicate and is what the code implements.

**ONE TURN, WHICH IS NOT ALWAYS ONE `invoke_async`.** A turn cut off by one of
R-8's caps with no prose gets a budget-exhaustion draft
(`factory.draft_from_exhausted_loop`) — a second invocation on the SAME agent,
over the same accumulated context, with this watch still registered. Its
request genuinely carries the accumulated reasoning and the transport genuinely
drops it, so it is counted, and the unit of a row is deliberately the TURN
rather than the invocation. Excluding it would under-report a real strip on
precisely the turns that had already run out of room.

`strip_calls / continuation_calls`, summed over rows, is the rate. It is
computable from this table alone, without the process that produced it still
being alive, which a Prometheus counter cannot promise (`docs/adr/0013-*.md`).

ROWS ARE WRITTEN ONLY WHEN `continuation_calls > 0`. A single-call turn has
zero opportunity: it contributes nothing to either half of the ratio, and
writing it would make this table a log of every model invocation in the system
rather than a census of the ones at risk.

**A row with `strip_calls > continuation_calls` cannot be produced by the loop**
— it would mean the reasoning came from the prefill rather than from a model
answer — and it is refused three times over: `ReasoningStripWatch` logs it at
ERROR and declines to write, the write counter records `impossible` rather than
`failed`, and the table's CHECK would refuse it anyway.

WHAT MAKES A CHANGE LEGIBLE
---------------------------
`reasoning_effort` is read off the model provider's OWN config at the moment of
the call, never from `nora.config` — it records the value the request actually
carried, which makes an operator's change to it legible the next day rather
than after a log dive. Reconstructing which turns ran under which effort is a
`GROUP BY`.

**`model` is the OmniRoute ALIAS, and cannot see a combo repoint.** The column
looks like it answers more than it does: the provider holds a `model_id` copied
from `[llm] model` at startup, so when the combo behind `nora` is repointed
upstream, `GROUP BY model` reads `nora` on both sides of it. The served model
id is not available at this seam at all: `AfterModelCallEvent.
ModelStopResponse` carries `message` and `stop_reason` and nothing else. The
column is still useful because a deployment that moves the ALIAS (a second
tier, an eval run against `nora-gate`) is legible through it; it is not the
instrument for an upstream repoint.

`reasoning_effort = "none"` is the case that must not read as health: a path
emitting no reasoning at all cannot be stripped, so `strip_calls = 0` there
means «impossible», not «fine». The column is what separates those.

ABSENCE IS REPRESENTABLE PER ROW. `tag_id` is NULL for a Dreamer run because a
Dreamer run has no tag — not "" and not a synthesised one. The same rule
`participation_window_state.last_send_at` follows one table over.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol, get_args, runtime_checkable
from uuid import UUID

from nora.domain.audience import SPEAKING_AUDIENCES

#: The two outcomes one CONTINUATION model call can have, and the vocabulary
#: `nora_reasoning_strip_total`'s `outcome` label is derived from — never hand
#: copied into the metric declaration (CLAUDE.md's label-vocabulary rule).
#:
#: **Both are recorded, and that is the point.** A counter carrying only the
#: numerator cannot tell «no strips happened» from «nothing was observed»: both
#: read as an absent series, given restarts and lazy label creation. With
#: `clean` recorded too, an absent series means no observation and a present
#: `clean` with no `stripped` means a real zero.
StripOutcome = Literal["stripped", "clean"]

STRIP_OUTCOMES: tuple[str, ...] = get_args(StripOutcome)

#: What became of one row's WRITE, and the vocabulary
#: `nora_reasoning_strip_writes_total`'s `outcome` label is derived from.
#:
#: **`impossible` is separate from `failed` on purpose.** A row whose
#: `strip_calls` exceeds its `continuation_calls` cannot be produced by the
#: loop — it would mean the reasoning arrived from the PREFILL — and the CHECK
#: on `reasoning_strip_log` refuses it. Letting that arrive as `failed` would
#: give a corrupt writer exactly the same signal a wedged or restarting
#: database gives, which is the one confusion this counter exists to prevent.
WriteOutcome = Literal["ok", "failed", "impossible"]

WRITE_OUTCOMES: tuple[str, ...] = get_args(WriteOutcome)

#: `reasoning_strip_log.path` is this, joined: every audience that runs a model
#: loop (`nora.domain.audience`), plus one value per Dreamer adaptor prefixed
#: below. DERIVED rather than written out, so the vocabulary cannot drift from
#: what `fire.py` sends (`heartbeat`, `proactive`, ...).
DREAMER_PATH_PREFIX = "dreamer:"

PATHS_HELP = (
    " / ".join(SPEAKING_AUDIENCES) + f" / {DREAMER_PATH_PREFIX}<adaptor>"
)


def dreamer_path(adaptor: str) -> str:
    """The `path` value for one Dreamer adaptor's run.

    A function rather than an f-string at the call site so the prefix has one
    spelling: `nora.agent.dreamer` writes the row and
    `nora.obs.metric_specs` renders the help text, and a reader filtering on
    `path LIKE 'dreamer:%'` depends on those two agreeing.
    """
    return f"{DREAMER_PATH_PREFIX}{adaptor}"


class NaiveReasoningStripDatetimeError(ValueError):
    """A `created_at` that carries no timezone. Same rule, and the same local
    spelling of it, as `nora.domain.tool_call_log` — the two ports are
    independent even though the rule is identical."""


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise NaiveReasoningStripDatetimeError(
            "created_at must be timezone-aware; got a naive datetime"
        )
    return value.astimezone(UTC)


@dataclass(frozen=True)
class ReasoningStripObservation:
    """One multi-turn agent invocation, as the transport saw it.

    `path` is which loop ran: one of `nora.domain.audience.SPEAKING_AUDIENCES`
    (`tag`, `heartbeat`, `participation`, `proactive`) or
    `dreamer:<adaptor>`. It is a free `str` rather than a `Literal` because
    the Dreamer half of the vocabulary is the ADAPTOR REGISTRY — a closed set
    here would have to be edited every time an adaptor lands, and would be
    wrong in the interval, which is the failure mode a `Literal` is supposed to
    prevent. The other half IS closed and is `SPEAKING_AUDIENCES`, imported
    rather than restated.

    **`proactive` and `dreamer:proactive` are two different loops sharing a
    word.** The first is a `kind="proactive"` reminder coming due — a turn that
    may speak into a room. The second is the Dreamer's proactive ADAPTOR, a
    background research pass that holds no act verb at all. Filtering one and
    reading it as the other is the mistake this note exists to prevent.

    `strip_calls <= continuation_calls <= model_calls` holds by construction in
    `nora.agent.reasoning_strip.ReasoningStripWatch`; it is asserted rather
    than assumed by `tests/unit/test_reasoning_strip_watch.py`.
    """

    id: UUID
    path: str
    model: str
    reasoning_effort: str
    tag_id: str | None
    model_calls: int
    continuation_calls: int
    strip_calls: int
    stripped_blocks: int
    created_at: datetime


def normalise_observation(
    observation: ReasoningStripObservation,
) -> ReasoningStripObservation:
    from dataclasses import replace

    return replace(observation, created_at=ensure_utc(observation.created_at))


@runtime_checkable
class ReasoningStripStore(Protocol):
    """Write-only, deliberately.

    There is no read verb and no consumer to invent one for: nothing in `nora/`
    acts on this signal — acting on it would be a behaviour change, and a retry
    would turn a deterministic request-shape incompatibility into an apparently
    intermittent one. The reader is a person
    with `psql`, and the query is written down in `docs/runbook.md` beside the
    table rather than added here as a method
    `tests/architecture/test_store_methods_have_production_callers.py` would
    then have to be told to forgive.
    """

    async def record_observation(
        self, observation: ReasoningStripObservation
    ) -> None: ...
