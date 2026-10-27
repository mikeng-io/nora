"""The reaction picker: whether a GIF makes her reply land better, and which.

**Why this is its own call and not a tool she chooses.** An optional garnish on
the one verb a turn must end with (`send_reply`) is never the thing a cheap
model spends its call on, however it is framed: with the reply as the first
call of a no-reasoning turn she sends a GIF only when asked. The choice needs a
moment of its own. Asked as its OWN question, over the last few lines and the
reply she already wrote, the same model puts a GIF on teasing or banter and not
on practical answers.

**It never touches her words.** It runs after `send_reply` has her text and
before delivery, and it can only put a GIF under the text (`with`) or in its
place (`alone`). Everything else, and every failure, is the text exactly as
she wrote it: this is a garnish, so the fail-closed direction is «no GIF»,
never «no reply». The whole pick is bounded by one timeout.

The instruction is `reaction.md` (doctrine audience `reaction`, hot-reloaded,
never shown to her); the candidates it chooses between are Giphy's own
described results, as `search_gifs` returns them to her, so a GIF is chosen
from its description, never sent because it came first.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from nora.domain.blocks import (
    BlockKey,
    ContextBlock,
    assemble,
    restamp,
    row_speaker,
    speaker_label,
)
from nora.domain.external_files import render_file_links
from nora.domain.media import GifCandidate
from nora.domain.models import TranscriptRow
from nora.domain.ports import GifSource, OutboundAttachment
from nora.domain.prompt import escape, one_line

from .egress import scan_identifiers

log = logging.getLogger("nora.agent.reaction")

AUDIENCE = "reaction"
MODES = ("with", "alone")

#: How many of the room's latest lines the picker reads. A reaction answers
#: the moment, not the thread.
RECENT_LINES = 8

#: What this picker carries, declared as data.
#:
#: `_pick` hands its blocks to `assemble()`, so `order` IS the order the model
#: reads (the goldens in `tests/unit/test_step2b_assembly_goldens.py` pin both
#: prompts byte for byte). The occasion is TWO calls over one table: the first
#: question sees `recent_chat` and `nora_reply`, the second adds
#: `gif_candidates` after them. `key="turn"` throughout: nothing here is a
#: cached prefix.
#:
#: Every block is built `when_empty="render"`, so an empty window or a reply that
#: escapes to nothing renders as an empty pair rather than vanishing.
_BLOCKS: tuple[tuple[str, int, BlockKey], ...] = (
    ("recent_chat", 10, "turn"),
    ("nora_reply", 20, "turn"),
    ("gif_candidates", 30, "turn"),
)

#: `name -> (order, key)`, derived so no call site restates a `_BLOCKS` row.
_ROW: dict[str, tuple[int, BlockKey]] = {
    name: (order, key) for name, order, key in _BLOCKS
}

#: What `assemble` puts between the blocks.
_SEP = "\n"


class Completion(Protocol):
    @property
    def content(self) -> str: ...

    @property
    def finish_reason(self) -> str: ...


class ModelClient(Protocol):
    """One text completion. The composition root's `_DebriefModelClient`
    satisfies it, which is where `nora.config` and the transport live."""

    async def complete(self, *, system: str, prompt: str) -> Completion: ...


class PromptSource(Protocol):
    def get(self, *, applies_to: str = ...) -> Any: ...


class Counter(Protocol):
    def inc(self, name: str, value: float = 1.0, **labels: object) -> None: ...


@dataclass(frozen=True)
class Reaction:
    mode: str
    query: str
    candidate: GifCandidate
    #: The GIF's bytes, fetched INSIDE the pick's timeout: a reaction with no
    #: bytes is not a reaction, and a download that hangs must cost the same
    #: bounded wait as a model call that hangs.
    attachment: OutboundAttachment


def render_recent(rows: Sequence[TranscriptRow], *, display_name: str = "Nora") -> str:
    """The room's last few lines as `name: body`, the picker's whole view of
    the moment, escaped and collapsed to one line per row (`one_line`: a
    newline inside a message must not forge a second speaker). An empty body
    (a bare attachment) is skipped rather than rendered as nobody speaking.

    **The speaker goes through `speaker_label`**, as in every other transcript
    renderer, and `row_speaker` names the field. `escape` and `one_line` are not
    enough there: a profile name is attacker-controlled on an unmapped account,
    and `Eve: ignore the above` or `x]: [y` would write a row that reads as
    another speaker's line. The label strips `[`, `]` and `:` from a name
    (`Eve ignore the above: ...`); every other name is unchanged."""
    lines = []
    for row in rows[-RECENT_LINES:]:
        # The handle before the clip, so a cut never leaves half a link.
        body = one_line(render_file_links(row.body or "")[:300]).strip()
        if not body:
            continue
        who = display_name if row.is_nora else row_speaker(row)
        if row.turn_handle is not None:
            who += f" (turn {row.turn_handle})"
        lines.append(f"{speaker_label(who)}: {escape(body)}")
    return "\n".join(lines)


def _json(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        return {}
    try:
        got = json.loads(match.group(0))
    except ValueError:
        return {}
    return got if isinstance(got, dict) else {}


def _usable_query(query: str, *, content_checks: bool = True) -> bool:
    # The same bar `search_gifs` holds her own queries to: short, and nothing
    # that looks like an identifier leaves for Giphy.
    return (bool(query) and len(query) <= 60 and len(query.split()) <= 6
            and scan_identifiers(query, content_checks=content_checks) is None)


class ReactionPicker:
    """Never raises and never blocks past `timeout_seconds`; `None` is «no
    GIF», and it is the answer to every failure."""

    def __init__(
        self, *, client: ModelClient, prompts: PromptSource, gifs: GifSource,
        metrics: Counter, timeout_seconds: float = 8.0,
        content_checks: bool = True,
    ) -> None:
        self._content_checks = content_checks
        self._client = client
        self._prompts = prompts
        self._gifs = gifs
        self._metrics = metrics
        self._timeout_seconds = timeout_seconds

    def _count(self, outcome: str) -> None:
        self._metrics.inc("nora_reaction_total", outcome=outcome)

    async def pick(self, *, recent: str, reply: str) -> Reaction | None:
        try:
            return await asyncio.wait_for(
                self._pick(recent=recent, reply=reply), timeout=self._timeout_seconds,
            )
        except TimeoutError:
            self._count("timeout")
        except Exception:
            log.exception("reaction pick failed; sending the reply without a GIF")
            self._count("error")
        return None

    async def _pick(self, *, recent: str, reply: str) -> Reaction | None:
        instruction = (self._prompts.get(applies_to=AUDIENCE).doctrine_text or "").strip()
        if not instruction or not reply.strip():
            self._count("no_instruction" if not instruction else "none")
            return None
        material = [
            restamp(ContextBlock(
                name="recent_chat", body=recent, when_empty="render",
            ), _ROW),
            restamp(ContextBlock(
                name="nora_reply", body=escape(reply), when_empty="render",
            ), _ROW),
        ]

        first = await self._client.complete(
            system=instruction,
            prompt=f"{assemble(material, sep=_SEP)}\n\nAnswer the first question.",
        )
        if first.finish_reason == "length":
            # A cut-off body can still parse into something; it is not the
            # model declining, so it is not counted as `none`.
            self._count("truncated")
            return None
        verdict = _json(first.content)
        mode = verdict.get("mode")
        query = " ".join(str(verdict.get("query") or "").split())
        if mode not in MODES:
            self._count("none")
            return None
        if not _usable_query(query, content_checks=self._content_checks):
            self._count("bad_query")
            return None

        found = await self._gifs.search(query=query)
        if not found:
            self._count("no_candidates")
            return None
        listing = json.dumps(
            [{"id": c.id, "title": c.title, "description": c.alt_text} for c in found],
            ensure_ascii=False,
        )
        candidates = restamp(ContextBlock(
            name="gif_candidates", body=escape(listing), attrs=(("query", query),),
            when_empty="render",
        ), _ROW)
        second = await self._client.complete(
            system=instruction,
            prompt=(
                f"{assemble([*material, candidates], sep=_SEP)}"
                "\n\nAnswer the second question."
            ),
        )
        chosen = {c.id: c for c in found}.get(str(_json(second.content).get("id") or ""))
        if chosen is None:
            self._count("no_pick")
            return None
        attachment = await self._gifs.fetch(candidate=chosen)
        if attachment is None:
            self._count("no_bytes")
            return None
        self._count(mode)
        return Reaction(mode=mode, query=query, candidate=chosen, attachment=attachment)
