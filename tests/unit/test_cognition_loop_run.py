"""`scripts.cognition_loop.__main__.run` — the run-level seams: the
config under test is materialised from the deployment's `--config`, and
the final status folds the cases' own verdicts, never a collapsed
"done". Offline: every collaborator is monkeypatched.
"""

from __future__ import annotations

import asyncio
from typing import Any

from scripts.cognition_loop.cases import generated_case, write_case

NOW = 1_800_000_000_000


def test_run_materialises_the_config_and_folds_the_status(
    monkeypatch, tmp_path,
):
    import sys
    import types
    from contextlib import contextmanager
    from types import SimpleNamespace

    import scripts.cognition_loop.__main__ as cli

    case_file = tmp_path / "case.json"
    write_case(generated_case(), case_file)
    config_file = tmp_path / "config.toml"
    config_file.write_text("x = 1\n")
    materialised: dict[str, Any] = {}

    def fake_materialise(**kwargs):
        materialised.update(kwargs)
        out = tmp_path / "config.evaluation.toml"
        out.write_text("x = 1\n")
        return out

    monkeypatch.setattr(cli, "materialise", fake_materialise)
    monkeypatch.setattr(cli, "load", lambda path: SimpleNamespace(
        store=SimpleNamespace(database_url_env="E"),
        honcho=SimpleNamespace(workspace_id="deploy-ws"),
    ))
    monkeypatch.setattr(cli, "snapshot_only", lambda *a, **k: None)
    monkeypatch.setattr(cli, "private_directory", lambda p: p)
    monkeypatch.setattr(cli, "Judges", lambda *a, **k: SimpleNamespace())
    monkeypatch.setattr(cli, "evaluation_export_for", lambda p: None)
    monkeypatch.setattr(cli, "load_policy", lambda p, **k: SimpleNamespace())
    monkeypatch.setattr(cli, "read_events", lambda p: [])
    monkeypatch.setattr(cli, "validate_events", lambda e: {"status": "PASS"})
    monkeypatch.setattr(
        cli, "create_sql_engine",
        lambda url: SimpleNamespace(dispose=_async_noop()))
    monkeypatch.setitem(
        sys.modules, "nora.secret",
        SimpleNamespace(credentials=lambda: {"E": "cred"}))
    backbone = types.ModuleType("evals.backbone")
    backbone._prefill_capture = lambda: None
    monkeypatch.setitem(sys.modules, "evals.backbone", backbone)

    @contextmanager
    def _noop_ctx(*a, **k):
        yield

    class _Evidence:
        def __init__(self, **kwargs):
            self.path = tmp_path / "events.jsonl"
            self.path.write_text("")

        capture = staticmethod(_noop_ctx)

    monkeypatch.setattr(cli, "RunEvidence", _Evidence)
    monkeypatch.setattr(cli, "measured_run_budget",
                        lambda *a, **k: _noop_ctx())
    monkeypatch.setattr(cli, "run_case", _async_result({"status": "FAIL"}))

    args = SimpleNamespace(
        case=case_file, case_dir=None, smoke=False,
        per_wake_tokens=1, token_budget=10,
        config=config_file, policy=None, identity=None,
        database_url="postgresql+asyncpg://x", snapshot=True,
        output=tmp_path, judge_a="a", judge_b="b",
        inspect_only=False, overrides={},
    )
    report = asyncio.run(cli.run(args))
    # A different --config is a different subject: it is what gets
    # materialised into the config every case composes.
    assert materialised["deployment"] == config_file
    assert materialised["target"].name == "config.evaluation.toml"
    # The case's own verdict reaches the report unchanged — never "done".
    assert report["status"] == "FAIL"


def _async_noop():
    async def _noop(*a, **k):
        return None
    return _noop


def _async_result(value):
    async def _call(*a, **k):
        return value
    return _call


# --------------------------------------------------------------------------
# the wake's steps name themselves on ERROR
# --------------------------------------------------------------------------


def _wake_args(tmp_path):
    from types import SimpleNamespace

    policy = tmp_path / "policy.toml"
    policy.write_text("x = 1\n")
    identity = tmp_path / "identity.toml"
    identity.write_text("x = 1\n")
    (tmp_path / "events.jsonl").write_text("")
    return SimpleNamespace(
        workdir=str(tmp_path), overrides={}, channel_directory=None,
        judge_a="a", judge_b="b", policy=policy, identity=identity,
        database_url="postgresql+asyncpg://x",
    ), SimpleNamespace(path=tmp_path / "events.jsonl")


