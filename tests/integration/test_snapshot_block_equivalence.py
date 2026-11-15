"""`scripts/snapshot_block_equivalence.py` run for real — the positive half
`tests/unit/test_snapshot_block_equivalence.py` cannot reach.

A generated eval source holds what a deployment's clone holds: the three
recorded in-force loads (written through the production `SqlRevisionStore`,
the same table the deployment's own start records), a tag turn's transcript
and a note (written through `SqlRecordStore`/`SqlNoteStore`), all before the
snapshot instant. The live render runs inside the same finite recorded
services the script then opens: `materialise` writes `in_force/`,
`sandbox_as_of` restores as of T, `obtain` composes from the RECORDED config
through `SubjectSpec.config` inside a case-owned environment under
`RunEvidence`, and the rendered blocks compare EQUAL to the turn's own
prefill. The recorded model service carries no response at all: any dispatch
on this path is an unrecorded call and fails the environment, which is the
assertion that none was made.
"""

from __future__ import annotations

import functools
import json
import os
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import sqlalchemy as sa

from evals.backbone import _close
from nora.domain.models import NormalizedMessage
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.notes import SqlNoteStore
from nora.infra.sql.revisions import SqlRevisionStore
from nora.subject import SubjectSpec, obtain, sandbox_as_of
from nora.subject.as_of import render_tag_blocks
from nora.subject.eval_evidence import read_events
from nora.subject.obtain import build_config
from nora.subject.recorded_services import RecordedServices
from scripts import snapshot_block_equivalence as snap
from tests.integration.test_cognition_anchor_finder import (
    IDENTITY,
    POLICY,
    ROOM,
    _eval_database,
    _recording,
)


def _services(cfg: Any) -> dict[str, Any]:
    """The finite world obtain+render needs: the obtain-time workspace and
    peer claims, then the two reads the tag assemble adds — the sender's
    card and her model of the window's speaker — once for the LIVE obtain+
    render and once for the snapshot's own. NO model response at all, so a
    dispatch on this path is an unrecorded call, not a passed render."""
    ws = cfg.honcho.workspace_id
    reads = [
        {"method": "GET",
         "path": f"/v3/workspaces/{ws}/peers/miles_novak/card",
         "response": {"status": 200, "json": {
             "peer_card": ["Miles prefers Friday mornings"]}}},
        # Any body matches: the read is identified by method, path, order
        # and count, not by a re-derivation of the assembler's query.
        {"method": "POST",
         "path": f"/v3/workspaces/{ws}/peers/{cfg.honcho.ai_peer}/representation",
         "response": {"status": 200, "json": {
             "representation": "## Explicit Observations\n"
                               "[2026-07-01 09:00] Miles is deciding "
                               "between friday and saturday"}}},
    ]
    doc = _recording(cfg)._data
    doc["services"]["model"] = []
    doc["services"]["memory"] += reads
    # This path reads the room roster once (the tag render's `<members>`),
    # not twice as the confirmer's session assembly does.
    doc["services"]["bridge"] = doc["services"]["bridge"][:-1]
    return doc


