"""The retrieval bake-off's labelled set is whole, resolvable and locked.

`evals/retrieval_bakeoff/` holds the set a retrieval arm is scored on
(the rules are in its PROTOCOL.md). A score is only evidence if the set it was
scored on is the set that was labelled, so every check here fails closed: a
missing file, a missing key or a malformed value is a FAIL, never a skip.
Nothing here reads an image or calls a model; the image bytes live outside the
repo and their hash check belongs to the harness.

Each slice names where its items live (`media.yaml` for images, `texts.yaml`
for messages) and which labels file judges it (`labels.yaml` for
`text_image`, `labels_text.yaml` for `cantonese` and `names`, which share
their labellers). Every per-query check runs against the slice's own pool and
its own labels file.
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
LOCKED = ("set.yaml", "media.yaml", "texts.yaml", "labels.yaml", "labels_text.yaml")
LOCK = SET_DIR / "labels.lock"

# The three journey slices the decision rule reads. A set missing one cannot decide anything.
JOURNEY_SLICES = {"text_image", "cantonese", "names"}
MEDIA_ITEMS, TEXT_ITEMS = "media.yaml", "texts.yaml"
ITEM_FILES = {MEDIA_ITEMS, TEXT_ITEMS}
LABEL_FILES = {"labels.yaml", "labels_text.yaml"}

MIN_QUERIES_PER_SLICE = 40
MIN_QUERIES_PER_LANGUAGE = 10
REQUIRED_LANGUAGES = {"yue", "en", "mixed"}
# Romanised Cantonese is a text-slice language (PROTOCOL.md, text slices).
LANGUAGES_BY_ITEMS = {
    MEDIA_ITEMS: REQUIRED_LANGUAGES,
    TEXT_ITEMS: REQUIRED_LANGUAGES | {"roman"},
}
SOURCES = {"commons", "generated"}
COMMONS_LICENCES = {"CC0", "Public domain"}
SHA256 = re.compile(r"[0-9a-f]{64}")
SHA1 = re.compile(r"[0-9a-f]{40}")
# A page id, not a File: title: titles carry photographers' names, and the
# protocol copies no credits. `commons_sha1` pins which upload it was.
COMMONS_URL = re.compile(r"https://commons\.wikimedia\.org/\?curid=[0-9]+")
SENT = re.compile(r"20[0-9]{2}-[01][0-9]-[0-3][0-9] [0-2][0-9]:[0-5][0-9]")
AS_OF = re.compile(r"20[0-9]{2}-[01][0-9]-[0-3][0-9]")


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
    return _load(MEDIA_ITEMS)


@pytest.fixture(scope="module")
def texts() -> dict:
    return _load(TEXT_ITEMS)


@pytest.fixture(scope="module")
def label_files() -> dict[str, dict]:
    return {name: _load(name) for name in sorted(LABEL_FILES)}


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


def _pools(the_set: dict) -> dict[str, set[str]]:
    return {name: set(sl.get("pool") or []) for name, sl in _slices(the_set).items()}


def _items_file(sl: dict, name: str) -> str:
    items = sl.get("items")
    assert items in ITEM_FILES, f"{name}: items {items!r} is not one of {sorted(ITEM_FILES)}"
    return items


def _labels_file(sl: dict, name: str) -> str:
    labels = sl.get("labels")
    assert labels in LABEL_FILES, f"{name}: labels {labels!r} is not one of {sorted(LABEL_FILES)}"
    return labels


def _labelled(the_set: dict, label_files: dict[str, dict]) -> dict[str, tuple[str, dict, dict]]:
    """query id -> (slice name, the query's label entry, its labels file)."""
    out: dict[str, tuple[str, dict, dict]] = {}
    slices = _slices(the_set)
    for qid, (name, _q) in _queries(the_set).items():
        lf = label_files[_labels_file(slices[name], name)]
        by_query = lf.get("queries")
        assert isinstance(by_query, dict), "a labels file has no queries"
        assert qid in by_query, f"{qid} has no labels"
        out[qid] = (name, by_query[qid], lf)
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
        path = SET_DIR / name
        assert path.is_file(), f"{path} is locked but missing"
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        assert entries[name] == actual, (
            f"{name} changed since it was locked; labels change only in a new "
            "commit with a new lock"
        )


def test_every_journey_slice_is_in_the_set(the_set: dict) -> None:
    missing = JOURNEY_SLICES - set(_slices(the_set))
    assert not missing, f"set.yaml lacks the journey slices {sorted(missing)}"


def test_every_slice_names_its_items_and_labels_files(the_set: dict) -> None:
    named = set()
    for name, sl in _slices(the_set).items():
        _items_file(sl, name)
        named.add(_labels_file(sl, name))
    assert named == LABEL_FILES, f"labels files no slice names: {sorted(LABEL_FILES - named)}"


def test_every_slice_has_at_least_forty_queries(the_set: dict) -> None:
    for name, sl in _slices(the_set).items():
        n = len(sl.get("queries") or [])
        assert n >= MIN_QUERIES_PER_SLICE, f"{name} has {n} queries"


def test_every_query_is_written_and_carries_a_known_language(the_set: dict) -> None:
    slices = _slices(the_set)
    for qid, (name, q) in _queries(the_set).items():
        allowed = LANGUAGES_BY_ITEMS[_items_file(slices[name], name)]
        assert isinstance(q.get("text"), str) and q["text"].strip(), f"{qid} has no text"
        assert q.get("lang") in allowed, f"{qid}: lang {q.get('lang')!r}"


def test_every_language_is_represented_in_every_slice(the_set: dict) -> None:
    by_slice: dict[str, Counter] = {name: Counter() for name in _slices(the_set)}
    for name, q in _queries(the_set).values():
        by_slice[name][q["lang"]] += 1
    for name, counts in by_slice.items():
        for lang in REQUIRED_LANGUAGES:
            assert counts[lang] >= MIN_QUERIES_PER_LANGUAGE, (
                f"{name}: {counts[lang]} {lang} queries"
            )


def test_every_media_pool_id_is_a_media_item_with_provenance(the_set: dict, media: dict) -> None:
    items = media.get("items")
    assert isinstance(items, dict) and items, "media.yaml has no items"
    hashes = Counter(it.get("sha256") for it in items.values())
    media_slices = {
        name: sl for name, sl in _slices(the_set).items() if _items_file(sl, name) == MEDIA_ITEMS
    }
    assert media_slices, "no slice draws on media.yaml"
    for name, sl in media_slices.items():
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


def test_every_text_pool_id_is_a_sent_message_and_every_text_slice_is_dated(
    the_set: dict, texts: dict
) -> None:
    """A time constraint ("this week") is judged by `sent` against `as_of`
    (PROTOCOL.md, text slices): a message without a time, or a slice without
    the moment its queries are asked, leaves that judgement ungrounded."""
    items = texts.get("items")
    assert isinstance(items, dict) and items, "texts.yaml has no items"
    bodies = Counter(str(it.get("text", "")).strip() for it in items.values())
    text_slices = {
        name: sl for name, sl in _slices(the_set).items() if _items_file(sl, name) == TEXT_ITEMS
    }
    assert text_slices, "no slice draws on texts.yaml"
    for name, sl in text_slices.items():
        assert AS_OF.fullmatch(str(sl.get("as_of", ""))), f"{name}: as_of {sl.get('as_of')!r}"
        pool = sl.get("pool") or []
        assert pool, f"{name} has an empty pool"
        assert len(pool) == len(set(pool)), f"{name}: duplicate pool ids"
        for iid in pool:
            it = items.get(iid)
            assert isinstance(it, dict), f"{name}: pool id {iid} is not in texts.yaml"
            body = str(it.get("text", "")).strip()
            assert body, f"{iid}: no text"
            assert bodies[body] == 1, f"{iid}: its text is an exact duplicate of another item"
            assert SENT.fullmatch(str(it.get("sent", ""))), f"{iid}: sent {it.get('sent')!r}"
            assert str(it["sent"])[:10] <= str(sl["as_of"]), f"{iid}: sent after {name}'s as_of"
            assert it.get("lang") in LANGUAGES_BY_ITEMS[TEXT_ITEMS], f"{iid}: lang"


def test_every_labels_file_covers_exactly_its_slices_queries(
    the_set: dict, label_files: dict[str, dict]
) -> None:
    slices = _slices(the_set)
    for fname, lf in label_files.items():
        by_query = lf.get("queries")
        assert isinstance(by_query, dict), f"{fname} has no queries"
        expected = {
            qid
            for qid, (name, _q) in _queries(the_set).items()
            if _labels_file(slices[name], name) == fname
        }
        assert set(by_query) == expected, (
            f"{fname}: labelled {sorted(set(by_query) ^ expected)} differ from the set"
        )


def test_every_query_has_labels_and_every_label_resolves(
    the_set: dict, label_files: dict[str, dict]
) -> None:
    pools = _pools(the_set)
    for qid, (name, lab, _lf) in _labelled(the_set, label_files).items():
        relevant = lab.get("relevant") or []
        hard = lab.get("hard_negatives") or []
        assert relevant, f"{qid} has no relevant item"
        assert len(relevant) == len(set(relevant)), f"{qid}: duplicate relevant ids"
        for iid in [*relevant, *hard]:
            assert iid in pools[name], f"{qid}: label {iid} is not in the {name} pool"
        assert not set(relevant) & set(hard), f"{qid}: an item is relevant AND a hard negative"


def test_both_labellers_raw_sets_are_kept(the_set: dict, label_files: dict[str, dict]) -> None:
    pools = _pools(the_set)
    for qid, (name, lab, _lf) in _labelled(the_set, label_files).items():
        raw = lab.get("raw")
        assert isinstance(raw, dict) and set(raw) == {"A", "B"}, f"{qid}: raw labels"
        for who, ids in raw.items():
            assert isinstance(ids, list), f"{qid}: raw {who} is not a list"
            assert set(ids) <= pools[name], f"{qid}: raw {who} names a non-pool id"


def test_every_disagreement_is_settled_with_a_reason(
    the_set: dict, label_files: dict[str, dict]
) -> None:
    logged_by_file: dict[int, dict] = {}
    for lf in label_files.values():
        logged: dict = {}
        for d in lf.get("disagreements") or []:
            key = (d.get("query"), d.get("item"))
            assert key not in logged, f"disagreement {key} logged twice"
            assert d.get("outcome") in {"relevant", "not_relevant"}, f"{key}: outcome"
            assert str(d.get("reason", "")).strip(), f"{key}: no reason"
            logged[key] = d["outcome"]
        logged_by_file[id(lf)] = logged
    for qid, (_name, lab, lf) in _labelled(the_set, label_files).items():
        logged = logged_by_file[id(lf)]
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


def test_every_text_item_is_in_a_pool(the_set: dict, texts: dict) -> None:
    """The same for messages: an unpooled message is one nobody labelled."""
    pooled = {iid for sl in _slices(the_set).values() for iid in sl.get("pool") or []}
    extra = set(texts.get("items") or {}) - pooled
    assert not extra, f"texts.yaml items in no pool: {sorted(extra)}"


def test_every_query_has_a_hard_negative_or_says_why_not(
    the_set: dict, label_files: dict[str, dict]
) -> None:
    """Clearing the hard-negative lists must not pass: the harness reports how
    often an arm ranks one first, and an empty list makes that silently 0."""
    for qid, (_name, lab, _lf) in _labelled(the_set, label_files).items():
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


def test_each_labellers_raw_sha256_recomputes_from_the_committed_raw_sets(
    label_files: dict[str, dict],
) -> None:
    for fname, lf in label_files.items():
        raw: dict[str, dict[str, list[str]]] = {"A": {}, "B": {}}
        for qid, lab in (lf.get("queries") or {}).items():
            for who in raw:
                raw[who][qid] = lab["raw"][who]
        for w in lf.get("withdrawn") or []:
            for who in raw:
                raw[who][w["query"]] = w["raw"][who]
        for who, sets in raw.items():
            declared = lf["labellers"][who].get("raw_sha256")
            assert declared == raw_digest(sets), (
                f"{fname}: labeller {who}: raw_sha256 does not recompute"
            )


def test_no_withdrawn_query_is_back_in_the_set(the_set: dict, label_files: dict[str, dict]) -> None:
    """A withdrawn query keeps its raw labels for the digest; if it returns
    to the set it would be scored on labels its withdrawal said cannot hold."""
    live = set(_queries(the_set))
    for fname, lf in label_files.items():
        for w in lf.get("withdrawn") or []:
            qid = w.get("query")
            assert qid not in live, f"{fname}: withdrawn query {qid} is back in the set"
            labelled = lf.get("queries") or {}
            assert qid not in labelled, f"{fname}: withdrawn query {qid} is labelled"
            assert str(w.get("reason", "")).strip(), f"{fname}: {qid} withdrawn with no reason"


def test_every_hard_negative_fail_names_a_hard_negative(
    the_set: dict, label_files: dict[str, dict]
) -> None:
    for qid, (_name, lab, _lf) in _labelled(the_set, label_files).items():
        extra = set(lab.get("hard_negative_fails") or {}) - set(lab.get("hard_negatives") or [])
        assert not extra, f"{qid}: hard_negative_fails names non-hard-negatives {sorted(extra)}"


def test_a_text_slices_as_of_is_its_labels_files_as_of(
    the_set: dict, label_files: dict[str, dict]
) -> None:
    """Labellers judged "this week" against the labels file's as_of; a set
    that asks at another moment changes what every time constraint means."""
    for name, sl in _slices(the_set).items():
        lf = label_files[_labels_file(sl, name)]
        if _items_file(sl, name) != TEXT_ITEMS and "as_of" not in lf:
            continue
        assert str(sl.get("as_of")) == str(lf.get("as_of")), (
            f"{name}: as_of {sl.get('as_of')!r} differs from its labels file's {lf.get('as_of')!r}"
        )


def test_no_two_queries_share_a_text(the_set: dict) -> None:
    seen: dict[str, str] = {}
    for qid, (_name, q) in _queries(the_set).items():
        body = str(q.get("text", "")).strip()
        assert body not in seen, f"{qid} repeats the text of {seen.get(body)}"
        seen[body] = qid


def agreement(raw_a: dict[str, list[str]], raw_b: dict[str, list[str]], pool_size: int) -> dict:
    """The agreement block PROTOCOL.md reports before reconciliation, from
    the two raw sets: identical sets, mean Jaccard (1.0 when both are
    empty), Cohen's kappa over every query-item pair, and the pair counts."""
    qs = sorted(raw_a)
    ident, jac, both, a_only, b_only = 0, [], 0, 0, 0
    for q in qs:
        a, b = set(raw_a[q]), set(raw_b[q])
        ident += a == b
        jac.append(1.0 if not a | b else len(a & b) / len(a | b))
        both, a_only, b_only = both + len(a & b), a_only + len(a - b), b_only + len(b - a)
    n = len(qs) * pool_size
    p_o = (n - a_only - b_only) / n
    p_a, p_b = (both + a_only) / n, (both + b_only) / n
    p_e = p_a * p_b + (1 - p_a) * (1 - p_b)
    return {
        "queries": len(qs),
        "identical_sets": ident,
        "mean_jaccard": round(sum(jac) / len(jac), 3),
        "cohen_kappa_over_query_item_pairs": round((p_o - p_e) / (1 - p_e), 3),
        "query_item_pairs": n,
        "relevant_to_both": both,
        "relevant_to_A_only": a_only,
        "relevant_to_B_only": b_only,
    }


def test_the_reported_agreement_recomputes_from_the_raw_sets(
    the_set: dict, label_files: dict[str, dict]
) -> None:
    """Agreement is reported over every query a labeller judged, withdrawn
    ones included, per slice when the file holds several slices."""
    slices = _slices(the_set)
    slice_of = {qid: name for qid, (name, _q) in _queries(the_set).items()}
    for fname, lf in label_files.items():
        mine = sorted(n for n, sl in slices.items() if _labels_file(sl, n) == fname)
        raw: dict[str, dict[str, dict[str, list[str]]]] = {n: {"A": {}, "B": {}} for n in mine}
        for qid, lab in (lf.get("queries") or {}).items():
            for who in ("A", "B"):
                raw[slice_of[qid]][who][qid] = lab["raw"][who]
        for w in lf.get("withdrawn") or []:
            name = w.get("slice") or (mine[0] if len(mine) == 1 else None)
            assert name in raw, f"{fname}: withdrawn {w.get('query')} names no slice of this file"
            for who in ("A", "B"):
                raw[name][who][w["query"]] = w["raw"][who]
        reported = lf.get("agreement_before_reconciliation")
        assert isinstance(reported, dict), f"{fname}: no agreement_before_reconciliation"
        if len(mine) == 1:
            reported = {mine[0]: reported}
        assert set(reported) == set(mine), f"{fname}: agreement reported for {sorted(reported)}"
        for name in mine:
            got = agreement(raw[name]["A"], raw[name]["B"], len(slices[name]["pool"]))
            assert reported[name] == got, f"{fname}/{name}: reported {reported[name]} != {got}"


def test_every_time_only_query_is_live_and_names_its_hard_negative(
    the_set: dict, label_files: dict[str, dict]
) -> None:
    labelled = _labelled(the_set, label_files)
    for fname, lf in label_files.items():
        for qid, ids in (lf.get("time_only_queries") or {}).items():
            assert qid in labelled, f"{fname}: time_only query {qid} is not a live query"
            hard = set(labelled[qid][1].get("hard_negatives") or [])
            assert ids and set(ids) <= hard, (
                f"{fname}: {qid} time_only ids are not its hard negatives"
            )
