"""Mechanical signals, aggregation and the spot-check file (design/context-plan/14 §5).

Run on norty in ~/composer-labels after label.py. Writes:
  aggregate.json, aggregate.txt   counts only (these two are copied into git)
  spotcheck.md                    20 turns with text, for the operator; stays on norty

Mechanical signals are positive evidence only: a tool call or a string overlap
says a unit was used; no signal does not say it was not needed. Token figures
are estimates from node 09's chars->tokens fit, not tokenizer counts.
"""
import glob, json, random, re, statistics as st, collections

A_ASCII, B_CJK, INTERCEPT = 0.259, 0.906, 2073
RESIDENT = {"now", "search_memory", "web_search", "web_fetch", "note_read", "send_reply"}
RESIDENT_TOK = round(A_ASCII * (433 + 579 + 800 + 563 + 624 + 2501))  # see extract.py
FRAMING_TOK = INTERCEPT - RESIDENT_TOK  # message framing + other schemas, kept as floor

TOOL_UNITS = {
    "document_read": ["block.documents", "doctrine.conduct.document"],
    "document_publish": ["block.documents", "doctrine.conduct.document", "doctrine.conduct.records"],
    "document_set_status": ["block.documents", "doctrine.conduct.document"],
    "media_fetch": ["doctrine.pictures"], "media_search": ["doctrine.pictures"], "media_send": ["doctrine.pictures"],
    "where_is": ["block.presence"], "where_been": ["block.presence"], "saved_places": ["block.presence"],
    "online_now": ["block.presence"], "find_parking": ["block.presence"], "parking_status": ["block.presence"],
    "note_read": ["block.notes"], "recall_lookups": ["block.notes"],
    "note_update": ["block.notes", "doctrine.conduct.records"], "remember": ["block.notes", "doctrine.conduct.records"],
    "remind": ["block.notes", "doctrine.conduct.records"],
    "create_event": ["doctrine.conduct.records"], "update_event": ["doctrine.conduct.records"],
    "skills": ["skills.available"], "load_tools": ["tools.catalogue"],
    "lookup_person": ["block.room_members"], "who_said": ["block.room_members"], "group_members": ["block.room_members"],
}
CJK = re.compile(r"[㐀-鿿]")
STOP = set("""this that with have from what your about there their they them then than when where which would could should
been were will just like into some more also only very much make made well here know want need want does done dont didn
time today tomorrow yeah okay sure thanks nora http https www com""".split())


def tok(s):
    k = len(re.findall(r"[　-鿿＀-￯]", s))
    return A_ASCII * (len(s) - k) + B_CJK * k


def grams(s):
    s = re.sub(r"<[^>]{0,400}>", " ", s or "")
    out = {w for w in re.findall(r"[a-z][a-z'\-]{3,}", s.lower()) if w not in STOP}
    out |= set(re.findall(r"\d{2,}(?:[.:]\d+)?", s))
    cj = "".join(ch if CJK.match(ch) else " " for ch in s)
    for run in cj.split():
        out |= {run[i:i + 3] for i in range(len(run) - 2)}
    return out


def msg_text(m):
    return re.sub(r"<[^>]*>", "", m or "").strip()


def is_greeting(r):
    t = re.sub(r"@\S+", "", msg_text(r["message"])).strip()
    others = [x for x in r["tools"] if x["name"] != "send_reply"]
    return len(t) <= 25 and "?" not in t and "？" not in t and not others and not r["media_codes"]


def mech(r):
    sig = collections.defaultdict(set)
    for t in r["tools"]:
        n = t["name"]
        for u in TOOL_UNITS.get(n, []):
            sig[u].add("tool:" + n)
        if n.startswith("computer_"):
            sig["block.computer"].add("tool:" + n)
        if n in RESIDENT:
            sig["tools.resident." + n].add("tool:" + n)
        elif n not in ("load_tools", "skills"):
            # a deferred tool is reachable only through the catalogue
            sig["tools.catalogue"].add("deferred:" + n)
    if r["media_codes"]:
        sig["doctrine.pictures"].add("media_code")
    out = " ".join(r["replies"]) + " " + (r["final_text"] if not r["replies"] else "") + " " + " ".join(t["args"] for t in r["tools"])
    go = grams(out)
    seen = grams(r["message"] + " ".join(r["transcript"]["last15"]))
    for uid, u in r["units"].items():
        if not uid.startswith("doctrine"):
            continue
        seen |= grams(u["text"] or "")
    for uid, u in r["units"].items():
        if not uid.startswith("block.") or not u["text"]:
            continue
        ov = (go & grams(u["text"])) - seen
        if len(ov) >= 3:
            sig[uid].add("overlap:%d" % len(ov))
    return {k: sorted(v) for k, v in sig.items() if k in r["units"]}


