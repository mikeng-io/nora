"""`SqlEntityStore` — the entity table's writer and reader, and pass 3.

`fact_entity` shipped with an alias mechanism, a foreign key from the note tier,
and **no writer and no reader anywhere in `nora/`**. This is both.

**Two verbs, and the split is `21-substrate-contract.md` §5's.**

- `resolve_or_create` is the DETERMINISTIC half: normalise, look up
  `canonical_name` and `aliases`, create what is missing. Free, no model.
  `MERGE` semantics, which is what a graph does natively and what a relational
  store has to do explicitly. **It runs on every FACT write, on the SUBJECT
  end**, from `SqlFactStore._resolve_subjects` inside the edge's own
  transaction (§4) — the sentence here used to say "on every write" while the
  only caller was `promote`, which is the document and the code disagreeing
  with the code losing silently. Subjects and not objects because §6 writes the
  object as a literal and derives entity-hood later (R-78); facts and not
  candidates because unadjudicated noise must not mint nodes.
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

#: 25 §4.1's measured floor for the trigram channel, against this database.
TRIGRAM_THRESHOLD = 0.3

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
# ---------------------------------------------------------------------------
# The QUANTITY vocabulary, and the rule is WHOLE-STRING, not leading-token.
#
# The first version vetoed on the first token alone, and a review reproduced
# what that costs in this room: `Sun Hung Kai`, `Seven Sisters`, `Ten Thousand
# Buddhas Monastery`, `1881 Heritage`, `one piece` and `May Chan` were all
# refused entity-hood — a weekday, a spelled numeral or a month name happening
# to START a real name. The docstring priced that as "a missing edge
# (recoverable)"; for `Sun Hung Kai` in a Hong Kong room it is a permanent hole
# exactly where the graph would be most useful.
#
# So a string is a quantity when EVERY whitespace-separated token is one.
# `three hours` and `9 August` are; `Seven Sisters` is not, because `sisters` is
# not. **Tokenising on whitespace ONLY, never on punctuation**, is what keeps
# `7-Eleven` a single unknown token rather than the two numerals it splits into.
_SPELLED = frozenset({
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
    "sixteen", "seventeen", "eighteen", "nineteen", "twenty", "thirty",
    "forty", "fifty", "sixty", "seventy", "eighty", "ninety", "hundred",
    "thousand", "million", "billion",
})
_UNITS = frozenset({
    "second", "seconds", "minute", "minutes", "mins", "hour", "hours", "hrs",
    "day", "days", "week", "weeks", "month", "months", "year", "years", "yrs",
    "am", "pm", "dollar", "dollars", "hkd", "usd", "cent", "cents",
    "percent", "%", "kg", "km", "cm", "mm", "lb", "lbs",
})
_CALENDAR = frozenset({
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
    "sunday", "mon", "tue", "tues", "wed", "thu", "thur", "thurs", "fri",
    "sat", "sun", "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct",
    "nov", "dec",
})
_QUANTITY_WORDS = _SPELLED | _UNITS | _CALENDAR
_NUMERIC_TOKEN = re.compile(r"^[\-+]?[$£€¥]?\d+([.,:/]\d+)*(st|nd|rd|th|%)?$")

# The same rule for CJK, which the first version did not have AT ALL — so every
# case §6.1 names was admitted the moment the room wrote it in the language it
# actually uses: 三個月, 五蚊, 十分鐘, 兩年. CJK has no whitespace, so the test
# is over CHARACTERS: drawn entirely from numerals and units, starting with a
# numeral.
#
# Narrow deliberately. 七姊妹 (Seven Sisters), 十萬個為什麼 and 大浪灣 each carry
# a character outside the two sets and are admitted, which is the direction to
# fail in.
_CJK_NUMERALS = frozenset("〇零一二三四五六七八九十百千萬万億兩两半")
_CJK_UNITS = frozenset("個个月年日時时分鐘钟秒週周星期蚊元塊块毫仙點点號号")

_DATEISH = re.compile(r"^\d{1,4}[-/.]\d{1,2}([-/.]\d{1,4})?$|^\d{1,2}:\d{2}")


def _is_quantity(s: str) -> bool:
    """True when the WHOLE string is a quantity, date, price or duration."""
    if _DATEISH.match(s):
        return True
    tokens = [t for t in normalise_name(s).split(" ") if t]
    if tokens and all(
        t in _QUANTITY_WORDS or _NUMERIC_TOKEN.match(t) for t in tokens
    ):
        return True
    packed = "".join(s.split())
    return bool(
        packed
        and packed[0] in _CJK_NUMERALS
        and all(c in _CJK_NUMERALS or c in _CJK_UNITS for c in packed)
    )


def vetoed(surface: str, *, extra: frozenset[str] = frozenset()) -> bool:
    """True when this object string must never become an entity.

    §6.1's list, as rules: a date, a quantity, a price, a duration, a
    time-of-day (spelled-out numerals included), a leading negation, a URL, or
    anything over `PROMOTION_MAX_OBJECT_CHARS` — "a string longer than this is
    a sentence, not a thing".

    **The quantity rule is WHOLE-STRING, and that correction is the whole
    reason `_is_quantity` exists.** Vetoing on the first token alone refused
    `Sun Hung Kai`, `Seven Sisters`, `Ten Thousand Buddhas Monastery`, `1881
    Heritage`, `one piece` and `May Chan` — a weekday, a spelled numeral or a
    month name happening to start a real name. And it had no CJK arm at all,
    so every case §6.1 names was admitted the moment the room wrote it in
    Cantonese: 三個月, 五蚊, 十分鐘.

    **A veto is not a refusal to store.** The literal stays exactly as written;
    it simply never gains an id. Nothing is lost by vetoing wrongly except a
    traversal, and §5's default-to-split says that is the survivable direction.
    """
    s = surface.strip()
    if not s or len(s) > PROMOTION_MAX_OBJECT_CHARS:
        return True
    if normalise_name(s) in extra:
        return True
    if _URL.search(s) or _NEGATION.match(s.casefold()):
        return True
    return _is_quantity(s)


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

    def __init__(
        self, engine: AsyncEngine, *,
        veto_extra: frozenset[str] = frozenset(),
        metrics: Any = None,
    ) -> None:
        """`veto_extra` is 21 §10's operator-owned list — the extra strings
        THIS room decides are not things, versioned and hashed like an
        objective. Normalised here so the file can be written in whatever case
        its author uses.

        It was a `promote` parameter that nothing ever passed, which is "a
        declared metric with no call site" wearing a different hat: the rule
        existed, the plumbing did not, and no reading would have shown the
        difference.
        """
        self._engine = engine
        self._veto_extra = frozenset(
            normalise_name(v) for v in veto_extra if v.strip()
        )
        self._metrics = metrics

    # -- the deterministic half ---------------------------------------------

    async def resolve(self, *, workspace: str, name: str) -> str | None:
        """The entity this name resolves to, or `None` for no such entity.

        Exact after normalisation, against `canonical_name` AND `aliases` — the
        alias array is the whole point of the table, and a resolver that read
        only the canonical name would fragment on the first spelling variant it
        met.

        **RAISES on an unreachable store, and that is the correction.** It used
        to catch, log and return `None` — the same value it returns for "no
        such entity" — so an outage read as «there is no such thing», which is
        R-27's forbidden collapse in three words. The justification given was
        "reachable from a render path", but nothing calls this from a render
        path or from anywhere else in `nora/` yet, so the degradation was
        hypothetical and the collapse was real. A caller that needs to survive
        an outage can catch; a caller that cannot tell the two apart has
        already lost the distinction.
        """
        target = normalise_name(name)
        if not target:
            return None
        async with self._engine.connect() as conn:
            rows = (await conn.execute(
                sa.select(
                    FACT_ENTITY.c.id,
                    FACT_ENTITY.c.canonical_name,
                    FACT_ENTITY.c.aliases,
                ).where(FACT_ENTITY.c.workspace == workspace)
            )).all()
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
        this cannot open its own. `SqlFactStore.propose` is the caller that
        makes that true — it passes its own `conn`, so a failed fact insert
        rolls back the entities it implied along with it.

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
                # `dict.fromkeys` because the incoming list is not distinct
                # from itself: `promote` passes `aliases=tuple(spellings)`,
                # which CONTAINS the canonical name it also passes. Without
                # this the array grew a duplicate on every pass after a new
                # spelling became the most frequent one — and the idempotence
                # test could not see it, because it never introduced a new
                # spelling between passes.
                new_aliases = [
                    a for a in dict.fromkeys((canonical_name, *aliases))
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
            # `xmax = 0` is Postgres's own insert-vs-update discriminator for
            # ON CONFLICT. `created_at == now_ms` was a PROXY for identity, and
            # it could report "created" for a row it did not create: the loser
            # of a race between two promotion passes reads the winner's row,
            # and two passes in the same tick share `now_ms`. That inflates
            # `nora_fact_entities_created_total` — a counter whose whole job is
            # to say how often the substrate actually grew.
            .returning(FACT_ENTITY.c.id, sa.text("xmax = 0"))
        )
        entity_id, inserted = (await conn.execute(stmt)).one()
        return str(entity_id), bool(inserted)

    # -- pass 6's candidate query --------------------------------------------

    async def unresolved_names(
        self, *, workspace: str, limit: int,
    ) -> tuple[str, ...]:
        """Subjects of PENDING candidates that resolve to no entity — the names
        the deterministic pass could not settle, which is exactly what pass 6
        adjudicates (21 §5).

        Pending only, and subjects only. A settled candidate's name is somebody
        else's problem, and the object axis is promotion's (§6.1): an object
        earns an entity by degree, never by being recognised.

        Deduplicated on the NORMALISED form, so the same name written three
        ways is one thing to adjudicate rather than three — the model should
        never be asked the same question twice in one prompt, and a prompt that
        did would read as three separate near-misses.

        **ORDERED, and this had no ordering clause at all** — which is worse
        than the ties elsewhere in this tier, not better: `SELECT DISTINCT`
        returns rows in whatever order the hash aggregate produces, and that
        shifts with statistics and with parallelism. So `[:limit]` over it
        chose pass 6's per-tick entity-resolution budget arbitrarily, which
        names got adjudicated varied run to run, and a name past the limit
        could be starved indefinitely. It has no `ORDER BY` to append to,
        which is why a grep for one could not find it.

        Ordered by the SURFACE, which is a total order over the deduped key
        and also settles which spelling represents a group. It is deliberately
        NOT a ranking: whether a more-recurrent unresolved name deserves the
        budget ahead of a rarer one is a real question, and answering it here
        would be inventing a priority the design has not decided. Determinism
        first; a ranking is a separate decision with its own evidence.
        """
        if limit <= 0:
            return ()
        async with self._engine.connect() as conn:
            rows = (await conn.execute(
                sa.text(
                    "select distinct c.subject_id from fact_candidate c "
                    "where c.workspace = :ws and c.status = 'pending' "
                    "order by c.subject_id"
                ),
                {"ws": workspace},
            )).all()
            known = (await conn.execute(
                sa.select(FACT_ENTITY.c.canonical_name, FACT_ENTITY.c.aliases)
                .where(FACT_ENTITY.c.workspace == workspace)
            )).all()
        resolved = {normalise_name(c) for c, _a in known}
        for _c, aliases in known:
            resolved |= {normalise_name(a) for a in aliases}

        out: dict[str, str] = {}
        for (subject,) in rows:
            n = normalise_name(subject)
            if n and n not in resolved and n not in out:
                out[n] = subject
        return tuple(out.values())[:limit]

    async def similar_entities(
        self, *, workspace: str, name: str, limit: int,
        threshold: float = TRIGRAM_THRESHOLD,
    ) -> tuple[tuple[str, str], ...]:
        """(id, canonical_name) for entities whose name is trigram-similar to
        `name`, best first. The near-misses pass 6 exists to decide.

        **`pg_trgm`, at 0.3, and its limits are measured rather than assumed**
        (25 §4.1, against this database): `Big Walk`/`big walk` scores 1.000,
        `大浪灣`/`大浪湾` 0.333 — trigram catching what the vendored ASCII-only
        matcher cannot — while `打風`/`打风` scores 0.200 and
        `香港天文台`/`天文台` 0.250, because a two-character word makes too few
        trigrams. `Hong Kong Observatory`/`HKO` scores 0.043.

        So this channel does NOT cover abbreviation or short CJK, and that is
        not a defect to fix here: §4.1 says the VECTOR channel exists for those
        rows. This method is one of three, and the third arrives with pgvector.
        Until then a name it cannot reach is simply never offered to the model,
        which is a missing merge — §5's survivable direction — and not a wrong
        one.
        """
        if limit <= 0:
            return ()
        async with self._engine.connect() as conn:
            rows = (await conn.execute(
                sa.text(
                    "select id::text, canonical_name, "
                    "  greatest(similarity(canonical_name, :n), "
                    "           coalesce((select max(similarity(a, :n)) "
                    "                     from unnest(aliases) a), 0)) as score "
                    "from fact_entity "
                    "where workspace = :ws "
                    "order by score desc, canonical_name asc "
                    "limit :lim"
                ),
                {"n": name, "ws": workspace, "lim": limit},
            )).all()
        # Ranked in SQL, THRESHOLDED here: `similarity()` is what the index
        # would serve, and applying the floor in Python keeps the number in one
        # place next to the measurement that chose it.
        return tuple(
            (rid, canonical) for rid, canonical, score in rows
            if score is not None and float(score) >= threshold
        )

    async def attach_alias(
        self, *, workspace: str, entity_id: str, alias: str,
    ) -> bool:
        """Record `alias` as another name for `entity_id`. True if it was new.

        **Attaching an alias is pass 6's ONLY write, and that is the whole
        safety argument.** 21 §5: "two entities wrongly kept apart is a missing
        edge, visible as a thin neighbourhood, and fixable by a later merge.
        Two wrongly merged is a corrupted node that has already absorbed both
        sets of edges, and unmerging it means knowing which edge came from
        which — which is exactly the provenance question §8 leaves open."

        So this pass never MERGES two existing entity rows. It attaches a name
        to one, which is reversible by removing it from the array; merging
        rows is not reversible at all until the provenance question is
        answered. A model that thinks two entities are one can say so and be
        wrong, and the cost is one extra name on one row.
        """
        async with self._engine.begin() as conn:
            row = (await conn.execute(
                sa.select(FACT_ENTITY.c.canonical_name, FACT_ENTITY.c.aliases)
                .where(FACT_ENTITY.c.id == entity_id,
                       FACT_ENTITY.c.workspace == workspace)
            )).one_or_none()
            if row is None:
                return False
            canonical, aliases = row
            if alias in {canonical, *aliases}:
                return False
            await conn.execute(
                sa.text(
                    "update fact_entity set aliases = aliases || :a "
                    "where id = :i"
                ),
                {"a": [alias], "i": entity_id},
            )
        return True

    # -- pass 3 --------------------------------------------------------------

    async def promote(
        self, *, workspace: str, now_ms: int,
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
            # Degree is counted over DISTINCT FACTS, not over endpoints. The
            # first version added one per endpoint, so a single row whose
            # subject and object normalise alike — `big walk is_called Big
            # Walk`, which is precisely the shape this table exists for —
            # reached degree 2 FROM ITSELF and minted an entity whose only edge
            # is a self-loop. §6.1's own justification is that "a node of
            # degree one is a string with extra steps: there is nothing to
            # traverse to", and a self-loop is exactly that.
            seen_in: dict[str, set[str]] = {}
            surfaces: dict[str, Counter[str]] = {}
            for fid, subject, obj, _eid in rows:
                for surface in (subject, obj):
                    n = normalise_name(surface)
                    if not n:
                        continue
                    seen_in.setdefault(n, set()).add(fid)
                    surfaces.setdefault(n, Counter())[surface] += 1
            degree = {n: len(fids) for n, fids in seen_in.items()}

            promotable: dict[str, list[str]] = {}
            below = 0
            for fid, _subject, obj, eid in rows:
                if eid is not None:
                    continue                       # already resolved
                if vetoed(obj, extra=self._veto_extra):
                    vetoed_count += 1
                    continue
                n = normalise_name(obj)
                if degree.get(n, 0) < 2:
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

        # Counters for the EVENTS, gauges for the CENSUS. `created` and
        # `resolved` are things this pass did; `vetoed` and `below` are
        # recomputed from the whole standing table every pass, so adding them
        # to a counter would measure ticks times rows. Set unconditionally,
        # including at zero: 0 means the pass ran and found none, ABSENT means
        # it has never run for this workspace, and that is the distinction that
        # says whether promotion is wired at all.
        self._inc("nora_fact_entities_created_total", created)
        self._inc("nora_fact_objects_resolved_total", resolved)
        self._set("nora_fact_objects_vetoed", vetoed_count, workspace=workspace)
        self._set("nora_fact_objects_below_degree", below, workspace=workspace)
        return PromotionResult(
            entities_created=created, entities_updated=updated,
            rows_resolved=resolved, vetoed=vetoed_count, below_degree=below,
        )

    def _set(self, name: str, value: int, **labels: object) -> None:
        if self._metrics is not None:
            self._metrics.gauge(name, float(value), **labels)

    def _inc(self, name: str, value: int) -> None:
        # Zero is not counted: a counter incremented by 0 is indistinguishable
        # from one never touched, and prometheus renders both as the series
        # existing. The pass having RUN is `nora_dream_run_total`'s job.
        if self._metrics is not None and value:
            self._metrics.inc(name, float(value))