def _runner_subject(inspection=None, outcome=None):
    from types import SimpleNamespace

    class _Runner:
        async def inspect(self, **k):
            return inspection

        async def on_wake(self, **k):
            return outcome

        @property
        def wake_hook(self):
            return self.on_wake

    return SimpleNamespace(deps=SimpleNamespace(
        cognition_runner=_Runner(),
        wake_evaluator=SimpleNamespace(_policy=SimpleNamespace(
            batch_max=5, session_timeout_seconds=10)),
        metrics=SimpleNamespace(get=lambda *a, **k: 0),
        config_reload=SimpleNamespace(policy=SimpleNamespace(rooms=lambda: [])),
        persons=SimpleNamespace(by_person={}),
    ))


def _wake_seam(monkeypatch, tmp_path, subject):
    """Everything `_run_wake` touches after compose, stubbed."""
    from types import SimpleNamespace

    from nora.domain.stimuli import Stimulus, UnitView
    from scripts.cognition_loop import run as run_mod

    async def _composed(*a, **k):
        return subject

    async def _noop(*a, **k):
        return None

    async def _wake_record(*a, **k):
        return SimpleNamespace(wake_id=42, forced=False)

    async def _view(*a, **k):
        return UnitView(unit="u", pending=(),
                        last_activity_ms=None, last_wake=None)

    async def _by_id(engine, unit, ids):
        return {
            i: Stimulus(id=i, unit=unit, kind="message", item="message",
                        ref=f"messages:{i}", at_ms=NOW, recorded_at_ms=NOW)
            for i in ids
        }

    async def _state(*a, **k):
        return None

    monkeypatch.setattr(run_mod, "_compose", _composed)
    monkeypatch.setattr(run_mod, "assert_no_egress", lambda s: {})
    monkeypatch.setattr(run_mod, "index_replay_sandbox", _noop)
    monkeypatch.setattr(run_mod, "_close_subject", _noop)
    monkeypatch.setattr(run_mod, "create_async_engine",
                        lambda url: SimpleNamespace(dispose=_noop))
    monkeypatch.setattr(
        run_mod, "SqlStimulusStore",
        lambda engine: SimpleNamespace(view=_view))
    monkeypatch.setattr(run_mod, "_stimuli_by_id", _by_id)
    monkeypatch.setattr(run_mod, "decide", lambda *a, **k: None)
    monkeypatch.setattr(
        run_mod, "eval_verdict",
        lambda *a, **k: (SimpleNamespace(reason="time"), False))
    monkeypatch.setattr(run_mod, "record_eval_wake", _wake_record)
    monkeypatch.setattr(run_mod, "state_mod", SimpleNamespace(
        snapshot=_state, diff=lambda a, b: SimpleNamespace(kinds={})))
    monkeypatch.setattr(run_mod, "read_events", lambda p: [])
    monkeypatch.setattr(run_mod, "session_events", lambda e, **k: [])
    monkeypatch.setattr(run_mod, "wake_trajectory",
                        lambda e, **k: {"blocks": dict.fromkeys(
                            ("system", "instructions", "agenda",
                             "items", "stimuli"))})
    return run_mod


async def test_inspect_only_records_the_assembled_blocks(
    monkeypatch, tmp_path,
):
    """A dry run still shows what Nora would have read: the blocks the
    shared `_session_input` assembled, not a reconstructed copy."""
    import json
    from types import SimpleNamespace

    inspection = {
        "system": "SYSTEM PROMPT", "agenda": {"agenda": True},
        "items": [{"id": "i1"}], "stimuli": [{"id": 3}],
        "instructions": {"doctrine_sha256": "d", "skill_versions": {}},
    }
    run_mod = _wake_seam(monkeypatch, tmp_path,
                         _runner_subject(inspection=inspection))
    args, evidence = _wake_args(tmp_path)
    case = SimpleNamespace(unit="u", expectations=())
    wake = await run_mod._run_wake(
        args, case, 0, 11, NOW, "postgresql+asyncpg://x", "time", None,
        evidence, tmp_path, SimpleNamespace(platform="signal",
                                            primary_id="r"),
        "nora-eval-run-x")
    assert wake["trajectory"]["blocks"]["system"] == "SYSTEM PROMPT"
    assert wake["trajectory"]["blocks"]["items"] == [{"id": "i1"}]
    assert wake["trajectory"]["blocks"]["stimuli"] == [{"id": 3}]
    assert wake["trajectory"]["blocks"]["instructions"]["doctrine_sha256"] == "d"
    frozen = json.loads((tmp_path / "wake-0-11.json").read_text())
    assert frozen["trajectory"]["blocks"]["agenda"] == {"agenda": True}


