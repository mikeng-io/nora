"""The operator control plane: `nora groups` and `nora persons pending`.

Every test here drives the REAL argparse surface (`nora.__main__.build_parser`) against a
real PostgreSQL record store and a fake signal-cli speaking the daemon's verified wire
shape, because the properties that matter are end-to-end ones: that two ids of one account
collapse, that the emitted stanza parses, and that nothing is written.
"""

from __future__ import annotations

import hashlib
import tomllib
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import tomli_w
from aiohttp import web

from nora.__main__ import build_parser
from nora.app import cli
from nora.domain.models import NormalizedMessage
from nora.domain.reminders import Reminder, ReminderState
from nora.infra.sql import SqlRecordStore, create_sql_engine, run_migrations
from nora.infra.sql.reminders import SqlReminderStore
from nora.persons import load_persons
from nora.policy import load_policy

ROOT = Path(__file__).resolve().parents[1]

GROUP_ID = "Lf2L9zqeNUahRsOz6BA+sTB8aWfbuvqu9vOhpvmJi34="
OTHER_GROUP_ID = "T3RoZXJHcm91cElkQmFzZTY0PT0="
MILES_PHONE = "+85290000101"
MILES_ACI = "a1000001-0000-4000-8000-000000000001"
STRANGER = "+85200000000"


# ── fake signal-cli ──────────────────────────────────────────────────────────

def daemon_app(*, groups=None, contacts=None, seen=None):
    """A signal-cli whose `/api/v1/rpc` answers the two read methods, and
    records every method it was asked for."""

    async def rpc(request):
        payload = await request.json()
        if seen is not None:
            seen.append(payload["method"])
        result = groups if payload["method"] == "listGroups" else contacts
        return web.json_response({"jsonrpc": "2.0", "result": result or [],
                                  "id": payload["id"]})

    app = web.Application()
    app.router.add_post("/api/v1/rpc", rpc)
    return app


def group_row(*, id=GROUP_ID, name="Ridgeline", members=()):
    return {
        "id": id, "name": name, "isMember": True, "isBlocked": False,
        "members": [dict(m) for m in members],
    }


MILES_MEMBER = {"number": MILES_PHONE, "uuid": MILES_ACI, "isAdmin": True}


def envelope(*, source="", number="", uuid="", name="", body="hi", ts=1):
    """One stored signal-cli envelope, in the HTTP-transport framing
    `messages.raw_json` actually holds (verified against the live DB)."""
    env = {"sourceName": name, "timestamp": ts,
           "dataMessage": {"message": body, "groupInfo": {"groupId": GROUP_ID}}}
    for key, value in (("source", source), ("sourceNumber", number), ("sourceUuid", uuid)):
        if value:
            env[key] = value
    return {"envelope": env, "account": "+85290000100"}


# ── fixtures ─────────────────────────────────────────────────────────────────

IDENTITY_EMPTY = "[persons.nobody]\nname = \"Nobody\"\n[[persons.nobody.signal]]\nphone = \"+0\"\n"
POLICY_ALLOWING_GROUP = (
    'default = "deny"\n[[conversations]]\nplatform = "signal"\n'
    f'id = "{GROUP_ID}"\nlabel = "Ridgeline"\npersons = ["nobody"]\n'
)


@pytest.fixture
async def env(tmp_path, aiohttp_server, monkeypatch, pg_url, example_config_path):
    """A config.toml, an identity.toml, a policy.toml, a migrated database and
    a fake daemon — the whole 3am situation, with nothing else running.

    `OMNIROUTE_API_KEY` is deliberately UNSET: the key lives in `.env` and is
    injected into the container, so the operator's shell does not have it, and
    a control plane that refused to run without it would be useless exactly
    when it is needed. `DATABASE_URL`, by contrast, IS present: after cutover the
    operator runs this via `docker exec` into a nora-network container, which
    carries it. `pg_url` provides a fresh throwaway database and exports it.
    """
    monkeypatch.delenv("OMNIROUTE_API_KEY", raising=False)
    state = {"groups": [], "contacts": [], "seen": []}

    async def rpc(request):
        payload = await request.json()
        state["seen"].append(payload["method"])
        result = state["groups"] if payload["method"] == "listGroups" else state["contacts"]
        return web.json_response({"jsonrpc": "2.0", "result": result, "id": payload["id"]})

    app = web.Application()
    app.router.add_post("/api/v1/rpc", rpc)
    daemon = await aiohttp_server(app)

    with example_config_path.open("rb") as f:
        cfg = tomllib.load(f)
    cfg["platforms"]["signal"]["base_url"] = str(daemon.make_url("")).rstrip("/")
    cfg["store"]["data_dir"] = str(tmp_path)
    cfg["doctrine"]["dir"] = str(ROOT / "nora" / "doctrine")
    cfg["identity_file"] = str(tmp_path / "identity.toml")
    cfg["policy_file"] = str(tmp_path / "policy.toml")
    (tmp_path / "identity.toml").write_text(IDENTITY_EMPTY)
    (tmp_path / "policy.toml").write_text(POLICY_ALLOWING_GROUP)
    config_path = tmp_path / "config.toml"
    with config_path.open("wb") as f:
        tomli_w.dump(cfg, f)

    run_migrations(pg_url)
    state.update(path=config_path, tmp=tmp_path, url=pg_url)
    return state


