#!/usr/bin/env python3
"""Optional model layers for the identifier detector (`scripts/pii_scan.py`).

Both are OFF unless asked for, and neither ever sees a denylist hit: a line
that carries a known real value has already failed, and sending it to a model
would be the disclosure the detector exists to stop.

1. **A line judge** (`NORA_PII_JUDGE=laya|jev`): one yes/no (Noul) score per
   ADDED line and per commit-message line — "does this line carry personal data
   of a private individual?". A line at or above the threshold becomes a
   finding the author acknowledges or fixes; it is never auto-decided.

   - `laya` — Convai's open-weight typed-decision model on Apple Silicon
     through `laya-mlx` (not a repo dependency). It runs in a separate
     interpreter (`NORA_PII_LAYA_PYTHON`, default this one), so the hook stays
     stdlib; a missing package or checkpoint is an ERROR. Local: no line
     leaves the machine. Downloads are off (`HF_HUB_OFFLINE=1`) unless
     `NORA_PII_LAYA_ALLOW_DOWNLOAD=1`.
   - `jev` — TypeSafe's hosted model, `POST https://api.typesafe.ai/v1/systemone`,
     pinned to `jev-1.13.0`. **Every judged line leaves the machine.** Owner
     decision 2026-09-26: no real repo data goes to TypeSafe until its zero
     data retention is confirmed, so this is for synthetic calibration only.
     Key from `TYPESAFE_API_KEY` (or the `.env` names it has lived under).

2. **An LLM re-judge** (`NORA_PII_LLM=<ollama tag>`): for each finding a shape
   rule or the line judge raised, a local ollama model reads the line with two
   lines either side and answers real / fake / unsure. Only `fake` clears a
   finding, and only with NORA_PII_LLM_CLEARS=1; by default the verdict is
   printed beside the finding for the human who decides. Unreachable ollama
   is an ERROR.

What a model can and cannot decide: whether a line LOOKS like personal data
copied from real records, from its shape and context. Not whether a name
belongs to a real person — an invented name and a member's name read the
same. The denylist is the ground truth for identity; these layers only widen
the net for what nobody has listed yet.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

JEV_URL = "https://api.typesafe.ai/v1/systemone"
JEV_MODEL = "jev-1.13.0"  # pinned: `jev-latest` would move the threshold under us
LAYA_MODEL = "aac6fef/laya-mlx"  # English: 9/55 vs multilingual 16/55 on the synthetic set
OLLAMA_URL = "http://127.0.0.1:11434/api/chat"

# Thresholds from the synthetic calibration (scripts/pii_judge_eval.py); the
# scrub track replaces them with thresholds measured on real labelled history.
DEFAULT_THRESHOLD = {"laya": 0.3, "jev": 0.4}

STATE = ("Lines being added to files in a software repository (code, tests, fixtures, docs), "
         "reviewed for personal data before the commit is accepted.")
QUESTION = ("Does `line` contain personal data about a real private individual: a phone number, "
            "an email address, a messaging or platform account id, a home or work address, GPS "
            "coordinates of where a person was, or a person's name together with any of these?")
CRITERIA = {
    "true": "Yes: a realistic personal identifier, contact detail or location of a private person, "
            "as it would appear in a production database row, log line or message export.",
    "false": "No: ordinary code, prose or configuration; a public organisation's contact; or an "
             "obvious placeholder (example.com, 555-01xx numbers, all-zero or sequential ids, "
             "member_a, John Doe).",
}


# Laya reads a short, criteria-free question better: over the 55-case
# synthetic set the long question above with criteria scored 14 errors
# (English checkpoint) and this one 9 (5 variants tried ON THAT SAME SET, so
# 9 is optimistic). Its instruction budget is 192 tokens shared with the
# options, and long criteria are truncated at 48 tokens each.
LAYA_QUESTION = "Does this line contain personal data about a real person?"


class JudgeError(Exception):
    pass


@dataclass
class Verdicts:
    scores: list
    threshold: float
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


def score_lines(backend: str, items: list) -> Verdicts:
    """items: (path, line_no, line, context). Returns one P(yes) per item."""
    if backend not in ("laya", "jev"):
        raise JudgeError(f"NORA_PII_JUDGE={backend!r}: expected off, laya or jev")
    thr = threshold_for(backend)
    t0 = time.monotonic()
    if not items:
        return Verdicts([], thr, 0.0, {})
    lines = [{"file": p, "line": text[:400]} for p, _n, text, _c in items]
    if backend == "laya":
        scores, detail = laya_scores(lines)
    else:
        scores, detail = jev_scores(lines)
    if len(scores) != len(items):
        raise JudgeError(f"{backend} returned {len(scores)} scores for {len(items)} lines")
    return Verdicts(scores, thr, time.monotonic() - t0, detail)


# --------------------------------------------------------------------------
# Laya (local, separate interpreter)


def laya_scores(lines: list, model: str | None = None) -> tuple[list, dict]:
    py = os.environ.get("NORA_PII_LAYA_PYTHON") or sys.executable
    model = model or os.environ.get("NORA_PII_LAYA_MODEL") or LAYA_MODEL
    env = dict(os.environ)
    if os.environ.get("NORA_PII_LAYA_ALLOW_DOWNLOAD") != "1":
        env["HF_HUB_OFFLINE"] = "1"
    payload = json.dumps({"model": model, "lines": lines, "question": LAYA_QUESTION, "criteria": None})
    try:
        proc = subprocess.run([py, os.path.abspath(__file__), "--laya-worker"], input=payload.encode(),
                              capture_output=True, timeout=float(os.environ.get("NORA_PII_LAYA_TIMEOUT", "600")),
                              env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise JudgeError(f"laya worker did not run: {exc}") from exc
    if proc.returncode != 0:
        tail = proc.stderr.decode(errors="replace").strip().splitlines()[-3:]
        raise JudgeError(f"laya worker failed (rc={proc.returncode}): {' | '.join(tail)}")
    try:
        out = json.loads(proc.stdout)
        return [float(x) for x in out["scores"]], {k: v for k, v in out.items() if k != "scores"}
    except (ValueError, KeyError, TypeError) as exc:
        raise JudgeError(f"laya worker answered something unreadable: {exc}") from exc


def _laya_batched(agent, lines: list, q: dict) -> list:
    """One Noul per line, each line its own state, batched across lines.

    `predict` batches the questions of ONE state; a commit is many states (one
    per line) and one question, so the rows are prepared per line and run
    through the agent's own collate/forward/calibration in batches. Falls back
    to one `predict` per line if the port's internals move."""
    try:
        import numpy as np  # noqa: PLC0415
        from laya_mlx.agent import collate_items  # noqa: PLC0415
        from laya_mlx.common import temp_bucket  # noqa: PLC0415
    except ImportError:
        return [float(agent.predict({"file": it["file"], "line": it["line"]}, q)["answers"]["pii"]["noul"])
                for it in lines]
    items = []
    for it in lines:
        prepared, _ = agent.prepare({"file": it["file"], "line": it["line"]}, q)
        items.append(prepared[0])
    out: list = []
    bs = agent.batch_size
    for start in range(0, len(items), bs):
        chunk = items[start:start + bs]
        batch = collate_items(chunk, agent.tok.pad_token_id, pad_to_multiple=agent.pad_to_multiple,
                              max_length=agent.cfg.get("max_len", 512))
        logits, _act = agent.forward(batch)
        logits = np.asarray(logits)
        if not np.isfinite(logits).all():
            raise FloatingPointError("non-finite Laya outputs")
        for row, item in enumerate(chunk):
            k, qt = len(item["markers"]), item["qtype"]
            scale = agent.temperature_by_options.get(temp_bucket(qt, k), agent.temperature[qt])
            z = logits[row, :k] / scale
            p = np.exp(z - z.max())
            p /= p.sum()
            out.append(float(p[1]))
    return out


