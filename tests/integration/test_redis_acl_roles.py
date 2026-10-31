"""Redis ACL roles — the `users.acl` lines docker-compose.yml's redis
entrypoint writes, exercised against a real server.

The compose lines are the artifact under test: they are parsed out of
docker-compose.yml and applied verbatim (generated passwords substituted for
the `$$ENV` placeholders) via `ACL SETUSER`, so an invalid modifier like the
`+xpending_range` that broke boot is a red here, and every grant is the one
the deployed process actually authenticates under. `secrets.token_urlsafe`
passwords are used throughout — the charset .env.example mandates — so URL
userinfo and ACL `>`-syntax parsing are exercised together.
"""
from __future__ import annotations

import dataclasses
import re
import secrets
from pathlib import Path

import pytest
import redis.asyncio as aioredis
from testcontainers.redis import RedisContainer

from nora.platform.bridge.envelope import Envelope

COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yml"

ROLE_VARS = {
    "nq-producer": "NORA_REDIS_PRODUCER_PASSWORD",
    "nq-consumer": "NORA_REDIS_CONSUMER_PASSWORD",
    "nq-inspector": "NORA_REDIS_INSPECTOR_PASSWORD",
}


def _env(event_id: str, *, account: str, conv: str, source: str) -> Envelope:
    return Envelope(
        platform="signal", account_key=account, kind="message",
        source_key=source, event_id=event_id, conversation_id=conv,
        conversation_type="group", sender_id="+1001", sender_name=None,
        sent_ms=1, received_ms=1, queued_ms=1, time_quality="upstream",
        message={"id": event_id, "body": "hello"})


def _acl_lines() -> dict[str, str]:
    """`user <name> on >$$VAR <grants>` lines from the redis entrypoint —
    returned with a GENERATED password substituted for the `$$VAR`."""
    text = COMPOSE.read_text()
    out = {}
    for name, var in ROLE_VARS.items():
        m = re.search(rf'"user {name} on >\$\${var} ([^"]+)"', text)
        assert m, f"no `user {name}` ACL line in docker-compose.yml"
        out[name] = f"{name} on >{secrets.token_urlsafe(32)} {m.group(1)}"
    assert "'user default off'" in text, "default user must be OFF"
    return out


def test_entrypoint_refuses_unsafe_password_charset():
    """Role passwords land in `redis://user:pass@` URLs and ACL `>`-fields:
    anything outside `[A-Za-z0-9_-]` (the token_urlsafe charset .env.example
    mandates) must refuse at the entrypoint BEFORE the acl file renders —
    never a silently corrupted ACL or unparseable URL."""
    text = COMPOSE.read_text()
    guard = text.index("*[!A-Za-z0-9_-]*")
    render = text.index("> /run/acl/users.acl")
    for var in ROLE_VARS.values():
        assert f'case "${var}"' in text, f"no charset guard for {var}"
    assert guard < render, "charset guard must precede the ACL file render"


@pytest.fixture(scope="module")
def acl_redis():
    """Disposable Redis with the real compose ACLs loaded."""
    with RedisContainer("redis:7.4-alpine") as c:
        def cli(*a):
            return c.exec(["redis-cli", *[str(x) for x in a]])
        creds = {}
        for name, spec in _acl_lines().items():
            tokens = spec.split()
            pw = tokens[2].removeprefix(">")
            creds[name] = pw
            rc, out = cli("ACL", "SETUSER", *tokens)
            assert rc == 0, f"compose ACL line for {name} rejected: {out}"
        # LAST: `default off` ends unauthenticated access — any earlier and
        # the management connection itself is locked out. Boot order in the
        # aclfile has no such constraint.
        rc, out = cli("ACL", "SETUSER", "default", "off")
        assert rc == 0, out
        host = c.get_container_host_ip()
        port = c.get_exposed_port(6379)
        yield {n: f"redis://{n}:{pw}@{host}:{port}/0" for n, pw in creds.items()}


async def _probe(url: str, fn):
    client = aioredis.from_url(url)
    try:
        return await fn(client)
    finally:
        await client.aclose()


async def test_producer_cold_script_enqueue(acl_redis):
    """The real StreamProducer's first call: EVALSHA → NOSCRIPT → SCRIPT
    LOAD → EVALSHA, plus the GET/SET/XADD the Lua runs under this user.
    A replay under a rotated event_id returns the ORIGINAL stream id."""
    from nora.platform.bridge.queue import StreamProducer

    async def run(client):
        producer = StreamProducer(client, "nq:in:signal:acl1")
        env = _env("v1:aaa", account="acl1", conv="room-1", source="src-1")
        sid1, canon1 = await producer.enqueue(env)
        sid2, canon2 = await producer.enqueue(
            dataclasses.replace(env, event_id="v2:rot"))
        return sid1, canon1, sid2, canon2

    sid1, canon1, sid2, canon2 = await _probe(acl_redis["nq-producer"], run)
    assert sid1 == sid2 and canon1 == canon2 == "v1:aaa"


