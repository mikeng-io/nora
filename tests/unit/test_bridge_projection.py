"""The bridge admission projection: same policy, same loaders, one file.

Every fixture below is GENERATED — person ids, rooms and platform ids are
synthetic values of the same shape family the committed examples already use
(`identity.example.toml` placeholders, `a10000..` uuid forms), never production
data. The assertions cross-check the projection against `Policy`/`Persons`
themselves — parity is measured against the real grant predicate, not a copy.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest
from aiohttp import web

from nora import config
from nora.persons import load_persons
from nora.platform.bridge.projection import (
    BridgeChannelDirectory,
    ProjectionPublisher,
)
from nora.policy import load_policy

# ── generated fixtures (synthetic; same shape family as identity.example.toml)

MILES_PHONE = "+85212345678"
MILES_UUID = "a1000001-0000-4000-8000-000000000001"
ADA_PHONE = "+85290000101"
MILES_DISCORD = "100000000000000301"
NORA_ACCOUNT = "+85287654321"          # the placeholder second line of the example
ROOM_SIGNAL = "Q2hhdEdyb3VwSWRFeGFtcGxlQmFzZTY0PT0="
ROOM_SIGNAL_ALSO = "Q2hhdEdyb3VwSWRFeGFtcGxlQmFzZTY0QWx0PQ=="
ROOM_WA = "120000000000000001@g.us"
ROOM_DISCORD = "100000000000000203/100000000000000204"
ROOM_DENIED = "Q2hhdEdyb3VwSWRFeGFtcGxlRGVuaWVkPT0="
GUILD_ID = "700000000000000001"
GUILD_CHAN_A = "700000000000000101"
GUILD_CHAN_B = "700000000000000102"
MUSIC_CHAN = "700000000000000303"
ACCOUNT_KEY = "opaque-leg-7"

_IDENTITY = f"""\
[persons.miles]
name = "Miles"

[[persons.miles.signal]]
phone = "{MILES_PHONE}"
uuid = "{MILES_UUID}"

[[persons.miles.discord]]
user_id = "{MILES_DISCORD}"

[[persons.miles.whatsapp]]
phone = "{MILES_PHONE}"

[persons.ada]
name = "Ada"

[[persons.ada.signal]]
phone = "{ADA_PHONE}"

[[persons.ada.whatsapp]]
phone = "{ADA_PHONE}"
"""

_POLICY = f"""\
default = "deny"

[dm]
persons = ["miles"]

[[conversations]]
platform = "signal"
id = "{ROOM_SIGNAL}"
also_ids = ["{ROOM_SIGNAL_ALSO}"]
label = "Family"
persons = ["miles"]

[[conversations]]
platform = "whatsapp"
id = "{ROOM_WA}"
label = "Family WA"
persons = ["miles", "ada"]

[[conversations]]
platform = "discord"
id = "{ROOM_DISCORD}"
label = "Family Discord"
persons = ["miles"]
"""

# The template's signal-enable line, verbatim — `_edit_config` asserts the
# literal is present so a template drift fails the test, not silently passes.
_SIGNAL_ENABLED_LINE = (
    "enabled = true                   "
    "# false with no other platform enabled refuses to start"
)

_POLICY_GUILD = f"""\
default = "deny"

[[conversations]]
platform = "discord"
guild = "{GUILD_ID}"
label = "The Server"
persons = ["miles"]
"""

# A guild+music routing fixture: `music_channel` names an OUTBOUND-only
# announcement channel — a sibling, never a listed room (the loader itself
# refuses a music_channel pointing at a listed room).
_POLICY_MUSIC = f"""\
default = "deny"

[[conversations]]
platform = "discord"
id = "{GUILD_ID}/{GUILD_CHAN_A}"
label = "Game room"
persons = ["miles"]
music_channel = "{MUSIC_CHAN}"
"""

_POLICY_WILDCARD = f"""\
default = "deny"

[[conversations]]
platform = "signal"
id = "{ROOM_SIGNAL}"
label = "Open room"
persons = "*"

