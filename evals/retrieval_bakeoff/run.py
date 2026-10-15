"""The retrieval bake-off's harness (design/unified-memory/20, PROTOCOL.md).

comprehend: `load_set` reads the locked set once (the pool, each item's
frozen reading, the queries and their labels) and refuses a set whose files
do not match `labels.lock`. Every arm then ranks the same fixed model.

measure: each arm turns every query into a ranking of pool ids, or into
`Errored(kind)`. An item that cannot be embedded (an exception, a timeout,
a non-finite value, the wrong length, a zero vector, a media file whose
bytes do not hash to its `sha256`) is an ERROR, never a miss: an ERROR query
is left out of the mean and counted, an ERROR pool item cannot be ranked
and is counted, and any ERROR makes the arm's verdict on the slice ERROR
(`score`). recall@5 = |relevant in top 5| / min(|relevant|, 5); MRR@10 =
1 / the rank of the first relevant item, 0 past 10; both with a bootstrap
95% CI that is reported and never decided on (`decide.py`). Fused arms are
reciprocal rank fusion, k = 60, over each channel's top 50.

serialize: one JSON per run under `results/`, aggregates only: no vector,
no query or reading text, nothing from a room. It carries what re-derives
it: the commit, `labels.lock`, each arm's model digest, dimensions, prefix
and dtype, and a host sample before and after.

**The reading gap** (PROTOCOL.md «No hand-judged gap flag») is derived, not
judged: a query is in the gap when no reading-only lexical arm places a
relevant item in its top 10. Every arm is also scored inside and outside it.

Stage 1 (lexical) plus the bge-m3 floor, on the deploy host, inside a
throwaway Postgres (never Nora's: `nora.subject.lexical_seed` writes only to
an empty database named `bakeoff*`, which it marks), with the memory
watchdog on:

    docker run -d --name bakeoff-pg --cpus 2 --memory 2g -e POSTGRES_DB=bakeoff \\
        -e POSTGRES_HOST_AUTH_METHOD=trust -p 127.0.0.1:55439:5432 pgvector/pgvector:pg18
    nice -n 10 uv run --with bm25s==0.3.13 python -m evals.retrieval_bakeoff.run \\
        --dsn postgresql+asyncpg://postgres@127.0.0.1:55439/bakeoff

The watchdog aborts the run (ERROR, exit 3) when MemAvailable drops below
3 GiB, swap grows by more than 1 GiB, or less than 1 GiB of swap is left; an
aborted run is rerun, never scored.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import importlib.metadata
import json
import os
import platform
import random
import re
import resource
import statistics
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from evals.retrieval_bakeoff.adapters import (
    OFFICIAL,
    Embedder,
    EmbedError,
    HFEmbedder,
    OfficialEmbedder,
    OllamaEmbedder,
    check,
    matryoshka,
    sha256_file,
)
from nora.subject import lexical_seed

SET_DIR = Path(__file__).resolve().parent
#: Where the deploy host keeps the pool's images, `<sha256>.jpg` (PROTOCOL.md).
MEDIA_DIR = Path("/home/norty/nora-bakeoff/media")
RRF_K = 60
DEPTH = 50
BATCH = 16
WARMUPS = 2
GIB_KIB = 1024 * 1024
#: The reading gap's rule: no lexical arm has a relevant item this deep.
GAP_DEPTH = 10


class LockMismatchError(RuntimeError):
    """A locked file does not hash to its `labels.lock` line."""


class AbortedError(RuntimeError):
    """The memory watchdog stopped the run, or could not read the host."""


@dataclass(frozen=True, slots=True)
class Errored:
    """A query or item that could not be measured. Never a miss."""

    kind: str


Ranking = list[str] | Errored
Rankings = dict[str, Ranking]


@dataclass(frozen=True, slots=True)
class Query:
    text: str
    lang: str


@dataclass(frozen=True, slots=True)
class Media:
    sha256: str
    title: str
    description: str
    content: str

    @property
    def reading(self) -> str:
        """The text a text-only arm sees: the three fields joined the way the
        memory index joins a `media_objects` row (`memory_index._reading`)."""
        return "\n".join(p for p in (self.title, self.description, self.content) if p)


@dataclass(frozen=True, slots=True)
class Message:
    sent_ms: int
    text: str


@dataclass(frozen=True, slots=True)
class BakeoffSet:
    slice: str
    version: int
    pool: tuple[str, ...]
    queries: dict[str, Query]
    relevant: dict[str, frozenset[str]]
    hard_negatives: dict[str, frozenset[str]]
    media: dict[str, Media]
    lock: dict[str, str]
    messages: dict[str, Message] = field(default_factory=dict)
    #: Named query subsets every result reports apart (`time_only`).
    subsets: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def text(self, pid: str) -> str:
        """What a text-only arm embeds for a pool item: a photo's reading or a
        message's text."""
        return self.media[pid].reading if pid in self.media else self.messages[pid].text


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_set(root: Path = SET_DIR, slice_name: str = "text_image") -> BakeoffSet:
    """The slice's pool, queries, labels and items, from the files `set.yaml`
    names for it (`items`, `labels`; a set without them is the first,
    text-to-image-only format: `media.yaml` and `labels.yaml`). Every file
    `labels.lock` lists must hash to its line, and every file the slice reads
    must be listed: an unlocked file is as unlabelled as a changed one."""
    lock: dict[str, str] = {}
    for line in (root / "labels.lock").read_text(encoding="utf-8").splitlines():
        if line.strip():
            digest, name = line.split()
            lock[name] = digest
    for name, digest in lock.items():
        actual = _sha256(root / name)
        if digest != actual:
            raise LockMismatchError(f"{name} hashes to {actual}, labels.lock says {digest}")
    the_set = yaml.safe_load((root / "set.yaml").read_text(encoding="utf-8"))
    sl = the_set["slices"][slice_name]
    items_file = sl.get("items", "media.yaml")
    labels_file = sl.get("labels", "labels.yaml")
    unlocked = [n for n in ("set.yaml", items_file, labels_file) if n not in lock]
    if unlocked:
        raise LockMismatchError(f"{slice_name} reads {unlocked}, which labels.lock does not list")
    items = yaml.safe_load((root / items_file).read_text(encoding="utf-8"))["items"]
    labels = yaml.safe_load((root / labels_file).read_text(encoding="utf-8"))
    queries = {q["id"]: Query(q["text"], q["lang"]) for q in sl["queries"]}
    per_query = labels["queries"]
    time_only = tuple(q for q in labels.get("time_only_queries") or {} if q in queries)
    return BakeoffSet(
        slice=slice_name,
        version=int(the_set["version"]),
        pool=tuple(sl["pool"]),
        queries=queries,
        relevant={q: frozenset(per_query[q]["relevant"]) for q in queries},
        hard_negatives={q: frozenset(per_query[q].get("hard_negatives") or ()) for q in queries},
        media={
            pid: Media(item["sha256"], item["reading"].get("title", ""),
                       item["reading"].get("description", ""), item["reading"].get("content", ""))
            for pid, item in items.items() if "reading" in item
        },
        lock=lock,
        messages={pid: Message(_hk_ms(item["sent"]), item["text"])
                  for pid, item in items.items() if "text" in item},
        subsets={"time_only": time_only} if time_only else {},
    )


