"""The Relevance Gate — one cheap structured call that decides whether an
untagged burst is worth Nora's attention at all.

WHAT THIS MODULE IS
-------------------
Admission control and nothing else: ONE call, transcript + standing, no tools,
no doctrine, no loop, JSON out. It is deliberately not a full agent turn (whole
doctrine, every tool, the turn's whole budget) run on every quiescence-closed
window to answer "is this worth speaking to?".

WHAT IT DELIBERATELY CANNOT ANSWER
----------------------------------
"Does NORA hold something true to add?" It never sees notes, facts or the
peer card, so it cannot — and must not pretend to. That question belongs to
the participation turn behind the gate, which keeps its right to silence. The
two questions are separate on purpose:

    gate  — is this worth a reply from anyone?      ~1-2k, transcript+standing
    turn  — do I hold anything real to add?         the full turn, everything

A gate that guessed the second question would be a full turn in a cheaper coat.

FAIL-CLOSED, AND THE THREE STATES ARE KEPT APART (R-27)
-------------------------------------------------------
A gate that forwards on failure is not a gate. Every non-answer — a transport
error, a timeout, a `finish_reason="length"` truncation, a body that does not
parse, a verdict string that is neither value — resolves to NO-GO.

But it is never counted as a clean `no_go`. `GateVerdict.source` keeps
«the model said no» apart from «I could not ask», because those are the same
observable outcome and completely different operational facts: the first is
the gate working, the second is the gate DOWN while looking exactly like a
quiet room. Both counters carry `conversation_id`, so a refusal can be joined
to what she was holding for that room.

The counters follow that split — `nora_relevance_gate_total`
moves only on a real verdict, `nora_relevance_gate_errors_total` carries the
reason a fail-closed one happened.

A truncated body is treated as an error and not parsed, for
`nora.engagement.stages.debrief`'s reason: a cut-off JSON
document can still parse into something shorter and wrong, which is an ERROR
wearing a PASS's face.

THE CEILING IS THE GATE'S OWN, AND IT IS ITS OWN ERROR KIND
-----------------------------------------------------------
`decide` promises never to raise. It does not promise to RETURN, and that is a
different property: `ModelClient.complete` is a structural type with no
timeout in its contract, so «the gate never hangs» would be a property of
whichever client the composition root happened to wire rather than of this
class. It matters here more than it would elsewhere, because the trigger holds
the conversation's SLOT across `decide` and walks its due windows serially —
one hanging call is not a slow background job, it is that room refusing tags
and every other room's window never being judged.

So the bound is taken here, in `_ask`, on top of whatever the client carries.
And it is COUNTED APART from `call_failed`: a timeout and a provider refusal
need different fixes. `nora_relevance_gate_seconds` is the other half — the
ceiling can only be argued against a latency distribution.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Literal, Protocol
from xml.sax.saxutils import escape

from pydantic import BaseModel, Field

from nora.domain.blocks import (
    BlockKey,
    ContextBlock,
    assemble,
    render_messages,
    speaker_name,
)
from nora.domain.media_join import MediaJoin
from nora.domain.models import TranscriptRow
from nora.domain.ports import RecordStore
from nora.domain.prompt import age as _age
from nora.domain.prompt import one_line as _one_line
from nora.domain.provenance import instruction_sha256
from nora.obs.metrics import Metrics
from nora.obs.tracing import TRACE_KIND, annotate, span
from nora.persons import Persons

log = logging.getLogger("nora.ingest.relevance_gate")

Verdict = Literal["go", "no_go"]

#: Why a verdict reads the way it does. `model` is the gate working — the call
#: was made, the body parsed, the model chose. `fail_closed` is every other
#: path, and it is a DIFFERENT fact wearing the same `no_go`.
VerdictSource = Literal["model", "fail_closed"]

#: The fail-closed reasons, one label per distinguishable failure. This tuple
#: IS the metric's label vocabulary — `nora/obs/metric_specs.py` reads it from
#: here rather than restating it, because a hand-copied vocabulary drifts.
GATE_ERROR_KINDS: tuple[str, ...] = (
    "call_failed",     # transport error, provider refusal — never reached a body
    # Split out of `call_failed`: pooled, the series cannot tell «the provider
    # refused» from «the ceiling is too low for this arm», two different fixes.
    "timeout",         # the ceiling fired: no body, and the call was still running
    "truncated",       # finish_reason="length": a body exists and must not be trusted
    "unparseable",     # not JSON, or not an object
    "bad_verdict",     # parsed, but `verdict` was absent or neither value
    "no_context",      # the transcript read failed — nothing was measured
    "no_instruction",  # participation.md is missing or empty — no criteria exist
)

#: The OTel attribute value for an ABSENT score. OTel attributes cannot carry
#: `None`, and `0.0` would read as «this room scored 0.00», so absence is a
#: sentinel. Shared by the gate span and the participation root span that
#: threads the score through (`fire.py`), so a `score > -1` filter means
#: «a score was stated» in both places.
SCORE_ABSENT = -1.0


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


class GateVerdictSchema(BaseModel):
    """The verdict, as a schema the SDK constrains the model to.

    The SAME two fields `participation.md` asks for in prose and
    `parse_verdict` reads back — declared once so the structured path and the
    raw path answer the same question rather than two similar ones.

    Production does not use it: the gate forms its verdict through a raw
    `json_object` completion, because the SDK's structured output breached the
    suite's safety bar on this call. Its one caller is the eval harness's
    `--via-sdk` arm, which MEASURES that — so this stays here, beside
    `parse_verdict`, rather than moving into the harness: the comparison is
    only worth reading while both arms are asked the same question.

    `relevance_score` defaults to the `-1.0` sentinel `parse_verdict` already
    uses for absent, because the score is optional by design: a gate that must
    invent a number to answer at all is a gate reporting confidence it does
    not have.
    """

    verdict: Literal["go", "no_go"] = Field(
        description="whether this window is worth a participation turn",
    )
    relevance_score: float = Field(
        default=-1.0,
        description="0.0-1.0 that the room wants Nora; omit rather than guess",
    )


class StandingReader(Protocol):
    """The gate's view of `StandingStore`: one verb, `read`.

    Narrow on purpose. The gate never writes a position and typing it as the
    full `StandingStore` said otherwise — which is not only a wider contract
    than it needs, it obliged every test double to grow a `write` that exists
    only to be refused. `nora.engagement.stages.debrief.ModelClient`'s
    precedent: one consumer, one verb, a documented structural type.
    """

    async def read(
        self, *, platform: str, conversation_id: str
    ) -> Any | None: ...


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

    async def complete(self, *, system: str, prompt: str) -> Completion: ...


@dataclass(frozen=True)
class _Instruction:
    """A non-empty gate instruction, with the provenance a verdict is read
    against.

    `version` and `sha256` ride along because the file HOT-RELOADS: without
    them nothing ties a verdict to the text that produced it.

    The prompt digest already on the span cannot do this job: it hashes
    instruction PLUS the room, so it changes on every call and is not
    comparable across windows.
    """

    text: str
    version: int
    sha256: str


def _has_body(doctrine_text: str) -> bool:
    """True when the assembly carries prose, not just its own wrapper.

    A file whose frontmatter parses but whose body is empty still yields a
    `<section>` element, so counting sections is not enough — the loader is
    happy and the gate would be criteria-free.
    """
    stripped = doctrine_text
    for tag in ("<nora_doctrine>", "</nora_doctrine>"):
        stripped = stripped.replace(tag, "")
    stripped = re.sub(r"</?section[^>]*>", "", stripped)
    return bool(stripped.strip())


@dataclass(frozen=True)
class GateVerdict:
    """What the gate decided, and whether it was the model that decided it.

    `score` is REFERENCE ONLY. It rides into the
    participation turn as context and shows in the shadow post; it is never a
    threshold. The verdict is the only enforcement surface, which is why there
    is nothing here to compare a score against.
    """

    verdict: Verdict
    #: `None` means the model returned no usable score. NOT 0.0 — the score is
    #: reference only, so an ABSENT one must render as nothing rather than as
    #: a low one: collapsing the two would show «this room scored 0.00» for a
    #: window the gate admitted.
    score: float | None
    source: VerdictSource
    #: Set only when `source == "fail_closed"`; one of `GATE_ERROR_KINDS`.
    error_kind: str | None = None
    #: The provider's own finish reason when the model answered, `""` when
    #: the verdict was fail-closed (nothing was answered, or the truncation
    #: was decided before the answer was trusted). Carried onto the span so
    #: the trace writes a REAL provider fact and never an invented one.
    finish_reason: str = ""

    @property
    def admits(self) -> bool:
        return self.verdict == "go"


def _fail_closed(kind: str, *, finish_reason: str = "") -> GateVerdict:
    return GateVerdict(
        verdict="no_go", score=None, source="fail_closed", error_kind=kind,
        finish_reason=finish_reason,
    )


class PromptSource(Protocol):
    """Where the gate's instruction comes from, read fresh on every call.

    A SOURCE and not a string, for `debrief.ObjectiveSource`'s reason: the
    doctrine directory is bind-mounted and hot-reloads on mtime, so a gate
    handed its prompt at construction would keep sending whatever was on disk
    when the process started and an operator's edit would look like it did
    nothing. That argument is at its strongest here: an operator reads
    verdicts and adjusts the instruction without a rebuild.
    """

    def get(self, *, applies_to: str = ...) -> Any: ...


#: The audience `nora/doctrine/instruments/participation.md` declares. Nothing else uses
#: it, which is the isolation: the gate never receives Nora's doctrine, and she
#: never receives the gate's instruction — it describes an instrument, not a
#: person.
GATE_AUDIENCE = "relevance_gate"

#: Everything ABOVE this is `participation.md`, which the operator edits; this
#: line and the JSON contract below are the parts the code owns because the
#: parser depends on them.
#:
#: The blocks are described by type in `nora.domain.block_types`, rendered into
#: each block's own opening tag, so each has exactly one description and none
#: lives in this module.
_CONTEXT = """{context}

