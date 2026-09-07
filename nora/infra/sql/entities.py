"""`SqlEntityStore` — the shared entity registry's reader and writer.

`fact_entity` is a TIER-NEUTRAL NAME REGISTRY. It keeps its `fact_` prefix
because the table does, and the table keeps it because renaming a table with a
foreign key pointed at it is a migration and not a tidy-up — but an entity
("Big Walk") is a shared name, not any one tier's property. That is why this
module and its table outlived the knowledge tier that first built them: the
note tier holds a foreign key into it (`note_entity.entity_id`, issue #100).

`SqlNoteStore` reads `resolve` on every note write — never
`resolve_or_create`: that tier only ever links a name to an entity that
already exists, and never mints one. `unresolved_note_anchor_names` below is
its near-miss queue, feeding the `similar_entities` / `attach_alias` verbs the
judgement pass (`nora.language.note_entity_adaptor
.NoteEntityResolutionAdaptor`) uses.

**THE REGISTRY CURRENTLY HAS NO WRITER, AND THAT IS A KNOWN GAP.**
`resolve_or_create` is the DETERMINISTIC mint half — normalise, look up
`canonical_name` and `aliases`, create what is missing, `MERGE` semantics —
and its only production caller was the deleted fact store, which minted an
entity for the subject of every fact it wrote. Nothing mints today, so every
note anchor resolves to nothing and `note_entity` stays empty. The method is
kept and PINNED in
`tests/architecture/test_store_methods_have_production_callers.py` with that
reason, so the operator's choice — give the note tier a minting path, or drop
the registry too — is visible rather than arriving later as an empty table
nobody can explain.

The half that is missing on purpose is ADJUDICATION — genuine near-misses a
deterministic pass cannot settle (`HKO` / `Hong Kong Observatory`). That is a
Dreamer pass (`NoteEntityResolutionAdaptor`), and an unresolved name stays
unresolved rather than being guessed at.

**Normalisation is `nora.domain.facts.normalise_name` and nothing else.** It is
imported rather than reimplemented in SQL because NFKC and `casefold` are not
available in Postgres without an extension, and — more to the point — two
normalisations is two definitions of identity. The comparison work therefore
happens in Python over the workspace's standing rows, which `25` §4 already
prices as "one aggregate over a few thousand rows".

**Entity creation was a SIDE EFFECT of writing an edge** (§4), never a verb in
an adaptor's tool set and never a vocabulary seeded ahead of use: the registry
grew only where something actually asserted a relation. With the edge writer
deleted it grows nowhere — see the gap named above.
"""

from __future__ import annotations

import logging
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.facts import normalise_name
from nora.infra.sql.schema import FACT_ENTITY, NOTE

log = logging.getLogger(__name__)

#: 25 §4.1's measured floor for the trigram channel, against this database.
TRIGRAM_THRESHOLD = 0.3


class SqlEntityStore:
    """`SqlEntityStore(engine)` — a bare `AsyncEngine`, and nothing else.

    It took an optional injected `metrics` until the knowledge tier was
    deleted; both series it emitted (`nora_fact_entities_created_total`,
    `nora_fact_entity_unresolved`) were promotion's and pass 6's, and both
    went with them. A metrics collaborator with no recording site is the
    "declared metric with no call site" defect wearing a constructor
    argument, so it is gone rather than kept for a future caller."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

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
        this cannot open its own. A tier's own write verb is the caller that
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

    # -- the note tier's judgement queue (issue #100) ------------------------

    async def unresolved_note_anchor_names(
        self, *, workspace: str, limit: int,
    ) -> tuple[str, ...]:
        """The note tier's own near-miss queue: anchor-entity strings off
        STANDING notes in this workspace that resolve to no entity today.

        The knowledge tier had a twin of this read over `fact_candidate`; it
        went with that tier. This tier has no candidate queue, so the raw
        strings come from `note.anchor_entity_ids` — exactly what the
        deterministic half (`SqlNoteStore`'s own resolution, run inline on
        every create/propose) could not settle at write time. A name that resolves NOW but did not
        when the note was written (the entity arrived later, from either
        tier) is correctly excluded here too: it is not a near-miss, it is an
        exact match the deterministic pass will pick up the next time this
        note is touched, and offering it to the model would ask a question
        that already has a free answer.

        Deduplicated
        on the NORMALISED form (one name written three ways is one question),
        ordered by the SURFACE rather than left to dict/set iteration order
        (an unordered `[:limit]` would starve whichever name a given tick's
        hash order happens to place last), retired notes excluded (their
        anchors are no longer occasions anybody can act on).
        """
        if limit <= 0:
            return ()
        async with self._engine.connect() as conn:
            rows = (await conn.execute(
                sa.select(NOTE.c.anchor_entity_ids).where(
                    NOTE.c.workspace == workspace, NOTE.c.retired_at.is_(None),
                )
            )).all()
            known = (await conn.execute(
                sa.select(FACT_ENTITY.c.canonical_name, FACT_ENTITY.c.aliases)
                .where(FACT_ENTITY.c.workspace == workspace)
            )).all()
        resolved = {normalise_name(c) for c, _a in known}
        for _c, aliases in known:
            resolved |= {normalise_name(a) for a in aliases}

        surfaces = sorted({s for (arr,) in rows for s in (arr or ())})
        out: dict[str, str] = {}
        for surface in surfaces:
            n = normalise_name(surface)
            if n and n not in resolved and n not in out:
                out[n] = surface
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
