"""Memory configuration names, retirement and host-shape compatibility.

These are pure loader rules split out of the legacy root config module: retired
names fail closed with their new home or removal, while the one-release host
compatibility keys load without becoming live switches.
"""

from __future__ import annotations

import re
import tomllib

import pytest

from nora.config import ConfigError, load


@pytest.fixture
def toml_path(example_config_path, monkeypatch):
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    return example_config_path


@pytest.mark.parametrize(('section', 'key', 'new_home'), [
    ('persona', None, '[memory.profile]'),
    ('profile', None, '[memory.profile]'),
    ('message_readings', None, '[memory.extraction], which has been removed'),
    ('memory_index', None, '[memory.index]'),
    ('persona', 'enabled', '[memory.profile] write'),
    ('persona', 'render', '[memory.profile] read'),
    ('profile', 'enabled', '[memory.profile] write'),
    ('profile', 'render', '[memory.profile] read'),
    ('message_readings', 'batch_size', '[memory.extraction], which has been removed'),
    ('message_readings', 'context_size', '[memory.extraction], which has been removed'),
    ('message_readings', 'settle_seconds', '[memory.extraction], which has been removed'),
    ('memory_index', 'enabled', '[memory.index] read'),
    ('memory_index', 'drain_seconds', '[memory.index] refresh_seconds'),
    *[('memory', key, f'[memory] {key} has been removed') for key in (
        'base_url', 'workspace_id', 'ai_peer', 'tagger_card_conclusions',
        'people_in_window_conclusions', 'people_in_window_speakers', 'read_timeout_seconds',
    )],
    ('memory.extraction', 'batch_size', '[memory.extraction] has been removed'),
    ('memory.extraction', 'context_size', '[memory.extraction] has been removed'),
    ('memory.extraction', 'settle_seconds', '[memory.extraction] has been removed'),
    ('memory.index', 'enabled', '[memory.index] read'),
    ('memory.index', 'drain_seconds', '[memory.index] refresh_seconds'),
    ('memory.profile', 'enabled', '[memory.profile] write'),
    ('memory.profile', 'render', '[memory.profile] read'),
    ('culture', 'person_wake_batch', '[memory.profile.person] update_every_messages'),
    ('culture', 'unit_wake_batch', '[memory.profile.unit] update_every_room_profile_changes'),
    ('culture', 'nora_wake_batch', '[memory.profile.nora] update_every_replies'),
])
def test_retired_memory_config_names_are_refused_naming_the_new_home_or_the_removal(
    toml_path, section, key, new_home,
):
    import tomli_w

    data = tomllib.loads(toml_path.read_text())
    target = data
    for part in section.split('.'):
        target = target.setdefault(part, {})
    if key is not None:
        target[key] = 'retired-value'
    toml_path.write_text(tomli_w.dumps(data))
    with pytest.raises(ConfigError, match=re.escape(new_home)):
        load(toml_path)


def test_memory_extraction_section_is_refused_as_removed(toml_path):
    import tomli_w

    data = tomllib.loads(toml_path.read_text())
    data['memory']['extraction'] = {'enabled': False}
    toml_path.write_text(tomli_w.dumps(data))
    with pytest.raises(ConfigError, match=r"\[memory\.extraction\] has been removed"):
        load(toml_path)


def test_example_loads_with_the_memory_config_names(toml_path):
    cfg = load(toml_path)
    assert cfg.memory.index.read is False
    assert cfg.memory.index.refresh_seconds == 60
    assert cfg.memory.profile.write is False
    assert cfg.memory.profile.read is False
    assert cfg.memory.profile.person.update_every_messages == 100
    assert cfg.memory.profile.unit.update_every_room_profile_changes == 5
    assert cfg.memory.profile.nora.update_every_replies == 100
    assert cfg.memory.profile.update_every_seconds == cfg.culture.stale_seconds == 604800


@pytest.mark.parametrize("sections", ["host", "absent"])
def test_retired_memory_graph_sections_load_accepted_and_ignored(toml_path, sections):
    import tomli_w

    data = tomllib.loads(toml_path.read_text())
    if sections == "absent":
        data["memory"].pop("graph", None)
    else:
        data["memory"].setdefault("graph", {})["enabled"] = True
        data["memory"]["graph"].setdefault("comprehend", {})["enabled"] = True
    data["honcho"] = {"enabled": False}
    toml_path.write_text(tomli_w.dumps(data))

    cfg = load(toml_path)

    if sections == "absent":
        assert cfg.memory.graph is None
    else:
        assert cfg.memory.graph["enabled"] is True
        assert cfg.memory.graph["comprehend"]["enabled"] is True
    assert cfg.memory.index.read is False
    assert cfg.memory.index.embed_images_per_hour == 120


def test_host_shaped_memory_index_values_load_through_the_real_loader(toml_path):
    import tomli_w

    data = tomllib.loads(toml_path.read_text())
    data["memory"]["index"].update(
        read=False, embed=True, embed_batch=256, embed_images_per_hour=400,
    )
    data["memory"]["profile"].update(write=True, read=False)
    data["memory"].setdefault("graph", {})["enabled"] = True
    data["memory"]["graph"].setdefault("comprehend", {})["enabled"] = True
    data["honcho"] = {"enabled": False}
    toml_path.write_text(tomli_w.dumps(data))

    cfg = load(toml_path)

    assert cfg.memory.index.read is False
    assert cfg.memory.index.embed is True
    assert cfg.memory.index.embed_batch == 256
    assert cfg.memory.index.embed_images_per_hour == 400
    assert cfg.memory.profile.write is True
    assert cfg.memory.profile.read is False
    assert cfg.memory.graph["enabled"] is True
    assert cfg.memory.graph["comprehend"]["enabled"] is True
