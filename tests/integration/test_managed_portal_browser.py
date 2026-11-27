"""The managed portal code sign-in proved through a real browser.

One browser lifecycle over the disposable Ory + Postgres graph: generated
accounts register through the real roster boundary; a real Chromium drives the
production Next app's login UI ( `NORA_IDENTITY_BACKEND=managed`,
`NORA_API_BASE` on the same aiohttp boundary this module owns) through the
genuine Auth.js credentials callback — `verifyManagedCode` /
`captureManagedSession` against `/app/v1/otp/*` and
`/app/v1/management/auth/exchange`. The retained session then scopes the
portal's reads and dies on unlink; an unrelated account keeps its access.

The browser never sees a key: OTP delivery lands in `nora.testing`'s recording
adapter (Nora's generated transport for this harness); every account, session
and authorization store is the real one — `SqlBindingStore`, `SqlOtpStore`,
`SqlDocumentStore` and pinned Kratos/Keto. Locally minted `Nora-Authentication`
assertions stand in for direct-HTTP probes only; provider callbacks and live
daemon delivery stay with the parent.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import time
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import aiohttp
import pytest
import sqlalchemy as sa
from aiohttp import web

from nora.app.documents_service import rooms_named
from nora.app.otp import OtpService
from nora.config_identity import ManagedOryConfig
from nora.domain.documents import RevisionIn
from nora.domain.identity import Identifier
from nora.domain.managed import OperationJournalState, room_scope_id
from nora.domain.management import Actor, ActorKind, Scope, ScopeKind
from nora.infra.sql.documents import SqlDocumentStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.otp import SqlOtpStore
from nora.obs.metrics import Metrics
from nora.people import AccountSessions, PortalScopes, wire
from nora.people.app.holder_link import HolderLinkService
from nora.persons import Persons
from nora.platform.signal.consent import SignalLinkDelivery
from nora.policy import load_policy
from nora.testing import FakeRecordStore
from nora.testing.fakes import FakeAdapter
from nora.web.api import register_web_routes
from nora.web.holder_links import register_holder_routes
from nora.web.managed_viewer import register_managed_viewer
from nora.web.management_api import ProvisioningCredential, register_management_routes
from nora.web.management_auth import AuthenticationIssuer, AuthenticationVerifier
from nora.web.management_sessions import register_session_routes
from nora.web.otp import register_otp_routes
from tests.conftest import build_test_doctrine_store
from tests.integration.managed_ory import ory as ory
from tests.integration.test_managed_identity_journey import (
    BEARER,
    CHECK,
    CREDENTIAL,
    DOCUMENTS,
    EXCHANGE,
    GATEWAY,
    OTP_TOKEN,
    ROOM_A,
    ROOM_B,
    ROOM_C,
    ROSTERS,
    UNLINK,
    _Sender,
    auth,
    claims,
    ident_json,
    roster,
)
from tests.integration.test_managed_identity_journey import (
    POLICY_TOML as _POLICY_BASE,
)
from tests.web.test_api import NO_SKILLS_DIR

SIG_A = Identifier("signal", "phone", "+85290000101")
WA_B = Identifier("whatsapp", "phone", "+85290000103")
WA_C = Identifier("whatsapp", "phone", "+85290000104")
SIG_C = Identifier("signal", "phone", "+85290000105")
DISC_D = Identifier("discord", "user_id", "generated-discord-portal")
ROOM_W = Scope(ScopeKind.ROOM, room_scope_id("whatsapp", "generated-room-w"))

_METHOD = {"signal": "signal_code", "whatsapp": "whatsapp_code",
           "discord": "discord_oauth"}  # discord has no `*_code` (DIRECT_PLATFORMS)

POLICY_TOML = _POLICY_BASE + """
[[conversations]]
platform = "whatsapp"
id = "generated-room-w"
unit = "generated-unit-b"
label = "Room W"
persons = ["generated-member"]
"""

WA_TOKEN = "generated-bridge-token-wa"  # noqa: S105 -- generated fixture credential
BEARER_WA = {"Authorization": f"Bearer {WA_TOKEN}"}
CREDENTIAL_WA = ProvisioningCredential(
    Actor(ActorKind.SERVICE, service_id="generated-bridge-wa", sources={"whatsapp"}),
    WA_TOKEN, frozenset({ROOM_A, ROOM_B, ROOM_C, ROOM_W}))

# The portal signs its own assertions; the Python boundary verifies kid "portal".
PORTAL_SECRET = "generated-portal-assertion-key-0123456"  # noqa: S105
PORTAL = AuthenticationIssuer("portal", PORTAL_SECRET.encode(), {
    "signal_code": "nora-otp", "whatsapp_code": "nora-otp"})
AUTH_SECRET = "generated-authjs-session-secret-01234567"  # noqa: S105

APP_DIR = Path(__file__).resolve().parents[2] / "app"
DRIVER = APP_DIR / "e2e" / "managed-portal-driver.mjs"
NEXT_BIN = APP_DIR / "node_modules" / "next" / "dist" / "bin" / "next"
EVIDENCE = Path(os.environ.get(
    "NORA_PORTAL_BROWSER_EVIDENCE", "/tmp/nora-managed-intake/nor6-portal-browser"))  # noqa: S108


def _write_receipt(evidence: list[dict]) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / "receipt.json").write_text(json.dumps(
        {"journey": "managed-portal-browser", "evidence": evidence}, indent=2))


class _PortalWorld:
    def __init__(self, engine, services, holder, client, sig_adapter, wa_adapter,
                 documents, ory) -> None:
        self.engine = engine
        self.services = services
        self.holder = holder
        self.client = client
        self.sig_adapter = sig_adapter
        self.wa_adapter = wa_adapter
        self.documents = documents
        self.ory = ory
        self.traffic: list[str] = []
        self.evidence: list[dict] = []

    def note(self, step: str, **fields) -> None:
        self.evidence.append({"step": step, **fields})

    async def person_id(self, account: Identifier) -> str:
        record = await self.services.bindings.record_for(account=account)
        assert record is not None, f"{account} is not bound"
        return record.person_id

    async def binding(self, account: Identifier) -> dict:
        """The retained binding a session assertion must carry, read back from SQL."""
        record = await self.services.bindings.record_for(account=account)
        assert record is not None
        person = await self.services.bindings.person(record.person_id)
        assert person is not None
        return {"person_id": record.person_id,
                "person_revision": str(person.revision),
                "account_id": record.account_id,
                "account_revision": str(record.revision)}

    def session_headers(self, account: Identifier, *, binding, path, body=b"",
                        http="POST"):
        return auth(claims(account=account, purpose="session",
                           method=_METHOD[account.platform],
                           path=path, body=body, http=http, binding=binding))


class _Browser:
    """The stdio actuator: pytest sends one JSON command, gets one result back.

    Every round-trip goes through a worker thread so the test's event loop —
    which is serving the aiohttp boundary the Next server calls — never blocks.
    """

    def __init__(self) -> None:
        self._proc = subprocess.Popen(  # noqa: S603 -- fixed local node argv
            ["node", str(DRIVER)], cwd=str(APP_DIR),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self._seq = 0
        ready = json.loads(self._proc.stdout.readline())
        assert ready.get("ready"), "browser driver did not launch"

    async def cmd(self, **command) -> dict:
        self._seq += 1
        command["id"] = self._seq
        return await asyncio.wait_for(asyncio.to_thread(self._roundtrip, command), 300)

    def _roundtrip(self, command: dict) -> dict:
        self._proc.stdin.write(json.dumps(command) + "\n")
        self._proc.stdin.flush()
        line = self._proc.stdout.readline()
        if not line:
            raise RuntimeError("browser driver exited mid-command")
        reply = json.loads(line)
        assert reply["id"] == command["id"]
        if not reply.get("ok"):
            raise AssertionError(
                f"browser command {command['cmd']} failed: {reply.get('error')}")
        return reply.get("result") or {}

    def close(self) -> None:
        with suppress(Exception):
            self._roundtrip({"id": 0, "cmd": "shutdown"})
        try:
            self._proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait(timeout=15)


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


# The code sign-in form carries the `locale` hidden field; the OAuth forms do not.
CODE_FORM = 'form:has(input[name="locale"])'


async def _dump_browser_state(browser: _Browser, world: _PortalWorld,
                              name: str, base: str) -> None:
    """Preserve the exact browser + upstream state when a UI leg fails."""
    await asyncio.to_thread(EVIDENCE.mkdir, parents=True, exist_ok=True)
    (EVIDENCE / "traffic.txt").write_text("\n".join(world.traffic))
    (EVIDENCE / f"{name}-page.html").write_text(
        (await browser.cmd(cmd="html", name=name))["html"])
    (EVIDENCE / f"{name}-session.json").write_text(
        (await browser.cmd(cmd="api", name=name,
                           url=f"{base}/api/auth/session"))["body"]
        if base else "skipped")
    (EVIDENCE / f"{name}-responses.json").write_text(json.dumps(
        (await browser.cmd(cmd="responses", name=name))["responses"], indent=1))
    (EVIDENCE / f"{name}-pageerrors.txt").write_text(
        "\n".join((await browser.cmd(cmd="pageerrors", name=name))["errors"]))


async def _send_code(browser: _Browser, world: _PortalWorld, name: str,
                     sent: list, *, platform: str = "signal") -> str:
    """Click the send button and read the challenge rendered by the UI."""
    await browser.cmd(cmd="click_role", name=name, role="button",
                      text="Send me a code")
    shown = await browser.cmd(cmd="wait_text", name=name,
                              text="Nora just sent a six-digit code", timeout_ms=25_000)
    if not shown["visible"]:
        await _dump_browser_state(browser, world, name, "")
    assert shown["visible"], "code step never rendered after clicking Send"
    world.note("sign-in-dispatch", who=name, leg="send", how="click")
    challenge = await browser.cmd(cmd="eval", name=name, expression=(
        "[...document.querySelectorAll('form input[type=hidden]')]"
        ".map(i => { try { return JSON.parse(i.value) } catch { return null } })"
        ".find(v => Array.isArray(v) && v[0] && v[0].challengeId)"
        "?.[0].challengeId ?? null"))
    delivered = challenge["value"]
    known = world.challenges.get(platform, [])
    if delivered in known:
        index = known.index(delivered)
        for _ in range(50):
            if len(sent) > index:
                return re.search(r"\b(\d{6})\b", sent[index][1]).group(1)
            await asyncio.sleep(0.1)
    return _code(sent)


async def _browser_session(browser: _Browser, name: str, base: str) -> dict:
    return json.loads((await browser.cmd(
        cmd="api", name=name, url=f"{base}/api/auth/session"))["body"]) or {}


async def _verify_code(browser: _Browser, world: _PortalWorld, name: str,
                       ) -> None:
    """Click Sign in and require the app's own redirect to the portal."""
    await browser.cmd(cmd="click_role", name=name, role="button", text="Sign in")
    inside = await browser.cmd(cmd="wait_url", name=name, regex=r"/en/today$",
                               timeout_ms=30_000)
    assert inside["matched"], f"sign-in landed at {inside['url']}"
    world.note("sign-in-dispatch", who=name, leg="verify", how="click")


