"""The composition root is the ONE place that reads config. Everything else
receives dependencies. This test pins that property."""

import ast
import base64
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from nora.app.composition import Dependencies, StartupError, build_dependencies
from nora.app.fire import FirePath
from nora.app.heartbeat import HEARTBEAT_NOT_RUNNING
from nora.config import load
from nora.domain.models import NormalizedMessage
from nora.infra.blobs import FilesystemBlobStore
from nora.platform.discord.media import DiscordMediaSource
from nora.platform.registry import MediaSourceRegistry
from nora.platform.signal.media import SignalMediaSource
from nora.policy import load_policy
from tests.builders import override_config

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def cfg(tmp_path, monkeypatch, pg_url):
    # `pg_url` (tests/conftest.py) creates a fresh Postgres database and exports
    # DATABASE_URL, which build_dependencies() reads (store.database_url_env) to
    # run migrations and wire the store — a throwaway database, never production.
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    import shutil
    dst = tmp_path / "config.toml"
    shutil.copy(ROOT / "config.example.toml", dst)
    # config.example.toml's identity_file ("identity.toml") resolves relative to
    # the config file's own directory (nora/config.py's path-resolution context);
    # build_dependencies() calls load_persons() against it, so it must exist.
    (tmp_path / "identity.toml").write_text(
        '[persons.miles]\nname = "Miles"\n[[persons.miles.signal]]\nphone = "+852111"\n'
    )
    # policy.toml resolves the same way. `persons = "*"` keeps these wiring
    # tests about the dependency graph rather than the allowlist.
    (tmp_path / "policy.toml").write_text(
        'default = "deny"\n[[conversations]]\nplatform = "signal"\n'
        'id = "g1"\npersons = "*"\n'
    )
    # store.data_dir is overridden to tmp_path so media custody lands in a
    # throwaway directory; the record store's own database is the throwaway
    # `pg_url` (via DATABASE_URL), never production.
    #
    # platforms.signal.account overrides the empty "derive from the daemon" default
    # (config.example.toml) with a fixed value: derive_account() returns an override
    # immediately, with no network call, so build_dependencies() never needs a real
    # (or faked) signal-cli daemon reachable at the example config's placeholder URL.
    #
    # doctrine.dir ("nora/doctrine", config.example.toml) resolves relative to the
    # copied config file's directory (tmp_path here), which has no such subdirectory
    # — point it at the real, already-valid doctrine files instead of copying them.
    #
    # `dreamer.dir` resolves the same way and for the same reason - the Dreamer's
    # instruction corpus is validated at STARTUP (a malformed objective must not
    # become a silent Dreamer three hours later), so an unpointed `[dreamer]`
    # section makes every wiring test here fail on a missing directory rather
    # than on the thing it is testing.
    return override_config(load(dst), {
        "store.data_dir": tmp_path,
        "platforms.signal.account": "+85290000100",
        "doctrine.dir": ROOT / "nora" / "doctrine",
        "dreamer.dir": ROOT / "nora" / "dreamer",
    })


async def test_build_dependencies_returns_the_wired_graph(cfg, tmp_path):
    deps = await build_dependencies(cfg)
    assert isinstance(deps, Dependencies)
    assert deps.audit is not None
    assert deps.memory is not None
    assert deps.persons is not None
    assert deps.metrics is not None
    assert deps.policy is not None
    assert deps.assembler is not None
    assert deps.ingest is not None
    assert deps.engagement is not None
    assert deps.dispatcher is not None


async def test_the_trigger_phrase_engages_by_default(cfg):
    """`[engagement.trigger].enabled` defaults to True — the trigger phrase
    already engaged her on every deployment before this flag existed, so the
    wired `CompositePolicy` must still include `TriggerTextPolicy` when the
    key is left at its default."""
    deps = await build_dependencies(cfg)
    msg = NormalizedMessage(
        platform="signal", conversation_id="g1", conversation_type="group",
        sender_id="+852111", sender_name="Miles", timestamp=1,
        body="nora.000 you around?", raw={},
    )

    decision = await deps.policy.should_engage(message=msg)

    assert decision.engage is True
    assert decision.reason == "trigger_text"


