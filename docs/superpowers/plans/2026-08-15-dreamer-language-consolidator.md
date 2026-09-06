> **DEPRECATED 2026-09-07 — the language tier this document designs has been deprecated and its code deleted**, as part of the same decision that deprecated the knowledge tier.
> This document is retained as a record of the reasoning, not as a description of the system. **Nothing here describes code that exists.**
> The language tier never ran in production — `[language] enabled = false` throughout its life. Its tables are **not** dropped by that change: the code goes, the schema stays, and dropping them is a separate explicit decision.
> The evidence that decided it is in `design/dreamer-agent-loop/20-knowledge-tier.md` §0. See issue #115.

# Dreamer, Plan 1 — Room Language Consolidator

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A background daemon distils the words this room has made its own out of the message record, and a capped, evidence-carrying glossary renders into every tag's prefill.

**Architecture:** A `dream_loop` in `nora/app/runtime.py` — a sibling of `prune_loop` — ticks per scope, reads unprocessed human messages from a watermark via a new `RecordStore.since`, sends them to one schema-forced LLM extraction call, appends observations, and rescores a lexicon deterministically. `TranscriptInjection` reads the rendered result through a Protocol-typed `RoomLanguageReader` and `prefill.py` renders it in the **volatile** tier, never the cached static head.

**Tech Stack:** Python 3.13, SQLAlchemy 2 async + asyncpg, Alembic, pydantic, pytest, Strands/OpenAI-compatible client via OmniRoute.

**Spec:** `docs/superpowers/specs/2026-08-15-room-language-dreamer-design.md` (git `425867a`)

## Global Constraints

- **Dependencies via `uv add`, never `pip`.** (operational constraint 5)
- **Gates, all four, before every commit:**
  `uv run pytest -q` · `uv run ruff check nora/ evals/ tests/` · `uv run mypy nora/domain nora/config.py nora/app nora/infra nora/presence` · `uv run lint-imports`
- **Every port method is keyword-only after `self`** (`tests/test_ports.py:80`) **and carries `@runtime_checkable`** (`tests/test_ports.py:62`). Both gates enumerate every Protocol in `nora.domain.ports` automatically.
- **Production classes take collaborators through `__init__` only** — no `*_fn` hooks, no `_for_test` params, no module-level mutable singletons.
- **A port never gains a second implementation, including a test fake, without a contract suite both pass** (`tests/contract/`).
- **Her own output is never evidence:** every read of `messages` for consolidation carries `is_nora == 0`.
- **No claim about a person.** This plan's output is words and counts. Nothing renders a person's name in a characterising role.
- **Migration head is `0012_sticker_cache`** as of 2026-08-15; this plan adds `0013`. Re-check `ls nora/infra/sql/migrations/versions/` before writing it — another session is committing to this checkout.
- **`idx_messages_conv_ts` is excluded from autogenerate on purpose.** Never trust `alembic revision --autogenerate` for index work here; hand-write it.
- **Release fragment required** under `release-fragments/unreleased/` before merge.

---

### Task 1: `TranscriptRow` carries `person_id`

The port that feeds the consolidators had no resolved-identity field, so every `person_id` column downstream was unpopulatable. `sender_id` is a raw platform id; `person_id` is the roster's canonical id and the thing observations must key on.

**Files:**
- Modify: `nora/domain/models.py` (the `TranscriptRow` dataclass)
- Modify: `nora/infra/sql/audit.py` (`SqlRecordStore.recent` select + row construction)
- Modify: `nora/testing/fakes.py` (`FakeRecordStore.recent`)
- Test: `tests/contract/test_audit_store_contract.py`

**Interfaces:**
- Produces: `TranscriptRow.person_id: str | None` — the resolved person, or `None` for an unmapped sender.

- [ ] **Step 1: Write the failing contract test**

Add to `tests/contract/test_audit_store_contract.py`, following the file's existing parametrised-over-both-implementations style:

```python
async def test_recent_carries_the_resolved_person_id(store):
    """`person_id` is written in the same INSERT as the row (identity resolves
    before the audit write), so a transcript row must be able to report it.
    Unmapped senders keep `None` — that is a real state, not a gap."""
    await store.append(
        message=NormalizedMessage(
            "signal", "g1", "group", "+852111", "Miles", 1, "congee", {}),
        person_id="miles",
    )
    await store.append(
        message=NormalizedMessage(
            "signal", "g1", "group", "+852999", None, 2, "who dis", {}),
    )
    rows = await store.recent(conversation_id="g1", limit=10)
    assert [r.person_id for r in rows] == ["miles", None]
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/contract/test_audit_store_contract.py -k person_id -v`
Expected: FAIL — `AttributeError: 'TranscriptRow' object has no attribute 'person_id'`.

- [ ] **Step 3: Add the field**

In `nora/domain/models.py`, append to `TranscriptRow` (defaulted, so every existing positional construction stays valid — the same reason `quoted_ts` is defaulted):

```python
    # The RESOLVED person, or None when the sender mapped to nobody at ingest.
    # `sender_id` above is the raw platform id; this is the roster's canonical
    # id, and it is what anything joining across stores keys on (design/20).
    # Defaulted so existing positional construction stays valid.
    person_id: str | None = None
```

- [ ] **Step 4: Select it in the real store**

In `nora/infra/sql/audit.py::recent`, add `MESSAGES.c.person_id` to the `sa.select(...)` column list and `person_id=r.person_id` to the `TranscriptRow(...)` construction.

- [ ] **Step 5: Return it from the fake**

In `nora/testing/fakes.py::FakeRecordStore.recent`, add `person_id=r["person_id"]` to the `TranscriptRow(...)` construction. The fake's row dict already stores `person_id`.

- [ ] **Step 6: Run the contract suite and the gates**

Run: `uv run pytest tests/contract/test_audit_store_contract.py -q` → PASS
Run: `uv run pytest -q && uv run ruff check nora/ evals/ tests/ && uv run mypy nora/domain nora/config.py nora/app nora/infra nora/presence && uv run lint-imports`

- [ ] **Step 7: Commit**

```bash
git add nora/domain/models.py nora/infra/sql/audit.py nora/testing/fakes.py tests/contract/test_audit_store_contract.py
git commit -m "feat(record): TranscriptRow carries the resolved person_id"
```

---

### Task 2: `RecordStore.since` — the watermark cursor

`recent` is `ORDER BY ts DESC, id DESC LIMIT n` — newest-N reversed. A consolidator needs ascending `id > watermark`, unbounded by recency. Borrowing `recent` would mean hand-duplicating row mapping outside the contract suite.

**Files:**
- Modify: `nora/domain/ports.py` (`RecordStore`)
- Modify: `nora/infra/sql/audit.py` (`SqlRecordStore`)
- Modify: `nora/testing/fakes.py` (`FakeRecordStore`)
- Test: `tests/contract/test_audit_store_contract.py`

**Interfaces:**
- Produces: `RecordStore.since(*, conversation_id: str, after_id: int, limit: int) -> list[TranscriptRow]` — ascending by `id`, **human messages only**, at most `limit`.

- [ ] **Step 1: Write the failing contract test**