async def _start_portal(api_base: str, tmp_path: Path):
    """Run the production app against this fixture's private HTTP boundary."""
    await asyncio.to_thread(EVIDENCE.mkdir, parents=True, exist_ok=True)
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    env = dict(os.environ)
    env.update({
        "NORA_IDENTITY_BACKEND": "managed",
        "NORA_API_BASE": api_base,
        "NORA_OTP_API_TOKEN": OTP_TOKEN,
        "NORA_AUTHENTICATION_ISSUER": "portal",
        "NORA_AUTHENTICATION_KEY": PORTAL_SECRET,
        "AUTH_SECRET": AUTH_SECRET,
        "NEXT_TELEMETRY_DISABLED": "1",
        "NORA_DOWNLOADS_DIR": str(tmp_path / "downloads"),
    })
    build_log = (EVIDENCE / "next-build.log").open("w")
    build = await asyncio.to_thread(
        subprocess.run,
        ["node", str(NEXT_BIN), "build"],
        cwd=str(APP_DIR), env=env, stdout=build_log,
        stderr=subprocess.STDOUT, timeout=600)
    build_log.close()
    if build.returncode != 0:
        raise RuntimeError(f"next build failed rc={build.returncode}; see {EVIDENCE}")
    standalone = APP_DIR / ".next" / "standalone"
    shutil.copytree(APP_DIR / ".next" / "static",
                    standalone / ".next" / "static", dirs_exist_ok=True)
    public = APP_DIR / "public"
    if public.is_dir():
        shutil.copytree(public, standalone / "public", dirs_exist_ok=True)
    env["PORT"] = str(port)
    env["HOSTNAME"] = "127.0.0.1"
    log_path = EVIDENCE / "next-start.log"
    log = log_path.open("w")
    proc = await asyncio.to_thread(
        subprocess.Popen,
        ["node", str(standalone / "server.js")],
        cwd=str(APP_DIR), env=env, stdout=log, stderr=subprocess.STDOUT,
        start_new_session=True)
    deadline = time.monotonic() + 240
    try:
        async with aiohttp.ClientSession() as poll:
            while True:
                if proc.poll() is not None:
                    raise RuntimeError(
                        f"next start exited rc={proc.returncode}; see {log_path}")
                try:
                    async with poll.get(
                            f"{base}/en/login",
                            timeout=aiohttp.ClientTimeout(total=120)) as response:
                        if response.status == 200:
                            break
                except (aiohttp.ClientError, TimeoutError):
                    pass
                if time.monotonic() > deadline:
                    raise RuntimeError(f"next start never served /en/login; see {log_path}")
                await asyncio.sleep(0.5)
    except Exception:
        _stop_process(proc)
        log.close()
        raise
    return proc, base, log