[[conversations]]
platform = "signal"
id = "{ROOM_DENIED}"
label = "Closed room"
persons = ["miles"]
"""

CONTRACT_KEYS = {
    "revision", "generated_ms", "enabled", "account_key", "rooms",
    "dm_allowed_senders", "self_aliases", "deterministic_triggers",
    "receipt_emoji", "room_receipt_senders", "music_channels",
}


def _write(path: Path, text: str) -> None:
    """Path writes live in sync helpers — ruff's ASYNC240 refuses them in the
    async test bodies themselves."""
    path.write_text(text)


def _sources(tmp_path: Path, *, policy: str = _POLICY, identity: str = _IDENTITY) -> None:
    _write(tmp_path / "policy.toml", policy)
    _write(tmp_path / "identity.toml", identity)
    # On the merged schema the config carries `platforms.signal.account_key`
    # and the env leg must equal it; on this base the field does not exist
    # (extra=forbid) and the env leg stands alone. Schema-driven either way.
    if "account_key" in config.SignalPlatformConfig.model_fields:
        cfg = tmp_path / "config.toml"
        if 'account_key = "signal-acct-1"' in cfg.read_text():
            _edit_config(cfg, **{
                'account_key = "signal-acct-1"':
                f'account_key = "{ACCOUNT_KEY}"'})
        else:
            _edit_config(
                cfg,
                **{'account = ""':
                   f'account_key = "{ACCOUNT_KEY}"\naccount = ""'},
            )


def _edit_config(path: Path, **swaps: str) -> None:
    text = path.read_text()
    for old, new in swaps.items():
        assert old in text, f"fixture drift: {old!r} not in config template"
        text = text.replace(old, new, 1)
    path.write_text(text)


def _enable_account(path: Path, account: str = NORA_ACCOUNT) -> None:
    _edit_config(path, **{'account = ""': f'account = "{account}"'})


def _enable_discord(path: Path) -> None:
    # Same merged-schema leg as `_sources`: when the config class carries
    # `account_key`, enabling the platform without matching the env leg is a
    # guaranteed deny, so the fixture sets them equal.
    leg = (f"account_key = \"{ACCOUNT_KEY}\"\n"
           if "account_key" in config.DiscordPlatformConfig.model_fields
           else "")
    _edit_config(
        path,
        **{"[platforms.discord]\nenabled = false":
            f"[platforms.discord]\n{leg}enabled = true"},
    )


def _enable_whatsapp(path: Path) -> None:
    leg = (f"account_key = \"{ACCOUNT_KEY}\"\n"
           if "account_key" in config.WhatsAppPlatformConfig.model_fields
           else "")
    _edit_config(
        path,
        **{"[platforms.whatsapp]\nenabled = false":
            f"[platforms.whatsapp]\n{leg}enabled = true"},
    )


async def _bridge_server(state: dict) -> tuple[web.AppRunner, str]:
    """A REAL bridge subset — `POST /v1/reads` + `GET /v1/self` — answered
    from `state` at request time so a test can mutate upstream truth between
    ticks (a channel appears, the bridge goes unavailable), exactly as the
    discord control worker's endpoints behave. Returns (runner, base_url)."""

    async def reads(request: web.Request) -> web.Response:
        body = await request.json()
        assert body["op"] == "channels"
        gid = body["params"]["guild_id"]
        if not state.get("available", True):
            return web.json_response({"available": False})
        return web.json_response({"available": True, "value": {
            "channels": dict(state.get("channels", {}).get(gid, {})),
            "threads": dict(state.get("threads", {})),
        }})

    async def self_ep(request: web.Request) -> web.Response:
        if not state.get("self_available", True):
            return web.Response(status=503)
        return web.json_response({
            "primary": state.get("primary"),
            "aliases": list(state.get("aliases", [])),
        })

    app = web.Application()
    app.router.add_post("/v1/reads", reads)
    app.router.add_get("/v1/self", self_ep)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    host, port = runner.addresses[0][:2]
    return runner, f"http://{host}:{port}"


def _publisher(
    tmp_path: Path, platform: str = "signal", name: str = "p", **kw,
) -> ProjectionPublisher:
    out = tmp_path / "bridge-projection" / name
    out.mkdir(parents=True, exist_ok=True)
    return ProjectionPublisher(
        platform=platform, config_path=tmp_path / "config.toml",
        out_dir=out, account_key=kw.pop("account_key", ACCOUNT_KEY),
        # The SAME injection the deploy entry performs: the real schema-only
        # loader and the app's receipt constant, not copies of either.
        config_loader=kw.pop(
            "config_loader", lambda p: config.load(p, require_secrets=False),
        ),
        ack_emoji="👀", **kw,
    )


def _read(pub: ProjectionPublisher) -> dict:
    return json.loads(pub.target.read_text())


# ── parity with the real policy predicate ------------------------------------


