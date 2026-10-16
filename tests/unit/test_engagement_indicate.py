"""`nora.engagement.stages.indicate` — the 👀 / ✅ / ⚠️ lifecycle.

The three states have to stay mutually distinguishable, because that IS the
feature: a person who tagged Nora must be able to tell "still working" from
"done, here is the reply" from "gave up" without the group ever seeing an
error. Each of those is pinned below by asserting the emoji actually sent, not
just that *a* reaction happened.
"""

from __future__ import annotations

import asyncio

from nora.app.runtime import BackgroundTasks
from nora.domain.models import NormalizedMessage
from nora.domain.ports import ReactionResult
from nora.engagement.stages import indicate
from nora.obs.metrics import Metrics


class _Recorder:
    """An adapter that records reactions. `fails`/`raises` cover the two ways a
    platform that HAS reactions can still not deliver one."""

    platform = "signal"

    def __init__(self, *, fails: bool = False, raises: bool = False) -> None:
        self.calls: list[tuple[str, str, int, str, bool]] = []
        self._fails = fails
        self._raises = raises

    async def react(
        self, *, target, emoji, to_author, to_timestamp, to_message_id="", remove=False,
    ):
        self.calls.append((emoji, to_author, to_timestamp, to_message_id, remove))
        if self._raises:
            raise RuntimeError("daemon exploded")
        return ReactionResult(ok=not self._fails)

    @property
    def emojis(self) -> list[str]:
        return [emoji for emoji, _, _, _, remove in self.calls if not remove]


class _NoReactions:
    """A future platform with no reaction concept at all — not even the
    attribute. This is the degradation case: it must be counted, not crashed
    on."""

    platform = "telegram"


def _msg(sender_id: str = "+852111", timestamp: int = 1754300000000) -> NormalizedMessage:
    return NormalizedMessage(
        platform="signal", conversation_id="g1", conversation_type="group",
        sender_id=sender_id, sender_name="Miles", timestamp=timestamp,
        body="nora.000 幾點食飯", raw={},
    )


async def _drain(background: BackgroundTasks) -> None:
    while len(background):
        await asyncio.gather(*list(background._tasks), return_exceptions=True)
        await asyncio.sleep(0)


async def _run(adapter, *, delivered: bool, message: NormalizedMessage | None = None):
    metrics, background = Metrics(), BackgroundTasks()
    indicator = indicate.begin(
        adapter=adapter, message=message or _msg(), tag_id="tag-1",
        metrics=metrics, spawn=background.spawn,
    )
    indicate.resolve(indicator=indicator, delivered=delivered, spawn=background.spawn)
    await _drain(background)
    return metrics, indicator


# ---------------------------------------------------------------------------
# The three states
# ---------------------------------------------------------------------------


async def test_accepting_a_tag_reacts_with_eyes_to_the_tagging_message():
    """PROBE: change `ACK` away from 👀, or stop spawning `_place`, and this
    fails."""
    adapter = _Recorder()
    metrics, _ = await _run(adapter, delivered=True)
    assert adapter.calls[0] == (indicate.ACK, "+852111", 1754300000000, "", False)
    assert indicate.ACK == "\N{EYES}"
    assert metrics.get("nora_reactions_total", phase="ack", status="sent") == 1.0


async def test_a_delivered_reply_resolves_to_a_check_mark():
    adapter = _Recorder()
    metrics, _ = await _run(adapter, delivered=True)
    assert adapter.emojis == [indicate.ACK, indicate.DONE]
    assert indicate.DONE == "\N{WHITE HEAVY CHECK MARK}"
    assert metrics.get("nora_reactions_total", phase="done", status="sent") == 1.0


async def test_a_turn_that_produced_no_reply_resolves_to_a_caution_never_a_check_mark():
    """The design decision, pinned. A ✅ after a failure claims a reply that
    does not exist; the group would wait for a message that is not coming. ⚠️
    is honest about the same outcome and still discloses nothing about WHY —
    no error text ever reaches the group (R-5).

    PROBE: resolve failures to `DONE` instead of `GAVE_UP` and this fails."""
    adapter = _Recorder()
    metrics, _ = await _run(adapter, delivered=False)
    assert adapter.emojis == [indicate.ACK, indicate.GAVE_UP]
    assert indicate.DONE not in adapter.emojis
    assert metrics.get("nora_reactions_total", phase="gave_up", status="sent") == 1.0


async def test_the_three_states_are_mutually_distinguishable():
    """If any two of these collapsed, the feature would be decorative: the
    tagger could no longer tell working from done from abandoned."""
    assert len({indicate.ACK, indicate.DONE, indicate.GAVE_UP}) == 3


async def test_the_eyes_are_always_retracted_before_the_terminal_emoji():
    """Ordering, not just membership: the retract must precede the replacement,
    or a platform that does not auto-replace ends up showing both."""
    adapter = _Recorder()
    await _run(adapter, delivered=True)
    removes = [i for i, c in enumerate(adapter.calls) if c[4]]
    finals = [i for i, c in enumerate(adapter.calls) if c[0] == indicate.DONE]
    assert removes and finals and max(removes) < min(finals)


# ---------------------------------------------------------------------------
# R-5: every failure is silent and counted
# ---------------------------------------------------------------------------


