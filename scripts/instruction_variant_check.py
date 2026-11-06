"""Offline check for the Orient instruction variants P0-P3. Zero model tokens.

For each arm it prints the system-text and fixture-agenda character counts,
whether each of the nine safety sentences is present, whether P0 composes
byte-identically to the recorded baseline, whether P1 is a strict
deletion-only subsequence of P0 (with a positive control), and whether the
relation and tool names each arm uses are exactly the ones the tools define.

The fixture agenda is GENERATED (the way `agenda_format_check._positive_
control` builds its): placeholder names, rooms and ids only.
"""

from __future__ import annotations

import inspect
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from nora.domain.cognition_ground import Grounding, RecordItem
from nora.domain.cognition_session import AgendaItem, Room
from nora.domain.memory_graph import RELATION_RULES, Source
from nora.domain.stimuli import Stimulus
from nora.instructions.dreamer import DreamerInstructions
from nora.language.cognition_render import RenderBudget, render_agenda
from nora.language.comprehend_render import render_session_system
from nora.language.instruction_variants import (
    INSTRUCTION_VARIANTS,
    SAFETY_SENTENCES,
    admitted_relations,
    instruction_variant_text,
    listed_relations,
    nonadmitted_relation_usage,
    p1_subset_failures,
    safety_failures,
    safety_report,
)
from nora.obs.metrics import Metrics

ROOT = Path(__file__).resolve().parents[1]
P0_GOLDEN = ROOT / "tests" / "fixtures" / "cognition_system_p0.txt"
NOW = 1_800_000_000_000
HK = ZoneInfo("Asia/Hong_Kong")


def composed_p0() -> str:
    """The system text the session sends under P0: prelude + objective with
    the generated `<cognition_orient>` block + the register skill, through the
    same `render_session_system` the session calls."""
    store = DreamerInstructions(
        ROOT / "nora" / "dreamer", reload="startup", strict=True, metrics=Metrics())
    loaded = store.for_objective("cognition")
    skill = store.skill("cognition-register")
    if skill is None:
        raise RuntimeError("cognition-register skill missing from the store")
    skills = f"\n\n# Skill: {skill.name}\n\n{skill.instructions}\n"
    return render_session_system(loaded.text, skills, memory=True)


def fixture_agenda(variant: str) -> str:
    """One generated agenda in format A: two message items (one returning),
    a rollup, a note index, events and a graph block — enough to exercise the
    P3 block-description overrides."""
    room = Room("signal", "room-a")

    def stim(sid: int) -> Stimulus:
        return Stimulus(
            id=sid, unit="u", kind="message", item="message",
            ref=f"messages:{sid}", at_ms=NOW - sid * 60_000,
            recorded_at_ms=NOW, platform="signal",
            conversation_id="room-a", cues=())

    def rec(sid: int, mid: int, text: str, author: str, pid: str) -> RecordItem:
        return RecordItem(
            ref=f"messages:{sid}", kind="message", message_id=mid, room=room,
            author=author, at_ms=NOW - sid * 60_000, text=text, person_id=pid)

    def row(mid: int, body: str, pid: str, author: str, ts: int) -> dict:
        return {"id": mid, "body": body, "ts": ts, "person_id": pid,
                "author": author, "mentions": [],
                "source_key": ["message", str(mid)]}

    stims = {i: stim(i) for i in (1, 2, 3)}
    records = {
        "messages:1": rec(1, 4471, "hike saturday?", "Ming", "ming"),
        "messages:2": rec(2, 4472, "yes — dragon's back", "Ada", "ada"),
        "messages:3": rec(3, 4473, "minibus at 9", "Ming", "ming"),
    }
    items = (
        AgendaItem(kind="message", room=room, summary="3 new", cues=(),
                   surprise=1.0, stimulus_ids=(1, 2, 3)),
        AgendaItem(kind="message", room=room, summary="still open", cues=(),
                   surprise=0.5, stimulus_ids=(), strike=2),
    )
    mem = SimpleNamespace(
        sources=(
            Source(("message", "4471"), "record", "u", ("ming",), "hike saturday?"),
            Source(("message", "4472"), "record", "u", ("ada",), "yes — dragon's back"),
            Source(("message", "4473"), "record", "u", ("ming",), "minibus at 9"),
        ),
        snapshot={
            "record_rows": [
                row(4471, "hike saturday?", "ming", "Ming", NOW - 60_000),
                row(4472, "yes — dragon's back", "ada", "Ada", NOW - 120_000),
                row(4473, "minibus at 9", "ming", "Ming", NOW - 180_000),
            ],
            "messages": [], "context": [], "source_keys": {},
        },
    )
    from nora.domain.cognition_ground import EventLine, NoteLine
    grounding = Grounding(
        records=records, room_labels={"signal:room-a": "Room A"},
        notes=(NoteLine(id="n-0", title="Hike", description="which trail"),),
        events=(EventLine(id="e-1", room=room, title="Hike", when_ms=NOW,
                          venue="", status="proposed"),),
    )
    return render_agenda(
        items, stimuli=stims, grounding=grounding, now_ms=NOW, tz=HK,
        budget=RenderBudget(), memory_items={0: mem},
        graph="@work:lantern Lantern Game · state=current",
        instruction_variant=variant)