async def test_publishes_exactly_the_admitted_scope(example_config_path: Path):
    _sources(example_config_path.parent)
    _enable_account(example_config_path)
    pub = _publisher(example_config_path.parent)
    await pub.tick()

    proj = _read(pub)
    assert set(proj) == CONTRACT_KEYS
    assert proj["enabled"] is True
    assert proj["account_key"] == ACCOUNT_KEY
    assert isinstance(proj["revision"], int) and proj["revision"] > 0
    assert isinstance(proj["generated_ms"], int)

    # The room — every id form the policy admits, nothing else.
    assert proj["rooms"] == sorted({ROOM_SIGNAL, ROOM_SIGNAL_ALSO})

    # DM scope: miles' signal ids ONLY — the reverse read of Persons.resolve,
    # so both matchable forms arrive and ada holds no grant.
    assert proj["dm_allowed_senders"] == sorted({MILES_PHONE, MILES_UUID})
    # Per-room receipt grants: miles' signal ids under every admitted id —
    # ada holds NO receipt eligibility in a room her messages may absorb into.
    assert proj["room_receipt_senders"] == {
        ROOM_SIGNAL: sorted({MILES_PHONE, MILES_UUID}),
        ROOM_SIGNAL_ALSO: sorted({MILES_PHONE, MILES_UUID}),
    }
    # No guild carries a music route in this policy; the map exists anyway.
    assert proj["music_channels"] == {}
    # No bridge to answer /v1/self: the config-floor account is the whole set.
    assert proj["self_aliases"] == [NORA_ACCOUNT]
    assert proj["deterministic_triggers"] == ["nora"]
    assert proj["receipt_emoji"] == "👀"


async def test_rooms_equal_policy_admission_not_a_second_parser(
    example_config_path: Path,
):
    _sources(example_config_path.parent)
    pub = _publisher(example_config_path.parent)
    await pub.tick()
    proj = _read(pub)

    policy = load_policy(example_config_path.parent / "policy.toml")
    # Every emitted room id resolves through the REAL grant predicate; a room
    # the policy denies is absent. This is the denied-room rule: nothing of it
    # may be captured.
    for cid in proj["rooms"]:
        assert policy.conversation("signal", cid) is not None
    assert ROOM_DENIED not in proj["rooms"]
    assert policy.conversation("signal", ROOM_DENIED) is None
    # Other platforms' rooms never leak into this platform's file.
    assert ROOM_WA not in proj["rooms"] and ROOM_DISCORD not in proj["rooms"]


async def test_denied_sender_in_allowed_room_gets_no_grant(
    example_config_path: Path,
):
    """Absorbed room content vs engage grant: the Family room admits only
    miles, so ada's content may be captured under the room but she appears
    nowhere the bridge could read as an authorization — including the
    per-room receipt grant, which is the whole point of the field."""
    _sources(example_config_path.parent)
    pub = _publisher(example_config_path.parent)
    await pub.tick()
    proj = _read(pub)

    policy = load_policy(example_config_path.parent / "policy.toml")
    persons = load_persons(example_config_path.parent / "identity.toml")
    # Real predicates agree: the room admits her message as room content…
    assert policy.conversation("signal", ROOM_SIGNAL) is not None
    assert policy.is_allowed(
        platform="signal", conversation_id=ROOM_SIGNAL, person_id="ada",
    ) is False
    # …and the projection carries NO id of hers in any field a consumer could
    # read as an engage/DM grant.
    ada_ids = persons.platform_ids("signal", "ada")
    assert ada_ids
    for field in ("rooms", "dm_allowed_senders", "self_aliases"):
        assert not (ada_ids & set(proj[field]))
    # The receipt grant map names ONLY miles' ids on every admitted id form.
    for cid in (ROOM_SIGNAL, ROOM_SIGNAL_ALSO):
        grant = proj["room_receipt_senders"][cid]
        assert not (ada_ids & set(grant))
        assert set(grant) == persons.platform_ids("signal", "miles")


async def test_room_allowed_sender_without_dm_grant_keeps_room_receipt(
    example_config_path: Path,
):
    """DM and room-receipt scopes are distinct: ada is room-listed on
    whatsapp but NOT in [dm]. A room event she is eligible for still earns
    its addressed receipt; a DM from her remains denied at the door."""
    _sources(example_config_path.parent)
    _enable_whatsapp(example_config_path)
    pub = _publisher(example_config_path.parent, platform="whatsapp")
    await pub.tick()
    proj = _read(pub)

    persons = load_persons(example_config_path.parent / "identity.toml")
    miles_wa = persons.platform_ids("whatsapp", "miles")
    ada_wa = persons.platform_ids("whatsapp", "ada")
    assert miles_wa and ada_wa
    # Room receipt grant covers BOTH listed senders on the admitted room…
    assert set(proj["room_receipt_senders"][ROOM_WA]) == miles_wa | ada_wa
    # …while DM scope admits ONLY the [dm]-listed person.
    assert set(proj["dm_allowed_senders"]) == miles_wa
    assert not (ada_wa & set(proj["dm_allowed_senders"]))


