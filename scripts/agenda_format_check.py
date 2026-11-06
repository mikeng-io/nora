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
    return {
        "m_rows": len(ns),
        "handles": len(_HANDLE.findall(agenda)),
        "unresolved_n": unresolved_n,
        "unresolved_handles": unresolved_handles,
        "fabricated_resolves": fabricated_resolves,
        "clipped": [
            (int(a), int(b)) for _c, a, b in _EARLIER.findall(agenda)
        ],
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
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
                "clip_exercised": False, "errors": 0}
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
                                # The clipped tail: force the budget so older
                                # rows fall out; their numbers must not resolve.
                                clipped = await frunner.with_collaborators(
                                    render_budget=RenderBudget(
                                        record_chars=300, min_item_chars=100,
                                    ),
                                ).inspect(view=view, now_ms=replay_ms)
                                ccust = _catalogue(clipped.get("custody") or {})
                                cshown = {
                                    tuple(s)
                                    for s in (clipped.get("custody") or {}).get(
                                        "shown_sources", [])
                                }
                                for lo, hi in _check(
                                    clipped["agenda"], clipped.get("custody") or {}
                                )["clipped"]:
                                    f["clip_exercised"] = True
                                    for n in range(lo, hi + 1):
                                        if ccust.get(n) in cshown or (
                                            ("message", str(n)) in cshown
                                        ):
                                            f["clipped_resolves"].append(n)
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
                else "FAIL"
            )
        report["formats"] = per_format
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
    print(f"{'fmt':<4} {'sessions':>8} {'chars':>8} {'~tokens':>8} {'m_rows':>6} "
          f"{'handles':>7} {'unres_n':>7} {'unres_h':>7} {'fabr':>4} {'clip':>4} {'verdict'}")
    for fmt, f in report["formats"].items():
        print(
            f"{fmt:<4} {f['sessions']:>8} {f['chars']:>8} {f['tokens']:>8} "
            f"{f['m_rows']:>6} {f['handles']:>7} {len(f['unresolved_n']):>7} "
            f"{len(f['unresolved_handles']):>7} "
            f"{'Y' if f['fabricated_resolves'] else 'n':>4} "
            f"{len(f['clipped_resolves']):>4} {f['status']}"
        )
    print(json.dumps({"wakes": report["wakes"], "seed": report["seed"],
                      "status": {k: v["status"] for k, v in report["formats"].items()}},
                     sort_keys=True))
    return 0 if all(f["status"] == "PASS" for f in report["formats"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
