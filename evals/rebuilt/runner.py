"""The runner. ONE function, no suite-specific branch.

    obtain → know → deliver → read

Every case goes through it, whichever entry point it names. The entry point is
looked up, not branched on (`01-boundaries` §3.1), so the runner does not grow
a limb when a suite is added.

**A subject per case, not per suite.** `build_dependencies` costs ~64ms against
one session-scoped container and a template-cloned database
(`03-step0-results` §3), which is what makes this affordable — and it is what
lets a case be plain data: nothing has to be reset between two of them because
nothing is shared between two of them.
"""

from __future__ import annotations

import dataclasses
import shutil
import tempfile
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from nora.subject import Knowledge, Message, NoteSeed, SubjectSpec, know, obtain

from .domain import Case, Outcome, Verdict
from .entry import ENTRY_POINTS


def load(path: Path) -> list[Case]:
    """A corpus file, as cases. DATA — no code runs to add a suite.

    Two shapes, both already in the tree: a bare list of cases, or a mapping
    with `cases:` and an optional `config:`. The second is what the adversarial
    corpora were already written as, and it is what lets a suite state the
    configuration it is ABOUT — `trigger.yaml`'s twelve cases are twelve hits on
    the literal `"nora.000"`, so the trigger text is a fact about the corpus and
    not about any one case.
    """
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    if isinstance(doc, dict):
        rows = doc.get("cases") or []
        config = dict(doc.get("config") or {})
        policy = str(doc.get("policy") or "")
    else:
        rows, config, policy = doc, {}, ""
    return [_case(r, config, policy) for r in rows]


def _case(
    raw: Mapping[str, Any], config: Mapping[str, Any], policy: str = "",
) -> Case:
    for key in ("name", "entry", "expect"):
        if key not in raw:
            raise ValueError(f"case is missing {key!r}: {raw!r}")
    if raw["entry"] not in ENTRY_POINTS:
        raise ValueError(
            f"{raw['name']}: unknown entry point {raw['entry']!r}; "
            f"known: {sorted(ENTRY_POINTS)}"
        )
    return Case(
        name=str(raw["name"]),
        entry=raw["entry"],
        expect=raw["expect"],
        knows=dict(raw.get("knows") or {}),
        input=str(raw.get("input", "")),
        sample=int(raw.get("sample", 1)),
        category=str(raw.get("category", "")),
        safety=bool(raw.get("safety", False)),
        expected_output=str(raw.get("expected_output", "")),
        metadata=dict(raw.get("metadata") or {}),
        # The case's own `config:` wins over the corpus's, so one awkward case
        # can differ without a second file.
        config={**config, **(raw.get("config") or {})},
        policy=str(raw.get("policy") or policy),
        now=str(raw.get("now") or ""),
    )


#: What `nora.subject.Knowledge` can actually write into a real store.
#:
#: `conclusions` is NOT here, and that is a finding rather than an omission:
#: Honcho derives a conclusion from messages, so there is no way to state one
#: for a case except by substituting the client — which is the thing this
#: rebuild ends. Two cases in the participation corpus need it (issue #210's
#: pair) and they come back as ERROR until the memory tier has a seam that is
#: not a substitution.
#:
#: Fail-closed rather than ignore-unknown, because ignoring is the worse of the
#: two by a long way: a dropped `conclusions` turns a `-known` case into its own
#: `-unknown` twin, so the pair reports "the knowledge cut works" having cut
#: nothing. That is `seed_from_dict`'s own documented footgun, arriving by a
#: different road.
_KNOWABLE = {"transcript", "standing", "notes", "spacing_seconds"}


def _ms(ts: Any) -> int | None:
    """An ISO-8601 instant as ms, or `None`.

    Corpora that state their own timestamps do so in ISO. They are kept as
    STATED rather than shifted onto a recent base: these cases assert an
    `anchor_date` derived from the messages, so moving them would move the
    thing being asserted. Every other corpus leaves `ts` out and gets the
    recent-base rule, which is what an age-rendering prompt needs.
    """
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        return int(ts)
    return int(datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp() * 1000)