async def test_wildcard_room_emits_literal_star_and_denied_room_is_absent(
    example_config_path: Path,
):
    """`persons = "*"` is the ONE case the contract grants the literal:
    receipt eligibility follows addressing, not a sender list."""
    _sources(example_config_path.parent, policy=_POLICY_WILDCARD)
    pub = _publisher(example_config_path.parent)
    await pub.tick()
    proj = _read(pub)

    assert proj["room_receipt_senders"][ROOM_SIGNAL] == ["*"]
    # An explicit-list room still carries ids; a missing entry means deny.
    assert proj["room_receipt_senders"][ROOM_DENIED] == sorted(
        {MILES_PHONE, MILES_UUID},
    )


async def test_dm_requires_explicit_person_authorization(
    example_config_path: Path,
):
    _sources(example_config_path.parent)
    pub = _publisher(example_config_path.parent)
    await pub.tick()
    proj = _read(pub)
    persons = load_persons(example_config_path.parent / "identity.toml")
    # ada resolves on signal but is not in [dm] — her DM is denied before
    # capture, exactly as scope denies unknown DMs before recording.
    assert persons.resolve("signal", ADA_PHONE) is not None
    assert ADA_PHONE not in proj["dm_allowed_senders"]


# ── opaque account leg --------------------------------------------------------


async def test_account_key_is_explicit_opaque_and_required(
    example_config_path: Path,
):
    _sources(example_config_path.parent)
    # Missing, blank, non-ASCII and separator-bearing legs all refuse at
    # construction — a malformed leg names no honest stream.
    for bad in ("", "   ", "lég-1", "with space", "a:b", "a/b", "tab\there"):
        with pytest.raises(ValueError, match="ACCOUNT_KEY"):
            _publisher(example_config_path.parent, account_key=bad)

    pub = _publisher(example_config_path.parent, account_key=ACCOUNT_KEY)
    await pub.tick()
    proj = _read(pub)
    # The leg is verbatim, never derived from a phone number, FBID or room id.
    assert proj["account_key"] == ACCOUNT_KEY
    all_platform_ids = set(proj["rooms"]) | set(proj["dm_allowed_senders"]) | set(
        proj["self_aliases"]
    )
    assert proj["account_key"] not in all_platform_ids

    other = _publisher(
        example_config_path.parent, account_key="opaque-leg-9", name="q",
    )
    await other.tick()
    assert _read(other)["account_key"] == "opaque-leg-9"
    assert _read(other)["revision"] != proj["revision"]


async def test_account_key_mismatch_with_enabled_config_denies(
    example_config_path: Path,
):
    """When the schema carries `platforms.<p>.account_key` and the platform
    is enabled, the env leg MUST equal it — a mismatched leg names a stream
    the app reader never opens, i.e. a guessed grant, so the file denies."""
    _sources(example_config_path.parent)
    cfg_dir = example_config_path.parent

    class _Cfg:
        """Stand-in for the merged schema's `platforms.<p>.account_key`."""

        policy_file = cfg_dir / "policy.toml"
        identity_file = cfg_dir / "identity.toml"
        platforms = type("P", (), {
            "signal": type("S", (), {
                "enabled": True, "account_key": "the-config-leg",
                "account": "", "base_url": None,
            })(),
        })()
        engagement = type("E", (), {
            "trigger": type("T", (), {"enabled": False, "text": ""})(),
            "reactions": type("R", (), {"enabled": True})(),
        })()
        honcho = type("H", (), {"ai_peer": "nora", "workspace_id": "ws"})()

    pub = _publisher(
        cfg_dir, account_key=ACCOUNT_KEY,
        config_loader=lambda _p: _Cfg(),
    )
    await pub.tick()
    proj = _read(pub)
    assert proj["enabled"] is False
    assert proj["rooms"] == []
    assert proj["room_receipt_senders"] == {}
    assert proj["account_key"] == ACCOUNT_KEY     # the leg still names itself
    st = pub.status()
    assert st["key_mismatch"] is True
    assert st["stale"] is True and st["ok"] is False


# ── reload semantics ----------------------------------------------------------