async def audit(env, *, sender, raw, ts=1, body="hi", person_id=None):
    store = SqlRecordStore(create_sql_engine(env["url"]))
    try:
        return await store.append(
            message=NormalizedMessage(
                platform="signal", conversation_id=GROUP_ID, conversation_type="group",
                sender_id=sender, sender_name=None, timestamp=ts, body=body, raw=raw,
            ),
            person_id=person_id,
        )
    finally:
        await store.aclose()


async def invoke(env, *argv):
    """Parse and run through the real CLI surface, returning the exit code.

    `cli.execute`, not `cli.run`: the two differ only by `asyncio.run`, which
    cannot nest inside the loop pytest-asyncio already owns. The error handling
    under test lives in `execute`; `test_run_turns_a_cli_error_into_an_exit_code`
    covers the sync wrapper separately.
    """
    args = build_parser().parse_args([*argv, "--config", str(env["path"])])
    return await cli.execute(args)


async def test_persona_media_retry_only_queues_known_completed_account(env, capsys):
    import json

    import sqlalchemy as sa

    from nora.app.composition import character_person_keys
    from nora.infra.sql.character import SqlCharacterEvidence
    from nora.infra.sql.persona_inputs import SqlPersonaInputs
    from nora.infra.sql.schema import CONSOLIDATION_WATERMARK as W
    from nora.infra.sql.schema import MEDIA_OBJECTS, MESSAGES

    cfg = tomllib.loads(env['path'].read_text())
    for section in ('dreamer', 'culture', 'persona'):
        cfg[section]['enabled'] = True
    env['path'].write_text(tomli_w.dumps(cfg))
    keys = character_person_keys(load_policy(Path(cfg['policy_file'])),
                                 load_persons(Path(cfg['identity_file'])))
    [key] = keys
    assert await invoke(env, 'memory', 'persona-retry-media', '--key', 'unknown', '--apply') == 1
    capsys.readouterr()
    engine = create_sql_engine(env['url'])
    try:
        sha = 'e' * 64
        async with engine.begin() as conn:
            await conn.execute(sa.insert(MESSAGES).values(id=10, platform='signal',
                conversation_id=GROUP_ID, conversation_type='group', sender_id='synthetic',
                person_id='nobody', ts=1, body='synthetic', media_json=json.dumps([
                    {'sha256': sha, 'media_type': 'image/png'}]), is_nora=0))
            await conn.execute(sa.insert(MEDIA_OBJECTS).values(sha256=sha, media_type='image/png',
                size=1, source='inbound', created_at_ms=1, description='cached synthetic scene'))
        # No history reset or bootstrap is permitted by the control command.
        assert await invoke(env, 'memory', 'persona-retry-media', '--key', key, '--apply') == 1
        capsys.readouterr()
        reads = SqlCharacterEvidence(engine)
        boundary, _ = await reads.history_state(key=key)
        await reads.advance_history(key=key, cursor=boundary)
        async with engine.connect() as conn:
            before = list((await conn.execute(sa.select(W))).all())
        assert await invoke(env, 'memory', 'persona-retry-media', '--key', key) == 0
        dry = json.loads(capsys.readouterr().out)
        assert dry['pending_cached'] == 1 and not dry['requested'] and not dry['applied']
        async with engine.connect() as conn:
            assert list((await conn.execute(sa.select(W))).all()) == before
        assert await invoke(env, 'memory', 'persona-retry-media', '--key', key, '--apply') == 0
        applied = json.loads(capsys.readouterr().out)
        assert applied['requested'] and applied['applied']
        source = SqlPersonaInputs(engine)
        assert await source.recovery_requested(key=key)
        assert await reads.history_state(key=key) == (boundary, boundary)
        await source.claim_recovery(key=key)
        pending = await source.messages(key=key, rooms=[('signal', GROUP_ID)],
                                        person='nobody', limit=100)
        await source.settle(key=key, revisions=pending)
        assert await invoke(env, 'memory', 'persona-retry-media', '--key', key, '--apply') == 1
        assert not await source.recovery_requested(key=key)
        assert env['seen'] == []  # no daemon/model/boot recovery is composed
    finally:
        await engine.dispose()


# ── nora groups ──────────────────────────────────────────────────────────────

async def test_groups_marks_an_allowlisted_group_allowed(env, capsys):
    env["groups"] = [group_row()]
    assert await invoke(env, "groups") == 0
    out = capsys.readouterr().out
    assert "ALLOWED" in out
    assert "DENIED" not in out
    assert GROUP_ID in out
    assert "Ridgeline" in out
    assert "1 group(s): 1 allowed, 0 denied." in out


async def test_groups_marks_an_unlisted_group_denied(env, capsys):
    """The operator's original question — which id is which group — answered
    beside the verdict, so a denied group is identifiable rather than a
    base64 blob he has to guess at."""
    env["groups"] = [group_row(id=OTHER_GROUP_ID, name="Family")]
    assert await invoke(env, "groups") == 0
    out = capsys.readouterr().out
    assert "DENIED" in out
    assert "ALLOWED" not in out
    assert "Family" in out and OTHER_GROUP_ID in out
    assert "1 group(s): 0 allowed, 1 denied." in out