async def test_the_block_gate_runs_the_migrated_path_end_to_end(
    pg_admin, pg_template, tmp_path, monkeypatch, evaluation_config, capsys,
) -> None:
    monkeypatch.setenv("OMNIROUTE_API_KEY", "generated-token")
    name = "t_snap_eq_" + uuid4().hex + "_eval"
    before_ms = int(time.time() * 1000) - 600_000
    recorded_dir = tmp_path / "recorded"
    recorded_dir.mkdir()
    recorded_config = recorded_dir / "config.toml"
    # The recorded in-force config differs from today's template — the
    # deployment moved on since T — so a subject composed from the template
    # instead of this file asks a workspace the recording does not know.
    recorded_config.write_text(re.sub(
        r'workspace_id = "[^"]*"',
        'workspace_id = "recorded-workspace"',
        evaluation_config.read_text(), count=1))
    (recorded_dir / "policy.toml").write_text(POLICY)
    (recorded_dir / "identity.toml").write_text(IDENTITY)

    with _eval_database(pg_admin, pg_template, name) as url:
        cfg = build_config(SubjectSpec(
            database_url=url, workdir=tmp_path / "declared", room=ROOM,
            config=recorded_config, policy=POLICY, identity=IDENTITY))
        services_path = tmp_path / "services.json"
        services_path.write_text(json.dumps(_services(cfg)))
        recording = RecordedServices.load(services_path)

        tag = NormalizedMessage(
            platform=ROOM[0], conversation_id=ROOM[1],
            conversation_type="group", sender_id="+852111",
            sender_name="Miles", timestamp=before_ms,
            body="@nora which day were we deciding?", raw={})
        async with recording.open(url, redis=True) as environment:
            live = await obtain(SubjectSpec(
                database_url=url, workdir=tmp_path / "live", room=ROOM,
                config=recorded_config,
                now=datetime.fromtimestamp(before_ms / 1000, tz=UTC),
                overrides={}, policy=POLICY, identity=IDENTITY,
                environment=environment,
            ))
            try:
                await live.deps.audit.append(message=NormalizedMessage(
                    platform=ROOM[0], conversation_id=ROOM[1],
                    conversation_type="group", sender_id="+852111",
                    sender_name="Miles", timestamp=before_ms - 60_000,
                    body="still deciding between friday and saturday",
                    raw={}), person_id="miles_novak")
                tag_id = await live.deps.audit.append(
                    message=tag, person_id="miles_novak")
                engine = create_sql_engine(url)
                try:
                    await SqlNoteStore(engine).create(
                        unit=live.unit, title="Friday ride",
                        description="whether friday holds",
                        uncertainty="open",
                        closing_condition="somebody says yes",
                        established_in=ROOM[1],
                        body="nobody has confirmed friday",
                        established_by="debrief", now_ms=before_ms - 30_000,
                        anchor_date=20260701)
                    # The deployment's own start records what it loaded; a
                    # snapshot at --at names these loads or refuses.
                    async with engine.connect() as conn:
                        clock = (await conn.execute(
                            sa.text("SELECT clock_timestamp()"))).scalar_one()
                    at = clock + timedelta(seconds=60)
                    loaded_at = at - timedelta(seconds=30)
                    revisions = SqlRevisionStore(engine)
                    for file, path in (
                            ("config.toml", recorded_config),
                            ("policy.toml", recorded_dir / "policy.toml"),
                            ("identity.toml", recorded_dir / "identity.toml")):
                        await revisions.record_config(
                            file=file, path=str(path),
                            content=path.read_text(encoding="utf-8"),
                            loaded_at=loaded_at, hostname="generated-host")
                finally:
                    await engine.dispose()
                now = datetime.fromtimestamp(before_ms / 1000, tz=UTC)
                recorded = await render_tag_blocks(live, message=tag, now=now)
            finally:
                await _close(live)
        prefill_text = "\n".join(recorded.values())
        assert "transcript" in recorded

        monkeypatch.setattr(
            snap, "sandbox_as_of",
            functools.partial(sandbox_as_of, template=pg_template))
        args = SimpleNamespace(
            source=url, at=at.isoformat(), message_id=tag_id,
            prefill_text=prefill_text, services=str(services_path),
            workdir=str(tmp_path / "snap"), set=[])
        rc = await snap._main(args)
        out = capsys.readouterr().out
        assert rc == 0, out
        compared = next(
            line for line in out.splitlines() if line.startswith("compared:"))
        assert "(none)" not in compared
        assert "EQUAL" in out.splitlines()[-1]

        # Canonical evidence: the run started and settled inside
        # `RunEvidence`, and the render dispatched no model call at all.
        root = os.environ["NORA_EVAL_EXCHANGES"]
        newest = Path(max(
            (os.path.join(root, d) for d in os.listdir(root)),
            key=os.path.getmtime))
        events = read_events(newest)
        kinds = {e.get("kind") for e in events}
        assert "run_started" in kinds
        assert "run_finished" in kinds
        assert "nora.model.request" not in {e.get("name") for e in events}
