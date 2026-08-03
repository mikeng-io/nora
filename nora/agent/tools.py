"""The three model-facing tools (spec C.7). Thin wrappers over injected dependencies.

Docstrings are the LLM-facing schema — they are written for a model, not a developer.
feed_honcho is deliberately NOT a tool: the handler owns the single post-reply feed
(audit fix #5).
"""

from __future__ import annotations

import logging

from strands import tool

from ..store import db

log = logging.getLogger("nora.tools")


def build_tools(
    *,
    db_path,
    conversation_id: str,
    conversation_type: str,
    persons,
    honcho,
    deliverer,
    context_window: int = 40,
) -> list:
    @tool
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
        try:
            rows = await db.recent(db_path, conversation_id, min(int(limit), 200))
        except Exception:                     # noqa: BLE001 — a tool never raises into the loop
            log.exception("read_buffer failed")
            return []
        return [
            {
                "sender": "Nora" if r["is_nora"] else (r["sender_name"] or "unknown"),
                "body": r["body"],
                "ts": r["ts"],
            }
            for r in rows
        ]

    @tool
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
            return await honcho.peer_card(ref.honcho_peer_id)
        except Exception:                     # noqa: BLE001
            log.exception("read_peer_card failed")
            return []

    @tool
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

    return [read_buffer, read_peer_card, send_reply]