Respond with JSON only:
{{"verdict": "go" | "no_go", "relevance_score": 0.0}}"""


def build_user_message(context: str) -> str:
    """The room, and only the room.

    The instruction is NOT in here. It is the system prompt
    (`build_system_prompt`), a different role from the user message.

    Rules and transcript concatenated into one `user` message would let a
    member forge a turn: the eight characters `[assistant]:` typed by a person
    are structurally identical to a turn Nora actually took. `_one_line` stops
    a row producing a second ROW; it cannot make a role out of text that has
    none.
    """
    return _CONTEXT.format(context=context)


#: The header the derived capability digest arrives under, so the instruction
#: can name it and the two never contradict each other.
CAPABILITY_HEADER = "# What she can actually call"

_CAPABILITY_BLOCK = """{header}

Derived from her live tool registry — this is the whole read-only surface, and
it is what «something she can supply» means. Read-only verbs only: a tool that
ACTS is never the reason a window is worth her turn.

{digest}"""


def build_system_prompt(instruction: str, capabilities: str = "") -> str:
    """`participation.md` as the system prompt,
    plus the capability digest it cannot write down without going stale.

    A system message is not merely «first»: it is a different role, one the
    group's own words can never occupy, so an instruction here cannot be
    imitated by anything a member types into the transcript below it.

    `capabilities` is `ToolRegistry.capability_digest()`. It is
    APPENDED rather than interpolated so an operator editing the instruction
    cannot accidentally delete it, and it is derived on every start so a tool
    added on master is a capability the gate knows about on the next deploy.

    Empty is allowed and appends nothing: a composition that cannot build a
    registry degrades to the bare instruction rather than failing closed on
    every window.
    """
    text = instruction.rstrip()
    if not capabilities.strip():
        return text
    block = _CAPABILITY_BLOCK.format(
        header=CAPABILITY_HEADER, digest=capabilities.strip(),
    )
    return f"{text}\n\n{block}"


# `_one_line` is `nora.domain.prompt.one_line`, imported under this module's own
# name because its call sites and its two tests read it. It collapses everything
# `str.splitlines` breaks on.


#: Her own rows are `assistant` here, not her display name: this prompt speaks
#: of her in the third person, unlike the tag prefill. It is what the `from`
#: accessor returns; an attribute needs no delimiter and this prompt addresses
#: nothing.
_GATE_SELF_NAME = "assistant"


#: The two blocks the gate is allowed to see, and no third. Declared as data
#: for the same reason `nora.agent.prefill._BLOCKS` is: so «what does the gate
#: carry» is a table to read rather than a trace through `render_context`, and
#: so a third row can be caught by a test. `key="turn"` for both
#: (`ContextBlock.key` defaults to it); the gate's prompt is not part of the tag
#: path's cached prefix, so a key copied from prefill would not mean the same
#: thing here.
_BLOCKS: tuple[tuple[str, int, BlockKey], ...] = (
    ("group_transcript", 10, "turn"),
    ("standing", 20, "turn"),
)

#: `name -> (order, key)`, derived so `render_context` cannot restate a row
#: `_BLOCKS` already gives it, which would let the two drift.
_ROW: dict[str, tuple[int, BlockKey]] = {
    name: (order, key) for name, order, key in _BLOCKS
}


def render_context(
    transcript: tuple[TranscriptRow, ...],
    standing: tuple[str, int] | str | None,
    *, now: datetime | None = None, window: int | None = None,
) -> str:
    """The two blocks the gate is allowed to see, and no third.

    Rendered here rather than reusing the tag path's prefill assembler
    because that assembler's job is to build the RICHEST prompt it can, and
    every block it learns to add would silently widen this one. The gate's
    input is a closed set, so it is built by a function that can only produce
    a closed set.

    **What that decision does NOT license is dropping R-13.** Raw room text
    with no escaping and no framed block would let a member whose message
    contained the verdict JSON write the gate's own answer, because this prompt
    ends with the literal verdict schema. `nora.agent.prefill` escapes and
    labels group text (R-13); declining to reuse the ASSEMBLER never meant
    declining the RULE.

    Rows arrive **oldest-first** from `RecordStore.recent` —
    `tests/contract/test_audit_store_contract.py` pins it for every
    implementation — so they are rendered in the order given.

    Speakers are labelled `person_name or sender_name or "unknown"`, the
    canonical order (DEC-80, `prefill._label`). Never `sender_id`: a raw ACI
    or E.164 in this string is an identifier on the wire to the provider,
    which is the egress R-18 exists to keep closed.

    **A newline inside a body cannot forge a row; the defence is the element
    boundary.** Each message is its own `m` element inside
    `<group_transcript>`, so a body keeps its newlines and produces no row:
    `escape` makes a closing tag unwritable (R-13b) and the speaker is an
    attribute `escape_attr` closes. The newline collapse is kept on every
    ATTRIBUTE value, in `render_elements`. A forged `[assistant]: …` line
    would take the gate to `go` even against a hardened prompt, which is why
    this is the renderer's job and not the instruction's. The separator has to
    be something a row's content cannot contain: a tag.
    """
    clock = now or datetime.now(UTC)
    # A row with nothing left after the collapse is DROPPED, not rendered as an
    # empty element. That is a filter rather than a style, so it stays here: an
    # attachment or a reaction carries no words for this gate to weigh, and a
    # bare `<m from="somebody"></m>` is a row that looks like silence from a
    # person who did not speak.
    rows = tuple(r for r in transcript if _one_line(r.body or ""))
    transcript_block = render_messages(
        rows,
        speaker=lambda r: (
            _GATE_SELF_NAME if r.is_nora
            else speaker_name(r.person_name, r.sender_name)
        ),
        body=lambda r: r.body,
        # `or None`: a row stamped 0 renders no age rather than «56y ago».
        timestamp=lambda r: r.ts or None,
        now=clock,
        edited=lambda r: r.edit.ts if r.edit else None,
        was=lambda r: r.edit.was if r.edit else None,
    ) if rows else "(no messages)"
    # Standing is Nora's own folded text rather than a member's, but it is
    # rendered in the same document and a fold summarises what people said —
    # so the same collapse applies rather than trusting the tier above.
    # `(body, updated_at)`; a bare string still works and renders no age.
    standing_body, written = (
        standing if isinstance(standing, tuple) else (standing, None)
    )
    standing_block = escape(_one_line(standing_body or "")) or "(none)"
    # THROUGH THE INJECTOR, and DESCRIBED BY TYPE. Each block's framing is its
    # own `description=`, where content cannot reach it and where it cannot
    # disagree with how the prefill path describes the same two blocks.
    #
    # `<standing>` renders `(none)` rather than nothing, and that is load-
    # bearing rather than cosmetic: the instruction leans on this block being
    # the only thing that can establish what she owes the room, so its absence
    # has to be READABLE. An empty body would be dropped by `assemble` and the
    # sentence explaining what its absence means would go with it.
    transcript_order, transcript_key = _ROW["group_transcript"]
    standing_order, standing_key = _ROW["standing"]
    return assemble((
        ContextBlock(
            name="group_transcript", order=transcript_order, key=transcript_key,
            # `window` is what the caller READ, not what it rendered, and the
            # two differ: a row with nothing left after the collapse is dropped
            # above. «How far back you were shown» is the question this answers
            # and the read bound is its honest answer — saying 37 when 40 were
            # read would tell her three messages do not exist.
            #
            # Absent when the caller cannot say, rather than guessed. The type
            # annotates this field, so a block that renders no `window` is one
            # whose caller did not know the bound — visible, not silently 40.
            attrs=(("window", str(window)),) if window is not None else (),
            body=transcript_block,
        ),
        ContextBlock(
            name="standing", order=standing_order, key=standing_key,
            attrs=(
                (("written", _age(written, clock)),) if written is not None
                else ()
            ),
            body=standing_block,
        ),
    )).rstrip("\n")


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
        prompts: PromptSource,
        records: RecordStore,
        metrics: Metrics,
        persons: Persons | None = None,
        standing: StandingReader | None = None,
        transcript_limit: int = 40,
        standing_timeout_seconds: float = 2.0,
        call_timeout_seconds: float = 20.0,
        media_join: MediaJoin | None = None,
        capabilities: str = "",
    ) -> None:
        self._client = client
        # `ToolRegistry.capability_digest()`, rendered once by the composition
        # root. A STRING and not the registry: this class's whole
        # contract is that it never constructs an agent, and holding the thing
        # that builds tools would be the first step away from that.
        self._capabilities = capabilities
        self._prompts = prompts
        self._records = records
        self._metrics = metrics
        # The same join every other transcript builder runs. A gate blind to
        # media does not merely render less — it under-admits exactly the
        # windows a photo prompted, because `render_context` drops a row whose
        # body is empty and an image-only message IS an empty body. `None` is
        # the inert default and renders the bare handles the rows carry.
        self._media_join = media_join
        # DEC-80's one-name-space projection. Without it `person_name` is
        # None on every row by construction and the label falls through to
        # the platform's self-set profile name — the two-name-space bug that
        # decision exists to close, here on the one call that reads every
        # opted-in room.
        self._persons = persons
        # `None` is the dormant default, not an error: `[standing] enabled =
        # false` is a shipped configuration, and a gate that refused to run
        # without a position would make an optional tier mandatory.
        self._standing = standing
        self._transcript_limit = transcript_limit
        self._standing_timeout_seconds = standing_timeout_seconds
        # The gate's OWN ceiling on the model call, mirroring
        # `[relevance_gate] timeout_seconds` the way `transcript_limit` and
        # `standing_timeout_seconds` above mirror their keys. A client that
        # already bounds itself (the composition root's does, and the
        # architecture suite requires it to) makes this a backstop rather than
        # a second policy — both fire as `timeout`, so which one wins is not
        # an observable difference. What it buys is that «never hangs» is a
        # property of THIS class, provable against a client that never
        # returns, instead of a property of the wiring.
        self._call_timeout_seconds = call_timeout_seconds

    def _emit(self, verdict: GateVerdict, platform: str, conversation_id: str) -> None:
        """Write ONE verdict to the wire on every path — model and
        fail-closed alike.

        `conversation_id` is carried here only to reach `_count`: the span
        already has it as `nora.conversation_id`, set at span start. It is a
        REQUIRED positional rather than a defaulted keyword because
        `Metrics.inc` fills a missing declared label with `""` instead of
        raising (`_full_labels`), so a forgotten call site would emit a series
        that looks attributable and is not — the exact failure the label
        exists to remove. A signature that cannot be called without it is the
        only part of this that a test cannot be written around.

        The verdict JSON is the gate's OWN output, safe for the wire (no room
        content), and WITHOUT it the span renders in Langfuse as a bare
        rectangle — usage and latency, no readable payload. The early returns
        (`no_context`, `no_instruction`) emit the same tiny document, with
        `finish_reason=""` saying «the model never answered».
        """
        annotate(**{
            # The gate's own verdict JSON, safe for the wire (no room
            # content) — already includes `finish_reason` (`_verdict_json`'s
            # own docstring). Plain `annotate()`, not `annotate_generation()`:
            # this span is not itself a generation span; the model call opens
            # its own child span inside `_RelevanceGateModelClient.complete()`
            # (`nora.agent.telemetry`).
            "nora.relevance_gate.verdict_json": _verdict_json(verdict),
            "nora.relevance_gate.verdict": verdict.verdict,
            # OTel attributes cannot carry `None`: an absent score is
            # `SCORE_ABSENT`, so `score > -1` is the «a score was stated»
            # filter in both this span and the participation root.
            "nora.relevance_gate.score": (
                verdict.score if verdict.score is not None else SCORE_ABSENT
            ),
            "nora.relevance_gate.source": verdict.source,
            "nora.relevance_gate.error_kind": verdict.error_kind or "",
        })
        self._count(verdict, platform, conversation_id)

    async def decide(self, *, platform: str, conversation_id: str) -> GateVerdict:
        """Judge one closed window. NEVER raises — every failure is a
        counted `no_go`, because a gate that propagates an exception to the
        trigger is a gate that stops the whole ambient path on one bad call.

        And never HANGS, which is the other half: the model
        call is bounded by `call_timeout_seconds` here rather than only in
        whichever client is wired, so the worst case is a `timeout` verdict at
        the ceiling. The trigger holds the room's slot across this and judges
        its due windows serially, so an unbounded call is not one slow window,
        it is that room refusing tags and every other room going unjudged.
        """
        with span(
            "relevance_gate",
            # A ROOT: the trigger is driven from the runtime loop, not from
            # under a turn, so nothing above it opens a span. It must be set
            # AT START or `build_sampler` decides without it and the trace is
            # dropped, and the gate is the path where a dropped trace hurts
            # most: nobody is waiting on it, so an unmeasured gate failure
            # has no human to notice it.
            #
            # `"generation"`, not `"plumbing"`, even though a fail-closed
            # verdict may never reach the model — a gate refused before the
            # call is still a gate decision worth seeing, DEC-141's point
            # about a tag refused at admission, one path over. This span is
            # not itself shaped like a generation (no `model=`, no
            # GenAI attributes) — the model call, when one happens, opens
            # its OWN child span inside `_RelevanceGateModelClient.complete`
            # (`nora.agent.telemetry.start_raw_model_span`), which is the
            # thing R-76's `TRACE_KIND` label describes: whether
            # a model call CAN happen under this root, not whether this
            # exact span is one.
            **{
                TRACE_KIND: "generation",
                "nora.conversation_id": conversation_id,
                "nora.platform": platform,
            },
        ):
            transcript = await self._read_transcript(platform, conversation_id)
            if transcript is None:
                self._emit(_fail_closed("no_context"), platform, conversation_id)
                return _fail_closed("no_context")
            instruction = self._instruction()
            if instruction is None:
                self._emit(_fail_closed("no_instruction"), platform, conversation_id)
                return _fail_closed("no_instruction")
            standing = await self._read_standing(platform, conversation_id)
            system = build_system_prompt(instruction.text, self._capabilities)
            prompt = build_user_message(render_context(
                transcript, standing, window=self._transcript_limit,
            ))
            # The digest is retained regardless — the retained transcript
            # re-derives the prompt exactly. The prompt ALSO reaches a span by
            # a separate path: `_ask` hands `system`/`prompt` to
            # `self._client.complete`, whose production implementation
            # (`_RelevanceGateModelClient`) passes them into
            # `nora.agent.telemetry.start_raw_model_span`, which Strands
            # records as real `gen_ai.system.message`/`.user.message` span
            # events — the room's own words, and every room's, including ones
            # Nora does not serve, UNSCREENED. The gate's evaluation mapper
            # needs exactly this.
            annotate(**{
                # The INSTRUCTION, separately from the whole prompt: the digest
                # below hashes the room too, so it cannot fingerprint what the
                # gate was told across windows.
                "nora.relevance_gate.instruction_version": instruction.version,
                "nora.relevance_gate.instruction_sha256": instruction.sha256,
                "nora.relevance_gate.prompt_sha256": instruction_sha256(system + prompt),
                "nora.relevance_gate.transcript_rows": len(transcript),
                # Whether the gate was told what she can call, on the trace
                # that carries the verdict: a run of no_go verdicts reads very
                # differently depending on this.
                "nora.relevance_gate.capabilities": bool(self._capabilities),
                "nora.relevance_gate.standing": standing is not None,
            })
            verdict = await self._ask(system, prompt)
            self._emit(verdict, platform, conversation_id)
            return verdict

    async def _ask(self, system: str, prompt: str) -> GateVerdict:
        started = time.perf_counter()
        try:
            completion = await asyncio.wait_for(
                self._client.complete(system=system, prompt=prompt),
                timeout=self._call_timeout_seconds,
            )
        except TimeoutError:
            # Its OWN kind, not `call_failed`: pooled with a transport error
            # the series could not say the ceiling fired.
            #
            # `TimeoutError` and not `asyncio.TimeoutError` — the same object
            # since 3.11, and the builtin is what a client's own `wait_for`
            # raises too, so a bound taken below this one is caught here rather
            # than falling through to the generic arm and losing its label.
            log.warning(
                "relevance gate call exceeded %.1fs; failing closed",
                self._call_timeout_seconds,
            )
            return _fail_closed("timeout")
        except Exception:
            log.exception("relevance gate call failed; failing closed")
            return _fail_closed("call_failed")
        finally:
            # EVERY exit, including the two failures — `nora_debrief_seconds`'s
            # rule one stage over: a latency series that records only successes
            # describes the path that already works. The timeout samples are
            # CENSORED at the ceiling (a call cut at 20s may have wanted 45),
            # so this reads as «how long the gate is willing to wait», and the
            # top bucket paired with `{kind="timeout"}` is what says the
            # ceiling is the binding constraint rather than the provider.
            self._metrics.observe(
                "nora_relevance_gate_seconds", time.perf_counter() - started,
            )
        if completion.finish_reason == "length":
            # NOT parsed. See the module docstring: a truncated JSON body can
            # parse into something shorter and wrong. The REAL stop reason
            # still rides on the verdict, so the trace reads «truncated by
            # the provider», not «the gate silently decided» — and the span's
            # JSON output carries `"finish_reason": "length"` rather than a
            # fabricated one.
            log.warning("relevance gate answer was truncated; failing closed")
            return _fail_closed("truncated", finish_reason="length")
        verdict = parse_verdict(completion.content)
        if verdict.source == "model":
            # The PROVIDER's own stop reason, only when the model actually
            # answered. A fail-closed verdict (unparseable, bad_verdict)
            # must NOT carry a reason that would read as «the model answered
            # and stopped».
            return replace(verdict, finish_reason=completion.finish_reason)
        return verdict

    def _instruction(self) -> _Instruction | None:
        """`participation.md`, or `None` — and `None` FAILS CLOSED.

        The instruction is bind-mounted and hot-reloads, which is the point:
        the operator tunes it against live shadow verdicts without a rebuild.
        The cost of that is that an ordinary editing accident — a truncated
        save, a rename to `.bak`, a `applies_to` typo — is a live change, and
        the loader does not treat it as an error: a filter matching zero
        sections is not a failed scan, so `nora_doctrine_reload_failures_total`
        stays flat and the last-good machinery never engages.

        What survives an empty file is the code-owned half: `build_prompt`
        still appends the JSON contract and `render_context` still applies
        R-13's escaping and untrusted blocks. What does NOT survive is every
        criterion, the no_go bias, and "you are not Nora, do not draft" — the
        parts that decide the answer. A model handed the room and a bare JSON
        schema still returns a parseable verdict, which would land on
        `nora_relevance_gate_total` wearing `source="model"`: a gate with no
        instruction, reporting as a working one.

        That is the same substitution `_read_transcript` refuses one layer in.
        Refusing it here too means a typo in the instruction costs a quiet
        hour rather than an uninvited voice in every opted-in room.

        **`.doctrine_text`, never `.text`.** `.text` appends
        `<runtime_context>`, which carries the CANARY — the string LeakGuard
        treats as proof her prompt leaked. The gate has no use for it, runs on
        a different provider, and has no LeakGuard, so sending it there would
        put the tripwire itself on the wire on every quiet window.
        """
        try:
            doctrine = self._prompts.get(applies_to=GATE_AUDIENCE)
        except Exception:
            log.exception("relevance gate instruction read failed; failing closed")
            return None
        text = (doctrine.doctrine_text or "").strip()
        if not doctrine.sections or not _has_body(text):
            log.error(
                "relevance gate has NO INSTRUCTION: %s resolved to %d section(s). "
                "Every window will fail closed until it is restored.",
                GATE_AUDIENCE, len(doctrine.sections),
            )
            return None
        section = doctrine.sections[0]
        return _Instruction(
            text=text, version=section.version, sha256=doctrine.sha256,
        )

    async def _read_transcript(
        self, platform: str, conversation_id: str,
    ) -> tuple[TranscriptRow, ...] | None:
        """The window, or `None` — and `None` means FAIL CLOSED, not "empty
        room".

        Returning `()` and asking the model anyway would be the R-27
        substitution this module refuses: a Postgres outage would render
        `(no messages)`, the model would answer `no_go` about a room it had
        never seen, and the verdict would land on
        `nora_relevance_gate_total` wearing `source="model"` — an outage
        reading as a healthy gate declining a quiet estate. And nothing
        forces `no_go` on an empty transcript, so the same path could return
        GO and run a turn on a room the gate never read.

        A gate with no transcript has not measured anything. It says so.
        """
        try:
            rows = tuple(await self._records.recent(
                conversation_id=conversation_id, limit=self._transcript_limit,
            ))
        except Exception:
            log.exception("relevance gate transcript read failed; failing closed")
            self._metrics.inc(
                "nora_errors_total", stage="relevance_gate_context", platform=platform,
            )
            return None
        if self._media_join is not None:
            # Fail-SOFT, like the standing fold's: this runs before a cheap
            # gate call, and a join that cannot answer must cost a thinner
            # prompt rather than a verdict. `enrich` is total and counts its
            # own failures, so a permanently failing join is not silent.
            rows = await self._media_join.enrich(rows, path="relevance_gate")
        if self._persons is None:
            return rows
        return self._persons.name_transcript(rows, platform=platform)

    async def _read_standing(
        self, platform: str, conversation_id: str,
    ) -> tuple[str, int] | None:
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
        # The BODY AND ITS AGE. The instruction's fourth category — «a
        # commitment SHE made that is being followed up … only the standing
        # block can establish this» — is a question about WHEN, and so is
        # «anything the room has already moved past». Both are undecidable
        # against a paragraph with no time on it, so the age renders with the
        # body, as in the prefill's renderer.
        return (row.body, row.updated_at) if row is not None else None

    def _count(self, verdict: GateVerdict, platform: str, conversation_id: str) -> None:
        if verdict.source == "fail_closed":
            # NOT counted on `nora_relevance_gate_total`. A fail-closed no_go
            # in the same series as a chosen one is R-23's bug: the healthy
            # outcome and the broken one become indistinguishable exactly when
            # someone needs to tell them apart.
            self._metrics.inc(
                "nora_relevance_gate_errors_total",
                kind=verdict.error_kind or "call_failed", platform=platform,
                conversation_id=conversation_id,
            )
            return
        self._metrics.inc(
            "nora_relevance_gate_total", verdict=verdict.verdict, platform=platform,
            conversation_id=conversation_id,
        )


def parse_verdict(raw: str) -> GateVerdict:
    """`raw` -> a verdict, fail-closed.

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


