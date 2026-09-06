"""The Relevance Gate — one cheap structured call that decides whether an
untagged burst is worth Nora's attention at all
(design/dreamer-agent-loop/36).

WHAT THIS MODULE IS
-------------------
The judgement that used to cost a full agent turn. The participation design
(35) answered "is this worth speaking to?" by running Nora — whole doctrine,
every tool, the turn's whole budget — on every quiescence-closed window. That
is not an evaluation, it is the answer done twice; and the trace that settled
it showed the determination opening a weather tool on a path whose only job
was to decide whether to open its mouth.

This module is the opposite shape: ONE call, transcript + standing, no tools,
no doctrine, no loop, JSON out. It is admission control and nothing else.

WHAT IT DELIBERATELY CANNOT ANSWER
----------------------------------
"Does NORA hold something true to add?" It never sees notes, facts or the
peer card, so it cannot — and must not pretend to. That question belongs to
the participation turn behind the gate, which keeps its right to silence
(design 36 §7). The two questions are separate on purpose:

    gate  — is this worth a reply from anyone?      ~1-2k, transcript+standing
    turn  — do I hold anything real to add?         the full turn, everything

A gate that started guessing the second question would be the 35 design again
in a cheaper coat.

FAIL-CLOSED, AND THE THREE STATES ARE KEPT APART (R-27)
-------------------------------------------------------
A gate that forwards on failure is not a gate. Every non-answer — a transport
error, a timeout, a `finish_reason="length"` truncation, a body that does not
parse, a verdict string that is neither value — resolves to NO-GO.

But it is never counted as a clean `no_go`. `GateVerdict.source` keeps
«the model said no» apart from «I could not ask», because those are the same
observable outcome and completely different operational facts: the first is
the gate working, the second is the gate DOWN while looking exactly like a
quiet room. The counters follow that split — `nora_relevance_gate_total`
moves only on a real verdict, `nora_relevance_gate_errors_total` carries the
reason a fail-closed one happened.

A truncated body is treated as an error and not parsed, for
`nora.engagement.stages.debrief`'s already-paid-for reason: a cut-off JSON
document can still parse into something shorter and wrong, which is an ERROR
wearing a PASS's face.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Literal, Protocol

from nora.domain.models import TranscriptRow
from nora.domain.ports import RecordStore, StandingStore
from nora.domain.provenance import instruction_sha256
from nora.obs.metrics import Metrics
from nora.obs.tracing import annotate, span

log = logging.getLogger("nora.ingest.relevance_gate")

Verdict = Literal["go", "no_go"]

#: Why a verdict reads the way it does. `model` is the gate working — the call
#: was made, the body parsed, the model chose. `fail_closed` is every other
#: path, and it is a DIFFERENT fact wearing the same `no_go`.
VerdictSource = Literal["model", "fail_closed"]

#: The fail-closed reasons, one label per distinguishable failure. This tuple
#: IS the metric's label vocabulary — `nora/obs/metric_specs.py` reads it from
#: here rather than restating it, because a hand-copied vocabulary is what
#: drifts (the `nora_ingest_outcomes_total` precedent).
GATE_ERROR_KINDS: tuple[str, ...] = (
    "call_failed",     # transport, timeout, provider refusal — never reached a body
    "truncated",       # finish_reason="length": a body exists and must not be trusted
    "unparseable",     # not JSON, or not an object
    "bad_verdict",     # parsed, but `verdict` was absent or neither value
)


@dataclass(frozen=True)
class Completion:
    """One raw completion, with the field that decides whether it may be
    parsed at all.

    `finish_reason` rides along for `debrief.Completion`'s reason: a raw
    `chat.completions.create` does not raise on a cut-off, it returns
    `"length"` and a silently truncated body. The two must arrive together or
    a caller will eventually check one and use the other.
    """

    content: str
    finish_reason: str


class ModelClient(Protocol):
    """The gate's one collaborator that talks to a model.

    A documented structural type rather than a `nora.domain.ports` Protocol,
    following `debrief.ModelClient`'s stated precedent: one consumer, one
    verb, and promoting it would commit a production implementation plus a
    fake to the shared contract-suite rule for a shape with nothing to share
    it with. `nora.ingest` may not import `nora.config` or build an OpenAI
    client, so the real one is constructed in the composition root.
    """

    @property
    def model(self) -> str: ...

    async def complete(self, *, prompt: str) -> Completion: ...


@dataclass(frozen=True)
class GateVerdict:
    """What the gate decided, and whether it was the model that decided it.

    `score` is REFERENCE ONLY (design 36 decision 5). It rides into the
    participation turn as context and shows in the shadow post; it is never a
    threshold. The verdict is the only enforcement surface, which is why there
    is nothing here to compare a score against.
    """

    verdict: Verdict
    score: float
    source: VerdictSource
    #: Set only when `source == "fail_closed"`; one of `GATE_ERROR_KINDS`.
    error_kind: str | None = None

    @property
    def admits(self) -> bool:
        return self.verdict == "go"


def _fail_closed(kind: str) -> GateVerdict:
    return GateVerdict(verdict="no_go", score=0.0, source="fail_closed", error_kind=kind)


#: The whole instruction. Deliberately NOT Nora's doctrine and deliberately
#: not a persona: this is a measurement instrument, and the one thing it must
#: never start doing is drafting what she would say. The last rule is load-
#: bearing — a gate that proposes an answer has begun answering.
PROMPT = """\
You are a relevance gate for a group-chat assistant. Decide whether the LATEST \
activity in this room deserves the assistant's attention.