def _knowledge(raw: Mapping[str, Any]) -> Knowledge:
    """A case's `knows:` mapping, as the subject's own type.

    The translation lives HERE and not in the domain, which imports nothing
    from `nora`, and not in `nora.subject`, which knows nothing about YAML.
    """
    unknown = set(raw) - _KNOWABLE
    if unknown:
        raise ValueError(
            f"cannot seed {sorted(unknown)} into a real store; "
            f"knowable: {sorted(_KNOWABLE)}"
        )
    transcript = []
    for entry in raw.get("transcript") or []:
        if isinstance(entry, str):
            transcript.append(Message(body=entry))
            continue
        transcript.append(Message(
            body=entry["body"],
            name=entry.get("name", "Miles"),
            person=entry.get("person", ""),
            label=entry.get("id"),
            at_ms=_ms(entry.get("ts")),
            is_nora=bool(entry.get("is_nora", False)),
        ))
    notes = tuple(
        NoteSeed(
            title=n["title"],
            description=n["description"],
            body=n["body"],
            uncertainty=n.get("uncertainty", "open"),
            closing_condition=n.get("closing_condition", "the room settles it"),
            anchor_date=n.get("anchor_date", 20260901),
        )
        for n in (raw.get("notes") or [])
    )
    return Knowledge(
        transcript=tuple(transcript),
        standing=raw.get("standing"),
        notes=notes,
        spacing_seconds=int(raw.get("spacing_seconds", 60)),
    )


async def run_once(
    case: Case, *, database_url: str, overrides: dict[str, Any] | None = None,
) -> Outcome:
    """One draw. A fresh subject, a fresh workdir, nothing shared.

    Wrapped so that a failure to COMPOSE is an `Outcome` with an error rather
    than an exception out of the run: a subject that could not be built is not
    a Nora that behaved badly, and the two must not arrive at the grader
    wearing the same face.
    """
    port = ENTRY_POINTS[case.entry]
    workdir = Path(tempfile.mkdtemp(prefix="nora-case-"))
    started = time.monotonic()
    try:
        subject = await obtain(
            SubjectSpec(
                database_url=database_url,
                workdir=workdir,
                # A suite's room set, when it has one. `SubjectSpec` supplies a
                # valid closed default otherwise.
                **({"policy": case.policy} if case.policy else {}),
                # WHAT TIME IT IS, when the case says. Parsed here rather than
                # in the domain, which imports nothing from `nora` and knows
                # nothing about datetimes.
                **({"now": datetime.fromisoformat(case.now)} if case.now else {}),
                # The entry point's own requirements UNDER the case's, so a
                # case can still override anything and nothing has to remember
                # which switch its suite needs.
                overrides={**port.config, **case.config, **(overrides or {})},
            )
        )
    except Exception as exc:                        # noqa: BLE001 — see docstring
        shutil.rmtree(workdir, ignore_errors=True)
        return Outcome(admitted=None, error=f"could not obtain the subject: {exc}")

    try:
        written = await know(
            subject, _knowledge(case.knows),
            leave_room_for_arrival=port.delivers_input,
        )
        outcome = await port.drive(subject, case, now_ms=written.now_ms)
        return dataclasses.replace(
            outcome, elapsed_seconds=time.monotonic() - started,
        )
    except Exception as exc:                        # noqa: BLE001
        return Outcome(admitted=None, error=f"{type(exc).__name__}: {exc}")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


async def run(
    case: Case, *, database_url: str, overrides: dict[str, Any] | None = None,
) -> Verdict:
    """`case.sample` draws, judged together.

    Serial rather than gathered: each draw composes its own graph against the
    same database, and two turns writing the same room concurrently is not the
    thing under test.
    """
    outcomes = [
        await run_once(case, database_url=database_url, overrides=overrides)
        for _ in range(case.sample)
    ]
    return Verdict(case=case, outcomes=tuple(outcomes))


def now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)