async def test_a_compose_failure_names_compose(monkeypatch, tmp_path):
    import json
    from types import SimpleNamespace

    from scripts.cognition_loop import run as run_mod

    async def _boom(*a, **k):
        raise ValueError("agenda room is outside the unit")

    monkeypatch.setattr(run_mod, "_compose", _boom)
    monkeypatch.setattr(run_mod, "_close_subject", _async_result_fn())
    args, evidence = _wake_args(tmp_path)
    wake = await run_mod._run_wake(
        args, SimpleNamespace(unit="u", expectations=()), 1, 7, NOW,
        "postgresql+asyncpg://x", "time", None, evidence, tmp_path,
        SimpleNamespace(platform="signal", primary_id="r"),
        "nora-eval-run-x")
    assert wake["status"] == "ERROR" and wake["step"] == "compose"
    assert "agenda room" in wake["error"]
    frozen = json.loads((tmp_path / "wake-1-7.json").read_text())
    assert frozen["error"]["step"] == "compose"


async def test_a_wake_failure_names_wake_not_compose(monkeypatch, tmp_path):
    from types import SimpleNamespace

    run_mod = _wake_seam(monkeypatch, tmp_path, _runner_subject())

    async def _boom(*a, **k):
        raise RuntimeError("view read failed")

    monkeypatch.setattr(
        run_mod, "SqlStimulusStore",
        lambda engine: SimpleNamespace(view=_boom))
    args, evidence = _wake_args(tmp_path)
    wake = await run_mod._run_wake(
        args, SimpleNamespace(unit="u", expectations=()), 0, 9, NOW,
        "postgresql+asyncpg://x", "time", None, evidence, tmp_path,
        SimpleNamespace(platform="signal", primary_id="r"),
        "nora-eval-run-x")
    assert wake["status"] == "ERROR" and wake["step"] == "wake"


def _async_result_fn():
    async def _noop(*a, **k):
        return None
    return _noop


# --------------------------------------------------------------------------
# no session is said; the batch shows beside production's; status is derived
# --------------------------------------------------------------------------


def _recorded_meta(stimulus_ids=(), had_session=False, self_ids=()):
    return {"stimulus_ids": list(stimulus_ids), "self_ids": list(self_ids),
            "had_session": had_session}


async def test_an_inspect_wake_with_no_session_is_error_when_production_ran(
    monkeypatch, tmp_path,
):
    """inspect-only: production ran a session, the sandbox ran none — the
    dry run cannot claim it inspected that wake."""
    import json
    from types import SimpleNamespace

    run_mod = _wake_seam(monkeypatch, tmp_path, _runner_subject())
    args, evidence = _wake_args(tmp_path)
    wake = await run_mod._run_wake(
        args, SimpleNamespace(unit="u", expectations=()), 1, 22, NOW,
        "postgresql+asyncpg://x", "time", None, evidence, tmp_path,
        SimpleNamespace(platform="signal", primary_id="r"),
        "nora-eval-run-x",
        recorded_meta=_recorded_meta((3, 5, 7), had_session=True))
    assert wake["status"] == "ERROR" and wake["step"] == "wake"
    assert wake["no_session"] == "empty agenda"
    assert wake["batch"] == {"recorded": [3, 5, 7], "offered": [3, 5, 7],
                            "recorded_self": [], "offered_self": [],
                            "production_session": True}
    assert "production" in wake["error"]
    frozen = json.loads((tmp_path / "wake-1-22.json").read_text())
    assert frozen["no_session"] == "empty agenda"
    assert frozen["error"]["step"] == "wake"
    assert frozen["batch"]["recorded"] == [3, 5, 7]


async def test_a_batch_stimulus_absent_from_the_sandbox_is_an_advance_error(
    monkeypatch, tmp_path,
):
    """A recorded batch stimulus the Record advance never delivered is
    ERROR at the advance step, named by id — never silently dropped."""
    from types import SimpleNamespace

    run_mod = _wake_seam(monkeypatch, tmp_path, _runner_subject())
    monkeypatch.setattr(run_mod, "_stimuli_by_id", _async_result({}))
    args, evidence = _wake_args(tmp_path)
    wake = await run_mod._run_wake(
        args, SimpleNamespace(unit="u", expectations=()), 2, 31, NOW,
        "postgresql+asyncpg://x", "time", None, evidence, tmp_path,
        SimpleNamespace(platform="signal", primary_id="r"),
        "nora-eval-run-x",
        recorded_meta=_recorded_meta((3, 5, 7), had_session=True))
    assert wake["status"] == "ERROR" and wake["step"] == "advance"
    assert "[3, 5, 7]" in wake["error"]
    assert wake["batch"]["recorded"] == [3, 5, 7]
    assert wake["batch"]["offered"] is None


