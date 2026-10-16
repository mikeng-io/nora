"""The `tool_choice` sequence — `required` for a turn's first model call, `auto`
once she has spoken. This IS the multi-message mechanism.

Two constants and one function, in their own module because both ends of the
sequence read them and neither may import the other: `nora.agent.factory`
sets the opening value on every model it builds, and `nora.agent.guards`
relaxes it mid-turn — and `factory` already imports `guards` (for `ReplyGuard`),
so the shared names cannot live in either.

**Why `required` first, `auto` after.** Under `auto` on the FIRST call the model
tends to answer in prose, which is not a tool call, which is not a message —
Nora silent in the group while the turn looks successful. That is R-5's exact
failure shape, so the opening value is `required` and relaxing globally is
not an option.

Once a reply exists in history, `auto` lets her stop where `required` never
stops. The loop ends because she declines to call anything, not because a
limit caught her — which is why a message cap can only be a backstop, never
the stopping mechanism (with no way to stop early a cap becomes a quota that
is always spent in full).

**HOW MANY messages she then sends is a `voice.md` decision, not this
module's.** Identical mechanism, two doctrines: a neutral prompt yields one
message, and an invitation to chat like a person yields several.
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

    **Safe only because the model is per tag** (`nora.agent.factory.ModelSource`).
    Against a process-wide model it would flip a CONCURRENT tag's first call to
    `auto` — see the sequence above for what that call sends.

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
        # No agent on the event: not a production shape (`HookEvent.agent` is
        # a required dataclass field), but the hand-rolled event doubles in
        # `tests/test_guards_terminal.py` have none and no model to relax.
        # Silent rather than logged: a traceback per turn would be noise.
        return False
    return _set(model, AFTER_SPEAKING, "relax")


def force(model: Any, tool_name: str) -> bool:
    """Pin one tag's model to CALL `tool_name` on its next model call (Phase 2,
    `nora.agent.intent_router`). True if it changed.

    OpenAI's named tool_choice, `{"type":"function","function":{"name":…}}`, is
    how you force a SPECIFIC tool rather than merely `required` (some tool). Set
    from `ReplyGuard._before_model` on the first call for a routed intent, and
    released with `require` on the next — a pin left in place would make her call
    the same tool every pass and never `send_reply`. Same per-tag safety and same
    READ-COPY-WRITE-WHOLE `params` handling as `relax`; never raises (failing to
    force just leaves `required`, which is the safe direction — she still speaks).
    """
    if model is None:
        return False
    return _set(model, {"type": "function", "function": {"name": tool_name}}, "force")


def require(model: Any) -> bool:
    """Release a `force` pin back to the model's INITIAL `tool_choice`, so the
    next call is free again. True if it changed.

    The release target is whatever the model was BUILT with — recorded by `_set`
    the first time it mutates that model — not the module constant. For a tag
    model that is `FIRST_CALL` (`required`) and nothing changes: she has not
    spoken yet, and `auto` on a not-yet-spoken turn sends prose, R-5's
    silent-failure shape (see the module docstring). But a participation turn
    composes its model with `tool_choice="auto"` ON PURPOSE (speaking is
    optional) — releasing its pin to `FIRST_CALL` would flip the rest of that
    turn to `required`, destroying the right-to-silence the occasion exists to create."""
    if model is None:
        return False
    initial = getattr(model, "_nora_initial_tool_choice", None)
    if initial is None:
        # No mutation of this model was ever recorded, so there is no pin to
        # release: its CURRENT value is its initial one, and releasing to it
        # is a no-op by construction. Reading it (rather than defaulting to
        # `FIRST_CALL`) is what keeps that true for a model built `"auto"`.
        config = model.get_config() or {}
        initial = (config.get("params") or {}).get("tool_choice", FIRST_CALL)
    return _set(model, initial, "require")


def _set(model: Any, value: Any, what: str) -> bool:
    """Read the model's `params`, set `tool_choice=value`, write the whole dict
    back. Shared by `relax`/`force`/`require` for the reason `relax` gives:
    `update_config` is a shallow update, so `params` must be written WHOLE or
    `max_tokens`/`temperature`/`reasoning_effort` vanish for the rest of the
    turn. Never raises.

    The first mutation of a model records its as-built `tool_choice` ON the
    model (`_nora_initial_tool_choice`), which is what `require` releases a
    `force` pin back to. The value is per-turn state, so it lives on the per-tag
    model object itself — a module-level table of them would be the retained
    cross-tag state `ModelSource` exists to prevent."""
    try:
        config = model.get_config() or {}
        params = dict(config.get("params") or {})
        if params.get("tool_choice") == value:
            return False
        if getattr(model, "_nora_initial_tool_choice", None) is None:
            model._nora_initial_tool_choice = params.get("tool_choice", FIRST_CALL)
        params["tool_choice"] = value
        model.update_config(params=params)
    except Exception:
        log.exception("could not %s tool_choice; leaving it at %s", what, FIRST_CALL)
        return False
    return True
