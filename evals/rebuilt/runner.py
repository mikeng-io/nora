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

from evals.backbone import _close, _knowledge
from nora.subject import SubjectSpec, know, obtain, room_for, workspace_for

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


#: `_knowledge` (a case's `knows:` mapping, as `nora.subject.Knowledge`) and
#: `_close` (best-effort subject teardown) MOVED to `evals.backbone`,
#: 2026-09-20 — they are layer 3 (case isolation and seeding, which no SDK
#: supplies), and this module is layer 1, on its way to being deleted once
#: the corpora are ported onto the new spine (`GOAL-2-backbone.md`). The
#: dependency now runs the direction that survives the delete: this OLD
#: harness imports from the NEW one, not the other way round.


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
    # A WORKSPACE OF ITS OWN, for the reason the room needed one: every
    # accumulating table the Dreamer reads is workspace-keyed, so without this
    # a case's notes are still standing when the next case runs. Both the
    # config and the policy are rewritten, because the two must agree or the
    # pass writes where nothing reads.
    stated = str((case.config or {}).get("memory.workspace_id") or "nora")
    workspace = workspace_for(case.name, stated)
    case_config = {**case.config, "memory.workspace_id": workspace}
    case_policy = case.policy.replace(
        f'workspace = "{stated}"', f'workspace = "{workspace}"',
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
                    {"policy": case_policy.format(
                        platform=room[0], room=room[1],
                    )}
                    if case_policy else {}
                ),
                # WHAT TIME IT IS, when the case says. Parsed here rather than
                # in the domain, which imports nothing from `nora` and knows
                # nothing about datetimes.
                **({"now": datetime.fromisoformat(case.now)} if case.now else {}),
                # The entry point's own requirements UNDER the case's, so a
                # case can still override anything and nothing has to remember
                # which switch its suite needs.
                overrides={**port.config, **case_config, **(overrides or {})},
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

    if meta.get("anchor_date") is not None and case.expect in ("create", "update"):
        # NOT `and outcome.staged`: that made it fail OPEN — a pass that staged
        # nothing skipped the assertion instead of missing it. Guarded on the
        # EXPECTATION instead, so only a case that should have staged something
        # is held to it.
        want = int(meta["anchor_date"])
        got = [a for _t, a, _m, _s in outcome.staged]
        if want not in got:
            failed.append(f"anchor_date: expected {want}, staged {got}")
    # `must_cite` MEANS TWO DIFFERENT THINGS, by corpus, and applying one
    # rule to both is a defect a live run found:
    #
    #   note-candidates  INTEGERS — message labels the staged candidate must
    #                    have cited, translated through `rows` into the ids the
    #                    store assigned.
    #   proactive_act    STRINGS — substrings the reply must contain, "a reply
    #                    reached the subject" (`evals/proactive.py:281`), with
    #                    `must_not_cite` the negative half (issue #226): a reply
    #                    carrying her own bookkeeping's vocabulary.
    #
    # Dispatch on the TYPE rather than on the corpus name, so a corpus cannot
    # get the wrong rule by being renamed.
    #
    # The string form is `substring in text`, which is what the corpus means
    # and what the old driver did. It cannot tell a mention from a use — #182's
    # finding — so it says a reply REACHED the subject and never that it
    # reached it rightly. That is the claim it is allowed to make.
    said = " ".join(outcome.said)
    for key, negate in (("must_cite", False), ("must_not_cite", True)):
        wanted = meta.get(key)
        if not wanted:
            continue
        if all(isinstance(w, int) for w in wanted):
            cited: set[int] = set()
            for _t, _a, message_ids, _s in outcome.staged:
                cited |= set(message_ids)
            ids = {rows.get(label) for label in wanted}
            if None in ids:
                failed.append(
                    f"{key} names a message the case did not seed: {wanted}"
                )
                continue
            hit = {i for i in ids if i is not None} & cited
            missing = {i for i in ids if i is not None} - cited
            if negate and hit:
                failed.append(f"{key}: cited {sorted(hit)}")
            elif not negate and missing:
                failed.append(f"{key}: {sorted(missing)} not among {sorted(cited)}")
        else:
            # CASE-FOLDED, as the driver this replaces did deliberately:
            # «Note» must not pass where «note» fails. Lost on the move.
            folded = said.casefold()
            present = [w for w in wanted if str(w).casefold() in folded]
            absent = [w for w in wanted if str(w).casefold() not in folded]
            if negate and present:
                failed.append(f"{key}: the reply contains {present}")
            elif not negate and absent:
                failed.append(f"{key}: the reply does not contain {absent}")

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
