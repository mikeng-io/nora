"""Best-effort Honcho v3 write path (spec §C.5's corrected session model).

Honcho is never load-bearing: every method here logs and returns a benign value
(`None`, `False`, or a no-op) on any failure so a Honcho outage never blocks the
ambient reply path.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from collections.abc import Mapping
from typing import Any, cast

import aiohttp

from nora.domain.ports import MemoryHealth, MessageHits, Recollection

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
#
# THE STRIP STAYS — this is A2 of the 2026-08-20 prompt-architecture audit,
# decided rather than inherited. F2 found the real cost of it: `[2026-08-19
# 10:17:08] nora agreed to speak all Chinese after miles_novak requested.` reaches
# the model undated inside `<self_card>` and reads as a standing directive, a
# one-time in-conversation request made permanent in under three minutes. The
# audit offered two fixes and this is the second of them.
#
# Restoring the date was rejected on the paragraph above: 77 of one peer's 102
# documents share ONE stamp, so a per-line date is precision that does not
# exist, and a hedged form («recorded around …») is read as per-fact anyway
# because a date next to a sentence is a date about that sentence. It would
# also mean widening the port — `Recollection.conclusions` is `tuple[str, ...]`
# and the batch identity is already gone by the time the prefill sees a line —
# to carry a number nothing downstream is allowed to sort or age by. Inventing
# the precision is the worse failure of the two.
#
# So the honest statement is «undated», and it is made where the line is
# rendered: `nora.agent.prefill._self_card_block` / `._peer_history_block` say
# so in the block note. Anyone re-litigating the strip must read those two
# first — dropping the note is what re-opens F2, not touching this regex.
_STAMP = re.compile(r"^\[[^\]]*\]\s*")
# `[id:abc123] ` — only emitted for `include_ids=True`, which this client never
# asks for. Stripped anyway: it costs one regex and the alternative, if the
# default ever flips, is a nanoid read out to the group.
_ID = re.compile(r"^\[id:[^\]]*\]\s*")
# ` **Pattern** [high]: <conclusion>` and ` **CONTRADICTION**: <content>` — the
# two derived sections that label their conclusion inline instead of stamping
# it. The confidence token is dropped with the label: `about` hands the
# model a
# flat list of things Nora knows, and `high`/`medium`/`low` is a number she
# cannot calibrate, act on, or explain to a group.
_PATTERN = re.compile(r"^\*\*Pattern\*\*\s*\[[^\]]*\]:\s*")
_CONTRADICTION = re.compile(r"^\*\*CONTRADICTION\*\*:\s*")

# Cosine distance above which a semantic hit is discarded rather than returned.
#
# BRACKETED ON BOTH SIDES against the live instance and the `probe_zzz` corpus
# on 2026-08-06, because a threshold justified from one direction only is a
# guess:
#
#   d       "how does he get to work"      "quantum chromodynamics lattice…"
#   none    5 hits, correct                5 hits — the VW Golf, the rusty
#   0.8     5 hits, correct                Chinese: pure nearest-neighbour noise
#   0.5     5 hits, correct                0 hits
#   0.3     0 hits — kills the real one    0 hits
#
# Without a floor the semantic channel ALWAYS returns `top_k` rows: pgvector
# ranks, it does not judge, so a question about nothing in the corpus comes back
# with the nearest unrelated facts and Nora reads them out as though they were
# an answer. 0.5 is the widest value that rejects that cleanly while keeping
# every real hit. Calibrated against the embedding model, not the deployment,
# which is why it lives here and not in `config.toml`.
_MAX_DISTANCE = 0.5

# Honcho constrains a session id to `^[a-zA-Z0-9_-]+$` (a 422 otherwise, verified
# live 2026-08-08). A Signal group id is STANDARD base64 (`+`, `/`, `=`), and a
# DM key can be a phone number (`+`) — all rejected. This maps the base64
# alphabet to the url-safe one and drops padding, so the common ids stay
# readable in the Honcho UI; anything still non-conforming falls back to a hash.
_SESSION_ID_OK = re.compile(r"^[a-zA-Z0-9_-]+$")
_B64_TO_URLSAFE = str.maketrans({"+": "-", "/": "_", "=": ""})


def _safe_conversation_key(conversation_id: str) -> str:
    """A Honcho-legal session key derived from an opaque conversation id.

    A Signal group id (`Lhof…+…/…=`) 422s the session-create endpoint on its `+`
    `/` `=`, which silently broke the ENTIRE feed/recall path on real groups —
    offline tests used clean ids and never saw it (live-verified 2026-08-08).
    Base64→url-safe keeps the id readable and deterministic (survives restart);
    an id that STILL violates the pattern falls back to a sha256 hex so the key
    is ALWAYS legal rather than a silent feed failure."""
    candidate = conversation_id.translate(_B64_TO_URLSAFE)
    if candidate and _SESSION_ID_OK.match(candidate):
        return candidate
    return hashlib.sha256(conversation_id.encode()).hexdigest()


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


class WorkspaceRouter:
    """A conversation → Honcho workspace map. A Honcho workspace is a TENANT:
    one whole Nora, holding every `unit` at once. It is NOT a unit — that
    equation is what design/24 §S.1 used to assert and what the 2026-09-19
    rename undid, after `NoteCurator._discharge` spent months addressing a
    Honcho workspace named after a unit.

    **`overrides` is dead by decision, not pending (operator, 2026-09-19).**
    One Nora is one Honcho workspace permanently; a second workspace means a
    second Nora. Honcho is multi-tenant and would take a workspace per unit,
    but its PEERS are workspace-scoped, so that would split one person into N
    unrelated person-models and N self-cards, with no way to split the derived
    history that already exists. design/24 §S.4 carries the full reversal and
    the three follow-ups populating this map would still have needed.

    Kept, rather than deleted with the decision, because `default` is the live
    path: every conversation resolves to `cfg.memory.workspace_id` through
    `HonchoClient._workspace_for`, and collapsing that to a bare attribute buys
    nothing. Nothing constructs it with overrides."""

    def __init__(self, default: str, overrides: Mapping[str, str] | None = None) -> None:
        self._default = default
        self._overrides = dict(overrides or {})

    def workspace_for(self, conversation_id: str) -> str:
        return self._overrides.get(conversation_id, self._default)


class HonchoClient:
    def __init__(
        self,
        base_url: str,
        workspace_id: str,
        *,
        ai_peer: str = "nora",
        workspace_router: WorkspaceRouter | None = None,
        feed_timeout: float = 2.0,
        card_timeout: float = 3.0,
        representation_timeout: float = 3.0,
        chat_timeout: float = 25.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._ws = workspace_id
        # A conversation's workspace is resolved through the router (design/24
        # §S). None → every call uses the single `workspace_id` (the default and
        # today's behaviour); a router lets a future per-social-unit map send
        # different conversations to different workspaces without touching this
        # class. `_ws` stays the DEFAULT for peer-keyed calls that carry no
        # conversation (ensure_peer, and a recall where the caller passes no
        # workspace).
        self._router = workspace_router
        # The OBSERVER peer — Nora. Honcho keys memory by (observer, observed)
        # peer pairs (README, "the Honcho loop"), so this is the peer from whose
        # perspective every recall is read: `(observer=nora, observed=person)` is
        # NORA'S model of that person, distinct from the person's self-model. The
        # deriver only builds that pair when Nora joins the session with
        # `observe_others=True` — which is why the client, not just the caller,
        # has to know who Nora is (design/24 §4).
        self._ai_peer = ai_peer
        self._feed_timeout = feed_timeout
        self._card_timeout = card_timeout
        # Measured at 0.19 to 0.26 s against the live instance on 2026-08-06, so
        # 3 s is ~12x headroom rather than a guess. It is a ceiling on a call
        # that happens INSIDE a live turn (`about`), which is why it
        # is bounded at all: the tag already costs a model round trip per tool
        # call, and a hung memory service must not add to that.
        self._representation_timeout = representation_timeout
        # The DIALECTIC chat is a REASONING call — measured ~19 s against the live
        # instance 2026-08-07 (the Honcho-side reasoning model is slow; a faster
        # model is Miles's mimo/M3 tuning). 25 s is headroom over that measurement,
        # deliberately larger than every other Honcho timeout, and it is why chat
        # is NOT an eager in-turn tool yet: 19 s blows the p95≤5s SLO.
        self._chat_timeout = chat_timeout
        self._workspace_ready = False
        self._known_peers: set[str] = set()
        self._sessions: dict[str, str] = {}
        # (session_id, peer_id) pairs already given their observer config, so the
        # add-peers call fires ONCE per peer per session, not every message.
        self._configured_peers: set[tuple[str, str]] = set()
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

    def _url(self, path: str, *, workspace: str | None = None) -> str:
        return f"{self._base}/v3/workspaces/{workspace or self._ws}{path}"

    def _workspace_for(self, conversation_id: str) -> str:
        """The workspace for a conversation — the router's answer, or the single
        default when no router is wired (today, every conversation)."""
        return self._router.workspace_for(conversation_id) if self._router else self._ws

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

    async def ensure_session(self, conversation_id: str) -> str | None:
        """The CONVERSATION's session id, created on first use (Honcho v2,
        design/24). One session per Signal group / DM, holding EVERY speaker in
        that conversation as its own peer — so a human's words and Nora's reply
        share one session while each message stays under its own `peer_id`.

        This REPLACES the per-person `nora-<peer_id>` keying (design/24 §1): a
        per-person session structurally cannot hold a two-speaker conversation,
        which is why Nora's reply had nowhere honest to go (DEC-36). Old
        per-person sessions become legacy — their peer cards carry forward
        (peers are unchanged); nothing writes to them any more.

        Sessions are created at the WORKSPACE level via `POST /sessions` (201
        with the session; the live instance answers 200 with the same id on a
        repeat POST, so re-creating is idempotent). The id is deterministic, so
        it survives a restart without state; the cache only saves a round trip.
        """
        ws = self._workspace_for(conversation_id)
        async with self._peer_lock(f"conv:{conversation_id}"):
            # Nora joins every session as an OBSERVER (observe_others=True), so
            # the deriver builds her model of each human who speaks here; and as
            # observable herself (observe_me=True) so her own self-card can form.
            # Without this the (nora → person) pair is never derived and every
            # recall reads empty — proven live 2026-08-07 (design/24 §4).
            #
            # This runs on the cache HIT path too, NOT only at creation: it is
            # idempotent via `_configured_peers` (a no-op once it has landed), so
            # running it every call means a transient add-peers failure RETRIES on
            # the next message instead of leaving Nora a non-observer of this
            # session for the whole process life. The human speaker's config in
            # `feed` already retries this way; the ai_peer must match it, or one
            # dropped POST silently disables all recall here (review C1, 2026-08-08).
            cached = self._sessions.get(conversation_id)
            if cached is not None:
                await self._configure_peer(
                    cached, self._ai_peer, observe_others=True, observe_me=True,
                    workspace=ws,
                )
                return cached
            session_id = f"nora-conv-{_safe_conversation_key(conversation_id)}"
            result = await self._post(
                self._url("/sessions", workspace=ws), {"id": session_id}, self._feed_timeout
            )
            if result is None:
                return None
            returned = result.get("id")
            if not isinstance(returned, str) or not returned:
                return None
            self._sessions[conversation_id] = returned
            await self._configure_peer(
                returned, self._ai_peer, observe_others=True, observe_me=True,
                workspace=ws,
            )
            return returned

    async def _configure_peer(
        self, session_id: str, peer_id: str, *, observe_others: bool, observe_me: bool,
        workspace: str | None = None,
    ) -> None:
        """Add `peer_id` to `session_id` with its observer config, ONCE per pair.

        Honcho keys memory by (observer, observed) peer pairs, and only derives a
        pair when the observer has `observe_others=True` AND the observed has
        `observe_me=True`. Best-effort: a failure leaves the pair unconfigured so
        the next message retries it — a missed config degrades to «not modelled»,
        never a crash. `POST /sessions/{id}/peers` with `{peer_id: config}`
        (verified against the live add-peers schema, 2026-08-07). `workspace` is
        the conversation's workspace (design/24 §S); None is the default."""
        pair = (session_id, peer_id)
        if pair in self._configured_peers:
            return
        result = await self._post(
            self._url(f"/sessions/{session_id}/peers", workspace=workspace),
            {peer_id: {"observe_others": observe_others, "observe_me": observe_me}},
            self._feed_timeout,
        )
        if result is not None:
            self._configured_peers.add(pair)

    async def feed(
        self,
        *,
        peer_id: str,
        text: str,
        conversation_id: str,
        metadata: dict[str, object] | None = None,
    ) -> bool:
        """Feed one message under its speaker's `peer_id` into the conversation's
        session (design/24 §3). Best-effort: False on any failure, the caller
        counts it and moves on. `metadata` rides on the message for the deriver
        (never the text itself)."""
        session_id = await self.ensure_session(conversation_id)
        if session_id is None:
            return False
        ws = self._workspace_for(conversation_id)
        # A human speaker joins as OBSERVABLE (observe_me=True) so Nora's model of
        # them derives; not as an observer (observe_others=False) — only Nora
        # models others, so the deriver does no N² human-of-human work. Nora's own
        # feed skips this: she was configured at session creation.
        if peer_id != self._ai_peer:
            await self._configure_peer(
                session_id, peer_id, observe_others=False, observe_me=True, workspace=ws,
            )
        message: dict[str, object] = {"content": text, "peer_id": peer_id}
        if metadata:
            message["metadata"] = metadata
        result = await self._post(
            self._url(f"/sessions/{session_id}/messages", workspace=ws),
            {"messages": [message]},
            self._feed_timeout,
        )
        return result is not None

    async def conclude(
        self,
        *,
        peer_id: str,
        content: str,
        target: str,
        workspace_id: str | None = None,
    ) -> bool:
        """Write ONE settled fact into the conclusions store, under an
        (observer, observed) peer pair. Best-effort: False on any failure, the
        caller (the `remember` tool) reports `unavailable` and the reply path
        moves on — Honcho is never load-bearing.

        `peer_id` is the OBSERVER (Nora, `self._ai_peer`), `target` the
        OBSERVED person — the exact mirror of `representation`'s pair
        (design/24 §4), so what this writes is what `about`/`ask_memory` read
        back over the same pair. This is the only legitimate write lane for a
        settled fact about a person: notes are TRACKED UNCERTAINTY and the
        fact lane excludes person descriptions by design, so before this
        existed 「記低」 agreed out loud and landed nowhere (live incident
        2026-08-29).

        No `session_id` is sent. Recall is workspace-wide ON PURPOSE (see
        `representation`): her model of a person blends every conversation she
        shares with them, and disclosure is gated by the room ROSTER at the
        tool layer — not by narrowing the memory query. Scoping the write to
        one session would key visibility to provenance the readers never
        filter on, so the write is stored workspace-wide too: what the write
        lane commits, the read lane can serve, with ONE scope.

        No dedup here either: Honcho's deriver owns that on its side, and a
        repeated line costs one row, never a crash. The timeout is
        `_feed_timeout` (2 s) for the same reason `feed` uses it: the write
        happens INSIDE a live turn, where a hung memory service must not add
        latency the group feels."""
        result = await self._post(
            self._url("/conclusions", workspace=workspace_id),
            {
                "conclusions": [
                    {
                        "content": content,
                        "observer_id": peer_id,
                        "observed_id": target,
                    }
                ]
            },
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
        self,
        *,
        peer_id: str,
        search_query: str,
        max_conclusions: int = 9,
        target: str | None = None,
        workspace_id: str | None = None,
    ) -> Recollection:
        """What Honcho concludes about one peer, curated to one question.

        `POST /v3/workspaces/{ws}/peers/{peer_id}/representation` with a
        `search_query`, verified against the live instance on 2026-08-06:
        answers 200 in ~0.2 s with `{"representation": "<markdown>"}`.

        **`peer_id` is the OBSERVER, `target` the OBSERVED** (design/24 §4).
        With `target` set, this reads `(observer=peer_id, observed=target)` — so
        `representation(peer_id="nora", target="miles_novak")` is NORA'S model of
        Miles, built from messages she observed, distinct from Miles's self-model
        (`target=None` → observer==observed). The theory-of-mind form was once
        dead here ("Nora is not a peer"); Honcho v2 makes Nora a peer that joins
        every session with `observe_others=True`, so the pair now derives —
        proven live 2026-08-07 (Nora's model of a probe peer returned its job and
        hobby from one observed message). No `session_id`: recall is workspace-
        wide ON PURPOSE, so Nora's model of a person blends every conversation
        she shares with them in this workspace (the cross-group knowledge one
        workspace buys); disclosure is gated by the room ROSTER at the tool
        layer, not by narrowing the memory query.

        TWO THINGS REMAIN DELIBERATE, each a live-verified fact not a doc-read:

        * **Not `peer_card`.** `GET /peers/{id}/card` returns `{"peer_card":
          null}` even for peers that demonstrably have conclusions — a dead
          endpoint that has been answering "I know nothing" all along. This
          endpoint returns the same peer's conclusions as prose.
        * **An unknown peer is 200 with an EMPTY STRING, never 404.** So the
          transport cannot tell "no such peer" from "nothing known", and the
          only recoverable bit is whether the request succeeded at all. That
          is what `Recollection.reachable` carries, and why this does not
          return `list[str]` like `peer_card` does. `_post` answers `None` for
          both a >=400 and a transport failure, which is exactly the set of
          "could not ask" outcomes.

        ## `max_conclusions` is a budget split THREE ways, not a row limit

        Read out of `src/crud/representation.py::
        _get_working_representation_internal` (honcho 3.0.11), which spends it
        across three independent queries::

            semantic = min(search_top_k or total // 3, total)   # pgvector rank
            derived  = min(total // 3, total - semantic)        # times_derived DESC
            recent   = total - semantic - derived               # created_at DESC

        This used to send `search_top_k = max_conclusions` and no
        `include_most_frequent`, which computes to `semantic = 5, derived = 0,
        recent = 0`: **the semantic channel ate the entire budget and the other
        two were structurally unreachable.** The derived channel is the one
        ADR 0001 says Honcho exists for — `times_derived` counts how often the
        deduplicator has re-derived the same conclusion, so it ranks the durable
        core of what is known about a person above whatever one question happens
        to embed near.

        `search_top_k = max_conclusions // 3` restores an even three-way blend.
        That is also Honcho's own default when `search_top_k` is omitted, and it
        is sent explicitly anyway: an upstream default is a value that can change
        underneath us without a diff, and the one thing this endpoint has proven
        is that its behaviour is worth pinning from this side. `max(1, …)` keeps
        it inside the API's `ge=1` bound for small budgets.

        `max_conclusions` rose from 5 to 9 in the same change, because the split
        turns it into three budgets rather than one. At 5 the semantic channel
        was **one row**, and measured against `probe_zzz` — a corpus that
        demonstrably contains the answer — one row was not enough to answer «how
        does he get to work»: the nearest hit is *does the morning school run*
        and *drives to work instead of taking the train* is third. 9 gives 3/3/3
        and costs roughly 1,100 characters of tool result, in the same order as
        `load_transcript`'s default and well inside `llm.limits`.
        """
        payload: dict[str, object] = {
            "search_query": search_query,
            "max_conclusions": max_conclusions,
            "search_top_k": max(1, max_conclusions // 3),
            "include_most_frequent": True,
            "search_max_distance": _MAX_DISTANCE,
        }
        if target is not None:
            payload["target"] = target
        result = await self._post(
            self._url(f"/peers/{peer_id}/representation", workspace=workspace_id),
            payload,
            self._representation_timeout,
        )
        if result is None:
            return Recollection(reachable=False)
        return Recollection(
            reachable=True,
            conclusions=_conclusions(result.get("representation")),
        )

    async def chat(
        self,
        *,
        peer_id: str,
        query: str,
        reasoning_level: str = "low",
        target: str | None = None,
        workspace_id: str | None = None,
    ) -> str | None:
        """The DIALECTIC verb — Honcho REASONS over a peer's representation and
        peer card to SYNTHESISE a natural-language answer (Honcho v2, design/24
        §4). Distinct from `representation`, which RETRIEVES raw conclusions:
        asked «where does he live», representation returns event-log conclusions,
        while chat answers «Tin Shui Chi, based on repeated 天恆/天澤商場
        references» — probe-verified against the live instance 2026-08-07.

        **`peer_id` is the OBSERVER, `target` the OBSERVED** — same axis as
        `representation`. `chat(peer_id="nora", target="miles_novak", …)` reasons
        from NORA'S model of Miles; `target=None` reasons from the peer's own
        self-model. Workspace-wide by the same rationale (no `session_id`):
        Nora reasons over everything she has observed of that person across the
        workspace, and the room roster gates disclosure at the tool layer.

        `POST /v3/workspaces/{ws}/peers/{peer_id}/chat`, body `{query,
        reasoning_level, target?}` → `{"content": "<answer>"}` (docs.honcho.dev/
        v3, and the probe). **`reasoning_level` is chosen by the CALLER per
        question** (minimal→max), because it is the depth/latency trade and only
        the caller knows how hard the question is: a name-lookup takes `low`; a
        «what would he think about X» takes `high`. The model picks it in the
        `ask_memory` tool — Nora decides. NEVER streamed: one synchronous answer
        inside the loop budget.

        Returns the answer string, or None on any failure (unreachable, non-200,
        no `content`) — the best-effort `MemoryStore` contract: the caller says
        «I couldn't recall that», never guesses. Empty/whitespace content is None
        (Honcho has nothing to say), the honest `unavailable` for the tool.

        Its own timeout: this is a REASONING call (an LLM inside Honcho) measured
        ~14-19s even at `low`, so it is far slower than `representation`. Bounded
        by `_chat_timeout` so a slow reason cannot overrun the tag's budget; a
        higher `reasoning_level` spends more of that budget for more depth."""
        body: dict[str, object] = {"query": query, "reasoning_level": reasoning_level}
        if target is not None:
            body["target"] = target
        result = await self._post(
            self._url(f"/peers/{peer_id}/chat", workspace=workspace_id),
            body,
            self._chat_timeout,
        )
        if result is None:
            return None
        content = result.get("content")
        if not isinstance(content, str) or not content.strip():
            return None
        return content.strip()

    async def search(
        self, *, query: str, conversation_id: str | None = None, limit: int = 8,
        workspace_id: str | None = None,
    ) -> MessageHits:
        """Semantic search over the raw MESSAGES (not derived conclusions) — the
        verb behind `search_memory` (design/24 §S.3). Returns a LIST of message
        objects `{content, peer_id, session_id, …}` parsed to `(peer_id, content)`
        pairs, carried in a `MessageHits` beside the one bit the caller cannot
        recover: WHETHER THE SEARCH RAN. This returned a bare list and answered
        `[]` for a transport failure, a >=400, a malformed body and a genuine
        miss alike — so `search_memory` reported a Honcho outage as «nothing was
        said here», which is an ERROR reading as a PASS (external review,
        2026-08-11). Its OWN parse, not `_post`, because the response is a list,
        not the dict every other endpoint returns.

        SESSION-scoped when a `conversation_id` is given — `POST /sessions/{id}/
        search`, restricted to THIS channel's messages. That is the safe default
        and what `search_memory` uses: a workspace-wide search would surface a
        roster member's words from a PRIVATE DM (a different session in the same
        workspace) into this room — the raw-word blend the one-workspace topology
        does NOT license (Kimi audit 2026-08-08). Without a `conversation_id` it
        falls back to the workspace search, for an operator/probe that wants the
        whole unit. Media-only messages (the `￼` attachment placeholder) are
        dropped. DISCLOSURE remains the caller's: `search_memory` also keeps only
        the room's roster speakers and screens identifiers (R-18)."""
        if conversation_id is not None:
            safe = _safe_conversation_key(conversation_id)
            url = self._url(f"/sessions/nora-conv-{safe}/search", workspace=workspace_id)
        else:
            url = self._url("/search", workspace=workspace_id)
        try:
            timeout = aiohttp.ClientTimeout(total=self._representation_timeout)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.post(url, json={"query": query, "limit": limit}) as r,
            ):
                if r.status >= 400:
                    log.warning("honcho search -> %s", r.status)
                    return MessageHits(reachable=False)
                body = await r.json()
        except Exception as e:  # noqa: BLE001 — Honcho is never load-bearing
            log.warning("honcho search unreachable: %s", e)
            return MessageHits(reachable=False)
        if not isinstance(body, list):
            # Reached the server and could not read it: still "could not check".
            log.warning("honcho search returned a non-list body")
            return MessageHits(reachable=False)
        out: list[tuple[str, str]] = []
        for m in body:
            if not isinstance(m, dict):
                continue
            peer, content = m.get("peer_id"), m.get("content")
            if not isinstance(peer, str) or not isinstance(content, str):
                continue
            text = content.replace("￼", "").strip()   # drop the media placeholder
            if text:
                out.append((peer, text))
        return MessageHits(reachable=True, hits=tuple(out))

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