Context (nothing else is available to you):
{context}

Rules:
- GO only if the room holds a question the assistant can answer, a fact it can \
correct, a commitment it made that is being followed up, or something that \
specifically needs it.
- NO-GO for: status updates, acknowledgements, small talk that needs no answer, \
private exchanges between other people, inside jokes, commands aimed at other \
services, and anything the assistant cannot add grounded value to.
- You cannot see the assistant's notes, facts or memory, so do NOT try to judge \
whether it knows the answer. Judge only whether the room is ASKING for one.
- Do not suggest what the assistant would say. You only decide relevance.

Respond with JSON only:
{{"verdict": "go" | "no_go", "relevance_score": 0.0}}"""


def render_context(
    transcript: tuple[TranscriptRow, ...], standing_body: str | None,
) -> str:
    """The two blocks the gate is allowed to see, and no third.

    Rendered here rather than reusing the tag path's prefill assembler on
    purpose: that assembler's job is to build the RICHEST prompt it can, and
    every block it learns to add would silently widen this one. The gate's
    input is a closed set, so it is built by a function that can only produce
    a closed set.

    Rows arrive newest-first from `RecordStore.recent` and are rendered
    oldest-first, because a transcript read backwards is a different
    conversation.
    """
    lines = []
    for row in reversed(transcript):
        body = (row.body or "").strip()
        if not body:
            continue
        who = "assistant" if row.is_nora else (row.sender_name or row.sender_id)
        lines.append(f"{who}: {body}")
    transcript_block = "\n".join(lines) if lines else "(no messages)"
    standing_block = (standing_body or "").strip() or "(none)"
    return (
        f"- transcript (oldest first):\n{transcript_block}\n"
        f"- standing (the assistant's own position in this conversation):\n"
        f"{standing_block}"
    )


class RelevanceGate:
    """One verdict per closed window. Never sends, never drafts, never
    constructs an agent.

    Collaborators arrive through `__init__` only — no `*_fn` hooks and no
    module-level state, per the repo's test-seam policy. `records` and
    `standing` are the same ports the fire path reads, so the gate sees the
    same room the turn behind it would.
    """

    def __init__(
        self,
        *,
        client: ModelClient,
        records: RecordStore,
        metrics: Metrics,
        standing: StandingStore | None = None,
        transcript_limit: int = 40,
        standing_timeout_seconds: float = 2.0,
    ) -> None:
        self._client = client
        self._records = records
        self._metrics = metrics
        # `None` is the dormant default, not an error: `[standing] enabled =
        # false` is a shipped configuration, and a gate that refused to run
        # without a position would make an optional tier mandatory.
        self._standing = standing
        self._transcript_limit = transcript_limit
        self._standing_timeout_seconds = standing_timeout_seconds

    async def decide(self, *, platform: str, conversation_id: str) -> GateVerdict:
        """Judge one closed window. NEVER raises — every failure is a
        counted `no_go`, because a gate that propagates an exception to the
        trigger is a gate that stops the whole ambient path on one bad call.
        """
        with span(
            "ingest.relevance_gate",
            **{
                "langfuse.observation.type": "generation",
                "langfuse.observation.model.name": self._client.model,
                "gen_ai.operation.name": "chat",
                "gen_ai.request.model": self._client.model,
                "nora.conversation_id": conversation_id,
                "nora.platform": platform,
            },
        ):
            transcript = await self._read_transcript(platform, conversation_id)
            standing_body = await self._read_standing(platform, conversation_id)
            prompt = PROMPT.format(
                context=render_context(transcript, standing_body),
            )
            # The prompt is a room's own words, so it is NOT put on the wire.
            # The digest plus the retained transcript re-derives it exactly —
            # design 13's rule, applied to the one call that reads every room.
            annotate(**{
                "nora.relevance_gate.prompt_sha256": instruction_sha256(prompt),
                "nora.relevance_gate.transcript_rows": len(transcript),
                "nora.relevance_gate.standing": standing_body is not None,
            })
            verdict = await self._ask(prompt)
            annotate(**{
                "nora.relevance_gate.verdict": verdict.verdict,
                "nora.relevance_gate.score": verdict.score,
                "nora.relevance_gate.source": verdict.source,
                "nora.relevance_gate.error_kind": verdict.error_kind or "",
            })
            self._count(verdict, platform)
            return verdict

    async def _ask(self, prompt: str) -> GateVerdict:
        try:
            completion = await self._client.complete(prompt=prompt)
        except Exception:
            log.exception("relevance gate call failed; failing closed")
            return _fail_closed("call_failed")
        if completion.finish_reason == "length":
            # NOT parsed. See the module docstring: a truncated JSON body can
            # parse into something shorter and wrong.
            log.warning("relevance gate answer was truncated; failing closed")
            return _fail_closed("truncated")
        return parse_verdict(completion.content)

    async def _read_transcript(
        self, platform: str, conversation_id: str,
    ) -> tuple[TranscriptRow, ...]:
        try:
            return tuple(await self._records.recent(
                conversation_id=conversation_id, limit=self._transcript_limit,
            ))
        except Exception:
            # A gate with no transcript has nothing to judge, so this is not
            # "a thinner prompt" the way the fire path's identical handler is —
            # it is the input missing. It still returns rather than raising;
            # `_ask` will be asked about an empty room and the model will say
            # no_go, and the counter below is what says the read failed.
            log.exception("relevance gate transcript read failed")
            self._metrics.inc(
                "nora_errors_total", stage="relevance_gate_context", platform=platform,
            )
            return ()

    async def _read_standing(
        self, platform: str, conversation_id: str,
    ) -> str | None:
        """The room's position, or `None`. Fail-SOFT and counted — the three
        states `FirePath._read_standing` distinguishes, for its reason: no
        store is the dormant default and is not an error; no row is a
        conversation never folded; a FAILED read is neither and is counted.
        """
        if self._standing is None:
            return None
        try:
            row = await asyncio.wait_for(
                self._standing.read(platform=platform, conversation_id=conversation_id),
                timeout=self._standing_timeout_seconds,
            )
        except Exception:
            log.exception("relevance gate standing read failed")
            self._metrics.inc(
                "nora_errors_total", stage="relevance_gate_standing", platform=platform,
            )
            return None
        return row.body if row is not None else None

    def _count(self, verdict: GateVerdict, platform: str) -> None:
        if verdict.source == "fail_closed":
            # NOT counted on `nora_relevance_gate_total`. A fail-closed no_go
            # in the same series as a chosen one is R-23's bug: the healthy
            # outcome and the broken one become indistinguishable exactly when
            # someone needs to tell them apart.
            self._metrics.inc(
                "nora_relevance_gate_errors_total",
                kind=verdict.error_kind or "call_failed", platform=platform,
            )
            return
        self._metrics.inc(
            "nora_relevance_gate_total", verdict=verdict.verdict, platform=platform,
        )


def parse_verdict(raw: str) -> GateVerdict:
    """`raw` -> a verdict, fail-closed (design 36 decision 2).

    A module-level function because it is the one piece worth testing without
    a model, a store or a metric in the way.
    """
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return _fail_closed("unparseable")
    if not isinstance(parsed, dict):
        # `json.loads("null")`, `"[]"` and `"3"` all succeed and are not
        # verdicts. Checking the TYPE and not just the parse is the difference
        # between "it was JSON" and "it was an answer".
        return _fail_closed("unparseable")
    verdict = parsed.get("verdict")
    if verdict not in ("go", "no_go"):
        return _fail_closed("bad_verdict")
    return GateVerdict(
        verdict=verdict, score=_score(parsed.get("relevance_score")), source="model",
    )


def _score(value: object) -> float:
    """A number in [0, 1], or 0.0.

    The score never blocks anything (decision 5), so a missing or malformed
    one is not a failure of the verdict — it is a missing annotation, and
    rejecting an otherwise good verdict over it would let a reference field
    behave like enforcement.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return max(0.0, min(1.0, float(value)))
