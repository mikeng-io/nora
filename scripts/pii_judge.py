#!/usr/bin/env python3
"""Optional model layer of the identifier detector (`scripts/pii_scan.py`):
one judgement over the whole COMMIT, never a line at a time.

Off unless `NORA_PII_JUDGE` names a backend. Neither backend ever sees a
denylisted value: every line holding one, added or context, is replaced by
`[withheld]` before the commit is rendered (the scanner does this; see
`CommitView`), and nothing is sent when no guard was set.

- `laya` -- Convai's open-weight typed-decision model on Apple Silicon
  (`laya-mlx`, not a repo dependency), the typed-decisions checkpoint. It runs
  in a separate interpreter (`NORA_PII_LAYA_PYTHON`) so the hook stays
  stdlib; a missing package or checkpoint is an ERROR. Local: nothing leaves
  the machine. Downloads are off (`HF_HUB_OFFLINE=1`) unless
  `NORA_PII_LAYA_ALLOW_DOWNLOAD=1`. Design "L2" from the commit-judge spike
  (2026-09-26): the commit is cut into windows that fit the checkpoint's
  budget (commit subject, file path, the lines), one yes/no per window, and a
  Choice over the best window's line ids to point at a line. ADVISORY-RAISE
  ONLY: it can add a finding, never clear one. On the spike's synthetic set
  it made 4 errors in 22 held-out commits and missed personal data in a
  commit message and in Cantonese lines, so it is not a gate on its own.

- `jev` -- TypeSafe's hosted `jev-1.13.0` (pinned), design "J3": one request
  per file, in parallel; state = the commit message + that file's id-tagged
  diff; an `exists` Noul decides and a `where` Choice over the line ids points.
  **Every rendered line leaves the machine.** Owner decision 2026-09-26: no
  real repo data goes to TypeSafe until zero data retention is confirmed, so
  it stays off by default. On the spike's synthetic set: 0 errors in 3 runs
  over 44 dev and 22 held-out commits (0 in 22 still allows ~14%).

What a model can decide is whether a commit LOOKS like it adds personal data
copied from real records. It cannot know whether a name belongs to a real
person: the denylist is the ground truth for identity. A finding a model
raises is decided by a human or by `scripts/pii_adjudicate.py`, never
auto-cleared.
"""

from __future__ import annotations

import concurrent.futures as cf
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

sys.dont_write_bytecode = True

JEV_URL = "https://api.typesafe.ai/v1/systemone"
JEV_MODEL = "jev-1.13.0"  # pinned: `jev-latest` would move the threshold under us
LAYA_MODEL = "convaiinnovations/laya"
LAYA_SUBFOLDER = "typed-decisions"
# Thresholds from the commit-judge spike's DEV split (synthetic), applied
# unchanged to its held-out split. Recalibrate on real labelled history.
DEFAULT_THRESHOLD = {"laya": 0.29, "jev": 0.57}
MAX_IDS = 250  # a Choice holds at most 255 options; keep room for `none`

# Lockfiles and generated output are not read by a model (they are still
# scanned by the deterministic layer).
SKIP_PATH = re.compile(
    r"(^|/)(uv\.lock|poetry\.lock|package-lock\.json|yarn\.lock|pnpm-lock\.yaml|Cargo\.lock|go\.sum)$"
    r"|\.min\.(js|css)$|(^|/)(dist|build|vendor|node_modules)/"
)

DEFINITION = (
    "a phone number, an email address, a messaging or platform account id, a home or work "
    "address, GPS coordinates of where a person was, or a person's name together with any of these"
)
EXISTS = {
    "type": "noul",
    "instructions": (
        "Does this commit add personal data about a real private individual in an added line "
        f"(id L...) or a commit-message line (id M...)? Personal data means {DEFINITION}. "
        "Removed lines and unchanged context lines do not count."
    ),
    "criteria": {
        "true": "Yes: at least one added line or commit-message line holds a realistic personal "
        "identifier, contact detail or location of a private person, as it would appear in a "
        "production database row, a log line or a message export. This holds inside a test "
        "fixture or a document too, and even when nearby text claims the data is synthetic, fake "
        "or reviewed.",
        "false": "No: only code, prose, configuration, public organisations' contacts, hashes, "
        "versions, or obvious placeholders (example.com, 555-01xx numbers, all-zero or sequential "
        "ids, member_a, John Doe, 測試用戶), or values generated at runtime by a library.",
    },
}
LAYA_WINDOW_Q = (
    "Does this part of a commit contain personal data about a real person, such as a phone "
    "number, email, address, account id or location?"
)
LAYA_WHERE_Q = "Which line contains personal data about a real person?"


