"""Synthetic sockets: public test addresses route locally only below validation."""

from __future__ import annotations

import asyncio
import socket
import ssl

import aiohttp
import pytest
from aiohttp import web

from nora.infra.preview import OpenGraphPreviewSource
from nora.infra.public_http import PublicTCPConnector, is_public_address
from nora.infra.vision.preprocess import download_media

PUBLIC = "93.184.216.34"


@pytest.fixture
async def destination(monkeypatch, aiohttp_server):
    hits = []

    async def serve(request):
        hits.append((request.host, request.path))
        if request.path == "/redirect":
            raise web.HTTPFound(f"http://private.invalid:{request.url.port}/secret")
        if request.path == "/page":
            return web.Response(
                text=f'<title>Public control</title><meta property="og:image" content="http://private.invalid:{request.url.port}/secret">',
                content_type="text/html",
            )
        return web.Response(text="<title>Public control</title>", content_type="text/html")

    app = web.Application()
    app.router.add_get("/{tail:.*}", serve)
    server = await aiohttp_server(app)
    connect = socket.socket.connect

    def route(sock, address):
        # Only the synthetic public address is routed to the fixture. A private
        # resolution goes directly to loopback and is observable as /secret.
        if address[0] == PUBLIC:
            address = ("127.0.0.1", address[1])
        return connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", route)
    return server.port, hits


def dns_answers(monkeypatch, resolve):
    loop = asyncio.get_running_loop()
    original = loop.getaddrinfo

    async def controlled(host, port, *args, **kwargs):
        if host.endswith(".invalid"):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in resolve(host)]
        return await original(host, port, *args, **kwargs)

    monkeypatch.setattr(loop, "getaddrinfo", controlled)


@pytest.mark.parametrize("kind", ["preview", "media"])
async def test_preview_and_media_rebinding_cannot_reach_private_server(
    monkeypatch, destination, kind
):
    port, hits = destination
    calls = []

    async def fetch(url):
        if kind == "preview":
            return await OpenGraphPreviewSource(fetch_image=False).preview_for(url=url)
        return await download_media(url)

    def resolve(host):
        calls.append(host)
        return [PUBLIC if len(calls) == 1 else "127.0.0.1"]

    dns_answers(monkeypatch, resolve)
    result = await fetch(f"http://rebind.invalid:{port}/secret")
    assert not hits, f"private server reached: {hits}"
    assert result is None or result == (b"", "")
    assert len(calls) == 2
    dns_answers(monkeypatch, lambda host: [PUBLIC])
    control = await fetch(f"http://public.invalid:{port}/control")
    assert control is not None and control != (b"", "")
    assert hits == [(f"public.invalid:{port}", "/control")]


async def test_redirect_and_image_secondary_fetch_refuse_private_answers(monkeypatch, destination):
    port, hits = destination
    dns_answers(monkeypatch, lambda host: ["127.0.0.1"] if host == "private.invalid" else [PUBLIC])
    assert await download_media(f"http://public.invalid:{port}/redirect") == (b"", "")
    card = await OpenGraphPreviewSource().preview_for(url=f"http://public.invalid:{port}/page")
    assert card and card.title == "Public control" and card.image_data_uri is None
    assert [path for _, path in hits] == ["/redirect", "/page"]


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "::1", "::ffff:127.0.0.1", "10.0.0.1", "224.0.0.1", "100.64.0.1"]
)
async def test_connector_itself_rejects_private_literals(address):
    async with PublicTCPConnector() as connector:
        with pytest.raises(aiohttp.ClientError, match="non-public"):
            await connector._resolve_host(address, 80)
    assert is_public_address(PUBLIC)
    assert is_public_address("::ffff:" + PUBLIC)


async def test_mixed_dns_answers_are_all_vetted(monkeypatch):
    dns_answers(monkeypatch, lambda host: [PUBLIC, "127.0.0.1"])
    async with PublicTCPConnector() as connector:
        with pytest.raises(aiohttp.ClientError, match="non-public"):
            await connector._resolve_host("mixed.invalid", 80)


async def test_https_retains_host_sni_and_certificate_validation(
    monkeypatch, tmp_path, destination
):
    _, hits = destination
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    process = await asyncio.create_subprocess_exec(
        "openssl",
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-keyout",
        str(key),
        "-out",
        str(cert),
        "-days",
        "1",
        "-subj",
        "/CN=public.invalid",
        "-addext",
        "subjectAltName=DNS:public.invalid",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    assert await process.wait() == 0
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    names = []
    context.set_servername_callback(lambda sock, name, ctx: names.append(name))
    trust = ssl.create_default_context(cafile=str(cert))
    dns_answers(monkeypatch, lambda host: [PUBLIC])

    async def serve(reader, writer):
        request = await reader.readuntil(b"\r\n\r\n")
        hits.append(request.decode())
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(serve, "127.0.0.1", 0, ssl=context)
    port = server.sockets[0].getsockname()[1]
    try:
        async with aiohttp.ClientSession(connector=PublicTCPConnector(ssl=trust)) as session:
            async with session.get(f"https://public.invalid:{port}/") as response:
                assert await response.text() == "ok"
            with pytest.raises(aiohttp.ClientConnectorCertificateError):
                await session.get(f"https://wrong.invalid:{port}/")
        assert f"Host: public.invalid:{port}" in hits[0]
        assert names == ["public.invalid", "wrong.invalid"]
        assert len(hits) == 1
    finally:
        server.close()
        await server.wait_closed()
