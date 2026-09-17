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

import contextlib
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


#: What `nora.subject.Knowledge` can write into a real store.
#:
#: Fail-closed rather than ignore-unknown, because ignoring is much the worse
#: of the two: a dropped `conclusions` turns a `-known` case into its own
#: `-unknown` twin, so the pair reports "the knowledge cut works" having cut
#: nothing. That is `seed_from_dict`'s own documented footgun.
#:
#: `conclusions` was on the WRONG side of this list for half a day, refused on
#: the claim that Honcho only derives conclusions and cannot be told one. That
#: was an absence asserted without reading
#: `nora/infra/honcho/client.py:489` — `conclude()` is production's own write
#: lane, the one the `remember` tool uses, and it has existed since that tool
#: was built.
_KNOWABLE = {
    "transcript", "standing", "standing_age_hours", "notes",
    "spacing_seconds", "rooms", "conclusions",
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
        conclusions={
            str(peer): tuple(str(x) for x in (lines or ()))
            for peer, lines in (raw.get("conclusions") or {}).items()
        },
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
        return Outcome(
            admitted=None,
            error=f"could not obtain the subject: {_scrubbed(exc, database_url)}",
        )

    try:
        written = await know(
            subject, _knowledge(case.knows),
            leave_room_for_arrival=port.delivers_input,
        )
        outcome = await port.drive(subject, case, now_ms=written.now_ms)
        outcome = _check_the_corpus_assertions(case, outcome, written.rows)
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
        return Outcome(
            admitted=None,
            error=f"{type(exc).__name__}: {_scrubbed(exc, database_url)}",
        )
    finally:
        # TEARDOWN, the three things `nora/__main__.py`'s shutdown does. Without
        # it every draw leaks a connection pool and an LLM client and leaves any
        # spawned task running against the shared database while the next case
        # starts — 150 cases at five samples is 750 pools, and the failure
        # arrives as case errors rather than as an instrument fault.
        await _close(subject)
        shutil.rmtree(workdir, ignore_errors=True)


async def _close(subject: Any) -> None:
    """Best-effort teardown. Never raises: a case's RESULT must not depend on
    whether its cleanup worked, and a teardown error is not a reading."""
    for shut in (
        subject.deps.background.cancel_all(),
        subject.deps.audit.aclose(),
        subject.deps.client.close(),
    ):
        with contextlib.suppress(Exception):
            await shut


def _scrubbed(exc: BaseException, database_url: str) -> str:
    """An exception's text, with the database URL taken out.

    `report.render` prints these verbatim, and a composition failure raised out
    of `run_migrations` or asyncpg carries the DSN — password included — into a
    report somebody pastes. The URL is known here, so removing it is exact
    rather than a pattern guess at what a secret looks like.
    """
    text = str(exc)
    return text.replace(database_url, "<database-url>") if database_url else text


def _check_the_corpus_assertions(
    case: Case, outcome: Outcome, rows: Mapping[Any, int],
) -> Outcome:
    """`must_cite`, `anchor_date`, `creates`, `updates` — the four the
    note-candidates corpus states and nothing checked.

    `rows` translates a case's own message LABEL into the id the store
    assigned. That translation is the whole reason `Message.label` and
    `Written.rows` exist, and it was being discarded: a citation checked
    against a renumbered store is checked against nothing.

    Skipped entirely for an outcome that already carries an error — an un-run
    case must not acquire a second reason to fail.
    """
    meta = case.metadata
    if outcome.error is not None or not meta:
        return outcome
    failed: list[str] = []

    creates = [t for kind, t in outcome.changed if kind == "create"]
    updates = [t for kind, t in outcome.changed if kind == "update"]
    if meta.get("creates") is not None and len(creates) != int(meta["creates"]):
        failed.append(f"creates: expected {meta['creates']}, got {len(creates)}")
    if meta.get("updates") and meta["updates"] not in updates:
        failed.append(f"updates: {meta['updates']!r} was not revised; got {updates}")
    # A `create` case asserts that NOTHING standing was revised — the corpus
    # header says so, and a pass that creates the right note while quietly
    # rewriting three others is not the behaviour it describes.
    if case.expect == "create" and updates:
        failed.append(f"create case also revised {updates}")

    if meta.get("anchor_date") is not None and outcome.staged:
        want = int(meta["anchor_date"])
        got = [a for _t, a, _m, _s in outcome.staged]
        if want not in got:
            failed.append(f"anchor_date: expected {want}, staged {got}")
    if meta.get("must_cite"):
        want_ids = {rows.get(label) for label in meta["must_cite"]}
        cited: set[int] = set()
        for _t, _a, message_ids, _s in outcome.staged:
            cited |= set(message_ids)
        missing = {i for i in want_ids if i is not None} - cited
        if None in want_ids:
            failed.append(
                f"must_cite names a message the case did not seed: "
                f"{meta['must_cite']}"
            )
        elif missing:
            failed.append(f"must_cite: {sorted(missing)} not among {sorted(cited)}")
    if meta.get("must_not_cite"):
        forbidden = {rows.get(label) for label in meta["must_not_cite"]}
        cited = set()
        for _t, _a, message_ids, _s in outcome.staged:
            cited |= set(message_ids)
        hit = {i for i in forbidden if i is not None} & cited
        if hit:
            failed.append(f"must_not_cite: cited {sorted(hit)}")

    return dataclasses.replace(outcome, checks_failed=tuple(failed))


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