```python
async def test_since_returns_human_rows_ascending_after_a_cursor(store):
    """The consolidation cursor. Ascending by id (NOT recent's newest-first),
    and `is_nora` rows are excluded IN THE QUERY — her own words are never
    evidence, and a caller that had to remember to filter would eventually
    forget."""
    ids = []
    for i, (body, is_nora) in enumerate(
        [("a", False), ("b", True), ("c", False), ("d", False)], start=1
    ):
        ids.append(await store.append(
            message=NormalizedMessage(
                "signal", "g1", "group", "+852111", "Miles", i, body, {}),
            is_nora=is_nora,
        ))

    rows = await store.since(conversation_id="g1", after_id=ids[0], limit=10)
    assert [r.body for r in rows] == ["c", "d"], "ascending, human-only, after the cursor"

    assert await store.since(conversation_id="g1", after_id=0, limit=0) == []
    assert await store.since(conversation_id="g1", after_id=ids[-1], limit=10) == []
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/contract/test_audit_store_contract.py -k since -v`
Expected: FAIL — `AttributeError: ... has no attribute 'since'`.

- [ ] **Step 3: Declare the port method**

In `nora/domain/ports.py`, inside `RecordStore` (keyword-only, and the class already carries `@runtime_checkable`):

```python
    async def since(
        self, *, conversation_id: str, after_id: int, limit: int
    ) -> list[TranscriptRow]:
        """Human rows with `id > after_id`, ASCENDING, at most `limit`.

        A cursor read, not a window read — the opposite ordering to `recent`,
        which is newest-first for the prompt. `is_nora` rows are excluded here
        rather than by the caller: this is the only read built for derivation,
        and the exclusion is the property the whole Dreamer rests on.
        """
        ...
```

- [ ] **Step 4: Implement it on the SQL store**

In `nora/infra/sql/audit.py`, mirroring `recent`'s column list and row construction (`limit <= 0` short-circuits for the same `LIMIT -1` reason documented on `recent`):

```python
    async def since(
        self, *, conversation_id: str, after_id: int, limit: int
    ) -> list[TranscriptRow]:
        if limit <= 0:
            return []
        stmt = (
            sa.select(
                MESSAGES.c.id, MESSAGES.c.sender_id, MESSAGES.c.sender_name,
                MESSAGES.c.ts, MESSAGES.c.body, MESSAGES.c.is_nora,
                MESSAGES.c.delivery_status, MESSAGES.c.raw_json,
                MESSAGES.c.quoted_ts, MESSAGES.c.media_json, MESSAGES.c.person_id,
            )
            .where(
                MESSAGES.c.conversation_id == conversation_id,
                MESSAGES.c.id > after_id,
                MESSAGES.c.is_nora == 0,
            )
            .order_by(MESSAGES.c.id.asc())
            .limit(limit)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [self._row(r) for r in rows]
```

Extract the shared `TranscriptRow(...)` construction from `recent` into a `_row` static method and call it from both, so the mapping exists once.

- [ ] **Step 5: Implement it on the fake**

In `nora/testing/fakes.py::FakeRecordStore`, behaving the same way — including the `is_nora` exclusion and the `limit <= 0` short-circuit. A fake that returned everything would make the contract test decorative:

```python
    async def since(self, *, conversation_id: str, after_id: int, limit: int):
        if limit <= 0:
            return []
        rows = [
            r for r in self.rows
            if r["conversation_id"] == conversation_id
            and r["id"] > after_id
            and not r["is_nora"]
        ]
        rows.sort(key=lambda r: r["id"])
        return [self._transcript_row(r) for r in rows[:limit]]
```

- [ ] **Step 6: Run the suite and the gates**

Run: `uv run pytest tests/contract/test_audit_store_contract.py tests/test_ports.py -q` → PASS
Then all four gates.

- [ ] **Step 7: Commit**

```bash
git add nora/domain/ports.py nora/infra/sql/audit.py nora/testing/fakes.py tests/contract/test_audit_store_contract.py
git commit -m "feat(record): RecordStore.since — ascending human rows after a cursor"
```

---

### Task 3: Schema and migration 0013

Three tables plus the index that makes the lag query survivable. Without the index this reproduces the exact defect the spec diagnoses in `prune_all`: a fixed-cadence unindexed scan over a table that now grows without bound.

**Files:**
- Modify: `nora/infra/sql/schema.py`
- Create: `nora/infra/sql/migrations/versions/0013_room_language.py`
- Modify: `tests/infra/pg_schema_baseline.sql` (regenerated, not hand-edited)
- Test: `tests/infra/test_schema_parity.py` (runs as-is)

**Interfaces:**
- Produces: `CONSOLIDATION_WATERMARK`, `LANGUAGE_OBSERVATION`, `ROOM_LEXEME` table objects in `nora/infra/sql/schema.py`.

- [ ] **Step 1: Confirm the migration head has not moved**

Run: `ls nora/infra/sql/migrations/versions/`
Expected: `0012_sticker_cache.py` is the highest. If a `0013` already exists, renumber this one and set `down_revision` to the real head. Another session commits to this checkout.

- [ ] **Step 2: Declare the tables in `schema.py`**

Append to `nora/infra/sql/schema.py`. Note the type split the module docstring already establishes — epoch-ms `BigInteger` for values arriving from a platform wire, `DateTime(timezone=True)` for Nora's own instants compared against `now` in SQL:

