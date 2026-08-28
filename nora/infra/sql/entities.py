"""`SqlEntityStore` — the entity table's writer and reader, and pass 3.

`fact_entity` shipped with an alias mechanism, a foreign key from the note tier,
and **no writer and no reader anywhere in `nora/`**. This is both.

**Two verbs, and the split is `21-substrate-contract.md` §5's.**

- `resolve_or_create` is the DETERMINISTIC half: normalise, look up
  `canonical_name` and `aliases`, create what is missing. Free, no model, runs
  on every write. `MERGE` semantics, which is what a graph does natively and
  what a relational store has to do explicitly.
- `promote` is pass 3 (`25-knowledge-pipeline.md` §4): decide which object
  LITERALS have earned entity-hood, by arithmetic over the store's own
  contents. No model here either.

The half that is missing on purpose is ADJUDICATION — genuine near-misses a
deterministic pass cannot settle (`HKO` / `Hong Kong Observatory`). That is a
Dreamer pass, and until it exists an unresolved name stays unresolved rather
than being guessed at.

**Normalisation is `nora.domain.facts.normalise_name` and nothing else.** It is
imported rather than reimplemented in SQL because NFKC and `casefold` are not
available in Postgres without an extension, and — more to the point — two
normalisations is two definitions of identity. The comparison work therefore
happens in Python over the workspace's standing rows, which `25` §4 already
prices as "one aggregate over a few thousand rows".

**Entity creation is a SIDE EFFECT of writing an edge** (§4). There is no
entity verb in any adaptor's tool set, no entity-creation pass, no vocabulary
seeded ahead of use. The substrate grows only where something actually asserted
a relation.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.facts import PROMOTION_MAX_OBJECT_CHARS, normalise_name
from nora.infra.sql.schema import FACT, FACT_ENTITY

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# The literal veto (21 §6.1). RULES, not a list — a date is a shape, and an
# enumeration of dates would be a list nobody can finish. The operator-owned
# FILE (21 §10) carries the extra strings this room decides are not things; it
# is additive to these and never replaces them.
# ---------------------------------------------------------------------------

_URL = re.compile(r"^\w+://|^www\.", re.IGNORECASE)
# A leading negation: "not a thing", "no black tower", 「唔係…」, 「冇…」.
_NEGATION = re.compile(
    r"^(no|not|never|none)\b"
    r"|^(\u5187|\u5514\u4fc2|\u6c92\u6709|\u6ca1\u6709|\u4e0d\u662f)"
)
# Anything whose first token is a numeral, a spelled-out numeral, or a currency
# mark — a quantity, a price, a duration or a time of day. Deliberately crude:
# the cost of vetoing a real entity is a missing edge (recoverable, and §5's
# stated default), while the cost of promoting `"five"` is a node the graph
# will happily traverse to from every unrelated claim that mentions five.
_SPELLED = frozenset({
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
    "sixteen", "seventeen", "eighteen", "nineteen", "twenty", "thirty",
    "forty", "fifty", "sixty", "seventy", "eighty", "ninety", "hundred",
    "thousand", "million", "billion",
})
_NUMERIC_START = re.compile(r"^[\-+]?[$£€¥]?\d")
# The month/day arm needs a WORD BOUNDARY after it. Without one, `mar` vetoed
# "Marco's car", `may` vetoed "maybe", `sat` vetoed "satay" — a three-letter
# prefix match against a room that talks about people and food. Found by the
# veto's own positive-control test, which is why that test is not decoration.
_DATEISH = re.compile(
    r"^\d{1,4}[-/.]\d{1,2}([-/.]\d{1,4})?$"
    r"|^\d{1,2}:\d{2}"
    r"|^(mon|tue|wed|thu|fri|sat|sun|jan|feb|mar|apr|may|jun|jul|aug|sep|oct"
    r"|nov|dec)(day|ruary|ch|il|e|y|ust|tember|ober|ember)?\b",
    re.IGNORECASE,
)


def vetoed(surface: str, *, extra: frozenset[str] = frozenset()) -> bool:
    """True when this object string must never become an entity.

    §6.1's list, as rules: a date, a quantity, a price, a duration, a
    time-of-day (spelled-out numerals included), a leading negation, a URL, or
    anything over `PROMOTION_MAX_OBJECT_CHARS` — "a string longer than this is
    a sentence, not a thing".

    **A veto is not a refusal to store.** The literal stays exactly as written;
    it simply never gains an id. Nothing is lost by vetoing wrongly except a
    traversal, and §5's default-to-split says that is the survivable direction.
    """
    s = surface.strip()
    if not s or len(s) > PROMOTION_MAX_OBJECT_CHARS:
        return True
    if normalise_name(s) in extra:
        return True
    if _URL.search(s) or _NEGATION.match(s.casefold()) or _NUMERIC_START.match(s):
        return True
    if _DATEISH.match(s):
        return True
    first = normalise_name(s).split(" ")[0] if s else ""
    return first in _SPELLED


@dataclass(frozen=True)
class PromotionResult:
    """What one promotion pass did, so `09-measurement.md`'s rule holds: a
    change whose effect cannot be read back is not done."""

    entities_created: int
    entities_updated: int
    rows_resolved: int
    vetoed: int
    below_degree: int


class SqlEntityStore:
    """`SqlEntityStore(engine)` — a bare `AsyncEngine`, the same affordance
    `SqlFactStore` states for itself. `metrics` is optional and injected, never
    imported (`nora.infra` sits below `nora.obs`)."""

    def __init__(self, engine: AsyncEngine, *, metrics: Any = None) -> None:
        self._engine = engine
        self._metrics = metrics

    # -- the deterministic half ---------------------------------------------

    async def resolve(self, *, workspace: str, name: str) -> str | None:
        """The entity this name resolves to, or `None`.

        Exact after normalisation, against `canonical_name` AND `aliases` — the
        alias array is the whole point of the table, and a resolver that read
        only the canonical name would fragment on the first spelling variant it
        met. Never raises: this is reachable from a render path.
        """
        target = normalise_name(name)
        if not target:
            return None
        try:
            async with self._engine.connect() as conn:
                rows = (await conn.execute(
                    sa.select(
                        FACT_ENTITY.c.id,
                        FACT_ENTITY.c.canonical_name,
                        FACT_ENTITY.c.aliases,
                    ).where(FACT_ENTITY.c.workspace == workspace)
                )).all()
        except Exception:
            log.exception("entity resolve unavailable for workspace %s", workspace)
            return None
        for entity_id, canonical, aliases in rows:
            if target == normalise_name(canonical):
                return str(entity_id)
            if any(target == normalise_name(a) for a in aliases):
                return str(entity_id)
        return None

    async def resolve_or_create(
        self, conn: Any, *, workspace: str, canonical_name: str,
        aliases: tuple[str, ...], now_ms: int,
    ) -> tuple[str, bool]:
        """MERGE: the entity for this name, creating it if nothing holds it.
        Returns `(id, created)`.

        **Takes a CONNECTION, not the engine.** §4: the sink upserts the
        entities an edge implies IN THE SAME TRANSACTION that writes the edge,
        or the store can hold an edge whose endpoints do not exist and an
        entity nothing references. Atomicity is not a separate question, so
        this cannot open its own.

        **Alias accumulation is a set UNION, never a replace** (30 §4). A
        second sighting under a different spelling must add that spelling, not
        overwrite the one already there — replacing is how an alias table ends
        up holding only whatever was written last.

        `UNIQUE (workspace, canonical_name)` is what makes this safe under
        concurrent proposals from two adaptors, which will happen: more than
        one tier writes at the same entity.
        """
        existing = (await conn.execute(
            sa.select(FACT_ENTITY.c.id, FACT_ENTITY.c.canonical_name,
                      FACT_ENTITY.c.aliases)
            .where(FACT_ENTITY.c.workspace == workspace)
        )).all()
        target = normalise_name(canonical_name)
        for entity_id, canonical, current in existing:
            known = {normalise_name(canonical)} | {
                normalise_name(a) for a in current
            }
            if target in known or any(normalise_name(a) in known for a in aliases):
                # Deduped on the SURFACE, not the normalised form. 25 §4 says
                # aliases are "all distinct surface forms", and two spellings
                # that normalise alike are exactly the pair this table exists
                # to record — `Big Walk` and `big  walk` resolve to one entity
                # BECAUSE both spellings are written down, not despite it.
                # Deduping on the normalised form kept only whichever arrived
                # first and made the alias array unable to answer "how has the
                # room actually written this".
                seen_surfaces = {canonical, *current}
                new_aliases = [
                    a for a in (canonical_name, *aliases)
                    if a not in seen_surfaces
                ]
                if new_aliases:
                    await conn.execute(
                        sa.text(
                            "update fact_entity set aliases = aliases || :add "
                            "where id = :id"
                        ),
                        {"add": new_aliases, "id": entity_id},
                    )
                return str(entity_id), False

        stmt = (
            pg.insert(FACT_ENTITY)
            .values(
                workspace=workspace,
                canonical_name=canonical_name,
                aliases=list(dict.fromkeys(aliases)),
                created_at=now_ms,
            )
            # Concurrent creation of the same name: the loser reads the
            # winner's row rather than raising, which is what MERGE means.
            .on_conflict_do_update(
                constraint="fact_entity_workspace_name_key",
                set_={"canonical_name": FACT_ENTITY.c.canonical_name},
            )
            .returning(FACT_ENTITY.c.id, FACT_ENTITY.c.created_at)
        )
        entity_id, created_at = (await conn.execute(stmt)).one()
        return str(entity_id), created_at == now_ms

    # -- pass 3 --------------------------------------------------------------

    async def promote(
        self, *, workspace: str, now_ms: int,
        veto_extra: frozenset[str] = frozenset(),
    ) -> PromotionResult:
        """Pass 3: promote object literals that have earned entity-hood, and
        point their rows at the entity WITHOUT touching the words.

        The rule is §6.1's, and degree is the whole criterion:

        > promoted **iff** it survives the literal veto AND its normalised form
        > appears in at least one other edge position in that workspace — as
        > some standing fact's `subject_id`, or another standing fact's
        > `object_literal`.

        **A node of degree one is a string with extra steps** — there is
        nothing to traverse to. `made_by "House House"` stays a literal while
        it is the only House House claim, and both rows promote the moment a
        second one arrives. Traversal becomes possible exactly when it becomes
        meaningful.

        **Standing facts only, never candidates**: unadjudicated noise must not
        mint nodes.

        **Idempotent, and one transaction.** Re-running converges — a row
        already carrying its `object_entity_id` is left alone, alias
        accumulation is a union, and `resolve_or_create` returns the existing
        row. A concurrent re-run therefore cannot double-write. It is also
        deliberately NOT inside the adjudication transaction (30 §4:
        "promotion and embedding are downstream and idempotent, never inside
        it").

        The canonical name is the **most frequent surface form**, with ties
        broken on the lowest normalised spelling so a plan change cannot change
        which spelling wins. Every distinct surface form becomes an alias,
        which is how `big walk` and `Big Walk` stop being two rows that cannot
        see each other.
        """
        created = updated = resolved = vetoed_count = 0
        async with self._engine.begin() as conn:
            rows = (await conn.execute(
                sa.select(FACT.c.id, FACT.c.subject_id, FACT.c.object_literal,
                          FACT.c.object_entity_id)
                .where(FACT.c.workspace == workspace,
                       FACT.c.superseded_at.is_(None))
            )).all()

            # Every normalised form in EITHER edge position, with how often
            # each surface spelling was used. Subjects count toward degree
            # (§6.1's "as some standing fact's subject_id") but are not
            # themselves promoted here: only the OBJECT column has a pointer to
            # write to today.
            degree: Counter[str] = Counter()
            surfaces: dict[str, Counter[str]] = {}
            for _fid, subject, obj, _eid in rows:
                for surface in (subject, obj):
                    n = normalise_name(surface)
                    if not n:
                        continue
                    degree[n] += 1
                    surfaces.setdefault(n, Counter())[surface] += 1

            promotable: dict[str, list[str]] = {}
            below = 0
            for fid, _subject, obj, eid in rows:
                if eid is not None:
                    continue                       # already resolved
                if vetoed(obj, extra=veto_extra):
                    vetoed_count += 1
                    continue
                n = normalise_name(obj)
                if degree[n] < 2:
                    below += 1
                    continue
                promotable.setdefault(n, []).append(fid)

            for n, fact_ids in promotable.items():
                spellings = surfaces[n]
                canonical = min(
                    spellings, key=lambda s: (-spellings[s], normalise_name(s), s),
                )
                entity_id, was_created = await self.resolve_or_create(
                    conn, workspace=workspace, canonical_name=canonical,
                    aliases=tuple(spellings), now_ms=now_ms,
                )
                created += int(was_created)
                updated += int(not was_created)
                # TWO guards, and they are not the same guard. The Python
                # filter above (`eid is not None`) is the ordinary path: it
                # keeps an already-resolved row out of the work entirely, and
                # is what makes `rows_resolved` mean "rows this pass changed".
                # This predicate is the CONCURRENCY guard — two passes that
                # both read the row as unresolved before either writes, which
                # 30 §4 requires to converge. Postgres blocks the second
                # UPDATE on the first's row lock and re-evaluates it.
                #
                # **No test drives that race**, and the honest reason is that
                # it cannot be driven deterministically through `promote`: the
                # two transactions serialise at their connection acquisition,
                # so a `gather` of two calls runs them in sequence and the
                # Python filter catches the second. A test asserting otherwise
                # passed with this predicate deleted, which is worse than no
                # test — it is written down here instead.
                result = await conn.execute(
                    sa.update(FACT)
                    .where(FACT.c.id.in_(fact_ids),
                           FACT.c.object_entity_id.is_(None))
                    .values(object_entity_id=entity_id)
                )
                resolved += result.rowcount

        self._inc("nora_fact_entities_created_total", created)
        self._inc("nora_fact_objects_resolved_total", resolved)
        self._inc("nora_fact_objects_vetoed_total", vetoed_count)
        self._inc("nora_fact_objects_below_degree_total", below)
        return PromotionResult(
            entities_created=created, entities_updated=updated,
            rows_resolved=resolved, vetoed=vetoed_count, below_degree=below,
        )

    def _inc(self, name: str, value: int) -> None:
        # Zero is not counted: a counter incremented by 0 is indistinguishable
        # from one never touched, and prometheus renders both as the series
        # existing. The pass having RUN is `nora_dream_run_total`'s job.
        if self._metrics is not None and value:
            self._metrics.inc(name, float(value))