def _laya_worker() -> int:
    req = json.load(sys.stdin)
    t0 = time.monotonic()
    import laya_mlx  # noqa: PLC0415 — only this interpreter needs it
    import mlx.core as mx  # noqa: PLC0415

    t_import = time.monotonic()
    agent = laya_mlx.load(req["model"], batch_size=int(os.environ.get("NORA_PII_LAYA_BATCH", "64")))
    t_load = time.monotonic()
    q = {"pii": {"type": "noul", "instructions": req["question"]}}
    if req.get("criteria"):
        q["pii"]["criteria"] = req["criteria"]
    scores = _laya_batched(agent, req["lines"], q)
    t_done = time.monotonic()
    peak = None
    try:
        peak = mx.get_peak_memory() / 2**20
    except AttributeError:
        try:
            peak = mx.metal.get_peak_memory() / 2**20
        except AttributeError:
            pass
    json.dump({"scores": scores, "import_s": t_import - t0, "load_s": t_load - t_import,
               "infer_s": t_done - t_load, "peak_mib": peak, "model": req["model"]}, sys.stdout)
    return 0


# --------------------------------------------------------------------------
# Jev (hosted)


def _typesafe_key() -> str:
    for name in ("TYPESAFE_API_KEY", "TYPRSAFE_AI_API_KEY", "TYPESAFE_AI_API_KEY"):
        if os.environ.get(name):
            return os.environ[name].strip()
    try:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip()
        with open(os.path.join(top, ".env"), encoding="utf-8") as fh:
            for line in fh:
                k, _, v = line.strip().partition("=")
                if k in ("TYPESAFE_API_KEY", "TYPRSAFE_AI_API_KEY", "TYPESAFE_AI_API_KEY") and v:
                    return v.strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def _post_json(url: str, body: dict, headers: dict, timeout: float, retries: int = 5) -> dict:
    data = json.dumps(body).encode()
    delay = 1.0
    last = ""
    for _attempt in range(retries):
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **headers})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code}: {exc.read()[:200].decode(errors='replace')}"
            if exc.code not in (429, 500, 502, 503, 504, 529):
                break
            retry_after = exc.headers.get("retry-after") if exc.headers else None
            time.sleep(float(retry_after) if retry_after and retry_after.isdigit() else delay)
            delay *= 2
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            last = str(exc)
            time.sleep(delay)
            delay *= 2
    raise JudgeError(f"{url}: {last}")