async def test_disabling_the_trigger_removes_only_that_path(cfg):
    """Turning the flag off narrows `CompositePolicy` down to `MentionPolicy` +
    `ReplyToNoraPolicy` — the trigger PHRASE alone stops matching, which is
    the only property this test needs to pin (the two structural policies
    have their own coverage in `tests/unit/test_ingest_policy.py`)."""
    disabled = override_config(cfg, {"engagement.trigger.enabled": False})
    deps = await build_dependencies(disabled)
    msg = NormalizedMessage(
        platform="signal", conversation_id="g1", conversation_type="group",
        sender_id="+852111", sender_name="Miles", timestamp=1,
        body="nora.000 you around?", raw={},
    )

    decision = await deps.policy.should_engage(message=msg)

    assert decision.engage is False
    assert decision.reason == "no_trigger"


async def test_dependencies_exposes_the_reminders_store(cfg):
    """The web API's status route (nora/web/status.py) needs to read pending
    reminders, and the only reminders store this process builds lives inside
    build_dependencies() as a local — this pins it onto Dependencies so
    anything outside composition.py can reach it."""
    from nora.infra.sql.reminders import SqlReminderStore

    deps = await build_dependencies(cfg)
    assert isinstance(deps.reminders, SqlReminderStore)


async def test_dependencies_games_client_is_none_without_pulse_api_token(cfg, monkeypatch):
    """`nora.web.games`'s `PulseGamesClient` is a separate, REST credential
    (`PULSE_API_TOKEN`) from the LLM tool-calling path's `[tools.mcp.pulse]`
    MCP mount (`PULSE_MCP_TOKEN`) — this fixture's environment sets neither,
    so the web layer must degrade to `None` (a 503), never a guessed client
    pointed at nothing."""
    monkeypatch.delenv("PULSE_API_TOKEN", raising=False)
    deps = await build_dependencies(cfg)
    assert deps.games_client is None


async def test_dependencies_builds_a_games_client_when_pulse_api_token_is_set(cfg, monkeypatch):
    monkeypatch.setenv("PULSE_API_TOKEN", "test-pulse-token")
    deps = await build_dependencies(cfg)
    assert deps.games_client is not None


async def test_the_doctrine_the_root_builds_carries_the_generated_capability(cfg):
    """DEC-29: the composition root is where the registry meets the doctrine.

    Everything else about the capability section is testable in isolation and
    means nothing if this wiring is missing — the store would assemble four
    sections, the prompt would say nothing about what she can do, and every
    unit test would still pass. So: the REAL graph, the REAL registry, and the
    assembled block asserted to name every registered tool.
    """
    from nora.agent.registry import ToolRegistry

    deps = await build_dependencies(cfg)
    text = deps.doctrine.doctrine_text
    assert '<section kind="capability" name="capability"' in text
    for name in ToolRegistry().tool_names():
        assert f"- {name} — " in text, f"{name} is registered but not in the doctrine"


async def test_the_runtime_context_carries_the_configured_language_tag(cfg):
    """The composition root must hand the loader `doctrine.language_hint`, or
    every tag's runtime_context ships a blank `language:` line — the model is
    then never told, from outside, what to speak. This was a live defect: the
    DoctrineStore had the parameter and defaulted it to "", and the root never
    passed it. The value is a BCP 47 tag (RFC 5646), asserted here against the
    real assembled runtime_context. Drop `language_hint=` from the root and the
    line reads `language:` with nothing after it, and this goes red.
    """
    deps = await build_dependencies(cfg)
    assert "language: yue-Hant-HK" in deps.doctrine.runtime_context
    assert "language: \n" not in deps.doctrine.runtime_context


