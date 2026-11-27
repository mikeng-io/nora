"""Memory configuration names and removed keys.

Pure loader rules: a retired name fails closed with its new home or removal,
and a removed key is refused by name.
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
    ('message_readings', None, '[memory.extraction], which has been removed'),
    ('memory_index', None, '[memory.index]'),
    ('message_readings', 'batch_size', '[memory.extraction], which has been removed'),
    ('message_readings', 'context_size', '[memory.extraction], which has been removed'),
    ('message_readings', 'settle_seconds', '[memory.extraction], which has been removed'),
    ('memory_index', 'drain_seconds', '[memory.index] refresh_seconds'),
    *[('memory', key, f'[memory] {key} has been removed') for key in (
        'base_url', 'workspace_id', 'ai_peer', 'tagger_card_conclusions',
        'people_in_window_conclusions', 'people_in_window_speakers', 'read_timeout_seconds',
    )],
    ('memory.extraction', 'batch_size', '[memory.extraction] has been removed'),
    ('memory.extraction', 'context_size', '[memory.extraction] has been removed'),
    ('memory.extraction', 'settle_seconds', '[memory.extraction] has been removed'),
    ('memory.index', 'drain_seconds', '[memory.index] refresh_seconds'),
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


def test_lessons_render_is_refused_as_removed(toml_path):
    import tomli_w

    data = tomllib.loads(toml_path.read_text())
    data['lessons']['render'] = True
    toml_path.write_text(tomli_w.dumps(data))
    with pytest.raises(ConfigError, match=r"\[lessons\] render has been removed"):
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
    assert cfg.memory.index.refresh_seconds == 60


@pytest.mark.parametrize(('path', 'value', 'named'), [
    (('memory', 'index', 'read'), False, 'memory.index.read'),
    (('memory', 'index', 'embed_images_per_hour'), 120, 'memory.index.embed_images_per_hour'),
    (('memory', 'graph'), {'enabled': True}, 'memory.graph'),
    (('memory', 'graph', 'comprehend'), {'enabled': True}, 'memory.graph'),
    (('honcho',), {'enabled': False}, 'honcho'),
    (('memory', 'profile'), {'read': True}, 'memory.profile'),
    (('memory', 'profile', 'write'), True, 'memory.profile'),
])
def test_removed_config_keys_are_refused_naming_the_key(toml_path, path, value, named):
    import tomli_w

    data = tomllib.loads(toml_path.read_text())
    target = data
    for part in path[:-1]:
        target = target.setdefault(part, {})
    target[path[-1]] = value
    toml_path.write_text(tomli_w.dumps(data))
    with pytest.raises(ConfigError, match=rf"(?m)^{re.escape(named)}$"):
        load(toml_path)