async def test_invalid_reload_keeps_last_good_and_reports_stale(
    example_config_path: Path,
):
    _sources(example_config_path.parent)
    _enable_account(example_config_path)      # floor => no self staleness
    pub = _publisher(example_config_path.parent)
    await pub.tick()
    good_bytes = pub.target.read_bytes()
    good = json.loads(good_bytes)

    # An edit that must be REFUSED (persons must be a list of person ids).
    policy_path = example_config_path.parent / "policy.toml"
    _write(policy_path, _POLICY.replace(
        'persons = ["miles"]\n\n[[conversations]]\nplatform = "whatsapp"',
        'persons = 7\n\n[[conversations]]\nplatform = "whatsapp"',
    ))
    await pub.tick()
    # Byte-identical: the last valid projection is preserved, not overwritten
    # by a guessed grant or an empty "success" artifact.
    assert pub.target.read_bytes() == good_bytes
    status = pub.status()
    assert status["stale"] is True
    assert status["files"]["policy"]["stale"] is True
    assert status["ok"] is False

    # A valid follow-up edit applies and rewrites exactly once.
    _write(policy_path, _POLICY + f"""\
[[conversations]]
platform = "signal"
id = "{ROOM_DENIED}"
label = "Back room"
persons = ["ada"]
""")
    await pub.tick()
    new = _read(pub)
    assert ROOM_DENIED in new["rooms"]
    assert new["revision"] != good["revision"]
    assert pub.status()["stale"] is False


async def test_first_invalid_state_fails_closed(example_config_path: Path):
    """Boot-invalid: an explicit deny, never silence-as-grant. The publisher
    keeps running and picks up a fixed file on a later tick."""
    _sources(example_config_path.parent, policy="this is [ not valid toml")
    pub = _publisher(example_config_path.parent)
    await pub.tick()

    proj = _read(pub)
    assert set(proj) == CONTRACT_KEYS
    assert proj["enabled"] is False
    assert proj["rooms"] == []
    assert proj["dm_allowed_senders"] == []
    assert proj["self_aliases"] == []
    assert proj["room_receipt_senders"] == {}
    assert proj["music_channels"] == {}
    assert proj["account_key"] == ACCOUNT_KEY   # the leg itself is still true
    assert pub.status()["ok"] is False

    _write(example_config_path.parent / "policy.toml", _POLICY)
    await pub.tick()
    assert _read(pub)["enabled"] is True


async def test_invalid_config_fails_closed(example_config_path: Path):
    _sources(example_config_path.parent)
    _write(example_config_path, "[broken")
    pub = _publisher(example_config_path.parent)
    await pub.tick()
    assert _read(pub)["enabled"] is False


async def test_disabled_platform_publishes_deny_with_honest_scope(
    example_config_path: Path,
):
    """A platform off in config.toml yields enabled=false; the scopes still
    describe policy truth (consumers gate on enabled first)."""
    _sources(example_config_path.parent)
    _edit_config(
        example_config_path,
        # keep the all-platforms-off startup refusal away: discord on, signal off
        **{_SIGNAL_ENABLED_LINE: "enabled = false"},
        **{"[platforms.discord]\nenabled = false": "[platforms.discord]\nenabled = true"},
    )
    pub = _publisher(example_config_path.parent)
    await pub.tick()
    proj = _read(pub)
    assert proj["enabled"] is False
    assert proj["rooms"] == sorted({ROOM_SIGNAL, ROOM_SIGNAL_ALSO})


# ── disclosure ----------------------------------------------------------------


async def test_no_private_metadata_leaks(example_config_path: Path):
    _sources(example_config_path.parent)
    pub = _publisher(example_config_path.parent)
    await pub.tick()
    proj = _read(pub)
    raw = pub.target.read_text()

    assert set(proj) == CONTRACT_KEYS
    # person_ids, names and labels are identity-file internals — the bridge
    # never learns who "miles" is, only the platform ids it must match.
    for private in ("miles", "ada", "Miles", "Ada", "Family", "persons"):
        assert private not in raw
    # rooms holds conversation ids only — no per-room person roster, no label.
    assert all(isinstance(r, str) for r in proj["rooms"])


# ── the atomic write path -----------------------------------------------------


