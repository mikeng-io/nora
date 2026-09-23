"""The judge, called. Structural grading says *did she reply*; this says *was
the reply all right*.

Without it the adversarial corpora are close to vacuous: every one of their 28
cases expects `reply`, so `injection_dan` passes on the literal answer
`"I am free"` — the exact output the attack is trying to produce. A suite that
scores that as a hit is worse than no suite, because it reports a number.

**The judge is NOT the subject, and does not come from the graph.** It builds
its own client here. That is not a second Nora and it does not breach
`01-boundaries` §5(1): the rule forbids the eval layer COMPOSING A SUBJECT, and
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

import os
from dataclasses import dataclass
from typing import Literal

from openai import AsyncOpenAI

from .judge import (
    POSITION_CRITERION,
    SAFETY_CRITERION,
    _build_judge_prompt,
    _build_position_prompt,
    criterion_rationales,
    criterion_verdict,
    majority_verdict,
)
from .rubric import OPTIONAL_CRITERIA, Criterion

#: The judge alias the DEPLOYMENT uses — norty's `config.toml` line 840,
#: `[evals] judge_model = "nora-judge"`.
#:
#: It must name a DEPLOYED alias, and this was wrong: the default was
#: `ollamacloud/glm-5.3`, the raw model id behind the alias, copied out of
#: `evals/judge.py`'s comment about which tier the operator chose. That id IS
#: in `/v1/models` — and `/v1/models` is not proof an id works. Measured: a
#: judged run of `source_scope` returned 4 errors on 4 cases and took 1,654
#: seconds, which is what a judge that never answers looks like.
#:
#: Named here rather than read off the subject's config on purpose: the judge's
#: model is a property of the MEASUREMENT, the subject's is a property of the
#: deployment, and tying them together is how a suite ends up grading a model
#: with itself the first time somebody changes `[llm] model`. But "not the
#: subject's" does not mean "not the deployment's" — the OPERATOR picks the
#: judge too, and this is where his pick is written down.
DEFAULT_JUDGE_MODEL = "nora-judge"

#: Seconds one judge sample may take before it is an ERROR rather than a hang.
#:
#: MEASURED 2026-09-18 against the deployed `nora-judge`: ONE sample on a
#: source-scope case took **211 seconds** and returned correct, parseable JSON —
#: it scored `no_overreach_or_fabrication` at 0.1, which is the R-53 failure
#: caught. The judge works; it is just slow, and three samples across 150 cases
#: at that rate is about 26 HOURS.
#:
#: Without a bound, a judge that stops answering stops the RUN — `grade` had no
#: timeout at all, so the only limit was whatever the HTTP client defaulted to.
#: A bound makes a slow judge a reported ERROR, which is a reading; a hang is
#: not.
DEFAULT_TIMEOUT_SECONDS = 300.0

#: Judge samples per grading. THREE, not one: the judge caught a fabrication
#: 1/3 on one run and 3/3 on another, and `majority_verdict` is what turns that
#: into a reading. Odd so a majority exists; FAIL wins any tie it is in, because
#: a fabrication one sample caught must not be overruled by a coin flip.
DEFAULT_SAMPLES = 3


@dataclass(frozen=True, slots=True)
class Judgement:
    """One criterion's verdict, plus what produced it."""

    criterion: str
    verdict: Literal["PASS", "FAIL", "REVIEW"]
    samples: tuple[str, ...]
    error: str | None = None

    @property
    def why(self) -> str:
        """The distinct rationales the samples gave for this criterion."""
        return " | ".join(dict.fromkeys(criterion_rationales(self.samples, self.criterion)))


@dataclass(frozen=True, slots=True)
class Judge:
    """A model that grades. Built once per run, not per case."""

    base_url: str
    api_key_env: str = "OMNIROUTE_API_KEY"
    model: str = DEFAULT_JUDGE_MODEL
    samples: int = DEFAULT_SAMPLES
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    def _client(self) -> AsyncOpenAI:
        return AsyncOpenAI(
            base_url=self.base_url,
            api_key=os.environ.get(self.api_key_env, ""),
            timeout=self.timeout_seconds,
            max_retries=0,
        )

    async def grade(
        self,
        *,
        said: str,
        asked: str,
        doctrine: str,
        source: str | None = None,
        criterion: str = SAFETY_CRITERION,
        extra: tuple[Criterion, ...] = (),
    ) -> Judgement:
        """One reply, one criterion, `samples` draws, majority verdict.

        `criterion` defaults to SAFETY because that is what the adversarial gate
        binds on and it is the one criterion meaningful with or without source
        data. Binding all six made a good doctrine false-fail on judge noise —
        six criteria are six chances to land outside the PASS band on one
        stochastic sample — and on the injection cases, which carry no source,
        it bound a BLIND full-rubric verdict.
        """
        prompt = _build_judge_prompt(
            tag_input=asked, reply_text=said, doctrine_text=doctrine,
            source_data=source, extra_criteria=extra,
        )
        return await self._draw(prompt, criterion)

    async def grade_position(
        self, *, position: str, evidence: str, kind: str, prop: str,
    ) -> Judgement:
        """One written standing position, the case's ONE property, `samples`
        draws, majority verdict — the same fail-closed reading `grade` makes,
        over `evals.rebuilt.judge._build_position_prompt`."""
        prompt = _build_position_prompt(
            kind=kind, prop=prop, evidence=evidence, position=position,
        )
        return await self._draw(prompt, POSITION_CRITERION)

    async def _draw(self, prompt: str, criterion: str) -> Judgement:
        raws: list[str] = []
        client = self._client()
        try:
            for _ in range(self.samples):
                try:
                    answer = await client.chat.completions.create(
                        model=self.model,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0,
                    )
                except Exception as exc:                      # noqa: BLE001
                    # The judge failing is an ERROR about the INSTRUMENT, and it
                    # must not read as a verdict about her. `max_retries=0` so a
                    # slow judge is reported once rather than silently tripled —
                    # at 211s a sample, a retry is four minutes nobody asked for.
                    return Judgement(
                        criterion=criterion, verdict="REVIEW", samples=tuple(raws),
                        error=(
                            f"judge failed after {self.timeout_seconds:g}s: "
                            f"{type(exc).__name__}: {exc}"
                        ),
                    )
                raws.append(answer.choices[0].message.content or "")
        finally:
            await client.close()

        verdicts = [criterion_verdict(raw, criterion) for raw in raws]
        return Judgement(
            criterion=criterion,
            verdict=majority_verdict(verdicts),
            samples=tuple(raws),
        )


def criteria_for(names: tuple[str, ...]) -> tuple[Criterion, ...]:
    """The optional criteria a case opted into, by name. An unknown name RAISES
    rather than being skipped: a case asking for a criterion nobody grades would
    otherwise report a clean pass on an axis that was never checked."""
    missing = [n for n in names if n not in OPTIONAL_CRITERIA]
    if missing:
        raise ValueError(
            f"unknown criteria {missing}; known: {sorted(OPTIONAL_CRITERIA)}"
        )
    return tuple(OPTIONAL_CRITERIA[n] for n in names)
