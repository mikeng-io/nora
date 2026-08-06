from tests.builders import load_example_config, override_config


def test_override_config_replaces_a_nested_field(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    base = load_example_config(tmp_path)

    out = override_config(base, {
        "store.data_dir": tmp_path / "messages.db",
        "engagement.trigger.min_interval_seconds": 0,
    })

    assert out.store.data_dir == tmp_path / "messages.db"
    assert out.engagement.trigger.min_interval_seconds == 0
    assert out.store.context_window == base.store.context_window   # siblings preserved
    assert base.engagement.trigger.min_interval_seconds != 0       # base is not mutated
