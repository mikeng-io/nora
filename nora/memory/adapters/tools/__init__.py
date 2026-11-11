from __future__ import annotations

from nora.agent.toolkit.spec import _spec


def build(deps):
    from nora.memory.adapters.tools.profile import _make_remember_told

    remember_told = _make_remember_told(deps)
    from nora.memory.adapters.tools.standing import _make_rollup_read

    rollup_read = _make_rollup_read(deps)
    from nora.memory.adapters.tools.standing import _make_standing_read

    standing_read = _make_standing_read(deps)
    from nora.memory.adapters.tools.standing import _make_character_read

    character_read = _make_character_read(deps)
    from nora.memory.adapters.tools.search import _make_ask_memory

    ask_memory = _make_ask_memory(deps)
    from nora.memory.adapters.tools.search import _make_search_memory

    search_memory = _make_search_memory(deps)
    from nora.memory.adapters.tools.search import _make_memory_search

    memory_search = _make_memory_search(deps)
    from nora.memory.adapters.tools.search import _make_memory_read

    memory_read = _make_memory_read(deps)
    from nora.memory.adapters.tools.meta import _make_recall_lookups

    recall_lookups = _make_recall_lookups(deps)
    from nora.memory.adapters.tools.memos import _make_search_memory_workspace

    search_memory_workspace = _make_search_memory_workspace(deps)
    from nora.memory.adapters.tools.notes import _make_note_read

    note_read = _make_note_read(deps)
    from nora.memory.adapters.tools.notes import _make_note_search

    note_search = _make_note_search(deps)
    from nora.memory.adapters.tools.memos import _make_memo_read

    memo_read = _make_memo_read(deps)
    from nora.memory.adapters.tools.notes import _make_note_create

    note_create = _make_note_create(deps)
    from nora.memory.adapters.tools.notes import _make_note_revise

    note_revise = _make_note_revise(deps)
    from nora.memory.adapters.tools.profile import _make_remember

    remember = _make_remember(deps)
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
        _spec("standing_read", standing_read),
        _spec("character_read", character_read),
        _spec("ask_memory", ask_memory),
        _spec("search_memory", search_memory),
        _spec("memory_search", memory_search),
        _spec("memory_read", memory_read),
        _spec("recall_lookups", recall_lookups),
        _spec("search_memory_workspace", search_memory_workspace),
        _spec("note_read", note_read),
        _spec("note_search", note_search),
        _spec("memo_read", memo_read),
        _spec("note_create", note_create),
        _spec("note_revise", note_revise),
        _spec("remember", remember_told)
        if deps.memory_retrieval is not None
        else _spec("remember", remember),
        _spec("profile_dispute", profile_dispute),
        _spec("version_list", version_list),
        _spec("version_read", version_read),
        _spec("checkpoint_update", checkpoint_update),
    )
