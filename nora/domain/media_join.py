"""The media join: `[image a3f2c1]` becomes `[image a3f2c1: …]`, for EVERY
transcript a model is about to read.

**One join, not a method on the tag assembler.** Every transcript consumer
(the tag and fire paths, the standing fold, the note tier's adaptors, lazy
adoption's Honcho back-feed) must see what a photo, sticker or voice note
showed; a join living in one builder leaves the others blind to it.

**Why the DOMAIN layer.** `nora/language/` sits two layers below `nora/agent/`
in the import contract, so a join living beside `scan_identifiers` could not
be reached by the note tier. The screen is INJECTED instead (`screen`), which
puts this at the bottom of the graph where every caller can import it and
keeps the identifier rule itself in one place. It is also the test-seam
policy's own shape: a collaborator through `__init__`, not a module-level
import somebody could forget to apply.

**The screen is not optional and there is no path that skips it.** Every caller
gets the screened text or the bare handle; a caller cannot ask for the raw
detail, because there is no method that returns one.

**Scoped by `media_json`, never by the body alone.** A handle is six hex
characters and a body is sender-supplied, so resolving `[image a3f2c1]` out of
prose would let a member type another room's handle and have its description
rendered into this one. `media_json` is written by the custody stage from bytes
Nora actually held for THAT row, so it is the evidence that the row's own
conversation holds those bytes. A row with no `media_json` contributes nothing
(`UnattributedRow` carries the column for that reason).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Literal, Protocol, TypeVar, get_args

from nora.domain.media import (
    CONTENT_LABELS,
    HANDLE_CHARS,
    MediaObject,
    enrich_placeholders,
    label_agrees,
    label_from_media_type,
    media_detail,
    placeholders_in,
    sanitize_description,
)
from nora.domain.models import TranscriptRow, UnattributedRow
from nora.domain.ports import MediaLookup, MediaObjectStore

log = logging.getLogger("nora.domain.media_join")

# The two row shapes a transcript is built from. A VALUE-constrained
# TypeVar rather than a bound: mypy then checks `replace` against each concrete
# dataclass, which a `Protocol` bound could not do, and a third row type is a
# deliberate edit here rather than something that silently type-checks.
Row = TypeVar("Row", TranscriptRow, UnattributedRow)

# What `scan_identifiers` is: text in, the KIND of identifier found, or None.
# Injected rather than imported (module docstring).
Screen = Callable[[str], str | None]

#: WHO paid for a join. One named vocabulary, and the metric's help text is
#: derived from it (`nora.obs.metric_specs`) rather than hand-copied. The two
#: histograms are labelled by this, the only thing separating a tag window
#: from a thousand-row backfill in a series whose buckets differ by two orders
#: of magnitude.
#:
#: A `Literal` plus `get_args`, AND a runtime check in `_count`: `nora/domain`
#: is inside the strict mypy override, but not every call site is in a package
#: it reaches, so a static-only guard would be a claim.
JoinPath = Literal[
    "tag", "fire", "debrief", "dreamer",
    "adoption", "load_transcript", "relevance_gate",
    # The eval seam's two readers (`nora.subject`): its dream pass, and the
    # window a judge grades a reply against. `nora.subject` is outside the
    # gate's mypy, so an unlisted label is caught only by `_count` at runtime.
    "dreamer_eval", "judge_source",
    # `nora memory backfill` (DEC-214): a unit's history replayed into its own
    # Honcho workspace, joined for adoption's reason — it feeds memory.
    "memory_backfill",
    "unknown",
]
JOIN_PATHS: tuple[str, ...] = get_args(JoinPath)

#: How many distinct blobs one QUERY may ask about. A bound on the round trip,
#: not on how much of the window gets served: `enrich` batches, and every blob
#: is asked about. Truncating instead would lose data on the one caller that is
#: a backfill: `RecordStore.unattributed` returns rows OLDEST FIRST
#: (`nora/infra/sql/audit.py` documents the ordering as load-bearing), and
#: adoption feeds each row to Honcho and then calls `set_person_id`, which
#: removes it from every future scan, so a blob left as a bare handle there is
#: never revisited. The number of queries is bounded by the CALLER's own row
#: limit, which every caller already has.
DEFAULT_QUERY_BLOBS = 200

#: How long ONE query may take before the join gives up and renders the bare
#: handles. Short, because that degrade is cheap and correct: the row already
#: says `[image a3f2c1]`, so a join that does not happen claims nothing. The
#: two slot-held callers pass `[standing] store_timeout_seconds` instead.
DEFAULT_TIMEOUT_SECONDS = 3.0


class JoinMetrics(Protocol):
    """The two verbs this class uses, named so `nora/domain` stays inside the
    strict mypy override.

    `Any` would opt the class out of it, and the failure mode is specific:
    `Metrics.inc` raises `KeyError` for an undeclared name, the raise lands
    inside `enrich`'s own `except`, and every render on every path degrades to
    bare handles with nothing said.
    """

    def inc(self, name: str, value: float = 1.0, **labels: object) -> None: ...

    def observe(self, name: str, value: float, **labels: object) -> None: ...


class MediaJoin:
    """Renders each row's stored comprehension into its own placeholders.

    One instance per process, built by the composition root and handed to every
    transcript builder, so «what a photo showed» is one answer.

    `render_chars` bounds what ONE attachment may spend of a prompt. It does not
    apply to a voice note, whose detail is the words somebody actually said,
    and it never applies to the CAVEAT, which is appended after the clip: a
    caveat inside the bound is one a rich description pushes out, exactly when
    a confident partial read does the most damage.
    """

    def __init__(
        self,
        *,
        store: MediaObjectStore,
        screen: Screen,
        metrics: JoinMetrics,
        render_chars: int = 240,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        query_blobs: int = DEFAULT_QUERY_BLOBS,
    ) -> None:
        self._store = store
        self._screen = screen
        self._metrics = metrics
        self._render_chars = render_chars
        self._timeout = timeout_seconds
        self._query_blobs = max(1, query_blobs)

    async def enrich(
        self, rows: Sequence[Row], *, path: JoinPath = "unknown",
    ) -> tuple[Row, ...]:
        """The rows with every resolvable placeholder rendered, or the rows.

        `path` names the CALLER. Callers differ in cost: a tag window is a few
        blobs and must stay under a reply's latency, while adoption's is a
        thousand-row backfill at process start. One unlabelled series would put
        both in the same histogram, where the backfill saturates `+Inf`.

        Total. The store port promises never to raise, so the catch below is
        belt and braces, but this runs on the critical path of a tagged turn,
        and an implementation that broke the promise must cost a thinner prompt
        rather than the reply. It is COUNTED, not merely logged: container logs
        are not on the docker log driver, so a permanently failing join would
        otherwise be silent on the deployment.
        """
        try:
            return await self._enrich(rows, path=path)
        except Exception:
            log.exception("media join failed; rendering the bare handles")
            self._count(path, "unavailable", "error")
            return tuple(rows)

    async def _enrich(self, rows: Sequence[Row], *, path: str) -> tuple[Row, ...]:
        if not rows:
            return tuple(rows)
        # sha256 -> handle, from `media_json` ONLY (see the module docstring).
        wanted: dict[str, str] = {}
        for row in rows:
            for sha in shas_in(row.media_json):
                wanted[sha] = sha[:HANDLE_CHARS]
        if not wanted:
            return tuple(rows)
        # The label comes from the record, and the body only refines it.
        # `body` is sender-supplied: a member who types the literal string
        # `[voice note a3f2c1]` would otherwise win the last-write-wins map
        # keyed on the handle, and `voice note` is the one label whose detail
        # is rendered UNCLIPPED, so a typed string would turn off
        # `comprehension_render_chars`, the only bound on per-attachment prompt
        # spend. A claimed label is accepted only when it AGREES with the media
        # type the custody stage recorded.
        claimed: dict[str, str] = {}
        for row in rows:
            for label, handle in placeholders_in(row.body or ""):
                claimed[handle] = label

        started = time.perf_counter()
        shas = tuple(wanted)
        objects: dict[str, MediaObject] = {}
        # Batched, and every blob is asked about (`DEFAULT_QUERY_BLOBS`).
        for start in range(0, len(shas), self._query_blobs):
            found = await self._lookup(shas[start:start + self._query_blobs])
            if not found.available:
                # The index could not be read. Not «nothing has been read
                # yet»: a window of ten photos rendering bare because the
                # database is down is a different event from ten nobody has
                # looked at, and only one is worth waking somebody for (R-27).
                # A partial answer is refused whole: rendering the batches
                # that did land would be a window that looks complete.
                self._count(path, "unavailable", found.reason or "store")
                return tuple(rows)
            objects.update(found.objects)

        details: dict[str, str] = {}
        for sha, handle in wanted.items():
            obj = objects.get(sha)
            if obj is None or not obj.comprehended:
                continue
            record_label = label_from_media_type(obj.media_type)
            asked = claimed.get(handle, "")
            label = asked if label_agrees(asked, obj.media_type) else record_label
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
        # The cost, measured where it is paid and labelled by who paid it.
        # Observed only when the join actually ran, so a window with no media
        # never dilutes it.
        self._metrics.observe(
            "nora_media_join_seconds", time.perf_counter() - started, path=path)
        # `len(wanted)`, the true count, so the histogram can size
        # `DEFAULT_QUERY_BLOBS`.
        self._metrics.observe("nora_media_blobs_joined", len(wanted), path=path)
        self._count(path, "ok" if details else "nothing_found", "")
        if not details:
            return tuple(rows)
        return tuple(
            replace(row, body=enrich_placeholders(row.body or "", details))
            if row.body else row
            for row in rows
        )

    async def _lookup(self, shas: tuple[str, ...]) -> MediaLookup:
        """One query, BOUNDED. `enrich` catches exceptions; it cannot catch a
        hang. `nora/infra/sql/engine.py` sets no `command_timeout`, so a
        slow-but-alive Postgres (a pool saturated by a burst of comprehension
        jobs, say) would hold every tag reply until the agent loop's own budget
        killed the turn as `invocation_timeout`, where it should degrade to
        bare handles in milliseconds. The bound lives here rather than at each
        call site so no caller can omit it."""
        return await asyncio.wait_for(
            self._store.lookup(sha256s=shas), timeout=self._timeout,
        )

    def _count(self, path: str, status: str, reason: str) -> None:
        # The runtime half of the vocabulary check. `Metrics.inc` would take
        # any string, and some call sites are in packages `make gate`'s mypy
        # does not cover, so a typo'd label would render as its own series.
        if path not in JOIN_PATHS:
            log.error("media join: unknown path label %r", path)
            path = "unknown"
        self._metrics.inc(
            "nora_media_join_total", path=path, status=status, reason=reason)


def shas_in(media_json: str | None) -> tuple[str, ...]:
    """Every sha256 in one row's `messages.media_json`, or `()`. Public
    because the memory index reads a message's attachments with it too
    (`nora.infra.sql.memory_index`), so the two cannot parse the column apart.

    Total, like everything else on this path: a row whose JSON does not decode
    contributes nothing rather than raising into a turn. The column is written
    by `nora.domain.media.media_json`, so a malformed value means something
    else wrote it, which is a reason to render the bare handle, not to fail.
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
