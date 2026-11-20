"""The Cognitive Graph's tables (design/cognition): registry, entity, claim,
intention and edge.

Registered on the shared `schema.METADATA`; kept in their own module because
`schema.py` is at its file-size baseline. The same plane columns are declared
once and shared across the four row tables so a plane cannot drift per kind.
The CHECK value sets here are declared again as `Literal`s in
`nora/memory/domain/` — the unit tests compare them without a database, so a
vocabulary change lands in both places or fails.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import get_args

import sqlalchemy as sa
from pgvector.sqlalchemy import HALFVEC
from sqlalchemy.dialects import postgresql as pg

from nora.domain.graph_registration import SourceKind
from nora.infra.sql.graph_stimulus_schema import graph_stimulus_claim_table, graph_stimulus_table
from nora.infra.sql.intention_verdict_schema import (
    attach_verdict_constraints,
    intention_verdict_table,
)
from nora.infra.sql.lesson_tally_schema import attach_lesson_tally_constraints, lesson_tally_table
from nora.infra.sql.schema import (
    EMBEDDING_DIMENSIONS,
    EMBEDDING_INDEXED_DIMENSIONS,
    METADATA,
)

INTENTION_VERDICT = intention_verdict_table(METADATA)
LESSON_TALLY = lesson_tally_table(METADATA)
GRAPH_STIMULUS = graph_stimulus_table(METADATA)
GRAPH_STIMULUS_CLAIM = graph_stimulus_claim_table(METADATA)

def _planes(
    basis_check: str, state_check: str, kind: str, *,
    table: str | None = None, row_id: str = "id",
) -> list[sa.schema.SchemaItem]:
    """The columns and rules every row and edge carries under one shape."""
    table = f"{table}_graph" if table else kind
    return [
        sa.Column(row_id, sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("basis", sa.Text, nullable=False),
        sa.Column("reasoning", sa.Text),
        sa.Column("confidence", sa.Text),
        sa.Column("unit", sa.Text, nullable=False),
        sa.Column("origin_room", sa.Text),
        sa.Column("persons", pg.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("excluded_persons", pg.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("lineage", pg.JSONB(none_as_null=True), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("exposure", sa.Text, nullable=False),
        sa.Column("writer", sa.Text, nullable=False),
        sa.Column("occasion", sa.Text),
        sa.Column("pass", sa.Text),
        sa.Column("trace", sa.Text),
        sa.Column("model", sa.Text),
        sa.Column("rev_code", sa.Text),
        sa.Column("rev_instruction", sa.Text),
        sa.Column("rev_skill", sa.Text),
        sa.Column("rev_config", sa.Text),
        sa.Column("rev_identity", sa.Text),
        sa.Column("rev_policy", sa.Text),
        sa.Column("occurred_at", sa.DateTime(timezone=True)),
        sa.Column("asserted_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("valid_from", sa.DateTime(timezone=True)),
        sa.Column("valid_to", sa.DateTime(timezone=True)),
        sa.Column("replaces", sa.Text),
        sa.Column("state", sa.Text, nullable=False),
        sa.CheckConstraint(f"kind = '{kind}'", name=f"ck_{table}_kind"),
        sa.CheckConstraint(basis_check, name=f"ck_{table}_basis"),
        sa.CheckConstraint(state_check, name=f"ck_{table}_state"),
        sa.CheckConstraint(
            "exposure IN ('external', 'internal', 'operator')", name=f"ck_{table}_exposure"
        ),
        sa.CheckConstraint(
            "reasoning IS NULL OR reasoning IN ('told', 'deduced', 'induced', 'abduced')",
            name=f"ck_{table}_reasoning",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR confidence IN ('low', 'medium', 'high')",
            name=f"ck_{table}_confidence",
        ),
        sa.CheckConstraint(
            "(basis = 'inferred') = (reasoning IS NOT NULL AND confidence IS NOT NULL)",
            name=f"ck_{table}_inferred_detail",
        ),
        sa.ForeignKeyConstraint(
            [row_id, "unit", "kind"],
            ["registry.id", "registry.unit", "registry.kind"],
            name=f"fk_{table}_registry",
        ),
        sa.ForeignKeyConstraint(
            ["replaces", "unit", "kind"],
            ["registry.id", "registry.unit", "registry.kind"],
            name=f"fk_{table}_replaces",
        ),
        sa.UniqueConstraint("replaces", name=f"uq_{table}_replaces"),
    ]


REGISTRY = sa.Table(
    "registry",
    METADATA,
    sa.Column("id", sa.Text, nullable=False),
    sa.Column("unit", sa.Text, nullable=False),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("state", sa.Text, nullable=False, server_default="live"),
    sa.Column(
        "registered_at", sa.DateTime(timezone=True), nullable=False,
        server_default=sa.func.now(),
    ),
    sa.Column("content_hash", sa.Text),
    sa.Column("tombstoned_at", sa.DateTime(timezone=True)),
    sa.Column("occurred_at", sa.DateTime(timezone=True)),
    sa.PrimaryKeyConstraint("id", "unit", name="pk_registry"),
    sa.UniqueConstraint("id", "unit", "kind", name="uq_registry_id_unit_kind"),
    sa.CheckConstraint(
        "kind IN ('entity', 'item', 'aggregate', 'claim', 'intention', 'edge')",
        name="ck_registry_kind",
    ),
    sa.CheckConstraint("state IN ('live', 'tombstoned')", name="ck_registry_state"),
    sa.CheckConstraint(
        "state <> 'tombstoned' OR content_hash IS NOT NULL",
        name="ck_registry_tombstone_hash",
    ),
    sa.CheckConstraint(
        "(state = 'tombstoned') = (tombstoned_at IS NOT NULL)",
        name="ck_registry_tombstone_at",
    ),
)

EXPIRED_COVERAGE = sa.Table(
    "expired_coverage", METADATA,
    sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
    sa.Column("unit", sa.Text, nullable=False),
    sa.Column("source", sa.Text, nullable=False),
    sa.Column("person", sa.Text),
    sa.Column("from", sa.DateTime(timezone=True), nullable=False),
    sa.Column("to", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('"from" <= "to"', name="ck_expired_coverage_order"),
)
sa.Index("idx_expired_coverage_unit_to", EXPIRED_COVERAGE.c.unit, EXPIRED_COVERAGE.c["to"])

ENTITY = sa.Table(
    "entity",
    METADATA,
    *_planes(
        "basis IN ('computed', 'inferred')",
        "state IN ('admitted', 'merged', 'retired', 'tombstoned')",
        "entity",
    ),
    sa.Column("name", sa.Text),
    sa.Column("aliases", pg.ARRAY(sa.Text)),
    sa.Column("pointer_kind", sa.Text),
    sa.Column("pointer_ref", sa.Text),
    sa.Column("source_ref_kind", sa.Text),
    sa.Column("source_ref_value", sa.Text),
    sa.PrimaryKeyConstraint("id", "unit", name="pk_entity"),
    sa.CheckConstraint(
        "state = 'tombstoned' OR name IS NOT NULL", name="ck_entity_name"
    ),
    sa.CheckConstraint(
        "pointer_kind IS NULL OR pointer_kind IN ('person', 'room', 'unit', 'nora')",
        name="ck_entity_pointer_kind",
    ),
    sa.CheckConstraint(
        "(pointer_kind IS NULL) = (pointer_ref IS NULL)", name="ck_entity_pointer_pair"
    ),
    sa.CheckConstraint(
        "pointer_kind IS NULL OR basis = 'computed'", name="ck_entity_pointer_basis"
    ),
    sa.CheckConstraint(
        f"source_ref_kind IS NULL OR source_ref_kind IN {get_args(SourceKind)!r}",
        name="ck_entity_source_ref_kind",
    ),
    sa.CheckConstraint(
        "(source_ref_kind IS NULL) = (source_ref_value IS NULL)", name="ck_entity_source_ref_pair",
    ),
)

CLAIM = sa.Table(
    "claim",
    METADATA,
    *_planes(
        "basis IN ('inferred')",
        "state IN ('live', 'superseded', 'hidden', 'awaiting_weighing', 'retired',"
        " 'tombstoned')",
        "claim",
    ),
    sa.Column("part", sa.Text, nullable=False),
    sa.Column("content", pg.JSONB(none_as_null=True)),
    sa.Column("prediction", sa.Text),
    sa.Column("window_end", sa.DateTime(timezone=True)),
    sa.PrimaryKeyConstraint("id", "unit", name="pk_claim"),
    sa.CheckConstraint(
        "part IN ('fact', 'trait', 'guess', 'lesson', 'situation')", name="ck_claim_part",
    ),
    sa.CheckConstraint(
        "state = 'tombstoned' OR content IS NOT NULL", name="ck_claim_content"
    ),
    sa.CheckConstraint(
        "state = 'tombstoned' OR part <> 'guess' "
        "OR (prediction IS NOT NULL AND window_end IS NOT NULL)",
        name="ck_claim_guess",
    ),
    sa.CheckConstraint(
        "part = 'guess' OR (prediction IS NULL AND window_end IS NULL)",
        name="ck_claim_non_guess",
    ),
)

INTENTION = sa.Table(
    "intention",
    METADATA,
    *_planes(
        "basis IN ('committed')",
        "state IN ('open', 'ended', 'superseded', 'tombstoned')",
        "intention",
    ),
    sa.Column("owner", sa.Text, nullable=False),
    sa.Column("owed_to", sa.Text, nullable=False),
    sa.Column("form", sa.Text, nullable=False),
    sa.Column("done_when_form", sa.Text),
    sa.Column("done_when", pg.JSONB(none_as_null=True)),
    sa.Column("holds_while_form", sa.Text),
    sa.Column("holds_while", pg.JSONB(none_as_null=True)),
    sa.Column("drop_when_form", sa.Text),
    sa.Column("drop_when", pg.JSONB(none_as_null=True)),
    sa.Column("deadline", sa.DateTime(timezone=True)),
    sa.Column("review_time", sa.DateTime(timezone=True)),
    sa.Column("delivery_mode", sa.Text, nullable=False),
    sa.Column("trigger", pg.JSONB(none_as_null=True)),
    sa.Column("ending", sa.Text),
    sa.Column("ended_at", sa.DateTime(timezone=True)),
    sa.PrimaryKeyConstraint("id", "unit", name="pk_intention"),
    sa.CheckConstraint("form IN ('achieve', 'maintain')", name="ck_intention_form"),
    sa.CheckConstraint(
        "done_when_form IS NULL OR done_when_form IN "
        "('act_event', 'person_event', 'predicate', 'untyped')",
        name="ck_intention_done_when_form",
    ),
    sa.CheckConstraint(
        "holds_while_form IS NULL OR holds_while_form IN "
        "('act_event', 'person_event', 'predicate', 'deadline', 'untyped')",
        name="ck_intention_holds_while_form",
    ),
    sa.CheckConstraint(
        "drop_when_form IS NULL OR drop_when_form IN "
        "('act_event', 'person_event', 'predicate', 'deadline', 'untyped')",
        name="ck_intention_drop_when_form",
    ),
    sa.CheckConstraint(
        "state = 'tombstoned' OR (form = 'achieve') = (done_when_form IS NOT NULL)",
        name="ck_intention_form_pair",
    ),
    sa.CheckConstraint(
        "state = 'tombstoned' "
        "OR (done_when_form IS NULL) <> (holds_while_form IS NULL)",
        name="ck_intention_condition_pair",
    ),
    sa.CheckConstraint(
        "(done_when_form IS NULL) = (done_when IS NULL)", name="ck_intention_done_pair"
    ),
    sa.CheckConstraint(
        "(holds_while_form IS NULL) = (holds_while IS NULL)",
        name="ck_intention_holds_pair",
    ),
    sa.CheckConstraint(
        "(drop_when_form IS NULL) = (drop_when IS NULL)", name="ck_intention_drop_pair"
    ),
    sa.CheckConstraint(
        "state = 'tombstoned' OR done_when_form <> 'untyped' OR deadline IS NOT NULL",
        name="ck_intention_untyped_deadline",
    ),
    sa.CheckConstraint(
        "state = 'tombstoned' OR holds_while_form <> 'untyped' "
        "OR (drop_when_form IS NOT NULL AND drop_when_form = 'deadline')",
        name="ck_intention_untyped_drop",
    ),
    sa.CheckConstraint(
        "delivery_mode IN ('choose_moment', 'when_it_comes_up')",
        name="ck_intention_delivery_mode",
    ),
    sa.CheckConstraint(
        "state = 'tombstoned' "
        "OR (delivery_mode = 'choose_moment') = (trigger IS NOT NULL)",
        name="ck_intention_trigger",
    ),
    sa.CheckConstraint(
        "form <> 'achieve' OR ending IS NULL "
        "OR ending IN ('achieved', 'dropped', 'leaked', 'unknown')",
        name="ck_intention_achieve_ending",
    ),
    sa.CheckConstraint(
        "form <> 'maintain' OR ending IS NULL OR ending IN ('ceased', 'dropped')",
        name="ck_intention_maintain_ending",
    ),
    sa.CheckConstraint(
        "(ending IS NULL) = (ended_at IS NULL)", name="ck_intention_ended_pair"
    ),
    sa.CheckConstraint(
        "state = 'tombstoned' "
        "OR (state = 'ended') = (ending IS NOT NULL AND ended_at IS NOT NULL)",
        name="ck_intention_ended",
    ),
)

EDGE = sa.Table(
    "edge",
    METADATA,
    *_planes(
        "basis IN ('observed', 'computed', 'inferred', 'committed')",
        "state IN ('live', 'superseded', 'retired', 'tombstoned')",
        "edge",
    ),
    sa.Column("type", sa.Text, nullable=False),
    sa.Column("src_id", sa.Text, nullable=False),
    sa.Column("dst_id", sa.Text, nullable=False),
    sa.PrimaryKeyConstraint("id", "unit", name="pk_edge"),
    sa.CheckConstraint(
        "type IN ('cites', 'about', 'draws_on', 'rests_on', 'serves', 'watches',"
        " 'closed_by', 'merged_into')",
        name="ck_edge_type",
    ),
    sa.ForeignKeyConstraint(
        ["src_id", "unit"], ["registry.id", "registry.unit"], name="fk_edge_src"
    ),
    sa.ForeignKeyConstraint(
        ["dst_id", "unit"], ["registry.id", "registry.unit"], name="fk_edge_dst"
    ),
)
sa.Index("idx_edge_src", EDGE.c.src_id, EDGE.c.type)
sa.Index("idx_edge_dst", EDGE.c.dst_id, EDGE.c.type)


GRAPH_SEARCH = sa.Table(
    "graph_search",
    METADATA,
    sa.Column("graph_id", sa.Text, nullable=False),
    sa.Column("unit", sa.Text, nullable=False),
    sa.Column("text", sa.Text, nullable=False),
    sa.Column("embedding", HALFVEC(EMBEDDING_DIMENSIONS)),
    sa.Column("embedding_model", sa.Text),
    sa.Column("embedded_at", sa.BigInteger),
    sa.Column("embed_attempts", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Column("embed_error", sa.Text),
    sa.Column("embed_input", sa.Text),
    sa.PrimaryKeyConstraint("graph_id", "unit", name="pk_graph_search"),
    sa.ForeignKeyConstraint(
        ["graph_id", "unit"], ["registry.id", "registry.unit"], name="fk_graph_search_registry",
    ),
    sa.CheckConstraint("embed_input = 'text'", name="ck_graph_search_embed_input"),
)
sa.Index(
    "idx_graph_search_text_trgm", GRAPH_SEARCH.c.text,
    postgresql_using="gin", postgresql_ops={"text": "gin_trgm_ops"},
)
sa.Index(
    "idx_graph_search_embedding_256",
    sa.cast(
        sa.func.subvector(GRAPH_SEARCH.c.embedding, 1, EMBEDDING_INDEXED_DIMENSIONS),
        HALFVEC(EMBEDDING_INDEXED_DIMENSIONS),
    ).label("embedding_256"),
    postgresql_using="hnsw",
    postgresql_ops={"embedding_256": "halfvec_cosine_ops"},
)


WATCH_RESULT = sa.Table(
    "watch_result", METADATA,
    sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
    sa.Column("watch_edge", sa.Text, nullable=False),
    sa.Column("content_hash", sa.Text, nullable=False),
)

FORGET_REQUEST = sa.Table(
    "forget_request", METADATA,
    sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
    sa.Column("author", sa.Text, nullable=False),
    sa.Column("targets", pg.ARRAY(sa.Text), nullable=False),
    sa.Column("delete_now", sa.Boolean, nullable=False),
    sa.Column("blocked", pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
)

DISPUTE = sa.Table(
    "dispute", METADATA,
    sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
    sa.Column("author", sa.Text, nullable=False),
    sa.Column("claim_id", sa.Text, nullable=False),
)

CLAIM_SUPPORT = sa.Table(
    "claim_support", METADATA,
    sa.Column("claim_id", sa.Text, nullable=False),
    sa.Column("support_count", sa.Integer, nullable=False),
    sa.Column("last_evidenced_at", sa.DateTime(timezone=True)),
    sa.CheckConstraint(
        "support_count >= 0 AND ((support_count = 0) = (last_evidenced_at IS NULL))",
        name="ck_claim_support_tally",
    ),
)


NORA_ACT = sa.Table(
    "nora_act", METADATA,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("executor", sa.Text, nullable=False),
    sa.Column("trigger_id", sa.Text, nullable=False),
    sa.Column("ordinal", sa.Integer, nullable=False),
    sa.Column("act_kind", sa.Text, nullable=False),
    sa.Column("destination", sa.Text, nullable=False),
    sa.Column("authority_person", sa.Text),
    sa.Column("authority_intention", sa.Text),
    sa.Column("guarantee", sa.Text, nullable=False),
    sa.Column("idempotency_key", sa.Text),
    sa.CheckConstraint("ordinal >= 0", name="ck_nora_act_ordinal"),
    sa.CheckConstraint(
        "act_kind IN ('delivery', 'effect', 'delegation')", name="ck_nora_act_act_kind",
    ),
    sa.CheckConstraint(
        "guarantee IN ('idempotent_effect', 'read_back', 'unknown')",
        name="ck_nora_act_guarantee",
    ),
    sa.CheckConstraint(
        "(guarantee = 'idempotent_effect') = (idempotency_key IS NOT NULL)",
        name="ck_nora_act_key",
    ),
)

NORA_ACT_OUTCOME = sa.Table(
    "nora_act_outcome", METADATA,
    sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
    sa.Column("act_id", sa.Text, sa.ForeignKey("nora_act.id", name="fk_nora_act_outcome_id"),
              nullable=False),
    sa.Column("verdict", sa.Text, nullable=False),
    sa.Column("result", pg.JSONB(none_as_null=True)),
    sa.CheckConstraint(
        "verdict IN ('done', 'failed', 'uncertain')", name="ck_nora_act_outcome_verdict",
    ),
)

NORA_PASS_OUTPUT = sa.Table(
    "nora_pass_output", METADATA,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("output_kind", sa.Text, nullable=False),
    sa.Column("silence_reason", sa.Text),
    sa.Column("recheck_reason", sa.Text),
    sa.Column("authority_intention", sa.Text),
    sa.Column("content", pg.JSONB(none_as_null=True)),
    sa.CheckConstraint(
        "output_kind IN ('expression', 'silence', 'tool_call')", name="ck_nora_pass_output_kind",
    ),
    sa.CheckConstraint(
        "(output_kind <> 'silence') = (content IS NOT NULL)", name="ck_nora_pass_output_content",
    ),
)

DEBRIEF_OBLIGATION = sa.Table(
    "debrief_obligation", METADATA,
    sa.Column("unit", sa.Text, primary_key=True),
    sa.Column("pass_id", sa.Text, primary_key=True),
    sa.Column("room", sa.Text, nullable=False),
    sa.Column("occasion", sa.Text, nullable=False),
    sa.Column("intention", sa.Text),
    sa.Column("state", sa.Text, nullable=False, server_default="pending"),
    sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
    sa.Column("created", sa.DateTime(timezone=True), nullable=False),
    sa.Column("settled", sa.DateTime(timezone=True)),
    sa.Column("first_message_id", sa.BigInteger),
    sa.Column("last_message_id", sa.BigInteger),
    sa.Column("proposal_ids", pg.JSONB(none_as_null=True)),
    sa.CheckConstraint("state IN ('pending', 'settled', 'parked')",
                       name="ck_debrief_obligation_state"),
    sa.CheckConstraint("(state = 'settled') = (settled IS NOT NULL)",
                       name="ck_debrief_obligation_settled"),
    sa.CheckConstraint("attempts >= 0", name="ck_debrief_obligation_attempts"),
    sa.CheckConstraint("(first_message_id IS NULL) = (last_message_id IS NULL)",
                       name="ck_debrief_obligation_window"),
)
sa.Index("idx_debrief_obligation_room", DEBRIEF_OBLIGATION.c.unit,
         DEBRIEF_OBLIGATION.c.room, DEBRIEF_OBLIGATION.c.created)


_ITEM_WRITERS = {
    "messages": ("SqlRecordStore", "external"),
    "message_edit": ("SqlRecordStore", "external"),
    "message_reaction": ("SqlRecordStore", "external"),
    "message_delete": ("SqlRecordStore", "external"),
    "presence_fixes": ("PresenceStore", "internal"),
    "fetched_pages": ("SqlFetchedPageStore", "external"),
    "social_item": ("SqlSocialStreamStore", "external"),
    "social_item_count": ("SqlSocialStreamStore", "external"),
    "social_item_state": ("SqlSocialStreamStore", "external"),
    "social_poll": ("SqlSocialStreamStore", "external"),
    "platform_observation": ("SqlObservationStore", "external"),
    "source_health": ("SqlSourceHealthLog", "internal"),
    "watch_result": ("SqlWatchResults", "internal"),
    "nora_act": ("SqlActStore", "internal"),
    "nora_act_outcome": ("SqlActStore", "internal"),
    "nora_pass_output": ("SqlActStore", "internal"),
    "computer_events": ("SqlComputerEventStore", "internal"),
    "tool_call_log": ("SqlToolCallLogStore", "internal"),
    "forget_request": ("SqlMemoryIntake", "internal"),
    "dispute": ("SqlMemoryIntake", "internal"),
}

_AGGREGATE_WRITERS = {
    "presence_stays": ("SqlMobilityStore", "internal"),
    "presence_trips": ("SqlMobilityStore", "internal"),
    "presence_derived_places": ("SqlMobilityStore", "internal"),
    "presence_occupancy": ("SqlMobilityStore", "internal"),
    "presence_place_rhythm": ("SqlMobilityStore", "internal"),
    "presence_corridors": ("SqlMobilityStore", "internal"),
    "rollup_colocation": ("SqlRollupStore", "internal"),
    "rollup_outing": ("SqlRollupStore", "internal"),
    "rollup_response": ("SqlRollupStore", "external"),
    "rollup_tie": ("SqlRollupStore", "internal"),
    "rollup_daybook": ("SqlRollupStore", "internal"),
    "presence_places": ("PresenceStore", "internal"),
    "platform_object_state": ("SqlObservationStore", "external"),
    "claim_support": ("SqlClaimSupport", "internal"),
    "intention_verdict": ("SqlIntentionVerification", "internal"),
    "lesson_tally": ("SqlLessonTally", "internal"),
}

_SCOPED_ONLY = frozenset({
    "forget_request", "dispute", "claim_support", "intention_verdict", "lesson_tally",
})


def _observed_tables() -> Iterator[tuple[str, str, str, str, str]]:
    for kind, basis, writers in (
        ("item", "observed", _ITEM_WRITERS),
        ("aggregate", "computed", _AGGREGATE_WRITERS),
    ):
        for name, (writer, exposure) in writers.items():
            yield name, kind, basis, writer, exposure


def _registration_planes(
    table: str, kind: str, basis: str, writer: str, exposure: str,
) -> Iterator[sa.schema.SchemaItem]:
    for field in _planes(
        f"basis = '{basis}'", "state IN ('live', 'tombstoned')", kind,
        table=table, row_id="graph_id",
    ):
        if isinstance(field, sa.Column):
            if field.name == "unit" and table.startswith("rollup_"):
                continue
            if field.name == "persons" and table in {"rollup_colocation", "rollup_outing"}:
                continue
            if field.name in {"unit", "graph_id"} and table not in _SCOPED_ONLY:
                field.nullable = True
            defaults = {
                "kind": kind, "basis": basis, "writer": writer,
                "exposure": exposure, "state": "live",
            }
            if field.name in defaults:
                field.server_default = sa.DefaultClause(defaults[field.name])
        yield field
    yield sa.CheckConstraint(
        "(unit IS NULL) = (graph_id IS NULL)",
        name=f"ck_{table}_graph_pair",
        postgresql_not_valid=table not in _SCOPED_ONLY | {
            "nora_act", "nora_act_outcome", "nora_pass_output", "watch_result",
        },
    )


def _attach_registration_planes() -> None:
    for name, kind, basis, writer, exposure in _observed_tables():
        table = METADATA.tables[name]
        for field in _registration_planes(name, kind, basis, writer, exposure):
            if isinstance(field, sa.Column):
                table.append_column(field)
            elif isinstance(field, sa.Constraint):
                table.append_constraint(field)


sa.Index("idx_forget_request_targets", FORGET_REQUEST.c.targets, postgresql_using="gin")


for _log in ("computer_events", "tool_call_log"):
    METADATA.tables[_log].append_column(sa.Column("act_id", sa.Text))

_attach_registration_planes()
attach_verdict_constraints(INTENTION_VERDICT)
attach_lesson_tally_constraints(LESSON_TALLY)

for _key in ("unit", "graph_id"):
    WATCH_RESULT.c[_key].nullable = False
DISPUTE.append_constraint(sa.ForeignKeyConstraint(
    ["claim_id", "unit"], ["claim.id", "claim.unit"], name="fk_dispute_claim",
))
CLAIM_SUPPORT.append_constraint(sa.PrimaryKeyConstraint("claim_id", "unit"))
CLAIM_SUPPORT.append_constraint(sa.ForeignKeyConstraint(
    ["claim_id", "unit"], ["claim.id", "claim.unit"], name="fk_claim_support_claim",
))
WATCH_RESULT.append_constraint(sa.ForeignKeyConstraint(
    ["watch_edge", "unit"], ["edge.id", "edge.unit"], name="fk_watch_result_edge",
))
sa.Index(
    "idx_watch_result_latest", WATCH_RESULT.c.unit, WATCH_RESULT.c.watch_edge, WATCH_RESULT.c.id,
)

NORA_ACT.append_constraint(sa.UniqueConstraint("id", "unit", name="uq_nora_act_id_unit"))
sa.Index("idx_nora_act_pass", NORA_ACT.c.unit, NORA_ACT.c["pass"])
sa.Index("uq_nora_act_executor_key", NORA_ACT.c.executor, NORA_ACT.c.idempotency_key, unique=True)
sa.Index("idx_nora_act_outcome_act", NORA_ACT_OUTCOME.c.act_id, NORA_ACT_OUTCOME.c.id)
sa.Index("idx_nora_pass_output_pass", NORA_PASS_OUTPUT.c.unit, NORA_PASS_OUTPUT.c["pass"])
for _name in ("nora_act_outcome", "computer_events", "tool_call_log"):
    _table = METADATA.tables[_name]
    _table.append_constraint(sa.ForeignKeyConstraint(
        ["act_id", "unit"], ["nora_act.id", "nora_act.unit"], name=f"fk_{_name}_act",
    ))

sa.Index(
    "uq_social_item_key", METADATA.tables["social_item"].c.network,
    METADATA.tables["social_item"].c.item_id, METADATA.tables["social_item"].c.unit,
    unique=True, postgresql_nulls_not_distinct=True,
)


METADATA.tables["presence_places"].append_constraint(
    sa.UniqueConstraint(
        "circle_id",
        "place_id",
        "unit",
        name="uq_presence_places_unit",
        postgresql_nulls_not_distinct=True,
    )
)


METADATA.tables["platform_object_state"].append_constraint(
    sa.UniqueConstraint(
        "platform", "account_key", "conversation_id", "thread", "source_kind", "object_ref",
        "unit", name="uq_platform_object_state_object", postgresql_nulls_not_distinct=True,
    )
)


_TOMBSTONE_NULLABLE = {
    "presence_places": ("name", "lat", "lng"),
    "fetched_pages": ("blob_key", "provenance"),
    "platform_object_state": ("source_state", "coverage"),
    "platform_observation": ("payload", "coverage"),
    "presence_corridors": ("sequence", "sample_count"),
    "presence_derived_places": ("lat", "lng", "radius_m", "place_confidence"),
    "presence_fixes": ("lat", "lng"),
    "presence_occupancy": ("dwell_ms",),
    "presence_place_rhythm": ("sample_count",),
    "presence_stays": ("lat", "lng", "radius_m", "fix_count"),
    "presence_trips": ("distance_m", "avg_speed_kmh", "fix_count"),
    "rollup_colocation": ("lat", "lng", "radius_m", "at_home_of", "homes_unknown", "stay_count"),
    "rollup_daybook": (
        "stays",
        "stay_ms",
        "home_ms",
        "colocation_ms",
        "with_persons",
        "outings",
        "rooms",
        "messages",
        "reactions",
    ),
    "rollup_outing": ("lat", "lng"),
    "rollup_response": (
        "replies",
        "reactions",
        "edits",
        "follows",
        "responders",
        "named_persons",
        "outcome",
    ),
    "rollup_tie": (
        "replies_ab",
        "replies_ba",
        "mentions_ab",
        "mentions_ba",
        "reactions_ab",
        "reactions_ba",
        "colocation_ms",
    ),
    "source_health": ("detail",),
}


def _live_content_check(columns: tuple[str, ...]) -> str:
    expr = " AND ".join(f"{column} IS NOT NULL" for column in columns)
    return f"state = 'tombstoned' OR ({expr})"


for _table_name, _columns in _TOMBSTONE_NULLABLE.items():
    _target = METADATA.tables[_table_name]
    for _column in _columns:
        _target.c[_column].nullable = True
    _target.append_constraint(
        sa.CheckConstraint(
            _live_content_check(_columns),
            name=f"ck_{_table_name}_tombstone_content",
        )
    )
