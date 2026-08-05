"""The three model-facing tools (spec C.7). Thin wrappers over injected dependencies.

Docstrings are the LLM-facing schema — they are written for a model, not a developer.
feed_honcho is deliberately NOT a tool: the handler owns the single post-reply feed
(audit fix #5).

`build_tool_specs` builds each tool as a plain, undecorated `ToolSpec`
(`nora.agent.ports`) closed over this tag's dependencies — no `strands` import
here at all. `build_tools` is the thin, Strands-flavoured wrapper over it
(`nora.agent.ports.adapt` per spec), kept so every existing caller (the engagement
pipeline, `tests/test_tools.py`) that wants ready-to-use Strands tool objects
still can, without knowing `ToolSpec` exists. `NATIVE_TOOL_BUDGETS` is the single
source both draw budgets from — `nora.agent.registry.ToolRegistry.budgets()`
reads the very same mapping, so the number `ReplyGuard` enforces and the number
each tool is declared to cost can never drift apart (DEC-10).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from .ports import ToolSpec, adapt

log = logging.getLogger("nora.tools")

NATIVE_TOOL_BUDGETS: Mapping[str, int] = {
    "read_buffer": 2,
    "read_peer_card": 1,
    "send_reply": 1,
}


def build_tool_specs(
    *,
    store,
    conversation_id: str,
    conversation_type: str,
    persons,
    honcho,
    deliverer,
    metrics,
    context_window: int = 40,
) -> tuple[ToolSpec, ...]:
    # Captured under a name distinct from read_buffer's own `conversation_id`
    # parameter on purpose. That parameter is model-supplied and SHADOWS this
    # one inside read_buffer's body — reading the bare name there is exactly
    # the cross-group read leak Task 1 closes. Every reference to "this tag's
    # conversation" inside read_buffer must go through `bound_conversation_id`.
    bound_conversation_id = conversation_id

    async def read_buffer(
        conversation_id: str, conversation_type: str = "group", limit: int = 40
    ) -> list[dict]:
        """Read older messages from this conversation.

        You already have the most recent messages in front of you. Only call this if you
        need to look further back than what you were shown. Returns messages oldest-first.

        Args:
            conversation_id: The conversation you were tagged in.
            conversation_type: Always "group".
            limit: How many recent messages to fetch (max 200).
        """
        if conversation_id != bound_conversation_id:
            # The model asked for a conversation this tag was never bound to.
            # R-3 only guards the *send* target — nothing guarded the *read*
            # source before this, so a model asking (accidentally or as a
            # probe) for another group's id must never be silently honoured
            # NOR silently corrected. Log it and count it; still serve the
            # conversation these tools were actually built for.
            log.warning(
                "read_buffer: model requested conversation_id=%r but tools are bound "
                "to %r; ignoring the request and staying bound",
                conversation_id, bound_conversation_id,
            )
            metrics.inc("nora_tool_scope_violations_total", tool="read_buffer")
        try:
            # Clamp both ends: `limit` is a model-supplied argument, and a
            # non-positive value must never reach the store. SQLite renders
            # `LIMIT -1` as unbounded, so an unclamped negative limit would
            # dump the entire conversation into context.
            rows = await store.recent(
                conversation_id=bound_conversation_id, limit=max(1, min(int(limit), 200))
            )
        except Exception:
            log.exception("read_buffer failed")
            return []
        # `reply_to` keeps this tool structurally consistent with the prefilled
        # transcript, and that consistency is the point. If the prefill teaches
        # `[A ↳ B]` and read_buffer then hands back un-threaded rows, the older
        # messages look STRUCTURALLY different and the model reads a missing
        # arrow as "not a reply". JSON, not the arrow — a tool returns data,
        # the arrow is prompt syntax.
        #
        # Resolution happens ONLY within the rows this call already returned:
        # no extra query from inside a tool, on the reply path, ever. An
        # unresolved parent drops the key rather than reporting a null the
        # model would then have to interpret.
        def who(row) -> str:
            return "Nora" if row.is_nora else (row.sender_name or "unknown")

        by_ts = {r.ts: r for r in rows}
        out = []
        for r in rows:
            item = {"sender": who(r), "body": r.body, "ts": r.ts}
            parent = by_ts.get(r.quoted_ts) if r.quoted_ts is not None else None
            if parent is not None and parent.id != r.id:
                item["reply_to"] = who(parent)
            out.append(item)
        return out

    # Reviewed for the same shadowing shape as read_buffer (Task 1): `sender_id`
    # here is also model-supplied and unscoped, hardcoded to "signal". This is
    # NOT fixed in this task — see task-1-brief.md and the phase-8 notes for the
    # judgement — but it is not a silent gap either: it reads a durable,
    # per-PERSON Honcho card (not raw messages), and any sender_id valid enough
    # to resolve already had to be a real platform id someone confirmed into
    # identity.toml, so the disclosure is "what Nora recalls about a known
    # person" rather than another group's live transcript. Still a real gap:
    # nothing here checks that sender_id belongs to *this* conversation, so a
    # model that already knows (or guesses) another person's platform id can
    # pull their peer card across conversation boundaries.
    async def read_peer_card(sender_id: str) -> list[str]:
        """Look up what you durably recall about a person.

        Only call this if the recall block you were given is empty and who the person is
        actually matters to your answer. Returns an empty list if you don't know them.

        Args:
            sender_id: The platform id of the person, as shown in the conversation.
        """
        try:
            ref = persons.resolve("signal", sender_id)
            if ref is None:
                return []
            return await honcho.peer_card(peer_id=ref.honcho_peer_id)
        except Exception:
            log.exception("read_peer_card failed")
            return []

    async def send_reply(conversation_id: str, conversation_type: str, text: str) -> bool:
        """Send your reply to the group. Call this exactly once, at the end of your turn.

        This is the only way anything you write reaches the group. If you do not call it,
        nothing is sent. Reply in the conversation you were tagged in and nowhere else.

        Args:
            conversation_id: The conversation you were tagged in.
            conversation_type: Always "group".
            text: Your reply, in your own voice.
        """
        return await deliverer.deliver(text, source="tool")

    return (
        ToolSpec(
            name="read_buffer", description="", schema={}, call=read_buffer,
            budget=NATIVE_TOOL_BUDGETS["read_buffer"],
        ),
        ToolSpec(
            name="read_peer_card", description="", schema={}, call=read_peer_card,
            budget=NATIVE_TOOL_BUDGETS["read_peer_card"],
        ),
        ToolSpec(
            name="send_reply", description="", schema={}, call=send_reply,
            budget=NATIVE_TOOL_BUDGETS["send_reply"],
        ),
    )


def build_tools(
    *,
    store,
    conversation_id: str,
    conversation_type: str,
    persons,
    honcho,
    deliverer,
    metrics,
    context_window: int = 40,
) -> list:
    """The Strands-ready form of `build_tool_specs` — `adapt()` each spec so
    the docstrings above become the tool's model-facing description/schema
    (empty `description`/`schema` on every `ToolSpec` here means "derive from
    the callable", see `nora.agent.ports.adapt`)."""
    specs = build_tool_specs(
        store=store, conversation_id=conversation_id, conversation_type=conversation_type,
        persons=persons, honcho=honcho, deliverer=deliverer, metrics=metrics,
        context_window=context_window,
    )
    return [adapt(spec) for spec in specs]