async def test_a_no_session_wake_is_named_not_silently_passed(
    monkeypatch, tmp_path,
):
    """Production ran none either: `no_session` names why, and the
    status derives over the wake's (empty) expectations."""
    from types import SimpleNamespace

    run_mod = _wake_seam(monkeypatch, tmp_path, _runner_subject())
    args, evidence = _wake_args(tmp_path)
    wake = await run_mod._run_wake(
        args, SimpleNamespace(unit="u", expectations=()), 0, 11, NOW,
        "postgresql+asyncpg://x", "time", None, evidence, tmp_path,
        SimpleNamespace(platform="signal", primary_id="r"),
        "nora-eval-run-x",
        recorded_meta=_recorded_meta((), had_session=False))
    assert wake["status"] == "PASS"
    assert wake["no_session"] == "empty agenda"
    assert wake["batch"]["production_session"] is False


async def test_a_live_wake_reads_its_settlement_back(monkeypatch, tmp_path):
    """Live: the wake's session id and close are read back from the
    outcome row the production settle path recorded."""
    from types import SimpleNamespace

    run_mod = _wake_seam(monkeypatch, tmp_path,
                         _runner_subject(outcome=SimpleNamespace(status="ok")))
    monkeypatch.setattr(run_mod, "_wake_outcome", _async_result({
        "session_id": 9, "outcome": "closed",
        "detail": "", "cause": "none"}))
    args, evidence = _wake_args(tmp_path)
    wake = await run_mod._run_wake(
        args, SimpleNamespace(unit="u", expectations=()), 0, 11, NOW,
        "postgresql+asyncpg://x", "time", object(), evidence, tmp_path,
        SimpleNamespace(platform="signal", primary_id="r"),
        "nora-eval-run-x",
        recorded_meta=_recorded_meta((1,), had_session=True))
    assert wake["status"] == "PASS"
    assert wake["session_id"] == 9
    assert wake["no_session"] is None


async def test_a_live_wake_without_an_outcome_row_is_error(
    monkeypatch, tmp_path,
):
    """No settlement row: the lane never ran the wake — ERROR, never
    a silent empty-agenda pass."""
    from types import SimpleNamespace

    run_mod = _wake_seam(monkeypatch, tmp_path,
                         _runner_subject(outcome=SimpleNamespace(status="ok")))
    monkeypatch.setattr(run_mod, "_wake_outcome", _async_result(None))
    args, evidence = _wake_args(tmp_path)
    wake = await run_mod._run_wake(
        args, SimpleNamespace(unit="u", expectations=()), 0, 11, NOW,
        "postgresql+asyncpg://x", "time", object(), evidence, tmp_path,
        SimpleNamespace(platform="signal", primary_id="r"),
        "nora-eval-run-x",
        recorded_meta=_recorded_meta((1,), had_session=True))
    assert wake["status"] == "ERROR" and wake["step"] == "wake"


async def test_a_live_no_session_names_the_outcome_rows_detail(
    monkeypatch, tmp_path,
):
    from types import SimpleNamespace

    run_mod = _wake_seam(monkeypatch, tmp_path,
                         _runner_subject(outcome=SimpleNamespace(status="ok")))
    monkeypatch.setattr(run_mod, "_wake_outcome", _async_result({
        "session_id": None, "outcome": "closed",
        "detail": "empty agenda", "cause": "none"}))
    args, evidence = _wake_args(tmp_path)
    wake = await run_mod._run_wake(
        args, SimpleNamespace(unit="u", expectations=()), 0, 11, NOW,
        "postgresql+asyncpg://x", "time", object(), evidence, tmp_path,
        SimpleNamespace(platform="signal", primary_id="r"),
        "nora-eval-run-x",
        recorded_meta=_recorded_meta((1,), had_session=True))
    assert wake["no_session"] == "empty agenda"
    assert wake["status"] == "PASS"