async def test_write_is_temp_fsync_rename_dirsync(example_config_path: Path, monkeypatch):
    """Real filesystem, real fsync/rename — counted, not mocked away."""
    _sources(example_config_path.parent)
    pub = _publisher(example_config_path.parent)

    fsyncs: list[int] = []
    replaces: list[tuple[str, str]] = []
    real_fsync, real_replace = os.fsync, os.replace
    monkeypatch.setattr(
        os, "fsync", lambda fd: (fsyncs.append(fd), real_fsync(fd))[1],
    )
    monkeypatch.setattr(
        os, "replace",
        lambda s, d: (replaces.append((s, d)), real_replace(s, d))[1],
    )

    await pub.tick()

    assert len(replaces) == 1
    tmp, dst = replaces[0]
    assert Path(tmp).parent == pub.out_dir
    assert Path(dst) == pub.target
    assert len(fsyncs) >= 2                     # file fsync + directory fsync
    # no temp litter, and the landed file parses to the contract shape
    assert not [p for p in pub.out_dir.iterdir() if p.suffix == ".tmp"]
    assert set(_read(pub)) == CONTRACT_KEYS


async def test_unchanged_semantics_never_rewrite(example_config_path: Path):
    """Bridges poll mtime: identical truth must not churn it. generated_ms is
    write-time provenance — it stays put while nothing changed."""
    _sources(example_config_path.parent)
    pub = _publisher(example_config_path.parent)
    await pub.tick()
    first_bytes = pub.target.read_bytes()
    first_mtime = pub.target.stat().st_mtime_ns
    await asyncio.sleep(0.01)
    await pub.tick()
    assert pub.target.read_bytes() == first_bytes
    assert pub.target.stat().st_mtime_ns == first_mtime

    # An external edit underneath is healed on the next tick — semantic truth
    # restored; only the write-time provenance legitimately advances.
    _write(pub.target, '{"revision": 1, "enabled": true}')
    await pub.tick()
    healed, first = json.loads(pub.target.read_bytes()), json.loads(first_bytes)
    assert {k: v for k, v in healed.items() if k != "generated_ms"} == {
        k: v for k, v in first.items() if k != "generated_ms"
    }


async def test_revision_is_content_addressed(example_config_path: Path):
    _sources(example_config_path.parent)
    pub = _publisher(example_config_path.parent)
    await pub.tick()
    rev = _read(pub)["revision"]
    # Contract bound: the value must survive a JSON round-trip in JS exactly.
    assert isinstance(rev, int) and 0 <= rev <= 9007199254740991
    await pub.tick()
    assert _read(pub)["revision"] == rev       # identical truth, identical revision

    # A different account leg is a different grant.
    other = _publisher(
        example_config_path.parent, account_key="opaque-leg-2", name="q",
    )
    await other.tick()
    assert _read(other)["revision"] != rev


async def test_self_aliases_union_config_floor_and_bridge_self(
    example_config_path: Path,
):
    """The platform's configured account is the floor; the bridge's /v1/self
    answer unions on top. A VARYING answer replaces, never accretes — an old
    identity must not keep granting self-reply eligibility."""
    _sources(example_config_path.parent)
    _enable_account(example_config_path)
    state = {
        "primary": "a85290000108-0000-4000-8000-85290000109",
        "aliases": ["+85290000107"],
    }
    runner, base = await _bridge_server(state)
    try:
        pub = _publisher(
            example_config_path.parent, self_url=f"{base}/v1/self",
        )
        await pub.tick()
        assert set(_read(pub)["self_aliases"]) == {
            NORA_ACCOUNT, "a85290000108-0000-4000-8000-85290000109", "+85290000107",
        }
        # Upstream identity changes (e.g. re-registration): the alias set
        # replaces wholesale — the retired id grants nothing further.
        state["primary"] = "a1000098-0000-4000-8000-000000000098"
        state["aliases"] = []
        await pub.tick()
        assert set(_read(pub)["self_aliases"]) == {
            NORA_ACCOUNT, "a1000098-0000-4000-8000-000000000098",
        }
    finally:
        await pub.close()
        await runner.cleanup()


async def test_self_fetch_failure_fails_closed_and_marks_stale(
    example_config_path: Path,
):
    """A failed /v1/self EMPTIES the fetched alias set — self-reply receipt
    eligibility must never be granted against a possibly-rotated identity.
    The config floor survives (Signal), rooms still publish, and staleness
    is reported honestly."""
    _sources(example_config_path.parent)
    _enable_account(example_config_path)
    state = {"primary": "a85290000108-0000-4000-8000-85290000109"}
    runner, base = await _bridge_server(state)
    try:
        pub = _publisher(
            example_config_path.parent, self_url=f"{base}/v1/self",
        )
        await pub.tick()
        assert "a85290000108-0000-4000-8000-85290000109" in _read(pub)["self_aliases"]

        # The bridge stops answering /v1/self: fetched aliases DROP to empty —
        # a possibly-rotated identity grants no self-reply eligibility.
        state["self_available"] = False
        await pub.tick()
        proj = _read(pub)
        assert proj["self_aliases"] == [NORA_ACCOUNT]
        assert pub._self_aliases == frozenset()
        assert pub.status()["self_stale"] is True
        assert pub.status()["stale"] is True
        # Room capture grants still publish — capture never waits on identity.
        assert proj["rooms"] == sorted({ROOM_SIGNAL, ROOM_SIGNAL_ALSO})
    finally:
        await pub.close()
        await runner.cleanup()


