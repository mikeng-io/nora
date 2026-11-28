"""A SOURCE database that stands in for the deployment's clone, and the
`Spec` that drives a case against it.

The eval layer has ONE path: every case names a real room at a real instant,
`nora.subject.sandbox_from_snapshot` reads that moment out of the source and
writes it into a sandbox database of the case's own. A test therefore cannot
hand a case a transcript — it seeds a ROOM into a source, the way the
deployment's history seeds the clone, and the case names the moment.

`know()` is the seeding primitive for that: a throwaway subject composed
against the source writes the room's rows through the production stores at
explicit timestamps, then goes away. Nothing here is a second seeding path
for the runner — the runner never sees a `Knowledge`; it sees a source with
rows in it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from aiohttp import web
from opentelemetry import trace

from evals import backbone
from nora.domain.standing import Standing
from nora.infra.sql import create_sql_engine
from nora.infra.sql.culture import SqlCultureStore
from nora.infra.sql.notes import SqlNoteStore
from nora.infra.sql.standing import SqlStandingStore
from nora.platform.bridge.service import ActionReply, action_response
from nora.platform.bridge.wire import ActionRequest
from nora.subject import Knowledge, Message, SubjectSpec, know, obtain
from nora.subject.environment import IsolatedServices
from nora.subject.eval_evidence import EvidenceError, RunEvidence, read_events
from nora.subject.obtain import _DEFAULT_IDENTITY, default_policy
from nora.subject.sandbox import eval_host_lock
from nora.testing.signal_daemon import FakeSignalDaemon

#: One room, the shape `SubjectSpec`'s defaults give a test.
ROOM = ("signal", "g1")
#: The instant every seeded room stands at: the newest row's own `ts`.
BEFORE_MS = int(datetime(2026, 9, 1, 12, 0, tzinfo=UTC).timestamp() * 1000)
STEP_MS = 60_000


@dataclass(frozen=True)
class Source:
    """What a test needs to drive a case against a seeded source."""

    url: str
    room: tuple[str, str]
    before_ms: int
    policy: str
    identity: str
    template: str

    @property
    def snapshot(self) -> dict[str, object]:
        """The `snapshot:` block a case states to name this moment."""
        return {
            "platform": self.room[0],
            "conversation_id": self.room[1],
            "before_ms": self.before_ms,
        }


class OfflineSubjectServices:
    """Own a fresh protocol world per draw. Model requests receive a local outage."""

    @asynccontextmanager
    async def open(self, database_url: str, redis: bool = False) -> AsyncIterator[IsolatedServices]:
        model = web.Application()

        async def unavailable(request: web.Request) -> web.Response:
            body = await request.json()
            if not isinstance(body, dict) or not (body.get("model") and body.get("messages")):
                return web.json_response({"error": {"message": "invalid request"}}, status=400)
            return web.json_response({"error": {"message": "offline test provider"}}, status=503)

        model.router.add_post("/v1/chat/completions", unavailable)
        daemon = FakeSignalDaemon()
        bridge = web.Application()

        async def identity(_request: web.Request) -> web.Response:
            return web.json_response({"primary": "generated-self", "aliases": []})

        async def groups(_request: web.Request) -> web.Response:
            return web.json_response(daemon.groups)

        async def health(_request: web.Request) -> web.Response:
            return web.json_response({"ok": True})

        async def action(request: web.Request) -> web.Response:
            body = await request.json()
            try:
                action = ActionRequest.from_dict(body)
                target = action.request["target"]
                payload = action.request[{"send": "text", "react": "emoji"}[action.op]]
                if not action.action_id or not target["id"] or not isinstance(payload, str):
                    raise ValueError("invalid bridge action")
            except (KeyError, TypeError, ValueError):
                return web.json_response({"error": "unexpected bridge action"}, status=400)
            return web.json_response(action_response(
                action.action_id, ActionReply("confirmed", platform_ms=daemon.sent_at_ms),
            ))

        async def signal_rpc(request: web.Request) -> web.Response:
            body = await request.json()
            if not isinstance(body, list):
                return await daemon.rpc(request)
            if {row.get("method") for row in body} != {"listContacts", "getUserStatus"}:
                return web.json_response({"error": "unexpected identity batch"}, status=400)
            return web.json_response([
                {"jsonrpc": "2.0", "id": row["id"], "result": []} for row in body
            ])

        bridge.router.add_post("/api/v1/rpc", signal_rpc)
        bridge.router.add_get("/v1/self", identity)
        bridge.router.add_get("/v1/groups", groups)
        bridge.router.add_get("/v1/health", health)
        bridge.router.add_post("/v1/actions", action)
        async with IsolatedServices(database_url=database_url, redis=redis, applications={
            "llm.base_url": model,
            "platforms.signal.base_url": bridge,
        }) as environment:
            yield environment
            environment.check()


@asynccontextmanager
async def offline_subject(spec: SubjectSpec):
    """Compose and close a subject for a test that supplies no external model."""
    async with OfflineSubjectServices().open(spec.database_url, redis=True) as environment:
        yield await obtain(replace(spec, environment=environment))


async def offline_drive(case, spec):
    """Drive the harness with real private capture and an isolated offline world."""
    from scripts.memory_graph_spike import measured_run_budget

    backbone._prefill_capture()
    services = {case.name or "unnamed": OfflineSubjectServices()}
    with eval_host_lock() as lock_fd:
        evidence = RunEvidence(run_id=f"offline-{uuid4().hex}", lock_fd=lock_fd, metadata={})
        result = None
        try:
            with evidence.capture(trace.get_tracer_provider()), measured_run_budget(
                SimpleNamespace(stop_after_tokens=20_000), evidence, evidence.path, {},
                max_requests=20, max_output_tokens=400_000,
            ):
                result = await backbone._drive(case, replace(spec, services=services))
        except EvidenceError as error:
            # The negative controls intentionally drive a provider failure. Read
            # the persisted ERROR outcome while preserving the failed run receipt.
            if str(error) != "model_error" or result is None or not any(
                state.name == backbone.NORA_ERROR and state.state
                for state in result["environment_state"]
            ):
                raise
            assert any(row["kind"] == "run_failed" for row in read_events(evidence.path))
        return result


def dated(
    transcript: tuple[Message | str, ...] | list, *, before_ms: int = BEFORE_MS,
) -> tuple[Message, ...]:
    """Rows stamped one minute apart, the NEWEST at `before_ms` — so a case
    anchored at `before_ms` sees the whole window and nothing after it."""
    rows = [Message(body=m) if isinstance(m, str) else m for m in transcript]
    base = before_ms - (len(rows) - 1) * STEP_MS
    return tuple(
        Message(
            body=m.body, name=m.name, person=m.person, label=m.label,
            at_ms=base + i * STEP_MS, is_nora=m.is_nora, media=m.media,
        )
        for i, m in enumerate(rows)
    )


async def embed_graph_source(url: str) -> int:
    """Give a generated eval source the graph-search vectors production's
    backfill would hold, through the real projection queue. Returns the
    number of vectors written; zero means the fixture proved nothing."""
    import sqlalchemy as sa

    from nora.config import MemoryIndexConfig
    from nora.infra.embedder import fingerprint
    from nora.infra.sql import create_sql_engine, graph_search_embed
    from nora.infra.sql.cognitive_graph_schema import REGISTRY
    from nora.infra.sql.schema import EMBEDDING_DIMENSIONS
    from nora.memory.wire import rebuild_graph_search

    model = fingerprint(MemoryIndexConfig().embedder_model)
    engine = create_sql_engine(url)
    written = 0
    try:
        async with engine.begin() as conn:
            units = tuple(await conn.scalars(sa.select(REGISTRY.c.unit).distinct()))
            for unit in units:
                await rebuild_graph_search(conn, unit)
            items = await graph_search_embed.pick(conn, limit=500, now_ms=0)
            for n, item in enumerate(items):
                vector = [0.0] * EMBEDDING_DIMENSIONS
                vector[n % EMBEDDING_DIMENSIONS] = 1.0
                written += int(await graph_search_embed.store(
                    conn, item, vector=vector, now_ms=1, model=model,
                ))
    finally:
        await engine.dispose()
    return written


async def seed_source(
    url: str,
    workdir: Path,
    knowledge: Knowledge,
    *,
    template: str,
    room: tuple[str, str] = ROOM,
    before_ms: int = BEFORE_MS,
    policy: str = "",
    identity: str = _DEFAULT_IDENTITY,
) -> Source:
    """Write `knowledge` into `url` as the history of `room`, ending at
    `before_ms`, and return the `Source` a case drives against.

    Rows without an `at_ms` are dated by `dated()`; `standing`'s age and the
    notes are written relative to `before_ms` exactly as `know()` does.
    """
    policy = policy or default_policy(room)
    async with offline_subject(SubjectSpec(
        database_url=url, workdir=workdir / "seed", room=room,
        policy=policy, identity=identity,
        now=datetime.fromtimestamp(before_ms / 1000, tz=UTC),
    )) as subject:
        transcript = knowledge.transcript
        if transcript and any(
            isinstance(m, str) or m.at_ms is None for m in transcript
        ):
            transcript = dated(transcript, before_ms=before_ms)
        await know(
            subject,
            Knowledge(
                transcript=transcript,
                rooms=knowledge.rooms,
                spacing_seconds=knowledge.spacing_seconds,
            ),
            now_ms=before_ms,
            leave_room_for_arrival=False,
        )
        # Tests that measure an as-of restore need the old rows in their source.
        engine = create_sql_engine(url)
        try:
            if knowledge.standing is not None:
                await SqlStandingStore(engine).write(standing=Standing(
                    platform=room[0], conversation_id=room[1], body=knowledge.standing,
                    fold_count=1,
                    updated_at=before_ms - int((knowledge.standing_age_hours or 0) * 3_600_000),
                ))
            if knowledge.culture is not None:
                await SqlCultureStore(engine).append(
                    key=subject.culture_key, body=knowledge.culture,
                    written_at=before_ms, established_by="fixture",
                )
            for offset, note in enumerate(knowledge.notes):
                await SqlNoteStore(engine).create(
                    unit=subject.unit, title=note.title, description=note.description,
                    uncertainty=note.uncertainty, closing_condition=note.closing_condition,
                    established_in=room[1], body=note.body, established_by="fixture",
                    now_ms=before_ms + offset, anchor_date=note.anchor_date,
                    anchor_entity_ids=note.anchor_entity_ids,
                )
        finally:
            await engine.dispose()
    return Source(
        url=url, room=room, before_ms=before_ms, policy=policy, identity=identity,
        template=template,
    )
