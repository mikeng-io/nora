"""The reasoning-strip port — one row per MULTI-TURN agent invocation, so that
a turn whose reasoning the transport threw away stops being indistinguishable
from a turn that reasoned fine (issue #325).

WHAT IS BEING COUNTED, AND WHY IT NEEDS A DENOMINATOR
-----------------------------------------------------
OmniRoute's Chat Completions transport cannot carry `reasoningContent` in a
multi-turn position. Strands' `OpenAIModel._format_regular_messages` therefore
DROPS every `reasoningContent` block out of the request it is about to send,
logs one WARNING from `strands.models.openai`, and continues. In production it
does not raise: the loop runs on to the next completion and the turn reaches
TTS (#325, read back off norty's logs 2026-09-17). Nothing counted it.

A raw count of those drops answers nothing. Two in two hours is a catastrophe
if the loop only went multi-turn twice, and a rounding error if it went
multi-turn two hundred times. So the unit of record here is the OPPORTUNITY,
not the event:

    continuation_calls  — model calls in this invocation that were made with
                          at least one prior assistant turn already in the
                          message list. These are exactly the calls the
                          transport can strip, because a `reasoningContent`
                          block can only be in the list if a previous model
                          answer put it there.
    strip_calls         — of those, how many carried reasoning that the
                          formatter then discarded.

`strip_calls / continuation_calls`, summed over rows, is the rate. It is
computable from this table alone, at any later date, without the process that
produced it still being alive — which a Prometheus counter cannot promise
(`docs/adr/0013-*.md` states that choice and what it rejected).

ROWS ARE WRITTEN ONLY WHEN `continuation_calls > 0`. A single-call turn has
zero opportunity: it contributes nothing to either half of the ratio, and
writing it would make this table a log of every model invocation in the system
rather than a census of the ones at risk.

WHAT MAKES A CHANGE LEGIBLE
---------------------------
`model` and `reasoning_effort` are read off the model provider's OWN config at
the moment of the call, never from `nora.config` — the row records what was
actually sent. Between 08:52Z and 09:42Z on 2026-09-17 the OmniRoute combo
behind the alias changed upstream and the operator moved `reasoning_effort` on
two paths in opposite directions; it took two hours and a log dive to see it,
and the before-window turned out to contain zero comparable events. With these
two columns beside the ratio, the next such change is a `GROUP BY` rather than
an investigation.

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

#: The two outcomes one CONTINUATION model call can have, and the vocabulary
#: `nora_reasoning_strip_total`'s `outcome` label is derived from — never hand
#: copied into the metric declaration (CLAUDE.md's label-vocabulary rule).
#:
#: **Both are recorded, and that is the point.** A counter carrying only the
#: numerator cannot tell «no strips happened» from «nothing was observed»: both
#: read as an absent series, and this process restarts two or three times a day
#: with lazy label creation. With `clean` recorded too, an absent series means
#: no observation and a present `clean` with no `stripped` means a real zero.
StripOutcome = Literal["stripped", "clean"]

STRIP_OUTCOMES: tuple[str, ...] = get_args(StripOutcome)


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

    `path` is which loop ran: `"tag"`, `"participation"`, or
    `"dreamer:<adaptor>"`. It is a free `str` rather than a `Literal` because
    the Dreamer half of the vocabulary is the ADAPTOR REGISTRY — a closed set
    here would have to be edited every time an adaptor lands, and would be
    wrong in the interval, which is the failure mode a `Literal` is supposed to
    prevent.

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
    acts on this signal — acting on it would be a behaviour change, and #325 is
    explicit that a retry would turn a deterministic request-shape
    incompatibility into an apparently intermittent one. The reader is a person
    with `psql`, and the query is written down in `docs/runbook.md` beside the
    table rather than added here as a method
    `tests/architecture/test_store_methods_have_production_callers.py` would
    then have to be told to forgive.
    """

    async def record_observation(
        self, observation: ReasoningStripObservation
    ) -> None: ...
