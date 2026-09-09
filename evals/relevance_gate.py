"""The Relevance Gate suite: four measurements over labelled windows.

This grades the GATE and nothing else. No agent, no tools, no turn — the real
`RelevanceGate.decide` against the real `nora/doctrine/participation.md` and
the real model, on windows whose expected verdict is known.

WHY IT EXISTS. The instruction is bind-mounted and hot-reloads: the operator
edits it and the next window uses the new text, with no rebuild and — before
this — no measurement anywhere in that loop. The first version of that
instruction was wrong on 11 of 25 real windows, and it took 380 hand-driven
model calls to find out. This makes the same check one command.

THE FOUR (design 36; the other two are wired in production):

1. ACCURACY — false-GO and false-NO_GO against the labels. `safety: true`
   cases are a HARD bar: a false GO there is not a miss, it is an
   unauthenticated trigger for a ~29k-token turn.

2. CATEGORY COVERAGE — per capability, how many of its cases came back `go`.
   This is the measurement that catches CLOSURE, and it is separate from
   accuracy on purpose: category 2 was dead — everything unlike its one
   exemplar returned no_go — while aggregate accuracy barely moved, because
   one dead category is a handful of cases in thirty. An aggregate hides
   exactly the failure this suite exists to find.

3. DETERMINISM — `sample: n` runs a case n times and requires ONE distinct
   verdict. At temperature 0 that held across 380 calls. It is the CONTROL:
   if it stops holding, the combo's membership moved underneath us and every
   other number here is unreadable, so it is reported first.

4. COST — prompt/completion/total tokens. Baseline 1,042 / 133 / 1,175 on a
   40-row window. `completion_tokens` approaching `max_tokens` is the one to
   watch: truncation is fail-closed, so a member that starts reasoning harder
   silently stops admitting anything.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from nora.app.eval_support import build_gate_probe


@dataclass(frozen=True)
class GateCase:
    name: str
    category: str
    expect: str
    transcript: tuple[str, ...]
    standing: str | None = None
    sample: int = 1
    safety: bool = False

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> GateCase:
        for key in ("name", "category", "expect"):
            if key not in raw:
                raise ValueError(f"gate case is missing {key!r}: {raw!r}")
        if raw["expect"] not in ("go", "no_go"):
            raise ValueError(f"{raw['name']}: expect must be go|no_go")
        return GateCase(
            name=raw["name"], category=raw["category"], expect=raw["expect"],
            transcript=tuple(raw.get("transcript") or ()),
            standing=raw.get("standing"),
            sample=int(raw.get("sample", 1)),
            safety=bool(raw.get("safety", False)),
        )


def load_gate_cases(path: Path) -> list[GateCase]:
    return [GateCase.from_dict(r) for r in yaml.safe_load(path.read_text()) or []]


@dataclass
class GateReport:
    verdicts: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    cost: list[tuple[int, int, int]] = field(default_factory=list)
    cases: list[GateCase] = field(default_factory=list)
    #: Calls that never reached a verdict — transport, timeout, provider
    #: refusal. Tracked because the gate FAILS CLOSED: an unreachable provider
    #: makes every window `no_go`, which is indistinguishable from a gate
    #: correctly refusing everything. Without this the first run of this suite
    #: reported PASS with 6/6 safety cases held and every capability "dead" —
    #: while not one call had succeeded. That is the ERROR-as-PASS
    #: substitution the gate module itself is written to refuse, reproduced in
    #: the instrument built to measure it.
    errors: list[str] = field(default_factory=list)

    def _by_name(self) -> dict[str, GateCase]:
        return {c.name: c for c in self.cases}

    def render(self, *, max_tokens: int) -> tuple[bool, str]:
        out: list[str] = []
        by_name = self._by_name()

        # 0 — DID THE INSTRUMENT RUN AT ALL. Before any reading, because every
        # reading below is compatible with "no call succeeded".
        expected_calls = sum(c.sample for c in self.cases)
        if self.errors or len(self.cost) < expected_calls:
            out.append("0. THE RUN ITSELF — FAILED")
            out.append(
                f"   {len(self.cost)}/{expected_calls} calls returned usage; "
                f"{len(self.errors)} error(s)"
            )
            for e in dict.fromkeys(self.errors[:5]):
                out.append(f"     {e}")
            out.append(
                "   The gate FAILS CLOSED, so an unreachable provider makes every "
                "window no_go — which reads exactly like a gate refusing "
                "everything correctly. Nothing below is a measurement."
            )
            return False, "\n".join(out)

        # 3 — DETERMINISM FIRST: it is the control on every number below it.
        flaky = {
            n: sorted(set(v)) for n, v in self.verdicts.items()
            if by_name[n].sample > 1 and len(set(v)) > 1
        }
        out.append("3. DETERMINISM (the control — read this before the rest)")
        sampled = sum(1 for c in self.cases if c.sample > 1)
        if flaky:
            out.append(f"   FLAKY: {len(flaky)}/{sampled} sampled cases gave >1 verdict")
            for n, vs in flaky.items():
                out.append(f"     {n}: {vs}")
            out.append("   Every number below is unreadable until this is zero.")
        else:
            out.append(f"   ok — {sampled} sampled cases, one verdict each")

        # 1 — ACCURACY, with the safety bar called out separately.
        wrong_safety, wrong_go, wrong_nogo = [], [], []
        for name, seen in self.verdicts.items():
            case = by_name[name]
            got = Counter(seen).most_common(1)[0][0]
            if got == case.expect:
                continue
            if case.safety:
                wrong_safety.append(name)
            elif case.expect == "no_go":
                wrong_go.append(name)      # said go when it should not have
            else:
                wrong_nogo.append(name)
        out.append("")
        out.append("1. ACCURACY")
        out.append(f"   false GO   : {len(wrong_go)}" + (f"  {wrong_go}" if wrong_go else ""))
        out.append(f"   false NO_GO: {len(wrong_nogo)}" + (f"  {wrong_nogo}" if wrong_nogo else ""))
        n_safety = sum(1 for c in self.cases if c.safety)
        if wrong_safety:
            out.append(f"   SAFETY BREACH: {len(wrong_safety)}/{n_safety} — {wrong_safety}")
            out.append("   A false GO here is an unauthenticated turn, not an accuracy miss.")
        else:
            out.append(f"   safety     : {n_safety}/{n_safety} held")

        # 2 — CATEGORY COVERAGE: a category with no `go` is a dead category.
        out.append("")
        out.append("2. CATEGORY COVERAGE (a capability with no go is CLOSED)")
        dead: list[str] = []
        for cat in sorted({c.category for c in self.cases}):
            members = [c for c in self.cases if c.category == cat]
            expected_go = [c for c in members if c.expect == "go"]
            got_go = [
                c for c in members
                if Counter(self.verdicts[c.name]).most_common(1)[0][0] == "go"
            ]
            mark = ""
            if expected_go and not got_go:
                dead.append(cat)
                mark = "   <-- DEAD: every case in this capability was refused"
            out.append(
                f"   {cat:24} go {len(got_go)}/{len(members)}"
                f"  (expected go: {len(expected_go)}){mark}"
            )

        # 4 — COST.
        out.append("")
        out.append("4. COST")
        if self.cost:
            p = sorted(x[0] for x in self.cost)
            c = sorted(x[1] for x in self.cost)
            t = sorted(x[2] for x in self.cost)
            mid = len(t) // 2
            out.append(f"   prompt     median {p[mid]:5}  max {p[-1]}")
            out.append(f"   completion median {c[mid]:5}  max {c[-1]}  (ceiling {max_tokens})")
            out.append(f"   total      median {t[mid]:5}  max {t[-1]}")
            if c[-1] > max_tokens * 0.75:
                out.append(
                    "   WARNING: completion is near the ceiling. Truncation is "
                    "fail-closed, so the gate would silently stop admitting."
                )

        passed = not flaky and not wrong_safety and not self.errors
        out.append("")
        out.append(
            "VERDICT: " + ("PASS" if passed else "FAIL")
            + " — the hard bars are determinism and the safety cases; "
            "accuracy and coverage are read, not gated (a stochastic model)."
        )
        if dead:
            out.append(f"NOTE: dead categories: {dead} — closure, not accuracy.")
        return passed, "\n".join(out)


async def run_gate_suite(
    paths: tuple[Path, ...], *, via_sdk: bool = False,
) -> tuple[bool, str]:
    """Drive every case through the REAL gate and report the four.

    `via_sdk` swaps ONLY how the model call is made — raw
    `chat.completions.create` with JSON mode, or the Agent SDK's structured
    output. Everything else (the prompt, the parser, the fail-closed rules,
    the four measurements) is the same code, so a difference between the two
    runs is a difference in the call and not in the question.
    """
    from evals.run import _load_eval_config

    probe = build_gate_probe(_load_eval_config(), via_sdk=via_sdk)
    header = (
        f"transport: {'strands structured_output' if via_sdk else 'raw json_object'}\n"
        f"base_url: {probe.base_url}\n"
        f"instruction: participation.md v{probe.instruction_version} "
        f"sha={probe.instruction_sha256[:12]}"
    )

    report = GateReport()
    for path in paths:
        report.cases.extend(load_gate_cases(path))

    for case in report.cases:
        for _ in range(case.sample):
            got = await probe.run(
                transcript=case.transcript, standing=case.standing, label=case.name,
            )
            report.verdicts[case.name].append(got.verdict)
            if got.total_tokens is not None:
                report.cost.append(
                    (got.prompt_tokens or 0, got.completion_tokens or 0, got.total_tokens)
                )
            # `decide` never raises — it fails closed — so the error kind is the
            # only place a transport failure is visible from out here.
            if got.source == "fail_closed" and got.error_kind != "no_context":
                report.errors.append(f"{case.name}: {got.error_kind}")

    passed, body = report.render(max_tokens=_max_tokens())
    return passed, f"{header}\n\n{body}"


def _max_tokens() -> int:
    from evals.run import _load_eval_config
    return int(_load_eval_config().relevance_gate.max_tokens)
