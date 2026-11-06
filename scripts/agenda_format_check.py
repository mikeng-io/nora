"""Offline render check for the agenda-format experiment. Zero model tokens.

Renders every candidate (A/B/C/D) for the frozen smoke sample — the same
wakes `memory_graph_spike --smoke` replays — and reports, per candidate:
prompt size, shown handle/citation counts, whether every shown citation
number and `@kind:id` handle resolves through the emitted custody, and the
negative checks (a budget-clipped tail and an unshown row do NOT resolve).
Any candidate that fails is reported FAIL and must not be replayed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nora.config import load
from nora.domain.cognition_ground import Grounding  # noqa: F401 - same fixture
from nora.infra.sql.cognition_replay import recorded_wake
from nora.infra.sql.engine import create_sql_engine
from nora.language.cognition_render import AGENDA_FORMATS, RenderBudget
from nora.language.computer_render import estimate_tokens
from nora.persons import load_persons
from nora.policy import load_policy
from nora.secret import credentials
from nora.subject import SubjectSpec, obtain, sandbox_as_of
from nora.subject.sandbox_index import EMBEDDER_FROM_HOST
from scripts.memory_graph_spike import (
    SMOKE_MARKS,
    index_replay_sandbox,
    rows,
    snapshot_only,
    stratify,
)

_N = re.compile(r'<m n="(\d+)"')
_HANDLE = re.compile(r"@([a-z_]+):([^\s\";<>)]]+)")
_EARLIER = re.compile(r'earlier_not_shown="(\d+)"[^>]*earlier_range="(\d+) to (\d+)"')


def _catalogue(custody: dict[str, Any]) -> dict[int, tuple[str, str]]:
    return {int(n): tuple(k) for n, k in custody.get("catalogue", {}).items()}


def _check(agenda: str, custody: dict[str, Any]) -> dict[str, Any]:
    """Every shown number and handle resolves; nothing unshown does."""
    catalogue = _catalogue(custody)
    shown_sources = {tuple(s) for s in custody.get("shown_sources", [])}
    shown_persons = set(custody.get("shown_persons", []))
    shown_nodes = {tuple(k) for k in custody.get("shown_node_keys", [])}
    ns = [int(m) for m in _N.findall(agenda)]
    unresolved_n = [
        n for n in ns
        if catalogue.get(n) not in shown_sources and ("message", str(n)) not in shown_sources
    ]
    unresolved_handles = []
    for kind, ref in _HANDLE.findall(agenda):
        if kind == "person" and ref in shown_persons:
            continue
        if (kind, ref) in shown_nodes or (kind, ref) in shown_sources:
            continue
        unresolved_handles.append(f"{kind}:{ref}")
    # Negative: a citation number never rendered must not resolve.
    fabricated = (max(ns) + 1) if ns else 1
    fabricated_resolves = (
        catalogue.get(fabricated) in shown_sources
        or ("message", str(fabricated)) in shown_sources
    )
    # Negative: the clipped tail. `earlier_range` is the FIRST and LAST row of
    # the dropped set — the bound rows themselves, not every number between
    # them (numbers in the interval may be shown rows, so only the bounds are
    # tested).
    clipped_resolves = []
    for _c, a, b in _EARLIER.findall(agenda):
        for n in {int(a), int(b)}:
            if catalogue.get(n) in shown_sources or (
                ("message", str(n)) in shown_sources
            ):
                clipped_resolves.append(n)
    return {
        "m_rows": len(ns),
        "handles": len(_HANDLE.findall(agenda)),
        "unresolved_n": unresolved_n,
        "unresolved_handles": unresolved_handles,
        "fabricated_resolves": fabricated_resolves,
        "clipped_resolves": clipped_resolves,
        "clipped_items": sum(1 for _ in _EARLIER.findall(agenda)),
    }


def _positive_control() -> dict[str, str]:
    """A generated fixture (no production rows) that MUST pass the same
    custody check: renders every candidate, registers the shown rows, and
    confirms each shown citation resolves while a fabricated and a clipped
    bound never do. If this fails the instrument is suspect, not the data."""
    from types import SimpleNamespace
    from zoneinfo import ZoneInfo

    from nora.agent.fetch_watch import GraphFetched
    from nora.app.cognition_memory import WakeMemory
    from nora.app.comprehend import proposal_scope
    from nora.domain.cognition_ground import Grounding, RecordItem
    from nora.domain.cognition_session import AgendaItem, Room
    from nora.domain.memory_graph import Source
    from nora.domain.stimuli import Stimulus
    from nora.language.cognition_render import render_agenda

    hk, now = ZoneInfo("Asia/Hong_Kong"), 1_800_000_000_000
    room = Room("signal", "room-a")

    def stim(sid):
        return Stimulus(
            id=sid, unit="u", kind="message", item="message",
            ref=f"messages:{sid}", at_ms=now - sid * 60_000,
            recorded_at_ms=now, platform="signal",
            conversation_id="room-a", cues=())

    def rec(sid, mid, text, author, pid):
        return RecordItem(
            ref=f"messages:{sid}", kind="message", message_id=mid, room=room,
            author=author, at_ms=now - sid * 60_000, text=text, person_id=pid)

    def row(mid, body, pid, author, ts):
        return {"id": mid, "body": body, "ts": ts, "person_id": pid,
                "author": author, "mentions": [],
                "source_key": ["message", str(mid)]}

    out = {}
    for fmt in AGENDA_FORMATS:
        stims = {i: stim(i) for i in (1, 2, 3)}
        records = {
            "messages:1": rec(1, 4471, "first words", "Ming", "ming"),
            "messages:2": rec(2, 4472, "second words", "Ada", "ada"),
            "messages:3": rec(3, 4473, "third words", "Ming", "ming"),
        }
        item = AgendaItem(kind="message", room=room, summary="3 new", cues=(),
                          surprise=1.0, stimulus_ids=(1, 2, 3))
        mem = SimpleNamespace(
            sources=(
                Source(("message", "4471"), "record", "u", ("ming",), "first words"),
                Source(("message", "4472"), "record", "u", ("ada",), "second words"),
                Source(("message", "4473"), "record", "u", ("ming",), "third words"),
            ),
            snapshot={
                "record_rows": [
                    row(4471, "first words", "ming", "Ming", now - 60_000),
                    row(4472, "second words", "ada", "Ada", now - 120_000),
                    row(4473, "third words", "ming", "Ming", now - 180_000),
                ],
                "messages": [], "context": [], "source_keys": {},
            },
        )
        try:
            text = render_agenda(
                (item,), stimuli=stims,
                grounding=Grounding(records=records,
                                    room_labels={"signal:room-a": "Room A"}),
                now_ms=now, tz=hk, budget=RenderBudget(),
                memory_items={0: mem}, agenda_format=fmt)
            state = SimpleNamespace(memory_items={0: mem},
                                    graph_fetched=GraphFetched())
            WakeMemory(graph=None, records=None,
                       metrics=None).register_shown(state)
            scope = proposal_scope([mem], state.graph_fetched)
            ns = _N.findall(text)
            shown_ok = all(
                int(n) in scope.catalogue
                and scope.catalogue[int(n)] in state.graph_fetched.sources
                for n in ns
            )
            fabricated = (max((int(n) for n in ns), default=0) + 1)
            fab_ok = (
                fabricated not in scope.catalogue
                and ("message", str(fabricated)) not in state.graph_fetched.sources
            )
            out[fmt] = "PASS" if ns and shown_ok and fab_ok else "FAIL"
        except Exception:  # noqa: BLE001 - fail closed
            out[fmt] = "FAIL"
    return out


async def run(args: argparse.Namespace) -> dict[str, Any]:
    control = _positive_control()
    cfg = load(args.config)
    production = credentials()[cfg.store.database_url_env]
    snapshot_only(args.database_url, production, args.snapshot)
    policy, persons = load_policy(args.policy), load_persons(args.identity)
    engine = create_sql_engine(args.database_url)
    report: dict[str, Any] = {"formats": {}, "seed": args.seed}
    try:
        wakes = await rows(engine, "SELECT id,at_ms FROM cognition_wake ORDER BY at_ms,id")
        candidates = []
        for wake in wakes:
            _, view = await recorded_wake(engine, wake["id"])
            strata = []
            for stimulus in view.pending:
                if stimulus.kind == "message":
                    message = await rows(
                        engine, "SELECT body FROM messages WHERE id=:id",
                        {"id": int(stimulus.ref.split(":")[-1])},
                    )
                    body = str(message[0]["body"]) if message else ""
                    strata.append(
                        "link" if "http://" in body or "https://" in body
                        else "attachment"
                        if "[image " in body or "[audio " in body
                        or "[video " in body or "[file " in body
                        else "text"
                    )
            kinds = {s.kind for s in view.pending}
            if "social" in kinds:
                strata.append("social")
            if "rollup" in kinds and len(kinds) > 1:
                strata.append("multi_signal")
            candidates.append({**wake, "strata": sorted(set(strata))})
        sample = stratify(candidates, 1, args.seed)
        selected = {w["id"]: w for group in sample.values() for w in group}
        for wake in candidates:
            if len(selected) >= SMOKE_MARKS["sample"]:
                break
            selected.setdefault(wake["id"], wake)
        report["wakes"] = len(selected)
        per_format = {
            f: {"sessions": 0, "chars": 0, "tokens": 0, "m_rows": 0, "handles": 0,
                "unresolved_n": [], "unresolved_handles": [],
                "fabricated_resolves": False, "clipped_resolves": [],
                "clipped_items": 0, "person_rows": 0, "node_keys": 0,
                "relation_ids": 0, "errors": 0}
            for f in AGENDA_FORMATS
        }
        for wake in selected.values():
            replay_ms, view = await recorded_wake(engine, wake["id"])
            at = datetime.fromtimestamp(replay_ms / 1000, UTC)
            room = next((c for c in policy.rooms() if c.unit == view.unit), None)
            if room is None:
                for f in per_format.values():
                    f["errors"] += 1
                continue
            async with sandbox_as_of(args.database_url, at) as (url, _):
                from tempfile import TemporaryDirectory
                with TemporaryDirectory(prefix="nora-format-check-") as workdir:
                    subject = await obtain(
                        SubjectSpec(
                            database_url=url,
                            workdir=Path(workdir),
                            room=(room.platform, room.primary_id),
                            now=at,
                            identity=args.identity.read_text(),
                            policy=args.policy.read_text(),
                            overrides={
                                "cognition.enabled": True,
                                "memory.graph.enabled": True,
                                "memory.graph.comprehend.enabled": True,
                                "memory.index.embedder_url": EMBEDDER_FROM_HOST,
                                "llm": cfg.llm.model_dump(),
                                "providers": {k: v.model_dump() for k, v in cfg.providers.items()},
                                "model_routes": {
                                    k: v.model_dump() for k, v in cfg.model_routes.items()
                                },
                                "cognition": {**cfg.cognition.model_dump(), "enabled": True},
                            },
                        )
                    )
                    try:
                        runner = subject.deps.cognition_runner
                        if runner is None:
                            raise ValueError("snapshot session unavailable")
                        await index_replay_sandbox(subject, source_url=args.database_url)
                        for fmt in AGENDA_FORMATS:
                            f = per_format[fmt]
                            try:
                                frunner = runner.with_collaborators(agenda_format=fmt)
                                inspection = await frunner.inspect(view=view, now_ms=replay_ms)
                                agenda = inspection["agenda"]
                                got = _check(agenda, inspection.get("custody") or {})
                                f["sessions"] += 1
                                f["chars"] += len(agenda)
                                f["tokens"] += estimate_tokens(agenda)
                                f["m_rows"] += got["m_rows"]
                                f["handles"] += got["handles"]
                                f["unresolved_n"] += got["unresolved_n"]
                                f["unresolved_handles"] += got["unresolved_handles"]
                                f["fabricated_resolves"] |= got["fabricated_resolves"]
                                custody = inspection.get("custody") or {}
                                f["person_rows"] += len(custody.get("shown_persons", []))
                                f["node_keys"] += len(custody.get("shown_node_keys", []))
                                f["relation_ids"] += len(
                                    custody.get("shown_relation_ids", []))
                                # The clipped tail: force the budget so older
                                # rows fall out; their bound rows must not
                                # resolve (the range is first..last of the
                                # dropped set, not every number in it).
                                clipped = await frunner.with_collaborators(
                                    render_budget=RenderBudget(
                                        record_chars=300, min_item_chars=100,
                                    ),
                                ).inspect(view=view, now_ms=replay_ms)
                                clip_check = _check(
                                    clipped["agenda"], clipped.get("custody") or {})
                                f["clipped_items"] += clip_check["clipped_items"]
                                f["clipped_resolves"] += clip_check["clipped_resolves"]
                            except Exception:  # noqa: BLE001 - keep the run, mark the format
                                f["errors"] += 1
                    finally:
                        from evals.backbone import _close
                        await _close(subject)
        for fmt, f in per_format.items():
            f["status"] = (
                "PASS"
                if f["sessions"] == len(selected)
                and not f["errors"]
                and not f["unresolved_n"]
                and not f["unresolved_handles"]
                and not f["fabricated_resolves"]
                and not f["clipped_resolves"]
                and control.get(fmt) == "PASS"
                else "FAIL"
            )
        report["formats"] = per_format
        report["control"] = control
        return report
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--identity", type=Path, required=True)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--snapshot", action="store_true")
    parser.add_argument("--seed", type=int, default=20261029)
    args = parser.parse_args()
    try:
        report = asyncio.run(run(args))
    except Exception:  # noqa: BLE001 - fail closed without private exception text
        print('{"status":"ERROR","reason":"format check failed; inspect private host state"}')
        return 2
    print(f"{'fmt':<4} {'ctrl':>4} {'sessions':>8} {'chars':>8} {'~tokens':>8} {'m_rows':>6} "
          f"{'handles':>7} {'unres_n':>7} {'unres_h':>7} {'fabr':>4} {'clip':>4} "
          f"{'clips':>5} {'pers':>4} {'nodes':>5} {'rels':>4} {'verdict'}")
    for fmt, f in report["formats"].items():
        print(
            f"{fmt:<4} {report['control'].get(fmt,'?'):>4} {f['sessions']:>8} "
            f"{f['chars']:>8} {f['tokens']:>8} {f['m_rows']:>6} {f['handles']:>7} "
            f"{len(f['unresolved_n']):>7} {len(f['unresolved_handles']):>7} "
            f"{'Y' if f['fabricated_resolves'] else 'n':>4} "
            f"{len(f['clipped_resolves']):>4} {f['clipped_items']:>5} "
            f"{f['person_rows']:>4} {f['node_keys']:>5} {f['relation_ids']:>4} "
            f"{f['status']}"
        )
    print(json.dumps({"wakes": report["wakes"], "seed": report["seed"],
                      "control": report["control"],
                      "status": {k: v["status"] for k, v in report["formats"].items()}},
                     sort_keys=True))
    return 0 if all(f["status"] == "PASS" for f in report["formats"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