async def test_groups_shows_both_verdicts_side_by_side(env, capsys):
    env["groups"] = [group_row(), group_row(id=OTHER_GROUP_ID, name="Family")]
    await invoke(env, "groups")
    out = capsys.readouterr().out
    assert "2 group(s): 1 allowed, 1 denied." in out


async def test_the_denied_groups_paste_stanza_actually_allows_it(env, capsys, tmp_path):
    """The suggestion is only worth printing if pasting it works: the emitted
    `[[conversations]]` block must parse AND flip the verdict."""
    env["groups"] = [group_row(id=OTHER_GROUP_ID, name="Family")]
    await invoke(env, "groups")
    out = capsys.readouterr().out
    block = out[out.index("[[conversations]]"):out.index("persons  =")]
    pasted = tmp_path / "pasted-policy.toml"
    pasted.write_text(
        'default = "deny"\n'
        + "\n".join(line.strip() for line in block.splitlines())
        + '\npersons = "*"\n'
    )
    policy = load_policy(pasted)
    assert policy.is_allowed(
        platform="signal", conversation_id=OTHER_GROUP_ID, person_id=None
    )


async def test_groups_degrades_to_one_sentence_when_the_daemon_is_down(env, capsys):
    """No traceback, a named exit code, and the two commands that diagnose it."""
    cfg = tomllib.loads(env["path"].read_text())
    cfg["platforms"]["signal"]["base_url"] = "http://127.0.0.1:1"
    env["path"].write_text(tomli_w.dumps(cfg))
    assert await invoke(env, "groups") == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "could not reach the signal-cli daemon" in captured.err
    assert "docker compose ps signal-cli" in captured.err
    assert "Traceback" not in captured.err


async def test_groups_only_asks_the_daemon_to_read(env):
    env["groups"] = [group_row()]
    await invoke(env, "groups")
    assert env["seen"] == ["listGroups"]


# ── nora persons pending ─────────────────────────────────────────────────────

async def test_two_ids_of_one_account_collapse_into_one_pending_entry(env, capsys):
    """THE core requirement. Sealed sender delivers the same account as a phone
    number on one message and as an ACI on another; listing them separately
    would invite two `[[persons.x.signal]]` blocks — the copy-paste duplicate
    `load_persons` refuses to start on."""
    env["contacts"] = [{"number": MILES_PHONE, "uuid": MILES_ACI, "name": "Miles Novak"}]
    await audit(env, sender=MILES_PHONE, ts=1,
                raw=envelope(source=MILES_PHONE, number=MILES_PHONE, name="Miles Novak"))
    await audit(env, sender=MILES_ACI, ts=2, raw=envelope(uuid=MILES_ACI, name="Miles Novak"))

    assert await invoke(env, "persons", "pending") == 0
    out = capsys.readouterr().out
    assert "# 1 unmapped account(s) from 2 unattributed row(s)." in out
    assert out.count("[[persons.") == 1
    assert MILES_PHONE in out and MILES_ACI in out
    assert "Miles Novak" in out


async def test_a_group_roster_correlates_ids_no_contact_pairs(env, capsys):
    """`listContacts` is not enough on its own: on the live daemon only 3 of 83
    contacts carry both a number and a uuid, while every group MEMBER does. A
    contact list that pairs nothing must still collapse the account."""
    env["contacts"] = [{"number": MILES_PHONE, "name": "Miles Novak"}]      # no uuid
    env["groups"] = [group_row(members=[MILES_MEMBER])]
    await audit(env, sender=MILES_PHONE, ts=1, raw=envelope(source=MILES_PHONE))
    await audit(env, sender=MILES_ACI, ts=2, raw=envelope(uuid=MILES_ACI))

    await invoke(env, "persons", "pending")
    out = capsys.readouterr().out
    assert "# 1 unmapped account(s)" in out
    assert MILES_PHONE in out and MILES_ACI in out


async def test_one_envelope_alone_collapses_its_own_id_forms(env, capsys):
    """The correlator of last resort, and the only one left when the daemon is
    down: ids that arrived on the SAME envelope named one sender by
    construction."""
    await audit(env, sender=MILES_PHONE, ts=1,
                raw=envelope(source=MILES_PHONE, number=MILES_PHONE, uuid=MILES_ACI))
    await invoke(env, "persons", "pending")
    out = capsys.readouterr().out
    assert "# 1 unmapped account(s)" in out
    assert MILES_PHONE in out and MILES_ACI in out


async def test_a_later_row_merges_two_accounts_already_filed_apart(env, capsys):
    """With no daemon there is nothing to link ids before the rows are read, so
    the FIRST two rows are filed under separate roots and only the third joins
    them. Grouping in the same pass that links would leave the first two filed
    under the roots they had at the time — one account reported as two, which
    is the duplicate-block trap.
    """
    cfg = tomllib.loads(env["path"].read_text())
    cfg["platforms"]["signal"]["base_url"] = "http://127.0.0.1:1"
    env["path"].write_text(tomli_w.dumps(cfg))
    await audit(env, sender=MILES_ACI, ts=1, raw=envelope(uuid=MILES_ACI))
    await audit(env, sender=MILES_PHONE, ts=2, raw=envelope(source=MILES_PHONE))
    await audit(env, sender=MILES_PHONE, ts=3,
                raw=envelope(source=MILES_PHONE, uuid=MILES_ACI))

    await invoke(env, "persons", "pending")
    out = capsys.readouterr().out
    assert "# 1 unmapped account(s) from 3 unattributed row(s)." in out
    assert out.count("[[persons.") == 1


