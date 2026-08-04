from pathlib import Path

from tests.builders import override_config


def test_override_config_replaces_a_nested_field(tmp_path, monkeypatch):
    import shutil

    from nora.config import load

    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    cfg_path = tmp_path / "config.yaml"
    shutil.copy(Path(__file__).resolve().parents[1] / "config.example.yaml", cfg_path)
    base = load(cfg_path)

    out = override_config(base, {
        "buffer.db_path": tmp_path / "messages.db",
        "trigger.min_interval_seconds": 0,
    })

    assert out.buffer.db_path == tmp_path / "messages.db"
    assert out.trigger.min_interval_seconds == 0
    assert out.buffer.context_window == base.buffer.context_window   # siblings preserved
    assert base.trigger.min_interval_seconds != 0                    # base is not mutated
