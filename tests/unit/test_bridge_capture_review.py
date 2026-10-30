"""Review findings 2/3/6 — Discord transition identity, Signal media-custody
binding, bridge producer telemetry wiring.

Every test names the finding it closes and goes RED on the exact old seam:
- finding 2 — `bridge_app._journal_and_enqueue` built the upstream part from
  `msg.message_id or f"{sender}:{timestamp}"`: two edits of one message, and
  the clockless add/remove/add sequence, collapsed to ONE event id.
- finding 3 — `wrapper._custody` wrote bytes under the content hash while
  `MediaRef.platform_id` kept the upstream handle `BridgeMediaSource`
  cannot fetch.
- finding 6 — capture ran with `trace={}` and no span; the shared capture /
  publisher / action collaborators carried no `bt.*` calls, so nothing a
  downstream span could link to ever existed.

The durable seam is REAL everywhere it matters: a filesystem `Journal` (the
transition ledger is its records, rebuilt across restart) and the real
`JournalCaptureSink` / `JournalPublisher` / `DurableActionService`
collaborators — only the Redis writer is a call-shape fake.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import os
import sys
import textwrap
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from nora.domain.actions import ActionOwner
from nora.domain.media import MediaRef
from nora.domain.models import NormalizedMessage
from nora.obs.metrics import Metrics
from nora.platform.bridge import telemetry as bt
from nora.platform.bridge.envelope import Envelope
from nora.platform.bridge.journal import Journal, JournalError
from nora.platform.bridge.service import (
    ActionReply,
    DurableActionService,
    JournalCaptureSink,
    JournalPublisher,
    resolve_clockless_transition,
)
from nora.platform.discord.bridge_app import DiscordBridge

ACCT = "it-acct"
GUILD, CHAN, MEMBER, TARGET = "11111", "22222", "33333", "44444"
CONV = f"{GUILD}/{CHAN}"
PROJ = {"revision": 3, "enabled": True, "rooms": [CONV]}


class _Producer:
    """The `StreamProducer` call shape — `enqueue(env) -> (stream_id, id)`.

    Records envelopes; the canonical id IS the computed alias (the fresh-
    capture answer), never a same-answer stub."""
    def __init__(self) -> None:
        self.enqueued: list[Any] = []

    async def enqueue(self, env: Any) -> tuple[str, str]:
        self.enqueued.append(env)
        return "1-1", env.event_id


class _Emoji:
    """`PartialEmoji`'s two shapes: `name` for unicode, `name:id` for
    custom — the same `str()` the parser keys its `Touch.emoji` on."""
    def __init__(self, name: str, eid: int | None = None) -> None:
        self.name = name
        self.id = eid

    def __str__(self) -> str:
        return f"{self.name}:{self.id}" if self.id else self.name


def _reaction_payload(
        *, removed: bool = False, message_id: int = int(TARGET),
        user_id: int = int(MEMBER), emoji: Any | None = None,
        type_value: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        guild_id=int(GUILD), user_id=user_id, message_id=message_id,
        channel_id=int(CHAN), emoji=emoji or _Emoji("\U0001F44D"),
        type=SimpleNamespace(value=type_value))


def _reaction_frame(
        s: int, *, t: str = "MESSAGE_REACTION_ADD",
        message_id: int = int(TARGET), user_id: int = int(MEMBER),
        emoji: dict[str, Any] | None = None) -> dict[str, Any]:
    """The Gateway dispatch BEFORE parsing — `s` is the per-session
    sequence the wire provenance joins on."""
    return {"t": t, "s": s, "d": {
        "channel_id": CHAN, "message_id": str(message_id),
        "user_id": str(user_id),
        "emoji": emoji or {"name": "\U0001F44D", "id": None}, "type": 0}}


async def _sequenced_touch(bridge: Any, kind: str, s: int,
                           **payload_kw: Any) -> None:
    """A REAL sequenced dispatch: the raw frame lands first (as
    `socket_raw_receive` delivers it), then the parsed callback fires —
    the same order the Gateway drives them."""
    t = ("MESSAGE_REACTION_REMOVE" if kind == "reaction_remove"
         else "MESSAGE_REACTION_ADD")
    await bridge._record_wire_frame(_reaction_frame(s, t=t))
    await bridge._capture_touch(
        kind, _reaction_payload(removed=kind == "reaction_remove",
                                **payload_kw))


def _message(
        *, edited_at: datetime | None = None,
        content: str = "hi") -> SimpleNamespace:
    guild = SimpleNamespace(id=int(GUILD))
    return SimpleNamespace(
        id=int(TARGET), content=content, attachments=[], stickers=[],
        mentions=[], reference=None, poll=None, message_snapshots=None,
        embeds=[], edited_at=edited_at,
        author=SimpleNamespace(id=int(MEMBER), display_name="Alice"),
        channel=SimpleNamespace(id=int(CHAN), parent_id=None),
        guild=guild)


def _bridge(tmp_path, *, with_journal: bool = True,
            session_id: str | None = None, metrics: Any | None = None):
    """A real `DiscordBridge` on the real capture seam — the only fakes are
    the client surface and the Redis writer."""
    journal = Journal(tmp_path / "journal.jsonl")
    producer = _Producer()
    publisher = JournalPublisher(
        journal=journal, producer=producer, metrics=metrics)
    sink = JournalCaptureSink(
        journal=journal, publisher=publisher, metrics=metrics)
    client = SimpleNamespace(
        get_channel=lambda channel_id: None,
        is_closed=lambda: False,
        ws=SimpleNamespace(session_id=session_id or "sess-1"))
    bridge = DiscordBridge(
        api_token="t", account_key=ACCT, client=client,  # noqa: S106 — fixture secret
        correlation_key=b"\x22" * 32, key_version=1,
        projection=lambda: PROJ,
        capture=sink, publisher=publisher,
        journal=journal if with_journal else None)
    return bridge, journal, sink, producer


def _envelopes(journal: Journal) -> list[Envelope]:
    return [Envelope.from_fields(r["envelope"])
            for r in journal.recover() if "envelope" in r]


# ---------------------------------------------------------------------------
# Finding 2 — edits and clockless reactions collapse under the old identity
# ---------------------------------------------------------------------------

class TestTransitionIdentity:
    async def test_real_sdk_registers_and_decodes_gateway_provenance(
            self, tmp_path, monkeypatch):
        import json

        import discord

        client = discord.Client(intents=discord.Intents.none(),
                                enable_debug_events=True)
        await client._async_setup_hook()

        async def start_without_network(received):
            assert received == "generated"

        monkeypatch.setattr(client, "start", start_without_network)
        auth_value = "generated"
        bridge = DiscordBridge(
            api_token=auth_value, account_key=ACCT, client=client,
            correlation_key=b"generated", projection=lambda: PROJ)
        try:
            await bridge.start(auth_value)
            assert hasattr(client, "on_raw_reaction_add")
            assert hasattr(client, "on_socket_raw_receive")
            client.ws = SimpleNamespace(session_id="generated-session")
            frame = {"op": 0, "t": "MESSAGE_REACTION_ADD", "s": 42,
                     "d": {"channel_id": CHAN, "message_id": TARGET,
                           "user_id": MEMBER,
                           "emoji": {"name": "x", "id": None}, "type": 0}}
            await client.on_socket_raw_receive(json.dumps(frame))
            assert any(v == [("generated-session", 42)]
                       for v in bridge._frames.values())
        finally:
            client.ws = None
            await bridge.close()

    async def test_sdk_dispatch_boundary_json_frame_to_journal(
            self, tmp_path, monkeypatch):
        """The REAL SDK seam end to end — no synthesized internals: JSON
        text off the wire → the `client.event`-registered
        `on_socket_raw_receive` (provenance joined) → discord.py's own
        `parse_message_reaction_add` → the registered `on_raw_reaction_add`
        → capture → journal. No live Gateway."""
        import json

        import discord

        client = discord.Client(intents=discord.Intents.none(),
                                enable_debug_events=True)
        await client._async_setup_hook()

        journal = Journal(tmp_path / "journal.jsonl")
        producer = _Producer()
        publisher = JournalPublisher(journal=journal, producer=producer)
        sink = JournalCaptureSink(journal=journal, publisher=publisher)
        bridge = DiscordBridge(
            api_token="t", account_key=ACCT, client=client,  # noqa: S106 — fixture secret
            correlation_key=b"\x22" * 32, key_version=1,
            projection=lambda: PROJ,
            capture=sink, publisher=publisher, journal=journal)

        async def start_without_network(received):
            assert received == "t"

        monkeypatch.setattr(client, "start", start_without_network)
        try:
            await bridge.start("t")
            client.ws = SimpleNamespace(session_id="sdk-sess")
            d = {"user_id": MEMBER, "channel_id": CHAN, "message_id": TARGET,
                 "guild_id": GUILD, "type": 0, "burst": False,
                 "burst_colors": [],
                 "emoji": {"id": None, "name": "\U0001F44D",
                           "animated": False}}
            await client.on_socket_raw_receive(
                json.dumps(
                    {"op": 0, "t": "MESSAGE_REACTION_ADD", "s": 77, "d": d}))
            client._connection.parse_message_reaction_add(d)
            # `dispatch` schedules the parsed handler as a task — poll the
            # journal rather than racing it.
            for _ in range(100):
                if _envelopes(journal):
                    break
                await asyncio.sleep(0.01)
            envs = _envelopes(journal)
            assert len(envs) == 1
            assert envs[0].kind == "reaction"
            rec = [r for r in journal.recover() if "transition" in r]
            assert rec[0]["transition"]["upstream"] == "g:sdk-sess:77"
        finally:
            client.ws = None
            await bridge.close()

    async def test_two_edits_of_one_message_keep_distinct_revisions(
            self, tmp_path):
        """Finding 2: `edited_at` is the upstream revision — Jan 1 and Jan 2
        are TWO events, not one `message_id` collapse."""
        bridge, journal, _, _ = _bridge(tmp_path)
        await bridge._capture_edit(_message(
            edited_at=datetime(2026, 1, 1, tzinfo=UTC)))
        await bridge._capture_edit(_message(
            edited_at=datetime(2026, 1, 2, tzinfo=UTC)))
        envs = _envelopes(journal)
        assert len(envs) == 2
        assert envs[0].kind == envs[1].kind == "edit"
        assert envs[0].event_id != envs[1].event_id

    async def test_an_edit_replay_keeps_its_identity(self, tmp_path):
        """The same `edited_at` redelivered is the SAME event — the dedupe
        alias must not move."""
        bridge, journal, _, _ = _bridge(tmp_path)
        msg = _message(edited_at=datetime(2026, 1, 1, tzinfo=UTC))
        await bridge._capture_edit(msg)
        await bridge._capture_edit(msg)
        envs = _envelopes(journal)
        assert len(envs) == 2
        assert envs[0].event_id == envs[1].event_id

    async def test_reaction_add_remove_add_are_three_transitions(
            self, tmp_path):
        """Finding 2: sequenced dispatches commit three distinct transitions —
        `add/remove/add` is three events with three proven upstream
        identities, never one `sender:0` collapse."""
        bridge, journal, _, _ = _bridge(tmp_path)
        await _sequenced_touch(bridge, "reaction_add", 41)
        await _sequenced_touch(bridge, "reaction_remove", 42)
        await _sequenced_touch(bridge, "reaction_add", 43)
        envs = _envelopes(journal)
        assert len(envs) == 3
        ids = {e.event_id for e in envs}
        assert len(ids) == 3
        recs = [r for r in journal.recover() if "transition" in r]
        assert [t["state"] for t in
                (r["transition"] for r in recs)] == ["add", "remove", "add"]
        assert [t["seq"] for t in
                (r["transition"] for r in recs)] == [1, 2, 3]
        assert [t["upstream"] for t in
                (r["transition"] for r in recs)] == [
                    "g:sess-1:41", "g:sess-1:42", "g:sess-1:43"]

    async def test_identical_reaction_redelivery_keeps_one_identity(
            self, tmp_path):
        """The same sequenced dispatch delivered twice → the SAME `g:`
        identity — at-least-once redelivery suppresses itself downstream."""
        bridge, journal, _, _ = _bridge(tmp_path)
        await _sequenced_touch(bridge, "reaction_add", 42)
        await _sequenced_touch(bridge, "reaction_add", 42)
        envs = _envelopes(journal)
        assert len(envs) == 2
        assert envs[0].event_id == envs[1].event_id
        # The committed upstream IS the frame's revision — a later
        # frame-less redelivery resolves to it (the durable answer).
        ledger_key = f"r:{CONV}:{TARGET}:{MEMBER}:" + "\U0001F44D:0"
        state, seq, upstream = journal.transition_ledger()[ledger_key]
        assert (state, seq, upstream) == ("add", 1, "g:sess-1:42")

    async def test_the_ledger_survives_a_process_restart(self, tmp_path):
        """A NEW `Journal` on the same file replays the same ledger — a
        frameless same-state callback resolves to the identity the recorded
        transition committed under, never a restarted local mint."""
        bridge, _, _, _ = _bridge(tmp_path, session_id="sess-42")
        await _sequenced_touch(bridge, "reaction_add", 4242)
        # A fresh process sees the same file — and answers the same ledger.
        restarted = Journal(tmp_path / "journal.jsonl")
        upstream, transition = resolve_clockless_transition(
            restarted,
            f"r:{CONV}:{TARGET}:{MEMBER}:" + "\U0001F44D:0", "add")
        assert upstream == "g:sess-42:4242"
        assert transition is None

    async def test_a_frameless_state_change_is_retained_not_published(
            self, tmp_path):
        """The reconnect-replay rule (design 02 §wire): a state change with
        NO upstream revision cannot be told apart from a delayed replay of
        an old transition — it is retained ambiguous, never published under
        a minted identity."""
        bridge, journal, _, _ = _bridge(tmp_path, session_id="sess-42")
        await _sequenced_touch(bridge, "reaction_add", 4242)
        # A remove arrives with no frame (lost, overflowed, or replayed
        # across a restart): `add → remove` looks like a state change, but
        # nothing proves it is one.
        await bridge._capture_touch(
            "reaction_remove", _reaction_payload(removed=True))
        envs = _envelopes(journal)
        assert len(envs) == 1          # only the sequenced add published
        ambiguous = [r["ambiguous"] for r in journal.recover()
                     if "ambiguous" in r]
        assert len(ambiguous) == 1
        assert ambiguous[0]["state"] == "remove"
        assert "reconnect replay" in ambiguous[0]["reason"]
        # The ledger is UNTOUCHED by the ambiguous evidence — no phantom
        # state a later legitimate transition could skip over.
        ledger_key = f"r:{CONV}:{TARGET}:{MEMBER}:" + "\U0001F44D:0"
        assert journal.transition_ledger()[ledger_key] == ("add", 1, "g:sess-42:4242")

    async def test_a_delayed_old_callback_after_restart_mints_nothing(
            self, tmp_path):
        """The probe's exact seam: a fresh `Journal`+bridge after a restart
        receives a delayed old `add` callback with no frame — the old code
        minted `l:...:add:N` and published a new logical event; now it is
        retained ambiguous."""
        bridge, _, _, _ = _bridge(tmp_path)
        await _sequenced_touch(bridge, "reaction_add", 41)
        await _sequenced_touch(bridge, "reaction_remove", 42)
        # Restart: new bridge + journal on the same file, NO frames.
        bridge2, journal2, _, _ = _bridge(tmp_path)
        await bridge2._capture_touch("reaction_add", _reaction_payload())
        envs = _envelopes(journal2)
        assert len(envs) == 2          # the two sequenced commits only
        ambiguous = [r["ambiguous"] for r in journal2.recover()
                     if "ambiguous" in r]
        assert len(ambiguous) == 1
        assert ambiguous[0]["state"] == "add"
        # The ledger still names the remove — the unprovable callback did
        # not rewind durable state.
        ledger_key = f"r:{CONV}:{TARGET}:{MEMBER}:" + "\U0001F44D:0"
        assert journal2.transition_ledger()[ledger_key][0] == "remove"

    async def test_a_replayed_frame_after_restart_dedupes(
            self, tmp_path):
        """A RESUME/replayed dispatch keeps its `(session, s)` — the new
        process resolves the SAME `g:` identity and the stream dedupe
        suppresses it."""
        bridge, journal, _, _ = _bridge(tmp_path, session_id="sess-42")
        await _sequenced_touch(bridge, "reaction_add", 4242)
        original = _envelopes(journal)[0].event_id
        # Restart on the same session's replay: the frame is re-presented
        # with its original session and sequence.
        bridge2, journal2, _, _ = _bridge(tmp_path, session_id="sess-42")
        await _sequenced_touch(bridge2, "reaction_add", 4242)
        envs = _envelopes(journal2)
        assert len(envs) == 2
        assert envs[1].event_id == original

    async def test_a_new_session_is_a_new_transition(self, tmp_path):
        """READY mints a new session id and resets `s`: a dispatch on the
        new session is genuinely new upstream evidence — it publishes under
        its own `g:` identity."""
        bridge, journal, _, _ = _bridge(tmp_path, session_id="sess-a")
        await _sequenced_touch(bridge, "reaction_add", 1)
        first = _envelopes(journal)[0].event_id
        # New process, new session: the same callback is a real new
        # dispatch, not an ambiguous replay — the session id distinguishes.
        bridge2, journal2, _, _ = _bridge(tmp_path, session_id="sess-b")
        await _sequenced_touch(bridge2, "reaction_add", 1)
        envs = _envelopes(journal2)
        assert len(envs) == 2
        assert envs[1].event_id != first

    async def test_a_failed_journal_append_allocates_no_sequence(
            self, tmp_path, monkeypatch):
        """The ledger step commits ON the event's fsync — a failed append
        leaves the recorded state untouched, so the NEXT provenanced
        dispatch does not skip a sequence over the phantom."""
        bridge, journal, _, _ = _bridge(tmp_path)
        await _sequenced_touch(bridge, "reaction_add", 41)

        def flaky(fd: int) -> None:
            raise OSError("generated disk failure")

        monkeypatch.setattr(os, "fsync", flaky)
        with pytest.raises(JournalError):
            await _sequenced_touch(bridge, "reaction_remove", 42)
        monkeypatch.undo()
        # The IN-MEMORY ledger never advanced past the failed fsync — the
        # recorded state is still the committed `add`.
        ledger_key = f"r:{CONV}:{TARGET}:{MEMBER}:" + "\U0001F44D:0"
        assert journal.transition_ledger()[ledger_key] == ("add", 1, "g:sess-1:41")
        # The next provenanced dispatch derives its step from the SAME
        # recorded state — seq 2, not a phantom-skip 3.
        await _sequenced_touch(bridge, "reaction_remove", 43)
        recs = [r["transition"] for r in journal.recover()
                if "transition" in r]
        assert recs[-1]["seq"] == 2
        assert recs[-1]["upstream"] == "g:sess-1:43"

    async def test_a_wire_dispatch_sequence_is_a_real_upstream_identity(
            self, tmp_path):
        """The Gateway `s` captured at `socket_raw_receive` IS an upstream
        sequence: the parsed reaction that frame dispatched resolves to
        `g:{session}:{s}`, and an at-least-once redelivery of the SAME
        frame keeps the SAME identity."""
        bridge, journal, _, _ = _bridge(tmp_path, session_id="sess-42")
        frame = {"t": "MESSAGE_REACTION_ADD", "s": 4242, "d": {
            "channel_id": CHAN, "message_id": TARGET, "user_id": MEMBER,
            "emoji": {"name": "\U0001F44D", "id": None}, "type": 0}}
        await bridge._record_wire_frame(frame)
        await bridge._capture_touch("reaction_add", _reaction_payload())
        # Redelivery: the same frame value dispatched again — same (s).
        await bridge._record_wire_frame(dict(frame))
        await bridge._capture_touch("reaction_add", _reaction_payload())
        envs = _envelopes(journal)
        assert len(envs) == 2
        assert envs[0].event_id == envs[1].event_id
        # Distinct wire dispatches are distinct transitions.
        await bridge._record_wire_frame({**frame, "s": 4243})
        await bridge._capture_touch("reaction_add", _reaction_payload())
        assert _envelopes(journal)[2].event_id != envs[0].event_id

    async def test_a_clockless_reaction_with_no_ledger_is_retained_ambiguous(
            self, tmp_path):
        """No wire frame AND no journal → the event is retained as ambiguous
        diagnostic evidence, never deduplicated under a guessed identity."""
        bridge, journal, _, _ = _bridge(tmp_path, with_journal=False)
        bridge._frames.clear()  # no wire provenance either
        await bridge._capture_touch("reaction_add", _reaction_payload())
        assert not _envelopes(journal)
        ambiguous = [r["ambiguous"] for r in journal.recover()
                     if "ambiguous" in r]
        assert len(ambiguous) == 1
        assert ambiguous[0]["kind"] == "reaction"
        assert ambiguous[0]["conversation_id"] == CONV
        assert ambiguous[0]["target"] == TARGET

    async def test_a_frameless_first_observation_is_retained_ambiguous(
            self, tmp_path):
        """With a journal but no wire frame and no recorded transition, the
        very first observation is still unprovable — retained, never minted."""
        bridge, journal, _, _ = _bridge(tmp_path)
        await bridge._capture_touch("reaction_add", _reaction_payload())
        assert not _envelopes(journal)
        ambiguous = [r["ambiguous"] for r in journal.recover()
                     if "ambiguous" in r]
        assert len(ambiguous) == 1
        assert ambiguous[0]["state"] == "add"

    async def test_a_delete_names_the_message_it_removes(self, tmp_path):
        """`d:{target}` — the target IS the delete's transition identity; a
        `sender:0` fallback could never name it."""
        bridge, journal, _, _ = _bridge(tmp_path)
        await bridge._capture_touch("delete", SimpleNamespace(
            guild_id=int(GUILD), message_id=int(TARGET),
            channel_id=int(CHAN)))
        envs = _envelopes(journal)
        assert len(envs) == 1 and envs[0].kind == "delete"

    async def test_two_deletes_of_different_messages_diverge(self, tmp_path):
        bridge, journal, _, _ = _bridge(tmp_path)
        for mid in (int(TARGET), int(TARGET) + 1):
            await bridge._capture_touch("delete", SimpleNamespace(
                guild_id=int(GUILD), message_id=mid,
                channel_id=int(CHAN)))
        envs = _envelopes(journal)
        assert len(envs) == 2
        assert envs[0].event_id != envs[1].event_id


# ---------------------------------------------------------------------------
# Finding 6 — producer span context frozen before the journal append
# ---------------------------------------------------------------------------

class TestProducerTelemetry:
    async def test_the_capture_span_context_lands_on_the_envelope(
            self, tmp_path, exported_spans):
        """`env.trace` IS the producer span's context, frozen inside the
        span BEFORE the journal append — never `{}` while tracing is on."""
        bridge, journal, _, _ = _bridge(tmp_path)
        await bridge._capture_message(_message())
        env = _envelopes(journal)[0]
        assert env.trace.get("traceparent")
        spans = exported_spans.get_finished_spans()
        producers = [s for s in spans
                     if s.attributes.get("nora.trace_kind") == "queue"
                     and s.kind.name == "PRODUCER"]
        assert len(producers) == 1
        ctx = producers[0].get_span_context()
        assert env.trace["traceparent"].split("-")[1] == (
            f"{ctx.trace_id:032x}")
        assert env.trace["traceparent"].split("-")[2] == (
            f"{ctx.span_id:016x}")

    async def test_the_publisher_span_links_back_to_capture(
            self, tmp_path, exported_spans):
        """A journaled envelope's publication is a NEW root carrying a LINK
        to the frozen producer context — never ambient parentage."""
        bridge, journal, _, producer = _bridge(tmp_path)
        await bridge._capture_message(_message())
        env = _envelopes(journal)[0]
        captured = exported_spans.get_finished_spans()
        capture_trace = int(env.trace["traceparent"].split("-")[1], 16)
        pub = JournalPublisher(
            journal=journal, producer=producer, metrics=Metrics())
        task = asyncio.ensure_future(pub.run())
        try:
            for _ in range(200):
                if producer.enqueued:
                    break
                await asyncio.sleep(0.02)
            assert producer.enqueued
        finally:
            pub.stop()
            task.cancel()
            with pytest.raises((asyncio.CancelledError, Exception)):
                await task
        spans = [s for s in exported_spans.get_finished_spans()
                 if s not in captured]
        pubs = [s for s in spans
                if s.attributes.get("nora.trace_kind") == "queue"
                and s.kind.name == "PRODUCER"]
        assert len(pubs) == 1
        linked = {lnk.context.trace_id for lnk in pubs[0].links}
        assert capture_trace in linked

    async def test_lifecycle_lines_and_counters_on_the_capture_path(
            self, tmp_path, caplog):
        """`captured` lifecycle evidence lands when the append lands — the
        JSON line and the `nora_bridge_queue_events_total` tick."""
        metrics = Metrics()
        bridge, journal, _, _ = _bridge(tmp_path, metrics=metrics)
        with caplog.at_level(logging.INFO,
                             logger="nora.platform.bridge.telemetry"):
            await bridge._capture_message(_message())
        assert len(_envelopes(journal)) == 1
        assert metrics.get("nora_bridge_queue_events_total",
                           platform="discord", event="captured") == 1
        assert any("bridge captured" in r.getMessage()
                   for r in caplog.records)

    async def test_lifecycle_carries_kinds_not_content(self, tmp_path,
                                                       caplog):
        """A synthetic credential-class error rides as its KIND only — the
        allowlist passes `error` (a closed vocabulary), never a message
        or credential bytes, even with the redactor off."""
        metrics = Metrics()
        with caplog.at_level(logging.WARNING,
                             logger="nora.platform.bridge.telemetry"):
            bt.lifecycle(metrics, "delivery_uncertain", platform="signal",
                         action_id="a-1",
                         error="credential_error",
                         leaked="token=sekrit-abc",
                         detail="Authorization: Bearer sekrit-abc")
        line = next(r.getMessage() for r in caplog.records
                    if "delivery_uncertain" in r.getMessage())
        assert "delivery_uncertain" in line
        # The unsafe fields never reached the line at all.
        for r in caplog.records:
            assert "sekrit-abc" not in str(r.__dict__)

    async def test_tracing_off_freezes_no_phantom_context(self, tmp_path):
        """No provider → the envelope's `trace` is exactly `{}` — the rule
        is no minted context, not an invented one."""
        bridge, journal, _, _ = _bridge(tmp_path)
        await bridge._capture_message(_message())
        assert _envelopes(journal)[0].trace == {}


# ---------------------------------------------------------------------------
# Finding 3 — the serialized media ref must name the custody key
# ---------------------------------------------------------------------------

class _Rpc:
    """The `_rpc` call shape — answers `getAttachment` from a byte map,
    records every call so a test can see which upstream handles were
    actually fetched."""
    def __init__(self, blobs: dict[str, bytes]) -> None:
        self._blobs = blobs
        self.calls: list[str] = []

    async def __call__(self, method: str, params: dict) -> Any:
        self.calls.append(str(params.get("id")))
        assert method == "getAttachment"
        raw = self._blobs[str(params.get("id"))]
        return {"data": base64.b64encode(raw).decode()}


class TestSignalCustodyBinding:
    def _wrapper(self, tmp_path):
        import importlib.util
        import sys as _sys
        from pathlib import Path as _Path
        from unittest.mock import AsyncMock

        spec = importlib.util.spec_from_file_location(
            "signal_bridge_wrapper_test",
            _Path(__file__).resolve().parents[2]
            / "deploy/signal-bridge/wrapper.py")
        mod = importlib.util.module_from_spec(spec)
        _sys.modules["signal_bridge_wrapper_test"] = mod
        spec.loader.exec_module(mod)
        cfg = mod.BridgeConfig(
            account_key=ACCT, token="t",  # noqa: S106 — synthetic fixture
            signal_http="http://unused", signal_account="+00",
            port=0, redis_url="redis://unused/0",
            database_url="sqlite+aiosqlite:///" + str(tmp_path / "b.db"),
            journal_path=tmp_path / "journal.jsonl",
            attach_dir=tmp_path / "att",
            projection_dir=tmp_path / "proj",
            correlation_key=b"\x33" * 16, key_version=1)
        # The enqueue path isn't under test here — the register_script seam
        # gets a call-shape answer, never a real Redis.
        fake_redis = SimpleNamespace(
            register_script=lambda lua: AsyncMock(
                return_value=["1-1", "eid"]))
        return mod, cfg, fake_redis

    async def test_custody_rebinds_the_serialized_platform_id(self,
                                                              tmp_path):
        """Finding 3: after `_custody`, `msg.media[i].platform_id` IS the
        hash key bytes answer to — the app fetches the reference it was
        handed, not the upstream handle that no byte answers to."""
        mod, cfg, fake_redis = self._wrapper(tmp_path)
        rpc = _Rpc({"up-one": b"blob-one", "up-two": b"blob-two"})
        bridge = mod.Bridge(cfg, rpc_client=rpc,
                            redis_client=fake_redis,
                            store=SimpleNamespace(_engine=SimpleNamespace()))
        cfg.attach_dir.mkdir()
        msg = NormalizedMessage(
            platform="signal", conversation_id="g",
            conversation_type="group", sender_id="+1", sender_name=None,
            timestamp=1, body="", raw={},
            media=(MediaRef(platform_id="up-one",
                            media_type="image/png", filename="a.png"),
                   MediaRef(platform_id="up-two",
                            media_type="image/png", filename="b.png")))
        bound, attachments = await bridge._custody(msg)
        key_one = hashlib.sha256(b"blob-one").hexdigest()[:32]
        key_two = hashlib.sha256(b"blob-two").hexdigest()[:32]
        # Index-stable rebind — every ref points at ITS custody key.
        assert [r.platform_id for r in bound.media] == [key_one, key_two]
        assert [a["key"] for a in attachments] == [key_one, key_two]
        assert (cfg.attach_dir / key_one).read_bytes() == b"blob-one"
        assert (cfg.attach_dir / key_two).read_bytes() == b"blob-two"

    async def test_a_failed_download_keeps_the_upstream_handle(
            self, tmp_path):
        """A custody failure does not fabricate a key: the ref keeps its
        upstream id and no attachment record claims custody — the app's
        fetch is honest `unavailable`, not a 404 under a wrong key."""
        mod, cfg, fake_redis = self._wrapper(tmp_path)
        rpc = _Rpc({"up-good": b"ok-bytes"})
        bridge = mod.Bridge(cfg, rpc_client=rpc,
                            redis_client=fake_redis,
                            store=SimpleNamespace(_engine=SimpleNamespace()))
        cfg.attach_dir.mkdir()
        msg = NormalizedMessage(
            platform="signal", conversation_id="g",
            conversation_type="group", sender_id="+1", sender_name=None,
            timestamp=1, body="", raw={},
            media=(MediaRef(platform_id="up-missing"),
                   MediaRef(platform_id="up-good")))
        bound, attachments = await bridge._custody(msg)
        assert next(
            r.platform_id for r in bound.media) == "up-missing"
        assert len(attachments) == 1

    async def test_custody_binds_before_the_journal_append(
            self, tmp_path):
        """The journal holds the BOUND reference — read the journaled
        envelope's `message.media` back and it names custody keys, not
        upstream handles (the actual handoff the app replays)."""
        mod, cfg, fake_redis = self._wrapper(tmp_path)
        rpc = _Rpc({"up-evt": b"evt-bytes"})
        bridge = mod.Bridge(cfg, rpc_client=rpc,
                            redis_client=fake_redis,
                            store=SimpleNamespace(_engine=SimpleNamespace()))
        cfg.attach_dir.mkdir()
        (cfg.projection_dir).mkdir(parents=True, exist_ok=True)
        (cfg.projection_dir / "signal.json").write_text(
            '{"revision": 1, "enabled": true, "rooms": ["g1"],'
            ' "dm_allowed_senders": []}')
        event = {"envelope": {
            "source": "+1", "sourceNumber": "+1", "timestamp": 9,
            "dataMessage": {"message": "pic", "timestamp": 9,
                            "groupInfo": {"groupId": "g1"},
                            "attachments": [{"id": "up-evt",
                                             "contentType": "image/png",
                                             "filename": "p.png"}]}}}
        await bridge._handle(event)
        rec = next(r for r in bridge.journal.recover()
                   if "envelope" in r)
        env = Envelope.from_fields(rec["envelope"])
        media = env.message["media"]
        key = hashlib.sha256(b"evt-bytes").hexdigest()[:32]
        assert media[0]["platform_id"] == key
        assert (cfg.attach_dir / key).read_bytes() == b"evt-bytes"


# ---------------------------------------------------------------------------
# Finding 6 — the real bootstrap seam + a dead collector
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Finding 2 (error paths) — queue-boundary logs carry types, never bodies
# ---------------------------------------------------------------------------

class _BoomStore:
    """A durable store whose transaction open raises a credential-bearing
    SDK error — the probe's exact shape."""

    def __init__(self, secret: str) -> None:
        self._engine = self
        self._secret = secret

    def begin(self) -> Any:
        raise RuntimeError(self._secret)