def _stop_process(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=15)


@pytest.fixture
async def portal_world(pg_module_url, ory, aiohttp_client, tmp_path):
    """The journey's composition plus the portal's issuer and a WhatsApp
    credential/adapter — `wire.managed_services`, the real private routes and
    the real stores; only the send transport is the recording fake."""
    engine = create_sql_engine(pg_module_url)
    async with engine.begin() as connection:
        # Same ownership reset as the journey module: this module's database.
        await connection.execute(sa.text("SET LOCAL nora.row_history = 'off'"))
        rows = await connection.execute(sa.text(
            "SELECT tablename FROM pg_tables WHERE schemaname='public'"
            " AND tablename <> 'alembic_version'"))
        for (table,) in rows:
            await connection.execute(sa.text(f'TRUNCATE "{table}" CASCADE'))
    try:
        async with aiohttp.ClientSession() as session:
            policy_path = tmp_path / "policy.toml"
            policy_path.write_text(POLICY_TOML)
            policy = load_policy(policy_path)
            services = wire.managed_services(
                managed=ManagedOryConfig(
                    kratos_admin_url=ory.kratos_admin,
                    kratos_public_url=ory.kratos_public,
                    keto_read_url=ory.keto_read,
                    keto_write_url=ory.keto_write,
                    timeout_seconds=15.0),
                session=session, policy=policy, engine=engine, credentials={})
            sender = _Sender()
            delivery = SignalLinkDelivery("http://generated-daemon", sender=sender)
            now = [datetime.now(UTC).replace(microsecond=0)]
            holder = HolderLinkService(
                services.bindings, services.registry, services.links,
                services.publication, services.registration,
                signal_delivery=delivery, clock=lambda: now[0])
            sig_adapter, wa_adapter = FakeAdapter(), FakeAdapter()
            wa_adapter.platform = "whatsapp"
            doctrine = build_test_doctrine_store()
            otp = OtpService(
                store=SqlOtpStore(engine), persons=Persons(by_person={}, index={}),
                bindings=services.bindings, registry=services.registry,
                adapters={"signal": sig_adapter, "whatsapp": wa_adapter},
                records=FakeRecordStore(), doctrine_store=doctrine,
                canary="generated-canary", metrics=Metrics(),
                secret=b"generated-otp-key", ttl=timedelta(minutes=5),
                max_attempts=5, max_per_hour=20, max_per_day=40,
                clock=lambda: datetime.now(UTC))
            documents = SqlDocumentStore(engine)
            app = web.Application()

            # Every upstream call the portal makes is evidence: a blank list
            # means the app never reached the boundary, a 5xx where it errored.
            traffic: list[str] = []

            challenges: dict[str, list[str]] = {}

            @web.middleware
            async def record(request, handler):
                # Delivery order and rendered challengeId bind the page's code.
                # aiohttp caches the body for the handler.
                request_platform = None
                if (request.method == "POST"
                        and request.path == "/app/v1/otp/request"):
                    try:
                        request_platform = (await request.json()).get("platform")
                    except ValueError:
                        request_platform = None
                try:
                    response = await handler(request)
                except Exception as error:
                    traffic.append(f"{request.method} {request.path_qs} "
                                   f"-> EXC {type(error).__name__}: {error}")
                    raise
                traffic.append(f"{request.method} {request.path_qs} -> {response.status}")
                if (request_platform and response.status == 202):
                    challenges.setdefault(request_platform, []).append(
                        json.loads(response.text)["challengeId"])
                return response

            app.middlewares.append(record)
            register_web_routes(
                app, store=FakeRecordStore(),

                persons=Persons(by_person={}, index={}),
                started_at=0.0, presence=None, presence_circles=(),
                skills_dir=NO_SKILLS_DIR,
                doctrine_store=doctrine, documents=documents,
                rooms_of=lambda pid: rooms_named(policy, pid))
            verifier = AuthenticationVerifier([GATEWAY, PORTAL])
            register_session_routes(
                app, sessions=AccountSessions(services.bindings, services.registry),
                verifier=verifier)
            register_holder_routes(app, holder=holder, verifier=verifier)
            register_otp_routes(app, otp=otp, api_token=OTP_TOKEN)
            register_management_routes(
                app, services=services, credentials=(CREDENTIAL, CREDENTIAL_WA),
                authentication=verifier)
            register_managed_viewer(
                app, bindings=services.bindings, registry=services.registry,
                scopes=PortalScopes(services.bindings, services.topology,
                                    services.authority),
                verifier=verifier)
            client = await aiohttp_client(app)
            world = _PortalWorld(engine, services, holder, client, sig_adapter,
                                 wa_adapter, documents, ory)
            world.traffic = traffic
            world.challenges = challenges
            yield world
    finally:
        await engine.dispose()


