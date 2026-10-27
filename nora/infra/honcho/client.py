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
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

import aiohttp

from nora.domain.models import strip_mention_placeholders
from nora.domain.ports import MemoryHealth, MessageHits, Recollection

log = logging.getLogger("nora.honcho")

# `[<stamp>] probe_zzz is not coming to events on Thursdays`.
#
# THE STAMP IS BATCH-LEVEL, not per-conclusion: it is
# `internal_metadata["message_created_at"]` — the created_at of the MESSAGE whose
# arrival triggered the derivation — copied onto every conclusion in that batch
# (`src/utils/representation.py::_safe_datetime_from_metadata`, honcho 3.0.11).
# Many conclusions share one stamp, so it is not a recency signal at conclusion
# granularity and nothing downstream may sort or age by it.
#
# It is stripped: it is provenance for a human reading the store, and in front of
# every line it invites the model to quote it back as a fact about the
# conversation. The strip stays rather than restoring the date: a per-line date
# is precision that does not exist (a date next to a sentence reads as a date
# about that sentence), and carrying it would widen the port —
# `Recollection.conclusions` is `tuple[str, ...]` and the batch identity is gone
# by the time the prefill sees a line. The honest statement is «undated», made
# where the line is rendered: `nora.agent.prefill._peer_history_block` says so in
# the block note. Dropping that note, not touching this regex, would let an
# undated line read as a standing directive.
_STAMP = re.compile(r"^\[[^\]]*\]\s*")
# `[id:abc123] ` — only emitted for `include_ids=True`, which this client never
# asks for. Stripped anyway: it costs one regex and the alternative, if the
# default ever flips, is a nanoid read out to the group.
_ID = re.compile(r"^\[id:[^\]]*\]\s*")
# ` **Pattern** [high]: <conclusion>` and ` **CONTRADICTION**: <content>` — the
# two derived sections that label their conclusion inline instead of stamping
# it. The confidence token is dropped with the label: `about` hands the model a
# flat list of things Nora knows, and `high`/`medium`/`low` is a number she
# cannot calibrate, act on, or explain to a group.
_PATTERN = re.compile(r"^\*\*Pattern\*\*\s*\[[^\]]*\]:\s*")
_CONTRADICTION = re.compile(r"^\*\*CONTRADICTION\*\*:\s*")

# Cosine distance above which a semantic hit is discarded rather than returned.
#
# Without a floor the semantic channel ALWAYS returns `top_k` rows: pgvector
# ranks, it does not judge, so a question about nothing in the corpus comes back
# with the nearest unrelated facts and Nora reads them out as though they were
# an answer. 0.5 is the widest value that rejects that cleanly while keeping
# every real hit: 0.8 admits pure nearest-neighbour noise, 0.3 drops real hits.
# Calibrated against the embedding model, not the deployment, which is why it
# lives here and not in `config.toml`.
_MAX_DISTANCE = 0.5

# Honcho constrains a session id to `^[a-zA-Z0-9_-]+$` (a 422 otherwise). A
# Signal group id is STANDARD base64 (`+`, `/`, `=`), and a DM key can be a
# phone number (`+`) — all rejected. This maps the base64 alphabet to the
# url-safe one and drops padding, so the common ids stay readable in the Honcho
# UI; anything still non-conforming falls back to a hash.
_SESSION_ID_OK = re.compile(r"^[a-zA-Z0-9_-]+$")
_B64_TO_URLSAFE = str.maketrans({"+": "-", "/": "_", "=": ""})


def _safe_conversation_key(conversation_id: str) -> str:
    """A Honcho-legal session key derived from an opaque conversation id.

    A Signal group id (`Lhof…+…/…=`) is rejected by the session-create endpoint
    on its `+` `/` `=`, so it cannot be used as-is.
    Base64→url-safe keeps the id readable and deterministic (survives restart);
    an id that STILL violates the pattern falls back to a sha256 hex so the key
    is ALWAYS legal rather than a silent feed failure."""
    candidate = conversation_id.translate(_B64_TO_URLSAFE)
    if candidate and _SESSION_ID_OK.match(candidate):
        return candidate
    return hashlib.sha256(conversation_id.encode()).hexdigest()


def conversation_session_id(conversation_id: str) -> str:
    """The Honcho session id a conversation's messages are fed into — the
    same in every Honcho workspace, derived with no state (`ensure_session`)."""
    return f"nora-conv-{_safe_conversation_key(conversation_id)}"