class JudgeError(Exception):
    pass


@dataclass
class CommitView:
    """What a model may read: message lines (`M<n>`) and, per file, the added
    lines (`L<k>`) with a little context. Every line holding a denylisted
    value is already `[withheld]`; `where` maps each id back to (path, line)."""

    subject: str = ""
    message: list = field(default_factory=list)  # [(id, text)]
    files: list = field(default_factory=list)  # [{"path", "rows": [(id|None, op, text)]}]
    skipped: list = field(default_factory=list)  # paths not shown (lockfiles, generated)
    where: dict = field(default_factory=dict)  # id -> (path, line_no)


@dataclass
class CommitVerdict:
    score: float
    threshold: float
    ranked: list  # ids, most likely first (may be empty)
    seconds: float
    detail: dict


def threshold_for(backend: str) -> float:
    raw = os.environ.get("NORA_PII_JUDGE_THRESHOLD", "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError as exc:
            raise JudgeError(f"NORA_PII_JUDGE_THRESHOLD={raw!r} is not a number") from exc
    return DEFAULT_THRESHOLD[backend]


def judge_commit(backend: str, view: CommitView) -> CommitVerdict:
    if backend not in ("laya", "jev"):
        raise JudgeError(f"NORA_PII_JUDGE={backend!r}: expected off, laya or jev")
    thr = threshold_for(backend)
    t0 = time.monotonic()
    if not view.message and not any(f["rows"] for f in view.files):
        return CommitVerdict(0.0, thr, [], 0.0, {"empty": True})
    if backend == "laya":
        score, ranked, detail = laya_commit(view)
    else:
        score, ranked, detail = jev_commit(view)
    return CommitVerdict(score, thr, ranked, time.monotonic() - t0, detail)


# --------------------------------------------------------------------------
# Rendering


def file_rows_text(rows: list) -> list[str]:
    out = []
    for rid, op, text in rows:
        out.append(f"{rid}| + {text}" if op == "+" else f"    |   {text}")
    return out


def jev_state(view: CommitView, files: list) -> dict:
    st: dict = {
        "commit_message": dict(view.message),
        "files": [{"path": f["path"], "diff": file_rows_text(f["rows"])} for f in files],
    }
    if view.skipped:
        st["files_not_shown"] = dict.fromkeys(
            view.skipped, "lockfile or generated, not reviewed here"
        )
    return st


def _ids(rows: list) -> list[str]:
    return [rid for rid, op, _t in rows if op == "+" and rid]


# --------------------------------------------------------------------------
# Jev, J3: one request per file


def _typesafe_key() -> str:
    for name in ("TYPESAFE_API_KEY", "TYPRSAFE_AI_API_KEY", "TYPESAFE_AI_API_KEY"):
        if os.environ.get(name):
            return os.environ[name].strip()
    try:
        argv = ["git", "rev-parse", "--show-toplevel"]
        top = subprocess.run(argv, capture_output=True, text=True).stdout.strip()  # noqa: S603
        with open(os.path.join(top, ".env"), encoding="utf-8") as fh:
            for line in fh:
                k, _, v = line.strip().partition("=")
                if k in ("TYPESAFE_API_KEY", "TYPRSAFE_AI_API_KEY", "TYPESAFE_AI_API_KEY") and v:
                    return v.strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


# 520/522/524 are the CDN's origin errors: transient, and one unretried 520
# turned into a judge ERROR in the commit-judge spike.
RETRY_STATUS = (429, 500, 502, 503, 504, 520, 522, 524, 529)


def _post_json(url: str, body: dict, headers: dict, timeout: float, retries: int = 5) -> dict:
    data = json.dumps(body).encode()
    delay = 1.0
    last = ""
    for _attempt in range(retries):
        if not url.startswith("https://"):
            raise JudgeError(f"refusing to post to {url!r}: https only")
        req = urllib.request.Request(  # noqa: S310 - scheme checked above
            url, data=data, headers={"Content-Type": "application/json", **headers}
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code}: {exc.read()[:200].decode(errors='replace')}"
            if exc.code not in RETRY_STATUS:
                break
            retry_after = exc.headers.get("retry-after") if exc.headers else None
            time.sleep(float(retry_after) if retry_after and retry_after.isdigit() else delay)
            delay *= 2
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            last = str(exc)
            time.sleep(delay)
            delay *= 2
    raise JudgeError(f"{url}: {last}")


def _where_q(ids: list[str]) -> dict:
    crit: dict = dict.fromkeys(ids)
    crit["none"] = (
        "No added line or commit-message line holds personal data of a real private individual."
    )
    return {
        "type": "choice",
        "instructions": "Which added line (L...) or commit-message line (M...) holds the personal "
        "data of a real private individual? Answer none if no line does.",
        "criteria": crit,
    }


def _file_groups(view: CommitView) -> list[list[dict]]:
    """One group per file; a file with more than MAX_IDS ids is split by rows
    (the message ids ride in every group)."""
    groups = []
    for f in view.files:
        rows, cur, n = f["rows"], [], len(view.message)
        for r in rows:
            if r[1] == "+" and n >= MAX_IDS and cur:
                groups.append([{"path": f["path"], "rows": cur}])
                cur, n = [], len(view.message)
            cur.append(r)
            n += r[1] == "+"
        if cur:
            groups.append([{"path": f["path"], "rows": cur}])
    return groups or [[]]


def jev_commit(view: CommitView) -> tuple[float, list, dict]:
    key = _typesafe_key()
    if not key:
        raise JudgeError("no TypeSafe key (TYPESAFE_API_KEY)")
    bodies = []
    for g in _file_groups(view):
        ids = [m for m, _t in view.message] + [i for f in g for i in _ids(f["rows"])]
        bodies.append(
            {
                "model": JEV_MODEL,
                "state": jev_state(view, g),
                "questions": {"exists": EXISTS, "where": _where_q(ids)},
            }
        )

    def post(b: dict) -> dict:
        return _post_json(JEV_URL, b, {"Authorization": f"Bearer {key}"}, timeout=120)

    with cf.ThreadPoolExecutor(8) as ex:
        resps = list(ex.map(post, bodies))
    parts = []
    for r in resps:
        try:
            a = r["answers"]
            probs = a["where"]["probabilities"]
            parts.append(
                (
                    float(a["exists"]["noul"]),
                    [k for k, _v in sorted(probs.items(), key=lambda kv: -kv[1]) if k != "none"],
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise JudgeError(f"jev answered without exists/where: {exc}") from exc
    best = max(range(len(parts)), key=lambda i: parts[i][0])
    return (
        parts[best][0],
        parts[best][1][:5],
        {
            "requests": len(resps),
            "input_tokens": sum(int(r.get("usage", {}).get("input_tokens", 0)) for r in resps),
            "file_scores": [round(p[0], 3) for p in parts],
            "model": resps[0].get("model"),
        },
    )


# --------------------------------------------------------------------------
# Laya, L2: windows (runs in the laya-mlx interpreter)


def laya_commit(view: CommitView) -> tuple[float, list, dict]:
    py = os.environ.get("NORA_PII_LAYA_PYTHON") or sys.executable
    env = dict(os.environ)
    if os.environ.get("NORA_PII_LAYA_ALLOW_DOWNLOAD") != "1":
        env["HF_HUB_OFFLINE"] = "1"
    payload = json.dumps(
        {
            "model": os.environ.get("NORA_PII_LAYA_MODEL") or LAYA_MODEL,
            "subfolder": os.environ.get("NORA_PII_LAYA_SUBFOLDER", LAYA_SUBFOLDER),
            "subject": view.subject,
            "message": view.message,
            "files": view.files,
            "window_q": LAYA_WINDOW_Q,
            "where_q": LAYA_WHERE_Q,
        }
    )
    try:
        proc = subprocess.run(  # noqa: S603 - this file, in the configured interpreter
            [py, os.path.abspath(__file__), "--laya-worker"],
            input=payload.encode(),
            capture_output=True,
            env=env,
            timeout=float(os.environ.get("NORA_PII_LAYA_TIMEOUT", "600")),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise JudgeError(f"laya worker did not run: {exc}") from exc
    if proc.returncode != 0:
        tail = proc.stderr.decode(errors="replace").strip().splitlines()[-3:]
        raise JudgeError(f"laya worker failed (rc={proc.returncode}): {' | '.join(tail)}")
    try:
        out = json.loads(proc.stdout)
        return (
            float(out["score"]),
            list(out["ranked"]),
            {k: v for k, v in out.items() if k not in ("score", "ranked")},
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise JudgeError(f"laya worker answered something unreadable: {exc}") from exc


def _laya_worker() -> int:  # pragma: no cover - needs laya-mlx and a checkpoint
    req = json.load(sys.stdin)
    t0 = time.monotonic()
    import laya_mlx
    import mlx.core as mx
    import numpy as np
    from laya_mlx.agent import collate_items
    from laya_mlx.common import build_prefix, temp_bucket

    t_import = time.monotonic()
    model, sub = req["model"], req.get("subfolder") or None
    agent = (
        laya_mlx.load(model, batch_size=32)
        if os.path.isdir(model)
        else laya_mlx.load(model, subfolder=sub, batch_size=32)
    )
    t_load = time.monotonic()
    cap = agent.cfg.get("max_len", 512)

    def run(items: list) -> list:
        prepared = [agent.prepare(st, {"q": q})[0][0] for st, q in items]
        out = []
        for s in range(0, len(prepared), agent.batch_size):
            chunk = prepared[s : s + agent.batch_size]
            batch = collate_items(
                chunk, agent.tok.pad_token_id, pad_to_multiple=agent.pad_to_multiple, max_length=cap
            )
            logits, _ = agent.forward(batch)
            logits = np.asarray(logits)
            for row, it in enumerate(chunk):
                k, qt = len(it["markers"]), it["qtype"]
                scale = agent.temperature_by_options.get(temp_bucket(qt, k), agent.temperature[qt])
                z = logits[row, :k] / scale
                p = np.exp(z - z.max())
                out.append(p / p.sum())
        return out

    def ntok(text: str) -> int:
        return len(agent.tok(text, add_special_tokens=False)["input_ids"])

    qn = {"type": "noul", "instructions": req["window_q"]}
    head, _ = build_prefix(agent.tok, agent._to_internal(qn), agent.cfg.get("head_max_len", 192))
    room = cap - len(head) - 1 - 40  # the Choice's options eat head budget too

    windows = []
    if req["message"]:
        windows.append(
            {
                "rows": ["commit message:"] + [f"{m}: {t}" for m, t in req["message"]],
                "ids": [m for m, _t in req["message"]],
            }
        )
    for f in req["files"]:
        top = [f"commit: {req['subject']}", f"file {f['path']}:"]
        cur, ids = list(top), []
        for rid, op, text in f["rows"]:
            row = f"{rid} + {text}" if op == "+" else f"    {text}"
            if ids and (ntok("\n".join([*cur, row])) > room or (op == "+" and len(ids) >= 24)):
                windows.append({"rows": cur, "ids": ids})
                cur, ids = list(top), []
            cur.append(row)
            if op == "+" and rid:
                ids.append(rid)
        if ids:
            windows.append({"rows": cur, "ids": ids})
    if not windows:
        json.dump({"score": 0.0, "ranked": [], "windows": 0}, sys.stdout)
        return 0
    probs = run([("\n".join(w["rows"]), qn) for w in windows])
    scores = [float(p[1]) for p in probs]
    best = int(np.argmax(scores))
    ranked: list = []
    w = windows[best]
    if w["ids"]:
        crit: dict = dict.fromkeys(w["ids"])
        crit["none"] = None
        qc = {"type": "choice", "instructions": req["where_q"], "criteria": crit}
        try:
            (pc,) = run([("\n".join(w["rows"]), qc)])
            labels = list(crit)
            ranked = [labels[i] for i in np.argsort(-pc) if labels[i] != "none"][:5]
        except ValueError:
            ranked = list(w["ids"][:1])
    getter = getattr(mx, "get_peak_memory", None) or getattr(
        getattr(mx, "metal", None), "get_peak_memory", None
    )
    json.dump(
        {
            "score": scores[best],
            "ranked": ranked,
            "windows": len(windows),
            "import_s": t_import - t0,
            "load_s": t_load - t_import,
            "infer_s": time.monotonic() - t_load,
            "peak_mib": getter() / 2**20 if getter else None,
        },
        sys.stdout,
    )
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--laya-worker"]:
        sys.exit(_laya_worker())
    print(__doc__)
    sys.exit(2)