class _FakeConn:
    async def __aenter__(self) -> Any:
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False


class _FakeStore:
    """Just enough `SqlBridgeStore` for a CONFIRMED `run_owned` — the
    positive control: sanitization must not swallow a working action."""

    def __init__(self) -> None:
        self._engine = self
        self.states: list[str] = []

    def begin(self) -> _FakeConn:
        return _FakeConn()

    async def reserve_action(self, conn: Any, **kw: Any) -> tuple[str, None]:
        return "execute", None

    async def action_state(self, conn: Any, action_id: str,
                           state: str, **kw: Any) -> str:
        self.states.append(state)
        return state


class TestQueueErrorLogging:
    async def test_a_credential_bearing_failure_logs_the_type_only(
            self, caplog):
        """`log.exception` used to export the SDK exception BODY. Now the
        boundary logs the TYPE name and returns `unavailable` — the secret
        in the message never reaches structured logs (redactor OFF)."""
        secret = "generated-sensitive-sdk-message"  # noqa: S105 — planted fixture
        svc = DurableActionService(
            store=_BoomStore(secret), platform="signal", account_key="a",
            execute=_unused_execute, receipt_intents=lambda: frozenset())
        action = {"action_id": "a-log", "op": "send",
                  "owner": {"kind": "live_invocation", "id": "7"},
                  "request": {"target": {"id": "g1"}, "text": "x"}}
        with caplog.at_level(logging.ERROR, logger="nora.bridge.service"):
            reply = await svc.run(action)
        assert reply.state == "unavailable"  # never suppressed into success
        emitted = "\n".join(r.getMessage() for r in caplog.records)
        assert secret not in emitted
        assert "RuntimeError" in emitted        # stays VISIBLE by type
        assert all(r.exc_info is None
                   for r in caplog.records if r.name == "nora.bridge.service")

    async def test_a_confirmed_action_still_moves_its_outcome(self):
        """The positive control — a working reserve→execute→terminal still
        answers `confirmed`; the same log path is quiet because nothing
        failed, not because errors were suppressed."""
        store = _FakeStore()

        async def execute(action: dict[str, Any]) -> ActionReply:
            return ActionReply("confirmed")

        svc = DurableActionService(
            store=store, platform="signal", account_key="a",
            execute=execute, receipt_intents=lambda: frozenset({"e1"}))
        owner = ActionOwner.from_dict(
            {"kind": "event_receipt", "id": "e1", "generation": None})
        state = await svc.run_owned(
            action_id="a-ok", owner=owner, op="send", request={"x": 1})
        assert state == "confirmed"
        assert store.states == ["confirmed"]