def _code(sent: list) -> str:
    return re.search(r"\b(\d{6})\b", sent[-1][1]).group(1)


async def _signal_portal(browser, world, base, person_a, person_b):
    services = world.services
    # ---- anonymous access refuses, over page and API ----
    await browser.cmd(cmd="new", name="anon")
    await browser.cmd(cmd="goto", name="anon", url=f"{base}/en/documents")
    landed = await browser.cmd(cmd="wait_url", name="anon", regex=r"/en/login")
    assert landed["matched"], f"anonymous stayed at {landed['url']}"
    anon_api = await browser.cmd(cmd="api", name="anon",
                                 url=f"{base}/api/v1/documents")
    assert anon_api["status"] == 401

    # ---- Signal code sign-in: UI -> Auth.js credentials -> real backend ----
    await browser.cmd(cmd="new", name="a",
                      headers={"x-forwarded-for": "198.51.100.11"})
    first = await browser.cmd(cmd="goto", name="a", url=f"{base}/en/login")
    assert first["status"] == 200
    # Managed backend: no development picker even under `next dev`.
    assert not (await browser.cmd(cmd="visible", name="a",
                                  text="Development sign-in"))["visible"]
    assert (await browser.cmd(cmd="visible", name="a",
                              text="Sign in with Discord"))["visible"]
    await browser.cmd(cmd="fill", name="a", label="Your phone number",
                      value="90000101")
    code = await _send_code(browser, world, "a", world.sig_adapter.sent)
    target, _text = world.sig_adapter.sent[-1]
    assert target.conversation_id == SIG_A.value

    # A wrong code refuses through the same genuine callback.
    wrong = "000000" if code != "000000" else "111111"
    await browser.cmd(cmd="fill", name="a", label="The code", value=wrong)
    await browser.cmd(cmd="click_role", name="a", role="button", text="Sign in")
    refused = await browser.cmd(cmd="wait_text", name="a",
                                text="That code didn't work", timeout_ms=25_000)
    if not refused["visible"]:
        await _dump_browser_state(browser, world, "a", base)
    assert refused["visible"], "a wrong code was not refused"
    assert (await browser.cmd(cmd="api", name="a",
                              url=f"{base}/api/v1/documents"))["status"] == 401

    await browser.cmd(cmd="fill", name="a", label="The code", value=code)
    try:
        await _verify_code(browser, world, "a")
    except AssertionError:
        await _dump_browser_state(browser, world, "a", base)
        raise AssertionError(
            f"sign-in did not reach /en/today (traffic in {EVIDENCE})") from None

    cookies = (await browser.cmd(cmd="cookies", name="a"))["cookies"]
    assert "authjs.session-token" in {c["name"] for c in cookies}
    session = json.loads((await browser.cmd(
        cmd="api", name="a", url=f"{base}/api/auth/session"))["body"])
    managed = session["user"]["managedIdentity"]
    assert managed["authentication"]["method"] == "signal_code"
    assert managed["authentication"]["proofIssuer"] == "nora-otp"
    assert managed["authentication"]["account"] == ident_json(SIG_A)
    assert managed["authentication"]["sessionId"] == session["user"]["sessionId"]

    # Retained binding and canonical identity agree with SQL/Kratos/Keto.
    binding = await world.binding(SIG_A)
    assert managed["binding"] == binding
    assert await services.bindings.canonical_person(person_a) == person_a
    relations = await services.backend.scope_relations_on(scope=ROOM_A)
    assert person_a in {r.subject for r in relations if r.relation == "member"}
    operation_id = "authentication:" + hashlib.sha256(json.dumps(
        ["portal", managed["authentication"]["sessionId"]]).encode()).hexdigest()
    recorded = await services.bindings.operation(operation_id)
    assert recorded is not None and recorded.state is OperationJournalState.COMPLETE
    assert recorded.snapshot["authentication"]["account"] == ident_json(SIG_A)
    assert recorded.snapshot["binding"] == binding

    # ---- the scoped portal view, through the browser ----
    await browser.cmd(cmd="goto", name="a", url=f"{base}/en/documents")
    listed = await browser.cmd(cmd="wait_text", name="a", text="A note")
    if not listed["visible"]:
        (EVIDENCE / "a-documents.html").write_text(
            (await browser.cmd(cmd="html", name="a"))["html"])
        (EVIDENCE / "a-pageerrors.txt").write_text(
            "\n".join((await browser.cmd(cmd="pageerrors", name="a"))["errors"]))
    assert listed["visible"], f"documents page missing 'A note' (html/pageerrors in {EVIDENCE})"
    assert not (await browser.cmd(cmd="visible", name="a", text="B note"))["visible"]
    await browser.cmd(cmd="screenshot", name="a",
                      path=str(EVIDENCE / "signal-documents.png"))

    # A browser-supplied personId is dropped: only the session's own scope returns.
    cross = await browser.cmd(cmd="api", name="a",
                              url=f"{base}/api/v1/documents?personId={person_b}")
    assert cross["status"] == 200
    assert {d["title"] for d in json.loads(cross["body"])["documents"]} == {"A note"}
    # Body and route bypass attempts refuse at the same boundary.
    assert (await browser.cmd(cmd="api", name="a", method="POST",
                              url=f"{base}/api/v1/documents",
                              body="{}"))["status"] == 405
    membership = json.dumps({
        "operation_id": f"op-{uuid4()}", "kind": "set_membership",
        "target": {"kind": "membership", "person_id": person_b,
                   "scope": {"kind": "room", "id": ROOM_W.id}},
        "payload": {"member": True},
        "observed_revisions": {"authority": "observed"}})
    denied_op = await browser.cmd(cmd="api", name="a", method="POST",
        url=f"{base}/api/v1/management/operations", body=membership,
        headers={"Origin": base, "Content-Type": "application/json"})
    assert denied_op["status"] == 403, denied_op["body"]
    assert (await browser.cmd(cmd="api", name="a",
                              url=f"{base}/api/v1/not-a-route"))["status"] == 404
    world.note("signal-portal", session="managedIdentity+binding verified",
               scoped_view="A note only", cross_viewer="param dropped, POST 403/405")

    return binding