def _hk_ms(sent: str) -> int:
    """`texts.yaml`'s 'YYYY-MM-DD HH:MM', Hong Kong time, as epoch ms."""
    at = datetime.strptime(sent, "%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo("Asia/Hong_Kong"))
    return int(at.timestamp() * 1000)


# --- embedding, fail-closed -------------------------------------------------------------


@dataclass
class Embedded:
    vectors: dict[str, list[float]] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    batch_ms: list[float] = field(default_factory=list)
    item_ms: list[float] = field(default_factory=list)


def _with_timeout(call: Callable[[], Any], timeout: float) -> Any:
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        return pool.submit(call).result(timeout=timeout)
    finally:
        pool.shutdown(wait=False)


def _embed_batch(out: Embedded, ids: Sequence[str], call: Callable[[], Any], native_dim: int,
                 dim: int, timeout: float) -> None:
    t0 = time.perf_counter()
    try:
        raw = list(_with_timeout(call, timeout))
    except TimeoutError:
        out.errors.update(dict.fromkeys(ids, "timeout"))
        return
    except EmbedError as e:
        out.errors.update(dict.fromkeys(ids, e.kind))
        return
    except Exception:  # noqa: BLE001 - every failure is an ERROR for its items
        out.errors.update(dict.fromkeys(ids, "exception"))
        return
    ms = (time.perf_counter() - t0) * 1000
    if len(raw) != len(ids):
        out.errors.update(dict.fromkeys(ids, "count_mismatch"))
        return
    out.batch_ms.append(ms)
    out.item_ms.extend([ms / len(ids)] * len(ids))
    for i, vec in zip(ids, raw, strict=True):
        try:
            out.vectors[i] = matryoshka(check(vec, native_dim), dim)
        except EmbedError as e:
            out.errors[i] = e.kind


def embed_texts(embedder: Embedder, items: Mapping[str, str], role: str, *, dim: int,
                timeout: float, batch: int = BATCH,
                watchdog: MemoryWatchdog | None = None) -> Embedded:
    out = Embedded()
    ids = list(items)
    for start in range(0, len(ids), batch):
        if watchdog is not None:
            watchdog.check()
        chunk = ids[start:start + batch]
        texts = [items[i] for i in chunk]
        _embed_batch(out, chunk, lambda texts=texts: embedder.embed_text(texts, role),
                     embedder.native_dim, dim, timeout)
    return _accounted(out, ids)


def _accounted(out: Embedded, ids: Sequence[str]) -> Embedded:
    """Every requested id is embedded or an ERROR; one that is neither was
    lost on the way and is an ERROR too, never absent from the scoring."""
    for i in ids:
        if i not in out.vectors and i not in out.errors:
            out.errors[i] = "lost"
    return out


#: Two different images whose vectors agree this closely were not both seen:
#: a runtime that drops the image embeds only its wrapper prompt (Ollama 0.35
#: did exactly that with `images` on /api/embed).
SAME_VECTOR_COSINE = 1 - 1e-6


def _same_vector_errors(out: Embedded, shas: Mapping[str, str]) -> None:
    ids = sorted(out.vectors)
    same: set[str] = set()
    for n, a in enumerate(ids):
        for b in ids[n + 1:]:
            if shas[a] != shas[b] and sum(
                x * y for x, y in zip(out.vectors[a], out.vectors[b], strict=True)
            ) >= SAME_VECTOR_COSINE:
                same.update((a, b))
    for i in same:
        del out.vectors[i]
        out.errors[i] = "same_vector"


def embed_media(embedder: Embedder, items: Mapping[str, tuple[Path, str]], *, dim: int,
                timeout: float, watchdog: MemoryWatchdog | None = None) -> Embedded:
    """One file per call, each hashed first: bytes that are not the labelled
    image are an ERROR and never reach the model. Different images that come
    back with the same vector are ERROR too (`SAME_VECTOR_COSINE`)."""
    out = Embedded()
    for i, (path, sha) in items.items():
        if watchdog is not None:
            watchdog.check()
        try:
            ok = path.is_file() and sha256_file(path) == sha
        except OSError:
            ok = False
        if not ok:
            out.errors[i] = "hash_mismatch"
            continue
        _embed_batch(out, [i], lambda path=path: embedder.embed_media([path]),
                     embedder.native_dim, dim, timeout)
    _same_vector_errors(out, {i: sha for i, (_, sha) in items.items()})
    return _accounted(out, list(items))


def truncated(e: Embedded, dim: int) -> Embedded:
    """`e` at `dim` (Matryoshka, renormalised): one embedding call serves
    every dimension, and an item that has no unit vector there is an ERROR."""
    out = Embedded(errors=dict(e.errors), batch_ms=e.batch_ms, item_ms=e.item_ms)
    for i, v in e.vectors.items():
        try:
            out.vectors[i] = matryoshka(v, dim)
        except EmbedError as err:
            out.errors[i] = err.kind
    return out


# --- ranking ----------------------------------------------------------------------------


def rank_dense(queries: Embedded, pool: Embedded, depth: int = DEPTH) -> Rankings:
    """Exact cosine (the vectors are unit length), so the score isolates the model."""
    out: Rankings = {}
    for qid in [*queries.vectors, *queries.errors]:
        if qid in queries.errors:
            out[qid] = Errored(queries.errors[qid])
            continue
        q = queries.vectors[qid]
        scored = sorted(
            ((sum(a * b for a, b in zip(q, v, strict=True)), pid)
             for pid, v in pool.vectors.items()),
            key=lambda s: (-s[0], s[1]),
        )
        out[qid] = [pid for _, pid in scored[:depth]]
    return out


def rrf_scores(channels: Sequence[Rankings], *, k: int = RRF_K,
               depth: int = DEPTH) -> dict[str, dict[str, float] | Errored]:
    """Per query, each pool id's sum over the channels of 1 / (k + rank),
    rank counted from 1 within each channel's top `depth`. An ERROR in any
    channel is the query's ERROR."""
    out: dict[str, dict[str, float] | Errored] = {}
    for qid in dict.fromkeys(q for ch in channels for q in ch):
        ranked = [ch.get(qid, Errored("exception")) for ch in channels]
        err = next((r for r in ranked if isinstance(r, Errored)), None)
        if err is not None:
            out[qid] = err
            continue
        score: Counter[str] = Counter()
        for r in ranked:
            for pos, pid in enumerate(r[:depth], start=1):  # type: ignore[index]
                score[pid] += 1.0 / (k + pos)
        out[qid] = dict(score)
    return out


def rrf(channels: Sequence[Rankings], *, k: int = RRF_K, depth: int = DEPTH) -> Rankings:
    out: Rankings = {}
    for qid, score in rrf_scores(channels, k=k, depth=depth).items():
        out[qid] = score if isinstance(score, Errored) else [
            pid for pid, _ in sorted(score.items(), key=lambda s: (-s[1], s[0]))][:depth]
    return out


_LATIN_OR_CJK = re.compile("[a-z0-9]+|[㐀-鿿豈-﫿]+")


def cjk_bigrams(text: str) -> list[str]:
    """BM25's CJK-aware tokens: Latin words whole, a CJK run as its character
    bigrams (a single character alone), since a whitespace tokenizer sees a
    Cantonese sentence as one token."""
    out: list[str] = []
    for m in _LATIN_OR_CJK.finditer(text.lower()):
        run = m.group(0)
        if run.isascii() or len(run) == 1:
            out.append(run)
        else:
            out.extend(run[i:i + 2] for i in range(len(run) - 1))
    return out


# --- metrics ----------------------------------------------------------------------------


def recall_at(ranked: Sequence[str], relevant: frozenset[str], k: int = 5) -> float:
    return len(relevant.intersection(ranked[:k])) / min(len(relevant), k)


def mrr_at(ranked: Sequence[str], relevant: frozenset[str], k: int = 10) -> float:
    return next((1.0 / pos for pos, pid in enumerate(ranked[:k], start=1) if pid in relevant), 0.0)


def bootstrap_ci(
    values: Sequence[float], *, n: int = 2000, seed: int = 0,
) -> tuple[float, float] | None:
    """Percentile 95% CI of the mean over queries, resampled with a fixed seed."""
    if not values:
        return None
    rng = random.Random(seed)  # noqa: S311 - a resample, not a secret
    means = sorted(statistics.fmean(rng.choices(values, k=len(values))) for _ in range(n))
    return (round(means[int(0.025 * n)], 4), round(means[int(0.975 * n) - 1], 4))


def _paired_ci(a: Mapping[str, float], b: Mapping[str, float]) -> tuple[float, float] | None:
    common = sorted(set(a) & set(b))
    return bootstrap_ci([a[q] - b[q] for q in common])


def _per_query(rankings: Rankings, labels: Mapping[str, frozenset[str]],
               metric: Callable[[Sequence[str], frozenset[str]], float]) -> dict[str, float]:
    return {q: metric(r, labels[q]) for q, r in rankings.items()
            if q in labels and not isinstance(r, Errored)}


def score(rankings: Rankings, labels: Mapping[str, frozenset[str]], *,
          pool_errors: Mapping[str, str] | None = None, subset: Iterable[str] | None = None,
          hard_negatives: Mapping[str, frozenset[str]] | None = None) -> dict[str, Any]:
    qids = list(labels) if subset is None else list(subset)
    pool_errors = pool_errors or {}
    errored = {q: rankings.get(q, Errored("exception")) for q in qids}
    errored = {q: r for q, r in errored.items() if isinstance(r, Errored)}
    scored = [q for q in qids if q not in errored]
    recall = [recall_at(rankings[q], labels[q]) for q in scored]  # type: ignore[arg-type]
    mrr = [mrr_at(rankings[q], labels[q]) for q in scored]  # type: ignore[arg-type]
    kinds = Counter(r.kind for r in errored.values()) + Counter(pool_errors.values())
    out: dict[str, Any] = {
        "n_queries": len(qids),
        "n_scored": len(scored),
        "recall5": round(statistics.fmean(recall), 4) if recall else None,
        "mrr10": round(statistics.fmean(mrr), 4) if mrr else None,
        "ci95": {"recall5": bootstrap_ci(recall), "mrr10": bootstrap_ci(mrr)},
        "errors": {"queries": len(errored), "pool": len(pool_errors), "by_kind": dict(kinds)},
        "verdict": "ERROR" if errored or pool_errors or (qids and not scored) else "OK",
    }
    if hard_negatives is not None:
        out["hard_negative_ranked_first"] = sum(
            1 for q in scored
            if rankings[q] and rankings[q][0] in hard_negatives.get(q, ())  # type: ignore[index]
        )
    return out


def first_relevant_ranks(rankings: Rankings, labels: Mapping[str, frozenset[str]],
                         ) -> dict[str, int | str | None]:
    """Per query id, the 1-based rank of its first relevant item in the arm's
    top `DEPTH`, None if none is there, `ERROR:<kind>` if the query errored:
    ids and ranks only, so a verdict that turns on a few queries names them."""
    out: dict[str, int | str | None] = {}
    for q in sorted(labels):
        r = rankings.get(q, Errored("exception"))
        if isinstance(r, Errored):
            out[q] = f"ERROR:{r.kind}"
            continue
        out[q] = next((pos for pos, pid in enumerate(r[:DEPTH], start=1) if pid in labels[q]),
                      None)
    return out


def reading_gap(
    lexical: Sequence[Rankings], labels: Mapping[str, frozenset[str]],
) -> dict[str, bool | Errored]:
    out: dict[str, bool | Errored] = {}
    for q, rel in labels.items():
        ranked = [arm.get(q, Errored("exception")) for arm in lexical]
        err = next((r for r in ranked if isinstance(r, Errored)), None)
        out[q] = err if err is not None else not any(
            mrr_at(r, rel, GAP_DEPTH) > 0 for r in ranked  # type: ignore[arg-type]
        )
    return out


# --- the host ---------------------------------------------------------------------------


def read_meminfo() -> dict[str, int]:
    out: dict[str, int] = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, _, rest = line.partition(":")
        out[key] = int(rest.split()[0])
    return out


class MemoryWatchdog:
    """Aborts the run when Nora's host runs short: MemAvailable under 3 GiB,
    swap grown by more than 1 GiB since the run started, or under 1 GiB of
    swap left (a host near the end of its swap cannot grow it by 1 GiB). A
    host it cannot read is refused, not assumed fine."""

    def __init__(self, read: Callable[[], dict[str, int]] | None = None, *,
                 min_available_kib: int = 3 * GIB_KIB, max_swap_growth_kib: int = GIB_KIB,
                 min_swap_free_kib: int = GIB_KIB) -> None:
        self._read = read or (lambda: read_meminfo())
        self._min = min_available_kib
        self._growth = max_swap_growth_kib
        self._min_swap_free = min_swap_free_kib
        self.lowest_available_kib: int | None = None
        self._swap0 = self._swap_used(self._sample())

    def _sample(self) -> dict[str, int]:
        try:
            m = self._read()
            m["MemAvailable"]
        except (OSError, KeyError, ValueError) as e:
            raise AbortedError(f"cannot read host memory: {e!r}") from e
        avail = m["MemAvailable"]
        self.lowest_available_kib = avail if self.lowest_available_kib is None else min(
            self.lowest_available_kib, avail)
        return m

    @staticmethod
    def _swap_used(m: Mapping[str, int]) -> int:
        return m.get("SwapTotal", 0) - m.get("SwapFree", 0)

    def check(self) -> None:
        m = self._sample()
        if m["MemAvailable"] < self._min:
            raise AbortedError(f"MemAvailable {m['MemAvailable']} kB is under {self._min} kB")
        if m.get("SwapTotal", 0) > 0 and m.get("SwapFree", 0) < self._min_swap_free:
            raise AbortedError(
                f"free swap {m.get('SwapFree', 0)} kB is under {self._min_swap_free} kB")
        grown = self._swap_used(m) - self._swap0
        if grown > self._growth:
            raise AbortedError(f"swap grew by {grown} kB, over {self._growth} kB")


def _quiet(cmd: list[str]) -> str | None:
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=True)  # noqa: S603
        return done.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def host_sample() -> dict[str, Any]:
    out: dict[str, Any] = {"at": datetime.now(UTC).isoformat(timespec="seconds")}
    try:
        out["loadavg"] = [round(x, 2) for x in os.getloadavg()]
        m = read_meminfo()
        out["mem_available_gib"] = round(m["MemAvailable"] / GIB_KIB, 2)
        out["swap_used_gib"] = round((m["SwapTotal"] - m["SwapFree"]) / GIB_KIB, 2)
    except (OSError, KeyError):
        out["memory"] = "unavailable"
    out["nora_container_cpu"] = _quiet(
        ["docker", "stats", "--no-stream", "--format", "{{.CPUPerc}}", "nora"])
    gpu = []
    for p in sorted(Path("/sys/class/drm").glob("card*/device/gpu_busy_percent")):
        try:
            gpu.append(int(p.read_text().strip()))
        except (OSError, ValueError):
            continue
    out["gpu_busy_percent"] = gpu or None
    return out


