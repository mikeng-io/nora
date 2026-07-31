"""Ingest every message; on a tag, build context → LLM → send → DB → Honcho (DESIGN.md §3)."""

import asyncio
import logging
import time

from . import db, parser, tag

log = logging.getLogger("nora.handler")


def build_context(rows: list[dict], peer_card: list[str], tagger_name: str) -> str:
    """system prompt (added by the LLM client) + peer card + recent buffer + the tag."""
    parts = []
    if peer_card:
        facts = "\n".join(f"- {f}" for f in peer_card)
        parts.append(f"What you durably recall about {tagger_name}:\n{facts}")
    transcript = "\n".join(
        f"[{r['sender_name'] or ('Nora' if r['is_nora'] else 'unknown')}]: {r['body']}" for r in rows
    )
    parts.append(f"Recent messages in this group:\n{transcript}")
    parts.append(f"{tagger_name} just tagged you. Reply to their last message.")
    return "\n\n".join(parts)


class Handler:
    def __init__(self, cfg, sender, honcho, llm):
        self.cfg = cfg
        self.sender = sender
        self.honcho = honcho
        self.llm = llm
        self.debounce = tag.Debounce(cfg.tag["min_interval_seconds"])
        self._bg: set[asyncio.Task] = set()

    def _spawn(self, coro) -> None:
        """Run off the listener's path so ingestion never stalls behind a slow call.

        The set holds a strong reference — asyncio only keeps weak ones, so a bare
        create_task can be garbage-collected mid-flight.
        """
        t = asyncio.create_task(coro)
        self._bg.add(t)
        t.add_done_callback(self._done)

    def _done(self, t: asyncio.Task) -> None:
        self._bg.discard(t)
        if not t.cancelled() and t.exception() is not None:
            log.error("background task failed: %s", t.exception())

    async def on_event(self, event: dict) -> None:
        msg = parser.parse_event(event, self.cfg.signal_cli["account"])
        if msg is None:
            return

        try:
            await db.append(
                self.cfg.db_path,
                group_id=msg.group_id,
                sender_id=msg.sender_id,
                sender_name=msg.sender_name,
                ts=msg.ts,
                body=msg.body,
                raw_json=msg.raw_json,
                max_per_group=self.cfg.buffer["max_per_group"],
            )
        except Exception:  # noqa: BLE001 — a DB failure drops the message, never the listener
            log.exception("db append failed; dropping message")
            return

        if not tag.is_tagged(msg.body, self.cfg.tag["trigger"]):
            return
        if not self.debounce.allow(msg.group_id):
            log.info("debounced tag from %s in %s", msg.sender_id, msg.group_id)
            return
        log.info("tagged by %s (%s)", msg.sender_name, msg.sender_id)
        # Replying takes seconds (LLM); keep consuming SSE meanwhile so ambient
        # messages stay timely and the debounce measures real arrival gaps.
        self._spawn(self.handle_tag(msg))

    async def handle_tag(self, msg: parser.Message) -> None:
        rows = await db.recent(self.cfg.db_path, msg.group_id, self.cfg.buffer["context_window"])

        await self.honcho.ensure_peer(msg.sender_id)
        card = await self.honcho.peer_card(msg.sender_id)

        context = build_context(rows, card, msg.sender_name)
        try:
            reply = await self.llm.reply(context)
        except Exception as e:  # noqa: BLE001 — better silent than noisy (§10)
            log.error("LLM call failed, staying silent: %s", e)
            return
        if not reply:
            log.warning("LLM returned empty reply, staying silent")
            return
        log.info("LLM reply (%d chars)", len(reply))

        await db.append(
            self.cfg.db_path,
            group_id=msg.group_id,
            sender_id=self.cfg.signal_cli["nora_sender_id"],
            sender_name="Nora",
            ts=int(time.time() * 1000),
            body=reply,
            is_nora=True,
            max_per_group=self.cfg.buffer["max_per_group"],
        )
        await self.sender.send_group(msg.group_id, reply)
        # Fire-and-forget (§5.2): the Deriver digests async and its response is slow
        # and unused, so don't make the reply path wait on it.
        self._spawn(self.honcho.feed(msg.sender_id, f"[{msg.sender_name}]: {msg.body}\n[Nora]: {reply}"))
