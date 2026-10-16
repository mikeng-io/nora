"""R-17 — Nora's own id forms, and the metric that proves the derivation ran.

The payload shapes asserted here are the ones the real daemon returns
(signal-cli 0.14.7, 127.0.0.1:18080), including the ACI/PNI split
that makes `getUserStatus` alone the wrong fix. See
`nora/platform/signal/self_id.py`'s module docstring for the full survey.
"""

import pytest
from aiohttp import web

from nora.domain.identity import Identifier
from nora.domain.models import NormalizedMessage
from nora.engagement.slots import ConversationSlots
from nora.engagement.stages import admission
from nora.engagement.stages.admission import Admission
from nora.obs.metrics import Metrics
from nora.platform.signal.adapter import SignalAdapter
from nora.platform.signal.self_id import (
    ALIASES_METRIC,
    DEGRADED_METRIC,
    derive_identifiers,
)
from nora.tag import Debounce

pytestmark = pytest.mark.asyncio

PRIMARY = "+85290000100"
ACI = "a1000000-0000-4000-8000-000000000000"
PNI = "a10000ff-0000-4000-8000-0000000000ff"
USERNAME = "nora.000"


def _contacts_result(rows=None):
    if rows is None:
        rows = [{"number": PRIMARY, "uuid": ACI, "username": USERNAME, "name": ""}]
    return rows


def _status_result(rows=None):
    if rows is None:
        rows = [
            {"recipient": PRIMARY, "number": PRIMARY, "uuid": PNI, "isRegistered": True}
        ]
    return rows


def _rpc_app(*, contacts=None, status=None, contacts_error=False):
    """A daemon that answers a batched JSON-RPC POST the way the real one does."""

    async def handler(request: web.Request) -> web.Response:
        batch = await request.json()
        out = []
        for call in batch:
            if call["method"] == "listContacts":
                if contacts_error:
                    out.append({
                        "jsonrpc": "2.0", "id": call["id"],
                        "error": {"code": -32601, "message": "Method not implemented"},
                    })
                else:
                    out.append({
                        "jsonrpc": "2.0", "id": call["id"],
                        "result": _contacts_result(contacts),
                    })
            else:
                out.append({
                    "jsonrpc": "2.0", "id": call["id"], "result": _status_result(status),
                })
        return web.json_response(out)

    app = web.Application()
    app.router.add_post("/api/v1/rpc", handler)
    return app


def _accounts_only_app():
    """The daemon as it actually is: `/api/v1/rpc` exists, `/api/v1/accounts`
    does not. Serving only `/api/v1/accounts`, the endpoint the daemon lacks, is what makes
    this a regression test rather than a generic 404 test."""

    async def handler(request: web.Request) -> web.Response:
        return web.json_response([{"number": PRIMARY, "uuid": ACI}])

    app = web.Application()
    app.router.add_get("/api/v1/accounts", handler)
    return app


async def _url(aiohttp_server, app) -> str:
    server = await aiohttp_server(app)
    return str(server.make_url("")).rstrip("/")


async def test_the_working_rpc_yields_every_typed_id_form(aiohttp_server):
    """listContacts gives phone + ACI + username; getUserStatus gives the PNI.
    All four are `Identifier`s in the `id_kinds("signal")` vocabulary."""
    metrics = Metrics()
    url = await _url(aiohttp_server, _rpc_app())

    identifiers = await derive_identifiers(url, PRIMARY, metrics=metrics)

    assert identifiers == frozenset({
        Identifier("signal", "phone", PRIMARY),
        Identifier("signal", "uuid", ACI),
        Identifier("signal", "uuid", PNI),
    })
    assert metrics.get(DEGRADED_METRIC) == 0.0
    # 3, not 4: the contact row's `username` is not collected — a renameable
    # handle is never a matchable identity.
    assert metrics.get(ALIASES_METRIC, platform="signal") == 3.0


