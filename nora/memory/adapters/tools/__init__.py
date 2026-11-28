from __future__ import annotations

from nora.agent.toolkit.spec import _spec


def build(deps):
    from nora.memory.adapters.tools.standing import _make_rollup_read

    rollup_read = _make_rollup_read(deps)
    from nora.memory.adapters.tools.search import _make_memory_search

    memory_search = _make_memory_search(deps)
    from nora.memory.adapters.tools.search import _make_memory_read

    memory_read = _make_memory_read(deps)
    from nora.memory.adapters.tools.intentions import _make_intention_commit

    intention_commit = _make_intention_commit(deps)
    from nora.memory.adapters.tools.profile import _make_profile_dispute

    profile_dispute = _make_profile_dispute(deps)
    from nora.memory.adapters.tools.meta import _make_version_list

    version_list = _make_version_list(deps)
    from nora.memory.adapters.tools.meta import _make_version_read

    version_read = _make_version_read(deps)
    from nora.memory.adapters.tools.meta import _make_checkpoint_update

    checkpoint_update = _make_checkpoint_update(deps)
    return (
        _spec("rollup_read", rollup_read),
        _spec("memory_search", memory_search),
        _spec("memory_read", memory_read),
        _spec("intention_commit", intention_commit),
        _spec("profile_dispute", profile_dispute),
        _spec("version_list", version_list),
        _spec("version_read", version_read),
        _spec("checkpoint_update", checkpoint_update),
    )
