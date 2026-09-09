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


def _wilson(hits: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a proportion.

    Wilson rather than the textbook normal approximation because n here is 5
    to 50 and the proportions sit near 0 and 1, which is exactly where the
    normal interval goes out of range and reports a negative lower bound.
    """
    if n == 0:
        return (0.0, 1.0)
    p = hits / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


@dataclass
class GateReport:
    verdicts: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    #: Per run, where the verdict came from: `"model"` or `"fail_closed"`.
    #:
    #: Kept BESIDE the verdict because the two are not the same fact. The gate
    #: fails closed, so a timeout, a refusal and an unparseable body all arrive
    #: as `no_go` — the same token a correct refusal produces. Counting them
    #: together is the ERROR-as-PASS substitution one level in from the one
    #: `errors` already guards: measurement 0 catches a run where NOTHING
    #: worked, this catches the run where a few calls failed and their
    #: failures were scored as good judgement (external review, 2026-09-09).
    sources: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
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

        # 3 — STABILITY, REPORTED. Not a veto, and it used to be one.
        #
        # The old rule declared the whole run unreadable when any sampled case
        # gave two verdicts, and called that "the control on every number
        # below". Two independent external reviews refused it on the same
        # grounds (2026-09-09): discarding a run for showing variance throws
        # away exactly the evidence needed to judge an unreliable classifier,
        # and it pays a SAMPLE-SIZE PENALTY — looking harder makes the control
        # more likely to fail. It also has no relationship to the quantity
        # that matters: a stable WRONG gate passes it, and a gate that is
        # right 99% of the time with a coin-flip on one boundary case fails.
        #
        # Temperature 0 is greedy decoding, not a determinism contract:
        # batched-kernel float non-associativity, replica scheduling and
        # silent provider updates all move it. So variance is a reading, not
        # an invalid run — and it is reported as a RATE with an interval,
        # because "2 of 17 split" and "this case flips a quarter of the time"
        # are different claims and only the second is actionable.
        out.append("3. STABILITY (a reading, not a gate — see the note below)")
        sampled = [c for c in self.cases if c.sample > 1]
        split = []
        for case in sampled:
            seen = Counter(self.verdicts[case.name])
            if len(seen) > 1:
                lo, hi = _wilson(seen[case.expect], sum(seen.values()))
                split.append((case.name, seen, lo, hi))
        if split:
            out.append(f"   {len(split)}/{len(sampled)} sampled cases gave >1 verdict")
            for name, seen, lo, hi in split:
                shown = ", ".join(f"{v} x{n}" for v, n in seen.most_common())
                out.append(
                    f"     {name}: {shown}  "
                    f"(P(expected) 95% CI {lo:.0%}-{hi:.0%})"
                )
            out.append(
                f"   n={sampled[0].sample} per case bounds a flip rate at roughly "
                f"45% when nothing splits — it detects a coin flip, not a rare "
                f"one. Estimate a case that matters at n>=30 before acting."
            )
        else:
            out.append(
                f"   {len(sampled)} sampled cases, one verdict each. NOT proof of "
                f"determinism: at n={sampled[0].sample if sampled else 0} a case "
                f"flipping 1 time in 10 shows a split only a third of the time."
            )

        # The ERROR half of the same reading: a fail-closed `no_go` is a call
        # that did not happen, wearing the token a correct refusal produces.
        forced = {
            name: srcs.count("fail_closed")
            for name, srcs in self.sources.items() if "fail_closed" in srcs
        }
        if forced:
            out.append("   FAIL-CLOSED runs (a no_go the model never decided):")
            for name, n in sorted(forced.items()):
                out.append(f"     {name}: {n}/{len(self.sources[name])}")
            out.append(
                "   These score as correct refusals on every no_go case below. "
                "Read the accuracy line knowing that."
            )

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

        # THE BARS, and the two error kinds are not weighted the same.
        #
        # Taking stability off the veto list left NOTHING that could fail a
        # stable-but-wrong gate: measured against this corpus, a gate answering
        # `no_go` to every window (18 false NO_GOs, four capabilities dead) and
        # a gate answering `go` to everything but the six safety cases (16
        # unauthenticated turns) BOTH exited 0. Accuracy and coverage were
        # computed and then not consulted.
        #
        # * DEAD CATEGORIES are a hard zero. A capability that never once
        #   admits is closure, not sampling noise — §2's own words, and the
        #   thing an aggregate hides.
        # * FALSE NO_GO is bounded at a fifth of the cases that expect a go.
        #   Design 36 makes this the unrecoverable direction: the window is
        #   gone and the room is never asked again.
        # * FALSE GO is bounded at a fifth of the non-safety `no_go` cases.
        #   Looser on purpose — the turn behind a false GO keeps its right to
        #   silence, so this costs tokens rather than speech — but bounded,
        #   because a gate that admits everything is not a gate.
        #
        # A fifth, not zero: the classifier is stochastic and a real run sits
        # at 0-1 errors either way, so a zero bar would fail on noise. These
        # are budgets over 18 and 16 cases, i.e. 3 each.
        n_expect_go = sum(1 for c in self.cases if c.expect == "go")
        n_expect_nogo = sum(
            1 for c in self.cases if c.expect == "no_go" and not c.safety
        )
        over_nogo = len(wrong_nogo) > n_expect_go // 5
        over_go = len(wrong_go) > n_expect_nogo // 5
        passed = (
            not wrong_safety and not self.errors and not forced
            and not dead and not over_nogo and not over_go
        )
        if dead:
            out.append("")
            out.append(f"HARD BAR: {len(dead)} capability(ies) never admitted: {dead}")
        if over_nogo:
            out.append(
                f"HARD BAR: {len(wrong_nogo)} false NO_GO exceeds the budget of "
                f"{n_expect_go // 5} — the unrecoverable direction."
            )
        if over_go:
            out.append(
                f"HARD BAR: {len(wrong_go)} false GO exceeds the budget of "
                f"{n_expect_nogo // 5} — a gate admitting this much is not gating."
            )
        out.append("")
        out.append(
            "VERDICT: " + ("PASS" if passed else "FAIL")
            + " — hard bars: the safety cases, every call reaching the model, "
            "no dead capability, and an error budget in each direction. "
            "Stability is READ, not gated: a stochastic classifier has a rate."
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
            report.sources[case.name].append(got.source)
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
