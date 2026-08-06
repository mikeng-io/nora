"""Best-effort Honcho v3 write path (spec §C.5's corrected session model).

Honcho is never load-bearing: every method here logs and returns a benign value
(`None`, `False`, or a no-op) on any failure so a Honcho outage never blocks the
ambient reply path.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, cast

import aiohttp

from nora.domain.ports import MemoryHealth, Recollection

log = logging.getLogger("nora.honcho")

# `[2026-08-06 11:04:13] probe_zzz is not coming to events on Thursdays`.
#
# THE STAMP IS BATCH-LEVEL, not per-conclusion. This comment used to say Honcho
# "stamps each conclusion with when it drew it", which is wrong in the way that
# matters: it is `internal_metadata["message_created_at"]` — the created_at of
# the MESSAGE whose arrival triggered the derivation — copied onto every
# conclusion in that batch (`src/utils/representation.py::
# _safe_datetime_from_metadata`, honcho 3.0.11). Counted on the live instance:
# `probe_zzz`'s 102 documents carry SIX distinct stamps, one of them shared by
# 77 of them. So it is not a recency signal at conclusion granularity and
# nothing downstream may sort or age by it.
#
# It is dropped for the reason it always was: provenance for a human reading
# the store, not something to read out in a group chat — in front of every line
# it invites the model to quote it back as a fact about the conversation.
_STAMP = re.compile(r"^\[[^\]]*\]\s*")
# `[id:abc123] ` — only emitted for `include_ids=True`, which this client never
# asks for. Stripped anyway: it costs one regex and the alternative, if the
# default ever flips, is a nanoid read out to the group.
_ID = re.compile(r"^\[id:[^\]]*\]\s*")
# ` **Pattern** [high]: <conclusion>` and ` **CONTRADICTION**: <content>` — the
# two derived sections that label their conclusion inline instead of stamping
# it. The confidence token is dropped with the label: `recall` hands the model a
# flat list of things Nora knows, and `high`/`medium`/`low` is a number she
# cannot calibrate, act on, or explain to a group.
_PATTERN = re.compile(r"^\*\*Pattern\*\*\s*\[[^\]]*\]:\s*")
_CONTRADICTION = re.compile(r"^\*\*CONTRADICTION\*\*:\s*")


def _conclusions(representation: Any) -> tuple[str, ...]:
    """One markdown blob → one conclusion per OBSERVATION. Never one per line.

    ## What is actually on the wire

    `Representation.format_as_markdown` (honcho 3.0.11, read out of the running
    container rather than out of docs) emits FOUR sections, and only the first
    is one-line-per-fact::

        ## Explicit Observations
        [<stamp>] <content>

        ## Deductive Observations
        [<stamp>] <conclusion>
           Premises:
           - <premise>

        ## Inductive Observations
         **Pattern** [<confidence>]: <conclusion>
           **Type**: <pattern_type>
           **Sources**:
           - <source>
           - ... and <n> more

        ## Contradictions
         **CONTRADICTION**: <content>
           **Conflicting statements**:
           - <source>

    The premises under a deduction and the sources under a pattern are the
    observations it was BUILT FROM. They are not independent facts, and
    returning them flattened both inflates what Nora appears to know and hands
    the model the same fact twice. Measured before this was written: the live
    body for `probe_zzz` produced **151 "conclusions" from 25 documents**, of
    which most were premise and source bullets, and every pattern arrived as
    `Pattern** [high]: …` because the old `.lstrip("-*")` ate markdown bold from
    the left and left the closing asterisks behind.

    Only the Explicit section had ever been tested, because the only fixture was
    `miles_novak`, whose 16 documents are all `level='explicit'`. Nothing had fired
    yet purely because the old call shape (`search_top_k == max_conclusions`)
    zeroed the two channels that surface derived levels — so fixing the call
    shape without fixing this would have SHIPPED the mangling.

    ## Why prose is still parsed at all

    `POST /v3/workspaces/{ws}/conclusions/query` returns structured `Conclusion`
    objects (`id`, `content`, `observer`, `observed`, `session_id`,
    `created_at`) with clean `content` for every level and no parser needed. It
    was evaluated against this and REJECTED, for one reason that is not about
    tidiness: it is **semantic search and nothing else**. Its handler is a
    single `crud.query_documents` call over one embedding of `body.query`
    (`src/routers/conclusions.py::query_conclusions`), so it has no access to
    the most-derived channel or the recent channel that
    `get_working_representation` blends. Adopting it would hard-code the exact
    failure the call shape below exists to undo — one channel of three, deciding
    everything — and would trade a parser for a permanent loss of coverage.
    It also carries no `distance` in its response, so it does not even buy back
    a ranking signal; `distance` there is an input threshold, exactly as
    `search_max_distance` is here.

    ## The rules, and why they are these rules

    A conclusion line is never a bullet and never indented; a premise, a source
    and a sub-label are always one or the other. That is the discriminator, and
    it is a property of the emitter rather than of any one body. An unrecognised
    bold label is DROPPED rather than passed through, so a level Honcho adds
    upstream degrades to silence instead of arriving as a fact Nora believes.

    Non-string input (a shape change upstream) yields `()` — the caller still
    knows the request itself succeeded, which is the distinction that matters.
    """
    if not isinstance(representation, str):
        return ()
    out: list[str] = []
    for raw in representation.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # A premise, a source, or `- ... and 3 more`. No conclusion at any of
        # the four levels is ever emitted as a bullet.
        if line.startswith(("- ", "* ")) or line in ("-", "*"):
            continue
        # `   Premises:`, `   **Type**: …`, `   **Sources**:`,
        # `   **Conflicting statements**:` — the sub-labels, all indented by the
        # emitter. Conclusion lines start at column 0 (explicit, deductive) or
        # after a single space (inductive, contradiction).
        if raw.startswith("   "):
            continue
        line = _PATTERN.sub("", _STAMP.sub("", _ID.sub("", line)), count=1)
        line = _CONTRADICTION.sub("", line, count=1).strip()
        # Anything still wearing a bold label is a shape this parser has not
        # been taught. `Pattern** [high]: …` is what passing it through looks
        # like, and it is what this function was rewritten to stop emitting.
        if not line or line.startswith("**"):
            continue
        out.append(line)
    return tuple(out)


class HonchoClient:
    def __init__(
        self,
        base_url: str,
        workspace_id: str,
        *,
        feed_timeout: float = 2.0,
        card_timeout: float = 3.0,
        representation_timeout: float = 3.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._ws = workspace_id
        self._feed_timeout = feed_timeout
        self._card_timeout = card_timeout
        # Measured at 0.19 to 0.26 s against the live instance on 2026-08-06, so
        # 3 s is ~12x headroom rather than a guess. It is a ceiling on a call
        # that happens INSIDE a live turn (the `recall` tool), which is why it
        # is bounded at all: the tag already costs a model round trip per tool
        # call, and a hung memory service must not add to that.
        self._representation_timeout = representation_timeout
        self._workspace_ready = False
        self._known_peers: set[str] = set()
        self._sessions: dict[str, str] = {}
        # One asyncio.Lock per peer id, not one global lock — different peers must
        # still proceed concurrently. Shared between ensure_peer and ensure_session:
        # both are check-then-act over per-peer state, and person-centric identity
        # means the same peer id can be hit from two platforms at the same moment.
        # `_peer_locks`, `_known_peers`, and `_sessions` are all unbounded for the
        # lifetime of this client — deliberately not fixed here, since a private
        # group chat bounds them by human count, not message volume. A future TTL
        # sweep across all three is the forward pointer if that stops being true
        # (e.g. a multi-tenant deployment with peer churn).
        self._peer_locks: dict[str, asyncio.Lock] = {}

    def _peer_lock(self, peer_id: str) -> asyncio.Lock:
        lock = self._peer_locks.get(peer_id)
        if lock is None:
            lock = self._peer_locks[peer_id] = asyncio.Lock()
        return lock

    def _url(self, path: str) -> str:
        return f"{self._base}/v3/workspaces/{self._ws}{path}"

    async def _post(
        self,
        url: str,
        payload: dict[str, Any],
        timeout: float,  # noqa: ASYNC109 -- caller budget; asyncio.timeout is Phase 6
    ) -> dict[str, Any] | None:
        try:
            async with (
                aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session,
                session.post(url, json=payload) as r,
            ):
                if r.status >= 400:
                    log.warning("honcho POST %s -> %s", url, r.status)
                    return None
                return cast(dict[str, Any], await r.json())
        except Exception as e:  # noqa: BLE001 — Honcho is never load-bearing
            log.warning("honcho POST %s failed: %s", url, e)
            return None

    async def _get(
        self,
        url: str,
        timeout: float,  # noqa: ASYNC109 -- caller budget; asyncio.timeout is Phase 6
    ) -> dict[str, Any] | None:
        try:
            async with (
                aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session,
                session.get(url) as r,
            ):
                if r.status >= 400:
                    log.warning("honcho GET %s -> %s", url, r.status)
                    return None
                return cast(dict[str, Any], await r.json())
        except Exception as e:  # noqa: BLE001 — Honcho is never load-bearing
            log.warning("honcho GET %s failed: %s", url, e)
            return None

    async def ensure_workspace(self) -> None:
        if self._workspace_ready:
            return
        result = await self._post(
            f"{self._base}/v3/workspaces", {"id": self._ws}, self._card_timeout
        )
        if result is not None:
            self._workspace_ready = True

    async def _peer_exists(self, peer_id: str) -> bool:
        return peer_id in self._known_peers

    async def _create_peer(self, peer_id: str) -> None:
        result = await self._post(self._url("/peers"), {"id": peer_id}, self._card_timeout)
        if result is not None:
            self._known_peers.add(peer_id)

    async def ensure_peer(self, *, peer_id: str) -> None:
        # Check-then-act without this lock creates two peers for one id when
        # two platforms feed the same person at the same moment — and
        # person-centric identity guarantees that happens.
        async with self._peer_lock(peer_id):
            if await self._peer_exists(peer_id):
                return
            await self._create_peer(peer_id)

    async def ensure_session(self, peer_id: str) -> str | None:
        """The peer's one session id, created on first use.

        Sessions are created at the WORKSPACE level. This used to POST to
        `/peers/{peer_id}/sessions`, which is not a create endpoint at all —
        it LISTS a peer's sessions and answers 200 with a pagination envelope
        (`{"items": [], "total": 0, ...}`). That envelope has no `id`, so this
        returned None every single time, `feed` returned False before posting
        anything, and **Honcho never received one message** for the entire life
        of the deployment. Verified against the live instance: the old path
        answers 200-with-no-id, `POST /sessions` answers 201 with the session.

        It failed invisibly because feeding is best-effort by contract — the
        caller counts `honcho_feed_failure` and moves on, and nothing else
        depends on the result. Both feed counters sat at 0.0.

        The id is deterministic, so it survives a restart without state and
        re-creating it is idempotent (the live instance answers 200 with the
        same id on a repeat POST). The cache below only saves a round trip.
        """
        async with self._peer_lock(peer_id):
            cached = self._sessions.get(peer_id)
            if cached is not None:
                return cached
            session_id = f"nora-{peer_id}"
            result = await self._post(
                self._url("/sessions"), {"id": session_id}, self._feed_timeout
            )
            if result is None:
                return None
            returned = result.get("id")
            if not isinstance(returned, str) or not returned:
                return None
            self._sessions[peer_id] = returned
            return returned

    async def feed(self, *, peer_id: str, text: str) -> bool:
        session_id = await self.ensure_session(peer_id)
        if session_id is None:
            return False
        result = await self._post(
            self._url(f"/sessions/{session_id}/messages"),
            {"messages": [{"content": text, "peer_id": peer_id}]},
            self._feed_timeout,
        )
        return result is not None

    async def peer_card(self, *, peer_id: str) -> list[str]:
        """Durable recall about a peer. [] on any failure — a missing card is normal.

        The field is `peer_card`, and it is a LIST. This read `content` as a
        string, which Honcho has never returned: `GET /peers/{id}/card` answers
        `{"peer_card": [...] | null}`, verified against the live instance and
        against its OpenAPI schema. So this returned `[]` every single time and
        the tagging path's entire recall channel has been dead for the life of
        the deployment — silently, because "a missing card is normal" is a true
        sentence that made the failure indistinguishable from the healthy case.

        Third instance of one bug class, all found in this phase: a shape
        nobody checked against the live service, pinned by a test double that
        invented it (the others: `ensure_session` POSTing to a list endpoint,
        and `parse_event` requiring a `method` key the HTTP transport does not
        send). The test for THIS one asserted `{"content": ...}` — a payload
        Honcho cannot produce.
        """
        result = await self._get(self._url(f"/peers/{peer_id}/card"), self._card_timeout)
        card = (result or {}).get("peer_card")
        if not isinstance(card, list):
            return []
        return [str(line).lstrip("-* ").strip() for line in card if str(line).strip()]

    async def representation(
        self, *, peer_id: str, search_query: str, max_conclusions: int = 5
    ) -> Recollection:
        """What Honcho concludes about one peer, curated to one question.

        `POST /v3/workspaces/{ws}/peers/{peer_id}/representation` with a
        `search_query`, verified against the live instance on 2026-08-06:
        answers 200 in ~0.2 s with `{"representation": "<markdown>"}`.

        THREE THINGS ARE DELIBERATE, and each one is a live-verified fact
        rather than a reading of the docs:

        * **Not `peer_card`.** `GET /peers/{id}/card` returns `{"peer_card":
          null}` even for peers that demonstrably have conclusions — a dead
          endpoint that has been answering "I know nothing" all along. This
          endpoint returns the same peer's conclusions as prose.
        * **No `target`.** The theory-of-mind form (`what does A think of B`)
          404s unless the target peer itself exists, and Nora is not a peer.
        * **An unknown peer is 200 with an EMPTY STRING, never 404.** So the
          transport cannot tell "no such peer" from "nothing known", and the
          only recoverable bit is whether the request succeeded at all. That
          is what `Recollection.reachable` carries, and why this does not
          return `list[str]` like `peer_card` does. `_post` answers `None` for
          both a >=400 and a transport failure, which is exactly the set of
          "could not ask" outcomes.

        `session_id` is deliberately omitted: sessions here are per-peer
        (`ensure_session` builds `nora-<peer_id>`), so scoping to one would
        return a subset of what the unscoped call already returns for that
        same peer.
        """
        result = await self._post(
            self._url(f"/peers/{peer_id}/representation"),
            {
                "search_query": search_query,
                "max_conclusions": max_conclusions,
                "search_top_k": max_conclusions,
            },
            self._representation_timeout,
        )
        if result is None:
            return Recollection(reachable=False)
        return Recollection(
            reachable=True,
            conclusions=_conclusions(result.get("representation")),
        )

    async def health(self) -> MemoryHealth:
        result = await self._get(f"{self._base}/health", self._card_timeout)
        return MemoryHealth(reachable=result is not None)

    async def aclose(self) -> None:
        """No-op today: every request opens and closes its own `aiohttp.ClientSession`
        (see `_post`/`_get`), so there is no persistent session to release. Declared
        now so `MemoryStore.aclose()` has a real implementation to check against —
        the persistence phase is expected to move to a session held for the
        client's lifetime, at which point this stops being a no-op."""
        return None
