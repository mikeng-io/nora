"""`nora.platform.signal.directory` — the control plane's half of the daemon
conversation. Field names here mirror what the live daemon actually returns
(see the module docstring); these tests pin the mapping, not the
daemon."""

from __future__ import annotations

import pytest
from aiohttp import web

from nora.platform.signal.directory import (
    _METHODS,
    DirectoryError,
    _rpc,
    list_contacts,
    list_groups,
)

GROUP_ID = "Lf2L9zqeNUahRsOz6BA+sTB8aWfbuvqu9vOhpvmJi34="


def _daemon_app(*, groups=None, contacts=None, seen=None, status=200, error=None):
    async def rpc(request):
        payload = await request.json()
        if seen is not None:
            seen.append(payload["method"])
        if status != 200:
            return web.json_response({}, status=status)
        if error is not None:
            return web.json_response({"jsonrpc": "2.0", "error": error, "id": payload["id"]})
        result = groups if payload["method"] == "listGroups" else contacts
        return web.json_response({"jsonrpc": "2.0", "result": result or [], "id": payload["id"]})

    app = web.Application()
    app.router.add_post("/api/v1/rpc", rpc)
    return app


async def test_list_groups_maps_the_daemons_field_names(aiohttp_server):
    server = await aiohttp_server(_daemon_app(groups=[{
        "id": GROUP_ID, "name": "Ridgeline", "isMember": True, "isBlocked": False,
        "members": [
            {"number": "+85290000101", "uuid": "a1000001-0000-4000-8000-000000000001",
             "isAdmin": True},
            {"number": "+85290000103", "uuid": None, "isAdmin": False},
        ],
    }]))
    groups = await list_groups(base_url=str(server.make_url("")), account="+1")
    assert len(groups) == 1
    assert groups[0].id == GROUP_ID
    assert groups[0].name == "Ridgeline"
    assert groups[0].is_member is True
    assert groups[0].is_blocked is False
    assert groups[0].members == (
        frozenset({"+85290000101", "a1000001-0000-4000-8000-000000000001"}),
        frozenset({"+85290000103"}),
    )


async def test_list_contacts_prefers_the_operators_own_nickname(aiohttp_server):
    """A nickname the operator set outranks the profile name the contact chose:
    it is the string Signal shows him, so it is the one he will recognise."""
    server = await aiohttp_server(_daemon_app(contacts=[
        {"number": "+1", "uuid": None, "username": None,
         "name": "Profile Name", "nickName": "Dad"},
        {"number": "+2", "givenName": "Ada", "familyName": "Lovelace"},
        {"number": "+3", "profile": {"givenName": "Grace"}},
        {"number": "+4"},
        {"number": None, "uuid": None, "username": None, "name": "ghost"},
    ]))
    contacts = await list_contacts(base_url=str(server.make_url("")), account="+1")
    assert [c.name for c in contacts] == ["Dad", "Ada Lovelace", "Grace", ""]
    assert all(c.ids for c in contacts), "a contact with no id form is unusable"


async def test_an_unreachable_daemon_is_a_readable_error_not_a_traceback():
    with pytest.raises(DirectoryError) as exc:
        await list_groups(base_url="http://127.0.0.1:1", account="+1", timeout=0.5)
    assert "could not reach the signal-cli daemon" in str(exc.value)
    assert "127.0.0.1:1/api/v1/rpc" in str(exc.value)


async def test_a_non_200_is_a_readable_error(aiohttp_server):
    server = await aiohttp_server(_daemon_app(status=500))
    with pytest.raises(DirectoryError, match="HTTP 500"):
        await list_groups(base_url=str(server.make_url("")), account="+1")


async def test_a_jsonrpc_error_is_surfaced(aiohttp_server):
    server = await aiohttp_server(_daemon_app(error={"code": -32602, "message": "nope"}))
    with pytest.raises(DirectoryError, match="rejected by signal-cli"):
        await list_contacts(base_url=str(server.make_url("")), account="+1")


async def test_only_read_methods_may_be_called(aiohttp_server):
    """The read-only guarantee (D6) is a property of this module, not a promise
    in its docstring: `send` reaches the same endpoint, so nothing but the
    allowlist stands between the control plane and a write."""
    seen: list[str] = []
    server = await aiohttp_server(_daemon_app(seen=seen))
    with pytest.raises(DirectoryError, match="not a read-only directory method"):
        await _rpc(base_url=str(server.make_url("")), method="send",
                   account="+1", timeout=1.0)
    assert seen == [], "a refused method must never reach the daemon"
    assert sorted(_METHODS) == ["listContacts", "listGroups"]
