"""The private journey cohort: declared live-clock cases, plans and judges.

A cohort is the generated world's declared input — an explicit, versioned
private file that stays outside the repository. Each case is a version-2
(behavior) journey plan; the legacy wake-anchored, storage-kind measure is
refused here because unit 4 retires kind-bound cases as behavioral
baselines (`design/evaluation/cognition-loop.md` «Two measurement
versions»). The two judge aliases are declared once and must agree with
every case plan and every services plan the selection binds.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from strands_evals import Case

from evals.rebuilt.journey_manifest import _plan_error

#: The cohort file's own format version — distinct from each case plan's
#: measure version, which must be 2.
COHORT_VERSION = 1
MEASURE_VERSION = 2


@dataclass(frozen=True, slots=True)
class JourneyCohort:
    """A bound cohort: its fingerprint, judges and SDK cases."""

    sha256: str
    raw: bytes
    judges: tuple[str, str]
    cases: tuple[Case, ...]

    def record(self, evidence: Any) -> None:
        """Retain the exact bytes beside the run, then their fingerprint."""
        evidence.retain("journey-cohort.json", self.raw)
        evidence.record("journey_cohort", ref="journey-cohort.json",
                        sha256=self.sha256)


def _case(corpus: str, entry: dict[str, Any], plan: dict[str, Any]) -> Case:
    """The SDK case one cohort entry selects (`metadata.journey` is the plan)."""
    return Case(
        name=str(entry["name"]),
        input=str(entry.get("input", "")),
        expected_output=str(entry.get("expected_output", "")),
        metadata={
            "corpus": corpus,
            "entry": corpus,
            "expect": "either",
            "journey": dict(plan),
            "snapshot": entry.get("snapshot"),
            "parked": {},
            "counterfactual": {},
            "person": str(entry.get("person", "")),
            "mention": bool(entry.get("mention", False)),
            "category": str(entry.get("category", "")),
        },
    )


def load_cohort(path: Path, *, corpus: str) -> JourneyCohort:
    """A declared cohort, or a refusal naming the exact missing input.

    Every check runs before any case composes or a model is dispatched:
    missing or empty cohort, an unversioned file, a legacy kind-bound
    (non-version-2) plan, a malformed plan, or judge aliases the cohort
    did not declare.
    """
    raw = path.read_bytes()
    data = json.loads(raw)
    if (not isinstance(data, dict) or set(data) != {"version", "judges", "cases"}
            or type(data["version"]) is not int
            or data["version"] != COHORT_VERSION):
        raise ValueError("invalid journey cohort manifest")
    judges = data["judges"]
    if (not isinstance(judges, list) or len(judges) != 2
            or not all(isinstance(m, str) and m for m in judges)
            or len(set(judges)) != 2):
        raise ValueError(
            "a journey cohort declares exactly two distinct judge aliases")
    entries = data["cases"]
    if not isinstance(entries, list) or not entries:
        raise ValueError("the journey cohort declares no cases")
    cases: list[Case] = []
    names: set[str] = set()
    for position, entry in enumerate(entries):
        if (not isinstance(entry, dict)
                or not {"name", "plan"} <= set(entry)
                or set(entry) - {"name", "plan", "input", "expected_output",
                                 "person", "mention", "category", "snapshot"}):
            raise ValueError(
                f"journey cohort case {position} is not a declared entry")
        name = entry["name"]
        if not isinstance(name, str) or not name or name in names:
            raise ValueError(f"journey cohort case {position} has no unique name")
        names.add(name)
        plan = entry["plan"]
        if not isinstance(plan, dict):
            raise ValueError(f"journey cohort case {name!r} declares no plan")
        if plan.get("version") != MEASURE_VERSION:
            raise ValueError(
                f"journey cohort case {name!r} is not a version-2 behavior "
                "case; the legacy wake-anchored, storage-kind measure is "
                "retired and is never selected as a behavioral baseline")
        if problem := _plan_error(plan):
            raise ValueError(f"journey cohort case {name!r}: {problem}")
        if list(plan["judges"]) != list(judges):
            raise ValueError(
                f"journey cohort case {name!r} binds undeclared judge "
                f"aliases {plan['judges']!r}; the cohort declares "
                f"{list(judges)!r}")
        cases.append(_case(corpus, entry, plan))
    return JourneyCohort(
        sha256=hashlib.sha256(raw).hexdigest(), raw=raw,
        judges=(judges[0], judges[1]), cases=tuple(cases))


def bound_journey_services(
    corpus: str, cases: Any, plans: Any, *, judges: tuple[str, str],
) -> dict[str, Any]:
    """Every selected case's declared finite world, judge-bound, else a refusal.

    A case with no plan, a plan that is not a finite journey world (a
    positional `RecordedServices` can never stand in for it), or a world
    bound to aliases the cohort did not declare all refuse before any
    case composes or a model is dispatched.
    """
    from nora.subject.journey_world import JourneyServices

    declared = dict(plans or {})
    bound: dict[str, Any] = {}
    for case in cases:
        plan = declared.get(case.name)
        if plan is None:
            raise ValueError(
                f"{corpus}: journey case {case.name!r} declares no services plan")
        if not isinstance(plan, JourneyServices):
            raise ValueError(
                f"{corpus}: journey case {case.name!r}'s services plan is "
                "not a finite journey world; a positional recording cannot "
                "serve the live-clock entry")
        if tuple(plan.judge_models) != tuple(judges):
            raise ValueError(
                f"{corpus}: journey case {case.name!r}'s world binds "
                f"undeclared judge aliases {list(plan.judge_models)!r}; "
                f"the cohort declares {list(judges)!r}")
        if (plan.model_gateway is not None
                and not set(judges) <= set(plan.model_gateway.models)):
            raise ValueError(
                f"{corpus}: journey case {case.name!r}'s model gateway "
                "does not declare the cohort's judge identities "
                f"{list(judges)!r}")
        bound[case.name] = plan
    return bound


def bound_selection(
    args: Any, names: list[str], only: Any,
) -> JourneyCohort | None:
    """The selected journey corpora's bound cohort; refusals raise ValueError.

    Runs before any case composes or a model is dispatched: the cohort file
    itself, every selected case's finite world under `--services`, the two
    judge aliases agreeing everywhere they are declared, and the explicit
    budget — anything missing, empty, malformed, legacy or undeclared is a
    refusal, never a guess. No flag promotes the generated world into a
    real-model baseline.
    """
    from evals.scenarios import JOURNEY_CORPORA, selected_cases

    journey = [n for n in names if n in JOURNEY_CORPORA]
    if args.journey_cohort and not journey:
        raise ValueError("--journey-cohort applies to no selected corpus")
    if not journey:
        return None
    if len(journey) > 1:
        raise ValueError("each journey corpus declares its own cohort")
    if not args.journey_cohort:
        raise ValueError(
            f"{journey} select the declared cohort — pass --journey-cohort")
    if args.services is None:
        raise ValueError(
            f"{journey} require --services declaring each case's finite "
            "journey world")
    if args.run_budget is None:
        raise ValueError(f"{journey} require an explicit --run-budget")
    if args.sample != 1:
        raise ValueError(
            "a live-clock journey re-runs its declared world; --sample stays 1")
    if args.memory_index or args.subject_overrides:
        raise ValueError(
            "subject overrides cannot reach the declared journey world")
    if args.judge and len(names) == len(journey):
        raise ValueError(
            "--judge names the eval judge; a journey's two judge aliases are "
            "declared by its cohort, never promoted by a flag")
    cohort = load_cohort(args.journey_cohort, corpus=journey[0])
    plans = args.service_inputs.corpora.get(journey[0]) if args.service_inputs else None
    if not plans:
        raise ValueError(
            f"the services manifest declares no plans for {journey[0]!r}")
    cases = selected_cases(journey[0], only=only, cohort=cohort)
    bound_journey_services(journey[0], cases, plans, judges=cohort.judges)
    return cohort
