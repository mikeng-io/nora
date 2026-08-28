"""Pass 4 — and the three ways it could look like it worked while corrupting
the store.

The doubles here match production **where the pass branches**: the embedder
returns the three statuses `OllamaEmbedder` really returns, the store's
`existing` returns a status beside its rows, and `upsert` reports what it
wrote. A fake that answered every call the same way could not produce the
mid-sweep failure this module exists to survive.
"""

from __future__ import annotations

from dataclasses import dataclass

from nora.domain.facts import Fact
from nora.infra.embedding import fingerprint, render_for_embedding
from nora.infra.sql.embeddings import EmbeddingRead, StoredEmbedding
from nora.language.fact_embedder import FACT_KIND, FactEmbedder
from nora.obs.metrics import Metrics

WS = "ws-main"
NOW = 1_755_200_000_000
MODEL = "bge-m3"


def _fact(fact_id: str, *, subject="big walk", predicate="means",
          obj="a walking activity", created_at=NOW) -> Fact:
    return Fact(
        id=fact_id, workspace=WS, subject_id=subject, predicate=predicate,
        object_entity_id=None, object_literal=obj,
        source_url="https://x.test/a", source_excerpt="x",
        valid_from=created_at, created_at=created_at,
        instruction_sha256="a" * 64, model="m", established_by="tick-1",
    )


def _text(f: Fact) -> str:
    return render_for_embedding(
        subject=f.subject_id, predicate=f.predicate, obj=f.object_literal,
    )


@dataclass
class _EmbedResult:
    vectors: tuple[tuple[float, ...], ...]
    status: str


class _FakeEmbedder:
    """Answers `ok` until `fail_after` batches, then `unavailable` — which is
    what a service going down mid-sweep actually looks like, and the one shape
    a fake that always succeeds cannot produce."""

    def __init__(self, *, fail_after: int | None = None,
                 model: str = MODEL) -> None:
        self._fail_after = fail_after
        self._model = model
        self.batches: list[tuple[str, ...]] = []

    @property
    def model(self) -> str:
        return self._model

    async def embed(self, texts):
        self.batches.append(texts)
        if self._fail_after is not None and len(self.batches) > self._fail_after:
            return _EmbedResult(vectors=(), status="unavailable")
        return _EmbedResult(
            vectors=tuple((float(i), 0.0) for i in range(len(texts))),
            status="ok",
        )


class _FakeFacts:
    def __init__(self, facts=(), *, status="ok") -> None:
        self._facts = tuple(facts)
        self._status = status
        self.calls: list[dict] = []

    async def embeddable(self, *, workspace: str, limit: int):
        self.calls.append({"workspace": workspace, "limit": limit})
        if self._status != "ok":
            return (), self._status
        return self._facts[:limit], ("ok" if self._facts else "nothing_found")


class _FakeEmbeddings:
    def __init__(self, *, held=(), status="ok") -> None:
        self.rows: dict[str, StoredEmbedding] = {
            r.row_key: r for r in held
        }
        self._status = status
        self.written: list[StoredEmbedding] = []

    async def existing(self, *, kind: str, scope: str, row_keys):
        if self._status != "ok":
            return EmbeddingRead(rows=(), status=self._status)
        found = tuple(self.rows[k] for k in row_keys if k in self.rows)
        return EmbeddingRead(
            rows=found, status="ok" if found else "nothing_found",
        )

    async def upsert(self, *, rows):
        for row, _vector in rows:
            self.rows[row.row_key] = row
            self.written.append(row)
        return len(rows)


def _embedder(*, facts, embeddings, embedder=None, metrics=None, **kw):
    return FactEmbedder(
        facts=facts, embeddings=embeddings,
        embedder=embedder or _FakeEmbedder(), metrics=metrics, **kw,
    )


# ---------------------------------------------------------------------------
# the staleness predicate — one comparison, three things
# ---------------------------------------------------------------------------


async def test_a_fact_with_no_embedding_is_stale():
    facts = _FakeFacts([_fact("f1")])
    store = _FakeEmbeddings()
    metrics = Metrics()

    sweep = await _embedder(
        facts=facts, embeddings=store, metrics=metrics,
    ).sweep(workspace=WS, now_ms=NOW)

    assert sweep.status == "ok"
    assert sweep.embedded == 1
    assert [r.row_key for r in store.written] == ["f1"]
    assert store.written[0].kind == FACT_KIND
    assert store.written[0].scope == WS
    assert metrics.get("nora_fact_embed_sweep_total", status="ok") == 1


