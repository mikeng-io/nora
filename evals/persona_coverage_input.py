"""No-call private coverage export. Used by `python -m evals --coverage-export`.

Reads the immutable audit artifacts and the EXISTING readonly Persona/Record
stores. Never re-samples, re-labels, writes Persona or trusts a repair receipt as
current state. No model client is constructed here.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from evals.rebuilt.persona_coverage import STABLE, digest, plan, validate


def read_json(path: Path):
    return json.loads(path.read_text())


def write_private(path: Path, value: dict) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True)


async def export_input(
    *,
    inventory: Path,
    semantic: Path,
    recovery: Path,
    identity: Path | None,
    database_url: str,
    output: Path,
) -> dict:
    import hashlib

    from nora.subject.persona_coverage import read_accounts

    paths = [
        inventory / n
        for n in (
            "roster.json",
            "DENOMINATOR-DB-FROZEN.json",
            "cards.json",
            "honcho-db-snapshot.json",
            "db-populations.json",
        )
    ]
    paths += [
        semantic / n
        for n in (
            "prelabels.json",
            "comparisons.json",
            "bounded-source-search.json",
            "reference20-for-independent-review.json",
            "original-context.json",
            "original-context-extra.json",
        )
    ]
    paths += [recovery / "closure-ledger.json"]
    if identity is not None:
        paths.append(identity)
    provenance = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    roster = read_json(inventory / "roster.json")["accounts"]
    pre = read_json(semantic / "prelabels.json")
    if pre["denominator_hash"] != provenance[str(inventory / "DENOMINATOR-DB-FROZEN.json")]:
        raise ValueError("semantic prelabels refer to a different frozen denominator")
    comparisons = read_json(semantic / "comparisons.json")
    prelabels = {r["row_id"]: r for r in pre["rows"]}
    search = {
        (r["account_index"], r["row_id"]): r
        for r in read_json(semantic / "bounded-source-search.json")
    }
    contexts = read_json(semantic / "original-context.json") + read_json(
        semantic / "original-context-extra.json"
    )
    closure = read_json(recovery / "closure-ledger.json")
    original_refs = read_json(semantic / "reference20-for-independent-review.json")
    as_of_ms = int(datetime.now(UTC).timestamp() * 1000)
    accounts, fresh, all_sources, authored = await read_accounts(
        roster=roster,
        identity=identity,
        identity_hash=provenance.get(str(identity)) if identity else None,
        database_url=database_url,
    )
    canonical = fresh
    fresh = [
        [h for h in heads if h["part"] == "identity" and h.get("fact_key")] for heads in canonical
    ]
    rows = []
    for comparison in comparisons:
        idx, row_id = comparison["account_index"], comparison["row_id"]
        p, account = prelabels[row_id], accounts[idx]
        candidates = search.get((idx, row_id), {}).get("sources", [])
        sources = {int(s["row"]["id"]): s for s in candidates}
        ids = {int(c) for h in fresh[idx] for c in h["citations"] if str(c).isdigit()}
        ids.update(int(c) for c in comparison.get("cites", []) if str(c).isdigit())
        for record_id in ids:
            if record_id in all_sources[idx]:
                sources[record_id] = all_sources[idx][record_id]
        # Exact original contextual questions/answers, never all-unit history.
        wanted = set(sources)
        for c in contexts:
            if (
                c["conversation_id"] in account["rooms"]
                and c["id"] <= account["boundary"]
                and any(abs(c["id"] - i) <= 3 for i in wanted)
            ):
                sources.setdefault(c["id"], {"room": c["conversation_id"], "row": c})
        rendered = []
        for source in sources.values():
            r = source["row"]
            if source["room"] not in account["rooms"] or r["id"] > account["boundary"]:
                continue
            rendered.append(
                {
                    "id": f"record-{r['id']}",
                    "record_id": r["id"],
                    "room": source["room"],
                    "unit": account["unit"],
                    "author": r.get("person_id"),
                    "ts": r["ts"],
                    "age_ms": as_of_ms - r["ts"],
                    "body": r["body"],
                    "role": "subject_assertion"
                    if r.get("person_id") == account["person"]
                    else "context_only",
                }
            )
        frozen = {
            "row_id": row_id,
            "account_index": idx,
            "scope": account["key"],
            "kind": p["kind"],
            "prelabel": p["semantic_label"],
            "excluded": p["excluded"],
            "original": p,
        }
        rows.append(
            {
                "id": f"A{idx:02d}-{row_id}",
                "account_index": idx,
                "frozen": frozen,
                "claim": p["text"],
                "claim_origin": {
                    "kind": p["kind"],
                    "row_id": row_id,
                    "conclusion_id": p.get("conclusion_id"),
                    "snapshot_time": pre["timestamp"],
                    "prelabel_is_truth": False,
                },
                "sources": sorted(rendered, key=lambda s: (s["ts"], s["record_id"])),
                "heads": fresh[idx],
                "authored": authored[idx],
                "closure_annotations": [
                    c
                    for c in closure
                    if c["account_index"] == idx and row_id in c.get("row_ids", [])
                ],
            }
        )
    refs = []
    for reference in original_refs["rows"]:
        c = reference["comparisons"][0]
        refs.append(
            {
                "id": f"A{c['account_index']:02d}-{c['row_id']}",
                "expected": None,
                "proposed_label": "covered"
                if c["status"] == "coveredcandidate"
                else "derivable_missed"
                if c.get("confirmed_derivable_miss")
                else "source_unresolved",
                "preliminary": c,
            }
        )
    frozen = [r["frozen"] for r in rows]
    doc = {
        "schema": 1,
        "as_of": as_of_ms,
        "provenance": provenance,
        "original_denominator": read_json(inventory / "DENOMINATOR-DB-FROZEN.json"),
        "original_prelabels": pre,
        "canonical_current_heads": canonical,
        "accounts": accounts,
        "cards": read_json(inventory / "cards.json"),
        "frozen": frozen,
        "frozen_hash": digest(frozen),
        "rows": rows,
        "references": {
            "status": "PENDING_ROOT_REVIEW",
            "adjudicated_by": None,
            "provenance": provenance[str(semantic / "reference20-for-independent-review.json")],
            "rows": refs,
        },
        "models": {
            "judge_id": "nora-judge",
            "writer_id": "nora-dreamer",
            "judge_actual": None,
            "writer_actual": None,
            "route_provenance": None,
        },
        "closure_ledger": closure,
    }
    validate(doc, gold=False)
    # Specific source population acceptance: never silently export a partial audit.
    if (len(prelabels), len(rows), len([r for r in rows if r["frozen"]["prelabel"] == STABLE])) != (
        561,
        879,
        179,
    ):
        raise ValueError("frozen source population changed; expected 561/879/179")
    write_private(output, doc)
    write_private(output.with_suffix(".review20.json"), doc["references"])
    return plan(doc)