async def _whatsapp_portal(browser, world, base):
    # ---- WhatsApp code sign-in through the same UI ----
    await browser.cmd(cmd="new", name="b",
                      headers={"x-forwarded-for": "198.51.100.12"})
    await browser.cmd(cmd="goto", name="b", url=f"{base}/en/login")
    await browser.cmd(cmd="click_text", name="b", text="WhatsApp")
    # A dead island leaves React's onChange unfired but the label click
    # still toggles the DOM radio — and DOM state is what the form posts.
    picked = await browser.cmd(cmd="eval", name="b", expression=(
        "document.querySelector('input[name=platform][value=whatsapp]').checked"))
    if not picked["value"]:
        await browser.cmd(cmd="eval", name="b", expression=(
            "document.querySelector('input[name=platform][value=whatsapp]').click()"))
    await browser.cmd(cmd="fill", name="b", label="Your phone number",
                      value="90000103")
    wa_code = await _send_code(browser, world, "b", world.wa_adapter.sent,
                               platform="whatsapp")
    wa_target, _ = world.wa_adapter.sent[-1]
    assert wa_target.conversation_id == "85290000103@s.whatsapp.net"
    await browser.cmd(cmd="fill", name="b", label="The code", value=wa_code)
    await _verify_code(browser, world, "b")
    wa_session = json.loads((await browser.cmd(
        cmd="api", name="b", url=f"{base}/api/auth/session"))["body"])
    wa_managed = wa_session["user"]["managedIdentity"]
    assert wa_managed["authentication"]["method"] == "whatsapp_code"
    assert wa_managed["authentication"]["account"] == ident_json(WA_B)
    assert wa_managed["binding"] == await world.binding(WA_B)
    await browser.cmd(cmd="goto", name="b", url=f"{base}/en/documents")
    assert (await browser.cmd(cmd="wait_text", name="b", text="B note"))["visible"]
    assert not (await browser.cmd(cmd="visible", name="b", text="A note"))["visible"]
    await browser.cmd(cmd="screenshot", name="b",
                      path=str(EVIDENCE / "whatsapp-documents.png"))
    world.note("whatsapp-portal", scoped_view="B note only")



