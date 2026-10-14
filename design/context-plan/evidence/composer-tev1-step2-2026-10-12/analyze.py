"""Apply §4a's rules to Tev1's answers and measure against what each turn actually did.

Run on norty in ~/composer-labels/step2 after run.py. Writes aggregate.json and
aggregate.txt (counts only; these two go into git).

For every (arm, model) run in out/ and every threshold multiplier (0.5, 1, 2):
  composed = floor + base (arm B) + included parts/bundles + dependencies + the
  media-code override; a request that errored or missed an answer composes the
  full recorded prompt (fail closed).
Recall is per evidence item (a tool call, a media code, a reused block): the item
is kept when every part it implies that existed in the turn's recorded prompt is
in the composed set. Arm B also reports D1-recall: the item's bundle was selected.
A `code_only` row (floor + base + override, no model) shows what the model adds.
Tokens are node 09's fit (see ../composer-labels-2026-10-12/README.txt).
"""
import collections, glob, json, os, statistics as st

from common import (AG, BASE, BUNDLES, DEPS, FLOOR_UNITS, HERE, MEDIA_OVERRIDE, THRESH, cjk_share, evidence,
                    load_turns, part_class)

MULTS = (0.5, 1.0, 2.0)
T_D1 = 0.15


def q(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(p * len(v)))] if v else None


def close(units, present):
    c = set(units) | (FLOOR_UNITS & present)
    for u in list(c):
        c |= DEPS.get(u, set())
    return c & present


def compose_A(r, rec, m):
    present = set(r["units"])
    if rec is None or rec.get("error") or rec.get("missing"):
        return present, "full", None
    inc = {u for u, p in rec["p"].items() if p >= THRESH[part_class(u)] * m}
    if r["media_codes"]:
        inc |= MEDIA_OVERRIDE
    return close(inc, present), "window", None


def compose_B(r, rec, m):
    present = set(r["units"])
    if rec is None or rec.get("error") or rec.get("missing"):
        return present, "full", None
    p1 = rec["d1"]["p"]
    sel = {o for o, p in p1.items() if p >= T_D1 * m} | {max(p1, key=p1.get)}
    inc = set(BASE)
    for b in sel | ({"media"} if r["media_codes"] else set()):
        inc |= BUNDLES[b]
    return close(inc, present), rec["d2"]["choice"], sel


def compose_code_only(r):
    inc = set(BASE) | (MEDIA_OVERRIDE if r["media_codes"] else set())
    return close(inc, set(r["units"])), "window", None


def tokens(r, comp, extent):
    base = r["floor_tokens"] + AG.FRAMING_TOK + sum(r["units"][u]["tokens"] for u in comp)
    tr = r["transcript"]["last5_tokens"] if extent == "last_few" else r["transcript"]["tokens"]
    return round(base + tr)


def score(name, turns, ev, comp_fn, recs):
    """recs: id -> answer record or None when this run has no answer for that turn (excluded)."""
    by_b = collections.defaultdict(collections.Counter)
    lang = collections.defaultdict(collections.Counter)
    missed_parts = collections.Counter()
    sizes = collections.defaultdict(list)
    ratio = []
    multi = collections.Counter()
    d2 = collections.Counter()
    n = n_ev = turns_all_kept = 0
    for r, aug in turns:
        if r["id"] not in recs:
            continue
        n += 1
        comp, extent, sel = comp_fn(r, recs[r["id"]])
        tk = tokens(r, comp, extent)
        g = "greeting" if AG.is_greeting(r) else "other"
        sizes[g].append(tk)
        if r["input_tokens"]:
            ratio.append(tk / r["input_tokens"])
        items, depth, _ = ev[r["id"]]
        lg = "cantonese" if cjk_share(r) >= 0.3 else "english"
        if sel is not None:
            evb = {i["bundle"] for i in items}
            multi[("sel>=2" if len(sel) >= 2 else "sel1", "ev>=2" if len(evb) >= 2 else ("ev1" if evb else "ev0"))] += 1
            if len(evb) >= 2:
                multi["ev>=2_all_selected"] += evb <= sel
            d2[(extent, depth)] += 1
        if not items:
            continue
        n_ev += 1
        all_kept = True
        for i in items:
            kept = set(i["parts"]) <= comp
            k = i["bundle"] + ("(code)" if i["src"] == "media_code" else "")
            by_b[k]["items"] += 1
            by_b[k]["kept"] += kept
            if sel is not None:
                by_b[k]["d1_selected"] += i["bundle"] in sel
            lang[lg]["items"] += 1
            lang[lg]["kept"] += kept
            if not kept:
                all_kept = False
                missed_parts.update(set(i["parts"]) - comp)
        turns_all_kept += all_kept
    tot_i = sum(c["items"] for c in by_b.values())
    tot_k = sum(c["kept"] for c in by_b.values())
    out = {
        "name": name, "n_turns": n, "n_turns_with_evidence": n_ev,
        "recall_items": [tot_k, tot_i, round(tot_k / tot_i, 3) if tot_i else None],
        "turns_all_evidence_kept": [turns_all_kept, n_ev],
        "per_bundle": {k: dict(v) for k, v in sorted(by_b.items())},
        "by_language": {k: dict(v) for k, v in lang.items()},
        "missed_parts_top": missed_parts.most_common(12),
        "tokens": {g: {"n": len(v), "median": q(v, .5), "p90": q(v, .9), "under_5k": sum(x < 5000 for x in v),
                       "under_10k": sum(x <= 10000 for x in v)} for g, v in sizes.items()},
        "composed_over_recorded_median": round(st.median(ratio), 3) if ratio else None,
    }
    if multi:
        out["multi_select"] = {"/".join(k) if isinstance(k, tuple) else k: v for k, v in sorted(multi.items(), key=str)}
        out["d2_vs_depth_evidence"] = {"/".join(k): v for k, v in sorted(d2.items())}
    return out