async def _unused_execute(action: dict[str, Any]) -> ActionReply:
    raise AssertionError("the executor must not run — reserve failed")


# ---------------------------------------------------------------------------
# Finding 3 (bootstrap) — the rendered Compose env IS the tracing contract
# ---------------------------------------------------------------------------

class TestComposeTracingContract:
    def _env(self, service: str) -> set[str]:
        import yaml
        compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
        return {str(e).split("=")[0]
                for e in compose["services"][service].get("environment", [])}

    def test_signal_bridge_forwards_the_wrapper_env_contract(self):
        """`_setup_tracing` reads exactly `NORA_TRACING_{ENABLED,EXPORTER,
        ENDPOINT,HEADERS_ENV}` — every name must arrive through the rendered
        service env or the deployment can never turn tracing on."""
        env = self._env("signal-bridge")
        for name in ("NORA_TRACING_ENABLED", "NORA_TRACING_EXPORTER",
                     "NORA_TRACING_ENDPOINT", "NORA_TRACING_HEADERS_ENV"):
            assert name in env, name
        # The headers indirection target: HEADERS_ENV names the variable —
        # the deployment's documented one must be forwardable.
        assert "LANGFUSE_OTLP_HEADERS" in env

    def test_whatsapp_bridge_forwards_the_standard_otel_endpoint(self):
        """`tracing.js otlpEndpoint` reads `OTEL_EXPORTER_OTLP_TRACES_
        ENDPOINT` (then the base variant) — the standard name, same shape
        the instagram service already carries."""
        env = self._env("whatsapp-bridge")
        assert "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT" in env
        assert "OTEL_SERVICE_NAME" in env

    def test_every_bridge_tracing_value_is_optional(self):
        """Tracing is never load-bearing: no forwarded tracing value may be
        `:?` (refuse-to-boot when unset) — `:-` or a static default only."""
        import yaml
        compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
        for svc, names in (("signal-bridge", ("NORA_TRACING", "LANGFUSE")),
                           ("whatsapp-bridge", ("OTEL_",))):
            for e in compose["services"][svc]["environment"]:
                e = str(e)
                if any(n in e for n in names):
                    assert ":?" not in e, f"{svc}: {e}"