async def test_a_sender_absent_from_listcontacts_reads_unknown(env, capsys):
    env["contacts"] = [{"number": MILES_PHONE, "uuid": MILES_ACI, "name": "Miles Novak"}]
    await audit(env, sender=STRANGER, raw=envelope(source=STRANGER, number=STRANGER))
    await invoke(env, "persons", "pending")
    out = capsys.readouterr().out
    assert "# ── unknown  (signal)" in out
    assert "# not in signal-cli's contacts" in out
    assert STRANGER in out


async def test_a_spoofable_profile_name_is_never_the_identification(env, capsys):
    """`sourceName` is chosen by the sender and changeable at will. It
    is reported and labelled, never printed as who this account IS."""
    await audit(env, sender=STRANGER,
                raw=envelope(source=STRANGER, name="Miles Novak"))
    await invoke(env, "persons", "pending")
    out = capsys.readouterr().out
    assert "# ── unknown  (signal)" in out
    assert "sender-controlled and spoofable" in out
    assert 'name = "Miles Novak"' not in out


async def test_the_emitted_stanza_parses_and_resolves_every_id(env, capsys, tmp_path):
    """The output is worthless if a paste breaks startup. Round-trip it through
    the real loader and confirm every id form resolves to the same person."""
    env["contacts"] = [{"number": MILES_PHONE, "uuid": MILES_ACI, "name": "Miles Novak"}]
    await audit(env, sender=MILES_PHONE, ts=1, raw=envelope(source=MILES_PHONE))
    await audit(env, sender=MILES_ACI, ts=2, raw=envelope(uuid=MILES_ACI))
    await invoke(env, "persons", "pending")
    out = capsys.readouterr().out

    stanza = out[out.index("[persons."):out.index("# Paste into identity.toml")]
    pasted = tmp_path / "pasted-identity.toml"
    pasted.write_text(stanza)
    persons = load_persons(pasted)
    resolved = {persons.resolve("signal", MILES_PHONE), persons.resolve("signal", MILES_ACI)}
    assert len(resolved) == 1 and None not in resolved
    assert next(iter(resolved)).name == "Miles Novak"


async def test_two_unknown_accounts_get_distinct_person_ids(env, capsys, tmp_path):
    """Both slug to `person`, and two `[persons.person]` tables in one file is
    a TOML duplicate-key error — the stanza would not even load."""
    await audit(env, sender=STRANGER, ts=1, raw=envelope(source=STRANGER))
    await audit(env, sender="+85211111111", ts=2, raw=envelope(source="+85211111111"))
    await invoke(env, "persons", "pending")
    out = capsys.readouterr().out
    stanza = out[out.index("[persons."):out.index("# Paste into identity.toml")]
    pasted = tmp_path / "pasted-identity.toml"
    pasted.write_text(stanza)
    assert len(load_persons(pasted).by_person) == 2


async def test_a_sender_mapped_since_ingest_is_no_longer_pending(env, capsys):
    """`person_id IS NULL` means "unmapped WHEN IT ARRIVED". Once the operator
    has added the account, the row belongs to lazy adoption (DEC-16), not to
    this list — and re-suggesting it is how a duplicate block gets pasted."""
    env["tmp"].joinpath("identity.toml").write_text(
        f'[persons.miles]\nname = "Miles"\n[[persons.miles.signal]]\nphone = "{MILES_PHONE}"\n'
    )
    await audit(env, sender=MILES_PHONE, raw=envelope(source=MILES_PHONE))
    await invoke(env, "persons", "pending")
    out = capsys.readouterr().out
    assert "# No unmapped senders" in out
    assert MILES_PHONE not in out


async def test_mapping_one_id_form_retires_the_whole_account(env, capsys):
    """The correlation cuts both ways: an operator who mapped the phone number
    must not be told the ACI is still pending, because pasting THAT is the
    same-id-in-two-blocks error."""
    env["contacts"] = [{"number": MILES_PHONE, "uuid": MILES_ACI, "name": "Miles Novak"}]
    env["tmp"].joinpath("identity.toml").write_text(
        f'[persons.miles]\nname = "Miles"\n[[persons.miles.signal]]\nphone = "{MILES_PHONE}"\n'
    )
    await audit(env, sender=MILES_ACI, raw=envelope(uuid=MILES_ACI))
    await invoke(env, "persons", "pending")
    assert "# No unmapped senders" in capsys.readouterr().out


async def test_pending_survives_an_unreachable_daemon(env, capsys):
    """It loses the names, and says so — rather than failing, which would make
    the tool useless in the one situation it exists for."""
    cfg = tomllib.loads(env["path"].read_text())
    cfg["platforms"]["signal"]["base_url"] = "http://127.0.0.1:1"
    env["path"].write_text(tomli_w.dumps(cfg))
    await audit(env, sender=MILES_PHONE, raw=envelope(source=MILES_PHONE, name="Miles Novak"))

    assert await invoke(env, "persons", "pending") == 0
    out = capsys.readouterr().out
    assert "WARNING: signal-cli is unreachable" in out
    assert "one person may appear twice" in out
    assert "# ── unknown  (signal)" in out
    assert MILES_PHONE in out
    # It must not claim a lookup it never performed came back empty.
    assert "not in signal-cli's contacts" not in out
    assert "signal-cli was unreachable, so no name could be looked up" in out