def _ollama_ps(base: str, model: str) -> dict[str, Any] | None:
    try:
        from evals.retrieval_bakeoff.adapters import http_json

        ps = http_json("GET", f"{base}/api/ps", None, 10)
    except Exception:  # noqa: BLE001 - provenance only
        return None
    for m in ps.get("models", []):
        if m.get("name", "").split(":")[0] == model.split(":")[0]:
            size = m.get("size") or 0
            return {"size_gib": round(size / 2**30, 2),
                    "vram_share": round((m.get("size_vram") or 0) / size, 3) if size else None}
    return None


def _commit() -> dict[str, Any]:
    head = _quiet(["git", "-C", str(SET_DIR), "rev-parse", "HEAD"])
    # `results/` is the harness's own output, not the set.
    dirty = _quiet(["git", "-C", str(SET_DIR), "status", "--porcelain", "--", ".",
                    ":(exclude)results"])
    return {"commit": head, "set_dir_clean": dirty == "" if dirty is not None else None}


def _latency(e: Embedded, batch: int = BATCH) -> dict[str, Any]:
    def p(xs: list[float], q: float) -> float | None:
        return round(sorted(xs)[min(len(xs) - 1, int(q * len(xs)))], 1) if xs else None

    return {"batch": batch, "batches": len(e.batch_ms),
            "batch_ms_p50": p(e.batch_ms, 0.5), "batch_ms_p95": p(e.batch_ms, 0.95),
            "item_ms_p50": p(e.item_ms, 0.5), "item_ms_p95": p(e.item_ms, 0.95)}