def _conclusions(representation: Any) -> tuple[str, ...]:
    """One markdown blob → one conclusion per OBSERVATION. Never one per line.

    ## What is on the wire

    `Representation.format_as_markdown` (honcho 3.0.11) emits FOUR sections, and
    only the first is one-line-per-fact::

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
    the model the same fact twice.

    ## Why prose is still parsed at all

    `POST /v3/workspaces/{ws}/conclusions/query` returns structured `Conclusion`
    objects with clean `content` for every level, but it is **semantic search
    and nothing else**: its handler is a single `crud.query_documents` call over
    one embedding of `body.query` (`src/routers/conclusions.py::query_conclusions`),
    so it has no access to the most-derived channel or the recent channel that
    `get_working_representation` blends. Adopting it would reduce the read to
    one channel of three. It also carries no `distance` in its response;
    `distance` there is an input threshold, exactly as `search_max_distance` is
    here.

    ## The rules

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
        # been taught; `Pattern** [high]: …` is what passing it through yields.
        if not line or line.startswith("**"):
            continue
        out.append(line)
    return tuple(out)


#: Honcho reserves this peer-name prefix for the peer that BACKS a named scope
#: (`src/utils/scopes.py::SCOPE_PEER_PREFIX`, honcho v3.1.2): scope `friends`
#: is observer peer `scope.friends`. The public API hides it everywhere except
#: one place this client needs it — a conclusion's `observer_id`, which is
#: "how scoped conclusions are stored" in Honcho's own words
#: (`src/crud/document.py::create_observations`). See `HonchoClient.conclude`.
SCOPE_PEER_PREFIX = "scope."

#: Workspace metadata key Nora stamps on every Honcho workspace she creates or
#: claims, valued with her `ai_peer`. Honcho workspaces are shared by every
#: application on the server, so a name alone proves nothing about who a
#: workspace belongs to. See `HonchoClient._owned`.
OWNER_KEY = "nora_owner"

#: How long a failed scope-member list is remembered before it is asked again.
_MEMBER_LIST_RETRY_SECONDS = 60

#: Honcho's scope-id alphabet (`^[a-zA-Z0-9_-]+$`, at most 506 characters;
#: docs/v3/documentation/features/advanced/scopes.mdx, "Limits").
_SCOPE_ID_OK = re.compile(r"^[a-zA-Z0-9_-]+$")
_SCOPE_ID_MAX = 506


def unit_scope(unit: str) -> str:
    """The Honcho NAMED SCOPE a unit's rooms are gathered into (DEC-214).

    Deterministic, so a restart, the backfill command and the live process
    all name the same scope with no state. A unit name already legal as a
    scope id IS the scope id, so the Honcho UI reads `friends`, not a hash.
    Anything else keeps its legal characters for readability and gains a
    short hash of the ORIGINAL name, so two unit names that differ only in
    an illegal character can never collapse onto one scope."""
    if unit and _SCOPE_ID_OK.match(unit) and len(unit) <= _SCOPE_ID_MAX:
        return unit
    legible = re.sub(r"[^a-zA-Z0-9_-]", "_", unit)[:64] or "unit"
    digest = hashlib.sha256(unit.encode()).hexdigest()[:12]
    return f"{legible}-{digest}"


@dataclass(frozen=True)
class HonchoRoute:
    """Where one Honcho call goes: the HONCHO workspace, and the unit's
    named scope inside it (`None` = unscoped).

    A Honcho workspace is not a Nora workspace — they are separate names for
    separate things (GLOSSARY.md). The Nora workspace is the operator's
    grouping of units (`policy.toml`); the Honcho workspace is where that
    grouping's person models live (`identity.toml`'s binding). This value
    carries only the Honcho half: by the time a call is routed, which Nora
    workspace it came from has already decided everything it can."""

    workspace: str
    scope: str | None = None


class WorkspaceRouter:
    """Conversation -> unit -> Honcho workspace + the unit's scope (DEC-214).

    **The hierarchy is workspace > unit > room.** A Nora workspace holds one or
    more units, a unit holds one or more rooms (at most one per platform), and
    each Nora workspace is bound to exactly ONE Honcho workspace. Units in one
    workspace SHARE its Honcho workspace — so one person is one Honcho peer, one
    model, across every unit of a workspace — and each unit is a Honcho NAMED
    SCOPE inside it, so a read made for a unit sees only what was said in that
    unit's rooms. Only DIFFERENT Nora workspaces split a person into separate
    models, and that split is the boundary the operator drew. A Honcho workspace
    per UNIT would split one person into N models, so units never get one.

    `strict` is True when `policy.toml` declares `[units]`. Then a
    conversation or unit this router was not built with has NO route —
    `None`, and the client fails the call closed (a read is `unavailable`, a
    write is `False`) rather than widening it to some default workspace.
    Without `[units]` there is one implicit workspace, bound to
    `honcho.workspace_id`, and anything unknown (a harness's synthetic room)
    falls back to it UNSCOPED. Built by `nora.app.memory_routing`, never here:
    this layer may not read the policy."""

    def __init__(
        self,
        default: str,
        *,
        unit_workspaces: Mapping[str, str] | None = None,
        conversation_units: Mapping[str, str] | None = None,
        workspace_routes: Mapping[str, str] | None = None,
        strict: bool = False,
    ) -> None:
        self._default = default
        self._unit_workspaces = dict(unit_workspaces or {})
        self._conversation_units = dict(conversation_units or {})
        # Nora workspace name -> its Honcho workspace, for the one kind of
        # read keyed on a WORKSPACE rather than a room or unit: a person's own
        # model across that workspace, unscoped (the portal's memory page).
        self._workspace_routes = dict(workspace_routes or {})
        self._strict = strict

    @property
    def default(self) -> str:
        """`honcho.workspace_id`: the implicit workspace's Honcho workspace, and
        the one Honcho workspace Nora is known to own without a marker (it
        predates the marker, and every deployment's history is in it)."""
        return self._default

    @property
    def strict(self) -> bool:
        return self._strict

    def unit_of(self, conversation_id: str) -> str | None:
        return self._conversation_units.get(conversation_id)

    def for_unit(self, unit: str) -> HonchoRoute | None:
        """The unit's Honcho workspace and — only once `policy.toml` declares
        `[units]` — its named scope. Without `[units]` every route is UNSCOPED,
        because scoping reads with no config change would read empty scopes
        (nothing has joined them yet) and silently empty every recall."""
        workspace = self._unit_workspaces.get(unit)
        if workspace is None:
            return None if self._strict else HonchoRoute(self._default)
        return HonchoRoute(workspace, unit_scope(unit) if self._strict else None)

    def conversations(self) -> tuple[tuple[str, str], ...]:
        """Every (conversation id, unit) this router was built with."""
        return tuple(sorted(self._conversation_units.items()))

    def for_conversation(self, conversation_id: str) -> HonchoRoute | None:
        unit = self._conversation_units.get(conversation_id)
        if unit is None:
            return None if self._strict else HonchoRoute(self._default)
        return self.for_unit(unit)

    def for_workspace(self, name: str) -> HonchoRoute | None:
        """A Nora WORKSPACE's Honcho workspace, UNSCOPED — every unit of it at
        once. `None` for a workspace this router was not built with."""
        workspace = self._workspace_routes.get(name)
        return HonchoRoute(workspace) if workspace is not None else None

    def unrouted(self) -> HonchoRoute | None:
        """The route of a call that names neither a room nor a unit: the
        implicit workspace, unscoped — or none at all once `[units]` exists,
        because then there is no single workspace a caller could have meant."""
        return None if self._strict else HonchoRoute(self._default)

    def workspaces(self) -> tuple[str, ...]:
        """Every Honcho workspace a call can reach, for `ensure_workspace`."""
        reachable = set(self._unit_workspaces.values())
        if not self._strict:
            reachable.add(self._default)
        return tuple(sorted(reachable))

    def scoped_units(self) -> tuple[tuple[str, HonchoRoute], ...]:
        """Every known unit with its route, for `ensure_workspace`'s scopes."""
        out: list[tuple[str, HonchoRoute]] = []
        for unit in sorted(self._unit_workspaces):
            route = self.for_unit(unit)
            if route is not None:
                out.append((unit, route))
        return tuple(out)


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
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._base = base_url.rstrip("/")
        # Every call is ROUTED (DEC-214): conversation or unit -> the Honcho
        # workspace its Nora workspace is bound to, plus the unit's named
        # scope. No router is the single-workspace case: every call goes to
        # `workspace_id`, unscoped (what a bare client in an offline test gets).
        self._router = workspace_router or WorkspaceRouter(workspace_id)
        # The OBSERVER peer — Nora. Honcho keys memory by (observer, observed)
        # peer pairs, so this is the peer from whose
        # perspective every recall is read: `(observer=nora, observed=person)` is
        # NORA'S model of that person, distinct from the person's self-model. The
        # deriver only builds that pair when Nora joins the session with
        # `observe_others=True` — which is why the client, not just the caller,
        # has to know who Nora is.
        self._ai_peer = ai_peer
        self._feed_timeout = feed_timeout
        self._card_timeout = card_timeout
        # Calls take 0.19 to 0.26 s, so 3 s is ~12x headroom. It is a ceiling on a call
        # that happens INSIDE a live turn (`about`), which is why it
        # is bounded at all: the tag already costs a model round trip per tool
        # call, and a hung memory service must not add to that.
        self._representation_timeout = representation_timeout
        # The DIALECTIC chat is a REASONING call — ~19 s (the Honcho-side
        # reasoning model is slow). 25 s is headroom over that, deliberately
        # larger than every other Honcho timeout, and it is why chat is NOT an
        # eager in-turn tool: 19 s blows the p95≤5s SLO.
        self._chat_timeout = chat_timeout
        # Seconds, monotonic: only the scope-member list's negative cache
        # reads it (`_scope_members`).
        self._clock = clock
        # Honcho workspaces already ensured. A SET since DEC-214: one process
        # reaches one Honcho workspace per Nora workspace.
        self._workspaces_ready: set[str] = set()
        # (honcho workspace, peer id). Honcho PEERS are workspace-scoped, so a
        # peer ensured in one workspace does not exist in another — keying on
        # the id alone would skip creating it in the second workspace forever.
        self._known_peers: set[tuple[str, str]] = set()
        self._sessions: dict[str, str] = {}
        # (honcho workspace, session_id, peer_id) triples already given their
        # observer config, so the add-peers call fires ONCE per peer per
        # session, not every message. The workspace is in the key for
        # `_known_peers`' reason: session ids are derived from the
        # conversation alone and are therefore the same string in every
        # workspace.
        self._configured_peers: set[tuple[str, str, str]] = set()
        # (honcho workspace, scope) pairs known to exist, and (workspace,
        # scope, session) memberships already asserted this process — each
        # costs one idempotent POST, once, like `_configured_peers`.
        self._known_scopes: set[tuple[str, str]] = set()
        # Honcho workspace -> whether it is Nora's (`_owned`). Only settled
        # answers are cached; an unreachable Honcho is asked again.
        self._ownership: dict[str, bool] = {}
        self._scoped_sessions: set[tuple[str, str, str]] = set()
        # (honcho workspace, scope) -> its member session ids, listed once
        # per process (`_scope_members`) so a join never re-adds a member;
        # and when a list last FAILED, so a Honcho that cannot answer is asked
        # again after `_MEMBER_LIST_RETRY_SECONDS`, not on every message.
        self._members: dict[tuple[str, str], set[str]] = {}
        self._members_failed_at: dict[tuple[str, str], float] = {}
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

    def _url(self, path: str, *, workspace: str) -> str:
        return f"{self._base}/v3/workspaces/{workspace}{path}"

    async def _route(
        self, *, conversation_id: str | None = None, unit: str | None = None,
        workspace: str | None = None, claim: bool = True,
    ) -> HonchoRoute | None:
        """Where a call for this room or unit goes, or `None` for nowhere.

        `unit` and `conversation_id` are both ways to name the same thing —
        the unit the call is for — so when a caller passes both they must
        agree; two different answers is a caller bug, and the call is failed
        closed rather than sent to either. Neither is the unrouted case,
        which exists only without `[units]` (`WorkspaceRouter.unrouted`).
        `None` is logged here once per call so every verb's own fail-closed
        branch stays one line.

        `workspace` names a NORA workspace, for a read of a person across all
        of its units (unscoped), and excludes the other two keys. Every route
        is then checked for OWNERSHIP (`_owned`): a Honcho workspace that
        belongs to some other application has no route at all."""
        if workspace is not None:
            if unit is not None or conversation_id is not None:
                log.warning("honcho call names a workspace AND a room or unit; refused")
                return None
            route = self._router.for_workspace(workspace)
            if route is None:
                log.warning("honcho call names workspace %r, which is not bound", workspace)
                return None
            # A whole-workspace read is a PAGE VIEW (the portal) or an
            # operator's read: it never creates or marks a workspace.
            return route if await self._owned(route.workspace, claim=False) else None
        if unit is not None:
            route = self._router.for_unit(unit)
            if conversation_id is not None and route != self._router.for_conversation(
                conversation_id
            ):
                log.warning(
                    "honcho call names unit %r and a conversation of another "
                    "unit; refused rather than routed to either", unit,
                )
                return None
        elif conversation_id is not None:
            route = self._router.for_conversation(conversation_id)
        else:
            route = self._router.unrouted()
        if route is None:
            log.warning(
                "honcho call has no route (unit=%r, conversation=%r): policy.toml "
                "names no workspace for it, so it is refused, not widened",
                unit, conversation_id,
            )
            return None
        return route if await self._owned(route.workspace, claim=claim) else None

    async def inspect_workspace(self, workspace: str) -> str | None:
        """Who a Honcho workspace belongs to, READ-ONLY: `"owned"` (it carries
        Nora's `OWNER_KEY`), `"absent"` (no such workspace), `"empty"` (it
        exists, unmarked, with no session in it), `"foreign"` (unmarked, or
        marked by another `ai_peer`, and holding sessions), or `None` when
        Honcho could not be read.

        Built on the two reads Honcho v3.1.2 offers for it: the workspace
        list (`POST /workspaces/list`, which carries each workspace's
        `metadata`; there is no single-workspace GET that does not also
        create) and the session list (`POST /workspaces/{id}/sessions/list`,
        whose page `total` counts them). The marker is the reliable signal —
        a session-name heuristic would have to know every name another
        application might choose, and the live `nora` workspace already
        holds probe sessions no rule of ours would call Nora-shaped."""
        # `filters={"id": ...}`: the workspace list's filter maps `id` to the
        # workspace's name (`src/utils/filter.py`, applied by
        # `crud.get_all_workspaces`), so this is one row, not every tenant.
        body = await self._post(
            f"{self._base}/v3/workspaces/list?page=1&size=10",
            {"filters": {"id": workspace}}, self._card_timeout,
        )
        items = (body or {}).get("items")
        if not isinstance(items, list):
            log.warning(
                "could not tell who Honcho workspace %r belongs to: the workspace "
                "list was unreadable", workspace,
            )
            return None
        found = next(
            (i for i in items if isinstance(i, dict) and i.get("id") == workspace), None,
        )
        if found is None:
            return "absent"
        metadata = found.get("metadata")
        owner = metadata.get(OWNER_KEY) if isinstance(metadata, dict) else None
        if owner == self._ai_peer:
            return "owned"
        sessions = await self._post(
            self._url("/sessions/list?page=1&size=1", workspace=workspace), {},
            self._card_timeout,
        )
        total = (sessions or {}).get("total")
        if not isinstance(total, int):
            log.warning(
                "could not tell who Honcho workspace %r belongs to: its session "
                "list was unreadable", workspace,
            )
            return None
        if owner is None and total == 0:
            return "empty"
        return "foreign"

    async def _owned(self, workspace: str, *, claim: bool = True) -> bool:
        """Whether calls may go to this Honcho workspace (DEC-214).

        The DEFAULT (`honcho.workspace_id`) always may: it is the workspace
        every deployment's history is already in, created before the marker
        existed, and the eval harness's draw workspaces arrive through it. Any
        other bound workspace must be OURS: marked with `OWNER_KEY`, or
        absent or empty — in which case it is claimed here, created with the
        marker or given it (`PUT /workspaces/{id}` REPLACES metadata, so the
        existing keys are carried over). An unmarked workspace that holds
        sessions belongs to someone else, and every call routed to it is
        refused for the life of the process, logged as an error; a binding
        is a line in identity.toml, and writing Nora's speech into another
        application's peers because of a name clash could not be undone.

        `claim=False` is the read-only form, for a caller that must write
        nothing (the backfill's dry run): an absent or empty workspace is
        then allowed without being created or marked. An unreadable Honcho
        is `False`, not cached, so the next call asks again."""
        if workspace == self._router.default:
            return True
        known = self._ownership.get(workspace)
        if known is not None:
            return known
        state = await self.inspect_workspace(workspace)
        if state is None:
            return False
        if state == "foreign":
            log.error(
                "honcho workspace %r holds sessions and is not marked as Nora's "
                "(%s); every call bound to it is refused — bind this workspace to "
                "another Honcho workspace in identity.toml",
                workspace, OWNER_KEY,
            )
            self._ownership[workspace] = False
            return False
        if state == "owned":
            self._ownership[workspace] = True
            return True
        if not claim:
            return True
        if not await self._claim(workspace, exists=state == "empty"):
            return False
        self._ownership[workspace] = True
        return True

    def _marked(self, workspace: dict[str, Any] | None) -> bool:
        metadata = (workspace or {}).get("metadata")
        return isinstance(metadata, dict) and metadata.get(OWNER_KEY) == self._ai_peer

    async def _claim(self, workspace: str, *, exists: bool) -> bool:
        """Create or mark the workspace, and believe it only from Honcho's own
        answer: get-or-create on a workspace somebody else created a moment
        ago returns THEIRS, unmarked and with our metadata ignored, and that
        must not be cached as ours."""
        marker = {OWNER_KEY: self._ai_peer}
        if not exists:
            result = await self._post(
                f"{self._base}/v3/workspaces", {"id": workspace, "metadata": marker},
                self._card_timeout,
            )
            if not self._marked(result):
                log.warning(
                    "Honcho workspace %r was not created with Nora's marker (it may "
                    "have been created by someone else meanwhile); not claimed",
                    workspace,
                )
                return False
            return True
        # It exists and is empty: mark it, keeping whatever metadata it had.
        body = await self._post(
            f"{self._base}/v3/workspaces", {"id": workspace}, self._card_timeout,
        )
        existing = (body or {}).get("metadata")
        merged = {**(existing if isinstance(existing, dict) else {}), **marker}
        url = f"{self._base}/v3/workspaces/{workspace}"
        try:
            async with (
                aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=self._card_timeout)
                ) as session,
                session.put(url, json={"metadata": merged}) as r,
            ):
                if r.status >= 400:
                    log.warning("honcho PUT %s -> %s", url, r.status)
                    return False
                answer = await r.json()
        except Exception as e:  # noqa: BLE001 — Honcho is never load-bearing
            log.warning("honcho PUT %s failed: %s", url, e)
            return False
        if not self._marked(answer if isinstance(answer, dict) else None):
            log.warning("Honcho workspace %r did not keep Nora's marker; not claimed", workspace)
            return False
        return True

    def foreign_workspaces(self) -> tuple[str, ...]:
        """The bound Honcho workspaces found to belong to someone else — for
        the composition root to refuse startup on, after `ensure_workspace`."""
        return tuple(sorted(w for w, ok in self._ownership.items() if not ok))

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
        """Every Honcho workspace this process can route to, with Nora's own
        peer in each and every known unit's named scope (DEC-214). Best-effort
        and repeatable: what failed is simply tried again on the next call,
        and a scope or peer that is still missing is created lazily by the
        first call that needs it."""
        for workspace in self._router.workspaces():
            if workspace not in self._workspaces_ready:
                if workspace == self._router.default:
                    result = await self._post(
                        f"{self._base}/v3/workspaces", {"id": workspace},
                        self._card_timeout,
                    )
                    if result is None:
                        continue
                elif not await self._owned(workspace):
                    # Foreign (logged, and `foreign_workspaces` names it) or
                    # unreachable: no peer and no scope is created in it.
                    continue
                self._workspaces_ready.add(workspace)
            # Nora's aiPeer, per workspace: peers are workspace-scoped, so her
            # self-model in one workspace is a different peer from her
            # self-model in another, and each must exist before a session
            # there configures her as its observer.
            await self._ensure_peer_in(workspace, self._ai_peer)
        for _unit, route in self._router.scoped_units():
            if route.workspace in self._workspaces_ready:
                await self._ensure_scope(route)
        # Every room the policy knows, into its unit's scope — at STARTUP, not
        # on its next message. A room that is quiet after the cutover would
        # otherwise never join, and its unit's scope would be empty of it (and
        # of the explicit conclusions Honcho's backfill copies on joining)
        # for as long as nobody spoke there. Session ids are deterministic, so
        # this needs no state; a room with no session yet in this workspace is
        # skipped (a 404), and `_join_scope` is idempotent on both sides.
        # CONCURRENT: each join is bounded by `_feed_timeout`, and a Honcho that
        # hangs must cost startup one timeout, not one per room.
        if self._router.strict:
            joins = []
            for cid, _unit in self._router.conversations():
                room = self._router.for_conversation(cid)
                if (
                    room is not None and room.scope is not None
                    and room.workspace in self._workspaces_ready
                    and (room.workspace, room.scope) in self._known_scopes
                ):
                    joins.append(self._join_scope(
                        room, conversation_session_id(cid), missing_ok=True,
                    ))
            await asyncio.gather(*joins)

    async def _ensure_peer_in(self, workspace: str, peer_id: str) -> None:
        # Check-then-act without this lock creates two peers for one id when
        # two platforms feed the same person at the same moment — and
        # person-centric identity guarantees that happens.
        async with self._peer_lock(f"{workspace}:{peer_id}"):
            if (workspace, peer_id) in self._known_peers:
                return
            result = await self._post(
                self._url("/peers", workspace=workspace), {"id": peer_id},
                self._card_timeout,
            )
            if result is not None:
                self._known_peers.add((workspace, peer_id))

    async def ensure_peer(
        self, *, peer_id: str, conversation_id: str | None = None,
        unit: str | None = None,
    ) -> None:
        """Create `peer_id` in the Honcho workspace of the room or unit the
        caller is about to write for. Per (workspace, peer): the same person
        is a separate peer in each Honcho workspace they are seen in."""
        route = await self._route(conversation_id=conversation_id, unit=unit)
        if route is None:
            return
        await self._ensure_peer_in(route.workspace, peer_id)

    async def _ensure_scope(self, route: HonchoRoute) -> bool:
        """Get-or-create the unit's named scope (`POST /scopes`, 201 created /
        200 existing, honcho v3.1.2). True when it is known to exist."""
        if route.scope is None:
            return True
        key = (route.workspace, route.scope)
        if key in self._known_scopes:
            return True
        result = await self._post(
            self._url("/scopes", workspace=route.workspace), {"id": route.scope},
            self._card_timeout,
        )
        if result is None:
            return False
        self._known_scopes.add(key)
        return True

    async def _join_scope(
        self, route: HonchoRoute, session_id: str, *, missing_ok: bool = False,
    ) -> None:
        """Put a session in the unit's scope — ONLY IF IT IS NOT ALREADY A
        MEMBER — once per process.

        `POST /scopes/{scope}/sessions` is NOT free to repeat, whatever the
        docs call it: honcho v3.1.2's `crud.add_sessions_to_scope`
        (`src/crud/scope.py`) enqueues a `scope_backfill` for EVERY named
        session that has messages, members included, and each backfill
        enqueues an omni dream per touched collection
        (`src/deriver/scope_backfill.py`) — model spend. Re-adding every known room on
        each restart would pay for it. So the scope's current membership is LISTED first
        (`POST /scopes/{scope}/sessions/list`, paged, once per scope per
        process) and only a session missing from it is added. A list that
        fails means the join is SKIPPED, logged, and tried again on a later
        call — never an add made blind. The scope is ensured first, and the
        check-and-add holds a per-session lock. This route also 404s on a
        Honcho with no scopes, so a server that ignored scopes entirely still
        shows here."""
        if route.scope is None:
            return
        key = (route.workspace, route.scope, session_id)
        if key in self._scoped_sessions:
            return
        # The scope itself first (cached): the session create does not name
        # it, so this is the only place a scope missing after a Honcho-down
        # startup gets created — for a room that has only ever listened, too.
        if not await self._ensure_scope(route):
            return
        # ONE check-then-add per session: the startup gather and a room's first
        # message can reach here together, and two adds are two backfills.
        async with self._peer_lock(f"join:{route.workspace}:{route.scope}:{session_id}"):
            if key in self._scoped_sessions:
                return
            members = await self._scope_members(route.workspace, route.scope)
            if members is None:
                return          # logged once per retry window by `_scope_members`
            if session_id in members:
                self._scoped_sessions.add(key)
                return
            # 204 with no body on success; `_post` would read that as a
            # failure, so this call parses its own status.
            url = self._url(f"/scopes/{route.scope}/sessions", workspace=route.workspace)
            try:
                async with (
                    aiohttp.ClientSession(
                        timeout=aiohttp.ClientTimeout(total=self._feed_timeout)
                    ) as session,
                    session.post(url, json={"session_ids": [session_id]}) as r,
                ):
                    if r.status == 404 and missing_ok:
                        return          # no such session in this workspace yet
                    if r.status >= 400:
                        log.warning("honcho POST %s -> %s", url, r.status)
                        return
            except Exception as e:  # noqa: BLE001 — Honcho is never load-bearing
                log.warning("honcho POST %s failed: %s", url, e)
                return
            self._scoped_sessions.add(key)

    async def _scope_members(self, workspace: str, scope: str) -> set[str] | None:
        """The scope's member session ids as listed ONCE per process — every
        page. A session this process adds afterwards is recorded in
        `_scoped_sessions`, not here, and `_join_scope` checks that first.

        `None` when the list could not be read. That is remembered for
        `_MEMBER_LIST_RETRY_SECONDS`: until then every join to this scope is
        skipped without asking again (a Honcho that is down would otherwise be
        asked on every message), and the failure is logged once per window."""
        key = (workspace, scope)
        lock = self._peer_lock(f"scope-members:{workspace}:{scope}")
        async with lock:
            cached = self._members.get(key)
            if cached is not None:
                return cached
            failed_at = self._members_failed_at.get(key)
            if failed_at is not None and self._clock() - failed_at < _MEMBER_LIST_RETRY_SECONDS:
                return None
            found: set[str] = set()
            page = 1
            while True:
                body = await self._post(
                    self._url(
                        f"/scopes/{scope}/sessions/list?page={page}&size=100",
                        workspace=workspace,
                    ),
                    {}, self._feed_timeout,
                )
                items = (body or {}).get("items")
                if not isinstance(items, list):
                    self._members_failed_at[key] = self._clock()
                    log.warning(
                        "could not list the members of scope %r in %r; no session "
                        "joins it for %ds rather than being added blind",
                        scope, workspace, _MEMBER_LIST_RETRY_SECONDS,
                    )
                    return None
                found.update(
                    str(i["id"]) for i in items if isinstance(i, dict) and "id" in i
                )
                pages = (body or {}).get("pages")
                if not isinstance(pages, int) or page >= pages or not items:
                    break
                page += 1
            self._members[key] = found
            self._members_failed_at.pop(key, None)
            return found

    async def ensure_session(self, conversation_id: str) -> str | None:
        """The CONVERSATION's session id, created on first use.
        One session per Signal group / DM, holding EVERY speaker in
        that conversation as its own peer — so a human's words and Nora's reply
        share one session while each message stays under its own `peer_id`.

        This replaces a per-person `nora-<peer_id>` keying: a per-person
        session structurally cannot hold a two-speaker conversation, so Nora's
        reply would have nowhere honest to go (DEC-36).

        Sessions are created at the WORKSPACE level via `POST /sessions` (201
        with the session; the live instance answers 200 with the same id on a
        repeat POST, so re-creating is idempotent). The id is deterministic, so
        it survives a restart without state; the cache only saves a round trip.

        **In the conversation's own Honcho workspace, inside its unit's named
        scope** (DEC-214). The create does NOT carry `scopes`: `POST
        /sessions` is get-or-create, and on an EXISTING session with messages
        honcho v3.1.2 enqueues a `scope_backfill` per listed scope whether or
        not it is already a member (`crud.get_or_create_session`) — model
        spend on the first message after every restart. Membership is
        `_join_scope`'s, which lists the scope first and adds only a
        non-member; for a session created here that is an empty session,
        joined before its first message, so nothing is backfilled and every
        message reaches the scope through the deriver's fan-out. `None` when
        the conversation has no route: nothing is created anywhere.
        """
        route = await self._route(conversation_id=conversation_id)
        if route is None:
            return None
        ws = route.workspace
        async with self._peer_lock(f"conv:{conversation_id}"):
            # Nora joins every session as an OBSERVER (observe_others=True), so
            # the deriver builds her model of each human who speaks here; and as
            # observable herself (observe_me=True) so her own model of herself can form.
            # Without this the (nora → person) pair is never derived and every
            # recall reads empty.
            #
            # This runs on the cache HIT path too, NOT only at creation: it is
            # idempotent via `_configured_peers` (a no-op once it has landed), so
            # running it every call means a transient add-peers failure RETRIES on
            # the next message instead of leaving Nora a non-observer of this
            # session for the whole process life. The human speaker's config in
            # `feed` already retries this way; the ai_peer must match it, or one
            # dropped POST silently disables all recall here.
            cached = self._sessions.get(conversation_id)
            if cached is not None:
                await self._configure_peer(
                    cached, self._ai_peer, observe_others=True, observe_me=True,
                    workspace=ws,
                )
                await self._join_scope(route, cached)
                return cached
            session_id = f"nora-conv-{_safe_conversation_key(conversation_id)}"
            body: dict[str, object] = {"id": session_id}
            result = await self._post(
                self._url("/sessions", workspace=ws), body, self._feed_timeout
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
            await self._join_scope(route, returned)
            return returned

    async def _configure_peer(
        self, session_id: str, peer_id: str, *, observe_others: bool, observe_me: bool,
        workspace: str,
    ) -> None:
        """Add `peer_id` to `session_id` with its observer config, ONCE per pair.

        Honcho keys memory by (observer, observed) peer pairs, and only derives a
        pair when the observer has `observe_others=True` AND the observed has
        `observe_me=True`. Best-effort: a failure leaves the pair unconfigured so
        the next message retries it — a missed config degrades to «not modelled»,
        never a crash. `POST /sessions/{id}/peers` with `{peer_id: config}`
        `workspace` is
        the conversation's Honcho workspace (DEC-214)."""
        pair = (workspace, session_id, peer_id)
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
        created_at_ms: int | None = None,
    ) -> bool:
        """Feed one message under its speaker's `peer_id` into the conversation's
        session. Best-effort: False on any failure, the caller
        counts it and moves on. `metadata` rides on the message for the deriver
        (never the text itself).

        `created_at_ms` is for a REPLAY only (`nora memory backfill`): the
        message's own epoch-ms time, sent as `MessageCreate.created_at` (honcho
        v3.1.2), so a replayed message is dated when it was said rather than
        when it was replayed — the deriver stamps its conclusions with it.
        Live ingestion never passes it (not on the `MemoryStore` port): a live
        message is said now."""
        session_id = await self.ensure_session(conversation_id)
        if session_id is None:
            return False
        route = await self._route(conversation_id=conversation_id)
        if route is None:
            return False
        ws = route.workspace
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
        if created_at_ms is not None:
            message["created_at"] = datetime.fromtimestamp(
                created_at_ms / 1000, UTC,
            ).isoformat()
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
        conversation_id: str | None = None,
        unit: str | None = None,
    ) -> bool:
        """Write ONE settled fact into the conclusions store, under an
        (observer, observed) peer pair. Best-effort: False on any failure, the
        caller (the `remember` tool) reports `unavailable` and the reply path
        moves on — Honcho is never load-bearing.

        `peer_id` is the OBSERVER (Nora, `self._ai_peer`), `target` the
        OBSERVED person — the exact mirror of `representation`'s pair, so what this writes is what
        `about`/`ask_memory` read
        back over the same pair. This is the only legitimate write lane for a
        settled fact about a person: notes are TRACKED UNCERTAINTY and the
        fact lane excludes person descriptions by design, so without it
        「記低」 would agree out loud and land nowhere.

        No `session_id` is sent. Recall is UNIT-wide on purpose (see
        `representation`): her model of a person blends every room of the
        unit she shares with them, and disclosure is gated by the room ROSTER
        at the tool layer — not by narrowing the memory query to one session.
        Scoping the write to one session would key visibility to provenance
        the readers never filter on, so the write is stored where the unit's
        reads look: what the write lane commits, the read lane can serve.

        No dedup here either: Honcho's deriver owns that on its side, and a
        repeated line costs one row, never a crash. The timeout is
        `_feed_timeout` (2 s) for the same reason `feed` uses it: the write
        happens INSIDE a live turn, where a hung memory service must not add
        latency the group feels.

        **Routed, and written where the SCOPED read will look** (DEC-214).
        The conclusion goes to the Honcho workspace of the room or unit the
        caller names. When that unit has a named scope, the reads are scoped
        (`representation`/`chat` pass `scope`), and a named-scope read swaps
        the observer to the scope's own peer: it reads the
        `(scope.<unit>, target)` collection, never `(nora, target)`. A fact
        written only under Nora would therefore be invisible to every read
        that serves it — `remember` would agree out loud and land nowhere.
        So ONE batch carries
        two rows: Nora's own (her workspace-wide model of the person, which
        the peer keeps as one representation) and the scope's (what the
        unit's reads see). One POST, so the two land or fail together, and
        the observer id `scope.<unit>` is Honcho's own storage shape for a
        scoped conclusion (`SCOPE_PEER_PREFIX`); a Honcho that refuses it
        answers >=400, which is `False` — `unavailable`, never a silent
        success. `False` too, without a request, when there is no route."""
        route = await self._route(conversation_id=conversation_id, unit=unit)
        if route is None:
            return False
        rows: list[dict[str, object]] = [
            {"content": content, "observer_id": peer_id, "observed_id": target},
        ]
        if route.scope is not None:
            if not await self._ensure_scope(route):
                return False
            rows.append({
                "content": content,
                "observer_id": f"{SCOPE_PEER_PREFIX}{route.scope}",
                "observed_id": target,
            })
        result = await self._post(
            self._url("/conclusions", workspace=route.workspace),
            {"conclusions": rows},
            self._feed_timeout,
        )
        return result is not None

    async def peer_card(
        self, *, peer_id: str, conversation_id: str | None = None,
        unit: str | None = None, workspace: str | None = None,
    ) -> list[str]:
        """Durable recall about a peer. [] on any failure — a missing card is normal.

        The field is `peer_card`, and it is a LIST: `GET /peers/{id}/card`
        answers `{"peer_card": [...] | null}`. Reading a string `content`
        instead would return `[]` every time, indistinguishable from the
        healthy "a missing card is normal" case.

        **Routed to the Honcho workspace, NOT scoped — because the endpoint
        cannot be** (DEC-214). `GET /peers/{id}/card` takes no `scope` in
        honcho v3.1.2 (`src/routers/peers.py::get_peer_card`: `target` is its
        only option). The one scope-aware card read is session context
        (`GET /sessions/{id}/context?peer_target=&scope=`), and it reads a
        DIFFERENT card: the scope's card OF the peer, where this reads the
        peer's own. So the card stays unscoped, and what that means is
        bounded by the workspace: it can blend the units of ONE Nora
        workspace, whose people the operator put in one Honcho workspace on
        purpose, and it can never reach another Nora workspace's. No route,
        no request: `[]`.
        """
        route = await self._route(
            conversation_id=conversation_id, unit=unit, workspace=workspace,
        )
        if route is None:
            return []
        result = await self._get(
            self._url(f"/peers/{peer_id}/card", workspace=route.workspace),
            self._card_timeout,
        )
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
        conversation_id: str | None = None,
        unit: str | None = None,
        workspace: str | None = None,
    ) -> Recollection:
        """What Honcho concludes about one peer, curated to one question.

        `POST /v3/workspaces/{ws}/peers/{peer_id}/representation` with a
        `search_query`:
        answers 200 in ~0.2 s with `{"representation": "<markdown>"}`.

        **`peer_id` is the OBSERVER, `target` the OBSERVED.**
        With `target` set, this reads `(observer=peer_id, observed=target)` — so
        `representation(peer_id="nora", target="miles_novak")` is NORA'S model of
        Miles, built from messages she observed, distinct from Miles's self-model
        (`target=None` → observer==observed). Nora is a peer that joins
        every session with `observe_others=True`, so the pair derives.
        No `session_id`: recall is UNIT-wide
        on purpose (the named scope below, DEC-214), so Nora's model of a
        person blends every room of the unit she shares with them;
        disclosure is gated by the room ROSTER at the tool layer, not by
        narrowing the memory query to one session.

        TWO THINGS ARE DELIBERATE, each live-verified:

        * **Not `peer_card`.** `GET /peers/{id}/card` returns `{"peer_card":
          null}` even for peers that demonstrably have conclusions — it answers
          "I know nothing". This
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

        Sending `search_top_k = max_conclusions` with no
        `include_most_frequent` computes to `semantic = 5, derived = 0,
        recent = 0`: **the semantic channel eats the entire budget and the other
        two are structurally unreachable.** The derived channel is the one
        ADR 0001 says Honcho exists for — `times_derived` counts how often the
        deduplicator has re-derived the same conclusion, so it ranks the durable
        core of what is known about a person above whatever one question happens
        to embed near.

        `search_top_k = max_conclusions // 3` gives an even three-way blend.
        That is also Honcho's own default when `search_top_k` is omitted, and it
        is sent explicitly anyway: an upstream default can change underneath us
        without a diff. `max(1, …)` keeps it inside the API's `ge=1` bound for
        small budgets.

        `max_conclusions` is 9 because the split turns it into three budgets
        rather than one. At 5 the semantic channel is **one row**, too few to
        answer «how does he get to work» from a corpus that contains the
        answer (the nearest hit is *does the morning school run*; *drives to
        work instead of taking the train* is third). 9 gives 3/3/3 and costs roughly 1,100
        characters of tool result, in the same order as
        `load_transcript`'s default and well inside `llm.limits`.

        ## Routed and SCOPED (DEC-214)

        The read goes to the Honcho workspace of the room or unit the caller
        names, and when the unit has a named scope it passes `scope=<unit>`
        — the NAMED arm, one scope name, which reads the scope's own model of
        `target` (or of `peer_id` with no target): conclusions reasoned only
        from that unit's rooms, at every level (scopes.mdx, «The Two Arms»).
        `scope` excludes `session_id` and `filters`, neither of which this
        sends. No route: `reachable=False` without a request — an honest
        «could not check», never a read of some other workspace.

        `workspace` (a NORA workspace name) instead of a room or unit reads
        the person across every unit of that workspace, UNSCOPED — the one
        caller is a person reading their OWN model on the portal
        (`nora.web.memory`), which is shown per workspace.
        """
        route = await self._route(
            conversation_id=conversation_id, unit=unit, workspace=workspace,
        )
        if route is None:
            return Recollection(reachable=False)
        if not await self._ensure_scope(route):
            return Recollection(reachable=False)
        payload: dict[str, object] = {
            "search_query": search_query,
            "max_conclusions": max_conclusions,
            "search_top_k": max(1, max_conclusions // 3),
            "include_most_frequent": True,
            "search_max_distance": _MAX_DISTANCE,
        }
        if target is not None:
            payload["target"] = target
        if route.scope is not None:
            payload["scope"] = route.scope
        result = await self._post(
            self._url(f"/peers/{peer_id}/representation", workspace=route.workspace),
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
        conversation_id: str | None = None,
        unit: str | None = None,
    ) -> str | None:
        """The DIALECTIC verb — Honcho REASONS over a peer's representation and
        peer card to SYNTHESISE a natural-language answer. Distinct from `representation`,
        which RETRIEVES raw conclusions: asked «where does he live», representation returns
        event-log conclusions,
        while chat answers «Tin Shui Chi, based on repeated 天恆/天澤商場
        references».

        **`peer_id` is the OBSERVER, `target` the OBSERVED** — same axis as
        `representation`. `chat(peer_id="nora", target="miles_novak", …)` reasons
        from NORA'S model of Miles; `target=None` reasons from the peer's own
        self-model. Unit-wide by the same rationale (no `session_id`): Nora
        reasons over everything she has observed of that person across the
        unit's rooms, and the room roster gates disclosure at the tool layer.

        `POST /v3/workspaces/{ws}/peers/{peer_id}/chat`, body `{query,
        reasoning_level, target?}` → `{"content": "<answer>"}` (docs.honcho.dev/
        v3). **`reasoning_level` is chosen by the CALLER per
        question** (minimal→max), because it is the depth/latency trade and only
        the caller knows how hard the question is: a name-lookup takes `low`; a
        «what would he think about X» takes `high`. The model picks it in the
        `ask_memory` tool — Nora decides. NEVER streamed: one synchronous answer
        inside the loop budget.

        Returns the answer string, or None on any failure (unreachable, non-200,
        no `content`) — the best-effort `MemoryStore` contract: the caller says
        «I couldn't recall that», never guesses. Empty/whitespace content is None
        (Honcho has nothing to say), the honest `unavailable` for the tool.

        Its own timeout: this is a REASONING call (an LLM inside Honcho) taking
        ~14-19s even at `low`, so it is far slower than `representation`. Bounded
        by `_chat_timeout` so a slow reason cannot overrun the tag's budget; a
        higher `reasoning_level` spends more of that budget for more depth.

        Routed and scoped exactly as `representation` is (DEC-214): the named
        arm, `scope=<unit>`, which confines both the conclusions the answer
        reasons from and the messages it may read to the unit's rooms. No
        route: None, without a request."""
        route = await self._route(conversation_id=conversation_id, unit=unit)
        if route is None:
            return None
        if not await self._ensure_scope(route):
            return None
        body: dict[str, object] = {"query": query, "reasoning_level": reasoning_level}
        if target is not None:
            body["target"] = target
        if route.scope is not None:
            body["scope"] = route.scope
        result = await self._post(
            self._url(f"/peers/{peer_id}/chat", workspace=route.workspace),
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
        unit: str | None = None,
    ) -> MessageHits:
        """Semantic search over the raw MESSAGES (not derived conclusions) — the
        verb behind `search_memory`. Returns a LIST of message
        objects `{content, peer_id, session_id, …}` parsed to `(peer_id, content)`
        pairs, carried in a `MessageHits` beside the one bit the caller cannot
        recover: WHETHER THE SEARCH RAN. A bare list would be `[]` for a
        transport failure, a >=400, a malformed body and a genuine miss alike,
        so `search_memory` would report a Honcho outage as «nothing was said
        here», an ERROR reading as a PASS. Its OWN parse, not `_post`, because the response is a
        list,
        not the dict every other endpoint returns.

        SESSION-scoped when a `conversation_id` is given — `POST /sessions/{id}/
        search`, restricted to THIS channel's messages. That is the safe default
        and what `search_memory` uses: a workspace-wide search would surface a
        roster member's words from a PRIVATE DM (a different session in the same
        workspace) into this room — the raw-word blend the one-workspace topology
        does NOT license. Without a `conversation_id` it
        falls back to the workspace search, for an operator/probe that wants the
        whole unit. Media-only messages (the `￼` attachment placeholder) are
        dropped. DISCLOSURE remains the caller's: `search_memory` also keeps only
        the room's roster speakers and screens identifiers (R-18).

        Routed (DEC-214). With a `conversation_id`: that room's session, in
        its Honcho workspace — the session search takes no `scope`
        (`MessageSearchOptions`, honcho v3.1.2) and needs none, being one
        member session of the unit's scope already. With a `unit` and no
        room: the WORKSPACE search, passed `scope=<unit>`
        (`WorkspaceMessageSearchOptions.scope`, one name only), so the
        Dreamer's «anything said about X» reaches the unit's rooms and not
        the whole workspace. No route: `reachable=False`, no request."""
        route = await self._route(conversation_id=conversation_id, unit=unit)
        if route is None:
            return MessageHits(reachable=False)
        payload: dict[str, object] = {"query": query, "limit": limit}
        if conversation_id is not None:
            safe = _safe_conversation_key(conversation_id)
            url = self._url(f"/sessions/nora-conv-{safe}/search", workspace=route.workspace)
        else:
            url = self._url("/search", workspace=route.workspace)
            if route.scope is not None:
                if not await self._ensure_scope(route):
                    return MessageHits(reachable=False)
                payload["scope"] = route.scope
        try:
            timeout = aiohttp.ClientTimeout(total=self._representation_timeout)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.post(url, json=payload) as r,
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
            text = strip_mention_placeholders(content)   # drop the placeholder
            if text:
                out.append((peer, text))
        return MessageHits(reachable=True, hits=tuple(out))

    async def session_messages(
        self, *, conversation_id: str, page_size: int = 100,
    ) -> list[dict[str, Any]] | None:
        """Every message the conversation's Honcho session already holds, as
        `{"peer_id", "content", "metadata"}` dicts in Honcho's order — or
        `None` when that could not be read (no route, a >=400, a transport
        failure, a body that is not a page). A session that does not exist
        yet is `[]`: nothing held is an answer, not a failure.

        NOT on the `MemoryStore` port: its one caller is the operator's
        backfill (`nora.app.memory_backfill`), which must know what a session
        already holds before it writes, and no turn ever needs that.
        `POST /sessions/{id}/messages/list` (paginated, honcho v3.1.2
        `src/routers/messages.py::get_messages`) in the conversation's
        routed workspace; a 404 is the absent session. READ-ONLY: it never
        creates or marks the workspace (`claim=False`), and a workspace that
        belongs to someone else is `None`."""
        route = await self._route(conversation_id=conversation_id, claim=False)
        if route is None:
            return None
        session_id = f"nora-conv-{_safe_conversation_key(conversation_id)}"
        out: list[dict[str, Any]] = []
        page = 1
        while True:
            url = self._url(
                f"/sessions/{session_id}/messages/list?page={page}&size={page_size}",
                workspace=route.workspace,
            )
            try:
                timeout = aiohttp.ClientTimeout(total=self._representation_timeout)
                async with (
                    aiohttp.ClientSession(timeout=timeout) as session,
                    session.post(url, json={}) as r,
                ):
                    if r.status == 404:
                        return out
                    if r.status >= 400:
                        log.warning("honcho POST %s -> %s", url, r.status)
                        return None
                    body = await r.json()
            except Exception as e:  # noqa: BLE001 — Honcho is never load-bearing
                log.warning("honcho message list failed: %s", e)
                return None
            items = body.get("items") if isinstance(body, dict) else None
            if not isinstance(items, list):
                return None
            for item in items:
                if isinstance(item, dict):
                    metadata = item.get("metadata")
                    out.append({
                        "peer_id": item.get("peer_id"),
                        "content": item.get("content"),
                        "metadata": metadata if isinstance(metadata, dict) else {},
                    })
            pages = body.get("pages")
            if not isinstance(pages, int) or page >= pages or not items:
                return out
            page += 1

    async def list_conclusions(
        self, *, workspace: str, observer: str, page_size: int = 100,
    ) -> list[dict[str, Any]] | None:
        """Every conclusion `observer` holds in a NORA workspace's Honcho
        workspace, as `{"id", "observed_id", "content", "session_id", "level",
        "created_at"}` dicts — or `None` when that could not be read.
        Read-only.

        NOT on the `MemoryStore` port: its one caller is the operator's
        `nora memory carry-conclusions`. `POST /conclusions/list`, paged,
        filtered by `observer_id` (honcho v3.1.2 `src/utils/filter.py` maps it
        to the document's observer)."""
        route = await self._route(workspace=workspace, claim=False)
        if route is None:
            return None
        out: list[dict[str, Any]] = []
        page = 1
        while True:
            body = await self._post(
                self._url(
                    f"/conclusions/list?page={page}&size={page_size}",
                    workspace=route.workspace,
                ),
                {"filters": {"observer_id": observer}},
                self._representation_timeout,
            )
            items = (body or {}).get("items")
            if not isinstance(items, list):
                return None
            out.extend(
                {
                    "id": item.get("id"),
                    "observed_id": item.get("observed_id"),
                    "content": item.get("content"),
                    "session_id": item.get("session_id"),
                    "level": item.get("level", "explicit"),
                    "created_at": item.get("created_at"),
                }
                for item in items if isinstance(item, dict)
            )
            pages = (body or {}).get("pages")
            if not isinstance(pages, int) or page >= pages or not items:
                return out
            page += 1

    async def list_scopes(self, *, workspace: str) -> list[str] | None:
        """Every named scope in a NORA workspace's Honcho workspace
        (`POST /scopes/list`, paged), or `None` when unreadable. Read-only."""
        route = await self._route(workspace=workspace)
        if route is None:
            return None
        out: list[str] = []
        page = 1
        while True:
            body = await self._post(
                self._url(f"/scopes/list?page={page}&size=100", workspace=route.workspace),
                {}, self._card_timeout,
            )
            items = (body or {}).get("items")
            if not isinstance(items, list):
                return None
            out.extend(str(i["id"]) for i in items if isinstance(i, dict) and "id" in i)
            pages = (body or {}).get("pages")
            if not isinstance(pages, int) or page >= pages or not items:
                return out
            page += 1

    async def carry_to_scopes(
        self, *, units: list[str], rows: list[tuple[str, str]],
    ) -> bool:
        """Write each `(observed, content)` row as a conclusion of EVERY
        unit's scope — observer `scope.<unit>`, no session — the rows a
        named-scope read of those units serves (see `conclude`).

        A row is never split across two requests: each request carries whole
        rows (all their units), at most 100 conclusions, Honcho's cap, and one
        request is one transaction on Honcho's side
        (`crud.create_observations` commits once). So a failure leaves every
        row in all the scopes or in none. False at the first request that does
        not land. The units must share one Honcho workspace. Operator-only
        (`nora memory carry-conclusions`)."""
        routes = [await self._route(unit=unit) for unit in units]
        if any(r is None or r.scope is None for r in routes) or not routes:
            return False
        workspaces = {r.workspace for r in routes if r is not None}
        if len(workspaces) != 1:
            return False
        workspace = workspaces.pop()
        observers: list[str] = []
        for route in routes:
            if route is None or not await self._ensure_scope(route):
                return False
            observers.append(f"{SCOPE_PEER_PREFIX}{route.scope}")
        per_request = max(1, 100 // len(observers))
        for start in range(0, len(rows), per_request):
            batch = [
                {"content": content, "observer_id": observer, "observed_id": observed}
                for observed, content in rows[start:start + per_request]
                for observer in observers
            ]
            result = await self._post(
                self._url("/conclusions", workspace=workspace),
                {"conclusions": batch}, self._feed_timeout,
            )
            if result is None:
                return False
        return True

    # ── operator-only, EXPLICIT Honcho workspace (`nora memory detach-unit`) ──
    #
    # These name a Honcho workspace outright instead of routing, because
    # their subject is the workspace a unit LEFT, which no route reaches. Each refuses a workspace
    # that is not Nora's (the default, or one
    # carrying her marker) before touching it.

    async def _nora_s(self, workspace: str) -> bool:
        return workspace == self._router.default or (
            await self.inspect_workspace(workspace) == "owned"
        )

    async def session_holds_messages_in(
        self, *, workspace: str, conversation_id: str,
    ) -> bool | None:
        """Whether the conversation's session in THIS Honcho workspace holds
        at least one message; `None` when that could not be read or the
        workspace is someone else's. Read-only: an absent workspace holds
        nothing, and is not created."""
        if not await self._owned(workspace, claim=False):
            return None
        sid = f"nora-conv-{_safe_conversation_key(conversation_id)}"
        url = self._url(f"/sessions/{sid}/messages/list?page=1&size=1", workspace=workspace)
        try:
            async with (
                aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=self._representation_timeout)
                ) as session,
                session.post(url, json={}) as r,
            ):
                if r.status == 404:
                    return False
                if r.status >= 400:
                    log.warning("honcho POST %s -> %s", url, r.status)
                    return None
                body = await r.json()
        except Exception as e:  # noqa: BLE001 — Honcho is never load-bearing
            log.warning("honcho message list failed: %s", e)
            return None
        total = body.get("total") if isinstance(body, dict) else None
        return total > 0 if isinstance(total, int) else None

    async def session_peer_ids_in(
        self, *, workspace: str, session_id: str,
    ) -> list[str] | None:
        """The peers of one session in THIS Honcho workspace (scope peers
        left out); `[]` for a session that does not exist, `None` unreadable
        or someone else's. Read-only."""
        if not await self._owned(workspace, claim=False):
            return None
        url = self._url(f"/sessions/{session_id}/peers?page=1&size=100", workspace=workspace)
        try:
            async with (
                aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=self._card_timeout)
                ) as session,
                session.get(url) as r,
            ):
                if r.status == 404:
                    return []
                if r.status >= 400:
                    log.warning("honcho GET %s -> %s", url, r.status)
                    return None
                body = await r.json()
        except Exception as e:  # noqa: BLE001 — Honcho is never load-bearing
            log.warning("honcho session peers failed: %s", e)
            return None
        items = body.get("items") if isinstance(body, dict) else None
        if not isinstance(items, list):
            return None
        return sorted({
            str(i["id"]) for i in items
            if isinstance(i, dict) and isinstance(i.get("id"), str)
            and not i["id"].startswith(SCOPE_PEER_PREFIX)
        })

    async def delete_session_in(self, *, workspace: str, session_id: str) -> bool:
        """`DELETE /sessions/{id}` in THIS Honcho workspace: 202, the session
        inactive at once and its messages AND every document stored under it
        deleted by Honcho's queue (`src/crud/session.py::delete_session`,
        honcho v3.1.2 — documents are selected by `session_name`). A session
        already gone (404) is success. What it does NOT reach: conclusions
        with no session that a dream reasoned from those messages."""
        if not await self._nora_s(workspace):
            return False
        url = self._url(f"/sessions/{session_id}", workspace=workspace)
        try:
            async with (
                aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=self._card_timeout)
                ) as session,
                session.delete(url) as r,
            ):
                if r.status == 404:
                    return True
                if r.status >= 400:
                    log.warning("honcho DELETE %s -> %s", url, r.status)
                    return False
        except Exception as e:  # noqa: BLE001 — Honcho is never load-bearing
            log.warning("honcho DELETE %s failed: %s", url, e)
            return False
        return True

    async def conclusions_under_session_in(
        self, *, workspace: str, session_id: str,
    ) -> int | None:
        """How many conclusions THIS Honcho workspace still stores under a
        session — `POST /conclusions/list` filtered by `session_id`, read off
        the page's `total`. A session delete (202) removes them from a queue,
        not at once; this is how to know it has. `None` when unreadable."""
        if not await self._owned(workspace, claim=False):
            return None
        body = await self._post(
            self._url("/conclusions/list?page=1&size=1", workspace=workspace),
            {"filters": {"session_id": session_id}}, self._representation_timeout,
        )
        total = (body or {}).get("total")
        return total if isinstance(total, int) else None

    async def refresh_card_in(self, *, workspace: str, observer: str, observed: str) -> bool:
        """Schedule a `card_refresh` dream with `rebuild=True` for one
        (observer, observed) pair in THIS Honcho workspace — the card rebuilt
        only from the observations still in the collection, the form Honcho
        documents for use after removals (`ScheduleDreamRequest.rebuild`,
        `POST /workspaces/{id}/schedule_dream`, 204)."""
        if not await self._nora_s(workspace):
            return False
        url = self._url("/schedule_dream", workspace=workspace)
        body = {
            "observer": observer, "observed": observed,
            "dream_type": "card_refresh", "rebuild": True,
        }
        try:
            async with (
                aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=self._card_timeout)
                ) as session,
                session.post(url, json=body) as r,
            ):
                if r.status >= 400:
                    log.warning("honcho POST %s -> %s", url, r.status)
                    return False
        except Exception as e:  # noqa: BLE001 — Honcho is never load-bearing
            log.warning("honcho POST %s failed: %s", url, e)
            return False
        return True

    async def health(self) -> MemoryHealth:
        result = await self._get(f"{self._base}/health", self._card_timeout)
        return MemoryHealth(reachable=result is not None)

    async def aclose(self) -> None:
        """No-op: every request opens and closes its own `aiohttp.ClientSession`
        (see `_post`/`_get`), so there is no persistent session to release.
        Declared so `MemoryStore.aclose()` has a real implementation to check
        against."""
        return None