async def _server_portal_boundaries(browser, world, base, person_a, person_b, binding):
    client = world.client
    # ---- server-side negative reads over the same boundary ----
    assert (await client.get(f"{DOCUMENTS}?personId={person_a}")).status == 401
    assert (await client.get(
        f"{DOCUMENTS}?personId={person_a}", headers=BEARER)).status == 401
    path = f"{DOCUMENTS}?personId={person_b}"
    assert (await client.get(path, headers=world.session_headers(
        SIG_A, binding=binding, path=path, http="GET"))).status == 403
    own = await client.get(f"{DOCUMENTS}?personId={person_a}",
        headers=world.session_headers(SIG_A, binding=binding,
            path=f"{DOCUMENTS}?personId={person_a}", http="GET"))
    assert own.status == 200
    assert {d["title"] for d in (await own.json())["documents"]} == {"A note"}

    # ---- no private key or admin credential reaches a browser surface ----
    html = (await browser.cmd(cmd="html", name="a"))["html"]
    seen = (await browser.cmd(cmd="responses", name="a"))["responses"] + \
        (await browser.cmd(cmd="responses", name="b"))["responses"]
    blob = html + json.dumps(seen)
    for secret in (OTP_TOKEN, WA_TOKEN, "generated-bridge-token", PORTAL_SECRET,
                   AUTH_SECRET, "Nora-Authentication ", "nora-mit-",
                   world.ory.kratos_admin, world.ory.keto_write):
        assert secret not in blob, f"private material surfaced: {secret[:16]}"