# --- the run ----------------------------------------------------------------------------


@dataclass
class ArmRun:
    rankings: Rankings
    pool_errors: dict[str, str] = field(default_factory=dict)
    cost: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)


def evaluate(the_set: BakeoffSet, arms: Mapping[str, ArmRun], *, lexical_arms: Sequence[str],
             provenance: Mapping[str, Any], reference: str | None = None) -> dict[str, Any]:
    labels = the_set.relevant
    langs = sorted({q.lang for q in the_set.queries.values()})
    by_lang = {lang: [q for q, v in the_set.queries.items() if v.lang == lang] for lang in langs}
    gap = reading_gap([arms[a].rankings for a in lexical_arms], labels)
    in_gap = [q for q, g in gap.items() if g is True]
    out_gap = [q for q, g in gap.items() if g is False]
    reference = reference or lexical_arms[0]
    ref_recall = _per_query(arms[reference].rankings, labels, recall_at)
    result_arms: dict[str, Any] = {}
    for name, arm in arms.items():
        def of(subset: Iterable[str] | None, arm: ArmRun = arm) -> dict[str, Any]:
            return score(arm.rankings, labels, pool_errors=arm.pool_errors, subset=subset)

        s = score(arm.rankings, labels, pool_errors=arm.pool_errors,
                  hard_negatives=the_set.hard_negatives)
        s["by_lang"] = {lang: of(qs) for lang, qs in by_lang.items()}
        s["in_reading_gap"] = of(in_gap)
        s["outside_reading_gap"] = of(out_gap)
        for sub, qs in the_set.subsets.items():
            s[sub] = of(qs)
            s[f"outside_{sub}"] = of([q for q in labels if q not in qs])
        s["paired_recall5_minus_reference_ci95"] = None if name == reference else _paired_ci(
            _per_query(arm.rankings, labels, recall_at), ref_recall)
        s["per_query"] = first_relevant_ranks(arm.rankings, labels)
        s["cost"] = arm.cost
        s["provenance"] = arm.provenance
        result_arms[name] = s
    gap_errors = [q for q, g in gap.items() if isinstance(g, Errored)]
    return {
        "schema": 1,
        "slice": the_set.slice,
        "set_version": the_set.version,
        "labels_lock": the_set.lock,
        "n_pool": len(the_set.pool),
        "n_queries": len(the_set.queries),
        "queries_by_lang": {lang: len(qs) for lang, qs in by_lang.items()},
        "metrics": {"recall": "recall@5 = |relevant in top 5| / min(|relevant|, 5)",
                    "mrr": "MRR@10", "ci": "bootstrap 95% percentile, 2000 resamples, seed 0; "
                    "reported, never decided on", "rrf_k": RRF_K, "depth": DEPTH},
        "reference_arm": reference,
        "reading_gap": {
            "rule": f"no arm of {list(lexical_arms)} ranks a relevant item in its top {GAP_DEPTH}",
            "n": len(in_gap),
            "by_lang": {lang: len([q for q in in_gap if q in qs]) for lang, qs in by_lang.items()},
            "queries": sorted(in_gap),
            "errors": len(gap_errors),
        },
        "subsets": {name: sorted(qs) for name, qs in the_set.subsets.items()},
        "arms": result_arms,
        "verdict": "ERROR" if gap_errors or any(
            a["verdict"] == "ERROR" for a in result_arms.values()) else "OK",
        "provenance": dict(provenance),
    }


