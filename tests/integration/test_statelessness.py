from nora.testing import ScriptedTurn, group_event


def _script(text):
    return [ScriptedTurn(tool_calls=[("send_reply", {"text": text})], final_text=text)]


async def test_two_tags_produce_two_independent_invocations(harness_factory):
    h = await harness_factory(script=_script("first"), debounce=0)
    await h.handler.on_event(group_event(body="nora.000 one"))
    await h.drain()
    h.set_script(_script("second"))
    await h.handler.on_event(group_event(body="nora.000 two"))
    await h.drain()

    assert h.sink.texts == ["first", "second"]
    assert h.agents_constructed == 2
    assert all(len(pf) == 3 for pf in h.prefills)     # no carry-over into agent.messages


async def test_restart_statelessness(harness_factory):
    """Continuity is the database + Honcho only — never agent memory."""
    h = await harness_factory(script=_script("before"), debounce=0)
    await h.handler.on_event(group_event(body="nora.000 one"))
    await h.drain()

    h2 = await h.simulate_restart(script=_script("after"))   # new Handler, same db + honcho
    await h2.handler.on_event(group_event(body="nora.000 two"))
    await h2.drain()

    assert h2.sink.texts == ["after"]
    # the second agent still sees the first exchange, because it came from the database
    transcript = h2.prefills[-1][1]["content"][0]["text"]
    assert "before" in transcript


async def test_a_fresh_reply_guard_is_built_per_tag(harness_factory):
    h = await harness_factory(script=_script("x"), debounce=0)
    await h.handler.on_event(group_event(body="nora.000 one"))
    await h.drain()
    await h.handler.on_event(group_event(body="nora.000 two"))
    await h.drain()
    assert len(h.guards) == 2
    assert h.guards[0] is not h.guards[1]
    assert all(g.counts["send_reply"] == 1 for g in h.guards)