async def test_the_root_hands_the_loader_the_tool_names_its_gate_needs(cfg, tmp_path):
    """The catalogue gate's backticked half is inert without them, and an inert
    gate that nobody notices is worse than no gate.

    It has to be a tool whose name is an ordinary English word — `now` or
    `about`. Every other native is snake_case, which the STRUCTURAL detector
    catches on its own, so injecting one of those would make this pass whether
    or not `tool_names` was ever handed over. This used to inject `recall`;
    DEC-39 renamed that tool to `about`, which is still in the same class.
    """
    import shutil


    d = tmp_path / "doctrine"
    shutil.copytree(ROOT / "nora" / "doctrine", d)
    conduct = d / "conduct.md"
    conduct.write_text(conduct.read_text().replace(
        "# Honesty", "Reach for `about` when you need to.\n\n# Honesty", 1))
    with pytest.raises(StartupError, match="may not name a tool"):
        await build_dependencies(override_config(cfg, {"doctrine.dir": d}))


async def test_the_nora_aipeer_id_is_reserved_and_no_human_may_hold_it(cfg):
    """Honcho v2 corpus-safety (council 2026-08-07): the aiPeer id is Nora's
    self-model peer. A human mapped to it would feed under it and merge
    IRREVERSIBLY into her card (Honcho keeps conclusions, not text). Setting
    `memory.ai_peer` to an existing person id ("miles" in this fixture) must
    refuse to start, not silently corrupt the corpus."""
    with pytest.raises(StartupError, match="reserved for Nora"):
        await build_dependencies(override_config(cfg, {"memory.ai_peer": "miles"}))


async def test_no_enabled_platform_refuses_to_start(cfg):
    """`platforms.signal.enabled = false` is honoured now (nora.platform.registry
    .AdapterRegistry) — previously (config.py's "NOT YET HONOURED" comment)
    SignalAdapter started regardless. With no platform enabled there is nothing
    for the engagement pipeline to send through, so this is a startup failure,
    not a degraded start."""
    disabled = override_config(cfg, {"platforms.signal.enabled": False})
    with pytest.raises(StartupError, match="no platform adapter is enabled"):
        await build_dependencies(disabled)


def test_build_dependencies_never_wires_the_real_database(cfg, tmp_path, pg_url):
    """Regression guard: a fixture that forgot to point the store at a throwaway
    database would wire a real SqlRecordStore against production. The store's URL
    comes from DATABASE_URL, which `pg_url` set to an ephemeral `t_…` database;
    the media root is under tmp_path. Both being throwaways is what proves it."""
    assert os.environ["DATABASE_URL"] == pg_url
    assert "/t_" in pg_url                          # the ephemeral-database marker
    assert cfg.store.data_dir == tmp_path


async def test_dependencies_do_not_expose_settings(cfg):
    """A stage must never be able to reach the whole config through its deps —
    that is how a god object grows back."""
    deps = await build_dependencies(cfg)
    assert not hasattr(deps, "config")
    assert not hasattr(deps, "settings")


async def test_media_custody_is_off_when_the_key_env_is_unset(cfg):
    """config.example.toml ships `[media] enabled = true`, but the key env var
    is not set in this fixture — so custody degrades to OFF (never a crash), and
    the ingest pipeline is wired with no source and no store. That is the one
    state `nora.ingest.stages.media` counts as `not_configured`."""
    deps = await build_dependencies(cfg)
    assert deps.ingest._media_sources is None
    assert deps.ingest._blobs is None


