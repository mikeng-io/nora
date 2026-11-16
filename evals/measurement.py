"""The one measurement claim a run declares, and its honest result policy.

`design/evaluation/coverage.md` «Execution modes and their claims»: a journey
run is a **protocol rehearsal** by default — a finite generated world
(`CausalProtocol`/`GeneratedJudge`) answers every model request, so behavior
stays `UNMEASURED` — or, only when the caller declares it AND every selected
case's world names a live `model_gateway`, a **behavioral evaluation** whose
evaluator verdicts carry the behavior axis (`measurement.md` «Three
results»).

Every surface reads this module, never its own string: the SDK draw's
`environment_state` (`per_draw`), the retained corpus JSON (`mode`/
`behavior`), the three-axis rollup (`behavior_status`) and the CLI line.
The parent's autonomous-journey adapter reads `for_world` and
`retained_behavior` for the per-case report it owns.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from evals.outcome import ERROR, FAIL, PASS, REVIEW, UNKNOWN, UNMEASURED

#: The claim a run binds into its grading selection and stamps per draw.
PROTOCOL_REHEARSAL = "protocol-rehearsal"
BEHAVIORAL_EVALUATION = "behavioral-evaluation"

#: The `--measure` surface: how the caller names each claim.
MEASURES = ("protocol", "behavioral")


@dataclass(frozen=True, slots=True)
class Measurement:
    """The bound measurement: one mode, its per-draw stamp and its rollup."""

    mode: str = PROTOCOL_REHEARSAL

    @property
    def behavioral(self) -> bool:
        return self.mode == BEHAVIORAL_EVALUATION

    @property
    def phrase(self) -> str:
        """The run line's prose form (`mode: <phrase>`)."""
        return "behavioral evaluation" if self.behavioral else "protocol rehearsal"

    def per_draw(self, *, error: Any, judged: Any) -> str:
        """`NORA_BEHAVIOR` on one draw's `environment_state`.

        Protocol draws never claim behavior. A behavioral draw claims what
        its own path retained — the journey's verdict — `ERROR` when the
        draw errored, and `UNMEASURED` when nothing was retained (an
        unestablished draw, never a pass).
        """
        if not self.behavioral:
            return UNMEASURED
        if error:
            return ERROR
        return str(judged) if judged else UNMEASURED

    def behavior_status(self, *, verdicts: Mapping[str, int],
                        unanswered: int, execution: str,
                        validity: str) -> str:
        """The behavior axis for a measurement-bound report.

        Instrument ERRORs and unanswered rows are `ERROR`, never a quality
        score; `FAIL`/`REVIEW` are the evaluator's own observed outcomes;
        `PASS` requires an established measurement — valid execution AND
        validity — so an unestablished run stays `UNKNOWN`.
        """
        if not self.behavioral:
            return UNMEASURED
        if unanswered or verdicts.get(ERROR) or not any(verdicts.values()):
            return ERROR
        if verdicts.get(FAIL):
            return FAIL
        if verdicts.get(REVIEW):
            return REVIEW
        if execution == PASS and validity == PASS:
            return PASS
        return UNKNOWN

    def retained_behavior(self, verdict: str) -> str:
        """What a per-case report may claim for behavior: the verdict under
        a behavioral measurement, `UNMEASURED` under protocol."""
        return verdict if self.behavioral else UNMEASURED


#: The two bound claims, for callers that select rather than construct.
PROTOCOL = Measurement()
BEHAVIORAL = Measurement(mode=BEHAVIORAL_EVALUATION)


def for_measure(value: str) -> Measurement:
    """The measurement a `--measure` choice names; anything else is refused."""
    if value == "behavioral":
        return BEHAVIORAL
    if value == "protocol":
        return PROTOCOL
    raise ValueError(f"unknown measurement {value!r}; declare one of {MEASURES}")


def for_world(world: Any) -> Measurement:
    """The measurement a generated journey world can honestly carry out —
    behavioral only when it declares a live model gateway, so the claim is
    read off the world rather than trusted to agree with one."""
    return BEHAVIORAL if getattr(world, "model_gateway", None) is not None else PROTOCOL