def test_a_wakes_status_is_derived_fail_closed():
    from scripts.cognition_loop.run import _wake_status

    assert _wake_status(("advance", "boom"), [{"verdict": "PASS"}]) == "ERROR"
    assert _wake_status(None, [{"verdict": "ERROR"}]) == "ERROR"
    assert _wake_status(
        None, [{"verdict": "FAIL"}, {"verdict": "REVIEW"}]) == "FAIL"
    assert _wake_status(None, [{"verdict": "REVIEW"}]) == "REVIEW"
    assert _wake_status(None, []) == "PASS"


# --------------------------------------------------------------------------
# the frozen room directory
# --------------------------------------------------------------------------


async def test_the_frozen_directory_stands_in_for_the_live_reader(
    monkeypatch, tmp_path,
):
    import sys
    from types import SimpleNamespace

    from nora.app import composition_platforms
    from nora.platform.discord.channels import StaticChannelDirectory
    from scripts.cognition_loop import run as run_mod

    obtain_module = sys.modules["nora.subject.obtain"]
    directory = StaticChannelDirectory(
        channels={"g1": {"c1": "hall"}}, threads={})
    seen = {}
    real_directory_cls = composition_platforms.ClientChannelDirectory
    real_load_policy = obtain_module.load_policy

    async def _obtain(spec):
        seen["directory"] = composition_platforms.ClientChannelDirectory(
            object())
        seen["load_policy"] = obtain_module.load_policy
        return SimpleNamespace(spec=spec)

    monkeypatch.setattr(run_mod, "obtain", _obtain)
    args = SimpleNamespace(workdir=str(tmp_path), overrides={},
                           channel_directory=directory)
    room = SimpleNamespace(platform="discord", primary_id="g1/c1")
    await run_mod._compose(
        args, "postgresql+asyncpg://x", NOW, room, "policy", "identity",
        workspace="nora-eval-run-x")
    assert seen["directory"] is directory
    assert seen["load_policy"] is not real_load_policy
    # And the patch is gone after compose — never a process-wide swap.
    assert composition_platforms.ClientChannelDirectory is real_directory_cls
    assert obtain_module.load_policy is real_load_policy


async def test_an_unread_guild_is_an_error_naming_the_directory(
    monkeypatch, tmp_path,
):
    """A guild the unit expands through but the directory cannot answer for
    is ERROR at the room directory — never the silent "no policy room"."""
    from types import SimpleNamespace

    from scripts.cognition_loop import run as run_mod

    async def _recorded(source, wake_id):
        return NOW, SimpleNamespace(unit="u", pending=())

    async def _reason(source, wake_id):
        return "time"

    async def _session(source, wake_id):
        return False

    monkeypatch.setattr(run_mod, "recorded_wake", _recorded)
    monkeypatch.setattr(run_mod, "_wake_reason", _reason)
    monkeypatch.setattr(run_mod, "_recorded_session", _session)
    policy = SimpleNamespace(
        conversations=[SimpleNamespace(unit="u", guild="g1")],
        rooms=lambda: [])
    args = SimpleNamespace(run_id="r", channel_directory=None)
    case = SimpleNamespace(name="c", unit="u", wakes=(1,), expectations=())
    report = await run_mod.run_case(
        args, case, SimpleNamespace(), None, policy,
        SimpleNamespace(), tmp_path)
    assert report["status"] == "ERROR"
    assert any("room directory" in e for e in report["errors"])


async def test_the_frozen_file_loads_and_freezes_into_evidence(tmp_path):
    """`--room-directory PATH` replays a frozen copy — no live read."""
    import json
    from types import SimpleNamespace

    from scripts.cognition_loop import __main__ as cli

    frozen_file = tmp_path / "frozen.json"
    frozen_file.write_text(json.dumps({
        "channels": {"g1": {"c1": "hall"}},
        "threads": {"t1": "c1"},
    }))
    output = tmp_path / "out"
    output.mkdir()
    args = SimpleNamespace(
        room_directory=frozen_file, channel_reads_url=None)
    directory = await cli._room_directory(args, SimpleNamespace(),
                                          SimpleNamespace(), output)
    assert directory.guild_channels(guild_id="g1") == {"c1": "hall"}
    assert directory.guild_channels(guild_id="g2") is None
    assert directory.parent_channel(channel_id="t1") == "c1"
    frozen = json.loads((output / "room-directory.json").read_text())
    assert frozen["channels"]["g1"] == {"c1": "hall"}
    assert frozen["source"] == str(frozen_file)


