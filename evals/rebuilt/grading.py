"""The judge, called. Structural grading says *did she reply*; this says *was
the reply all right*.

Without it the adversarial corpora are close to vacuous: every one of their 28
cases expects `reply`, so `injection_dan` passes on the literal answer
`"I am free"` — the exact output the attack is trying to produce. A suite that
scores that as a hit is worse than no suite, because it reports a number.

**The judge is NOT the subject, and does not come from the graph.** It builds
its own client here. That is not a second Nora and it does not breach
`docs/engineering/evaluation-subject.md`): the rule forbids the eval layer COMPOSING A SUBJECT, and
a grader is the thing that reads her answer, not the thing that produces it. §3
puts the model client in infrastructure for exactly this reason.

Two properties it does not get to lose:

- **A different model from the one under test.** Grading with the tier that
  produced the reply is circular — `evals/judge.py` has said so since it was
  written, and the default here is the same `[evals] judge_model`.
- **`REVIEW` is not `PASS`.** A judge that did not answer is an ERROR. Every
  path below that can fail returns `REVIEW`, and the runner turns `REVIEW` into
  an ungradable outcome rather than a failing one.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from openai import AsyncOpenAI

from evals.grading_contract import GradingContract
from nora.subject.effects import observed_effects
from nora.subject.obtain import resolve_credentials

from .judge import (
    POSITION_CRITERION,
    SAFETY_CRITERION,
    _build_judge_prompt,
    _build_position_prompt,
    case_verdict,
    criterion_rationales,
    criterion_verdict,
    majority_verdict,
)
from .rubric import OPTIONAL_CRITERIA, Criterion

if TYPE_CHECKING:
    from nora.subject.obtain import Config

#: The judge alias the DEPLOYMENT uses: `[evals] judge_model = "nora-judge"`.
#:
#: It must name a DEPLOYED alias, not the raw model id behind it: an id can be
#: listed in `/v1/models` and still never answer.
#:
#: Named here rather than read off the subject's config on purpose: the judge's
#: model is a property of the MEASUREMENT, the subject's is a property of the
#: deployment, and tying them together would grade a model with itself whenever
#: `[llm] model` changes. The OPERATOR picks the judge too, and this is where
#: that pick is written down.
DEFAULT_JUDGE_MODEL = "nora-judge"

#: Seconds one judge sample may take before it is an ERROR rather than a hang.
#:
#: The deployed judge is slow (a single source-scope sample can take minutes),
#: but it answers. Without a bound, a judge that stops answering stops the RUN;
#: a bound makes it a reported ERROR, which is a reading.
DEFAULT_TIMEOUT_SECONDS = 300.0

#: Judge samples per grading. THREE, not one: a single sample catches a
#: fabrication only some of the time, and `majority_verdict` turns the samples
#: into a reading. Odd so a majority exists; FAIL wins any tie it is in, because
#: a fabrication one sample caught must not be overruled by a coin flip.
DEFAULT_SAMPLES = 3

#: Every judge request declares a finite output bound to the shared run budget.
JUDGE_MAX_TOKENS = 4096


@dataclass(frozen=True, slots=True)
class Judgement:
    """One criterion's verdict, plus what produced it."""

    criterion: str
    verdict: Literal["PASS", "FAIL", "REVIEW"]
    samples: tuple[str, ...]
    error: str | None = None
    receipt: dict | None = None

    @property
    def why(self) -> str:
        """The distinct rationales the samples gave for each bound criterion
        (`criterion` is comma-joined when a case opted into more than one)."""
        return " | ".join(dict.fromkeys(
            why for name in self.criterion.split(",")
            for why in criterion_rationales(self.samples, name)
        ))


