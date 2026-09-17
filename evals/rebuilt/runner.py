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

from nora.subject import (
    Knowledge,
    Message,
    NoteSeed,
    RoomSeed,
    SubjectSpec,
    know,
    obtain,
    room_for,
)

from .domain import Case, Outcome, Verdict
from .entry import ENTRY_POINTS
from .grading import Judge, criteria_for


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


#: The trigger a corpus's `{trigger}` placeholders render against. A corpus
#: states it in `config:` like any other setting; this is the fallback for one
#: that does not.
_DEFAULT_TRIGGER = "nora"


def substitute_trigger(text: str, trigger: str) -> str:
    """Render `{trigger}` / `{trigger_split}` against the CONFIGURED trigger.

    Carried across from `evals/run.py:510` because dropping it broke ten cases
    silently and in both directions: `near_miss.yaml`'s four cases assert that
    trigger-SHAPED text does not fire, and a literal `{trigger}` does not fire
    either — so they PASSED while asserting nothing. `being_contradicted.yaml`'s
    six were never admitted at all and FAILED 6/6, saying nothing about R-83.

    The corpus holds RULES about `nora.tag.is_tagged`'s boundaries, not
    literals. Hardcoding one trigger couples them to a value the operator owns:
    when the plain `nora` trigger was re-enabled on 2026-08-26, two hardcoded
    `nora.000` cases became real tags and the gate failed pointing at the model
    instead of at the stale corpus.

    `{trigger_split}` is the same trigger with a space after its first
    character — how "broken by whitespace" is expressed for an arbitrary
    trigger.
    """
    split = f"{trigger[:1]} {trigger[1:]}" if len(trigger) > 1 else trigger
    return text.replace("{trigger_split}", split).replace("{trigger}", trigger)


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
    merged = {**config, **(raw.get("config") or {})}
    trigger = str(merged.get("engagement.trigger.text") or _DEFAULT_TRIGGER)
    return Case(
        name=str(raw["name"]),
        entry=raw["entry"],
        expect=raw["expect"],
        knows=dict(raw.get("knows") or {}),
        input=substitute_trigger(str(raw.get("input", "")), trigger),
        sample=int(raw.get("sample", 1)),
        category=str(raw.get("category", "")),
        safety=bool(raw.get("safety", False)),
        expected_output=str(raw.get("expected_output", "")),
        metadata=dict(raw.get("metadata") or {}),
        # The case's own `config:` wins over the corpus's, so one awkward case
        # can differ without a second file.
        config=merged,
        policy=str(raw.get("policy") or policy),
        room=list(raw.get("room") or ()),
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
_KNOWABLE = {
    "transcript", "standing", "standing_age_hours", "notes",
    "spacing_seconds", "rooms",
}

#: What one transcript ROW may state. Same fail-closed rule, one level in.
_MESSAGE_KEYS = {"body", "name", "person", "id", "ts", "is_nora", "media"}


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
        unknown = set(entry) - _MESSAGE_KEYS
        if unknown:
            # FAIL-CLOSED ON A TRANSCRIPT ROW TOO. Top-level `knows` keys were
            # already checked; row keys were not, and that asymmetry is how
            # `media: true` was dropped from five rows with nothing reporting
            # it. A corpus stating something the runner ignores is a corpus
            # asserting more than is measured.
            raise ValueError(
                f"cannot seed transcript keys {sorted(unknown)}; "
                f"knowable: {sorted(_MESSAGE_KEYS)}"
            )
        transcript.append(Message(
            body=entry["body"],
            name=entry.get("name", "Miles"),
            person=entry.get("person", ""),
            label=entry.get("id"),
            at_ms=_ms(entry.get("ts")),
            is_nora=bool(entry.get("is_nora", False)),
            media=bool(entry.get("media", False)),
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
    rooms = tuple(
        RoomSeed(
            conversation_id=str(r["conversation_id"]),
            platform=str(r.get("platform", "signal")),
            label=str(r.get("label", "")),
            home=bool(r.get("home", False)),
            transcript=tuple(
                Message(body=m["body"], name=m.get("name", "Miles"))
                if isinstance(m, dict) else Message(body=m)
                for m in (r.get("transcript") or ())
            ),
            standing=r.get("standing"),
            standing_age_hours=r.get("standing_age_hours"),
        )
        for r in (raw.get("rooms") or ())
    )
    return Knowledge(
        transcript=tuple(transcript),
        standing=raw.get("standing"),
        standing_age_hours=raw.get("standing_age_hours"),
        notes=notes,
        rooms=rooms,
        spacing_seconds=int(raw.get("spacing_seconds", 60)),
    )


async def run_once(
    case: Case,
    *,
    database_url: str,
    overrides: dict[str, Any] | None = None,
    judge: Judge | None = None,
) -> Outcome:
    """One draw. A fresh subject, a fresh workdir, nothing shared.

    Wrapped so that a failure to COMPOSE is an `Outcome` with an error rather
    than an exception out of the run: a subject that could not be built is not
    a Nora that behaved badly, and the two must not arrive at the grader
    wearing the same face.
    """
    port = ENTRY_POINTS[case.entry]
    room = (
        (case.room[0], case.room[1]) if len(case.room) == 2
        else room_for(case.name)
    )
    workdir = Path(tempfile.mkdtemp(prefix="nora-case-"))
    started = time.monotonic()
    try:
        subject = await obtain(
            SubjectSpec(
                database_url=database_url,
                workdir=workdir,
                # A ROOM OF ITS OWN. Cases share one database, so one room is
                # one transcript: the gate reads the newest 40 rows and the
                # Dreamer the oldest 64, so without this a case is graded on
                # other cases' messages — or, past 64 rows, on a batch its own
                # input never reached. Measured at three cases: the third's own
                # window was one row and the gate was shown five.
                # A suite that NAMES its room means it — the proactive corpora
                # mark a workspace's home room and the pass picks between
                # rooms. Everything else gets one derived from the case name,
                # which is what isolates cases sharing a database.
                room=room,
                # A suite's room set, when it has one. `SubjectSpec` supplies a
                # valid closed default otherwise.
                # A suite's policy is a TEMPLATE: `{platform}` and `{room}`
                # are filled with the room this case actually runs in.
                #
                # Without that a corpus stating trust flags — the proactive
                # rooms need `proactive = true` and `home_room = true` — would
                # have to hardcode a room id, and every case in the file would
                # share it, which is the cross-case contamination this layer
                # just fixed. The flags belong to the suite; the room belongs
                # to the case.
                **(
                    {"policy": case.policy.format(
                        platform=room[0], room=room[1],
                    )}
                    if case.policy else {}
                ),
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
        # JUDGED HERE, while the subject is alive, because the judge needs the
        # doctrine she actually ran on — not a copy read off disk, which is the
        # same file only until somebody edits it mid-run. The prompt is
        # bind-mounted and hot-reloads; that is the whole reason the gate suite
        # exists.
        if judge is not None and outcome.said and outcome.error is None:
            judgement = await judge.grade(
                said="\n".join(outcome.said),
                asked=case.input,
                doctrine=subject.deps.doctrine.doctrine_text,
                source=_source(case),
                extra=criteria_for(tuple(case.metadata.get("criteria") or ())),
            )
            outcome = dataclasses.replace(
                outcome,
                judged=judgement.verdict,
                # A judge that could not be reached is an ERROR about the
                # INSTRUMENT and is reported as one, rather than as a REVIEW
                # nobody can distinguish from a judge that answered vaguely.
                error=judgement.error,
            )
        return dataclasses.replace(
            outcome, elapsed_seconds=time.monotonic() - started,
        )
    except Exception as exc:                        # noqa: BLE001
        return Outcome(admitted=None, error=f"{type(exc).__name__}: {exc}")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _source(case: Case) -> str | None:
    """The SOURCE the reply had to ground against — the window she read.

    Without it the judge grades grounding BLIND: it cannot tell "correctly named
    the top entry" from "fabricated which entry is the asker", because both read
    as fluent confident text (DEC-75). `None` for a case with no transcript,
    which is the injection corpora's shape and is why SAFETY is the criterion
    that binds — it is the one meaningful with or without source data.
    """
    rows = (case.knows or {}).get("transcript") or []
    if not rows:
        return None
    return "\n".join(
        r["body"] if isinstance(r, dict) else str(r) for r in rows
    )


async def run(
    case: Case,
    *,
    database_url: str,
    overrides: dict[str, Any] | None = None,
    judge: Judge | None = None,
) -> Verdict:
    """`case.sample` draws, judged together.

    Serial rather than gathered: each draw composes its own graph against the
    same database, and two turns writing the same room concurrently is not the
    thing under test.
    """
    outcomes = [
        await run_once(
            case, database_url=database_url, overrides=overrides, judge=judge,
        )
        for _ in range(case.sample)
    ]
    return Verdict(case=case, outcomes=tuple(outcomes))


def now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)
