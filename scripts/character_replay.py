"""Replay Character sessions under THIS checkout's objectives, for a before/after
of an objective change.

Each input is one session's user message (what `inputs()` rendered: recorded
from Langfuse, or rebuilt by the production adaptor against the live database),
with the adaptor that ran it. The system prompt is this checkout's
`DreamerInstructions.for_objective(...)`, so running the script from two
worktrees is the two arms, one variable each. The model is called raw with the
two write tools (`character_revise`, `character_no_change`); a read tool a
person session holds answers `unavailable` (the replay does not reach memory).
Writes are captured, never stored. Output: `[{"key", "kind", "body"}]`, the
shape `character_synthesis_measure.py` reads; a session that closed unchanged or
wrote nothing is listed with an empty body and counted.

Inputs are private chat data: keep them and the output under /tmp, never in git.

    uv run python scripts/character_replay.py /tmp/inputs.json /tmp/arm.json --model nora-dreamer
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from openai import AsyncOpenAI

from nora.secret import credentials

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.objectives import shipped_objectives

OBJECTIVE = {
    "dream:culture": "culture", "dream:character-person": "character-person",
    "dream:character-nora": "character-nora", "dream:character-unit": "character-unit",
}
KIND = {"dream:culture": "room", "dream:character-person": "person",
        "dream:character-nora": "nora", "dream:character-unit": "unit"}

TOOLS = [
    {"type": "function", "function": {
        "name": "character_revise",
        "description": ("Write the new account of an entity of this session. "
                        "Give the WHOLE account."),
        "parameters": {"type": "object", "required": ["entity", "body", "citations"],
                       "properties": {
            "entity": {"type": "string"}, "body": {"type": "string"},
            "citations": {"type": "array", "items": {"type": "string"}}}}}},
    {"type": "function", "function": {
        "name": "character_no_change",
        "description": "Close an entity of this session with its account unchanged.",
        "parameters": {"type": "object", "required": ["entity", "why"], "properties": {
            "entity": {"type": "string"}, "why": {"type": "string"}}}}},
]
READS = ("ask_memory", "search_memory", "about", "lookup_person", "group_members")


async def replay(client: AsyncOpenAI, model: str, effort: str, system: str, user: str,
                 person: bool) -> tuple[str, str]:
    tools = list(TOOLS) + ([{"type": "function", "function": {
        "name": n, "description": "A read tool (not available in this replay).",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": True}}}
        for n in READS] if person else [])
    messages: list[dict] = [
        {"role": "system", "content": system}, {"role": "user", "content": user},
    ]
    body, outcome = "", "nothing"
    for _ in range(8):
        r = await client.chat.completions.create(
            model=model, messages=messages, tools=tools, max_tokens=8192,
            reasoning_effort=effort,  # type: ignore[arg-type]
        )
        msg = r.choices[0].message
        calls = msg.tool_calls or []
        if not calls:
            break
        messages.append({"role": "assistant", "content": msg.content or "",
                         "tool_calls": [c.model_dump() for c in calls]})
        for c in calls:
            if c.function.name == "character_revise":
                try:
                    body = str(json.loads(c.function.arguments).get("body") or "")
                    outcome = "revised"
                except ValueError:
                    pass
                answer = {"status": "ok"}
            elif c.function.name == "character_no_change":
                outcome = "kept" if outcome != "revised" else outcome
                answer = {"status": "ok"}
            else:
                answer = {"status": "unavailable", "note": "not available in this replay"}
            messages.append({"role": "tool", "tool_call_id": c.id, "content": json.dumps(answer)})
        if outcome in ("revised", "kept"):
            break
    return outcome, body


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", type=Path)
    ap.add_argument("output", type=Path)
    ap.add_argument("--model", default="nora-dreamer")
    ap.add_argument("--effort", default="low")
    ap.add_argument("--config", type=Path, help="use configured provider and purpose route")
    ap.add_argument("--base-url", default="http://127.0.0.1:20128/v1")
    args = ap.parse_args()
    key = credentials().get("OMNIROUTE_API_KEY", "")
    if args.config is not None:
        from nora.config import load
        cfg = load(args.config, require_secrets=False)
        args.base_url, key, args.model = cfg.endpoint("dreamer", args.model)
    if not key:
        print("selected model credential is missing or empty", file=sys.stderr)
        return 2
    store = shipped_objectives()
    client = AsyncOpenAI(base_url=args.base_url, api_key=key, max_retries=1, timeout=300)
    items = json.loads(args.inputs.read_text(encoding="utf-8"))
    sem = asyncio.Semaphore(6)

    async def one(i: int, it: dict) -> dict:
        system = store.for_objective(OBJECTIVE[it["adaptor"]]).text
        async with sem:
            try:
                outcome, body = await replay(client, args.model, args.effort, system, it["user"],
                                             it["adaptor"] == "dream:character-person")
            except Exception as exc:  # noqa: BLE001 - reported, never counted as a body
                outcome, body = f"error:{type(exc).__name__}", ""
        return {"key": it.get("key") or f"{KIND[it['adaptor']]}-{i}", "kind": KIND[it["adaptor"]],
                "outcome": outcome, "body": body}

    got = await asyncio.gather(*(one(i, it) for i, it in enumerate(items)))
    args.output.write_text(json.dumps(got, ensure_ascii=False), encoding="utf-8")
    from collections import Counter
    print(Counter((g["kind"], g["outcome"]) for g in got))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
