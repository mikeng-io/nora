"""The reaction picker: whether a GIF makes her reply land better, and which.

**Why this is its own call and not a tool she chooses.** She has had
`search_gifs`/`send_gif` since 2026-08-06 and sent three GIFs in seven weeks,
all when somebody asked for one. Replaying 40 recorded tag turns against the
deployed `nora` alias (2026-09-24, ~700 calls over seven arms, one variable
per arm) put the reason past argument:

    arm                                             unprompted GIF replies
    as recorded                                     0/80
    doctrine telling her the room talks in GIFs     0/80
    + `send_reply` no longer calling itself the
      ONLY way to reply                             0/80
    + a `gif` field on `send_reply` itself          2/128
    + two GIFs of her own already in the window     0/101
    the same turns, «send 個 gif 嚟啦» appended      12/12

So she CAN (the last row, and the GIFs were good) and, with the reply as the
first call of a no-reasoning turn, she does not: an optional garnish on the
one verb a turn must end with is never the thing a cheap model spends its
call on, however it is framed. The choice needed a moment of its own. Asked
as its OWN question, over the last few lines and the reply she already wrote,
the same model put a GIF on 17 of 104 replies, every one of them on teasing
or banter and none on the practical answers.

**It never touches her words.** It runs after `send_reply` has her text and
before delivery, and it can only put a GIF under the text (`with`) or in its
place (`alone`). Everything else, and every failure, is the text exactly as
she wrote it: this is a garnish, so the fail-closed direction is «no GIF»,
never «no reply». The whole pick is bounded by one timeout.

The instruction is `reaction.md` (doctrine audience `reaction`, hot-reloaded,
never shown to her); the candidates it chooses between are Giphy's own
described results, as `search_gifs` returns them to her, so the rule in
design/proposed/15 section 2 holds here too: a GIF is chosen from its
description, never sent because it came first.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from nora.domain.blocks import speaker_name
from nora.domain.media import GifCandidate
from nora.domain.models import TranscriptRow
from nora.domain.ports import GifSource

from .egress import scan_identifiers

log = logging.getLogger("nora.agent.reaction")

AUDIENCE = "reaction"
MODES = ("with", "alone")

#: How many of the room's latest lines the picker reads. A reaction answers
#: the moment, not the thread; the prototype that measured 17/104 read 8.
RECENT_LINES = 8


class Completion(Protocol):
    content: str


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


def render_recent(rows: Sequence[TranscriptRow], *, display_name: str = "Nora") -> str:
    """The room's last few lines as `name: body`, the picker's whole view of
    the moment. Bodies are one line each; an empty body (a bare attachment)
    is skipped rather than rendered as a speaker saying nothing."""
    lines = []
    for row in rows[-RECENT_LINES:]:
        body = " / ".join((row.body or "").split("\n")).strip()
        if not body:
            continue
        who = display_name if row.is_nora else speaker_name(
            getattr(row, "person_name", None), row.sender_name,
        )
        lines.append(f"{who}: {body[:300]}")
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


def _usable_query(query: str) -> bool:
    # The same bar `search_gifs` holds her own queries to: short, and nothing
    # that looks like an identifier leaves for Giphy.
    return (bool(query) and len(query) <= 60 and len(query.split()) <= 6
            and scan_identifiers(query) is None)


class ReactionPicker:
    """Never raises and never blocks past `timeout_seconds`; `None` is «no
    GIF», and it is the answer to every failure."""

    def __init__(
        self, *, client: ModelClient, prompts: PromptSource, gifs: GifSource,
        metrics: Counter, timeout_seconds: float = 8.0,
    ) -> None:
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
        material = (f"<recent_chat>\n{recent}\n</recent_chat>\n"
                    f"<nora_reply>\n{reply}\n</nora_reply>")

        first = await self._client.complete(
            system=instruction, prompt=f"{material}\n\nAnswer the first question.",
        )
        verdict = _json(first.content)
        mode = verdict.get("mode")
        query = " ".join(str(verdict.get("query") or "").split())
        if mode not in MODES:
            self._count("none")
            return None
        if not _usable_query(query):
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
        second = await self._client.complete(
            system=instruction,
            prompt=(f"{material}\n<candidates query=\"{query}\">\n{listing}\n"
                    "</candidates>\n\nAnswer the second question."),
        )
        chosen = {c.id: c for c in found}.get(str(_json(second.content).get("id") or ""))
        if chosen is None:
            self._count("no_pick")
            return None
        self._count(mode)
        return Reaction(mode=mode, query=query, candidate=chosen)