def _trigram(dsn: str, the_set: BakeoffSet) -> ArmRun:
    items: dict[str, lexical_seed.Reading | lexical_seed.Said] = {}
    for pid in the_set.pool:
        if pid in the_set.media:
            m = the_set.media[pid]
            items[pid] = (m.sha256, m.title, m.description, m.content)
        else:
            items[pid] = (the_set.messages[pid].sent_ms, the_set.messages[pid].text)
    t0 = time.perf_counter()
    try:
        lexical_seed.load(dsn, items)
        t0 = time.perf_counter()
        ranked: Rankings = dict(lexical_seed.seed(
            dsn, {q: v.text for q, v in the_set.queries.items()}, limit=DEPTH))
    except Exception:  # noqa: BLE001 - every query is an ERROR, never a miss
        ranked = dict.fromkeys(the_set.queries, Errored("exception"))
    ms = (time.perf_counter() - t0) * 1000 / max(1, len(the_set.queries))
    return ArmRun(ranked, cost={"query_ms_mean": round(ms, 1)}, provenance={
        "arm": "SqlMemoryIndex.seed (S5) with terms_of, over nodes memory_index.project "
               "wrote, on a migrated throwaway database",
        "server": _quiet(["docker", "inspect", "--format", "{{.Config.Image}}", "bakeoff-pg"]),
    })