async def test_producer_ensure_group_and_denied_rights(acl_redis):
    """IG's startup EnsureGroup must work under the producer role; the same
    role must NOT ack, read outside nq:*, or touch server config."""
    from nora.platform.bridge.queue import ensure_group

    async def run(client):
        await ensure_group(client, "nq:in:signal:acl1")
        # group was created under the producer's own rights — xinfo_groups
        # is not its business: consumers inspect, producers write.
        denied = []
        for coro in (
            client.xack("nq:in:signal:acl1", "nora-v1", "0-0"),
            client.xinfo_groups("nq:in:signal:acl1"),
            client.get("some:other:key"),
            client.set("nq:in:signal:acl1:aux", "x"),
            client.config_get("maxmemory"),
        ):
            try:
                await coro
                denied.append("ALLOWED")
            except aioredis.ResponseError:
                denied.append("DENIED")
        return denied

    results = await _probe(acl_redis["nq-producer"], run)
    # xack + xinfo_groups are command denials; the non-nq GET is a key denial;
    # the nq:* SET and CONFIG GET are the two surprises worth pinning: SET
    # inside the namespace IS allowed (the dedupe Lua needs it) — a write
    # outside stream semantics, documented as the role's own keys' scope.
    assert results == ["DENIED", "DENIED", "DENIED", "ALLOWED", "DENIED"]


async def test_consumer_claim_ack_inspect_and_denied_write(acl_redis):
    """The app's full read path under nq-consumer: group create (reader wins
    the race), XREADGROUP, XACK, XAUTOCLAIM, plus the QueueInspector reads
    (XINFO GROUPS, XRANGE, XPENDING both forms, TIME). XADD stays denied —
    the consumer never writes."""
    from nora.platform.bridge.inspection import QueueInspector
    from nora.platform.bridge.queue import ensure_group

    async def seed(client):  # producer seeds one entry
        from nora.platform.bridge.queue import StreamProducer
        await StreamProducer(client, "nq:in:signal:acl2").enqueue(
            _env("v1:ccc", account="acl2", conv="room-2", source="src-2"))

    await _probe(acl_redis["nq-producer"], seed)

    async def run(client):
        stream = "nq:in:signal:acl2"
        await ensure_group(client, stream)
        batch = await client.xreadgroup("nora-v1", "reader-1",
                                        {stream: ">"}, count=10)
        entries = batch[0][1] if batch else []
        assert entries, "consumer must read the producer's entry"
        sid = entries[0][0]
        sid = sid.decode() if isinstance(sid, bytes) else sid
        await client.xack(stream, "nora-v1", sid)
        claimed = await client.xautoclaim(stream, "nora-v1", "reader-2",
                                          min_idle_time=0, start_id="0-0")
        snap = await QueueInspector(client).inspect([stream],
                                                    platform="signal")
        denied = []
        for coro in (client.xadd(stream, {"e": "{}"}),
                     client.set("nq:anything", "x")):
            try:
                await coro
                denied.append("ALLOWED")
            except aioredis.ResponseError:
                denied.append("DENIED")
        return entries, claimed, snap, denied

    _entries, claimed, snap, denied = await _probe(acl_redis["nq-consumer"], run)
    assert claimed is not None
    assert snap.status in ("ok", "nothing_found")
    assert denied == ["DENIED", "DENIED"]


async def test_inspector_read_only(acl_redis):
    async def seed(client):
        from nora.platform.bridge.queue import StreamProducer
        await StreamProducer(client, "nq:in:signal:acl3").enqueue(
            _env("v1:ddd", account="acl3", conv="room-3", source="src-3"))

    await _probe(acl_redis["nq-producer"], seed)

    async def run(client):
        stream = "nq:in:signal:acl3"
        await client.xrange(stream, min="-", max="+", count=5)
        await client.xlen(stream)
        denied = []
        for coro in (client.xadd(stream, {"e": "{}"}),
                     client.eval("return 1", 0)):
            try:
                await coro
                denied.append("ALLOWED")
            except aioredis.ResponseError:
                denied.append("DENIED")
        return denied

    assert await _probe(acl_redis["nq-inspector"], run) == ["DENIED", "DENIED"]


async def test_default_user_is_off(acl_redis):
    url = acl_redis["nq-producer"]
    anon = aioredis.from_url(re.sub(r"nq-producer:[^@]+@", "default:x@",
                                   url))
    try:
        with pytest.raises(aioredis.AuthenticationError):
            await anon.ping()
    finally:
        await anon.aclose()