def _verdict_json(verdict: GateVerdict) -> str:
    """The gate's verdict as the compact JSON the trace carries as `output`.

    Safe for the wire by construction: it contains only the verdict, the
    score, the source, and the provider's finish reason — nothing from the
    room. `None` score renders as absent (the same omission rule
    `annotate_generation` uses for empty strings: an absent field reads as
    "not stated").
    """
    out: dict[str, object] = {"verdict": verdict.verdict}
    if verdict.score is not None:
        out["relevance_score"] = verdict.score
    out["source"] = verdict.source
    if verdict.finish_reason:
        out["finish_reason"] = verdict.finish_reason
    return json.dumps(out, sort_keys=True)


def _score(value: object) -> float | None:
    """A number in [0, 1], or `None`.

    The score never blocks anything, so a missing or malformed
    one is not a failure of the verdict — it is a missing annotation, and
    rejecting an otherwise good verdict over it would let a reference field
    behave like enforcement.

    But it returns `None` and not `0.0`, because those are different facts
    and the only consumers are a sentence SHE reads and a number the operator
    TUNES on. `{"verdict": "go"}` with no score is a real answer at
    `reasoning_effort="none"`; rendering it as 0.00 is the least persuasive
    possible framing of a room the gate just admitted.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return max(0.0, min(1.0, float(value)))
