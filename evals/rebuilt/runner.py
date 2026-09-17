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

import shutil
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from nora.subject import Knowledge, Message, NoteSeed, SubjectSpec, know, obtain

from .domain import Case, Outcome, Verdict
from .entry import ENTRY_POINTS


def load(path: Path) -> list[Case]:
    """A corpus file, as cases. DATA — no code runs to add a suite."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    return [_case(r) for r in raw]


def _case(raw: Mapping[str, Any]) -> Case:
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
    )


def _knowledge(raw: Mapping[str, Any]) -> Knowledge:
    """A case's `knows:` mapping, as the subject's own type.

    The translation lives HERE and not in the domain, which imports nothing
    from `nora`, and not in `nora.subject`, which knows nothing about YAML.
    """
    transcript = []
    for entry in raw.get("transcript") or []:
        if isinstance(entry, str):
            transcript.append(Message(body=entry))
        else:
            transcript.append(
                Message(body=entry["body"], name=entry.get("name", "Miles"))
            )
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
    try:
        subject = await obtain(
            SubjectSpec(
                database_url=database_url,
                workdir=workdir,
                # The entry point's own requirements UNDER the case's, so a
                # case can still override anything and nothing has to remember
                # which switch its suite needs.
                overrides={**port.config, **(overrides or {})},
            )
        )
    except Exception as exc:                        # noqa: BLE001 — see docstring
        shutil.rmtree(workdir, ignore_errors=True)
        return Outcome(admitted=None, error=f"could not obtain the subject: {exc}")

    try:
        now_ms = await know(subject, _knowledge(case.knows))
        return await port.drive(subject, case, now_ms=now_ms)
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
