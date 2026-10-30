"""Experiment-only exposure arms for the EXISTING tag replay.

Patch only while a selected eval corpus runs. Runtime never imports this module.
Retrieval, mandatory blocks, transcript handles and tool admission stay intact.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import httpx

from nora.agent import prefill
from nora.agent.room import Room
from nora.domain.blocks import ContextBlock, assemble
from nora.domain.models import TagContext
from nora.obs.tracing import annotate
from nora.subject.context_selector_render import (
    OPTIONAL,
    SYSTEM,
    selector_prompt,
)

_CASE: ContextVar[tuple[str, list[dict]] | None] = ContextVar("context_case", default=None)


@dataclass(frozen=True)
class ContextPlan:
    blocks: frozenset[str]
    transcript_window: int


@contextmanager
def case_scope(name: str) -> Iterator[None]:
    """Bind receipts to the actual replay draw, including its complete turn time."""
    rows: list[dict] = []
    token = _CASE.set((name, rows))
    start = time.perf_counter()
    try:
        yield
    finally:
        for row in rows:
            row["turn_seconds"] = time.perf_counter() - start
        _CASE.reset(token)


def parse_plan(content: str, window: int) -> ContextPlan:
    data = json.loads(content)
    if not isinstance(data, dict) or set(data) != {"blocks", "transcript_window"}:
        raise ValueError("wrong plan keys")
    names = data["blocks"]
    if (
        not isinstance(names, list)
        or not all(isinstance(n, str) for n in names)
        or len(names) != len(set(names))
        or not set(names) <= OPTIONAL
    ):
        raise ValueError("invalid optional blocks")
    if type(data["transcript_window"]) is not int or data["transcript_window"] != window:
        raise ValueError("transcript depth must stay unchanged")
    return ContextPlan(frozenset(names), window)


def rules(message: str, window: int) -> ContextPlan:
    # ponytail: omit optional context only for an explicit arithmetic question;
    # ambiguous requests retain everything. Broaden only after replay evidence.
    arithmetic = re.fullmatch(
        r"\s*\d+(?:\.\d+)?\s*[+*/\u00d7\u00f7-]\s*\d+(?:\.\d+)?\s*[=?\uff1f]*\s*", message
    )
    split_bill = re.fullmatch(
        r"\s*(?:nora\.\d+\s+)?\d+蚊夾\d+個人\uff0c每人俾幾多[?\uff1f]\s*",
        message, re.IGNORECASE,
    )
    return ContextPlan(frozenset() if arithmetic or split_bill else OPTIONAL, window)


def flash(prompt: str, window: int, receipt: dict) -> ContextPlan:
    from nora.config import load
    cfg = load(Path("config.toml"), require_secrets=False)
    url, key, model = cfg.endpoint("context_filter", "ollamacloud/deepseek-v4.1-flash")
    with httpx.Client(timeout=10) as client:
        response = client.post(
            url.rstrip("/") + "/chat/completions",
            headers={"Authorization": "Bearer " + key},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0,
                "reasoning_effort": "none",
                "max_tokens": 128,
            },
        )
        response.raise_for_status()
        data = response.json()
        receipt.update(
            model=data.get("model"),
            response_id=data.get("id"),
            usage=data.get("usage"),
            cache=response.headers.get("x-omniroute-cache"),
            provider=response.headers.get("x-omniroute-provider"),
        )
        return parse_plan(data["choices"][0]["message"]["content"], window)


@contextmanager
def exposure(
    arm: str,
    report: Path | None = None,
    *,
    selector: Callable[[str, int, dict], ContextPlan] = flash,
) -> Iterator[list[dict]]:
    """Whole-run patch; per-call context stays isolated across eval workers."""
    if arm == "full" and report is None:
        yield []
        return
    if arm not in {"full", "rules", "flash"}:
        raise ValueError("unknown context arm")
    active: ContextVar[tuple[TagContext, datetime, str] | None] = ContextVar(
        "context_filter", default=None
    )
    original = prefill.build_prefill
    current_room: ContextVar[Room | None] = ContextVar("context_room", default=None)
    receipts: list[dict] = []

    def room(**kwargs: object) -> Room:
        made = Room(**kwargs)
        current_room.set(made)
        return made

    def build(ctx: TagContext, **kwargs: object) -> list[dict]:
        clock = kwargs.get("now") or prefill.local_now()
        kwargs["now"] = clock
        token = active.set((ctx, clock, str(kwargs["display_name"])))
        try:
            return original(ctx, **kwargs)
        finally:
            active.reset(token)

    def filtered(blocks: Sequence[ContextBlock], **kwargs: object) -> str:
        state = active.get()
        if state is None:
            return assemble(blocks, **kwargs)
        ctx, now, display_name = state
        window = len(ctx.transcript)
        receipt = {
            "arm": arm,
            "tag_id": ctx.tag_id,
            "transcript_window": window,
            "available": [b.name for b in blocks if b.body],
        }
        start = time.perf_counter()
        try:
            if arm == "full":
                plan = ContextPlan(OPTIONAL, window)
            elif arm == "rules":
                plan = rules(ctx.message.body, window)
            else:
                plan = selector(
                    selector_prompt(ctx, blocks, now, display_name), window, receipt
                )
            # Validate injected selectors too; contract enforcement belongs here.
            plan = parse_plan(
                json.dumps(
                    {"blocks": sorted(plan.blocks), "transcript_window": plan.transcript_window}
                ),
                window,
            )
        except (
            httpx.HTTPError, KeyError, IndexError, AttributeError, TypeError, ValueError
        ) as exc:
            plan = ContextPlan(OPTIONAL, window)
            receipt["selector_error"] = type(exc).__name__
        selected = [b for b in blocks if b.name not in OPTIONAL or b.name in plan.blocks]
        made = current_room.get()
        if made is not None:
            # `about` must fetch history when it was withheld, rather than
            # pointing Nora at a block she never saw. This is an eval-only seam;
            # a runtime planner would pass exposure into Room's constructor.
            made._tagger_card_injected = any(
                b.name == "peer_history" and b.body for b in selected
            )
            current_room.set(None)
        receipt.update(
            seconds=time.perf_counter() - start,
            selected=sorted(plan.blocks),
            withheld=[b.name for b in blocks if b.body and b not in selected],
            full_chars=len(assemble(blocks)),
            exposed_chars=len(assemble(selected)),
        )
        receipts.append(receipt)
        case = _CASE.get()
        if case is not None:
            receipt["case"] = case[0]
            case[1].append(receipt)
        annotate(
            **{
                "nora.context_filter.arm": arm,
                "nora.context_filter.withheld": ",".join(receipt["withheld"]),
                "nora.context_filter.selector_error": receipt.get("selector_error", ""),
                "nora.context_filter.seconds": receipt["seconds"],
                "nora.context_filter.exposed_chars": receipt["exposed_chars"],
            }
        )
        return assemble(selected, **kwargs)

    try:
        with (
            patch("nora.engagement.pipeline.Room", room),
            patch("nora.engagement.pipeline.build_prefill", build),
            patch.object(prefill, "assemble", filtered),
        ):
            yield receipts
    finally:
        if report is not None:
            report.write_text(
                json.dumps({"arm": arm, "system": SYSTEM, "draws": receipts}, indent=2)
            )
        errors = sum("selector_error" in r for r in receipts)
        print(
            f"context arm={arm} draws={len(receipts)} selector_errors={errors} "
            "(full-context fallback)"
        )