def session_tool_names() -> frozenset[str]:
    """The session's own verbs, read off the tool definitions in
    `CognitionSessionRunner._build_tools` — the names the model may call."""
    from nora.app.cognition_session import CognitionSessionRunner
    src = inspect.getsource(CognitionSessionRunner._build_tools)
    return frozenset(re.findall(r"async def (\w+)\(", src))


_VERBISH = re.compile(
    r"^(?:close|remind|"
    r"[a-z]+_(?:propose|create|revise|set_status|merge|read|move|cancel))$")


def name_check(variant: str, text: str) -> tuple[str, str]:
    """Two verdicts: (the arm's listed vocabulary is exactly the tools'
    admitted set, no name it uses is invented, and no non-admitted name is
    used AS a relation; the RELATION_RULES names it merely mentions that are
    not admitted — P0's known relates-by flaw)."""
    words = set(re.findall(r"\b[a-z][a-z_]{2,}\b", text))
    admitted = admitted_relations()
    tools = session_tool_names()
    bad: list[str] = []
    if listed_relations(text) != admitted:
        bad.append(
            f"listed vocabulary != admitted: missing "
            f"{sorted(admitted - listed_relations(text))}, extra "
            f"{sorted(listed_relations(text) - admitted)}")
    invented = sorted(
        w for w in words
        if _VERBISH.match(w) and w not in tools and w not in RELATION_RULES)
    if invented:
        bad.append(f"tool-shaped names not defined: {invented}")
    misused = sorted(nonadmitted_relation_usage(text))
    if misused:
        bad.append(f"non-admitted names used as relations: {misused}")
    extra = sorted(
        w for w in words
        if w in RELATION_RULES and w not in admitted)
    return ("FAIL " + "; ".join(bad)) if bad else "PASS", f"mentions={extra}"


def main() -> int:
    p0 = composed_p0()
    texts = {"P0": p0}
    for v in INSTRUCTION_VARIANTS[1:]:
        text = instruction_variant_text(v)
        if text is None:
            raise RuntimeError(f"variant {v} produced no text")
        texts[v] = text

    print("== sizes ==")
    for v in INSTRUCTION_VARIANTS:
        agenda = fixture_agenda(v)
        print(f"{v}: system={len(texts[v])} chars  agenda={len(agenda)} chars")

    print("\n== safety sentences ==")
    safety_missing: dict[str, list[str]] = {}
    for name, _ in SAFETY_SENTENCES:
        row = [safety_report(texts[v])[name] for v in INSTRUCTION_VARIANTS]
        print(f"{name}: " + "  ".join(
            f"{v}={r}" for v, r in zip(INSTRUCTION_VARIANTS, row, strict=True)))
    for v in INSTRUCTION_VARIANTS:
        safety_missing[v] = safety_failures(texts[v])
    absent = {v: m for v, m in safety_missing.items() if m}
    print("verdict: " + ("FAIL " + str(absent) if absent else "PASS"))

    print("\n== P0 byte-identical ==")
    golden = P0_GOLDEN.read_text() if P0_GOLDEN.exists() else None
    print("PASS" if golden == p0 else "FAIL (golden missing or differs)")

    print("\n== P1 line-subsequence of P0 ==")
    failures = p1_subset_failures(p0, texts["P1"])
    print("PASS" if not failures else "FAIL\n" + "\n".join(failures))

    print("\n== positive controls: each guard must fire ==")
    doctored = texts["P1"] + "\nThis line was never in P0.\n"
    print("inserted line breaks P1's subset: "
          + ("PASS" if p1_subset_failures(p0, doctored) else "FAIL (passed)"))
    stripped = texts["P1"].replace("A plan does not establish attendance.\n", "")
    if stripped == texts["P1"]:
        raise RuntimeError("positive control broken: sentence was not in P1")
    print("missing safety sentence detected: "
          + ("PASS" if safety_failures(stripped) else "FAIL (passed)"))
    verdict, _ = name_check(
        "P1", texts["P1"] + "\nuse relation `settles` and X -cancels-> Y here.\n")
    print("non-admitted relation use detected: "
          + ("PASS" if verdict.startswith("FAIL") else "FAIL (passed)"))

    print("\n== relation and tool names are the defined ones ==")
    name_results: dict[str, str] = {}
    for v in INSTRUCTION_VARIANTS:
        verdict, mentions = name_check(v, texts[v])
        name_results[v] = verdict
        print(f"{v}: {verdict}  ({mentions})")

    print("\n== P3 block-type overrides gloss the same field set ==")
    from nora.domain.block_types import BLOCK_TYPES, P3_BLOCK_TYPES
    drift = {
        n for n, t in P3_BLOCK_TYPES.items()
        if set(dict(t.annotations)) != set(dict(BLOCK_TYPES[n].annotations))
    }
    print("PASS" if not drift else f"FAIL annotation keys drifted: {sorted(drift)}")

    bad = (
        golden != p0 or bool(failures)
        or not p1_subset_failures(p0, doctored)
        or not safety_failures(stripped)
        or any(name_results[v] != "PASS" for v in INSTRUCTION_VARIANTS)
        or bool(absent) or bool(drift)
        or not name_check(
            "P1", texts["P1"] + "\nuse relation `settles`.\n")[0].startswith("FAIL")
    )
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
