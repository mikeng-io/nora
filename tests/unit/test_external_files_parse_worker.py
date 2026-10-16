"""Fetched bytes are parsed in a killable child, never on the event loop
(a small crafted PDF can hold the loop for many seconds).

`parse_isolated` is the one parse path the adapter has: a deadline kills the
child and answers `refused` `TOO_SLOW`, the loop keeps running meanwhile, and
the child is started without Nora's environment (her secrets)."""

from __future__ import annotations

import asyncio
import sys
import time
import zlib

import pytest

from nora.domain.external_files import NOT_TEXT, TOO_SLOW, ExternalFileError
from nora.infra.external_files import parse
from nora.infra.external_files.parse import parse_isolated


def _slow_pdf(pages: int = 40, mb: float = 1.0) -> bytes:
    """Many pages sharing one large content stream: about 12 KB that pypdf
    takes tens of seconds over."""
    unit = b"BT /F1 1 Tf 0 0 Td ET\n"
    comp = zlib.compress(unit * int(mb * (1 << 20) / len(unit)), 9)
    kids = " ".join(f"{4 + i} 0 R" for i in range(pages)).encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [" + kids + b"] /Count %d >>" % pages,
        b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(comp) + comp + b"\nendstream",
    ]
    objs += [
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 10 10] /Contents 3 0 R /Resources "
        b"<< /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> >>"
    ] * pages
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


async def test_a_parse_past_its_deadline_is_killed_and_refused_too_slow():
    started = time.monotonic()
    with pytest.raises(ExternalFileError) as raised:
        await parse_isolated(
            "pdf", _slow_pdf(), max_bytes=10 << 20, max_chars=2_000_000, deadline_seconds=1.5,
        )
    assert (raised.value.status, raised.value.reason) == ("refused", TOO_SLOW)
    assert time.monotonic() - started < 6


async def test_the_event_loop_keeps_running_while_a_file_is_parsed():
    ticks = 0

    async def tick() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.05)
            ticks += 1

    ticker = asyncio.create_task(tick())
    with pytest.raises(ExternalFileError):
        await parse_isolated(
            "pdf", _slow_pdf(), max_bytes=10 << 20, max_chars=2_000_000, deadline_seconds=1.5,
        )
    ticker.cancel()
    assert ticks >= 15  # ~30 at 1.5 s; a parse on the loop would allow ~0


async def test_a_plain_file_reads_through_the_child():
    got = await parse_isolated("plain", b"Lunch set 88\n", max_bytes=1000, max_chars=1000)
    assert got.strip() == "Lunch set 88"


async def test_the_parsers_refusal_crosses_back_as_itself():
    with pytest.raises(ExternalFileError) as raised:
        await parse_isolated("docx", b"not a zip", max_bytes=1000, max_chars=1000)
    assert (raised.value.status, raised.value.reason) == ("refused", NOT_TEXT)


async def test_an_unknown_parser_is_refused_without_a_child(monkeypatch):
    async def never(*_a, **_k):
        raise AssertionError("a child was started")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", never)
    with pytest.raises(ExternalFileError) as raised:
        await parse_isolated("exe", b"MZ", max_bytes=10, max_chars=10)
    assert raised.value.reason == NOT_TEXT


async def test_the_child_gets_none_of_noras_environment(monkeypatch):
    """The process that reads untrusted bytes never holds a secret."""
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "sekrit-child")
    seen: dict[str, object] = {}
    real = asyncio.create_subprocess_exec

    async def spy(*args, **kwargs):
        seen.update(kwargs)
        return await real(*args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spy)
    await parse_isolated("plain", b"hi", max_bytes=10, max_chars=10)
    env = seen["env"]
    assert isinstance(env, dict)
    assert set(env) <= {"PATH", "PYTHONPATH", "LANG"}
    assert "sekrit-child" not in repr(env)


def test_a_child_without_an_answer_is_not_text():
    for out in (b"", b"garbage", b"[]", b'{"status": "ok"}'):
        with pytest.raises(ExternalFileError) as raised:
            parse._answer(1, out)
        assert raised.value.reason == NOT_TEXT


@pytest.mark.skipif(sys.platform != "linux", reason="RLIMIT_AS is enforced on Linux only")
async def test_a_child_out_of_memory_is_refused_not_crashed():
    with pytest.raises(ExternalFileError) as raised:
        await parse_isolated(
            "plain", b"x" * (8 << 20), max_bytes=10 << 20, max_chars=10 << 20,
            memory_bytes=64 << 20,
        )
    assert raised.value.status == "refused"