async def test_the_aci_and_the_pni_are_both_kept(aiohttp_server):
    """The trap: `getUserStatus` returns the PNI, not
    the ACI, so a fix wired to it alone would have collected the wrong UUID and
    still looked like it worked. `sourceUuid` on a real envelope is the ACI."""
    metrics = Metrics()
    url = await _url(aiohttp_server, _rpc_app())

    values = {i.value for i in await derive_identifiers(url, PRIMARY, metrics=metrics)}

    assert ACI in values, "the ACI (listContacts) must be recognised as Nora's own"
    assert PNI in values, "the PNI (getUserStatus) must be recognised as Nora's own"
    assert ACI != PNI


async def test_an_unreachable_daemon_counts_the_degradation():
    """R-17's core: enrichment failing is not fatal, but it is never silent."""
    metrics = Metrics()

    identifiers = await derive_identifiers(
        "http://127.0.0.1:1", PRIMARY, metrics=metrics, timeout=0.5,
    )

    assert identifiers == frozenset()
    assert metrics.get(DEGRADED_METRIC, platform="signal", reason="unreachable") == 1.0
    assert metrics.get(ALIASES_METRIC, platform="signal") == 1.0


async def test_a_daemon_serving_only_the_old_404_endpoint_counts_the_degradation(
    aiohttp_server,
):
    """The production shape, exactly. `/api/v1/accounts` is the ONLY endpoint
    this fake serves and `/api/v1/rpc` 404s — the mirror image of the real
    daemon — which must still be counted rather than logged and forgotten."""
    metrics = Metrics()
    url = await _url(aiohttp_server, _accounts_only_app())

    identifiers = await derive_identifiers(url, PRIMARY, metrics=metrics)

    assert identifiers == frozenset()
    assert metrics.get(DEGRADED_METRIC, platform="signal", reason="unreachable") == 1.0
    assert metrics.get(ALIASES_METRIC, platform="signal") == 1.0


async def test_a_daemon_with_no_matching_contact_row_counts_that_reason(aiohttp_server):
    """The daemon answered — with somebody else's rows. Distinguishable from
    `unreachable`, because it means the parse broke (a signal-cli upgrade), not
    that the network did."""
    metrics = Metrics()
    url = await _url(
        aiohttp_server,
        _rpc_app(contacts=[{"number": "+15550000000", "uuid": "someone-else"}]),
    )

    identifiers = await derive_identifiers(url, PRIMARY, metrics=metrics)

    assert {i.value for i in identifiers} == {PNI}
    assert (
        metrics.get(DEGRADED_METRIC, platform="signal", reason="no_self_contact_row")
        == 1.0
    )
    # The PNI still arrived, so this is a PARTIAL degradation: two aliases, not one.
    assert metrics.get(ALIASES_METRIC, platform="signal") == 2.0


async def test_a_jsonrpc_error_object_is_a_degradation_not_a_success(aiohttp_server):
    """`listAccounts` answers `-32601 Method not implemented` on this daemon; an
    error object carries no `result` key, and must never be read as an empty
    success."""
    metrics = Metrics()
    url = await _url(aiohttp_server, _rpc_app(contacts_error=True))

    identifiers = await derive_identifiers(url, PRIMARY, metrics=metrics)

    assert {i.value for i in identifiers} == {PNI}
    assert (
        metrics.get(DEGRADED_METRIC, platform="signal", reason="no_self_contact_row")
        == 1.0
    )


async def test_a_missing_user_status_is_counted_separately(aiohttp_server):
    metrics = Metrics()
    url = await _url(aiohttp_server, _rpc_app(status=[]))

    identifiers = await derive_identifiers(url, PRIMARY, metrics=metrics)

    # USERNAME deliberately absent: a renameable handle is never a matchable
    # identity — only the phone number / uuid is stable.
    # The contact row the fake returns DOES carry it, so this proves it is
    # dropped rather than missing from the fixture.
    assert {i.value for i in identifiers} == {PRIMARY, ACI}
    assert (
        metrics.get(
            DEGRADED_METRIC, platform="signal", reason="no_phone_number_identity"
        )
        == 1.0
    )


