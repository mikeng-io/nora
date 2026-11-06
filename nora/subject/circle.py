"""Explicit synthetic circle overlays. Nothing here describes the anchor's history."""
from __future__ import annotations

import contextlib
import hashlib
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Literal
from unittest.mock import patch
from uuid import UUID, uuid4

from nora.domain.compute import ComputeJob, ComputeJobState
from nora.domain.ports import LaunchRequest
from nora.domain.subagent import SubagentBrief
from nora.infra.sql import create_sql_engine
from nora.infra.sql.compute_jobs import SqlComputeJobStore

if TYPE_CHECKING:
    from .obtain import Subject


@dataclass(frozen=True)
class CircleOverlay:
    job: Literal['running', 'checked_complete'] | None = None
    reaction: Literal['unavailable', 'held'] | None = None
    gif: Literal['unavailable'] | None = None
    memory: Literal['partial', 'unavailable'] | None = None

    def __post_init__(self) -> None:
        for name, allowed in (
            ('job', (None, 'running', 'checked_complete')),
            ('reaction', (None, 'unavailable', 'held')),
            ('gif', (None, 'unavailable')),
            ('memory', (None, 'partial', 'unavailable')),
        ):
            if getattr(self, name) not in allowed:
                raise ValueError(f'unknown circle {name} overlay')

    @property
    def overrides(self) -> dict[str, object]:
        return ({'subagent.enabled': True, 'documents.enabled': True} if self.job else {}) | (
            {'memory.index.read': True, 'memory.index.embed': False} if self.memory else {}
        )


@contextlib.contextmanager
def external_boundaries(
    overlay: CircleOverlay | None, *, signal_url: str = "", memory: CircleMemory | None = None,
) -> Iterator[None]:
    """Composition-time external absence, with the real handlers/guards intact."""
    if overlay is None:
        yield
        return
    with contextlib.ExitStack() as stack:
        if memory is not None:
            from nora.app import composition
            from nora.infra.sql import memory_graph
            stack.enter_context(patch.object(composition, 'SqlMemoryIndex',
                lambda _engine: memory.index))
            stack.enter_context(patch.object(memory_graph, 'SqlMemoryGraph',
                lambda _engine, **_kwargs: memory.graph))
        if signal_url:
            from nora.platform.signal import adapter
            from nora.platform.signal.react import SignalReactor
            stack.enter_context(patch.object(adapter, "SignalReactor",
                lambda _url: SignalReactor(signal_url)))
        if overlay.gif:
            # No provider credential: the existing GifSource boundary is unavailable,
            # and search_gifs returns its real unavailable guidance, never a URL.
            from nora.agent.registry import ToolRegistry
            original = ToolRegistry.__init__

            def without_gifs(self, *args, **kwargs):
                kwargs['giphy'] = None
                original(self, *args, **kwargs)

            stack.enter_context(patch.object(ToolRegistry, '__init__', without_gifs))
        yield


async def seed_job(subject: Subject, overlay: CircleOverlay, *, person: str, now_ms: int) -> str:
    """A generated job in the real SQL outbox, served by existing desk/context reads."""
    if overlay.job is None:
        return ''
    from datetime import UTC, datetime

    from .sandbox import seed_documents

    backend = subject.deps.subagent_backend
    if backend is None:
        raise ValueError('circle job requires the composed subagent backend')
    now = datetime.fromtimestamp(now_ms / 1000, UTC)
    brief = SubagentBrief(title='Generated comparison',
                          objective='Compare generated options A and B',
                          deliverable='A checked comparison report')
    handle = await backend.launch(request=LaunchRequest(
        task=brief.objective, platform=subject.room[0], conversation_id=subject.room[1],
        conversation_type='group', requester_person_id=person, payload=brief,
    ))
    job_id = uuid4()
    job = ComputeJob(id=job_id, backend_job_id=handle, task=brief.objective,
        artifact_id=None, platform=subject.room[0], conversation_id=subject.room[1],
        conversation_type='group', requester_person_id=person,
        created_at=now - timedelta(minutes=5), state=ComputeJobState.PENDING,
        completed_at=None, result_url=None, result_summary=None, error=None,
        task_sha256=hashlib.sha256(brief.objective.encode()).hexdigest(),
        context_sha256=hashlib.sha256(b'[]').hexdigest(), kind='subagent',
        payload=brief.as_payload())
    engine = create_sql_engine(subject.database_url)
    try:
        store = SqlComputeJobStore(engine)
        await store.create(job)
        if overlay.job == 'checked_complete':
            document_id = str(uuid4())
            report = ('Generated comparison: A costs 12 units and takes 8 minutes; '
                      'B costs 9 units and takes 11 minutes. Choose B when cost matters; '
                      'choose A when speed matters.')
            await seed_documents(subject, 'circle checked output', [{
                'id': document_id, 'title': brief.title, 'body': report,
            }])
            checked = await subject.deps.documents.get(document_id=UUID(document_id))
            if checked is None or checked.text.strip() != brief.title + "\n" + report:
                raise ValueError('generated output readback missing or changed')
            from nora.domain import audience as audience_names
            from nora.domain.models import ConversationTarget
            from nora.engagement.stages import deliver
            # The one construction site every sender passes through (R-1b), so
            # R-2/R-4/R-6/R-18 and the audit-first write still stand between
            # the generated report and the wire — in this subject, the wire is
            # what `obtain`'s `landed_signal_sends()` captures. The handle's
            # guard half is never attached to a model here; `budgets` answers
            # `message_cap`'s one message and `composer=None` is the stated
            # eval decision (no roster, no preview fetcher).
            deliverer = deliver.build(
                store=subject.deps.audit,
                adapter=subject.deps.registry.adapters[subject.room[0]],
                target=ConversationTarget(*subject.room, 'group'),
                doctrine=subject.deps.doctrine, canary=subject.cfg.doctrine.canary,
                tag_id=f'generated-circle-{job_id}', nora_sender_id='eval',
                conversation_id=subject.room[1],
                budgets={'send_reply': 1}, terminal=frozenset({'send_reply'}),
                audience=audience_names.TAG, composer=None,
                metrics=subject.deps.metrics,
            ).deliverer
            if not await deliverer.deliver(report):
                raise ValueError('generated output delivery did not land')
            if not await store.claim(job_id):
                raise ValueError("generated job claim failed")
            if not await store.mark(job_id, ComputeJobState.DONE, completed_at=now,
                result_report=report, result_document_ids=(document_id,)):
                raise ValueError("generated job receipt missing")
        readback = await store.get(job_id)
        if readback is None or readback.state != (
            ComputeJobState.DONE if overlay.job == 'checked_complete' else ComputeJobState.PENDING
        ):
            raise ValueError('circle job lifecycle readback missing')
        return str(job_id)
    finally:
        await engine.dispose()