def _bm25(the_set: BakeoffSet) -> ArmRun:
    import bm25s  # type: ignore[import-not-found]

    corpus = [cjk_bigrams(the_set.text(pid)) for pid in the_set.pool]
    retriever = bm25s.BM25()
    retriever.index(corpus, show_progress=False)
    vocab = set(retriever.vocab_dict)
    ranked: Rankings = {}
    t0 = time.perf_counter()
    for qid, q in the_set.queries.items():
        tokens = [t for t in cjk_bigrams(q.text) if t in vocab]
        if not tokens:
            ranked[qid] = []
            continue
        docs, scores = retriever.retrieve([tokens], k=min(DEPTH, len(corpus)), show_progress=False)
        ranked[qid] = [the_set.pool[int(d)]
                       for d, s in zip(docs[0], scores[0], strict=True) if s > 0]
    ms = (time.perf_counter() - t0) * 1000 / max(1, len(the_set.queries))
    return ArmRun(ranked, cost={"query_ms_mean": round(ms, 2)}, provenance={
        "arm": "bm25s out of the database, CJK character bigrams plus Latin words",
        "bm25s": importlib.metadata.version("bm25s"), "method": retriever.method,
        "k1": retriever.k1, "b": retriever.b,
    })


def _arm_name(label: str, d: int, dims: Sequence[int], native: int) -> str:
    """`dense:bge-m3` for a model at its one native size, `dense:<label>@<d>`
    for each Matryoshka dimension of one that has several."""
    return f"dense:{label}" if tuple(dims) == (native,) else f"dense:{label}@{d}"


def _arms_at(label: str, dims: Sequence[int], native: int, docs: Embedded, queries: Embedded,
             cost: dict[str, Any], prov: dict[str, Any], over: str) -> dict[str, ArmRun]:
    arms: dict[str, ArmRun] = {}
    for d in dims:
        pool, qs = truncated(docs, d), truncated(queries, d)
        arms[_arm_name(label, d, dims, native)] = ArmRun(
            rank_dense(qs, pool), pool_errors=dict(pool.errors), cost=cost,
            provenance={**prov, "dims": d, "over": over})
    return arms