async def test_a_valid_key_turns_media_custody_on_with_an_encrypting_store(cfg, monkeypatch):
    """A base64 32-byte key present in the named env var is what wires the
    MediaSourceRegistry (Signal + Discord) + the encrypting FilesystemBlobStore
    into the pipeline."""
    monkeypatch.setenv("NORA_MEDIA_KEY", base64.b64encode(os.urandom(32)).decode())
    deps = await build_dependencies(cfg)
    assert isinstance(deps.ingest._media_sources, MediaSourceRegistry)
    assert isinstance(deps.ingest._media_sources.for_platform("signal"), SignalMediaSource)
    assert isinstance(deps.ingest._media_sources.for_platform("discord"), DiscordMediaSource)
    assert isinstance(deps.ingest._blobs, FilesystemBlobStore)


async def test_a_malformed_key_degrades_to_off_rather_than_crashing(cfg, monkeypatch):
    """The deliberate asymmetry with the LLM key: media is a bonus, so a bad key
    is custody OFF (warned), never a startup failure and never plaintext."""
    monkeypatch.setenv("NORA_MEDIA_KEY", "not-a-valid-base64-32-byte-key!!")
    deps = await build_dependencies(cfg)
    assert deps.ingest._media_sources is None
    assert deps.ingest._blobs is None


async def test_media_disabled_is_off_even_with_a_valid_key(cfg, monkeypatch):
    monkeypatch.setenv("NORA_MEDIA_KEY", base64.b64encode(os.urandom(32)).decode())
    deps = await build_dependencies(override_config(cfg, {"media.enabled": False}))
    assert deps.ingest._media_sources is None
    assert deps.ingest._blobs is None


async def test_the_heartbeat_is_wired_to_the_real_fire_path(cfg):
    """The tick's `fire` collaborator is a `FirePath`, not a stub.

    This replaces the startup refusal that guarded the stub while it existed.
    The refusal was a statement about the stub; this is a statement about the
    feature, and it is the assertion the whole wiring step needs: `HeartbeatLoop`
    takes `fire` as a collaborator and calls whatever it is handed, so a stub
    left in place claims every due row, delivers nothing, and resolves it — with
    every unit test green, because each one supplies its own fake `fire`.

    Reaching into `background._tasks` and `_fire` is the only way to see it from
    out here: `Dependencies` deliberately exposes no heartbeat, and widening it
    to make one test easier is exactly what its docstring refuses.
    """
    on = override_config(cfg, {"heartbeat.enabled": True})
    deps = await build_dependencies(on)
    try:
        loops = [
            task.get_coro().cr_frame.f_locals["self"]
            for task in deps.background._tasks
            if task.get_coro().__qualname__ == "HeartbeatLoop.run"
        ]
        assert loops, "the heartbeat loop was not spawned"
        assert isinstance(loops[0]._fire.__self__, FirePath)
    finally:
        await deps.background.cancel_all()


async def test_an_unstamped_build_spawns_no_announcer(cfg, no_build_stamp):
    """The gate that keeps a dev checkout (and any image not built through
    `scripts/build-image.sh`) quiet — DEC-79.

    `[version] announce` defaults TRUE, so this is the only thing standing
    between "no stamp" and a boot-time announcement of a version nobody knows.
    The composition root checks the manifest itself rather than spawning an
    announcer that would immediately answer `unavailable`: there is nothing to
    announce, and the warning belongs at boot, once, where an operator reads it.

    Reaching into `background._tasks` for the same reason
    `test_the_heartbeat_is_wired_to_the_real_fire_path` does: `Dependencies`
    deliberately exposes neither loop.

    The absent stamp is FORCED, not assumed. This passed for as long as nobody
    had ever run a real build, and went red the first time one did (V1,
    2026-08-11): `scripts/build-image.sh` writes `nora/version.json` into the
    working tree, it is gitignored so it survives unnoticed, and this test then
    exercised the STAMPED path while claiming to prove the unstamped one. A test
    whose meaning depends on ambient filesystem state proves whatever the
    machine happens to be in the mood for.
    """
    deps = await build_dependencies(cfg)
    try:
        assert not _spawned(deps, "VersionAnnouncer.run")
    finally:
        await deps.background.cancel_all()


