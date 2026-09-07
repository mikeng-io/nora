"""The media join — `[image a3f2c1]` becomes `[image a3f2c1: …]`, for EVERY
transcript a model is about to read (issue #116, operator 2026-09-08).

**Why this module exists rather than a method on the tag assembler.** The first
cut of #116 put the join in `nora/engagement/context.py` and nowhere else, so
exactly one of six transcript builders could see what a photo showed. On master
the sticker description and the voice-note transcript were baked into
`messages.body`, which meant EVERY consumer got them — the fire path, the
standing fold, the note tier's two adaptors, lazy adoption's Honcho back-feed.
Moving them behind a join one consumer performs was a regression on both flag
settings, and it defeated the point: the note tier is one of the consumers that
could no longer see a photo description, and «the group replied around ten
photos and memory has no idea what they showed» is the story the issue was
opened on.

**Why the DOMAIN layer.** `nora/language/` sits two layers below `nora/agent/`
in the import contract, so a join living beside `scan_identifiers` could never
be reached by the note tier. The screen is INJECTED instead (`screen`), which
puts this at the bottom of the graph where all six callers can import it and
keeps the identifier rule itself in one place. It is also the test-seam policy's
own shape: a collaborator through `__init__`, not a module-level import
somebody could forget to apply.

**The screen is not optional and there is no path that skips it.** Every caller
gets the screened text or the bare handle; a caller cannot ask for the raw
detail, because there is no method that returns one.

**Scoped by `media_json`, never by the body alone.** A handle is six hex
characters and a body is sender-supplied, so resolving `[image a3f2c1]` out of
prose would let a member type another room's handle and have its description
rendered into this one. `media_json` is written by the custody stage from bytes
Nora actually held for THAT row, so it is the evidence that the row's own
conversation holds those bytes. A row with no `media_json` contributes nothing,
which is why `UnattributedRow` had to gain the column before adoption could
join at all.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Any, TypeVar

from nora.domain.media import (
    CONTENT_LABELS,
    HANDLE_CHARS,
    enrich_placeholders,
    media_detail,
    placeholders_in,
    sanitize_description,
)
from nora.domain.models import TranscriptRow, UnattributedRow
from nora.domain.ports import MediaObjectStore

log = logging.getLogger("nora.domain.media_join")

# The two row shapes a transcript is ever built from. A VALUE-constrained
# TypeVar rather than a bound: mypy then checks `replace` against each concrete
# dataclass, which a `Protocol` bound could not do, and a third row type is a
# deliberate edit here rather than something that silently type-checks.
Row = TypeVar("Row", TranscriptRow, UnattributedRow)

# What `scan_identifiers` is: text in, the KIND of identifier found, or None.
# Injected rather than imported — see the module docstring's layering note.
Screen = Callable[[str], str | None]


class MediaJoin:
    """Renders each row's stored comprehension into its own placeholders.

    One instance per process, built by the composition root and handed to every
    transcript builder, so «what a photo showed» is one answer rather than six.

    `render_chars` bounds what ONE attachment may spend of a prompt. It does not
    apply to a voice note, whose detail is the words somebody actually said
    (issue #116 decision 4), and it never applies to the CAVEAT, which is
    appended after the clip — a caveat inside the bound is one a rich
    description pushes out, exactly when a confident partial read does the most
    damage.
    """

    def __init__(
        self,
        *,
        store: MediaObjectStore,
        screen: Screen,
        metrics: Any,
        render_chars: int = 240,
    ) -> None:
        self._store = store
        self._screen = screen
        self._metrics = metrics
        self._render_chars = render_chars

    async def enrich(self, rows: Sequence[Row]) -> tuple[Row, ...]:
        """The rows with every resolvable placeholder rendered, or the rows.

        Total. The store port promises never to raise (its contract suite pins
        that both implementations answer empty rather than throwing), so the
        catch below is belt for those braces — but this runs on the critical
        path of a tagged turn, and a fake or a future implementation that broke
        the promise must cost a thinner prompt rather than the reply.
        """
        try:
            return await self._enrich(rows)
        except Exception:
            log.exception("media join failed; rendering the bare handles")
            return tuple(rows)

    async def _enrich(self, rows: Sequence[Row]) -> tuple[Row, ...]:
        if not rows:
            return tuple(rows)
        # sha256 -> handle, from `media_json` ONLY (see the module docstring).
        wanted: dict[str, str] = {}
        for row in rows:
            for sha in _shas(getattr(row, "media_json", None)):
                wanted[sha] = sha[:HANDLE_CHARS]
        if not wanted:
            return tuple(rows)
        # handle -> label, read back off the body the custody stage wrote.
        # `voice note` and `audio` are the same top-level media type and
        # different events, and it is the LABEL that decides whether the
        # transcript renders somebody's words or a summary of them — so it is
        # taken from the record rather than re-derived into a second opinion.
        labels: dict[str, str] = {}
        for row in rows:
            for label, handle in placeholders_in(row.body or ""):
                labels[handle] = label

        started = time.perf_counter()
        details: dict[str, str] = {}
        for sha, handle in wanted.items():
            obj = await self._store.get(sha256=sha)
            if obj is None or not obj.comprehended:
                continue
            label = labels.get(handle, "")
            detail = media_detail(
                label=label, title=obj.title, description=obj.description,
                content=obj.content,
            )
            clip = 0 if label in CONTENT_LABELS else self._render_chars
            detail = sanitize_description(detail, max_chars=clip)
            caveat = sanitize_description(obj.caveat)
            if caveat:
                detail = f"{detail} {caveat}".strip()
            if not detail:
                continue
            kind = self._screen(detail)
            if kind is not None:
                log.warning(
                    "a media comprehension would have carried a %s identifier "
                    "— rendering the bare handle", kind,
                )
                self._metrics.inc("nora_egress_blocks_total", kind=kind)
                continue
            details[handle] = detail
        # THE COST, measured where it is paid. `design/19` §10 names this join
        # as what would reopen the decision — one sequential `get` per distinct
        # blob, now on SIX paths rather than one — and a reopening condition
        # nothing can read is a hope rather than a condition. Observed only when
        # the join actually ran, so a window with no media never dilutes it.
        self._metrics.observe(
            "nora_media_join_seconds", time.perf_counter() - started)
        self._metrics.observe("nora_media_blobs_joined", len(wanted))
        if not details:
            return tuple(rows)
        return tuple(
            replace(row, body=enrich_placeholders(row.body or "", details))
            if row.body else row
            for row in rows
        )


def _shas(media_json: str | None) -> tuple[str, ...]:
    """Every sha256 in one row's `messages.media_json`, or `()`.

    Total, like everything else on this path: a row whose JSON does not decode
    contributes nothing rather than raising into a turn. The column is written
    by `nora.domain.media.media_json` and pinned by the audit-store contract
    suite, so a malformed value means something else wrote it — which is a
    reason to render the bare handle, not a reason to fail.
    """
    if not media_json:
        return ()
    try:
        blobs = json.loads(media_json)
    except (ValueError, TypeError):
        return ()
    if not isinstance(blobs, list):
        return ()
    out = []
    for blob in blobs:
        if isinstance(blob, dict):
            sha = str(blob.get("sha256", ""))
            if sha:
                out.append(sha)
    return tuple(out)