def _warm(embedder: Embedder, timeout: float, dog: MemoryWatchdog | None) -> None:
    for _ in range(WARMUPS):
        embed_texts(embedder, {"warm": "warm up"}, "query", dim=embedder.native_dim,
                    timeout=timeout, watchdog=dog)


def _text_arms(embedder: Embedder, label: str, the_set: BakeoffSet, dims: Sequence[int], *,
               timeout: float, dog: MemoryWatchdog | None, base: str) -> dict[str, ArmRun]:
    """Each pool item's text (a photo's reading, a message's text) embedded
    once as a document, each query once as a query, then every dimension."""
    _warm(embedder, timeout, dog)
    docs = embed_texts(embedder, {pid: the_set.text(pid) for pid in the_set.pool},
                       "document", dim=embedder.native_dim, timeout=timeout, watchdog=dog)
    queries = embed_texts(embedder, {q: v.text for q, v in the_set.queries.items()}, "query",
                          dim=embedder.native_dim, timeout=timeout, watchdog=dog)
    cost = {"documents": _latency(docs), "queries": _latency(queries),
            "ollama_ps": _ollama_ps(base, embedder.name)}
    over = "the frozen readings" if the_set.media else "the message text"
    return _arms_at(label, dims, embedder.native_dim, docs, queries, cost,
                    embedder.provenance(), over)


def _image_arms(embedder: Embedder, label: str, the_set: BakeoffSet, dims: Sequence[int], *,
                timeout: float, dog: MemoryWatchdog | None, base: str,
                media_dir: Path = MEDIA_DIR) -> dict[str, ArmRun]:
    """Each pool image embedded once from its bytes (hash-checked), each query
    once as a query, then every Matryoshka dimension derived from those."""
    _warm(embedder, timeout, dog)
    images = embed_media(embedder, {pid: (media_dir / f"{the_set.media[pid].sha256}.jpg",
                                          the_set.media[pid].sha256) for pid in the_set.pool},
                         dim=embedder.native_dim, timeout=timeout, watchdog=dog)
    queries = embed_texts(embedder, {q: v.text for q, v in the_set.queries.items()}, "query",
                          dim=embedder.native_dim, timeout=timeout, watchdog=dog)
    cost = {"images": _latency(images, 1), "queries": _latency(queries),
            "ollama_ps": _ollama_ps(base, embedder.name)}
    return _arms_at(label, dims, embedder.native_dim, images, queries, cost,
                    embedder.provenance(), "the images")


def _unload(model: str) -> bool:
    """`ollama stop`: one model resident at a time, Nora's host gets it back."""
    return _quiet(["ollama", "stop", model]) is not None


LEXICAL = ("lexical:trigram", "lexical:bm25")


def gemma_embedder(*, ollama_tag: str = "", hf_repo: str = "", revision: str = "",
                   dtype: str = "", base_url: str, timeout: float) -> Embedder | None:
    """Stage 2's EmbeddingGemma 2: an Ollama tag, or a pinned Hugging Face
    revision through sentence-transformers on CPU at an explicit dtype (the
    full-precision path; float16 refuses in the adapter). Never both."""
    if ollama_tag and hf_repo:
        raise ValueError("--gemma and --gemma-hf are two serving paths; pass one")
    if hf_repo:
        if not revision or not dtype:
            raise ValueError("--gemma-hf needs --gemma-revision and --gemma-dtype")
        return HFEmbedder(hf_repo, revision=revision, dtype=dtype, native_dim=768)
    if ollama_tag:
        return OllamaEmbedder(ollama_tag, native_dim=768, base_url=base_url, timeout=timeout)
    return None


#: Each official-format candidate's Matryoshka dimensions: native, and 256.
OFFICIAL_DIMS = {"qwen3vl": (2048, 256), "jina5omni": (1024, 256)}


def official_embedder(label: str, *, dtype: str) -> OfficialEmbedder | None:
    """Stage 2's Qwen3-VL-Embedding-2B or jina-embeddings-v5-omni-small, in
    the format its own files define (`adapters.OfficialFormat`), at its
    pinned commit and an explicit dtype. One per run: one model resident."""
    if not label:
        return None
    if label not in OFFICIAL:
        raise ValueError(f"--official must be one of {sorted(OFFICIAL)}, not {label!r}")
    if not dtype:
        raise ValueError("--official needs --official-dtype (float32 or bfloat16)")
    return OfficialEmbedder(OFFICIAL[label], dtype=dtype)