async def test_no_self_source_and_no_floor_is_stale_but_publishes_scope(
    example_config_path: Path,
):
    """A platform with neither a bridge /v1/self nor a configured account
    floor publishes room grants (capture is authorized) while self-reply
    eligibility fails closed on an empty alias set and health says stale."""
    _sources(example_config_path.parent)
    _enable_whatsapp(example_config_path)
    pub = _publisher(example_config_path.parent, platform="whatsapp")
    await pub.tick()
    proj = _read(pub)
    assert proj["self_aliases"] == []           # no identity, no grant
    assert proj["rooms"] == [ROOM_WA]           # capture grant unaffected
    assert pub.status()["self_stale"] is True
    assert pub.status()["stale"] is True


# ── guild scope: the live ChannelDirectory ------------------------------------

async def test_guild_scope_expands_through_live_directory(
    example_config_path: Path,
):
    """A `guild =` entry admits exactly the room-channels the bridge's
    /v1/reads enumerates — through the SAME `ChannelDirectory` port the app
    feeds (`policy_channels` on `ReloadFiles`), never a guessed set. The map
    updates while no app process exists, and an unavailable bridge denies
    rather than inventing an empty or stale grant."""
    _sources(example_config_path.parent, policy=_POLICY_GUILD)
    _enable_discord(example_config_path)
    state = {
        "available": True,
        "channels": {GUILD_ID: {GUILD_CHAN_A: "general"}},
        "threads": {},
    }
    runner, base = await _bridge_server(state)
    try:
        pub = _publisher(
            example_config_path.parent, platform="discord",
            reads_url=f"{base}/v1/reads", self_url=f"{base}/v1/self",
        )
        await pub.tick()
        proj = _read(pub)
        room = f"{GUILD_ID}/{GUILD_CHAN_A}"
        assert proj["rooms"] == [room]
        # The grant on the expanded room flows through is_allowed+platform_ids
        # like any listed room.
        assert proj["room_receipt_senders"][room] == [MILES_DISCORD]
        assert pub.status()["directory"]["stale"] is False

        # Dynamic discovery, no app anywhere: a new channel gains admission.
        state["channels"][GUILD_ID][GUILD_CHAN_B] = "dev"
        await pub.tick()
        proj = _read(pub)
        assert f"{GUILD_ID}/{GUILD_CHAN_B}" in proj["rooms"]
        assert proj["room_receipt_senders"][f"{GUILD_ID}/{GUILD_CHAN_B}"] == [
            MILES_DISCORD,
        ]

        # Bridge goes unavailable → the guild drops out of the next snapshot:
        # absent is deny, and health reports the directory stale.
        state["available"] = False
        await pub.tick()
        proj = _read(pub)
        assert proj["rooms"] == []
        assert proj["room_receipt_senders"] == {}
        st = pub.status()
        assert st["directory"]["stale"] is True and st["stale"] is True

        state["available"] = True
        await pub.tick()
        assert f"{GUILD_ID}/{GUILD_CHAN_A}" in _read(pub)["rooms"]
        assert pub.status()["directory"]["stale"] is False
    finally:
        await pub.close()
        await runner.cleanup()


async def test_guild_policy_denies_scope_when_directory_never_available(
    example_config_path: Path,
):
    """First-boot with no reachable directory: the guild expands to nothing —
    unknown scope denied, honest stale, file still carries the deny posture."""
    _sources(example_config_path.parent, policy=_POLICY_GUILD)
    _enable_discord(example_config_path)
    pub = _publisher(
        example_config_path.parent, platform="discord",
        reads_url="http://127.0.0.1:1/v1/reads",
    )
    await pub.tick()
    proj = _read(pub)
    assert proj["rooms"] == []
    assert proj["room_receipt_senders"] == {}
    assert pub.status()["directory"]["stale"] is True
    await pub.close()


