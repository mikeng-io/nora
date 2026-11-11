from __future__ import annotations

from nora.agent.toolkit.spec import _spec


def build(deps):
    from nora.conversation.adapters.tools.record import _make_who_said

    who_said = _make_who_said(deps)
    from nora.conversation.adapters.tools.record import _make_load_transcript

    load_transcript = _make_load_transcript(deps)
    from nora.conversation.adapters.tools.rooms import _make_room_list

    room_list = _make_room_list(deps)
    from nora.conversation.adapters.tools.rooms import _make_room_read

    room_read = _make_room_read(deps)
    from nora.conversation.adapters.tools.query import _make_record_query

    record_query = _make_record_query(deps)
    from nora.conversation.adapters.tools.rooms import _make_poll_list

    poll_list = _make_poll_list(deps)
    from nora.conversation.adapters.tools.rooms import _make_poll_read

    poll_read = _make_poll_read(deps)
    from nora.conversation.adapters.tools.rooms import _make_room_state_read

    room_state_read = _make_room_state_read(deps)
    from nora.conversation.adapters.tools.replies import _make_send_reply

    send_reply = _make_send_reply(deps)
    from nora.conversation.adapters.tools.replies import _make_reaction_send

    reaction_send = _make_reaction_send(deps)
    from nora.conversation.adapters.tools.replies import _make_send_to

    send_to = _make_send_to(deps)
    from nora.conversation.adapters.tools.replies import _make_search_gifs

    search_gifs = _make_search_gifs(deps)
    from nora.conversation.adapters.tools.replies import _make_send_gif

    send_gif = _make_send_gif(deps)
    return (
        _spec("who_said", who_said),
        _spec("load_transcript", load_transcript),
        _spec("room_list", room_list),
        _spec("room_read", room_read),
        _spec("record_query", record_query),
        _spec("poll_list", poll_list),
        _spec("poll_read", poll_read),
        _spec("room_state_read", room_state_read),
        _spec("send_reply", send_reply),
        _spec("reaction_send", reaction_send),
        _spec("send_to", send_to),
        _spec("search_gifs", search_gifs),
        _spec("send_gif", send_gif),
    )