def _self_tag(sender_id: str) -> NormalizedMessage:
    return NormalizedMessage(
        platform="signal", conversation_id="g1", conversation_type="group",
        sender_id=sender_id, sender_name="ทᴏʀɑ", timestamp=1,
        body="nora.000 hello", raw={},
    )


def _slots() -> ConversationSlots:
    """Empty slots — R-12 is decided before the lock is ever consulted, so
    every admission call below starts from a free conversation."""
    return ConversationSlots(maxlen=16, spawn=lambda coro: coro.close())


async def test_degraded_identity_enrichment_is_counted_and_r12_still_holds(
    aiohttp_server,
):
    """R-17, end to end through the real adapter.

    Two claims, and BOTH have to hold at once, because satisfying the second
    while quietly failing the first is the failure this guards:

      1. enrichment being unavailable is COUNTED — a series an operator can
         alert on, not a `log.warning` nobody reads;
      2. R-12 nevertheless still refuses a self-tag arriving under the primary
         id, because degradation must never widen what Nora will answer.

    The daemon here serves only `/api/v1/accounts` — an endpoint the real daemon
    does not have — so `/api/v1/rpc` 404s and the derivation genuinely fails.
    Nothing is stubbed.
    """
    metrics = Metrics()
    url = await _url(aiohttp_server, _accounts_only_app())
    adapter = SignalAdapter(url, PRIMARY, metrics=metrics)

    identity = await adapter.resolve_identity()

    # (1) The degradation is on the board.
    assert metrics.get(DEGRADED_METRIC, platform="signal", reason="unreachable") == 1.0
    assert metrics.get(ALIASES_METRIC, platform="signal") == 1.0, (
        "one alias means R-12 is down to equality against the primary id — that "
        "is exactly the state this gauge exists to make visible"
    )
    assert identity.identifiers == frozenset()
    assert identity.aliases == frozenset({PRIMARY})

    # (2) R-12 still refuses her own message, by the one id that IS known.
    verdict = admission.run(
        message=_self_tag(PRIMARY),
        identity=identity,
        debounce=Debounce(0.0),
        slots=_slots(),
        metrics=metrics,
    )
    assert verdict is Admission.REJECTED
    assert metrics.get("nora_tags_total", platform="signal") == 0.0


async def test_enrichment_widens_r12_to_the_aci_it_could_not_see_before(
    aiohttp_server,
):
    """The other side, and the reason the degradation matters: a
    self-send arriving under her ACI is refused ONLY when enrichment worked.
    Under the degraded set the same message is admitted as somebody else's tag
    — which is the R-12 hole this closes."""
    working = Metrics()
    degraded = Metrics()

    enriched = await SignalAdapter(
        await _url(aiohttp_server, _rpc_app()), PRIMARY, metrics=working,
    ).resolve_identity()
    fallback = await SignalAdapter(
        await _url(aiohttp_server, _accounts_only_app()), PRIMARY, metrics=degraded,
    ).resolve_identity()

    assert ACI in enriched.aliases
    assert ACI not in fallback.aliases

    def _admit(identity, metrics):
        return admission.run(
            message=_self_tag(ACI), identity=identity, debounce=Debounce(0.0),
            slots=_slots(), metrics=metrics,
        )

    # Refused (self-tag) only when enrichment saw the ACI; under the degraded
    # set the same message is admitted as somebody else's tag — the R-12 hole.
    assert _admit(enriched, working) is Admission.REJECTED
    assert _admit(fallback, degraded) is Admission.RUN_NOW


async def test_the_account_may_be_configured_as_a_uuid(aiohttp_server):
    """`platforms.signal.account` is free-form; the self row is found by number
    OR uuid so an operator who pasted the ACI is not silently unenriched."""
    metrics = Metrics()
    url = await _url(aiohttp_server, _rpc_app())

    identifiers = await derive_identifiers(url, ACI, metrics=metrics)

    assert Identifier("signal", "phone", PRIMARY) in identifiers
    assert (
        metrics.get(DEGRADED_METRIC, platform="signal", reason="no_self_contact_row")
        == 0.0
    )