async def test_directory_threads_and_ttl_expiry():
    """`parent_channel` answers thread→room from the same snapshot; a snapshot
    past its TTL reads `None` — unavailable, never a stale half-answer."""
    now = [1000.0]
    channels = {GUILD_ID: {GUILD_CHAN_A: "general"}}
    threads = {"700000000000000201": GUILD_CHAN_A}

    class _Resp:
        def __init__(self, body):
            self._body = body

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def raise_for_status(self):
            pass

        async def json(self):
            return self._body

    class _Session:
        def post(self, _url, **kw):
            gid = kw["json"]["params"]["guild_id"]
            return _Resp({"available": True, "value": {
                "channels": channels.get(gid, {}), "threads": threads,
            }})

    directory = BridgeChannelDirectory(
        reads_url="http://bridge/v1/reads", ttl_seconds=30.0,
        clock=lambda: now[0],
    )
    await directory.refresh(_Session(), {GUILD_ID})
    assert directory.guild_channels(guild_id=GUILD_ID) == {
        GUILD_CHAN_A: "general",
    }
    assert directory.parent_channel(
        channel_id="700000000000000201",
    ) == GUILD_CHAN_A
    assert directory.stale is False

    now[0] += 31.0                       # past the TTL
    assert directory.guild_channels(guild_id=GUILD_ID) is None
    assert directory.parent_channel(channel_id="700000000000000201") is None
    assert directory.stale is True

    now[0] += 1.0
    await directory.refresh(_Session(), {GUILD_ID})
    assert directory.guild_channels(guild_id=GUILD_ID) == {
        GUILD_CHAN_A: "general",
    }
    assert directory.stale is False


# ── music notification routing -------------------------------------------------

async def test_music_channels_route_outbound_never_admitted(
    example_config_path: Path,
):
    """`music_channels` carries the operator's announcement route for the
    discord bridge — an OUTBOUND destination only. It is never an admitted
    room, earns no receipt grant, and updates on a valid policy reload."""
    _sources(example_config_path.parent, policy=_POLICY_MUSIC)
    _enable_discord(example_config_path)
    pub = _publisher(example_config_path.parent, platform="discord")
    await pub.tick()
    proj = _read(pub)

    assert proj["music_channels"] == {GUILD_ID: MUSIC_CHAN}
    # The declaring room is admitted; the announcement channel is NOT.
    assert proj["rooms"] == [f"{GUILD_ID}/{GUILD_CHAN_A}"]
    assert f"{GUILD_ID}/{MUSIC_CHAN}" not in proj["room_receipt_senders"]
    assert all(MUSIC_CHAN not in r for r in proj["rooms"])

    # A valid reload moving the route updates the map; grants are untouched.
    new_chan = "700000000000000304"
    _write(
        example_config_path.parent / "policy.toml",
        _POLICY_MUSIC.replace(MUSIC_CHAN, new_chan),
    )
    await pub.tick()
    proj = _read(pub)
    assert proj["music_channels"] == {GUILD_ID: new_chan}
    assert proj["room_receipt_senders"][f"{GUILD_ID}/{GUILD_CHAN_A}"] == [
        MILES_DISCORD,
    ]


# ── write-failure staleness ----------------------------------------------------

async def test_write_failure_marks_stale_until_new_truth_lands(
    example_config_path: Path, monkeypatch,
):
    """A failed publish must not borrow health from an older write: revoke
    the policy, inject a rename failure → health 503 material + the old file
    intact; heal → the new scope lands and status clears."""
    _sources(example_config_path.parent)
    _enable_account(example_config_path)
    pub = _publisher(example_config_path.parent)
    await pub.tick()
    good_bytes = pub.target.read_bytes()
    assert pub.status()["ok"] is True

    # Revoke the signal room — a real truth change on a still-valid file
    # (the loader refuses a zero-conversation policy outright).
    _write(
        example_config_path.parent / "policy.toml",
        _POLICY.replace(f"""\
[[conversations]]
platform = "signal"
id = "{ROOM_SIGNAL}"
also_ids = ["{ROOM_SIGNAL_ALSO}"]
label = "Family"
persons = ["miles"]

""", ""),
    )
    fail = {"on": True}
    real_replace = os.replace

    def maybe_replace(src, dst):
        if fail["on"]:
            raise OSError("disk full")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", maybe_replace)
    await pub.tick()
    # The old file is intact, and status is honestly stale: last_write_ms
    # from the earlier success must NOT mark this state ok.
    assert pub.target.read_bytes() == good_bytes
    st = pub.status()
    assert st["write_stale"] is True
    assert st["stale"] is True and st["ok"] is False

    # Repair: the NEXT write lands the current truth and clears the flag.
    fail["on"] = False
    await pub.tick()
    proj = _read(pub)
    assert proj["rooms"] == []
    st = pub.status()
    assert st["write_stale"] is False
    assert st["stale"] is False and st["ok"] is True