async def test_pending_only_asks_the_daemon_to_read(env):
    await audit(env, sender=MILES_PHONE, raw=envelope(source=MILES_PHONE))
    await invoke(env, "persons", "pending")
    assert sorted(env["seen"]) == ["listContacts", "listGroups"]


async def test_noras_own_rows_are_never_offered_for_adoption(env, capsys):
    """Her account is never written to identity.toml (DEC-9), so an outbound
    row can only ever resolve to nobody — offering it would be a permanent,
    unfixable entry at the top of this list."""
    store = SqlRecordStore(create_sql_engine(env["url"]))
    try:
        await store.append(message=NormalizedMessage(
            platform="signal", conversation_id=GROUP_ID, conversation_type="group",
            sender_id="+85290000100", sender_name="Nora", timestamp=1,
            body="mine", raw=envelope(source="+85290000100"),
        ), is_nora=True)
    finally:
        await store.aclose()
    await invoke(env, "persons", "pending")
    assert "# No unattributed audit rows at all" in capsys.readouterr().out


async def test_a_truncated_scan_says_so(env, capsys):
    """A silently truncated list is one an operator would read as complete."""
    for i in range(3):
        await audit(env, sender=f"+8521111111{i}", ts=i,
                    raw=envelope(source=f"+8521111111{i}"))
    await invoke(env, "persons", "pending", "--limit", "2")
    out = capsys.readouterr().out
    assert "# The scan stopped at its 2-row limit" in out


async def test_a_non_positive_limit_is_refused(env, capsys):
    """`unattributed(limit=0)` returns `[]`, so without this the report would
    announce "no unattributed audit rows at all" — a false statement about the
    database, produced by a typo in a flag."""
    await audit(env, sender=MILES_PHONE, raw=envelope(source=MILES_PHONE))
    assert await invoke(env, "persons", "pending", "--limit", "0") == 1
    captured = capsys.readouterr()
    assert "--limit must be positive" in captured.err
    assert "No unattributed audit rows" not in captured.out


async def test_an_unreachable_store_is_a_sentence_not_a_traceback(env, capsys, monkeypatch):
    """`nora-db` down, or a database Nora has never run against, is one readable
    line and an exit code — never a traceback at 3am."""
    from sqlalchemy.engine import make_url
    missing = make_url(env["url"]).set(database="does_not_exist")
    monkeypatch.setenv("DATABASE_URL", missing.render_as_string(hide_password=False))
    assert await invoke(env, "persons", "pending") == 1
    err = capsys.readouterr().err
    assert "could not read the record store" in err
    assert "Traceback" not in err


# ── nora reminders list ──────────────────────────────────────────────────────

REMINDER_T0 = datetime(2026, 8, 8, 11, 0, tzinfo=UTC)


async def _seed_reminder(env, **kw):
    """One reminder written through a WRITABLE store — the CLI's own engine
    cannot create rows, which is the property under test."""
    base = {
        "id": uuid.uuid4(), "requester_person_id": "miles_novak", "platform": "signal",
        "conversation_id": GROUP_ID, "conversation_type": "group", "text": "洗衫",
        "created_at": REMINDER_T0, "due_at": REMINDER_T0 + timedelta(hours=2),
        "state": ReminderState.PENDING, "fired_at": None, "outcome": None,
    }
    base.update(kw)
    row = Reminder(**base)
    store = SqlReminderStore(create_sql_engine(env["url"]))
    try:
        await store.create(row)
    finally:
        await store.aclose()
    return row


async def test_reminders_list_shows_what_is_still_waiting(env, capsys):
    row = await _seed_reminder(env)
    assert await invoke(env, "reminders", "list") == 0
    out = capsys.readouterr().out
    assert str(row.id) in out
    assert "miles_novak" in out
    assert GROUP_ID in out
    assert "洗衫" in out
    assert "1 pending" in out


async def test_reminders_list_never_shows_a_row_that_already_resolved(env, capsys):
    """A reminder resolves ONCE and is never re-run, so a `done` row in
    the "what is still coming" listing would read as a message about to be sent
    twice."""
    await _seed_reminder(env, state=ReminderState.DONE, text="already gone")
    await _seed_reminder(env, state=ReminderState.CANCELLED, text="called off")
    assert await invoke(env, "reminders", "list") == 0
    out = capsys.readouterr().out
    assert "already gone" not in out
    assert "called off" not in out
    assert "0 pending" in out
    assert "Nothing is waiting" in out


async def test_reminders_list_calls_out_an_overdue_row(env, capsys):
    """The one diagnosis this command exists for: a row whose time has passed
    and which is STILL pending means the tick is not firing it. Silence there
    would look identical to a quiet week."""
    await _seed_reminder(env, due_at=datetime(2020, 1, 1, tzinfo=UTC))
    assert await invoke(env, "reminders", "list") == 0
    out = capsys.readouterr().out
    assert "OVERDUE" in out
    assert "1 pending, 1 overdue" in out
    assert "heartbeat" in out


