"""CLOUD VARIANT (clef:27b on ollama.com). Ask Tev1 one turn at a time over Ollama's /v1/systemone (127.0.0.1:11434), concurrency 1.

Run on norty in ~/composer-labels/step2:
  python3 run.py --arm B --model tev1:0.8b --limit 5            # first 5, read by hand
  python3 run.py --arm A --model tev1-4b-ctx8k --ids sample40.txt
Arm A (§4a): one Noul per unit present in the turn's recorded prompt (floor units
excluded), card = cards.json, all in ONE request. Arm B (§4): D1 Choice over ten
turn kinds + D2 Choice over transcript depth, one request.
Writes out/<arm>/<model>/<id>.json: probabilities, seconds, usage; no room text.
A failed request is recorded as {"error": ...} (fail closed: full prompt).
"""
import argparse, json, os, subprocess, time, urllib.error, urllib.request

from common import (D1_CRITERIA, D1_INSTR, D2_CRITERIA, D2_INSTR, FLOOR_UNITS, HERE, NOUL_CRITERIA, NOUL_INSTR,
                    load_turns, state_of)

URL = "https://ollama.com/v1/systemone"
KEY = os.environ["OLLAMA_CLOUD_API_KEY"]
CARDS = json.load(open(os.path.join(HERE, "cards.json")))


def questions(arm, r):
    if arm == "B":
        return {"d1": {"type": "choice", "instructions": D1_INSTR, "criteria": D1_CRITERIA},
                "d2": {"type": "choice", "instructions": D2_INSTR, "criteria": D2_CRITERIA}}
    q = {}
    for uid in sorted(r["units"]):
        if uid in FLOOR_UNITS:
            continue
        c = CARDS[uid]
        q[uid] = {"type": "noul", "instructions": {"question": NOUL_INSTR,
                                                     "part": {"kind": c["kind"], "what_it_is_for": c["card"]}},
                  "criteria": NOUL_CRITERIA}
    return q


def post(body, timeout=900):
    req = urllib.request.Request(URL, data=json.dumps(body, ensure_ascii=False).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + KEY})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", choices="AB", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--ids", help="file of turn ids (one per line) to run instead of the newest N")
    ap.add_argument("--minutes", type=float, default=120)
    a = ap.parse_args()
    outdir = os.path.join(HERE, "out", a.arm, a.model.replace(":", "_"))
    os.makedirs(outdir, exist_ok=True)
    turns = load_turns()
    if a.ids:
        want = [l.strip() for l in open(a.ids) if l.strip()]
        by = {r["id"]: (r, g) for r, g in turns}
        turns = [by[i] for i in want]
    turns = turns[: a.limit]
    t0 = time.perf_counter()
    post({"model": a.model, "state": {"message": "hello"}, "keep_alive": "30m",
          "questions": {"w": {"type": "noul", "instructions": "Is this a greeting?"}}})
    with open(os.path.join(outdir, "_warmup.jsonl"), "a") as w:
        w.write(json.dumps({"t": time.time(), "seconds": round(time.perf_counter() - t0, 3)}) + "\n")
    print("warmup %.2fs" % (time.perf_counter() - t0), flush=True)
    deadline = time.time() + a.minutes * 60
    for r, aug in turns:
        dst = os.path.join(outdir, r["id"] + ".json")
        if os.path.exists(dst):
            continue
        if time.time() > deadline:
            print("deadline", flush=True)
            break
        body = {"model": a.model, "state": state_of(r, aug), "questions": questions(a.arm, r), "keep_alive": "30m"}
        t1 = time.perf_counter()
        rec = {"id": r["id"], "arm": a.arm, "model": a.model, "n_questions": len(body["questions"]), "retried": False}
        try:
            try:
                resp = post(body)
            except urllib.error.HTTPError as e:
                # Seen locally: after a long request the runner can start answering every request with
                # 500 "scoring returned an invalid candidate probability" until it is reloaded. Reload, retry once.
                if e.code != 500:
                    raise
                time.sleep(5)  # cloud: no local runner to reload
                rec["retried"] = True
                t1 = time.perf_counter()
                resp = post(body)
            rec["seconds"] = round(time.perf_counter() - t1, 3)
            ans = resp.get("answers") or {}
            if a.arm == "A":
                rec["p"] = {k: (ans.get(k) or {}).get("noul") for k in body["questions"]}
                rec["missing"] = [k for k, v in rec["p"].items() if not isinstance(v, (int, float))]
            else:
                for k in ("d1", "d2"):
                    x = ans.get(k) or {}
                    rec[k] = {"choice": x.get("choice"), "p": x.get("probabilities"), "confidence": x.get("confidence")}
                rec["missing"] = [k for k in ("d1", "d2") if not isinstance(rec[k]["p"], dict)]
            rec["usage"] = resp.get("usage")
        except Exception as e:  # HTTP 500, timeout, bad JSON: recorded, scored as fail-closed
            rec["seconds"] = round(time.perf_counter() - t1, 3)
            body_txt = ""
            if hasattr(e, "read"):
                try:
                    body_txt = e.read().decode(errors="replace")[:300]
                except Exception:
                    pass
            rec["error"] = (repr(e) + " " + body_txt)[:400]
        json.dump(rec, open(dst, "w"))
        print(r["id"][:8], rec.get("error", "ok")[:60], rec["seconds"], flush=True)


if __name__ == "__main__":
    main()
