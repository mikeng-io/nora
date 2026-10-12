"""What drives Tev1 0.8B latency on norty (operator, 2026-10-12). Writes latency.json (timings and token counts only).

Run on norty in ~/composer-labels/step2 with nothing else using Tev1. Every request carries a fresh
`nonce` as the FIRST state field so no request reuses a cached prompt prefix (a repeated identical
request was measured at ~1/3 of the cold time); `warm_repeat` sends the same body twice to show that.
  B split:   D1 alone / D2 alone / D1+D2, each cold, on the same turns
  A scaling: N Nouls (1, 2, 4, 8, 16, 32) with the turn's full state and with a minimal state
"""
import json, os, statistics as st, subprocess, time, urllib.error, uuid

import run
from common import HERE, load_turns, state_of

MODEL = "tev1-08b-ctx16k"
ERRORS = []  # question counts of requests that returned 500
PHASES = os.environ.get("PHASES", "B,A,warm").split(",")
OUT = os.path.join(HERE, os.environ.get("OUT", "latency.json"))


def call(state, qs):
    body = {"model": MODEL, "state": dict([("nonce", uuid.uuid4().hex)] + list(state.items())), "questions": qs,
            "keep_alive": "30m"}
    t = time.perf_counter()
    try:
        d = run.post(body)
    except urllib.error.HTTPError:
        # the runner's "invalid candidate probability" wedge (see run.py): count it, reload, carry on
        ERRORS.append(len(qs))
        subprocess.run(["ollama", "stop", MODEL], capture_output=True)
        return None, None
    return time.perf_counter() - t, (d.get("usage") or {}).get("input_tokens")


def main():
    turns = load_turns()
    sample = [turns[i] for i in range(0, 200, 20)]  # 10 turns spread over the window
    call({"m": "hi"}, {"w": {"type": "noul", "instructions": "greeting?"}})  # load
    out = {"model": MODEL, "n_turns": len(sample), "B": {}, "A": {}, "warm_repeat": {}}
    b_conds = (("d1", ["d1"]), ("d2", ["d2"]), ("d1+d2", ["d1", "d2"])) if "B" in PHASES else ()
    for name, keys in b_conds:
        secs, toks = [], []
        for r, aug in sample:
            qs = {k: v for k, v in run.questions("B", r).items() if k in keys}
            s, tk = call(state_of(r, aug), qs)
            if s is not None:
                secs.append(s)
                toks.append(tk)
        out["B"][name] = {"seconds_median": round(st.median(secs), 2), "seconds_max": round(max(secs), 2),
                          "input_tokens_median": st.median(toks)}
        print(name, out["B"][name], flush=True)
    for state_kind in ("full", "minimal") if "A" in PHASES else ():
        for n in [int(x) for x in os.environ.get("NS", "1,2,4,8,16,32").split(",")]:
            secs, toks = [], []
            for r, aug in sample[:4]:
                qa = dict(list(run.questions("A", r).items())[:n])
                stt = state_of(r, aug) if state_kind == "full" else {"message": state_of(r, aug)["message"]}
                s, tk = call(stt, qa)
                if s is not None:
                    secs.append(s)
                    toks.append(tk)
            if not secs:
                out["A"]["%s_state/%d" % (state_kind, n)] = {"all_failed": True}
                continue
            key = "%s_state/%d" % (state_kind, n)
            out["A"][key] = {"seconds_median": round(st.median(secs), 2), "per_question": round(st.median(secs) / n, 3),
                             "input_tokens_median": st.median(toks)}
            print(key, out["A"][key], flush=True)
    if "warm" not in PHASES:
        out["errors_500_at_n_questions"] = ERRORS
        json.dump(out, open(OUT, "w"), indent=1)
        return
    r, aug = sample[0]
    body = {"model": MODEL, "state": state_of(r, aug), "questions": run.questions("B", r)}
    t = time.perf_counter(); run.post(body); cold = time.perf_counter() - t
    t = time.perf_counter(); run.post(body); warm = time.perf_counter() - t
    out["warm_repeat"] = {"cold": round(cold, 2), "identical_repeat": round(warm, 2)}
    print(out["warm_repeat"], flush=True)
    out["errors_500_at_n_questions"] = ERRORS
    json.dump(out, open(OUT, "w"), indent=1)


if __name__ == "__main__":
    main()