async def test_a_fact_whose_fingerprint_still_matches_is_not_re_embedded():
    """The steady state, and the reason the sweep is affordable on a clock: a
    workspace where nothing changed costs one read and no model call at all."""
    f = _fact("f1")
    held = StoredEmbedding(
        kind=FACT_KIND, row_key="f1", scope=WS, text=_text(f), model=MODEL,
        fingerprint=fingerprint(model=MODEL, text=_text(f)),
        embedded_at=NOW - 1, has_vector=True,
    )
    facts, store = _FakeFacts([f]), _FakeEmbeddings(held=[held])
    embedder = _FakeEmbedder()
    metrics = Metrics()

    sweep = await _embedder(
        facts=facts, embeddings=store, embedder=embedder, metrics=metrics,
    ).sweep(workspace=WS, now_ms=NOW)

    assert sweep.status == "nothing_found"
    assert sweep.embedded == 0
    assert embedder.batches == [], "a current row must not cost a model call"
    assert metrics.get(
        "nora_fact_embed_sweep_total", status="nothing_found") == 1


async def test_a_model_swap_makes_every_row_stale():
    """The fingerprint covers (model, text), so ONE comparison catches a model
    change as well as a row change. A predicate over the text alone would leave
    the whole corpus embedded under the old model and silently unsearchable
    against the new one."""
    f = _fact("f1")
    held = StoredEmbedding(
        kind=FACT_KIND, row_key="f1", scope=WS, text=_text(f), model="bge-m2",
        fingerprint=fingerprint(model="bge-m2", text=_text(f)),
        embedded_at=NOW - 1, has_vector=True,
    )
    store = _FakeEmbeddings(held=[held])

    sweep = await _embedder(
        facts=_FakeFacts([f]), embeddings=store,
        embedder=_FakeEmbedder(model=MODEL),
    ).sweep(workspace=WS, now_ms=NOW)

    assert sweep.embedded == 1
    assert store.rows["f1"].model == MODEL


async def test_a_row_recorded_without_its_vector_is_still_stale():
    """`v1024` is nullable so the text can be recorded before the endpoint
    answers. A predicate that looked only at the fingerprint would call such a
    row current and it would never be embedded — the exact "un-embeddable
    forever" shape the endpoint's own short-batch refusal exists to avoid."""
    f = _fact("f1")
    held = StoredEmbedding(
        kind=FACT_KIND, row_key="f1", scope=WS, text=_text(f), model=MODEL,
        fingerprint=fingerprint(model=MODEL, text=_text(f)),
        embedded_at=NOW - 1, has_vector=False,
    )
    store = _FakeEmbeddings(held=[held])

    sweep = await _embedder(
        facts=_FakeFacts([f]), embeddings=store,
    ).sweep(workspace=WS, now_ms=NOW)

    assert sweep.embedded == 1
    assert store.rows["f1"].has_vector is True


# ---------------------------------------------------------------------------
# mid-sweep failure — partial PROGRESS is fine, partial ROWS are not
# ---------------------------------------------------------------------------


async def test_the_endpoint_dying_mid_sweep_keeps_what_it_wrote_and_stops():
    """**The failure that matters most in this pass**, and the one the
    coordinator asked to see built in rather than retrofitted.

    Three batches of two, the endpoint dies on the third. The first two
    batches are written and CORRECT — each row's fingerprint describes its own
    vector, because the text, the fingerprint and the vector go into one row
    in one statement. The rest are untouched and stay stale, so the next sweep
    retries exactly them.

    It STOPS rather than trying the next batch: the endpoint is one service,
    so a batch failing because it is down means every later batch fails too,
    and continuing is the polling anti-pattern with a worse blast radius.

    `unavailable` even though rows were written, because the question the
    status answers is «is the tier up to date», and the answer is no.
    """
    facts = _FakeFacts([_fact(f"f{i}") for i in range(6)])
    store = _FakeEmbeddings()
    embedder = _FakeEmbedder(fail_after=2)
    metrics = Metrics()

    sweep = await _embedder(
        facts=facts, embeddings=store, embedder=embedder, metrics=metrics,
        batch_size=2,
    ).sweep(workspace=WS, now_ms=NOW)

    assert sweep.status == "unavailable"
    assert sweep.embedded == 4, "the two successful batches are written"
    assert sweep.stale == 6, "and the sweep reports what it set out to do"
    assert len(embedder.batches) == 3, (
        "it stopped at the failure rather than trying the remaining batches"
    )
    assert [r.row_key for r in store.written] == ["f0", "f1", "f2", "f3"]
    assert metrics.get(
        "nora_fact_embed_sweep_total", status="unavailable") == 1