async def _revoke_binding(world, account, person, *, wrong_person):
    # Holder-exact: only the exact selected binding dies.
    client, services = world.client, world.services
    binding = await world.binding(account)
    other = json.dumps({"operation_id": f"op-{uuid4()}",
                        "expected_person": wrong_person}).encode()
    assert (await client.post(UNLINK, data=other, headers=world.session_headers(
        account, binding=binding, path=UNLINK, body=other))).status == 403
    exact = json.dumps({"operation_id": f"op-{uuid4()}",
                        "expected_person": person}).encode()
    assert (await client.post(UNLINK, data=exact, headers=world.session_headers(
        account, binding=binding, path=UNLINK, body=exact))).status == 200
    assert await services.bindings.record_for(account=account) is None


async def _browser_denied(browser, name, base):
    await browser.cmd(cmd="goto", name=name, url=f"{base}/en/documents")
    dead = await browser.cmd(cmd="wait_url", name=name, regex=r"/en/login\?denied=1")
    assert dead["matched"], f"revoked session landed at {dead['url']}"
    assert (await browser.cmd(cmd="wait_text", name=name,
                              text="not on the list"))["visible"]
    assert (await browser.cmd(cmd="api", name=name,
                              url=f"{base}/api/v1/documents"))["status"] == 401
    await browser.cmd(cmd="screenshot", name=name,
                      path=str(EVIDENCE / f"after-unlink-{name}.png"))


def _revision(title):
    return RevisionIn(title=title, spec={"version": 1, "root": {
        "type": "Page", "props": {"title": title}, "children": []}},
        text=title, sha256="e" * 64, media=(), written_by="generated")


