"""A broken lifecycle reader retains the subject trajectory without dispatching judges."""

from types import SimpleNamespace

from tests.unit.test_cognition_loop_run_wake import _runner_subject, _wake_args, _wake_seam


async def test_lifecycle_read_failure_keeps_terminal_evidence_and_names_the_error(
    monkeypatch, tmp_path,
):
    inspection = {"system": "Generated complete subject context", "agenda": "Generated agenda"}
    run = _wake_seam(monkeypatch, tmp_path, _runner_subject(inspection=inspection))
    args, evidence = _wake_args(tmp_path)
    calls = []

    async def unavailable(*args, **kwargs):
        raise RuntimeError("generated canonical history read failure")

    async def judge(*args, **kwargs):
        calls.append(1)
        raise AssertionError("a broken instrument must not dispatch a judge")

    monkeypatch.setattr(run, "read_effects", unavailable)
    monkeypatch.setattr(run, "_judge", judge)
    result = await run._run_wake(args, SimpleNamespace(unit="u", expectations=(), read_probes=()),
        0, 11, 1_800_000_000_000, "postgresql+asyncpg://x", "time", None,
        evidence, tmp_path, ("signal", "r"))
    assert result["status"] == "ERROR" and result["step"] == "report"
    assert "generated canonical history read failure" in result["error"]
    assert result["trajectory"]["blocks"]["system"] == inspection["system"]
    assert calls == []


def test_effect_scope_uses_the_composed_unit_and_all_its_room_members():
    from nora.domain.culture import character_key, nora_character_key, unit_culture_key
    from nora.subject.effects import effect_scope

    rooms = [SimpleNamespace(unit=unit, platform="signal", primary_id=room, ids=frozenset({room}),
                             persons=frozenset({person})) for unit, room, person in (
        ("generated-unit", "r1", "miles"), ("generated-unit", "r2", "nova"),
        ("foreign-unit", "other", "elm"))]
    subject = SimpleNamespace(unit="generated-unit", deps=SimpleNamespace(
        config_reload=SimpleNamespace(policy=SimpleNamespace(rooms=lambda: rooms)),
        persons=SimpleNamespace(by_person={"miles": object(), "nova": object(), "elm": object()})))
    scope = effect_scope(subject)
    assert scope.unit == "generated-unit"
    assert set(scope.rooms) == {("signal", "r1"), ("signal", "r2")}
    assert set(scope.profile_keys) == {
        nora_character_key("generated-unit"), unit_culture_key("generated-unit"),
        character_key("generated-unit", "miles"), character_key("generated-unit", "nova")}
    rooms[0].persons = None
    wildcard = effect_scope(subject)
    assert set(wildcard.profile_keys) == {
        *scope.profile_keys, character_key("generated-unit", "elm")}
