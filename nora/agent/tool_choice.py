"""The `tool_choice` sequence — `required` for a turn's first model call, `auto`
once she has spoken. This IS the multi-message mechanism
(`design/proposed/16-multi-message-replies.md` §2).

Two constants and one function, in their own module because both ends of the
sequence read them and neither may import the other: `nora.agent.factory`
sets the opening value on every model it builds, and `nora.agent.guards`
relaxes it mid-turn — and `factory` already imports `guards` (for `ReplyGuard`),
so the shared names cannot live in either.

**Measured, 2026-08-06, `opencode-zen/deepseek-v4-flash`, `reasoning_effort="low"`
(design/16 §2.1) — do not re-derive:**

    A. auto, fresh question          plain text 5/6, called a tool 1/6
    B. auto, already replied         STOPPED 8/8
    C. required, already replied     kept going 6/6            (control)
    D. auto throughout, invited      0 messages sent 6/6

A and D are why `required` cannot simply be dropped: under `auto` on the FIRST
call the model answers in prose, which is not a tool call, which is not a
message — Nora silent in the group while the turn looks successful. That is
R-5's exact failure shape, and relaxing globally would have shipped it.

B against its control C is the permission: once a reply exists in history,
`auto` lets her stop 8/8 where `required` never stops 6/6. The loop ends
because she declines to call anything, not because a limit caught her — which
is why a message cap can never BE the stopping mechanism, only a backstop
(§1: with no way to stop early a cap becomes a quota that is always spent in
full).

**HOW MANY messages she then sends is a `voice.md` decision, not this
module's.** Identical mechanism, two doctrines: a neutral prompt yields exactly
one message 6/6 — today's behaviour, no regression — and an invitation to chat
like a person yields 3-5.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("nora.agent.tool_choice")

#: What every model is built with (`nora.agent.factory.create_model_provider`).
FIRST_CALL = "required"

#: What `nora.agent.guards.ReplyGuard` flips it to once a terminal verb has run.
AFTER_SPEAKING = "auto"


def relax(model: Any) -> bool:
    """Flip one tag's model to `auto`. True if it changed, False otherwise.

    **Safe only because the model is per tag** (step 5a,
    `nora.agent.factory.ModelSource`). Against the process-wide model this
    repo built until 2026-08-06 it would have flipped a CONCURRENT tag's first
    call to `auto` — see the measurements above for what that call sends.

    `params` is READ, COPIED and WRITTEN BACK WHOLE on purpose:
    `OpenAIModel.update_config` is `self.config.update(model_config)`
    (verified against 1.50.2 and pinned by `tests/test_strands_api_contract.py`),
    a shallow dict update, so `update_config(params={"tool_choice": "auto"})`
    would REPLACE the params dict and silently drop `max_tokens`,
    `temperature` and `reasoning_effort` for the rest of the turn.

    Never raises. A model that does not carry an OpenAI-shaped config is not a
    reason to lose a turn — this is a relaxation, and failing to apply it
    leaves the strictly safer `required` in place (she keeps talking rather
    than going quiet, which R-5 says is the direction to fail in).
    """
    if model is None:
        # No agent on the event. Not a production shape — `HookEvent.agent` is
        # a required dataclass field — so this is the older hand-rolled event
        # doubles in `tests/test_guards_terminal.py`, which assert on
        # cancellation and have no model to relax. Silent rather than logged:
        # an exception traceback per turn in those tests would be noise that
        # says nothing about the code under test.
        return False
    try:
        config = model.get_config() or {}
        params = dict(config.get("params") or {})
        if params.get("tool_choice") == AFTER_SPEAKING:
            return False
        params["tool_choice"] = AFTER_SPEAKING
        model.update_config(params=params)
    except Exception:
        log.exception("could not relax tool_choice; leaving it at %s", FIRST_CALL)
        return False
    return True
