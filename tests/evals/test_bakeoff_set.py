"""The retrieval bake-off's labelled set is whole, resolvable and locked.

`evals/retrieval_bakeoff/` holds the set a retrieval arm is scored on
(design/unified-memory/20, the rules in its PROTOCOL.md). A score is only
evidence if the set it was scored on is the set that was labelled, so every
check here fails closed: a missing file, a missing key or a malformed value is
a FAIL, never a skip. Nothing here reads an image or calls a model; the image
bytes live outside the repo and their hash check belongs to the harness.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SET_DIR = ROOT / "evals" / "retrieval_bakeoff"
LOCKED = ("set.yaml", "media.yaml", "labels.yaml")
LOCK = SET_DIR / "labels.lock"

MIN_QUERIES_PER_SLICE = 40
MIN_QUERIES_PER_LANGUAGE = 10
LANGUAGES = {"yue", "en", "mixed"}
SOURCES = {"commons", "generated"}
COMMONS_LICENCES = {"CC0", "Public domain"}
SHA256 = re.compile(r"[0-9a-f]{64}")
SHA1 = re.compile(r"[0-9a-f]{40}")
# A page id, not a File: title: titles carry photographers' names, and the
# protocol copies no credits. `commons_sha1` pins which upload it was.
COMMONS_URL = re.compile(r"https://commons\.wikimedia\.org/\?curid=[0-9]+")


def _load(name: str) -> dict:
    path = SET_DIR / name
    assert path.is_file(), f"{path} is missing"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{name} is not a mapping"
    return data


@pytest.fixture(scope="module")
def the_set() -> dict:
    return _load("set.yaml")


@pytest.fixture(scope="module")
def media() -> dict:
    return _load("media.yaml")


@pytest.fixture(scope="module")
def labels() -> dict:
    return _load("labels.yaml")


def _slices(the_set: dict) -> dict:
    slices = the_set.get("slices")
    assert isinstance(slices, dict) and slices, "set.yaml has no slices"
    return slices


def _queries(the_set: dict) -> dict[str, tuple[str, dict]]:
    """query id -> (slice name, query). Duplicate ids are a FAIL."""
    out: dict[str, tuple[str, dict]] = {}
    for name, sl in _slices(the_set).items():
        for q in sl.get("queries") or []:
            qid = q.get("id")
            assert isinstance(qid, str) and qid, f"{name}: a query has no id"
            assert qid not in out, f"query id {qid} appears twice"
            out[qid] = (name, q)
    return out


def test_the_lock_matches_every_locked_file() -> None:
    assert LOCK.is_file(), "labels.lock is missing"
    entries: dict[str, str] = {}
    for line in LOCK.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, _, name = line.partition("  ")
        assert SHA256.fullmatch(digest), f"bad lock line: {line!r}"
        entries[name.strip()] = digest
    assert set(entries) == set(LOCKED), f"lock names {sorted(entries)}, expected {LOCKED}"
    for name in LOCKED:
        actual = hashlib.sha256((SET_DIR / name).read_bytes()).hexdigest()
        assert entries[name] == actual, (
            f"{name} changed since it was locked; labels change only in a new "
            "commit with a new lock"
        )


def test_every_slice_has_at_least_forty_queries(the_set: dict) -> None:
    for name, sl in _slices(the_set).items():
        n = len(sl.get("queries") or [])
        assert n >= MIN_QUERIES_PER_SLICE, f"{name} has {n} queries"


def test_every_query_is_written_and_carries_a_known_language(the_set: dict) -> None:
    for qid, (_name, q) in _queries(the_set).items():
        assert isinstance(q.get("text"), str) and q["text"].strip(), f"{qid} has no text"
        assert q.get("lang") in LANGUAGES, f"{qid}: lang {q.get('lang')!r}"


def test_every_language_is_represented_in_every_slice(the_set: dict) -> None:
    by_slice: dict[str, Counter] = {}
    for name, q in _queries(the_set).values():
        by_slice.setdefault(name, Counter())[q["lang"]] += 1
    for name, counts in by_slice.items():
        for lang in LANGUAGES:
            assert counts[lang] >= MIN_QUERIES_PER_LANGUAGE, (
                f"{name}: {counts[lang]} {lang} queries"
            )


def test_every_pool_id_is_a_media_item_with_provenance(the_set: dict, media: dict) -> None:
    items = media.get("items")
    assert isinstance(items, dict) and items, "media.yaml has no items"
    hashes = Counter(it.get("sha256") for it in items.values())
    for name, sl in _slices(the_set).items():
        pool = sl.get("pool") or []
        assert pool, f"{name} has an empty pool"
        assert len(pool) == len(set(pool)), f"{name}: duplicate pool ids"
        for iid in pool:
            it = items.get(iid)
            assert isinstance(it, dict), f"{name}: pool id {iid} is not in media.yaml"
            sha = it.get("sha256")
            assert isinstance(sha, str) and SHA256.fullmatch(sha), f"{iid}: bad sha256"
            assert hashes[sha] == 1, f"{iid}: sha256 shared with another item"
            assert it.get("source") in SOURCES, f"{iid}: source {it.get('source')!r}"
            if it["source"] == "commons":
                assert it.get("licence") in COMMONS_LICENCES, f"{iid}: licence"
                assert COMMONS_URL.fullmatch(str(it.get("source_url", ""))), f"{iid}: source_url"
                assert SHA1.fullmatch(str(it.get("commons_sha1", ""))), f"{iid}: commons_sha1"
            else:
                assert it.get("generator") and it.get("prompt"), f"{iid}: generator/prompt"
            reading = it.get("reading")
            assert isinstance(reading, dict), f"{iid}: no frozen reading"
            assert (reading.get("title") or reading.get("description")), (
                f"{iid}: the frozen reading is empty"
            )


def test_every_query_has_labels_and_every_label_resolves(the_set: dict, labels: dict) -> None:
    queries = _queries(the_set)
    pools = {name: set(sl.get("pool") or []) for name, sl in _slices(the_set).items()}
    by_query = labels.get("queries")
    assert isinstance(by_query, dict), "labels.yaml has no queries"
    assert set(by_query) == set(queries), (
        f"labelled {sorted(set(by_query) ^ set(queries))} differ from the set"
    )
    for qid, (name, _q) in queries.items():
        lab = by_query[qid]
        relevant = lab.get("relevant") or []
        hard = lab.get("hard_negatives") or []
        assert relevant, f"{qid} has no relevant item"
        assert len(relevant) == len(set(relevant)), f"{qid}: duplicate relevant ids"
        for iid in [*relevant, *hard]:
            assert iid in pools[name], f"{qid}: label {iid} is not in the {name} pool"
        assert not set(relevant) & set(hard), f"{qid}: an item is relevant AND a hard negative"


def test_both_labellers_raw_sets_are_kept(the_set: dict, labels: dict) -> None:
    pools = {name: set(sl.get("pool") or []) for name, sl in _slices(the_set).items()}
    for qid, (name, _q) in _queries(the_set).items():
        raw = labels["queries"][qid].get("raw")
        assert isinstance(raw, dict) and set(raw) == {"A", "B"}, f"{qid}: raw labels"
        for who, ids in raw.items():
            assert isinstance(ids, list), f"{qid}: raw {who} is not a list"
            assert set(ids) <= pools[name], f"{qid}: raw {who} names a non-pool id"


def test_every_disagreement_is_settled_with_a_reason(the_set: dict, labels: dict) -> None:
    queries = _queries(the_set)
    logged = {}
    for d in labels.get("disagreements") or []:
        key = (d.get("query"), d.get("item"))
        assert key not in logged, f"disagreement {key} logged twice"
        assert d.get("outcome") in {"relevant", "not_relevant"}, f"{key}: outcome"
        assert str(d.get("reason", "")).strip(), f"{key}: no reason"
        logged[key] = d["outcome"]
    for qid in queries:
        lab = labels["queries"][qid]
        a, b = set(lab["raw"]["A"]), set(lab["raw"]["B"])
        final = set(lab["relevant"])
        for iid in a ^ b:
            assert (qid, iid) in logged, f"{qid}/{iid}: A and B differ and nothing settles it"
            assert (logged[(qid, iid)] == "relevant") == (iid in final), (
                f"{qid}/{iid}: the settlement and the final label disagree"
            )
        for iid in a & b:
            assert iid in final, f"{qid}/{iid}: both labellers agree but the label dropped it"
        assert final <= a | b, f"{qid}: a final label neither labeller gave"


def test_every_media_item_is_in_a_pool(the_set: dict, media: dict) -> None:
    """A manifest entry no slice uses is an image nobody labelled against:
    an arm would embed it and the score would not know it was there."""
    pooled = {iid for sl in _slices(the_set).values() for iid in sl.get("pool") or []}
    extra = set(media.get("items") or {}) - pooled
    assert not extra, f"media.yaml items in no pool: {sorted(extra)}"


def test_every_query_has_a_hard_negative_or_says_why_not(the_set: dict, labels: dict) -> None:
    """Clearing the hard-negative lists must not pass: the harness reports how
    often an arm ranks one first, and an empty list makes that silently 0."""
    for qid in _queries(the_set):
        lab = labels["queries"][qid]
        if lab.get("hard_negatives"):
            continue
        assert str(lab.get("no_hard_negative", "")).strip(), (
            f"{qid}: no hard negative and no reason why the pool has none"
        )


def raw_digest(raw_sets: dict[str, list[str]]) -> str:
    """The serialisation PROTOCOL.md documents for `raw_sha256`: one labeller's
    raw relevant sets over EVERY query it labelled (withdrawn ones included),
    ids sorted, keys sorted, compact JSON, UTF-8."""
    canon = {q: sorted(ids) for q, ids in raw_sets.items()}
    blob = json.dumps(canon, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def test_each_labellers_raw_sha256_recomputes_from_the_committed_raw_sets(labels: dict) -> None:
    raw: dict[str, dict[str, list[str]]] = {"A": {}, "B": {}}
    for qid, lab in (labels.get("queries") or {}).items():
        for who in raw:
            raw[who][qid] = lab["raw"][who]
    for w in labels.get("withdrawn") or []:
        for who in raw:
            raw[who][w["query"]] = w["raw"][who]
    for who, sets in raw.items():
        declared = labels["labellers"][who].get("raw_sha256")
        assert declared == raw_digest(sets), f"labeller {who}: raw_sha256 does not recompute"
