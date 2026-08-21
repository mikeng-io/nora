"""The Dreamer runtime: run ONE adaptor for ONE key, and return a three-state
outcome.

The Dreamer is an agent (`docs/superpowers/specs/2026-08-20-dreamer-as-an-agent-design.md`).
It owns the loop, the tool ceiling, the budget and its own doctrine, and it
knows nothing about lexemes, exchanges or facts — everything tier-specific
arrives through `DreamAdaptor`. The path is fixed and the ORDER is the design:

    adaptor declares an objective
      -> agent researches with READ-ONLY tools
      -> returns a structured result
      -> runtime VALIDATES the envelope
      -> runtime SCREENS the items          (R-18/R-28, before any tier type)
      -> adaptor PARSES them into its own values
      -> runtime STAMPS provenance
      -> adaptor COMMITS through its own sink

**There is no write tool and there must not be one.** A `write_fact` tool is an
act verb pointed at the database instead of at the group, and it would defeat
the screen: if the model writes directly there is no sink left to screen at.
The model proposes; code disposes — the same asymmetry the existing
consolidators already have, where `LexemeObserver` extracts and
`LanguageConsolidator` persists.

**Why the runtime cannot use `create_agent`, which no spec says.**
`create_agent` requires a `ReplyGuard` — a deliverer, a set of terminal verbs,
and the `tool_choice` relaxation — and `create_model_provider` hardcodes
`tool_choice="required"`, which that module documents as load-bearing. It IS
load-bearing, *for a turn that must end in `send_reply`*: under `required` the
model must call something on every pass, so a loop with no terminal verb can
never stop by itself, because the guard's `BeforeModelCallEvent` hook is the
only thing that ends a turn and it ends it by observing a terminal call
(DEC-21, DEC-32). The Dreamer has no terminal verb by construction — §4 of the
keystone removes every act verb and the terminal ones ARE act verbs — so it
needs its own guard (`DreamGuard`) and its own stopping condition: the model
declining to call anything, under `tool_choice="auto"`, inside a wall clock.

**Validation, and the line this module draws rather than defers.** `jsonschema`
is not a dependency and this does not add one — a new runtime dependency for one
call site is not proportionate. So: the RUNTIME validates the ENVELOPE (parses
as JSON, is an object, carries the adaptor's declared key, is a list under a
size cap, every element is a mapping) and the ADAPTOR's `accept` does the typed
parse, dropping a malformed item silently rather than raising — exactly what
`_parse_observations` / `_parse_item` already do. Recorded here because it is
the sort of decision that gets re-litigated by whoever adds the fourth adaptor.

**R-27 at this layer, and the mapping is the point.** A model response that
will not parse is `unavailable` («this batch was never actually read»), never
`nothing_found` («there was nothing in it») — because `_dream_once` must not
advance a watermark past a batch nobody read. That is exactly what
`ExtractionUnparseableError` protects today (`nora/language/consolidator.py`),
and the outcome type is where that guarantee lands once the adaptor contract
returns a value instead of raising.

**The instruction digest is read PER RUN, never cached at construction**
(DEC-128). The corpus hot-reloads off a bind mount; a digest captured at
`__init__` keeps naming the corpus as it was at process start — forever, and it
looks correct the whole time, because the wrongly-stamped rows are
indistinguishable from right ones. Read once at the top of a run so a reload
landing mid-run cannot split one batch's rows across two digests, and hash only
what was actually SENT.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from ..domain.models import TranscriptRow
from ..domain.ports import DreamAdaptor, Proposal
from ..obs.metrics import Metrics
from . import factory
from .dream_guard import DreamGuard
from .dream_screen import screen

log = logging.getLogger("nora.dreamer")

# The envelope's bounds. Both are REJECT-shaped rather than truncate-shaped for
# the text and DROP-shaped for the items, and the difference is deliberate: a
# response 20x the size of any legal one is evidence the model was doing
# something other than answering, so nothing in it is trustworthy; a response of
# the right shape carrying too many items is an ordinary over-eager batch, and
# keeping the first N of it is better than discarding a room's whole tick.
MAX_ENVELOPE_CHARS = 200_000
MAX_ITEMS = 64

DreamStatus = Literal["ok", "nothing_found", "unavailable"]


@dataclass(frozen=True)
class AdaptorBudget:
    """One adaptor's ceiling. PER ADAPTOR, never one global number: proving a
    fact is worth more model calls than glossing a word, and a single ceiling
    would either starve the first or overpay for the second.

    **`timeout_seconds` is not optional, and the reason is written up at
    `nora/app/composition.py:145-164` from the last time it was got wrong.**
    Unlike a tag turn's timeout, a hung Dreamer call is QUIET: `dream_loop`
    marks the key `running` and skips it on every subsequent tick
    (`nora_dream_skipped_total{reason="in_flight"}`) without ever raising, so
    the exponential backoff — which only engages on an exception — never fires
    and a hung provider is skipped forever rather than backed off. The wall
    clock wraps the WHOLE loop, the way `nora/engagement/stages/invoke.py` and
    `nora/app/fire.py` each wrap theirs.
    """

    turns: int
    total_tokens: int
    output_tokens: int
    timeout_seconds: float


@dataclass(frozen=True)
class DreamOutcome:
    """What one run did, in R-27's three states.

    `unavailable` is never absent and never silently a zero: the Dreamer has
    nobody waiting on it, so a run that failed and a run that found nothing
    look identical from outside unless the difference is carried here.

    `stop_reason` is Strands' own, passed through. When one of the three caps
    binds, Strands raises nothing and logs nothing — `AgentResult.stop_reason`
    is the ONLY channel that says a loop was cut off rather than finished
    (R-23, verified end to end against 1.50.2). A runtime that dropped it would
    report a budget-exhausted loop exactly the way it reports a healthy quiet
    one.
    """

    status: DreamStatus
    written: int
    instruction_sha256: str
    stop_reason: str | None = None


class DreamToolSource(Protocol):
    """The tools an adaptor may hold, resolved by NAME.

    A documented structural type rather than a `nora.domain.ports` Protocol,
    the same reason `nora.app.runtime._InstructionVersions` and the observers'
    `_ObjectiveSource` are: a shape with one consumer does not earn a shared
    contract suite.

    It exists because building a tool is not free of context — `load_transcript`
    needs a store and a conversation, `about` needs Honcho, `web_search` needs
    the provider — and the composition root is the only place that holds all of
    them. The runtime asks for names and gets objects; it never learns what is
    behind them, which is the same relationship the Dreamer already has to
    `SearchProvider`.

    **`read_only()` is the CEILING and `build` must enforce it.** An adaptor
    declares tool names because `nora.language` may not import `nora.agent`,
    which means nothing about the declaration itself stops it naming
    `send_reply`. `verify_tool_subsets` below is the startup refusal;
    `DreamerRuntime.run` re-checks per run so a subset that changed under a
    hot-reload cannot slip through as a quiet grant.
    """

    def read_only(self) -> frozenset[str]: ...

    def build(self, *, key: str, names: frozenset[str]) -> list[Any]: ...


class _InstructionSource(Protocol):
    """`DreamerInstructions`, seen from the runtime: one objective name in, the
    assembled text and its digest out. Narrowed to the one method used so a
    test can supply an assembled pair without a corpus on disk."""

    def for_objective(self, name: str) -> Any: ...


class _ModelSource(Protocol):
    """One model provider per run, for the same reason there is one per tag:
    `tool_choice` is per-invocation state and two runs overlap."""

    def create(self) -> Any: ...


class AdaptorToolError(ValueError):
    """An adaptor asked for a tool outside the read-only ceiling, or for one
    that does not exist. Raised at STARTUP by `verify_tool_subsets` — R-5's
    refuse-to-start shape, because a background agent holding an act verb is
    not a condition to discover at 3am from a metric."""


def verify_tool_subsets(
    adaptors: Sequence[DreamAdaptor], *, tools: DreamToolSource
) -> None:
    """Refuse to start if any adaptor's declared subset escapes the ceiling.

    The hard line — the Dreamer may look and may not act — is enforced in two
    places on purpose, and neither is redundant. Here it is a startup refusal,
    which is where a wiring mistake belongs (`policy.toml`'s own precedent: she
    refuses to start rather than run an ambiguous policy). In `run` it is a
    per-run re-check, because the objective corpus hot-reloads and an adaptor
    that is rebuilt or swapped between ticks would otherwise be measured once,
    at a moment that has passed.
    """
    ceiling = tools.read_only()
    for adaptor in adaptors:
        escaped = frozenset(adaptor.tools) - ceiling
        if escaped:
            raise AdaptorToolError(
                f"Dreamer adaptor {adaptor.name!r} declares tools outside the "
                f"read-only ceiling: {', '.join(sorted(escaped))}. The Dreamer "
                f"may look and may not act; a tool is grantable only if its "
                f"NativeTool row says read_only=True."
            )


def parse_envelope(raw: str, *, key: str) -> tuple[Mapping[str, object], ...] | None:
    """The model's raw text -> the declared key's items, or `None` for any
    shape that is not exactly `{"<key>": [ {...}, ... ]}`.

    `None` means the batch was NOT READ — prose, an empty body, truncated JSON
    and a wrong top-level key all land here, and the caller maps every one of
    them to `unavailable`. It is deliberately NOT the same value as an empty
    list, which is a well-formed «I looked and found nothing».

    Never raises. `_parse_observations`' own posture, and for the same reason:
    the alternative is one malformed response taking down a tick that four
    other adaptors were sharing.
    """
    if len(raw) > MAX_ENVELOPE_CHARS:
        log.warning(
            "dream envelope rejected: %d chars exceeds the %d cap",
            len(raw), MAX_ENVELOPE_CHARS,
        )
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    items = data.get(key)
    if not isinstance(items, list):
        return None
    if not all(isinstance(item, Mapping) for item in items):
        return None
    return tuple(items)


class DreamerRuntime:
    """One public entry point: `run`.

    Collaborators through `__init__` only, per the test-seam policy — no
    `*_fn` hooks, no `_for_test` parameters. `budgets` is a mapping from
    adaptor name to its ceiling; an adaptor with no entry gets `default_budget`
    rather than a `KeyError` or an unbounded loop, because an adaptor added
    later must be bounded by omission rather than unbounded by it.
    """

    def __init__(
        self,
        *,
        models: _ModelSource,
        instructions: _InstructionSource,
        tools: DreamToolSource,
        budgets: Mapping[str, AdaptorBudget],
        default_budget: AdaptorBudget,
        retry: factory.RetryPolicy,
        metrics: Metrics,
        model_name: str,
    ) -> None:
        self._models = models
        self._instructions = instructions
        self._tools = tools
        self._budgets = dict(budgets)
        self._default_budget = default_budget
        self._retry = retry
        self._metrics = metrics
        self._model_name = model_name

    def budget_for(self, adaptor_name: str) -> AdaptorBudget:
        return self._budgets.get(adaptor_name, self._default_budget)

    async def run(
        self, *, adaptor: DreamAdaptor, key: str, batch: Sequence[TranscriptRow]
    ) -> DreamOutcome:
        """One adaptor, one key, one bounded read-only loop.

        Every failure path returns `unavailable` and is COUNTED. Nothing here
        raises into the caller: `dream_loop`'s per-key try/except drives an
        exponential backoff against an external model provider, and an
        ordinary «the model returned prose» must not look like an outage to it.
        The exception is the tool-ceiling re-check, which is a wiring fault
        rather than a runtime one and is loud.
        """
        started = time.perf_counter()
        budget = self.budget_for(adaptor.name)
        sha = ""
        try:
            # PER RUN, and read before anything else so a mid-run reload cannot
            # split one batch's rows across two digests (DEC-128).
            instructions = self._instructions.for_objective(adaptor.objective)
            sha = instructions.sha256

            ceiling = self._tools.read_only()
            escaped = frozenset(adaptor.tools) - ceiling
            if escaped:
                # Loud, and `unavailable` rather than a raise: a background loop
                # must not take its own clock down, but this is a wiring fault
                # and reads as one.
                log.error(
                    "dream run refused: adaptor %s declares tools outside the "
                    "read-only ceiling: %s",
                    adaptor.name, ", ".join(sorted(escaped)),
                )
                return self._finish(
                    adaptor, "unavailable", 0, sha, None, started,
                )

            tools = self._tools.build(key=key, names=frozenset(adaptor.tools))
            guard = DreamGuard(
                adaptor=adaptor.name,
                budgets=_tool_budgets(tools, adaptor),
                metrics=self._metrics,
            )
            prompt = await adaptor.inputs(key=key, batch=batch)
            agent = factory.create_dream_agent(
                model=self._models.create(),
                system_prompt=instructions.text,
                tools=tools,
                guard=guard,
                trace_attributes={
                    # Its OWN span, findable in Langfuse rather than merged into
                    # Nora's — the provenance every claim about a run
                    # re-derives from.
                    "dreamer.adaptor": adaptor.name,
                    "dreamer.key": key,
                    "dreamer.objective": adaptor.objective,
                    "dreamer.instruction_sha256": sha,
                },
                retry=self._retry,
                agent_name=f"dreamer:{adaptor.name}",
            )
            result = await asyncio.wait_for(
                factory.invoke(
                    agent, prompt, invocation_state={},
                    limits=factory.build_limits(
                        output_tokens=budget.output_tokens,
                        turns=budget.turns,
                        total_tokens=budget.total_tokens,
                    ),
                ),
                timeout=budget.timeout_seconds,
            )
        except TimeoutError:
            log.error(
                "dream run timed out after %.0fs (adaptor=%s key=%s)",
                budget.timeout_seconds, adaptor.name, key,
            )
            return self._finish(adaptor, "unavailable", 0, sha, "timeout", started)
        except Exception:
            log.exception(
                "dream run failed (adaptor=%s key=%s)", adaptor.name, key,
            )
            return self._finish(adaptor, "unavailable", 0, sha, None, started)

        stop_reason = getattr(result, "stop_reason", None)
        if stop_reason is not None:
            self._metrics.inc(
                "nora_dream_stop_reason_total",
                adaptor=adaptor.name, reason=str(stop_reason),
            )

        raw = factory.final_text(result)
        items = parse_envelope(raw, key=adaptor.envelope_key)
        if items is None:
            # The batch was never actually read. `unavailable`, so the caller
            # holds the watermark — this is `ExtractionUnparseableError`'s
            # guarantee, expressed as a value instead of an exception.
            log.warning(
                "dream run produced no readable envelope (adaptor=%s key=%s, "
                "%d chars)", adaptor.name, key, len(raw),
            )
            return self._finish(
                adaptor, "unavailable", 0, sha, stop_reason, started,
            )

        if len(items) > MAX_ITEMS:
            # Capped, never silently: a cap nobody counts reads as «we wrote
            # everything it found».
            dropped = len(items) - MAX_ITEMS
            log.warning(
                "dream run returned %d items, keeping %d (adaptor=%s key=%s)",
                len(items), MAX_ITEMS, adaptor.name, key,
            )
            for _ in range(dropped):
                self._metrics.inc(
                    "nora_dream_screened_total", adaptor=adaptor.name, reason="cap",
                )
            items = items[:MAX_ITEMS]

        kept, _rejected = screen(
            adaptor=adaptor.name, items=items, metrics=self._metrics,
        )

        try:
            payloads = await adaptor.accept(key=key, items=kept)
            now_ms = int(time.time() * 1000)
            proposals = tuple(
                Proposal(
                    payload=payload,
                    instruction_sha256=sha,
                    model=self._model_name,
                    # The run that produced it — the handle a later `supersede`
                    # names when it retires this row. Derived from the tick
                    # rather than minted at random so a row can be tied back to
                    # a trace without a lookup table.
                    established_by=f"dream:{adaptor.name}:{key}:{now_ms}",
                )
                for payload in payloads
            )
            # Called even when `proposals` is empty, and that is not a
            # formality: an empty `commit` is R-27's `nothing_found` and the
            # sink may act on it — `LanguageConsolidator` rescores for an empty
            # batch because decay is time-driven. Skipping the call would drop
            # decay silently.
            written = await adaptor.commit(
                key=key, proposals=proposals, now_ms=now_ms,
            )
        except Exception:
            log.exception(
                "dream commit failed (adaptor=%s key=%s)", adaptor.name, key,
            )
            return self._finish(
                adaptor, "unavailable", 0, sha, stop_reason, started,
            )

        # `ok` vs `nothing_found` is about what the MODEL returned, not about
        # how many rows survived: a batch whose every item was screened out, or
        # whose every verdict was `not_covered`, was still a batch that was
        # read. Collapsing them would make a working screen look like a silent
        # model.
        status: DreamStatus = "ok" if items else "nothing_found"
        return self._finish(adaptor, status, written, sha, stop_reason, started)

    def _finish(
        self, adaptor: DreamAdaptor, status: DreamStatus, written: int,
        sha: str, stop_reason: str | None, started: float,
    ) -> DreamOutcome:
        self._metrics.inc(
            "nora_dream_run_total", adaptor=adaptor.name, status=status,
        )
        if written:
            self._metrics.inc(
                "nora_dream_written_total", adaptor=adaptor.name,
            )
        self._metrics.observe(
            "nora_dream_run_seconds", time.perf_counter() - started,
            adaptor=adaptor.name,
        )
        return DreamOutcome(
            status=status, written=written, instruction_sha256=sha,
            stop_reason=stop_reason,
        )


def _tool_budgets(tools: list[Any], adaptor: DreamAdaptor) -> dict[str, int]:
    """The per-tool call ceiling `DreamGuard` enforces under R-7.

    Read off the BUILT tools rather than off the adaptor's declared names, so a
    name the tool source declined to build is absent from `budgets` and is
    therefore vetoed by default-deny rather than admitted with a ceiling it
    cannot spend. `adaptor` is named only in the log line, which is what makes
    a mismatch findable rather than merely safe.
    """
    from .tools import NATIVE_TOOLS

    budgets: dict[str, int] = {}
    for tool in tools:
        name = getattr(tool, "tool_name", None) or getattr(tool, "__name__", "")
        spec = NATIVE_TOOLS.get(name)
        if spec is None:
            log.warning(
                "dream tool %r has no NATIVE_TOOLS row; it will be vetoed "
                "(adaptor=%s)", name, adaptor.name,
            )
            continue
        budgets[name] = spec.budget
    return budgets