def run_stats(recs, outdir):
    secs = [x["seconds"] for x in recs.values() if not x.get("error")]
    warm = []
    wf = os.path.join(outdir, "_warmup.jsonl")
    if os.path.exists(wf):
        warm = [json.loads(l)["seconds"] for l in open(wf)]
    errs = collections.Counter(x["error"][:60] for x in recs.values() if x.get("error"))
    s = {"n": len(recs), "errors": dict(errs), "missing_answers": sum(bool(x.get("missing")) for x in recs.values()),
         "seconds_median": q(secs, .5), "seconds_p90": q(secs, .9), "seconds_max": max(secs) if secs else None,
         "first_load_seconds": warm,
         "n_questions_median": q([x["n_questions"] for x in recs.values()], .5)}
    ok = [x for x in recs.values() if not x.get("error") and not x.get("missing")]
    if ok and "p" in ok[0]:
        ps = [p for x in ok for p in x["p"].values()]
        s["noul_spread"] = {"n": len(ps), "ge_0.5": sum(p >= .5 for p in ps), "lt_0.1": sum(p < .1 for p in ps),
                            "median": round(q(ps, .5), 3)}
    if ok and "d1" in ok[0]:
        s["d1_argmax"] = dict(collections.Counter(x["d1"]["choice"] for x in ok))
        s["d2_choice"] = dict(collections.Counter(x["d2"]["choice"] for x in ok))
        s["d1_top_p_median"] = round(q([max(x["d1"]["p"].values()) for x in ok], .5), 3)
    return s