def pct(n, d):
    return round(100.0 * n / d, 1) if d else None


def q(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(p * len(v)))] if v else None


def main():
    turns = {json.load(open(f))["id"]: json.load(open(f)) for f in glob.glob("turns/*.json")}
    labels = {}
    status = collections.Counter()
    for f in glob.glob("labels/*.json"):
        d = json.load(open(f))
        status[d["status"]] += 1
        if d["status"] == "ok":
            labels[d["id"]] = d["parsed"]
    calls = sum(1 for _ in open("calls.log"))
    U = collections.defaultdict(lambda: collections.Counter())
    tokens = collections.defaultdict(list)
    extent = collections.Counter()
    missing = collections.Counter()
    per_turn = []
    agree = collections.Counter()
    for tid, lab in labels.items():
        r = turns[tid]
        m = mech(r)
        ext = lab.get("transcript_extent")
        extent[ext] += 1
        for x in lab.get("missing") or []:
            if isinstance(x, dict) and x.get("unit"):
                missing[x["unit"]] += 1
        composed = r["floor_tokens"] + FRAMING_TOK
        full = r["floor_tokens"] + FRAMING_TOK + r["transcript"]["tokens"]
        for uid, u in r["units"].items():
            ln = lab["units"].get(uid, {})
            need = bool(ln.get("needed"))
            c = U[uid]
            c["present"] += 1
            tokens[uid].append(u["tokens"])
            c["llm_need"] += need
            full += u["tokens"]
            if need:
                composed += u["tokens"]
            if uid in m:
                c["mech"] += 1
                c["mech_and_llm"] += need
                agree["mech_pos"] += 1
                agree["mech_pos_llm_need"] += need
            else:
                c["llm_need_no_mech"] += need
        composed += r["transcript"]["last5_tokens"] if ext == "last_few" else r["transcript"]["tokens"]
        per_turn.append({"greeting": is_greeting(r), "composed": round(composed), "full_est": round(full),
                         "actual": r["input_tokens"], "cache_read": r["cache_read"], "extent": ext})
    unit_rows = []
    for uid in sorted(U):
        c = U[uid]
        unit_rows.append({"unit": uid, "present": c["present"], "llm_need_pct": pct(c["llm_need"], c["present"]),
                          "mech_signal": c["mech"], "mech_pct": pct(c["mech"], c["present"]),
                          "llm_agrees_with_mech_pct": pct(c["mech_and_llm"], c["mech"]),
                          "llm_need_without_mech": c["llm_need_no_mech"],
                          "median_tokens": round(st.median(tokens[uid]))})

    def summ(rows, key):
        v = [x[key] for x in rows if x[key] is not None]
        return {"n": len(v), "median": q(v, 0.5), "p10": q(v, 0.1), "p90": q(v, 0.9)} if v else None
    G = [x for x in per_turn if x["greeting"]]
    T = [x for x in per_turn if not x["greeting"]]
    agg = {
        "labelled_turns": len(labels), "label_status": dict(status), "labeller_alias": "nora-judge",
        "http_calls_total": calls,
        "window": [min(turns[t]["ts"] for t in labels), max(turns[t]["ts"] for t in labels)] if labels else None,
        "token_note": "estimates: node 09 fit, 0.259/ascii char, 0.906/CJK char, intercept 2073 split into resident schemas %d + framing %d" % (RESIDENT_TOK, FRAMING_TOK),
        "mech_agreement": {"mech_positive_pairs": agree["mech_pos"], "llm_need_on_those": agree["mech_pos_llm_need"],
                           "pct": pct(agree["mech_pos_llm_need"], agree["mech_pos"])},
        "transcript_extent": dict(extent), "missing_suggested": dict(missing.most_common()),
        "sizes": {"greeting": {"composed": summ(G, "composed"), "actual": summ(G, "actual"), "full_est": summ(G, "full_est")},
                  "typical": {"composed": summ(T, "composed"), "actual": summ(T, "actual"), "full_est": summ(T, "full_est")},
                  "all": {"composed": summ(per_turn, "composed"), "actual": summ(per_turn, "actual"),
                          "cache_read": summ(per_turn, "cache_read")}},
        "composed_histogram_k": dict(sorted(collections.Counter(min(30, x["composed"] // 1000) for x in per_turn).items())),
        "targets": {"greeting": "< 5k", "typical": "~10k"},
        "units": unit_rows,
    }
    json.dump(agg, open("aggregate.json", "w"), indent=1)
    lines = ["labelled %d turns (status %s), http calls %d, window %s" % (len(labels), dict(status), calls, agg["window"]),
             agg["token_note"],
             "mechanical positives %(mech_positive_pairs)d, labeller says needed on %(llm_need_on_those)d (%(pct)s%%)" % agg["mech_agreement"],
             "transcript extent %s" % dict(extent), "missing suggested %s" % dict(missing.most_common()), ""]
    for k in ("greeting", "typical"):
        s = agg["sizes"][k]
        lines.append("%-8s n=%s composed median %s (p10 %s, p90 %s) | actual first call median %s | full estimate median %s" % (
            k, s["composed"] and s["composed"]["n"], s["composed"] and s["composed"]["median"], s["composed"] and s["composed"]["p10"],
            s["composed"] and s["composed"]["p90"], s["actual"] and s["actual"]["median"], s["full_est"] and s["full_est"]["median"]))
    lines.append("composed size histogram (k tokens: turns) %s" % agg["composed_histogram_k"])
    lines.append("")
    lines.append("%-40s %7s %8s %6s %7s %9s %8s %6s" % ("unit", "present", "llm_need", "mech", "mech%", "agree%", "llm_nomech", "tok"))
    for u in sorted(unit_rows, key=lambda x: -(x["llm_need_pct"] or 0)):
        lines.append("%-40s %7d %7s%% %6d %6s%% %8s%% %8d %6d" % (u["unit"], u["present"], u["llm_need_pct"], u["mech_signal"],
                     u["mech_pct"], u["llm_agrees_with_mech_pct"], u["llm_need_without_mech"], u["median_tokens"]))
    open("aggregate.txt", "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))

    # spot-check sample (room text: stays on norty)
    rnd = random.Random(20261012)
    ids = sorted(labels)
    pick = rnd.sample(ids, min(20, len(ids)))
    out = ["# Composer labels: spot-check sample (20 turns)", "",
           "Labeller: nora-judge. Mark each label you disagree with; the disagreement rate is the number design/context-plan/14 §5 step 3 asks for.", ""]
    for i, tid in enumerate(pick, 1):
        r, lab, m = turns[tid], labels[tid], mech(turns[tid])
        out += ["## %d. trace %s (%s, %s)%s" % (i, tid, r["platform"], r["ts"], "  [greeting-like]" if is_greeting(r) else ""), "",
                "**Message:** " + msg_text(r["message"])[:600], "",
                "**Reply:** " + (" / ".join(r["replies"]) or "(no send_reply) " + r["final_text"][:400])[:800], "",
                "**Tools:** " + (", ".join(t["name"] for t in r["tools"]) or "none"), "",
                "**Transcript extent:** %s; **missing:** %s" % (lab.get("transcript_extent"), lab.get("missing")), "",
                "| unit | needed | mech | why |", "|---|---|---|---|"]
        for uid in sorted(r["units"]):
            ln = lab["units"].get(uid, {})
            out.append("| %s | %s | %s | %s |" % (uid, "YES" if ln.get("needed") else "no", ",".join(m.get(uid, [])),
                                                   str(ln.get("why", "")).replace("|", "/")))
        out.append("")
    open("spotcheck.md", "w").write("\n".join(out))


if __name__ == "__main__":
    main()