async def test_every_row_written_carries_the_fingerprint_of_its_own_text():
    """The invariant a partial sweep must not break: a row whose fingerprint
    describes different text than its vector is worse than an absent row,
    because the staleness predicate would call it current forever.

    Checked over a batch of DIFFERENT facts, so a store that wrote one
    fingerprint for the whole batch — the shape a zip bug produces — fails
    here rather than looking correct.
    """
    facts = _FakeFacts([
        _fact("f1", subject="big walk"),
        _fact("f2", subject="打風", obj="a typhoon"),
        _fact("f3", predicate="made_by", obj="House House"),
    ])
    store = _FakeEmbeddings()

    await _embedder(facts=facts, embeddings=store).sweep(
        workspace=WS, now_ms=NOW,
    )

    assert len(store.written) == 3
    for row in store.written:
        assert row.fingerprint == fingerprint(model=MODEL, text=row.text)
    assert len({r.fingerprint for r in store.written}) == 3


# ---------------------------------------------------------------------------
# R-27 — the sweep must never report a clean pass it did not make
# ---------------------------------------------------------------------------


async def test_an_unreadable_fact_table_is_unavailable_not_nothing_found():
    """«I could not read the facts» is not «there is nothing to embed». The
    second says the tier is up to date, and a caller that advanced a watermark
    on it would never come back for the rows it never saw."""
    store = _FakeEmbeddings()
    metrics = Metrics()

    sweep = await _embedder(
        facts=_FakeFacts(status="unavailable"), embeddings=store,
        metrics=metrics,
    ).sweep(workspace=WS, now_ms=NOW)

    assert sweep.status == "unavailable"
    assert store.written == []
    assert metrics.get(
        "nora_fact_embed_sweep_total", status="unavailable") == 1


async def test_unreadable_stored_fingerprints_are_unavailable_not_stale():
    """The subtler half. If the fingerprint read fails, EVERY row looks
    un-embedded — so a sweep that carried on would re-embed the whole
    workspace against a database that is already unhappy, and report `ok`."""
    embedder = _FakeEmbedder()

    sweep = await _embedder(
        facts=_FakeFacts([_fact("f1")]),
        embeddings=_FakeEmbeddings(status="unavailable"),
        embedder=embedder,
    ).sweep(workspace=WS, now_ms=NOW)

    assert sweep.status == "unavailable"
    assert embedder.batches == [], "not one model call against an unknown state"


async def test_an_empty_workspace_is_nothing_found_and_costs_no_call():
    embedder = _FakeEmbedder()

    sweep = await _embedder(
        facts=_FakeFacts([]), embeddings=_FakeEmbeddings(), embedder=embedder,
    ).sweep(workspace=WS, now_ms=NOW)

    assert sweep.status == "nothing_found"
    assert embedder.batches == []


async def test_a_sweep_never_raises_into_the_tick():
    """It runs beside other adaptors in the Dreamer's tick, so a fault must
    cost the sweep and not the tick. Asserted with a collaborator that raises
    rather than returns, which is the case the status plumbing cannot see."""
    class _Exploding:
        async def embeddable(self, *, workspace: str, limit: int):
            raise RuntimeError("the connection pool is gone")

    metrics = Metrics()
    sweep = await _embedder(
        facts=_Exploding(), embeddings=_FakeEmbeddings(), metrics=metrics,
    ).sweep(workspace=WS, now_ms=NOW)

    assert sweep.status == "unavailable"
    assert sweep.embedded == 0
    assert metrics.get(
        "nora_fact_embed_sweep_total", status="unavailable") == 1


async def test_the_sweep_is_bounded_by_its_own_cap():
    """The tick's cost knob. Unbounded, one sweep over a large workspace is a
    Dreamer tick that never ends and an endpoint hammered in one burst."""
    facts = _FakeFacts([_fact(f"f{i}") for i in range(50)])

    await _embedder(
        facts=facts, embeddings=_FakeEmbeddings(), max_rows_per_sweep=10,
    ).sweep(workspace=WS, now_ms=NOW)

    assert facts.calls[0]["limit"] == 10