```python
# The Dreamer's cursor, one row per (consolidator, scope). GENERIC on purpose:
# "how far has this consumer read" is identical for any future consolidator, and
# this is the one thing the spec abstracts across them.
#
# `last_message_id` is `sa.Integer`, matching `messages.id` — which is INT4
# because it COUNTS UP (see this module's docstring). `updated_at` is
# `DateTime(timezone=True)`, NOT epoch-ms: the dream cycle compares it against
# `now` in SQL (`age(watermark) > 24h`), the same case `reminders.due_at` is.
CONSOLIDATION_WATERMARK = sa.Table(
    "consolidation_watermark",
    METADATA,
    sa.Column("consolidator_name", sa.Text, primary_key=True),
    sa.Column("scope_id", sa.Text, primary_key=True),
    sa.Column("last_message_id", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
              server_default=sa.text("now()")),
)

# Append-only EVIDENCE. This table, not `messages`, is what the lexicon is
# answerable to — and it is why `message_id` is a SOFT reference with no foreign
# key: evidence must outlive the message it came from. `store.prune_enabled` is
# false today, but the flag exists, and an FK would make this table inherit the
# record's retention policy — the one property it must not have.
#
# The UNIQUE key is natural, not synthetic, and it is load-bearing: without it a
# retried or concurrent cycle appends the same observation twice and the rendered
# «12x, 3 people» becomes a fabricated number.
LANGUAGE_OBSERVATION = sa.Table(
    "language_observation",
    METADATA,
    sa.Column("id", sa.Integer, sa.Identity(always=False), primary_key=True),
    sa.Column("scope_id", sa.Text, nullable=False),
    sa.Column("conversation_id", sa.Text, nullable=False),
    sa.Column("message_id", sa.Integer, nullable=False),
    sa.Column("person_id", sa.Text),
    sa.Column("surface_form", sa.Text, nullable=False),
    sa.Column("canonical_form", sa.Text, nullable=False),
    sa.Column("sense", sa.Text, nullable=False),
    sa.Column("part_of_speech", sa.Text),
    sa.Column("context", sa.Text, nullable=False),
    # Epoch MILLISECONDS — the MESSAGE's own ts, arriving from a platform wire.
    sa.Column("observed_at", sa.BigInteger, nullable=False),
    sa.Column("extractor_confidence", sa.Float, nullable=False),
    sa.UniqueConstraint("message_id", "canonical_form", "sense"),
)

# The consolidated lexicon. `sense` is IN the primary key: a word acquiring a
# second meaning in one room is ordinary, and keying on `(scope_id,
# canonical_form)` alone would either overwrite the first sense or merge counts
# from two unrelated meanings — then render a gloss no observation supports.
#
# `state` is CHECK-constrained like every other enumerated TEXT column here.
# `alembic check` is blind to CHECK constraints; the parity test's full-dump
# diff is what catches a drift.
ROOM_LEXEME = sa.Table(
    "room_lexeme",
    METADATA,
    sa.Column("scope_id", sa.Text, primary_key=True),
    sa.Column("canonical_form", sa.Text, primary_key=True),
    sa.Column("sense", sa.Text, primary_key=True),
    sa.Column("first_seen_at", sa.BigInteger, nullable=False),
    sa.Column("last_seen_at", sa.BigInteger, nullable=False),
    sa.Column("occurrence_count", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Column("distinct_speakers", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Column("recognition_score", sa.Float, nullable=False, server_default=sa.text("0")),
    sa.Column("state", sa.Text, nullable=False, server_default=sa.text("'live'")),
    # JSONB, following `tool_call_log`'s Postgres-native precedent rather than
    # `media_json`'s SQLite-era TEXT. Each example carries its OWN message_id and
    # person_id: a bare array of strings has no query path to redact one person's
    # quotes, and nothing in this system deletes anything any more.
    sa.Column("examples", pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    sa.CheckConstraint("state IN ('live','fading','stale')"),
)

# Serves the dream cycle's lag query EXACTLY:
#   count(*) where conversation_id = :c and id > :watermark and is_nora = 0
# `idx_messages_conv_ts` is (conversation_id, ts DESC) and carries neither `id`
# nor `is_nora`; `idx_messages_is_nora` is a majority-class predicate the planner
# will not choose. `is_nora = 0` is the right predicate for an INTEGER column —
# the same one `audit.py:210` already uses.
IDX_MESSAGES_DREAM_LAG = sa.Index(
    "idx_messages_dream_lag",
    MESSAGES.c.conversation_id,
    MESSAGES.c.id,
    postgresql_where=MESSAGES.c.is_nora == 0,
)
```

- [ ] **Step 3: Hand-write migration 0013**

Create `nora/infra/sql/migrations/versions/0013_room_language.py`. **`CREATE INDEX CONCURRENTLY` cannot run inside a transaction, and `migrations/env.py` wraps every migration in one** — hence `autocommit_block()`. This is the repo's **first** use of it, so verify by running the migration against a real database rather than trusting it:

```python
"""room language: watermark, observations, lexicon.

`idx_messages_dream_lag` is built CONCURRENTLY inside an `autocommit_block()`,
and both halves of that are required rather than cautious. `messages` is written
by every inbound turn and — since `store.prune_enabled = false` — grows without
bound, so a plain CREATE INDEX takes an ACCESS EXCLUSIVE lock and stalls ingest
for the length of the build. CONCURRENTLY cannot run inside a transaction, and
`migrations/env.py` runs migrations inside one (`context.begin_transaction()`),
so the autocommit block is what makes it legal. This is the first use of
`autocommit_block()` in this repo — there is no precedent to copy, only Alembic's
own contract.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0013_room_language"
down_revision = "0012_sticker_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "consolidation_watermark",
        sa.Column("consolidator_name", sa.Text, primary_key=True),
        sa.Column("scope_id", sa.Text, primary_key=True),
        sa.Column("last_message_id", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_table(
        "language_observation",
        sa.Column("id", sa.Integer, sa.Identity(always=False), primary_key=True),
        sa.Column("scope_id", sa.Text, nullable=False),
        sa.Column("conversation_id", sa.Text, nullable=False),
        sa.Column("message_id", sa.Integer, nullable=False),
        sa.Column("person_id", sa.Text),
        sa.Column("surface_form", sa.Text, nullable=False),
        sa.Column("canonical_form", sa.Text, nullable=False),
        sa.Column("sense", sa.Text, nullable=False),
        sa.Column("part_of_speech", sa.Text),
        sa.Column("context", sa.Text, nullable=False),
        sa.Column("observed_at", sa.BigInteger, nullable=False),
        sa.Column("extractor_confidence", sa.Float, nullable=False),
        sa.UniqueConstraint("message_id", "canonical_form", "sense"),
    )
    op.create_table(
        "room_lexeme",
        sa.Column("scope_id", sa.Text, primary_key=True),
        sa.Column("canonical_form", sa.Text, primary_key=True),
        sa.Column("sense", sa.Text, primary_key=True),
        sa.Column("first_seen_at", sa.BigInteger, nullable=False),
        sa.Column("last_seen_at", sa.BigInteger, nullable=False),
        sa.Column("occurrence_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("distinct_speakers", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("recognition_score", sa.Float, nullable=False, server_default=sa.text("0")),
        sa.Column("state", sa.Text, nullable=False, server_default=sa.text("'live'")),
        sa.Column("examples", pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.CheckConstraint("state IN ('live','fading','stale')"),
    )
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_messages_dream_lag "
            "ON messages (conversation_id, id) WHERE is_nora = 0"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_messages_dream_lag")
    op.drop_table("room_lexeme")
    op.drop_table("language_observation")
    op.drop_table("consolidation_watermark")
```

- [ ] **Step 4: Regenerate the baseline**

Run: `uv run python scripts/gen_pg_baseline.py`
This rewrites `tests/infra/pg_schema_baseline.sql`. Never hand-edit it. Read the diff before staging — it should show exactly the three tables and the one index, nothing else.

- [ ] **Step 5: Verify the schema gate**

Run: `uv run pytest tests/infra/test_schema_parity.py -q`
Expected: PASS. This is the real gate — it compares `schema.py`'s `MetaData` against the migrated database and diffs the normalised dump, and it sees CHECK constraints and index ordering that `alembic check` cannot.

If `autocommit_block()` misbehaves, this is where it surfaces.

**Verify the index against the throwaway test database the suite spins up — NEVER against `nora-db-1`.** `nora-db-1` is the live deployment: running a migration there puts the database ahead of the running image, and `nora/app/composition.py:292` turns a migration error into a `StartupError`, so Nora refuses to start on her next restart. An earlier draft of this step named `nora-db-1` and that is exactly what happened (2026-08-16).

The parity test already migrates a testcontainers database; add a temporary assertion inside `tests/infra/test_schema_parity.py` if you need to see the index definition, or inspect it through the same fixture:

```python
rows = conn.exec_driver_sql(
    "select indexdef from pg_indexes where indexname = 'idx_messages_dream_lag'"
).all()
assert "WHERE (is_nora = 0)" in rows[0][0]
```
Expected: the definition contains `WHERE (is_nora = 0)`, and `pg_index.indisvalid` is true.

- [ ] **Step 6: Run all four gates, then commit**

```bash
git add nora/infra/sql/schema.py nora/infra/sql/migrations/versions/0013_room_language.py tests/infra/pg_schema_baseline.sql
git commit -m "feat(sql): room-language tables and the dream-lag partial index"
```

---

### Task 4: Domain models and the `RoomConsolidator` port

**Files:**
- Modify: `nora/domain/models.py`
- Modify: `nora/domain/ports.py`
- Create: `tests/contract/test_room_consolidator_contract.py`

**Interfaces:**
- Produces: `RoomLexeme`, `RoomLanguage` dataclasses; `RoomConsolidator` and `RoomLanguageReader` Protocols.

- [ ] **Step 1: Add the domain types**

In `nora/domain/models.py` — these live here and **not** in `nora/language/`, and it is forced rather than preferred: `TagContext` is declared in this module and `nora.domain` is the floor of the layers contract, so nothing above it may be imported by it.

```python
@dataclass(frozen=True)
class LexemeExample:
    """One verbatim usage, kept WITH its attribution. The gloss above it is
    model-authored; this is not. Rendering one of these under every entry is
    what makes a wrong `sense` visible at the point of use instead of
    authoritative."""

    message_id: int
    person_id: str | None
    text: str


@dataclass(frozen=True)
class RoomLexeme:
    canonical_form: str
    sense: str
    occurrence_count: int
    distinct_speakers: int
    first_seen_ms: int
    last_seen_ms: int
    state: str          # 'live' | 'fading' | 'stale'
    examples: tuple[LexemeExample, ...] = ()


@dataclass(frozen=True)
class RoomLanguage:
    """What the Dreamer knows about one scope, as of `generated_ms`.

    Empty is a REAL state and the correct one for a room with no evidence yet —
    the renderer emits no block at all rather than an empty one.
    """

    scope_id: str
    lexemes: tuple[RoomLexeme, ...] = ()
    generated_ms: int = 0

    def __bool__(self) -> bool:
        return bool(self.lexemes)
```

- [ ] **Step 2: Add the ports**

In `nora/domain/ports.py`. **Both decorators are mandatory** — `tests/test_ports.py:62` asserts `@runtime_checkable` on every Protocol in this module and `:80` asserts keyword-only params, and both enumerate automatically:

```python
@runtime_checkable
class RoomConsolidator(Protocol):
    """One derivation pass over a batch of human messages for ONE scope.

    `scope_id` is a parameter and not an attribute because one consolidator
    instance serves every scope the deployment has — the loop ticks each in
    turn, and an instance-per-scope would make the object graph depend on
    policy.toml.
    """

    name: str

    async def consolidate(
        self, *, scope_id: str, batch: Sequence[TranscriptRow]
    ) -> int:
        """Returns OBSERVATIONS APPENDED — fixed here so a second
        implementation cannot mean something else by it. The contract suite
        asserts it: a batch yielding nothing returns 0, and a re-run over the
        same batch also returns 0 because the natural key makes it idempotent.
        """
        ...


@runtime_checkable
class RoomLanguageReader(Protocol):
    """The READ half, separated so `TranscriptInjection` keeps taking only
    Protocol-typed collaborators. The write side stays unabstracted — a lexicon
    and a future fact table differ in shape and lifetime — but this one call
    site is on the turn's critical path and must be fakeable."""

    async def room_language(self, *, scope_id: str, limit: int) -> RoomLanguage:
        """Top `limit` rendered-state lexemes by score. Never raises: an
        unavailable store yields an EMPTY `RoomLanguage`, because this block is
        background she may draw on and never the ground for a claim."""
        ...
```

- [ ] **Step 3: Write the contract suite**

Create `tests/contract/test_room_consolidator_contract.py`. Two implementations will exist (the real one and a fake), so the suite is required on day one rather than by anticipation:

```python
"""Shared contract for `RoomConsolidator`. Both implementations run every test.

The idempotency test is the one that matters: `consolidate` is called from a
loop that can legitimately re-fire over an overlapping batch, and the ONLY thing
standing between that and a fabricated «12x, 3 people» is the natural-key
constraint. A fake that simply appended would make this suite decorative.
"""

import pytest


@pytest.fixture(params=["sql", "fake"])
def consolidator(request, ...):
    ...


async def test_an_empty_batch_appends_nothing(consolidator):
    assert await consolidator.consolidate(scope_id="g1", batch=[]) == 0


async def test_the_same_batch_twice_appends_once(consolidator, batch_with_one_lexeme):
    first = await consolidator.consolidate(scope_id="g1", batch=batch_with_one_lexeme)
    assert first > 0
    second = await consolidator.consolidate(scope_id="g1", batch=batch_with_one_lexeme)
    assert second == 0, "the natural key must make a re-run a no-op"


async def test_scope_is_honoured(consolidator, store, batch_with_one_lexeme):
    """Evidence from one room must never surface in another. This is the whole
    reason `scope_id` is a parameter rather than instance state."""
    await consolidator.consolidate(scope_id="g1", batch=batch_with_one_lexeme)
    assert not await store.room_language(scope_id="g2", limit=10)
```

- [ ] **Step 4: Run the port gates**

Run: `uv run pytest tests/test_ports.py -q`
Expected: PASS. If `test_ports_are_runtime_checkable_protocols` fails, the decorator is missing; if `test_port_methods_are_keyword_only_after_self` fails, a `*` is missing.

- [ ] **Step 5: Run all four gates, then commit**

```bash
git add nora/domain/models.py nora/domain/ports.py tests/contract/test_room_consolidator_contract.py
git commit -m "feat(domain): RoomLexeme/RoomLanguage and the RoomConsolidator port"
```

---

### Task 5: `nora/language/scoring.py` — deterministic, and the layers entry

The LLM extracts; **policy is arithmetic**. Nothing here calls a model, which is what makes the lexicon reproducible from its own evidence table.

**Files:**
- Create: `nora/language/__init__.py`
- Create: `nora/language/scoring.py`
- Modify: `pyproject.toml` (the `layers` contract)
- Test: `tests/unit/test_language_scoring.py`

**Interfaces:**
- Produces: `score(occurrences, distinct_speakers, last_seen_ms, now_ms) -> float`, `state_for(score, last_seen_ms, now_ms) -> str`.

- [ ] **Step 1: Add `nora.language` to the layers contract**

In `pyproject.toml`, insert `"nora.language",` between `"nora.doctrine",` and `"nora.skills",`.

**This is not optional and the gate cannot catch it.** Import-linter's `layers` contract only analyses packages it *names* — an unlisted package is invisible in both directions, so `lint-imports` reports clean on any placement, right or wrong. The position is forced from both sides: `nora.engagement` (higher) must import `nora.language.render`, and `nora.language` must import `nora.infra` (lower).

- [ ] **Step 2: Write the failing test**

```python
def test_a_word_many_people_use_outscores_a_word_one_person_repeats():
    """Speaker SPREAD, not raw frequency, is what makes a word the room's.
    One person saying `firm` forty times is a personal tic; four people saying
    it eight times each is a lexeme."""
    tic = scoring.score(occurrences=40, distinct_speakers=1,
                        last_seen_ms=NOW, now_ms=NOW)
    shared = scoring.score(occurrences=32, distinct_speakers=4,
                           last_seen_ms=NOW, now_ms=NOW)
    assert shared > tic


def test_recency_decays_a_word_nobody_says_any_more():
    fresh = scoring.score(occurrences=10, distinct_speakers=3,
                          last_seen_ms=NOW, now_ms=NOW)
    old = scoring.score(occurrences=10, distinct_speakers=3,
                        last_seen_ms=NOW - 90 * DAY_MS, now_ms=NOW)
    assert old < fresh


def test_state_falls_through_live_then_fading_then_stale():
    assert scoring.state_for(score=0.9, last_seen_ms=NOW, now_ms=NOW) == "live"
    assert scoring.state_for(score=0.9, last_seen_ms=NOW - 30 * DAY_MS,
                             now_ms=NOW) == "fading"
    assert scoring.state_for(score=0.9, last_seen_ms=NOW - 200 * DAY_MS,
                             now_ms=NOW) == "stale"
```

- [ ] **Step 3: Run it and watch it fail**

Run: `uv run pytest tests/unit/test_language_scoring.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'nora.language'`.

- [ ] **Step 4: Implement**

```python
"""Deterministic scoring for the room lexicon. NO model call lives here.

The split is the whole design: an LLM decides what a candidate MEANS, arithmetic
decides whether the room has adopted it. Asking a model «should Nora know this
word yet» would hand a policy decision to a stochastic process and make the
lexicon unreproducible from its own evidence.

Speaker SPREAD is weighted above raw frequency deliberately. One person saying a
word forty times is a personal tic; four people saying it eight times each is the
room's vocabulary, and the second is the thing worth telling her about.
"""

from __future__ import annotations

import math

DAY_MS = 86_400_000
HALF_LIFE_DAYS = 45.0
FADING_AFTER_DAYS = 21.0
STALE_AFTER_DAYS = 120.0

W_FREQUENCY = 0.4
W_SPREAD = 1.0
W_RECENCY = 0.6


def score(*, occurrences: int, distinct_speakers: int,
          last_seen_ms: int, now_ms: int) -> float:
    """Frequency is logged so a single loud thread cannot dominate; spread is
    linear-ish and weighted hardest; recency is an exponential decay."""
    frequency = math.log1p(max(occurrences, 0))
    spread = math.log1p(max(distinct_speakers, 0)) * 2.0
    age_days = max(now_ms - last_seen_ms, 0) / DAY_MS
    recency = 0.5 ** (age_days / HALF_LIFE_DAYS)
    return W_FREQUENCY * frequency + W_SPREAD * spread + W_RECENCY * recency


def state_for(*, score: float, last_seen_ms: int, now_ms: int) -> str:
    """Rendering state, NOT a permission to use the word — there is no
    per-word generation gate in V1 because there is no per-word generation
    decision. `stale` entries stop rendering entirely."""
    age_days = max(now_ms - last_seen_ms, 0) / DAY_MS
    if age_days >= STALE_AFTER_DAYS:
        return "stale"
    if age_days >= FADING_AFTER_DAYS:
        return "fading"
    return "live"
```

- [ ] **Step 5: Run tests and gates**

Run: `uv run pytest tests/unit/test_language_scoring.py -q` → PASS
Run: `uv run lint-imports` → `Contracts: 5 kept, 0 broken.` The count does **not** change: `nora.language` joins the existing `layers` contract's list, it does not add a contract. What changes is that the contract can now see the package at all — before the edit it was invisible in both directions and would have reported clean on any placement.
Then the remaining gates.

- [ ] **Step 6: Commit**

```bash
git add nora/language/ tests/unit/test_language_scoring.py pyproject.toml
git commit -m "feat(language): deterministic lexeme scoring, and the layers entry"
```

---

### Task 6: `nora/language/observer.py` — the one LLM call

**Files:**
- Create: `nora/language/observer.py`
- Test: `tests/unit/test_language_observer.py`

**Interfaces:**
- Consumes: `TranscriptRow` (Task 1).
- Produces: `LexemeCandidate` dataclass; `LexemeObserver(model)` with `async def observe(self, *, batch) -> list[LexemeCandidate]`.

- [ ] **Step 1: Write the failing tests**

The fake model must behave like the real one **including its wrong-call shapes** — a fake that always returns one clean candidate would keep this test green through any regression:

```python
async def test_a_batch_of_ordinary_cantonese_yields_no_candidates():
    """The extractor must not treat normal speech as novel. A model that
    returns something for every batch would fill the lexicon with 係/唔係
    and the block would be worthless."""
    observer = LexemeObserver(FakeModel(responses=['{"observations": []}']))
    assert await observer.observe(batch=rows("今日食咩好", "隨便啦")) == []


async def test_malformed_json_yields_nothing_rather_than_raising():
    """R-5: silence beats a wrong answer. The loop must survive a model that
    returns prose, and the cycle then appends nothing rather than crashing."""
    observer = LexemeObserver(FakeModel(responses=["sorry, I can't help with that"]))
    assert await observer.observe(batch=rows("好撚kam")) == []


async def test_a_candidate_carries_its_own_message_and_speaker():
    """Provenance is not decoration — the example rendered under a gloss is what
    makes a wrong gloss visible, and it must point at a real row."""
    observer = LexemeObserver(FakeModel(responses=[json.dumps({"observations": [
        {"surface_form": "kam", "canonical_form": "kam", "sense": "尷",
         "part_of_speech": "adjective", "message_id": 7, "confidence": 0.9},
    ]})]))
    [c] = await observer.observe(batch=rows_with_id(7, "好撚kam", person_id="tyler"))
    assert (c.canonical_form, c.message_id, c.person_id) == ("kam", 7, "tyler")


async def test_a_candidate_naming_a_message_not_in_the_batch_is_dropped():
    """The model can hallucinate a message_id. An observation whose evidence is
    not in the batch has no evidence, and fabricated provenance is worse than a
    missing entry."""
    observer = LexemeObserver(FakeModel(responses=[json.dumps({"observations": [
        {"surface_form": "kam", "canonical_form": "kam", "sense": "尷",
         "part_of_speech": "adjective", "message_id": 999, "confidence": 0.9},
    ]})]))
    assert await observer.observe(batch=rows_with_id(7, "好撚kam")) == []
```

- [ ] **Step 2: Run and watch them fail**

Run: `uv run pytest tests/unit/test_language_observer.py -v` → FAIL (module missing)

- [ ] **Step 3: Implement**

`LexemeObserver.__init__(self, model)` takes the model client as a constructor collaborator — no `*_fn` hook, per the test-seam policy. The prompt asks for distinctive usage only, demands evidence, and forbids ordinary Cantonese. Every returned candidate is joined back against the batch by `message_id`; anything unmatched is dropped and counted on `nora_language_candidates_dropped_total{reason="no_evidence"}`.

- [ ] **Step 4: Run tests, then all four gates**

- [ ] **Step 5: Commit**

```bash
git add nora/language/observer.py tests/unit/test_language_observer.py
git commit -m "feat(language): schema-forced lexeme extraction, evidence-joined"
```

---

### Task 7: `nora/infra/sql/language.py` — the store

**Files:**
- Create: `nora/infra/sql/language.py`
- Test: `tests/infra/test_language_store.py`

**Interfaces:**
- Produces: `SqlLanguageStore(engine)` with exactly these methods — Task 11's `dream_loop` calls every one of them, so the names are fixed here:

```python
async def append_observations(self, *, scope_id: str,
                              observations: Sequence[LexemeCandidate]) -> int: ...
async def rescore(self, *, scope_id: str, now_ms: int) -> None: ...
async def room_language(self, *, scope_id: str, limit: int) -> RoomLanguage: ...
async def watermark_for(self, *, consolidator_name: str, scope_id: str) -> int: ...
async def advance_watermark(self, *, consolidator_name: str, scope_id: str,
                            last_message_id: int) -> None: ...
async def watermark_age_seconds(self, *, consolidator_name: str,
                                scope_id: str) -> float: ...
async def lag(self, *, scope_id: str, after_id: int) -> int: ...
async def seconds_since_last_message(self, *, scope_id: str) -> float: ...
async def watermark_is_behind_the_record(self, *, scope_id: str,
                                         watermark: int) -> bool: ...
```

`lag` is the query `idx_messages_dream_lag` exists for:
`SELECT count(*) FROM messages WHERE conversation_id = :s AND id > :w AND is_nora = 0`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_appending_the_same_observation_twice_stores_one(store):
    """ON CONFLICT DO NOTHING against the natural key. This is what makes a
    retried cycle safe, and it is the single guarantee the rendered counts
    depend on."""
    obs = [observation(message_id=1, canonical_form="kam", sense="尷")]
    assert await store.append_observations(scope_id="g1", observations=obs) == 1
    assert await store.append_observations(scope_id="g1", observations=obs) == 0


async def test_rescore_counts_distinct_speakers_not_rows(store):
    """Three messages from one person is one speaker. The rendered
    «3 people» must mean three people."""
    await store.append_observations(scope_id="g1", observations=[
        observation(message_id=i, person_id="miles", canonical_form="kam")
        for i in (1, 2, 3)
    ])
    await store.rescore(scope_id="g1", now_ms=NOW)
    [lex] = (await store.room_language(scope_id="g1", limit=10)).lexemes
    assert (lex.occurrence_count, lex.distinct_speakers) == (3, 1)


async def test_two_senses_of_one_word_are_two_rows(store):
    """`sense` is in the primary key. Without that this silently overwrites."""
    await store.append_observations(scope_id="g1", observations=[
        observation(message_id=1, canonical_form="firm", sense="正/掂"),
        observation(message_id=2, canonical_form="firm", sense="堅硬"),
    ])
    await store.rescore(scope_id="g1", now_ms=NOW)
    lexemes = (await store.room_language(scope_id="g1", limit=10)).lexemes
    assert {l.sense for l in lexemes} == {"正/掂", "堅硬"}


async def test_stale_entries_do_not_render(store):
    ...


async def test_room_language_is_empty_for_an_unknown_scope(store):
    assert not await store.room_language(scope_id="nobody", limit=10)
```

- [ ] **Step 2: Run and watch them fail**

- [ ] **Step 3: Implement**

`append_observations` is a single `pg.insert(...).on_conflict_do_nothing(...)` returning the affected count. `rescore` recomputes `occurrence_count`, `distinct_speakers` (`COUNT(DISTINCT person_id)`), `first_seen_at`/`last_seen_at` and `examples` from `language_observation` grouped by `(canonical_form, sense)`, applies `scoring.score`/`state_for`, and upserts into `room_lexeme`. `room_language` selects `state IN ('live','fading')` ordered by `recognition_score DESC` with `LIMIT`, and **returns an empty `RoomLanguage` on any failure** rather than raising.

- [ ] **Step 4: Run tests and all four gates**

- [ ] **Step 5: Commit**

```bash
git add nora/infra/sql/language.py tests/infra/test_language_store.py
git commit -m "feat(sql): the language store — idempotent append, deterministic rescore"
```

---

### Task 8: `LanguageConsolidator` and the contract suite

**Files:**
- Create: `nora/language/consolidator.py`
- Modify: `tests/contract/test_room_consolidator_contract.py` (fill in the fixture from Task 4)
- Test: `tests/unit/test_language_consolidator.py`

**Interfaces:**
- Consumes: `LexemeObserver` (Task 6), `SqlLanguageStore` (Task 7).
- Produces: `LanguageConsolidator(observer, store)` satisfying `RoomConsolidator`; `name = "language"`.

- [ ] **Step 1: Wire the contract fixture to both implementations and run it**

Run: `uv run pytest tests/contract/test_room_consolidator_contract.py -q` → FAIL until Step 2.

- [ ] **Step 2: Implement the consolidator**

```python
class LanguageConsolidator:
    """Satisfies `nora.domain.ports.RoomConsolidator`.

    Two collaborators through `__init__`, no hooks: the observer owns the one
    model call, the store owns durability. This class owns only the order —
    extract, append, rescore — and returns what was appended.
    """

    name = "language"

    def __init__(self, *, observer: LexemeObserver, store: SqlLanguageStore) -> None:
        self._observer = observer
        self._store = store

    async def consolidate(self, *, scope_id: str,
                          batch: Sequence[TranscriptRow]) -> int:
        if not batch:
            return 0
        candidates = await self._observer.observe(batch=batch)
        appended = await self._store.append_observations(
            scope_id=scope_id, observations=candidates)
        # Rescore even when nothing was appended: decay is time-driven, so a
        # quiet scope must still be able to let a word fade. `dream_loop`'s 24h
        # firing depends on this being unconditional.
        await self._store.rescore(scope_id=scope_id, now_ms=_now_ms())
        return appended
```

- [ ] **Step 3: Add the unit test that a re-run does not inflate counts**

```python
async def test_replaying_a_batch_does_not_inflate_the_rendered_count():
    """The failure this prevents is not a crash — it is `12×, 3 people`
    silently becoming `24×, 3 people`, which reads as evidence and is not."""
    ...
```

- [ ] **Step 4: Run the contract suite, unit tests, and all four gates**

- [ ] **Step 5: Commit**

```bash
git add nora/language/consolidator.py tests/contract/test_room_consolidator_contract.py tests/unit/test_language_consolidator.py
git commit -m "feat(language): LanguageConsolidator against the shared contract"
```

---

### Task 9: `nora/language/render.py` — the capped block

**Files:**
- Create: `nora/language/render.py`
- Test: `tests/unit/test_language_render.py`

**Interfaces:**
- Consumes: `RoomLanguage` (Task 4).
- Produces: `render_room_language(language: RoomLanguage, *, max_entries: int) -> str` — `""` when there is nothing to say.

- [ ] **Step 1: Write the failing tests**

```python
def test_an_empty_language_renders_no_block_at_all():
    """Not an empty block — NO block. A new room has no evidence, and a
    `<room_language>` tag with nothing in it invites the model to invent
    something to fill it."""
    assert render_room_language(RoomLanguage(scope_id="g1"), max_entries=10) == ""


def test_every_entry_carries_a_verbatim_example():
    """The gloss is model-authored; the quote is not. An entry with no quotable
    example must not render, because the gloss would then be an assertion with
    nothing under it."""
    block = render_room_language(language_with(
        lexeme("kam", sense="尷", examples=[example("個plan好撚kam")]),
        lexeme("ghost", sense="失蹤", examples=[]),
    ), max_entries=10)
    assert "個plan好撚kam" in block
    assert "ghost" not in block


def test_the_block_is_capped_by_score():
    """A room active for a year accumulates entries forever, and this block
    sits in the UNCACHED tier — so the cap is a cost control, not tidiness."""
    block = render_room_language(language_with(*[lexeme(f"w{i}") for i in range(50)]),
                                 max_entries=5)
    assert block.count("\n  ") == 5


def test_the_closing_frame_is_present_and_fixed():
    """Authored template text, never generated — it is what stops the list
    reading as a menu of words to reach for."""
    block = render_room_language(language_with(lexeme("kam")), max_entries=10)
    assert "not a set of words to reach for" in block
    assert "not a description of anybody" in block
```

- [ ] **Step 2: Run and watch them fail**

- [ ] **Step 3: Implement**

Renders `<room_language>`, a `Words this room has made its own:` heading, one line per entry (`form  sense  「example」  N×, M people, since <date>`), then the fixed closing frame. Entries without an example are skipped; the whole block is `""` if nothing survives.

- [ ] **Step 4: Run tests and all four gates**

- [ ] **Step 5: Commit**

```bash
git add nora/language/render.py tests/unit/test_language_render.py
git commit -m "feat(language): the capped, evidence-carrying prefill block"
```

---

### Task 10: Wire into the turn — `TagContext`, `TranscriptInjection`, `prefill`

**The placement is the point of this task.** The block goes in the **volatile** tier, after `context_header`. `prefill.py:112-146` documents that head as deliberately free of anything that varies between tags, at a re-measured 98-99% cache hit, because the cache matches on an exact prefix. A per-room, per-regeneration block there fragments one deployment-wide prefix into one per room.

**Files:**
- Modify: `nora/domain/models.py` (`TagContext`)
- Modify: `nora/engagement/context.py` (`TranscriptInjection`)
- Modify: `nora/agent/prefill.py` (`build_prefill`)
- Test: `tests/unit/test_prefill.py`, `tests/unit/test_context_assembly.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_the_room_language_block_is_after_the_context_header():
    """The static head must stay byte-identical across tags — it is 95-99%
    cached, re-measured 2026-08-08. A per-room block inside it fragments that
    prefix into one per room."""
    prefill = build_prefill(ctx_with_room_language(), ...)
    body = prefill[-1]["content"]
    assert body.index("<room_language>") > body.index("</context_header>")
    assert body.index("<room_language>") < body.index("<group_transcript>")


async def test_a_failing_language_read_yields_an_empty_block_not_a_dead_turn():
    """One of five siblings in a `gather` — an exception here would cancel the
    other four and take the turn down. Same property `_peer_card` already
    guards."""
    assembler = TranscriptInjection(..., language=RaisingLanguageReader())
    ctx = await assembler.assemble(request=request)
    assert ctx.room_language == RoomLanguage(scope_id="g1")
    assert ctx.transcript, "the other reads must still have completed"
```

- [ ] **Step 2: Run and watch them fail**

- [ ] **Step 3: Add the `TagContext` field**

```python
    room_language: RoomLanguage = field(default_factory=lambda: RoomLanguage(scope_id=""))
```

- [ ] **Step 4: Add the fifth `gather` branch**

In `nora/engagement/context.py`, add `language: RoomLanguageReader` to `__init__`, add `self._room_language(msg)` to the existing `asyncio.gather`, and write the helper **not to raise** — logging and counting `nora_errors_total{stage="room_language"}`, returning an empty `RoomLanguage`. The module docstring already explains why every sibling must have this property.

- [ ] **Step 5: Render it in the volatile tier**

In `prefill.py::build_prefill`, insert `render_room_language(...)` between `_peer_history_block(ctx)` and `_transcript_block(...)`.

- [ ] **Step 6: Run tests and all four gates**

- [ ] **Step 7: Commit**

```bash
git add nora/domain/models.py nora/engagement/context.py nora/agent/prefill.py tests/unit/
git commit -m "feat(prefill): render room language in the volatile tier"
```

---

### Task 11: `dream_loop`, config, and composition

**Files:**
- Modify: `nora/app/runtime.py`
- Modify: `nora/config.py` (a `[language]` section)
- Modify: `nora/app/composition.py`
- Modify: `nora/__main__.py`
- Modify: `config.toml`, `config.example.toml`
- Test: `tests/unit/test_runtime.py`, `tests/test_main.py`

**Interfaces:**
- Consumes: `RoomConsolidator` (Task 4), `RecordStore.since` (Task 2).
- Produces: `dream_loop(store, consolidators, language_store, metrics, *, scopes, tick_seconds, safety_lag, quiet_seconds, max_age_seconds, batch_limit)`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_a_second_tick_does_not_re_enter_a_running_scope():
    """Without the lock, a slow extraction lets the next tick re-fire against
    the same un-advanced watermark, and two cycles append the same evidence."""
    ...


async def test_the_watermark_advances_only_after_observations_are_committed():
    """The unsafe direction is watermark-first: a crash between them loses that
    range forever, and nora_dream_evidence_lost_total is defined for the
    PRUNING case and is structurally blind to it."""
    ...


async def test_a_failing_extraction_does_not_starve_the_other_scopes():
    """`prune_loop`'s single try/except wraps ONE unit of work. A dream tick's
    unit is a LIST of scopes, so copying that shape verbatim lets one bad scope
    take out every other scope that tick."""
    ...


async def test_the_daily_firing_rescores_even_with_an_empty_batch():
    """Two of three fire conditions need lag > 0, so a dormant room would never
    decay — its lexicon would stay `live` describing a slang era that ended."""
    ...
```

- [ ] **Step 2: Run and watch them fail**

- [ ] **Step 3: Implement `dream_loop`**

```python
async def dream_loop(
    record: _SinceReadable, language: SqlLanguageStore,
    consolidators: Sequence[RoomConsolidator], metrics: Metrics, *,
    scopes: Callable[[], Sequence[str]], tick_seconds: float,
    safety_lag: int, quiet_seconds: float, max_age_seconds: float,
    batch_limit: int,
) -> None:
    """The Dreamer's clock. A sibling of `prune_loop`, with two differences that
    are not stylistic.

    FIRST, the try/except is PER SCOPE. `prune_loop`'s single wrapper is correct
    there because its unit of work is one statement; a dream tick's unit is a
    LIST of scopes, so copying that shape verbatim lets one failing scope starve
    every other scope on that tick.

    SECOND, the backoff comes from `supervise_adapter` rather than from
    `prune_loop`. `prune_all` talks to the local database, where a failure is
    rare and transient; this talks to an external model provider, where a 429
    answered at full cadence forever makes the outage worse and burns the quota.
    """
    failures: dict[str, int] = {}
    running: set[str] = set()
    while True:
        await asyncio.sleep(tick_seconds)
        for scope_id in scopes():
            if scope_id in running:
                # A slow extraction is still going. Skipping is the whole
                # guarantee: re-entering would read the same un-advanced
                # watermark and append the same evidence twice, and the
                # rendered «12x, 3 people» would be a fabricated number.
                metrics.inc("nora_dream_skipped_total", reason="in_flight")
                continue
            if failures.get(scope_id) and not _backoff_elapsed(failures[scope_id]):
                continue
            running.add(scope_id)
            try:
                await _dream_once(
                    record=record, language=language, consolidators=consolidators,
                    metrics=metrics, scope_id=scope_id, safety_lag=safety_lag,
                    quiet_seconds=quiet_seconds, max_age_seconds=max_age_seconds,
                    batch_limit=batch_limit,
                )
                failures.pop(scope_id, None)
            except Exception:
                log.exception("dream cycle failed for %s", scope_id)
                metrics.inc("nora_errors_total", stage="dream")
                failures[scope_id] = failures.get(scope_id, 0) + 1
            finally:
                running.discard(scope_id)


async def _dream_once(*, record, language, consolidators, metrics, scope_id,
                      safety_lag, quiet_seconds, max_age_seconds, batch_limit) -> None:
    watermark = await language.watermark_for(
        consolidator_name="language", scope_id=scope_id)

    # The fail-closed tell. A watermark below the oldest surviving row means
    # messages were deleted before they were ever read — R-27's `unavailable`
    # («I could not see those»), never `nothing_found` («nothing happened»).
    # Structurally zero while `store.prune_enabled` is false; this is what
    # notices if anyone turns the sweep back on.
    if await language.watermark_is_behind_the_record(
            scope_id=scope_id, watermark=watermark):
        metrics.inc("nora_dream_evidence_lost_total", scope=scope_id)

    lag = await language.lag(scope_id=scope_id, after_id=watermark)
    quiet = await language.seconds_since_last_message(scope_id=scope_id)
    age = await language.watermark_age_seconds(
        consolidator_name="language", scope_id=scope_id)

    fires = (
        lag > safety_lag
        or (lag > 0 and quiet >= quiet_seconds)
        or age >= max_age_seconds      # fires at lag == 0 ON PURPOSE
    )
    if not fires:
        return

    batch = await record.since(
        conversation_id=scope_id, after_id=watermark, limit=batch_limit)

    for consolidator in consolidators:
        # An empty batch still reaches the consolidator, which still rescores.
        # Decay is TIME-driven: two of the three fire conditions need lag > 0,
        # so without this a dormant room never fades a word and its lexicon
        # stays `live` describing a slang era that ended months ago.
        await consolidator.consolidate(scope_id=scope_id, batch=batch)

    if batch:
        # LAST, and after the appends have committed. The unsafe direction is
        # watermark-first: a crash between the two loses that range forever,
        # with nothing able to notice — `nora_dream_evidence_lost_total` above
        # measures the PRUNING case and is structurally blind to this one.
        # Append-first plus the natural-key constraint makes a retry a no-op
        # instead, so over-reading is the safe failure and under-reading is not.
        await language.advance_watermark(
            consolidator_name="language", scope_id=scope_id,
            last_message_id=batch[-1].id,
        )
```

`_backoff_elapsed` is the capped exponential shape from `supervise_adapter` (`runtime.py:160-187`) — reuse it rather than re-deriving it.

- [ ] **Step 4: Add the config section**

`LanguageConfig` in `nora/config.py`, **whole section optional with every field defaulted**, so a `config.toml` written before it existed keeps loading — the convention every optional section in that file follows. Fields with their reasoning recorded inline: `enabled` (default `False` — a new subsystem does not switch itself on), `tick_seconds = 300`, `safety_lag = 300`, `quiet_seconds = 1800`, `max_age_seconds = 86400`, `batch_limit = 400`, `max_rendered_lexemes = 12`, `model`.

- [ ] **Step 5: Wire composition and spawn the loop**

Build the store, observer and consolidator in `composition.py`; spawn `dream_loop` in `__main__.py` next to `prune_loop`, **only when `cfg.language.enabled`**, logging a line when it is off (the same shape the retention flag uses).

- [ ] **Step 6: Run tests and all four gates**

- [ ] **Step 7: Commit**

```bash
git add nora/app/runtime.py nora/config.py nora/app/composition.py nora/__main__.py config.toml config.example.toml tests/
git commit -m "feat(app): dream_loop — per-scope, locked, watermark-last"
```

---

### Task 12: DEC-107, the acceptance rule, and the release fragment

**Files:**
- Modify: `design/09-decision-index.md`
- Modify: `tests/test_acceptance_rules.py`
- Create: `release-fragments/unreleased/room-language.json`

- [ ] **Step 1: Write the failing acceptance test**

```python
def test_her_own_words_are_never_evidence_for_the_room_lexicon():
    """DEC-107. The runaway loop this prevents: she emits a word -> it
    strengthens -> the block normalises it -> she emits it more. `is_nora = 0`
    lives in `RecordStore.since` itself, not in a caller that could forget it.

    Reverting the predicate must turn this red."""
    ...
```

Register it in `RULE_TESTS`.

- [ ] **Step 2: Run it and watch it fail, then pass**

- [ ] **Step 3: Add the DEC-107 entry**

Add to `design/09-decision-index.md` — confirm the next free number first, since another session is committing here. The entry states the rule: *classify the illocutionary act an utterance performs; never adopt its propositional content as a belief about the world, and never attribute a disposition to a person* — and records that Plan 1 exercises only the first half, with the exchange half arriving in Plan 2.

- [ ] **Step 4: Write the release fragment**

```json
{
  "type": "feature",
  "summary": "Nora picks up the words this room has made its own, and knows what they mean here.",
  "detail": "A word your group uses in its own way — one that means nothing to an outsider and may not have existed a month ago — used to be invisible to her unless somebody had just said it in the last few messages. She now builds up a small glossary from what people actually say in the room, with a real quote under each entry so you can see where it came from, and she keeps it current as words arrive and fade. It is a glossary, not a vocabulary list: it tells her what your words mean, and is not a set of words for her to start using. She never learns from her own messages, and nothing in it describes a person."
}
```

- [ ] **Step 5: Validate fragments, run all four gates, commit**

```bash
uv run python scripts/check_release_fragments.py --base origin/master
uv run python scripts/render_release_notes.py
git add design/09-decision-index.md tests/test_acceptance_rules.py release-fragments/unreleased/room-language.json
git commit -m "docs(dec): DEC-107 — the pattern, never the propositional content"
```

---

## After this plan

**Do not deploy with `language.enabled = true` until a dry run has been read.** Turn it on locally, let one cycle fire against real history, and read the extracted observations before the block ever reaches a prompt. The failure to look for is the extractor treating ordinary Cantonese as novel — that fills the lexicon with 係/唔係 and makes the block worse than nothing.

Then Plan 2 (exchange patterns, and the withhold rule that is the reason it needs its own plan) and Plan 3 (the portrait).