async def test_managed_portal_browser_lifecycle(portal_world, tmp_path):
    world = portal_world
    client, services = world.client, world.services

    # Generated accounts register through the real roster boundary; membership
    # is exactly what the rosters said — no implicit administration anywhere.
    assert (await client.post(ROSTERS, headers=BEARER, json=roster(
        "op-portal-sig", ROOM_A, [(SIG_A, "person")], platform="signal"))).status == 200
    assert (await client.post(ROSTERS, headers=BEARER_WA, json=roster(
        "op-portal-wa", ROOM_W, [(WA_B, "person"), (WA_C, "person")],
        platform="whatsapp"))).status == 200
    person_a = await world.person_id(SIG_A)
    person_b = await world.person_id(WA_B)
    assert person_a != person_b
    for scope, person in ((ROOM_A, person_a), (ROOM_W, person_b)):
        assert (await services.authority.check(
            scope=scope, person_id=person, permit="view")).allowed
        assert not (await services.authority.check(
            scope=scope, person_id=person, permit="manage_members")).allowed

    await world.documents.create(document_id=uuid4(), platform="signal",
        conversation_id="generated-room-a", created_by="generated",
        revision=_revision("A note"))
    await world.documents.create(document_id=uuid4(), platform="whatsapp",
        conversation_id="generated-room-w", created_by="generated",
        revision=_revision("B note"))
    world.note("registration", persons=3, implicit_admin="none")

    api_base = str(client.make_url("")).rstrip("/")
    proc, base, next_log = await _start_portal(api_base, tmp_path)
    browser = _Browser()
    try:
        binding = await _signal_portal(browser, world, base, person_a, person_b)
        await _whatsapp_portal(browser, world, base)
        await _server_portal_boundaries(browser, world, base, person_a, person_b, binding)

        # WhatsApp's own selected binding dies; its browser session is
        # denied while the Signal browser and WA_C's session stay alive.
        await _revoke_binding(world, WA_B, person_b, wrong_person=person_a)
        await _browser_denied(browser, "b", base)
        await browser.cmd(cmd="goto", name="a", url=f"{base}/en/documents")
        assert (await browser.cmd(cmd="wait_text", name="a",
                                  text="A note"))["visible"]
        binding_c = await world.binding(WA_C)
        assert (await client.post(CHECK, data=b"", headers=world.session_headers(
            WA_C, binding=binding_c, path=CHECK))).status == 200

        # Signal's binding dies; its browser is denied, WA_C stays alive.
        await _revoke_binding(world, SIG_A, person_a, wrong_person=person_b)
        await _browser_denied(browser, "a", base)
        assert (await client.post(CHECK, data=b"", headers=world.session_headers(
            WA_C, binding=binding_c, path=CHECK))).status == 200
        world.note("revocation", unlinked=[WA_B.value, SIG_A.value],
                   browsers_denied=2, unaffected=WA_C.value)
    finally:
        browser.close()
        _stop_process(proc)
        next_log.close()
    _write_receipt(world.evidence)


async def test_managed_portal_discord_sign_in_scopes_and_revokes(portal_world):
    # Discord has no `*_code` and a real OAuth callback is a live provider
    # call, so its leg runs at this adjacent boundary over the same routes.
    world = portal_world
    client, services = world.client, world.services

    assert (await client.post(ROSTERS, headers=BEARER, json=roster(
        "op-portal-dis", ROOM_A, [(DISC_D, "person")],
        platform="discord"))).status == 200
    assert (await client.post(ROSTERS, headers=BEARER, json=roster(
        "op-portal-sig-c", ROOM_B, [(SIG_C, "person")],
        platform="signal"))).status == 200
    person_d = await world.person_id(DISC_D)
    await world.documents.create(document_id=uuid4(), platform="signal",
        conversation_id="generated-room-a", created_by="generated",
        revision=_revision("D note"))

    exchange = await client.post(EXCHANGE, data=b"{}", headers=auth(claims(
        account=DISC_D, purpose="sign_in", method="discord_oauth",
        path=EXCHANGE, body=b"{}")))
    assert exchange.status == 200, await exchange.text()
    binding_d = (await exchange.json())["binding"]

    path = f"{DOCUMENTS}?personId={person_d}"
    own = await client.get(path, headers=world.session_headers(
        DISC_D, binding=binding_d, path=path, http="GET"))
    assert own.status == 200
    assert {d["title"] for d in (await own.json())["documents"]} == {"D note"}

    # The exact selected binding — a session cannot unlink another person's.
    wrong = json.dumps({"operation_id": f"op-{uuid4()}",
                        "expected_person": await world.person_id(SIG_C)}).encode()
    assert (await client.post(UNLINK, data=wrong, headers=world.session_headers(
        DISC_D, binding=binding_d, path=UNLINK, body=wrong))).status == 403
    exact = json.dumps({"operation_id": f"op-{uuid4()}",
                        "expected_person": person_d}).encode()
    assert (await client.post(UNLINK, data=exact, headers=world.session_headers(
        DISC_D, binding=binding_d, path=UNLINK, body=exact))).status == 200
    assert await services.bindings.record_for(account=DISC_D) is None

    assert (await client.post(CHECK, data=b"", headers=world.session_headers(
        DISC_D, binding=binding_d, path=CHECK))).status == 403
    assert (await client.get(path, headers=world.session_headers(
        DISC_D, binding=binding_d, path=path, http="GET"))).status == 403
    assert (await client.post(CHECK, data=b"", headers=world.session_headers(
        SIG_C, binding=await world.binding(SIG_C), path=CHECK))).status == 200
    world.note("discord-revocation", unlinked=DISC_D.value, session_denied=True,
               unaffected=SIG_C.value)
    _write_receipt(world.evidence)