async def test_a_stamped_build_announces_into_every_allowed_room(cfg, tmp_path, monkeypatch):
    """The other half: a stamped build DOES wire the announcer, at the rooms
    `policy.toml` allows — not a second list in config.toml (DEC-8).

    Without this the test above passes against a feature that never wires at
    all, which is the exact shape of a capability that ships inert by accident
    (DEC-42's `send_reply` cap, DEC-50's `skills` budget).
    """
    stamp = tmp_path / "version.json"
    stamp.write_text(json.dumps({
        "version": "V3",
        "versions": [{"version": "V3", "notes": "She can say her own version."}],
    }))
    monkeypatch.setattr("nora.infra.version.DEFAULT_MANIFEST_PATH", stamp)

    deps = await build_dependencies(cfg)
    try:
        announcers = _spawned(deps, "VersionAnnouncer.run")
        assert announcers, "the version announcer was not spawned"
        announcer = announcers[0]
        # Bound to the REAL fire turn, not a stub — the failure
        # `test_the_heartbeat_is_wired_to_the_real_fire_path` exists for, on the
        # second occasion.
        assert isinstance(announcer._speak.__self__, FirePath)
        assert [t.conversation_id for t in announcer._targets] == [
            conv.primary_id for conv in load_policy(cfg.policy_file).conversations
            if conv.platform == "signal"
        ]
    finally:
        await deps.background.cancel_all()


def _spawned(deps, qualname: str) -> list:
    """Every live background task running `qualname`, as its `self`."""
    return [
        task.get_coro().cr_frame.f_locals["self"]
        for task in deps.background._tasks
        if task.get_coro().__qualname__ == qualname
    ]


async def test_remind_writes_a_real_row_through_the_registry_the_root_builds(cfg, pg_url):
    """The live `ToolRegistry` gets the reminders store, and `[heartbeat]` being
    off does not change that.

    Without `reminders=` on that constructor, `remind` answers `unavailable` in
    production while every test in `tests/test_reminder_tools.py` passes — each
    builds its OWN registry around the fake, so none of them can see the wiring.
    That is the whole failure mode this test exists for, which is why it goes all
    the way to a row in the database rather than asserting on an attribute: a
    store held but never reached would satisfy the weaker check.

    `cfg.heartbeat.enabled` is False here (the shipped default). The tick is
    gated; the TOOLS are not — the model may set, list and cancel reminders on a
    deployment that never fires them, and the rows wait for the flag.
    """
    from nora.agent.room import Room
    from nora.domain.models import PersonRef
    from nora.domain.reminders import ReminderCaps, ReminderState
    from nora.infra.sql.engine import create_sql_engine
    from nora.infra.sql.reminders import SqlReminderStore

    assert cfg.heartbeat.enabled is False
    deps = await build_dependencies(cfg)
    try:
        registry = deps.engagement._tool_registry
        # §9's caps came from `[reminders]`, converted at the composition root —
        # `nora.agent` may not import `nora.config`, so a caps object that
        # silently fell back to its defaults would ignore every operator value.
        assert registry._caps == ReminderCaps(**cfg.reminders.model_dump())

        miles = PersonRef(person_id="miles", name="Miles", honcho_peer_id="miles")
        specs = registry.specs(
            store=deps.audit, conversation_id="g1", conversation_type="group",
            persons=deps.persons, honcho=deps.memory, deliverer=None,
            metrics=deps.metrics,
            room=Room(platform="signal", conversation_id="g1", roster_source=None,
                      persons=deps.persons, tagger=miles),
        )
        remind = {spec.name: spec.call for spec in specs}["remind"]
        result = await remind(
            when=(datetime.now(UTC) + timedelta(hours=1)).isoformat(), text="洗衫",
        )
        assert result["status"] == "ok", result
    finally:
        await deps.background.cancel_all()

    check = SqlReminderStore(create_sql_engine(pg_url))
    try:
        rows = await check.all_pending(limit=10)
        assert [r.text for r in rows] == ["洗衫"]
        assert rows[0].requester_person_id == "miles"
        assert rows[0].conversation_id == "g1"
        assert rows[0].state is ReminderState.PENDING
    finally:
        await check.aclose()