@contextlib.asynccontextmanager
async def reaction_wire(overlay: CircleOverlay | None) -> AsyncIterator[str]:
    """A draw-owned RPC endpoint, exercised by the REAL SignalReactor."""
    if overlay is None or overlay.reaction != 'unavailable':
        yield ''
        return
    from aiohttp import web

    async def rpc(request):
        payload = await request.json()
        params = payload.get('params', {})
        if payload.get('method') != 'sendReaction' or not all(
            params.get(k) for k in ('groupId', 'emoji', 'targetAuthor', 'targetTimestamp')
        ):
            return web.json_response({'error': {'code': -32602}}, status=400)
        return web.json_response({'jsonrpc': '2.0', 'id': payload.get('id'),
                                  'error': {'code': -32000, 'message': 'controlled outage'}})

    app = web.Application()
    app.router.add_post('/api/v1/rpc', rpc)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    try:
        host, port = runner.addresses[0]
        yield f'http://{host}:{port}'
    finally:
        await runner.cleanup()


class CircleMemory:
    """Generated evidence over the existing shared-contract-tested graph and index."""

    def __init__(self, *, unavailable: bool) -> None:
        from nora.domain.memory_index import IndexState
        from nora.testing.memory_graph import FakeMemoryGraph
        from nora.testing.memory_index import FakeMemoryIndex
        self.graph = FakeMemoryGraph()
        self.index = FakeMemoryIndex(state=IndexState('building', 0),
            fail=TimeoutError('controlled index outage') if unavailable else None)
        self.unavailable = unavailable

    async def populate(self, subject: Subject, *, person: str, now_ms: int) -> None:
        from datetime import UTC, datetime

        from nora.domain.memory_graph import (
            Correction,
            Entity,
            Evidence,
            Relation,
            Source,
            pointer_entity,
        )
        from nora.domain.memory_index import IndexedNode
        unit = subject.unit
        at = datetime.fromtimestamp(now_ms / 1000, UTC)
        person_entity = pointer_entity('person', unit, person, 'Generated speaker')
        topic = Entity(f'{unit}:generated-spicy', 'work', unit, 'generated spicy noodle dish 食辣')
        old = 'Generated preference 口味: I like generated spicy noodle dish 食辣.'
        new = ('Generated correction 口味: I no longer eat generated spicy noodle dish 唔食辣. '
               'This replaces my old preference.')
        await self.graph.upsert_entity(person_entity)
        sources = {}
        relations = []
        for i, text in enumerate((old, new)):
            stamp = now_ms - (2 - i) * 86_400_000
            row = f'generated-circle-source-{i}'
            key = ('message', str(row))
            node = IndexedNode('message', str(row), text, stamp,
                origin_room=':'.join(subject.room), persons=(person,))
            self.index.nodes[key] = node
            self.graph.indexed[key] = node
            self.graph.fields[key] = {'body': text, 'person_id': person, 'ts': stamp}
            sources[key] = Source(key, 'record', unit, (person,), text)
            relation = Relation(f'circle-preference-{i}', person_entity.key,
                'likes' if i == 0 else 'dislikes', topic.key, 'notebook', unit,
                persons=(person,), asserted_at=datetime.fromtimestamp(stamp / 1000, UTC))
            evidence = Evidence(key, relation_id=relation.id, span=text)
            admission = await self.graph.stage_and_admit(
                entities=(topic,) if i == 0 else (),
                relations=(relation,), evidence=(evidence,), sources=sources,
                corrections=(Correction(relations[0].id, 'supersede', at,
                    by=relation.id, evidence=(key,)),) if i else (),
            )
            if admission.refused:
                raise ValueError(f'generated preference refused: {admission.refused}')
            record = Relation(f'circle-authorship-{i}', key, 'authored_by',
                person_entity.key, 'record', unit, persons=(person,),
                asserted_at=datetime.fromtimestamp(stamp / 1000, UTC))
            admitted_record = await self.graph.stage_and_admit(entities=(), relations=(record,),
                evidence=(Evidence(key, relation_id=record.id, span=text),), sources=sources)
            if admitted_record.refused:
                raise ValueError('generated source authorship refused')
            relations.append(relation)
            self.index.edges.append(('message', str(row), 'authored_by', 'person', person))
        # Neither old nor corrected preference is visible in the arriving question.
        # Only the declared graph/index read may supply it; outage exposes neither.
        if self.unavailable:
            self.graph.available = False