async def test_reminders_list_says_truncated_only_when_it_really_is(env, capsys):
    """A complete listing is not reported truncated.

    The store answers at most what it is asked for, so a database holding exactly
    `--limit` rows produces the same length as one that was cut off; `len(rows) >= limit`
    would tell the operator to re-run with a larger limit and print identical output. The
    read asks for one row more than it prints, which is what makes the two states
    distinguishable.
    """
    for i in range(3):
        await _seed_reminder(env, text=f"row-{i}",
                             due_at=REMINDER_T0 + timedelta(hours=i + 1))

    assert await invoke(env, "reminders", "list", "--limit", "3") == 0
    out = capsys.readouterr().out
    assert "3 pending" in out
    assert "truncated" not in out          # exactly `limit` rows is COMPLETE

    assert await invoke(env, "reminders", "list", "--limit", "2") == 0
    out = capsys.readouterr().out
    assert "2 pending" in out
    assert "Listing was truncated" in out
    # And it prints `limit` rows, never the extra one it read to find out.
    assert "row-2" not in out


async def test_reminders_list_refuses_a_non_positive_limit(env, capsys):
    assert await invoke(env, "reminders", "list", "--limit", "0") == 1
    assert "--limit must be positive" in capsys.readouterr().err


async def test_the_reminder_store_the_cli_opens_refuses_a_write(env):
    """`cli.readonly_reminder_store` is the one place the control plane opens
    the reminders table, so this is the store `nora reminders list` really uses.

    `cancel` is the write that matters: it is the one subcommand this module
    could plausibly grow, and it must not — the operator's kill switch is
    `[heartbeat] enabled = false` plus a restart, so the CLI must not cancel. The
    driver refuses it, so no future edit can add it by accident.
    """
    row = await _seed_reminder(env)
    store = cli.readonly_reminder_store(env["url"])
    try:
        assert await store.all_pending(limit=10), "the store must still READ"
        with pytest.raises(Exception, match=r"read-only transaction"):
            await store.cancel(row.id, conversation_id=GROUP_ID)
    finally:
        await store.aclose()
    check = SqlReminderStore(create_sql_engine(env["url"]))
    try:
        assert (await check.get(row.id)).state is ReminderState.PENDING
    finally:
        await check.aclose()


async def test_there_is_no_way_to_cancel_a_reminder_from_the_cli(env):
    """A test rather than a promise. `nora reminders cancel` must not
    parse — the read-only guarantee is structural, and a subcommand that existed
    and then failed at the driver would be a worse answer than one that never
    existed."""
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            ["reminders", "cancel", "x", "--config", str(env["path"])],
        )


# ── read-only, and the daemon-less path ──────────────────────────────────────

def _tree(path: Path) -> dict[str, str]:
    return {
        str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(path.rglob("*")) if p.is_file()
    }


async def _rows(env):
    store = SqlRecordStore(create_sql_engine(env["url"]))
    try:
        return await store.recent(conversation_id=GROUP_ID, limit=1000)
    finally:
        await store.aclose()


@pytest.mark.parametrize("argv", [("groups",), ("persons", "pending")])
async def test_neither_command_writes_a_host_file(env, capsys, argv):
    """identity.toml stays authoritative and hand-edited; this is not negotiable.
    Hashing the whole tree catches a rewritten config.toml, a rewritten
    identity.toml, and any file the command invented. The record store is
    Postgres, not a file in this tree — its rows are covered by
    `test_neither_command_changes_a_single_audit_row`."""
    env["groups"] = [group_row(members=[MILES_MEMBER])]
    env["contacts"] = [{"number": MILES_PHONE, "uuid": MILES_ACI, "name": "Miles Novak"}]
    await audit(env, sender=MILES_ACI, raw=envelope(uuid=MILES_ACI))

    before = _tree(env["tmp"])
    assert {"config.toml", "identity.toml", "policy.toml"} <= set(before)
    assert await invoke(env, *argv) == 0
    capsys.readouterr()
    assert _tree(env["tmp"]) == before


@pytest.mark.parametrize("argv", [("groups",), ("persons", "pending")])
async def test_neither_command_changes_a_single_audit_row(env, capsys, argv):
    """Closes the gap the sidecar exemption above leaves: the rows themselves,
    read back through a fresh store."""
    env["contacts"] = [{"number": MILES_PHONE, "uuid": MILES_ACI, "name": "Miles Novak"}]
    await audit(env, sender=MILES_ACI, raw=envelope(uuid=MILES_ACI))
    before = await _rows(env)
    assert before, "the fixture must have audited something to compare"
    await invoke(env, *argv)
    capsys.readouterr()
    assert await _rows(env) == before


async def test_the_store_the_cli_actually_opens_refuses_a_write(env):
    """`cli.readonly_store` is the one place the control plane opens the audit
    database, so this is the store the commands really use — not a separately
    constructed engine that happens to be read-only.

    It is `set_person_id` rather than a raw DELETE on purpose: that is the one
    write on the whole `RecordStore` port a pending-sender tool could plausibly
    grow into ("adopt them for me"), and it must not. The driver
    refuses it, so no future edit can add it by accident.
    """
    row_id = await audit(env, sender=MILES_PHONE, raw=envelope(source=MILES_PHONE))
    store = cli.readonly_store(env["url"])
    try:
        assert await store.unattributed(limit=10), "the store must still READ"
        with pytest.raises(Exception, match=r"read-only transaction"):
            await store.set_person_id(row_id=row_id, person_id="miles")
    finally:
        await store.aclose()
    assert (await _rows(env))[0].sender_id == MILES_PHONE