def _cpu_bf16_flags() -> list[str]:
    try:
        text = Path("/proc/cpuinfo").read_text()
    except OSError:
        return []
    return sorted({f for f in text.split() if f in ("avx512_bf16", "amx_bf16")})


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dsn", required=True, help="a THROWAWAY postgres (asyncpg URL)")
    ap.add_argument("--ollama", default="http://127.0.0.1:11434")
    ap.add_argument("--slices", default="text_image,cantonese,names")
    ap.add_argument("--gemma", default="", help="an Ollama tag of EmbeddingGemma 2: stage 2")
    ap.add_argument("--gemma-hf", default="", help="or its Hugging Face repo, on CPU: stage 2")
    ap.add_argument("--gemma-revision", default="", help="the HF commit, pinned")
    ap.add_argument("--gemma-dtype", default="", help="bfloat16 or float32; never float16")
    ap.add_argument("--gemma-dims", default="768,256")
    ap.add_argument("--official", default="", help=f"one of {sorted(OFFICIAL)}: stage 2")
    ap.add_argument("--official-dtype", default="float32", help="never float16")
    ap.add_argument("--media-dir", type=Path, default=MEDIA_DIR)
    ap.add_argument("--timeout", type=float, default=120.0)
    ap.add_argument("--out", type=Path, default=SET_DIR / "results")
    args = ap.parse_args(argv)

    sets = {name: load_set(SET_DIR, name) for name in args.slices.split(",")}
    gemma = gemma_embedder(ollama_tag=args.gemma, hf_repo=args.gemma_hf,
                           revision=args.gemma_revision, dtype=args.gemma_dtype,
                           base_url=args.ollama, timeout=args.timeout)
    official = official_embedder(args.official, dtype=args.official_dtype)
    if official and gemma:
        raise ValueError("one candidate per run: --official or --gemma/--gemma-hf")
    stage = f"stage2-{args.official}" if official else "stage2" if gemma else "floor"
    candidate = official or gemma
    before = host_sample()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    provenance: dict[str, Any] = {
        **_commit(), "host": platform.node(), "python": sys.version.split()[0],
        "nice": os.nice(0), "host_before": before,
        "stage": "1 (lexical) plus the bge-m3 floor"
                 + (f", and 2 (dense: {candidate.name})" if candidate else ""),
        "cpu_bf16_flags": _cpu_bf16_flags(),
    }
    arms: dict[str, dict[str, ArmRun]] = {name: {} for name in sets}
    unloaded: dict[str, bool] = {}
    try:
        dog = MemoryWatchdog()
        for name, the_set in sets.items():
            dog.check()
            arms[name]["lexical:trigram"] = _trigram(args.dsn, the_set)
            dog.check()
            arms[name]["lexical:bm25"] = _bm25(the_set)
        bge = OllamaEmbedder("bge-m3", native_dim=1024, base_url=args.ollama, timeout=args.timeout)
        for name, the_set in sets.items():
            arms[name].update(_text_arms(bge, "bge-m3", the_set, (1024,), timeout=args.timeout,
                                         dog=dog, base=args.ollama))
        unloaded[bge.name] = _unload(bge.name)
        if gemma:
            dims = tuple(int(d) for d in args.gemma_dims.split(","))
            for name, the_set in sets.items():
                build = _image_arms if the_set.media else _text_arms
                kw = {"media_dir": args.media_dir} if the_set.media else {}
                arms[name].update(build(gemma, "gemma2", the_set, dims, timeout=args.timeout,
                                        dog=dog, base=args.ollama, **kw))
            if args.gemma:
                unloaded[gemma.name] = _unload(gemma.name)
        if official:
            dims = OFFICIAL_DIMS[args.official]
            for name, the_set in sets.items():
                build = _image_arms if the_set.media else _text_arms
                kw = {"media_dir": args.media_dir} if the_set.media else {}
                arms[name].update(build(official, args.official, the_set, dims,
                                        timeout=args.timeout, dog=dog, base=args.ollama, **kw))
    except AbortedError as e:
        for m in ("bge-m3", args.gemma):
            if m:
                unloaded[m] = _unload(m)
        result = {"slices": list(sets), "verdict": "ERROR", "aborted": str(e),
                  "labels_lock": next(iter(sets.values())).lock,
                  "provenance": {**provenance, "unloaded": unloaded,
                                 "host_after": host_sample()}}
        _write(args.out / f"{stamp}-{stage}-aborted.json", result)
        print(f"ABORTED: {e}", file=sys.stderr)
        return 3
    provenance["unloaded"] = unloaded
    provenance["host_after"] = host_sample()
    lowest = (dog.lowest_available_kib or 0) / GIB_KIB
    provenance["watchdog_lowest_mem_available_gib"] = round(lowest, 2)
    # This process's high-water mark (Linux: KiB); an in-process model's cost.
    provenance["peak_rss_mib"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
    worst = 0
    for name, the_set in sets.items():
        slice_arms = arms[name]
        for dense in [a for a in slice_arms if a.startswith("dense:")]:
            for lex in LEXICAL:
                slice_arms[f"fused:{lex.split(':')[1]}+{dense.split(':', 1)[1]}"] = ArmRun(
                    rrf([slice_arms[lex].rankings, slice_arms[dense].rankings]),
                    pool_errors=slice_arms[dense].pool_errors)
        result = evaluate(the_set, slice_arms, lexical_arms=LEXICAL, provenance=provenance)
        path = args.out / f"{stamp}-{stage}-{name}.json"
        _write(path, result)
        for arm, a in result["arms"].items():
            langs = " ".join(f"{k}={v['recall5']}/{v['mrr10']}" for k, v in a["by_lang"].items())
            print(f"{name:10} {arm:28} {a['verdict']:5} r5={a['recall5']} mrr={a['mrr10']} "
                  f"{langs}")
        gap = result["reading_gap"]["n"]
        print(f"{name}: reading gap {gap}; verdict {result['verdict']}; {path}")
        worst = max(worst, 0 if result["verdict"] == "OK" else 2)
    return worst


def _write(path: Path, result: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    path.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