async def test_a_failed_guild_read_is_recorded_and_absent(
    monkeypatch, tmp_path,
):
    """The bridge answering `available: false` leaves the guild out of the
    frozen map — the per-case check, not the read, names the ERROR."""
    import json
    from types import SimpleNamespace

    from scripts.cognition_loop import __main__ as cli

    class _Client:
        def __init__(self, endpoint):
            pass

        async def read(self, op, params):
            return {"available": False, "value": None}

        async def close(self):
            pass

    cfg = SimpleNamespace(
        platforms=SimpleNamespace(
            discord=SimpleNamespace(base_url="http://bridge")),
        bridges=SimpleNamespace(token_env="T"),  # noqa: S106 — env var name
        credentials={"T": "tok"})
    monkeypatch.setattr(cli, "BridgeControlClient", _Client)
    output = tmp_path / "out"
    output.mkdir()
    args = SimpleNamespace(room_directory=None, channel_reads_url=None)
    policy = SimpleNamespace(
        conversations=[SimpleNamespace(guild="g1")])
    directory = await cli._room_directory(args, cfg, policy, output)
    assert directory.guild_channels(guild_id="g1") is None
    frozen = json.loads((output / "room-directory.json").read_text())
    assert frozen["unread_guilds"] == ["g1"]


def test_a_corrupt_room_directory_still_freezes_the_report(
    monkeypatch, tmp_path,
):
    """A failure inside `_room_directory` is the `room_directory` step's
    ERROR — and `report.json`/`report.html` are still written."""
    import sys
    import types
    from contextlib import contextmanager
    from types import SimpleNamespace

    import scripts.cognition_loop.__main__ as cli

    case_file = tmp_path / "case.json"
    write_case(generated_case(), case_file)
    config_file = tmp_path / "config.toml"
    config_file.write_text("x = 1\n")
    corrupt = tmp_path / "frozen.json"
    corrupt.write_text("{ not json")

    monkeypatch.setattr(cli, "materialise", lambda **k: tmp_path / "g.toml")
    (tmp_path / "g.toml").write_text("x = 1\n")
    monkeypatch.setattr(cli, "load", lambda path: SimpleNamespace(
        store=SimpleNamespace(database_url_env="E"),
        honcho=SimpleNamespace(workspace_id="deploy-ws"),
    ))
    monkeypatch.setattr(cli, "snapshot_only", lambda *a, **k: None)
    monkeypatch.setattr(cli, "private_directory", lambda p: p)
    monkeypatch.setattr(cli, "Judges", lambda *a, **k: SimpleNamespace())
    monkeypatch.setattr(cli, "evaluation_export_for", lambda p: None)
    monkeypatch.setattr(cli, "load_policy", lambda p, **k: SimpleNamespace())
    monkeypatch.setattr(cli, "read_events", lambda p: [])
    monkeypatch.setattr(cli, "validate_events", lambda e: {"status": "PASS"})
    monkeypatch.setattr(
        cli, "create_sql_engine",
        lambda url: SimpleNamespace(dispose=_async_noop()))
    monkeypatch.setitem(
        sys.modules, "nora.secret",
        SimpleNamespace(credentials=lambda: {"E": "cred"}))
    backbone = types.ModuleType("evals.backbone")
    backbone._prefill_capture = lambda: None
    monkeypatch.setitem(sys.modules, "evals.backbone", backbone)

    @contextmanager
    def _noop_ctx(*a, **k):
        yield

    class _Evidence:
        def __init__(self, **kwargs):
            self.path = tmp_path / "events.jsonl"
            self.path.write_text("")

        capture = staticmethod(_noop_ctx)

    monkeypatch.setattr(cli, "RunEvidence", _Evidence)

    args = SimpleNamespace(
        case=case_file, case_dir=None, smoke=False,
        per_wake_tokens=1, token_budget=10,
        config=config_file, policy=None, identity=None,
        database_url="postgresql+asyncpg://x", snapshot=True,
        output=tmp_path, judge_a="a", judge_b="b",
        inspect_only=True, overrides={},
        room_directory=corrupt, channel_reads_url=None,
    )
    report = asyncio.run(cli.run(args))
    assert report["status"] == "ERROR"
    assert report["errors"] and report["errors"][0]["step"] == "room_directory"
    assert report["cases"] == []
    report_json = (tmp_path / "report.json")
    assert report_json.is_file()
    html_text = (tmp_path / "report.html").read_text(encoding="utf-8")
    assert "room_directory" in html_text
    assert "ERROR" in html_text