async def test_pending_needs_no_llm_credential(env, capsys, monkeypatch):
    """The key lives in `.env` and reaches the CONTAINER, not the operator's
    shell — and the container is stopped in the situation this tool exists for.
    Refusing to list group ids over a credential nothing on this path uses
    would be a self-inflicted outage."""
    monkeypatch.delenv("OMNIROUTE_API_KEY", raising=False)
    await audit(env, sender=MILES_PHONE, raw=envelope(source=MILES_PHONE))
    assert await invoke(env, "persons", "pending") == 0
    assert "OMNIROUTE_API_KEY" not in capsys.readouterr().out


async def test_the_daemon_still_needs_its_key(env, monkeypatch):
    """The relaxation is scoped to the control plane. `nora.config.load`'s
    default is unchanged, so the daemon still refuses to start mute."""
    from nora.config import ConfigError, load

    monkeypatch.delenv("OMNIROUTE_API_KEY", raising=False)
    with pytest.raises(ConfigError, match="OMNIROUTE_API_KEY"):
        load(env["path"])


# ── the argparse surface ─────────────────────────────────────────────────────

def test_the_bare_daemon_invocation_is_unchanged():
    """`--config` must keep working exactly as before: no subcommand, same
    default, same attribute."""
    assert build_parser().parse_args([]).config == "config.toml"
    assert build_parser().parse_args(["--config", "x.toml"]).config == "x.toml"
    assert getattr(build_parser().parse_args([]), "func", None) is None


@pytest.mark.parametrize("argv", [
    ["--config", "x.toml", "groups"],
    ["groups", "--config", "x.toml"],
    ["--config", "x.toml", "persons", "pending"],
    ["persons", "pending", "--config", "x.toml"],
])
def test_config_is_accepted_on_either_side_of_the_subcommand(argv):
    """`argparse.SUPPRESS` on the subparser copy is what stops a subcommand
    without the flag from clobbering the top-level value with a second
    default."""
    assert build_parser().parse_args(argv).config == "x.toml"


def test_run_turns_a_cli_error_into_an_exit_code(capsys):
    """`cli.run` is the only place `asyncio.run` is called, and the only place
    an exception may reach the terminal."""
    async def boom(_args):
        raise cli.CliError("nope")

    args = build_parser().parse_args(["groups"])
    args.func = boom
    assert cli.run(args) == 1
    assert capsys.readouterr().err.strip() == "error: nope"


def test_render_presence_members_is_paste_ready():
    """`nora presence members` output carries, per member, a paste-ready
    [[persons.<id>.life360]] block with BOTH member_id and circle_id — all ids in
    identity.toml, none in config.toml."""
    from nora.app.report import render_presence_members
    from nora.infra.life360 import DiscoveredCircle, DiscoveredMember
    out = "\n".join(render_presence_members([
        DiscoveredCircle(
            circle_id="cir-1", name="Ridgeline",
            members=(DiscoveredMember("m-miles", "Miles Novak"),
                     DiscoveredMember("m-gab", "Grant")),
        )
    ]))
    assert "# Miles Novak" in out
    assert "[[persons.CHANGEME.life360]]" in out
    assert 'member_id = "m-miles"' in out
    assert 'circle_id = "cir-1"' in out
    # ids live in identity.toml, never a config.toml block
    assert "[[presence.circles]]" not in out
    assert "config.toml carries no ids" in out


def test_render_presence_members_notes_an_empty_result():
    from nora.app.report import render_presence_members
    assert any("No circles" in line for line in render_presence_members([]))


# ── nora memory rebuild ──────────────────────────────────────────────────────

async def _index_status(env):
    from nora.infra.sql import memory_index

    engine = create_sql_engine(env["url"])
    try:
        async with engine.connect() as conn:
            return await memory_index.index_status(conn)
    finally:
        await engine.dispose()


async def test_memory_rebuild_dry_run_writes_nothing_then_the_rebuild_completes(env, capsys):
    """The dry run counts and writes nothing; the run writes the index and `complete`. Both
    print counts per kind, never a message's words."""
    await audit(env, sender=MILES_PHONE, raw=envelope(number=MILES_PHONE),
                body="the lantern plan", person_id="miles")
    assert await invoke(env, "memory", "rebuild", "--dry-run") == 0
    out = capsys.readouterr().out
    assert "message: would project 1, would drop 0" in out
    assert "lantern" not in out
    assert await _index_status(env) == ("never", 0, 0)

    assert await invoke(env, "memory", "rebuild") == 0
    out = capsys.readouterr().out
    assert "message: projected 1, dropped 0" in out
    assert "unprojected: 0" in out
    assert "lantern" not in out
    assert await _index_status(env) == ("complete", 0, 0)