async def test_boot_recovery_counts_the_rows_it_swept(cfg, pg_url):
    """The sweep is the only path that resolves reminders with no tick running,
    and `nora_reminders_fired_total`'s spec says a reminder that vanishes must
    never vanish from the metrics too. It logged a count and incremented
    nothing, so a crash that ate reminders left no trace on any dashboard.

    This asserts the WIRING, with a real stranded row in the real database — the
    counting function itself is unit-tested; a function nobody calls would pass
    that test and leave this defect exactly where it was.
    """
    from nora.domain.reminders import Reminder, ReminderState
    from nora.infra.sql import create_sql_engine, run_migrations
    from nora.infra.sql.reminders import SqlReminderStore

    run_migrations(pg_url)
    store = SqlReminderStore(create_sql_engine(pg_url))
    now = datetime.now(UTC)
    await store.create(Reminder(
        id=uuid.uuid4(), requester_person_id="miles", platform="signal",
        conversation_id="g1", conversation_type="group", text="洗衫",
        created_at=now - timedelta(hours=1), due_at=now - timedelta(minutes=5),
        state=ReminderState.FIRING, fired_at=None, outcome=None,
    ))
    await store.aclose()

    deps = await build_dependencies(cfg)

    assert deps.metrics.get(
        "nora_reminders_fired_total", outcome="failed_error",
    ) == 1


async def test_the_liveness_gauge_is_seeded_even_with_the_heartbeat_off(cfg):
    """`[heartbeat] enabled = false` SHIPS, and the gauge's callback was bound
    inside `HeartbeatLoop.__init__` — so on every default deployment the series
    exported `0.0` forever, which reads exactly like "ticked a moment ago". The
    one reading an operator must be able to trust is the one it could not give.
    """
    deps = await build_dependencies(cfg)
    assert cfg.heartbeat.enabled is False
    assert deps.metrics.get(
        "nora_heartbeat_last_tick_age_seconds",
    ) == HEARTBEAT_NOT_RUNNING


async def test_compute_boot_recovery_sweeps_a_stranded_row_even_when_disabled(cfg, pg_url):
    """Mirrors `test_boot_recovery_counts_the_rows_it_swept`: a `FIRING`
    `ComputeJob` (a crash mid-delivery) must resolve to `FAILED` at boot
    whether or not `[compute] enabled` is true — the whole point of the
    unconditional `compute_poller_recover` call in `build_dependencies`
    (composition.py, beside `reminders`' own unconditional sweep). An earlier
    draft gated this call behind `cfg.compute.enabled`, which would leave a
    row stranded forever on exactly the deployment that looks safest: one
    that crashed with compute on and was then switched off, unrelated to the
    crash. `cfg.compute.enabled` is False here (the shipped default) — the
    sweep must still run.
    """
    from nora.domain.compute import ComputeJob, ComputeJobState
    from nora.infra.sql import create_sql_engine, run_migrations
    from nora.infra.sql.compute_jobs import SqlComputeJobStore

    assert cfg.compute.enabled is False

    run_migrations(pg_url)
    store = SqlComputeJobStore(create_sql_engine(pg_url))
    now = datetime.now(UTC)
    await store.create(ComputeJob(
        id=uuid.uuid4(), backend_job_id="job_stranded", task="make a chart",
        artifact_id=None, platform="signal", conversation_id="g1",
        conversation_type="group", requester_person_id="miles",
        created_at=now - timedelta(hours=1), state=ComputeJobState.FIRING,
        completed_at=None, result_url=None, result_summary=None, error=None,
        task_sha256="hash-task", context_sha256="hash-context",
    ))
    await store.aclose()

    deps = await build_dependencies(cfg)

    assert deps.metrics.get(
        "nora_compute_jobs_delivered_total", outcome="failed_error",
    ) == 1


