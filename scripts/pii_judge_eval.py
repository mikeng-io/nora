#!/usr/bin/env python3
"""Synthetic calibration for the identifier detector's commit judge.

Everything here is GENERATED from a seed: names are drawn from generic pools,
numbers and ids from a seeded RNG, coordinates around random points. Nothing
is copied from a real record, and the source holds no identifier-shaped
literal (the detector scans this file like any other). The same generator
gives the unit tests their neutral fixtures.

Each labelled case is one LINE placed in a small COMMIT (the judge reads
commits): the line added to its file between two lines of ordinary code, or,
for a message case, the line as the commit message. Label 1 = the line carries
personal data of a private individual (a realistic contact, account id,
address or location, with or without a name); 0 = it does not (code, prose,
config, public organisations, obvious placeholders). A realistic value inside
a TEST FIXTURE is labelled 1 on purpose: the incident this detector exists for
was production rows pasted into a fixture (docs/CONVENTIONS.md), so "it is in
a test" is not evidence that it is fake. The larger commit-level study (66
commits, dev/held-out split) is the commit-judge spike of 2026-09-26; its
thresholds are the ones scripts/pii_judge.py ships.

    python3 scripts/pii_judge_eval.py cases                # print the set
    python3 scripts/pii_judge_eval.py run laya --runs 1 --out results.json
    python3 scripts/pii_judge_eval.py run jev --runs 3 --out r.json  # SYNTHETIC commits to TypeSafe
    python3 scripts/pii_judge_eval.py bench laya --sizes 50,500,5000 --out bench.json

`laya` runs through `NORA_PII_LAYA_PYTHON` (an interpreter with laya-mlx) and
`NORA_PII_LAYA_MODEL`, exactly as the hook does.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import string
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FIRST = [
    "Priya",
    "Kenji",
    "Aisha",
    "Mateo",
    "Ingrid",
    "Tomasz",
    "Nadia",
    "Oluwaseun",
    "Sofia",
    "Rahul",
    "Chloe",
    "Diego",
    "Hana",
    "Lars",
    "Amara",
    "Yusuf",
    "Elena",
    "Marcus",
    "Leilani",
    "Viktor",
]
LAST = [
    "Raman",
    "Sato",
    "Khan",
    "Alvarez",
    "Lindgren",
    "Kowalski",
    "Haddad",
    "Adeyemi",
    "Moretti",
    "Iyer",
    "Dubois",
    "Castillo",
    "Kimura",
    "Nyberg",
    "Okafor",
    "Demir",
    "Petrova",
    "Whitfield",
]
CJK_SURNAME = "陳林黃張李王吳劉蔡楊許鄭謝郭"
CJK_GIVEN = "嘉欣偉明詠詩志豪家俊美玲子朗曉峰慧敏凱琳浩然"
DOMAINS = ["gmail.com", "outlook.com", "yahoo.co.uk", "icloud.com", "proton.me", "hotmail.com"]
STREETS = [
    "Hskyhlu Road",
    "Nathan Road",
    "Maple Avenue",
    "Queen Street",
    "Lakeside Drive",
    "Rue Lepic",
]
# (country code, national significant number length, leading mobile digits)
PHONE_PLANS = [
    ("852", 8, "5679"),
    ("44", 10, "7"),
    ("81", 10, "789"),
    ("1", 10, "2345678"),
    ("65", 8, "89"),
    ("61", 9, "4"),
    ("49", 11, "1"),
    ("91", 10, "6789"),
]


class Synth:
    """Seeded, realistic-looking, invented values."""

    def __init__(self, seed: int = 283):
        self.r = random.Random(seed)  # noqa: S311 - reproducible fixtures, not secrets

    def _digits(self, n: int) -> str:
        while True:
            d = "".join(self.r.choice(string.digits) for _ in range(n))
            # avoid the detector's placeholder shapes: these must look REAL
            if len(set(d)) > 3 and not any(c * 4 in d for c in string.digits) and "123" not in d:
                return d

    def name(self) -> str:
        return f"{self.r.choice(FIRST)} {self.r.choice(LAST)}"

    def cjk_name(self) -> str:
        return self.r.choice(CJK_SURNAME) + "".join(self.r.sample(CJK_GIVEN, 2))

    def phone(self, cc: str | None = None, style: str = "e164") -> str:
        plan = next(p for p in PHONE_PLANS if p[0] == cc) if cc else self.r.choice(PHONE_PLANS)
        code, n, lead = plan
        nsn = self.r.choice(lead) + self._digits(n - 1)
        if style == "e164":
            return f"+{code}{nsn}"
        if style == "spaced":
            return f"+{code} {nsn[:4]} {nsn[4:]}"
        if style == "dashed":
            return f"+{code}-{nsn[:4]}-{nsn[4:]}"
        if style == "local":
            return nsn
        if style == "jid":
            return f"{code}{nsn}@s.whatsapp.net"
        raise ValueError(style)

    def email(self, name: str | None = None) -> str:
        first, last = (name or self.name()).lower().split(" ", 1)
        sep = self.r.choice([".", "_", ""])
        return f"{first}{sep}{last}{self.r.randint(7, 98)}" + "@" + self.r.choice(DOMAINS)

    def snowflake(self) -> str:
        return self.r.choice("1234") + self._digits(17)

    def uuid(self) -> str:
        h = "".join(self.r.choice("0123456789abcdef") for _ in range(32))
        h = h[:12] + "4" + h[13:16] + self.r.choice("89ab") + h[17:]
        return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"

    def signal_group(self) -> str:
        alphabet = string.ascii_letters + string.digits + "+/"
        while True:
            s = "".join(self.r.choice(alphabet) for _ in range(43))
            if (
                any(c.isupper() for c in s)
                and any(c.islower() for c in s)
                and any(c.isdigit() for c in s)
            ):
                return s + "="

    def base_point(self) -> tuple[float, float]:
        return round(self.r.uniform(-50, 60), 6), round(self.r.uniform(-170, 170), 6)

    def near(self, p: tuple[float, float], metres: float = 40.0) -> tuple[float, float]:
        d = metres / 111_000
        return round(p[0] + self.r.uniform(-d, d), 6), round(p[1] + self.r.uniform(-d, d), 6)

    def address(self) -> str:
        flat = f"{self.r.randint(2, 30)}{self.r.choice('ABCDEFG')}"
        return f"Flat {flat}, {self.r.randint(3, 280)} {self.r.choice(STREETS)}"

    def ts(self) -> str:
        return (
            f"2026-0{self.r.randint(1, 9)}-{self.r.randint(10, 28)}T"
            f"{self.r.randint(10, 23)}:{self.r.randint(10, 59)}:{self.r.randint(10, 59)}Z"
        )


def cases(seed: int = 283) -> list[dict]:
    s = Synth(seed)
    c: list[dict] = []

    def add(label: int, kind: str, file: str, line: str, lang: str = "en") -> None:
        c.append(
            {
                "id": f"c{len(c):02d}",
                "label": label,
                "kind": kind,
                "file": file,
                "line": line,
                "lang": lang,
            }
        )

    # ---- positives: production-row-like
    for _ in range(3):
        n = s.name()
        add(
            1,
            "production",
            "tests/fixtures/export_rows.py",
            f'    ({s.r.randint(1000, 9999)}, "{n}", "{s.phone(style="spaced")}", "{s.ts()}"),',
        )
    n = s.name()
    add(
        1,
        "production",
        "logs/sample.log",
        f'{s.ts()} INFO signal inbound source={s.phone()} sourceName="{n}" room=family',
    )
    add(
        1,
        "production",
        "tests/fixtures/wa_event.json",
        f'  "key": {{"remoteJid": "{s.phone("852", "jid")}", "fromMe": false}}, '
        f'"pushName": "{s.name()}",',
    )
    add(
        1,
        "production",
        "tests/fixtures/discord_event.json",
        f'  "author": {{"id": "{s.snowflake()}", '
        f'"username": "{s.name().split()[0].lower()}{s.r.randint(10, 99)}"}},',
    )
    n = s.name()
    add(1, "production", "data/members.csv", f"{n},{s.email(n)},{s.phone('44', 'dashed')}")
    p = s.base_point()
    for _ in range(2):
        q = s.near(p)
        add(
            1,
            "production",
            "tests/fixtures/presence_rows.py",
            f'    ("{s.name().split()[0].lower()}", {q[0]}, {q[1]}, "{s.ts()}", 12.0),',
        )
    add(
        1,
        "production",
        "docs/notes.md",
        f"{s.name()} lives at {s.address()}, call {s.phone('65', 'spaced')}.",
    )
    add(
        1,
        "production",
        "tests/fixtures/life360.json",
        f'  {{"id": "{s.uuid()}", "firstName": "{s.name().split()[0]}", '
        f'"latitude": "{s.base_point()[0]}", '
        f'"longitude": "{s.base_point()[1]}"}},',
    )
    add(
        1,
        "production",
        "tests/fixtures/signal_group.json",
        f'  "groupInfo": {{"groupId": "{s.signal_group()}", '
        f'"members": ["{s.phone()}", "{s.phone()}"]}}',
    )
    # CJK
    add(
        1,
        "production",
        "tests/fixtures/zh_rows.py",
        f'    ("{s.cjk_name()}", "{s.phone("852", "spaced")}"),',
        "zh",
    )
    add(
        1,
        "production",
        "logs/zh.log",
        f"{s.ts()} 收到 {s.cjk_name()} 嘅訊息 電話 {s.phone('852', 'local')}",
        "zh",
    )
    add(
        1,
        "production",
        "docs/zh-notes.md",
        f"{s.cjk_name()}住喺{s.r.randint(3, 88)}號{s.r.choice(['海景道', '彌敦道', '皇后大道'])}",
        "zh",
    )
    add(
        1,
        "production",
        "tests/fixtures/zh_presence.py",
        f'    ("{s.cjk_name()}", {s.near(p)[0]}, {s.near(p)[1]}),',
        "zh",
    )
    # ---- positives: realistic values in fixture context
    n = s.name()
    add(
        1,
        "fixture",
        "tests/unit/test_contacts.py",
        f'    person = make_person(name="{n}", phone="{s.phone("44", "spaced")}")',
    )
    add(1, "fixture", "tests/unit/test_mail.py", f'    assert parse_sender(raw) == "{s.email()}"')
    add(
        1, "fixture", "tests/unit/test_ids.py", f"MEMBER_DISCORD_ID = {s.snowflake()}  # {s.name()}"
    )
    add(1, "fixture", "tests/unit/test_wa.py", f'SENDER = "{s.phone("91", "jid")}"')
    add(
        1,
        "fixture",
        "tests/unit/test_geo.py",
        f"HOME = ({s.base_point()[0]}, {s.base_point()[1]})  # {s.name()}'s flat",
    )
    add(
        1,
        "fixture",
        "tests/unit/test_zh.py",
        f'    member = Person(name="{s.cjk_name()}", phone="{s.phone("852")}")',
        "zh",
    )
    add(
        1,
        "fixture",
        "tests/unit/test_contacts.py",
        f'    ("{s.name()}", "{s.phone("1", "dashed")}"),',
    )
    add(1, "fixture", "tests/unit/test_mail.py", f'    to = "{s.name()} <{s.email()}>"')
    # commit-message lines
    add(
        1,
        "message",
        "<commit-message>",
        f"The fix came from {s.name()}'s report; she texted from {s.phone('61', 'spaced')}.",
    )
    add(
        1,
        "message",
        "<commit-message>",
        f"Reproduced with the fix at {s.base_point()[0]}, {s.base_point()[1]} "
        "(where Amara sleeps).",
    )

    # ---- negatives: code, prose, config
    for line in [
        "    return sorted(rows, key=lambda r: r.ts)",
        "def normalise_sender(raw: str) -> str:",
        '    logger.info("tag handled", extra={"tag_id": tag.id, "ms": elapsed_ms})',
        "timeout_s = 30",
        "The relevance gate reads the transcript with an age per row.",
        '    assert result.status == "nothing_found"',
        "import asyncio",
        "| DEC-12 | The gate fails closed on an unknown speaker | tests/unit/test_gate.py |",
        '    version = "2.4.13"',
        "    retries: int = 5  # exponential backoff from 1s",
        "sha256 = 9f2c",
        "    x = rng.uniform(-1.0, 1.0)",
        "ports: 127.0.0.1:3100 -> langfuse",
    ]:
        add(0, "code", "nora/module.py" if not line.startswith(("The", "|")) else "docs/x.md", line)
    add(0, "code", "docs/zh.md", "今日天氣好好，我哋去飲茶啦。", "zh")  # noqa: RUF001 - Cantonese prose
    add(0, "code", "nora/doctrine/voice.md", "佢講嘢好直接，唔會兜圈。", "zh")  # noqa: RUF001
    # ---- negatives: placeholders and public organisations
    for line in [
        '    phone = "+1 555 0100"  # NANP fiction range',
        '    email = "someone@example.com"',
        '    person_id = "member_a"',
        '    uid = "00000000-0000-4000-8000-000000000001"',
        '    to = "John Doe <john.doe@example.org>"',
        "For billing questions write to support@stripe.com.",
        "In an emergency in Hong Kong dial 999.",
        '    phone = "+85212345678"  # identity.example.toml',
        "    snowflake = 100000000000000001",
        "    lat, lng = 0.0, 0.0",
        '    group_id = "Qk9YMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAw"',
    ]:
        add(0, "placeholder", "tests/unit/test_placeholders.py", line)
    add(0, "placeholder", "tests/unit/test_zh.py", '    name = "小明"  # 例子', "zh")
    add(
        0,
        "message",
        "<commit-message>",
        "fix(gate): the relevance gate reads each row's age (#283)",
    )
    add(
        0,
        "message",
        "<commit-message>",
        "Measured over 40 labelled cases: 3 errors before, 1 after.",
    )
    return c


# --------------------------------------------------------------------------
# Running a backend over the set


def case_view(judge, case: dict):
    """One case as the commit the judge reads."""
    v = judge.CommitView()
    if case["kind"] == "message":
        v.message = [("M1", case["line"])]
        v.files = [{"path": "nora/module.py", "rows": [("L1", "+", "x = compute(y)")]}]
    else:
        v.message = [("M1", f"chore: update {case['file']}")]
        v.files = [
            {
                "path": case["file"],
                "rows": [
                    (None, " ", "def build():"),
                    ("L1", "+", case["line"]),
                    (None, " ", "    return rows"),
                ],
            }
        ]
    v.subject = v.message[0][1]
    return v


def _score(backend: str, cases_: list[dict]) -> tuple[list, dict]:
    import pii_judge as judge

    scores, t0 = [], time.monotonic()
    for c in cases_:
        scores.append(judge.judge_commit(backend, case_view(judge, c)).score)
    return scores, {"seconds": time.monotonic() - t0}


def evaluate(scores: list, labels: list, thr: float) -> dict:
    fp = sum(1 for s, y in zip(scores, labels, strict=True) if s >= thr and y == 0)
    fn = sum(1 for s, y in zip(scores, labels, strict=True) if s < thr and y == 1)
    return {"threshold": thr, "fp": fp, "fn": fn, "errors": fp + fn}


def refuse_denylisted(cs: list[dict]) -> None:
    """A generated line that happens to spell a real value is never sent to
    any model: the synthetic set is checked against the private denylist
    first, and the run stops on a hit."""
    import pii_scan as scan

    deny, err, _ = scan.load_denylist()
    if err is not None or deny is None:
        raise SystemExit(
            f"refusing to run without the denylist check: {err.rule if err else 'no denylist'}"
        )
    sc = scan.Scanner(deny, scan.Allowlist(), set())
    hits = [x["id"] for x in cs if sc.denylist_hits(scan.normalise_text(x["line"]))]
    if hits:
        raise SystemExit(
            f"refusing to run: generated case(s) {hits} match a denylisted real value; "
            "change the seed"
        )


def cmd_run(a) -> int:
    cs = cases(a.seed)
    refuse_denylisted(cs)
    labels = [x["label"] for x in cs]
    runs = []
    for i in range(a.runs):
        scores, detail = _score(a.backend, cs)
        runs.append({"scores": scores, "detail": detail})
        print(f"run {i + 1}: {detail}", file=sys.stderr)
    thresholds = [round(t * 0.05, 2) for t in range(1, 20)]
    table = []
    for thr in thresholds:
        per_run = [evaluate(r["scores"], labels, thr) for r in runs]
        errs = [x["errors"] for x in per_run]
        table.append(
            {
                "threshold": thr,
                "errors": errs,
                "fp": [x["fp"] for x in per_run],
                "fn": [x["fn"] for x in per_run],
                "mean_errors": statistics.mean(errs),
            }
        )
    best = min(table, key=lambda r: (r["mean_errors"], max(r["fn"]), r["threshold"]))
    identical = all(r["scores"] == runs[0]["scores"] for r in runs[1:])
    by_kind: dict[str, dict] = {}
    for k in sorted({x["kind"] for x in cs}):
        idx = [i for i, x in enumerate(cs) if x["kind"] == k]
        by_kind[k] = {
            "n": len(idx),
            "errors_at_best": [
                sum(1 for i in idx if (r["scores"][i] >= best["threshold"]) != bool(labels[i]))
                for r in runs
            ],
        }
    zh_idx = [i for i, x in enumerate(cs) if x["lang"] == "zh"]
    out = {
        "backend": a.backend,
        "model": os.environ.get("NORA_PII_LAYA_MODEL", ""),
        "n": len(cs),
        "positives": sum(labels),
        "runs": a.runs,
        "deterministic": identical,
        "best": best,
        "by_kind": by_kind,
        "zh_errors_at_best": [
            sum(1 for i in zh_idx if (r["scores"][i] >= best["threshold"]) != bool(labels[i]))
            for r in runs
        ],
        "table": table,
        "per_case": [
            {
                "id": x["id"],
                "label": x["label"],
                "kind": x["kind"],
                "lang": x["lang"],
                "scores": [r["scores"][i] for r in runs],
            }
            for i, x in enumerate(cs)
        ],
        "details": [r["detail"] for r in runs],
    }
    with open(a.out, "w") as fh:
        json.dump(out, fh, indent=1, ensure_ascii=False)
    print(
        json.dumps(
            {
                k: out[k]
                for k in (
                    "backend",
                    "model",
                    "n",
                    "positives",
                    "runs",
                    "deterministic",
                    "best",
                    "by_kind",
                    "zh_errors_at_best",
                )
            },
            ensure_ascii=False,
        )
    )
    return 0


def synthetic_commit(n_lines: int, seed: int = 7) -> list[dict]:
    """Code-like added lines with a sprinkle of fixture values, for timing."""
    s = Synth(seed)
    templates = [
        lambda: (
            f"    result = await store.fetch(room_id=room_{s.r.randint(1, 99)}, "
            f"limit={s.r.randint(5, 50)})"
        ),
        lambda: f'    assert out.status == "ok", out.note  # case {s.r.randint(1, 999)}',
        lambda: "def handle(event: Event) -> Reply | None:",
        lambda: (
            f"        # {s.r.choice(['retry', 'bounded', 'fail closed', 'render age'])} per row"
        ),
        lambda: f'    person = make_person(name="{s.name()}")',
        lambda: "",
        lambda: f"    timeout_s: float = {s.r.randint(1, 60)}.0",
    ]
    return [
        {"file": f"nora/mod_{i // 200}.py", "line": s.r.choice(templates)()} for i in range(n_lines)
    ]


def bench_view(judge, n: int):
    """A synthetic commit of n added lines over files of 200 lines."""
    v = judge.CommitView(
        subject="feat: synthetic bench commit", message=[("M1", "feat: synthetic bench commit")]
    )
    k = 0
    by_file: dict[str, list] = {}
    for x in synthetic_commit(n):
        if not x["line"].strip():
            continue
        k += 1
        by_file.setdefault(x["file"], []).append((f"L{k}", "+", x["line"]))
    v.files = [{"path": p, "rows": rows} for p, rows in by_file.items()]
    return v, k


def cmd_bench(a) -> int:
    import pii_judge as judge

    res = []
    for n in [int(x) for x in a.sizes.split(",")]:
        refuse_denylisted(synthetic_commit(n))
        view, judged = bench_view(judge, n)
        for rep in range(a.reps):
            t0 = time.monotonic()
            v = judge.judge_commit(a.backend, view)
            row = {
                "added_lines": n,
                "judged": judged,
                "rep": rep,
                "wall_s": round(time.monotonic() - t0, 3),
                "score": round(v.score, 3),
                **{k: (round(x, 3) if isinstance(x, float) else x) for k, x in v.detail.items()},
            }
            res.append(row)
            print(json.dumps(row), file=sys.stderr)
    with open(a.out, "w") as fh:
        json.dump(res, fh, indent=1)
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("cases")
    p.add_argument("--seed", type=int, default=283)
    p = sub.add_parser("run")
    p.add_argument("backend")
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--seed", type=int, default=283)
    p.add_argument("--out", required=True)
    p = sub.add_parser("bench")
    p.add_argument("backend")
    p.add_argument("--sizes", default="50,500,5000")
    p.add_argument("--reps", type=int, default=2)
    p.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "cases":
        for x in cases(a.seed):
            print(json.dumps(x, ensure_ascii=False))
        return 0
    if a.cmd == "run":
        return cmd_run(a)
    return cmd_bench(a)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
