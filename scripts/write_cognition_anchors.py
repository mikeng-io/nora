"""Materialise confirmed anchors in the HOST eval checkout; never commit the output."""
from __future__ import annotations

import argparse
import json
import platform
from pathlib import Path

import yaml

from scripts.confirm_cognition_anchors import candidate
from scripts.find_cognition_anchors import room_aliases

ROOT = Path(__file__).resolve().parents[1]


def anchored(corpus: dict, confirmations: list[dict], platforms: dict[str, str]
             ) -> tuple[int, list[int]]:
    done = []
    for case in corpus["cases"]:
        scenario = case["metadata"]["scenario"]
        match = next((r for r in confirmations if r["scenario"] == scenario
                      and r.get("result") == "match"), None)
        if match is None or "parked" not in case:
            continue
        case["snapshot"] = {"platform": platforms[match["room"]],
                            "conversation_id": match["room"]}
        case["metadata"]["wake"] = match["wake"]
        del case["parked"]
        done.append(scenario)
    return len(done), sorted(done)


async def write(source_url: str, confirmations: Path) -> tuple[int, list[int]]:
    import asyncio

    from sqlalchemy.ext.asyncio import create_async_engine

    from nora.infra.sql.cognition_replay import recorded_wake
    from scripts.find_cognition_anchors import clone_url

    aliases = room_aliases()
    rows = []
    for line in (await asyncio.to_thread(confirmations.read_text)).splitlines():
        raw = json.loads(line)
        row = candidate(raw, aliases)
        if raw.get("result") == "match":
            rows.append({**row, "result": "match"})
    engine = create_async_engine(clone_url(source_url))
    platforms = {}
    try:
        for row in rows:
            _, view = await recorded_wake(engine, row["wake"])
            found = {s.platform for s in view.pending
                     if aliases.get(s.conversation_id) == row["room"] and s.platform}
            if len(found) != 1:
                raise ValueError("wake room unavailable")
            platforms[row["room"]] = found.pop()
    finally:
        await engine.dispose()
    path = ROOT / "evals/corpora/cognition_session.yaml"
    corpus = yaml.safe_load(path.read_text())
    count, scenarios = anchored(corpus, rows, platforms)
    if count:
        path.write_text(yaml.safe_dump(corpus, allow_unicode=True, sort_keys=False))
    return count, scenarios


def main() -> int:
    import asyncio
    import contextlib
    import logging
    import os
    import sys

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--confirmed", required=True, type=Path)
    args = parser.parse_args()
    previous = logging.root.manager.disable
    try:
        if (platform.system() != "Linux"
                or not (Path.home() / "workspace/nora/config.toml").is_file()):
            raise ValueError("host only")
        with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink), \
                contextlib.redirect_stderr(sink):
            logging.disable(sys.maxsize)
            count, scenarios = asyncio.run(write(args.source_url, args.confirmed))
    except Exception:  # noqa: BLE001 -- no private errors leave the host process
        print("anchor writing unavailable; run privately on the deploy host", file=sys.stderr)
        return 2
    finally:
        logging.disable(previous)
    print(json.dumps({"anchored": count, "scenarios": scenarios}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