def _compute_environment(monkeypatch, cfg) -> Path:
    """Every host-injected value `[compute] enabled = true` refuses to start
    without, set to a plausible value. Returns the host data root, which is the
    one of them a test may want to assert against.

    Reads the variable NAMES off `cfg` rather than hardcoding them, for the
    reason DEC-8 gives: config names the variable, the host holds the value, and
    a test that hardcodes the name passes after a rename that would break the
    deployment."""
    host_root = Path("/srv/nora/data")
    monkeypatch.setenv(cfg.compute.data_host_root_env, str(host_root))
    monkeypatch.setenv(cfg.shortener.token_env, "test-drop-token")
    monkeypatch.setenv(cfg.route.api_key_env, "test-maps-key")
    return host_root


@pytest.mark.parametrize(
    "unset",
    ["data_host_root_env", "shortener_token_env", "route_api_key_env"],
)
async def test_compute_refuses_to_start_without_its_host_injected_values(
    cfg, monkeypatch, unset,
):
    """`policy.toml`'s discipline (R-5: refuse to start rather than run in a
    state that looks like working), applied to `[compute]`.

    Every one of these is read with a `""` default, so without the check the
    daemon starts CLEANLY and then fails every job minutes later — an empty
    bearer token on the Drop mint, an empty key handed to Smith, or a
    workspace bind-mounted from a host path that does not exist. All three
    surface in the group as «it failed», with an error string nobody can act
    on, and none of them are things a group can fix. They are operator
    problems and they belong in the operator's terminal.

    Parametrized over each one INDIVIDUALLY: a check that only fired when all
    three were missing would pass a single combined test.
    """
    on = override_config(cfg, {"compute.enabled": True, "heartbeat.enabled": False})
    _compute_environment(monkeypatch, cfg)
    monkeypatch.delenv({
        "data_host_root_env": cfg.compute.data_host_root_env,
        "shortener_token_env": cfg.shortener.token_env,
        "route_api_key_env": cfg.route.api_key_env,
    }[unset])

    with pytest.raises(StartupError):
        await build_dependencies(on)


async def test_compute_refuses_a_relative_host_root(cfg, monkeypatch):
    """`./data` is what the compose mount falls back to when nobody sets the
    variable, and it is exactly the value that must NOT reach the backend: the
    host Docker daemon resolves a bind source against a working directory this
    process knows nothing about. Unset is caught above; a relative value is the
    same defect wearing a plausible-looking string."""
    on = override_config(cfg, {"compute.enabled": True, "heartbeat.enabled": False})
    _compute_environment(monkeypatch, cfg)
    monkeypatch.setenv(cfg.compute.data_host_root_env, "./data")

    with pytest.raises(StartupError):
        await build_dependencies(on)


async def test_the_sandbox_bind_source_is_the_host_path_not_noras_own(
    cfg, monkeypatch, tmp_path, no_build_stamp,
):
    """The wiring half of the two-roots rule (`LocalDockerBackend.__init__`).

    Nora's own `store.data_dir` is `/app/data` inside her container, and that
    is the right root for the `task.txt` SHE writes. It is the wrong root for
    the bind-mount source she hands the host Docker daemon, which resolves it
    on the host filesystem — where `/app/data` does not exist, so the daemon
    creates an empty directory and every job silently produces nothing.

    Asserted as a PAIR: same trailing directory name, different roots. Wiring
    that passed `data_dir` to both (the shipped defect) fails the second
    assertion; wiring that appended a different subdirectory to each fails the
    first.
    """
    on = override_config(cfg, {"compute.enabled": True, "heartbeat.enabled": False})
    host_root = _compute_environment(monkeypatch, cfg)
    deps = await build_dependencies(on)
    try:
        backend = _spawned(deps, "ComputeJobPoller.run")[0]._backend
        assert backend._workspace_root == cfg.store.data_dir / "compute-workspaces"
        assert backend._workspace_host_root == host_root / "compute-workspaces"
        assert backend._workspace_host_root != backend._workspace_root
    finally:
        await deps.background.cancel_all()