def main():
    turns = load_turns()
    ev = {r["id"]: evidence(r, aug) for r, aug in turns}
    res = {"ground_truth": {
        "turns": len(turns), "with_any_evidence": sum(bool(v[0]) for v in ev.values()),
        "items_by_source": dict(collections.Counter(i["src"].split(":")[0] + (":" + i["src"].split(":")[1] if i["src"].startswith("overlap") else "") for v in ev.values() for i in v[0])),
        "turns_by_bundle": dict(collections.Counter(b for v in ev.values() for b in {i["bundle"] for i in v[0]})),
        "turns_by_n_bundles": dict(collections.Counter(len({i["bundle"] for i in v[0]}) for v in ev.values())),
        "depth": dict(collections.Counter(v[1] for v in ev.values())),
        "greeting_like": sum(AG.is_greeting(r) for r, _ in turns),
        "cantonese_ge_0.3_cjk": sum(cjk_share(r) >= 0.3 for r, _ in turns),
        "floor_plus_framing_tokens": {"median": q([r["floor_tokens"] + AG.FRAMING_TOK for r, _ in turns], .5)},
        "transcript_tokens": {"median_window": q([r["transcript"]["tokens"] for r, _ in turns], .5),
                              "median_last5": q([r["transcript"]["last5_tokens"] for r, _ in turns], .5)},
        "recorded_first_call": {"median": q([r["input_tokens"] for r, _ in turns if r["input_tokens"]], .5),
                                "p90": q([r["input_tokens"] for r, _ in turns if r["input_tokens"]], .9)},
    }, "runs": {}, "scores": []}
    all_ids = {r["id"]: None for r, _ in turns}
    res["scores"].append(score("code_only (floor+base+media override, no model)", turns, ev,
                               lambda r, rec: compose_code_only(r), all_ids))
    for d in sorted(glob.glob(os.path.join(HERE, "out", "*", "*"))):
        arm, model = d.split(os.sep)[-2:]
        recs = {os.path.basename(f)[:-5]: json.load(open(f)) for f in glob.glob(d + "/*.json")}
        if not recs:
            continue
        res["runs"]["%s/%s" % (arm, model)] = run_stats(recs, d)
        fn = compose_A if arm == "A" else compose_B
        for m in MULTS:
            res["scores"].append(score("%s/%s x%.1f" % (arm, model, m), turns, ev,
                                       lambda r, rec, m=m: fn(r, rec, m), recs))
        if arm == "A":  # same turns, code-only, for a like-for-like reference
            res["scores"].append(score("code_only on %s/%s turns" % (arm, model), turns, ev,
                                       lambda r, rec: compose_code_only(r), recs))
    # like-for-like: the 0.8B run scored on exactly the turns the 4B run answered
    for arm in "AB":
        small, big = (os.path.join(HERE, "out", arm, m) for m in ("tev1-08b-ctx16k", "tev1-4b-ctx16k"))
        if not (os.path.isdir(small) and os.path.isdir(big)):
            continue
        ids = {os.path.basename(f)[:-5] for f in glob.glob(big + "/*.json")}
        recs = {i: json.load(open(os.path.join(small, i + ".json"))) for i in ids
                if os.path.exists(os.path.join(small, i + ".json"))}
        fn = compose_A if arm == "A" else compose_B
        for m in MULTS:
            res["scores"].append(score("%s/tev1-08b on 4B's %d turns x%.1f" % (arm, len(recs), m), turns, ev,
                                       lambda r, rec, m=m: fn(r, rec, m), recs))
    # arm A discrimination: does P(needed) rank evidenced parts above the rest? (AUC over all (turn, part) pairs)
    for d in sorted(glob.glob(os.path.join(HERE, "out", "A", "*"))):
        pos, neg = [], []
        for f in glob.glob(d + "/*.json"):
            rec = json.load(open(f))
            if rec.get("error") or rec.get("missing"):
                continue
            items = ev[rec["id"]][0]
            evp = {p for i in items for p in i["parts"]}
            for u, p in rec["p"].items():
                (pos if u in evp else neg).append(p)
        if pos and neg:
            auc = sum((a > b) + 0.5 * (a == b) for a in pos for b in neg) / (len(pos) * len(neg))
            res["runs"]["A/" + os.path.basename(d)]["discrimination"] = {
                "evidenced_parts": len(pos), "other_parts": len(neg), "p_median_evidenced": round(q(pos, .5), 3),
                "p_median_other": round(q(neg, .5), 3), "auc": round(auc, 3)}
        # arm B on exactly arm A's turns
        ids = {os.path.basename(f)[:-5] for f in glob.glob(d + "/*.json")}
        bdir = os.path.join(HERE, "out", "B", os.path.basename(d))
        recs = {i: json.load(open(os.path.join(bdir, i + ".json"))) for i in ids
                if os.path.exists(os.path.join(bdir, i + ".json"))}
        if recs:
            res["scores"].append(score("B/%s on A's %d turns x1.0" % (os.path.basename(d), len(recs)), turns, ev,
                                       lambda r, rec: compose_B(r, rec, 1.0), recs))
    json.dump(res, open(os.path.join(HERE, "aggregate.json"), "w"), indent=1)
    with open(os.path.join(HERE, "aggregate.txt"), "w") as w:
        w.write("ground truth: %s\n\n" % json.dumps(res["ground_truth"]))
        for k, v in res["runs"].items():
            w.write("run %s: %s\n" % (k, json.dumps(v)))
        w.write("\n%-48s %5s %16s %10s %18s %18s %6s\n" % ("arm/model x mult", "n", "recall kept/items", "turns ok",
                                                       "greet med/p90", "other med/p90", "ratio"))
        for s in res["scores"]:
            t = s["tokens"]
            gr, ot = t.get("greeting", {}), t.get("other", {})
            w.write("%-48s %5d %6d/%-4d %.3f %4d/%-4d %8s/%-8s %8s/%-8s %6s\n" % (
                s["name"][:48], s["n_turns"], s["recall_items"][0], s["recall_items"][1], s["recall_items"][2] or 0,
                s["turns_all_evidence_kept"][0], s["turns_all_evidence_kept"][1], gr.get("median"), gr.get("p90"),
                ot.get("median"), ot.get("p90"), s["composed_over_recorded_median"]))
        for s in res["scores"]:
            w.write("\n== %s\n per_bundle %s\n by_language %s\n missed %s\n tokens %s\n" % (
                s["name"], json.dumps(s["per_bundle"]), json.dumps(s["by_language"]),
                json.dumps(s["missed_parts_top"]), json.dumps(s["tokens"])))
            if "multi_select" in s:
                w.write(" multi_select %s\n d2 %s\n" % (json.dumps(s["multi_select"]), json.dumps(s["d2_vs_depth_evidence"])))
    print(open(os.path.join(HERE, "aggregate.txt")).read())


if __name__ == "__main__":
    main()