class TestBootstrap:
    def _run(self, code: str, *args: str,
             env_over: dict[str, str] | None = None):
        import subprocess
        env = dict(os.environ)
        env.update(env_over or {})
        return subprocess.run(  # noqa: S603 — fixed argv, test-local
            [sys.executable, "-c", code, *args],
            capture_output=True, text=True, cwd=str(ROOT), env=env)

    def test_signal_bootstrap_registers_a_real_provider(self):
        """`wrapper._setup_tracing()` — the exact call `__main__` makes —
        registers a REAL `TracerProvider` through the `NORA_TRACING_*` env
        contract, proven in a subprocess where no monkeypatch can pretend."""
        code = textwrap.dedent("""
            import importlib.util, sys
            spec = importlib.util.spec_from_file_location(
                "wrapper", sys.argv[1])
            mod = importlib.util.module_from_spec(spec)
            sys.modules["wrapper"] = mod
            spec.loader.exec_module(mod)
            mod._setup_tracing()
            from opentelemetry import trace
            print("PROVIDER:" + type(trace.get_tracer_provider()).__name__)
        """)
        proc = self._run(
            code, str(ROOT / "deploy/signal-bridge/wrapper.py"),
            env_over={"NORA_TRACING_ENABLED": "1",
                      "NORA_TRACING_EXPORTER": "console"})
        assert proc.returncode == 0, proc.stderr
        assert "PROVIDER:TracerProvider" in proc.stdout

    def test_signal_bootstrap_off_by_default(self):
        """Absent env → no provider: the bridge stays a no-op exporter,
        never minted context."""
        code = textwrap.dedent("""
            import importlib.util, os, sys
            os.environ.pop("NORA_TRACING_ENABLED", None)
            spec = importlib.util.spec_from_file_location(
                "wrapper", sys.argv[1])
            mod = importlib.util.module_from_spec(spec)
            sys.modules["wrapper"] = mod
            spec.loader.exec_module(mod)
            mod._setup_tracing()
            from opentelemetry import trace
            print("PROVIDER:" + type(trace.get_tracer_provider()).__name__)
        """)
        proc = self._run(code, str(ROOT / "deploy/signal-bridge/wrapper.py"))
        assert proc.returncode == 0, proc.stderr
        assert "PROVIDER:TracerProvider" not in proc.stdout

    def test_discord_entry_bootstrap_reads_the_same_config(
            self, tmp_path, example_config_path):
        """`entry._bootstrap_obs` — the exact call `__main__` makes — reads
        the SAME `config.toml` (`obs.logging` + `obs.tracing`): JSON logging
        comes up with the deployment's own settings and a real provider is
        registered when `[obs.tracing].enabled`."""
        cfg_text = example_config_path.read_text().replace(
            'exporter = "file"', 'exporter = "console"')
        cfg = tmp_path / "config.toml"
        cfg.write_text(cfg_text)
        code = textwrap.dedent("""
            import importlib.util, logging, sys
            spec = importlib.util.spec_from_file_location(
                "entry", sys.argv[1])
            mod = importlib.util.module_from_spec(spec)
            sys.modules["entry"] = mod
            spec.loader.exec_module(mod)
            mod._bootstrap_obs(sys.argv[2])
            from opentelemetry import trace
            print("PROVIDER:" + type(trace.get_tracer_provider()).__name__)
            fmt = logging.getLogger().handlers[0].formatter
            print("FORMATTER:" + type(fmt).__name__)
        """)
        proc = self._run(
            code, str(ROOT / "deploy/discord-bridge/entry.py"), str(cfg),
            env_over={
                "NORA_BRIDGE_CORRELATION_KEY": "aa" * 32,
                "NORA_BRIDGE_ACCOUNT_KEY": "it-acct",
                "NORA_BRIDGE_API_TOKEN": "it-bearer",
                "DISCORD_BOT_TOKEN": "it-bot",
                "DATABASE_URL": "postgresql+asyncpg://unused:5432/x",
                "NORA_REDIS_URL": "redis://unused:6379/0",
                "NORA_BRIDGE_JOURNAL": str(tmp_path / "journal.jsonl"),
                "NORA_BRIDGE_ATTACH_DIR": str(tmp_path / "att"),
            })
        assert proc.returncode == 0, proc.stderr
        assert "PROVIDER:TracerProvider" in proc.stdout
        # The structured-JSON pipeline, not basicConfig's text line.
        assert "FORMATTER:ProcessorFormatter" in proc.stdout


class TestCollectorOutage:
    async def test_a_dead_collector_never_blocks_the_capture_path(
            self, tmp_path, monkeypatch):
        """Finding 6: exporter errors are inside the SDK boundary — a
        collector that refuses the OTLP POST leaves the capture/journal
        path byte-identical."""
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor

        from nora.obs import tracing as _tracing

        provider = TracerProvider(sampler=_tracing.build_sampler())
        provider.add_span_processor(SimpleSpanProcessor(
            # 127.0.0.1:1 refuses instantly — a real network failure, not a
            # faked exporter answer.
            OTLPSpanExporter(endpoint="http://127.0.0.1:1/v1/traces")))
        monkeypatch.setattr(_tracing.trace, "get_tracer", provider.get_tracer)
        bridge, journal, _, _ = _bridge(tmp_path)
        await bridge._capture_message(_message())
        envs = _envelopes(journal)
        assert len(envs) == 1
        # The carrier was real — the context was frozen even though every
        # export attempt failed afterwards.
        assert envs[0].trace.get("traceparent")
        provider.shutdown()