async def test_a_platform_without_reactions_is_counted_unsupported_and_never_crashes():
    """PROBE: replace `getattr(adapter, "react", None)` with a direct
    `adapter.react(...)` and this raises AttributeError instead."""
    metrics, indicator = await _run(_NoReactions(), delivered=True)
    assert indicator is not None
    assert metrics.get("nora_reactions_total", status="unsupported") == 3.0
    assert metrics.get("nora_reactions_total", status="failed") == 0.0


async def test_a_rejected_reaction_is_counted_failed_and_swallowed():
    adapter = _Recorder(fails=True)
    metrics, _ = await _run(adapter, delivered=True)
    assert metrics.get("nora_reactions_total", status="failed") == 3.0
    assert metrics.get("nora_reactions_total", status="sent") == 0.0


async def test_a_reaction_that_raises_is_absorbed_and_counted():
    """An adapter that breaks the port's never-raise contract still must not
    take the tag down — these coroutines run detached, so an escaping exception
    would be a lost background task and nothing else."""
    adapter = _Recorder(raises=True)
    metrics, _ = await _run(adapter, delivered=False)
    assert metrics.get("nora_reactions_total", status="failed") == 3.0


async def test_the_resolution_still_runs_when_the_eyes_never_landed():
    """Self-healing: a failed 👀 must not strand the resolution. `_place` sets
    its event in a `finally` precisely so `_resolve` is never blocked by it.

    PROBE: move `indicator.placed.set()` out of `_place`'s `finally` into a
    trailing statement inside the `try`, and make the ack raise — the resolve
    then waits forever and this test hangs."""
    adapter = _Recorder(raises=True)
    metrics, _ = await _run(adapter, delivered=True)
    assert adapter.emojis == [indicate.ACK, indicate.DONE]
    assert metrics.get("nora_reactions_total", phase="done") == 1.0


# ---------------------------------------------------------------------------
# Targeting
# ---------------------------------------------------------------------------


async def test_a_sender_known_only_by_aci_is_still_reacted_to():
    """Under sealed sender the parser yields `sourceUuid`; signal-cli accepts a
    bare ACI as a RECIPIENT, so nothing extra is needed and nothing is skipped."""
    aci = "b9f1c2de-4a55-4c1e-9a77-0c1d2e3f4a5b"
    adapter = _Recorder()
    await _run(adapter, delivered=True, message=_msg(sender_id=aci))
    assert adapter.calls[0][1] == aci


async def test_a_message_with_no_usable_target_creates_no_indicator():
    adapter = _Recorder()
    metrics, indicator = await _run(adapter, delivered=True, message=_msg(timestamp=0))
    assert indicator is None
    assert adapter.calls == []
    assert metrics.get("nora_reactions_total") == 0.0


async def test_resolve_tolerates_a_missing_indicator():
    background = BackgroundTasks()
    indicate.resolve(indicator=None, delivered=True, spawn=background.spawn)
    assert len(background) == 0


async def test_the_reaction_targets_the_tagging_conversation_only():
    """R-3's property, structurally: the reaction target is derived from the
    tagging message, never from model input, so there is no argument to get
    wrong and no path to another conversation."""
    adapter = _Recorder()
    metrics, indicator = await _run(adapter, delivered=True)
    assert indicator is not None
    assert indicator.target == _msg().target()
    assert metrics.get("nora_reactions_total", platform="signal") == 3.0


async def test_the_platforms_own_message_handle_reaches_the_reaction():
    """The whole reason `NormalizedMessage.message_id` exists.

    Discord's reaction endpoint accepts NOTHING but the message snowflake —
    `(author, timestamp)` cannot reconstruct one, because
    `snowflake.timestamp_ms` throws the low 22 bits away. So the parser puts
    the platform's own handle on the message, `begin` carries it onto the
    `Indicator`, and `_react` passes it as `to_message_id`.

    Recorders that merely ACCEPT `to_message_id` and drop it would leave the
    suite green when it is blanked anywhere, while the bot answers
    `supported=False` on every reaction forever. This asserts the value that
    actually arrives at the adapter, which is the only place the chain can be
    observed end to end.
    """
    adapter = _Recorder()
    message = NormalizedMessage(
        platform="discord", conversation_id="111/222", conversation_type="group",
        sender_id="900001", sender_name="Miles", timestamp=1754300000000,
        body="nora.000 hi", raw={}, message_id="100000000000000208",
    )

    await _run(adapter, delivered=True, message=message)

    assert adapter.calls, "the 👀 must have been attempted"
    assert {c[3] for c in adapter.calls} == {"100000000000000208"}, (
        "the platform's own message handle must reach EVERY react() call; "
        "without it Discord cannot identify the message and the reaction is a "
        "silent no-op"
    )


async def test_a_platform_with_no_message_handle_still_reacts():
    """Signal's counterpart, and the reason `to_message_id` sits beside
    `(to_author, to_timestamp)` rather than replacing them: Signal names a
    message by who sent it and when, has no per-message handle, and reacts by
    author and timestamp."""
    adapter = _Recorder()

    await _run(adapter, delivered=True)

    assert adapter.calls[0][1] == "+852111"      # the author is still carried
    assert adapter.calls[0][3] == ""             # no handle, and that is correct
