"""Finite generated HTTP replies for the frozen coverage CLI controls."""

import json
import os
from pathlib import Path

from evals.rebuilt import profile_coverage as coverage
from nora.subject.obtain import _load_config


def services_file(tmp_path, doc, *, disagree=0, rendering=False):
    cfg = _load_config(Path(os.environ["NORA_CONFIG"]), require_secrets=False)
    bindings = {"llm.base_url": "judge"}
    bindings.update({f"providers.{name}.base_url": "judge" for name in cfg.providers})
    by_id = {row["id"]: row for row in doc["rows"]}
    refs = [by_id[ref["id"]] for ref in doc["references"]["rows"]]
    batches = coverage.batches(refs)
    if disagree <= 2:
        batches += coverage.batches(coverage.stable_rows(doc))
    rows = []
    for index, batch in enumerate([] if rendering else batches):
        labels = []
        for row in batch:
            labels.append({"id": row["id"],
                "label": "source_unresolved" if index < disagree else "covered",
                "evidence_ids": [row["heads"][0]["id"], row["sources"][0]["id"]],
                "why": "Generated protocol label"})
        request = {"model": cfg.endpoint("eval_judge", "nora-judge")[2],
            "messages": [{"role": "user", "content": coverage.prompt(doc, batch)}],
            "temperature": 0, "max_tokens": 4096, "reasoning_effort": "low"}
        rows.append({"method": "POST", "path": "/v1/chat/completions", "request_json": request,
            "response": {"status": 200, "json": {"id": f"generated-coverage-{index}",
                "object": "chat.completion", "created": 1, "model": "judge-physical",
                "choices": [{"index": 0, "message": {"role": "assistant",
                    "content": json.dumps(labels)}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 40, "completion_tokens": 20, "total_tokens": 60}}}})
    path = tmp_path / "services.json"
    path.write_text(json.dumps({"version": 1, "corpora": {"profile_coverage": {
        "frozen_coverage": {"version": 1, "bindings": bindings, "services": {"judge": rows}}}}}))
    return str(path)