async def test_compute_only_deployment_still_gets_a_live_fire_path(
    cfg, monkeypatch, no_build_stamp,
):
    """The widened `fire_path` gate: `[compute] enabled` alone — heartbeat
    off, no version announcement (`no_build_stamp` forces the unstamped path,
    the same way `test_an_unstamped_build_spawns_no_announcer` does) — must
    still construct a real `FirePath` and spawn `ComputeJobPoller`.

    Before the fix, `fire_path` was built only under `cfg.heartbeat.enabled or
    announcing`; a compute-only deployment (a real, plausible configuration —
    "I just want make_artifact") would build a live `compute_backend`, accept
    `make_artifact` slow-path jobs, and then never spawn a poller to deliver
    them — every such job would sit `pending` forever with no path to ever
    reach the group that asked for it. Reaching into `background._tasks` for
    the same reason `test_the_heartbeat_is_wired_to_the_real_fire_path` does:
    `Dependencies` deliberately exposes neither loop.
    """
    on = override_config(cfg, {
        "compute.enabled": True,
        "heartbeat.enabled": False,
    })
    assert on.version.announce is True  # the shipped default; no_build_stamp is what mutes it
    _compute_environment(monkeypatch, cfg)
    deps = await build_dependencies(on)
    try:
        pollers = _spawned(deps, "ComputeJobPoller.run")
        assert pollers, "the compute job poller was not spawned"
        assert isinstance(pollers[0]._speak.__self__, FirePath)
    finally:
        await deps.background.cancel_all()


def _resolved_module_name(rel: Path) -> str:
    """The dotted module name `nora/<rel>` would have at import time, e.g.
    `infra/sql/migrations_runner.py` -> `nora.infra.sql.migrations_runner`."""
    parts = rel.with_suffix("").parts
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(("nora", *parts))


def _resolve_import_from(module_name: str, node: ast.ImportFrom) -> str:
    """Resolve an `ast.ImportFrom` node's target to a fully-qualified dotted name,
    following Python's own relative-import rule so `from ..config import X` inside
    a deeply nested module resolves to the package it actually names — not to
    whatever text happens to follow the dots."""
    if node.level == 0:
        return node.module or ""
    # node.level dots: 1 means "this module's own package"; each extra dot climbs
    # one more level. A non-package module's own package is its dotted name minus
    # its last component.
    package_parts = module_name.split(".")[:-1]
    base_parts = package_parts[: len(package_parts) - (node.level - 1)]
    if node.module:
        base_parts = [*base_parts, node.module]
    return ".".join(base_parts)


def test_only_app_and_main_import_config():
    """Enforced properly by import-linter too (contract 3); asserted here as an
    AST-level check so the rule also has a home in the suite, with no allowlist."""
    offenders = []
    for p in (ROOT / "nora").rglob("*.py"):
        rel = p.relative_to(ROOT / "nora")
        # `app/`, `presence/` and `analytics/` are all composition roots — the
        # three packages allowed to read Config (import-linter contract 3
        # lists none as forbidden), same standing as `__main__.py`/`config.py`
        # themselves.
        if rel.parts[0] in ("app", "presence", "analytics") or str(rel) in (
            "__main__.py", "config.py",
        ):
            continue
        module_name = _resolved_module_name(rel)
        tree = ast.parse(p.read_text())
        for n in ast.walk(tree):
            if not isinstance(n, ast.ImportFrom):
                continue
            resolved = _resolve_import_from(module_name, n)
            if resolved == "nora.config" or resolved.startswith("nora.config."):
                offenders.append(str(rel))
    assert not offenders, f"config imported outside app/ and __main__: {offenders}"
