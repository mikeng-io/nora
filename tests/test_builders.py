from tests.builders import load_example_config, override_config


def test_override_config_replaces_a_nested_field(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    base = load_example_config(tmp_path)

    out = override_config(base, {
        "buffer.db_path": tmp_path / "messages.db",
        "trigger.min_interval_seconds": 0,
    })

    assert out.buffer.db_path == tmp_path / "messages.db"
    assert out.trigger.min_interval_seconds == 0
    assert out.buffer.context_window == base.buffer.context_window   # siblings preserved
    assert base.trigger.min_interval_seconds != 0                    # base is not mutated