async def test_memory_rebuild_is_refused_while_another_rebuild_runs(env, capsys):
    import sqlalchemy as sa

    from nora.infra.sql import memory_index

    engine = create_sql_engine(env["url"])
    holder = await engine.connect()
    try:
        lock = sa.func.pg_try_advisory_lock(memory_index.REBUILD_LOCK_KEY)
        assert (await holder.execute(sa.select(lock))).scalar_one() is True
        await holder.commit()
        assert await invoke(env, "memory", "rebuild") == 1
        assert "advisory lock" in capsys.readouterr().err
    finally:
        await holder.close()
        await engine.dispose()
    assert await _index_status(env) == ("never", 0, 0)


async def test_memory_rebuild_embeddings_only_clears_vectors_and_reports_counts(env, capsys):
    """`--embeddings-only` clears vectors (all, or with `--other-models` only another
    model's) for the worker to backfill, leaves the projection alone, and prints counts,
    never a message's words. `--dry-run` clears nothing."""
    import sqlalchemy as sa

    from nora.infra.sql import memory_index
    from nora.infra.sql.schema import MEMORY_NODE

    await audit(env, sender=MILES_PHONE, raw=envelope(number=MILES_PHONE),
                body="the lantern plan", person_id="miles")
    engine = create_sql_engine(env["url"])

    async def vectors() -> list:
        async with engine.connect() as conn:
            return list((await conn.execute(
                sa.select(MEMORY_NODE.c.embedding_model).where(MEMORY_NODE.c.kind == "message")
            )).scalars())

    try:
        async with engine.begin() as conn:
            [item] = await memory_index.embed_pick(conn, limit=5, now_ms=0)
            assert await memory_index.embed_store(
                conn, item, vector=[1.0] + [0.0] * 2047, model="another-model+fmt1",
                now_ms=1, by_bytes=False,
            )

        assert await invoke(env, "memory", "rebuild", "--embeddings-only", "--dry-run") == 0
        out = capsys.readouterr().out
        assert "vectors: would clear 1 (all)" in out and "lantern" not in out
        assert await vectors() == ["another-model+fmt1"]

        assert await invoke(
            env, "memory", "rebuild", "--embeddings-only", "--other-models",
        ) == 0
        out = capsys.readouterr().out
        assert "vectors: cleared 1 (of other models)" in out
        assert "waiting for the worker: 1" in out
        assert "given up after 8 attempts: 0" in out
        assert "[memory_index] embed is false" in out
        assert await vectors() == [None]

        assert await invoke(env, "memory", "rebuild", "--other-models") == 1
        assert "--other-models applies only with --embeddings-only" in capsys.readouterr().err
    finally:
        await engine.dispose()


async def test_persona_window_and_report_are_read_only_until_explicit_apply(
    env, capsys, monkeypatch,
):
    import sqlalchemy as sa

    from nora.app.composition import character_person_keys
    from nora.infra.sql.schema import PERSONA_LINE
    cfg = cli._config(build_parser().parse_args(["memory", "persona-history",
                                               "--config", str(env["path"])]))
    monkeypatch.delenv(cfg.evals.langfuse.headers_env, raising=False)
    [key] = character_person_keys(cli._policy(cfg), cli._persons(cfg))
    async def no_runtime(_cfg):
        pytest.fail("dry-run must never compose a runtime")
    monkeypatch.setattr("nora.app.composition.build_dependencies", no_runtime)
    assert await invoke(env, "memory", "persona-history", "--key", key,
                        "--since", "2026-10-01", "--until", "2026-10-02") == 0
    output = capsys.readouterr().out
    assert '"dry_run": true' in output
    assert await invoke(env, "memory", "persona-report", "--key", key) == 0
    output = capsys.readouterr().out
    assert '"part_kind": {}' in output and '"card"' not in output
    engine = create_sql_engine(env["url"])
    try:
        async with engine.connect() as conn:
            count = (await conn.execute(
                sa.select(sa.func.count()).select_from(PERSONA_LINE))).scalar()
            assert count == 0
    finally:
        await engine.dispose()


async def test_character_report_requires_a_separately_configured_judge(monkeypatch):
    from argparse import Namespace
    from types import SimpleNamespace

    import nora.app.cli as cli
    from nora.app.cli import CliError

    cfg = SimpleNamespace(
        evals=SimpleNamespace(judge_model="writer"), dreamer_model=lambda: "writer",
        obs=SimpleNamespace(tracing=SimpleNamespace(enabled=False)),
        llm=SimpleNamespace(base_url="http://example.invalid", api_key_env="UNSET_TEST_KEY"))
    monkeypatch.setattr(cli, "_config", lambda _: cfg)
    monkeypatch.setattr(cli, "_persons", lambda _: SimpleNamespace(by_person={}))
    reached = []
    class ModelSelected(Exception):
        pass
    def factory(**request):
        if request["model_id"] != "independent":
            raise AssertionError("the writer must not be constructed as the judge")
        reached.append(request["model_id"])
        raise ModelSelected  # stop at selection; no model or database call
    monkeypatch.setattr("nora.agent.structured.build_structured_model", factory)
    args = Namespace(since=None, until=None, judge=True)
    with pytest.raises(CliError, match="must differ"):
        await cli.cmd_persona_report(args)
    assert not reached
    cfg.evals.judge_model = "independent"
    with pytest.raises(ModelSelected):
        await cli.cmd_persona_report(args)
    assert reached == ["independent"]