def jev_scores(lines: list) -> tuple[list, dict]:
    key = _typesafe_key()
    if not key:
        raise JudgeError("no TypeSafe key (TYPESAFE_API_KEY)")
    scores: list = []
    usage = {"input_tokens": 0, "requests": 0}
    batch = int(os.environ.get("NORA_PII_JEV_BATCH", "50"))
    for start in range(0, len(lines), batch):
        chunk = lines[start:start + batch]
        questions = {
            f"l{i}": {"type": "noul",
                      "instructions": {"file": it["file"], "line": it["line"], "question": QUESTION},
                      "criteria": CRITERIA}
            for i, it in enumerate(chunk)
        }
        resp = _post_json(JEV_URL, {"state": STATE, "model": JEV_MODEL, "questions": questions},
                          {"Authorization": f"Bearer {key}"}, timeout=60)
        try:
            answers = resp["answers"]
            scores += [float(answers[f"l{i}"]["noul"]) for i in range(len(chunk))]
        except (KeyError, TypeError, ValueError) as exc:
            raise JudgeError(f"jev answered without a noul for every line: {exc}") from exc
        usage["input_tokens"] += int(resp.get("usage", {}).get("input_tokens", 0))
        usage["requests"] += 1
        usage["model"] = resp.get("model")
    return scores, usage


# --------------------------------------------------------------------------
# LLM re-judge (local ollama)

LLM_SYSTEM = (
    "You review a line that is about to be committed to a software repository (code, tests, "
    "fixtures, docs). An automatic detector flagged it as possibly containing personal data. "
    "Decide from the text alone:\n"
    "- real: the value looks copied from real records about a real private person (a production "
    "database row, a log line, a message export) -- realistic, irregular, specific.\n"
    "- fake: clearly invented for a test, example or documentation (placeholder patterns, "
    "example domains, sequential or all-zero ids, member_a / alice style names), or a public "
    "organisation's published contact, or not personal data at all.\n"
    "- unsure: you cannot tell.\n"
    "You cannot look anyone up; a realistic name alone does not make a line real. "
    "Answer with JSON: {\"verdict\": \"real\"|\"fake\"|\"unsure\"}.")


def llm_rejudge(tag: str, items: list) -> list:
    """items: (rule, line, context). Returns real|fake|unsure per item."""
    url = os.environ.get("NORA_PII_LLM_URL", OLLAMA_URL)
    out = []
    for rule, line, ctx in items:
        body = {
            "model": tag, "stream": False, "think": False, "keep_alive": "15m",
            "options": {"temperature": 0, "num_predict": 32},
            "format": {"type": "object", "properties": {"verdict": {"type": "string",
                                                                     "enum": ["real", "fake", "unsure"]}},
                       "required": ["verdict"]},
            "messages": [
                {"role": "system", "content": LLM_SYSTEM},
                {"role": "user", "content": f"Detector rule: {rule}\nFlagged line:\n{line}\n\n"
                                            f"Surrounding lines:\n{ctx}"},
            ],
        }
        resp = _post_json(url, body, {}, timeout=float(os.environ.get("NORA_PII_LLM_TIMEOUT", "120")), retries=2)
        try:
            v = json.loads(resp["message"]["content"])["verdict"]
        except (KeyError, TypeError, ValueError):
            v = "unsure"
        out.append(v if v in ("real", "fake", "unsure") else "unsure")
    return out


if __name__ == "__main__":
    if sys.argv[1:] == ["--laya-worker"]:
        sys.exit(_laya_worker())
    print(__doc__)
    sys.exit(2)
