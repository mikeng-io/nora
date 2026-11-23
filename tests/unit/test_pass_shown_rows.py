"""The context-local shown set starts with assembly and gains later reads."""

import asyncio

from nora.memory import ActAuthority
from nora.memory.adapters.pass_assembly import AssembledPass
from nora.memory.app.act_scope import act_event, pass_shown


def test_assembly_and_read_ids_accumulate_only_inside_the_pass():
    assembled = AssembledPass((), "ok", None, frozenset({"claim:assembled"}))
    with act_event("first", ActAuthority("unit"), shown=assembled.shown):
        shown = pass_shown()
        assert shown == {"claim:assembled"}
        shown.add("item:read")
        assert pass_shown() == {"claim:assembled", "item:read"}
    with act_event("second", ActAuthority("unit")):
        assert pass_shown() == set()
    assert assembled.shown == frozenset({"claim:assembled"})


async def test_concurrent_passes_keep_separate_shown_rows():
    entered = asyncio.Event()
    release = asyncio.Event()

    async def first():
        with act_event("first", ActAuthority("unit"), shown=frozenset({"claim:first"})):
            entered.set()
            await release.wait()
            return pass_shown()

    task = asyncio.create_task(first())
    await entered.wait()
    with act_event("second", ActAuthority("unit"), shown=frozenset({"claim:second"})):
        shown = pass_shown()
        shown.add("item:second")
        release.set()
        assert await task == {"claim:first"}
        assert shown == {"claim:second", "item:second"}


def test_no_active_pass_has_no_shown_set():
    assert pass_shown() is None