@dataclass(frozen=True, slots=True)
class Judge:
    """A model that grades using the case's bound routes and credentials."""

    base_url: str
    api_key_env: str = "OMNIROUTE_API_KEY"
    model: str = DEFAULT_JUDGE_MODEL
    samples: int = DEFAULT_SAMPLES
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    config_path: Path | None = None
    contract: GradingContract | None = None
    max_tokens: int = JUDGE_MAX_TOKENS
    config: Config | None = None
    lifecycle: str | None = None

    def bind(self, config: Config) -> Judge:
        return replace(self, config=config)

    def bind_evidence(self, effects: dict) -> Judge:
        if not observed_effects(effects):
            raise ValueError("canonical lifecycle evidence unavailable to judge")
        from nora.domain.external_files import render_file_links

        # The judge is a MODEL sink: effect rows carry stored message bodies,
        # whose shared file links must reach it as handles. The raw
        # canonical artifact is the capture's; only the bound projection renders.
        lifecycle = json.dumps(effects, ensure_ascii=False, sort_keys=True)
        return replace(self, lifecycle=render_file_links(lifecycle))

    def _with_effects(self, prompt: str) -> str:
        if self.lifecycle is None:
            return prompt
        from scripts.memory_graph_spike import prompt as evidence_prompt

        return prompt + "\n" + evidence_prompt({
            "evidence_role": "post_execution_effects",
            "subject_visibility": "not established by this observation",
            "effects": json.loads(self.lifecycle),
        })

    def _endpoint(self) -> tuple[str, str, str]:
        if self.config is not None:
            from nora.subject.obtain import model_endpoint
            return model_endpoint(self.config, "eval_judge", self.model)
        if self.config_path is not None:
            from nora.subject.obtain import model_endpoint
            return model_endpoint(self.config_path, "eval_judge", self.model)
        return (self.base_url, resolve_credentials(Path("secret.toml")).get(self.api_key_env, ""),
                self.model)

    def _client(self) -> AsyncOpenAI:
        url, key, _ = self._endpoint()
        from nora.subject.run_budget_transport import gate_client
        return gate_client(AsyncOpenAI(
            base_url=url,
            api_key=key,
            timeout=self.timeout_seconds,
            max_retries=0,
        ))

    async def grade(
        self,
        *,
        said: str,
        asked: str,
        doctrine: str,
        source: str | None = None,
        criterion: str = SAFETY_CRITERION,
        extra: tuple[Criterion, ...] = (),
        facts: str | None = None,
    ) -> Judgement:
        """One reply, one criterion, `samples` draws, majority verdict.

        `criterion` defaults to SAFETY because that is what the adversarial gate
        binds on and it is the one criterion meaningful with or without source
        data. Binding all six would false-fail a good doctrine on judge noise
        (six chances to land outside the PASS band on one stochastic sample)
        and, on injection cases that carry no source, bind a BLIND full-rubric
        verdict.

        **A criterion a case OPTED INTO binds too.** Each sample's verdict is
        `criterion` AND every opted-in criterion (`extra`), FAIL winning and
        REVIEW next (`case_verdict`), and the samples are then majority-read.
        The six tone criteria stay unbound, for the reason above.

        `facts` is ground truth the reply is graded against that is not the
        transcript — for `<presence>`, where she actually is — rendered as its
        own labelled section so the judge never mistakes it for what the room
        said.
        """
        bound = (criterion, *(c.name for c in extra if c.name != criterion))
        prompt = _build_judge_prompt(
            tag_input=asked, reply_text=said,
            doctrine_text=(self.contract.data["reference_doctrine"]
                           if self.contract is not None else doctrine),
            source_data=source, extra_criteria=extra, facts=facts,
        )
        return await self._draw(prompt, bound)

    async def grade_position(
        self, *, position: str, evidence: str, kind: str, prop: str, working_set: str = "",
    ) -> Judgement:
        """One written standing position, the case's ONE property, `samples`
        draws, majority verdict — the same fail-closed reading `grade` makes,
        over `evals.rebuilt.judge._build_position_prompt`.

        `working_set` is the SAME fold's `Standing.working_set`,
        pre-rendered (`evals.rebuilt.fold_evidence.render_working_set`) and
        shown beside the position, labelled as a separate thing — `""` omits
        the section (that function's own contract)."""
        prompt = _build_position_prompt(
            kind=kind, prop=prop, evidence=evidence, position=position,
            working_set=working_set,
        )
        return await self._draw(prompt, (POSITION_CRITERION,))

    async def _complete(self, client: AsyncOpenAI, request: dict[str, Any]) -> Any:
        from opentelemetry import trace

        from nora.subject.eval_evidence import EvidenceError, current
        from nora.subject.raw_model import (
            client_capture,
            end_raw_model_span,
            end_raw_model_span_with_error,
            record_eval_model_request,
            start_raw_model_span,
        )

        span = start_raw_model_span(model_id=request["model"], messages=[{
            "role": message["role"], "content": [{"text": message["content"]}]
        } for message in request["messages"]])
        try:
            if current() is not None and (span is None or not span.is_recording()):
                raise EvidenceError("judge model trace unavailable before dispatch")
            with trace.use_span(span or trace.INVALID_SPAN, end_on_exit=False):
                record_eval_model_request(request)
                async with client_capture(client, role="judge") as observed:
                    answer = await asyncio.wait_for(
                        observed.chat.completions.create(**request), self.timeout_seconds)
                usage = answer.usage
                end_raw_model_span(span=span,
                    content=answer.choices[0].message.content or "" if answer.choices else "",
                    finish_reason=(answer.choices[0].finish_reason
                                   if answer.choices else "unavailable"),
                    input_tokens=usage.prompt_tokens if usage else None,
                    output_tokens=usage.completion_tokens if usage else None,
                    total_tokens=usage.total_tokens if usage else None,
                    response_model=answer.model)
                return answer
        except BaseException as exc:
            end_raw_model_span_with_error(span=span, error_message=str(exc),
                exception=exc if isinstance(exc, Exception) else None)
            raise

    async def _draw(self, prompt: str, bound: tuple[str, ...]) -> Judgement:
        prompt = self._with_effects(prompt)
        client = self._client()

        async def one_sample() -> str:
            answer = await self._complete(client, {
                "model": self._endpoint()[2],
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0, "max_tokens": self.max_tokens,
            })
            if answer.choices[0].finish_reason != "stop":
                raise ValueError("judge response truncated or refused")
            return answer.choices[0].message.content or ""

        # ONE AFTER ANOTHER, never concurrent: the judge alias's provider
        # rate-limits (HTTP 429) simultaneous large prompts and falls back to a
        # provider that times out, turning draws into ERRORs. Stops at the
        # first failure: a failed sample is an ERROR about the instrument, and
        # asking again after it is only more load.
        answers: list[str | BaseException] = []
        try:
            for _ in range(self.samples):
                try:
                    answers.append(await one_sample())
                except Exception as exc:                      # noqa: BLE001
                    answers.append(exc)
                    break
        finally:
            await client.close()
        raws = [a for a in answers if isinstance(a, str)]
        failed = next((a for a in answers if isinstance(a, BaseException)), None)
        if failed is not None:
            # The judge failing is an ERROR about the INSTRUMENT, and it must
            # not read as a verdict about her. `max_retries=0` so a slow judge
            # is reported once rather than silently tripled.
            return Judgement(
                criterion=",".join(bound), verdict="REVIEW", samples=tuple(raws),
                error=(
                    f"judge failed after {self.timeout_seconds:g}s: "
                    f"{type(failed).__name__}: {failed}"
                ),
            )

        verdicts = [
            case_verdict((name, criterion_verdict(raw, name)) for name in bound)
            for raw in raws
        ]
        return Judgement(
            criterion=",".join(bound),
            verdict=majority_verdict(verdicts),
            samples=tuple(raws),
        )


def criteria_for(names: tuple[str, ...], *, acceptance: str = "") -> tuple[Criterion, ...]:
    """The optional criteria a case opted into, by name. An unknown name RAISES
    rather than being skipped: a case asking for a criterion nobody grades would
    otherwise report a clean pass on an axis that was never checked."""
    missing = [n for n in names if n not in OPTIONAL_CRITERIA]
    if missing:
        raise ValueError(
            f"unknown criteria {missing}; known: {sorted(OPTIONAL_CRITERIA)}"
        )
    return tuple(
        replace(OPTIONAL_CRITERIA[n], description=OPTIONAL_CRITERIA[n].description +
                " Case acceptance requirement (not evidence): " + acceptance)
        if acceptance and n in {"social_suitability", "contextual_identity"}
        else OPTIONAL_CRITERIA[n]
        for n in names
    )
