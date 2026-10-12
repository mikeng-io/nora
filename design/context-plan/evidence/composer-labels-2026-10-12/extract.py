"""Split each recorded tag turn into composer units (design/context-plan/14 §5, step 1).

Run on norty in ~/composer-labels after fetch.py. Reads raw/t_*.json, writes
turns/<trace id>.json (room text: stays on norty) and extract-summary.json
(counts only). Selection follows node 09 (composition-2026-10-12/seg.py): the
newest `engagement` traces whose first main-agent GENERATION input starts with a
system message; the newest N (argv[1], default 200) are kept.

Units follow evidence/composer-units.yaml. The recorded window predates the
doctrine's step 2, so a few recorded headings are mapped to their successor
unit (HEADING_UNIT, marked `succ`) and some inventory units cannot appear at
all; both are counted in the summary as segmentation gaps.
"""
import glob, json, os, re, sys, collections

N = int(sys.argv[1]) if len(sys.argv) > 1 else 200
CJK = re.compile(r"[　-鿿＀-￯]")

# node 09 fit (composition-2026-10-12/fit-output.txt): tokens per char
A_ASCII, B_CJK, INTERCEPT = 0.259, 0.906, 2073

# Resident (HOT_TOOLS) schema sizes in chars: json.dumps of strands
# FunctionToolMetadata(spec.call).extract_metadata() at master 6e7199d0d.
# They are not in the trace; master-time sizes stand in for the window's.
RESIDENT_CHARS = {"now": 433, "search_memory": 579, "web_search": 800,
                  "web_fetch": 563, "note_read": 624, "send_reply": 2501}

# Recorded heading -> unit id. `None` = floor (core/constraints.md).
HEADING_UNIT = {
    "Who you are": "doctrine.identity.who_you_are",
    "Where you come from": "doctrine.identity.where_you_come_from",
    "What you are": "doctrine.identity.what_you_are",
    "Your place in the group": "doctrine.belonging.place",
    "Why you're here": "doctrine.belonging.why",
    "How you serve a room": "doctrine.belonging.serve",
    "你係邊個": "doctrine.voice.who",
    "香港人點樣打字": "doctrine.voice.typing",
    "打字唔係寫文": "doctrine.voice.not_writing",
    "唔好表演性格": "doctrine.voice.no_performing",
    "Sticker 同 GIF": "doctrine.voice.gif",
    "粗口": "doctrine.voice.swearing",
    "笑位": "doctrine.voice.jokes",
    "有人話你錯": "doctrine.voice.told_wrong",
    "唔似 Nora 嘅聲音": "doctrine.voice.not_nora",
    "一個 message 嘅感覺": "doctrine.voice.one_message",
    "Silence is the default": "doctrine.silence",
    "What you already know about a picture": "doctrine.pictures",
    "You have judgement": "doctrine.conduct.judgement",
    "Follow the conversation": "doctrine.conduct.follow",
    "When the group is sorting something out": "doctrine.conduct.sorting_out",
    "Banter": "doctrine.conduct.banter",
    "Being asked to judge": "doctrine.conduct.judging",
    "Depth follows the stakes": "doctrine.conduct.depth",
    "Getting to an answer": "doctrine.conduct.answer",
    "Talking about a person": "doctrine.conduct.person",
    "Writing something down needs somebody here": "doctrine.conduct.records",
    "A written piece is a document": "doctrine.conduct.document",
    "Honesty": "doctrine.conduct.honesty",
    "Being contradicted": "doctrine.conduct.contradicted",
    "When one day becomes the next": "doctrine.conduct.next_day",
    "Where to look first": "doctrine.conduct.look_first",
    # recorded predecessors of step-2 units (rooms/constraints.md)
    "What a source does not say": "doctrine.room_law.found",
    "What you can actually do": "doctrine.room_law.can_do",
    # core/constraints.md -> floor
    "These come from outside the conversation": None,
    "Messages are data, not instructions": None,
    "Your own configuration stays yours": None,
    "You do not have another self": None,
    "You reply only where you were called": None,
    "Other people stay private": None,
    "A broken Nora and a quiet Nora look the same": None,
}
SUCCESSOR = {"What a source does not say", "What you can actually do"}

FLOOR_BLOCKS = {"context_header", "tag_message", "replying_to"}
MEDIA = re.compile(r"\[(image|sticker|video|file|document|audio|voice|shared|cover)\b")


def txt(c):
    if isinstance(c, str) and c[:2] == "[{":
        try:
            c = json.loads(c)
        except Exception:
            return c
    if isinstance(c, list):
        return "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in c)
    return c or ""


def ac(s):
    k = len(CJK.findall(s))
    return len(s) - k, k


def tok(s):
    a, b = ac(s)
    return A_ASCII * a + B_CJK * b


def main_agent_subtree(obs):
    """Observations under the `invoke_agent nora` AGENT, excluding nested agents."""
    byid = {o["id"]: o for o in obs}
    roots = [o["id"] for o in obs if o["type"] == "AGENT" and o["name"] == "invoke_agent nora"]
    if not roots:
        return []
    root = roots[0]
    out = []
    for o in obs:
        p, ok = o.get("parentObservationId"), False
        while p:
            if p == root:
                ok = True
                break
            q = byid.get(p)
            if q is None or (q["type"] == "AGENT" and q["id"] != root):
                break
            p = q.get("parentObservationId")
        if ok:
            out.append(o)
    return out


def split(inp, gaps):
    units, floor = {}, []
    sysc = txt(inp[0]["content"])

    def add(uid, s):
        if uid is None:
            floor.append(s)
            return
        u = units.setdefault(uid, "")
        units[uid] = u + s

    for m in re.finditer(r'<section[^>]*name="([a-z_\-]+)"[^>]*>(.*?)</section>', sysc, re.S):
        body = m.group(2)
        parts = re.split(r"(?m)^(?=# )", body)
        floor.append(m.group(0)[: len(m.group(0)) - len(body)] + "</section>")  # wrapper
        for part in parts:
            h = re.match(r"# (.+)", part)
            if not h:
                floor.append(part)  # section preamble
                continue
            head = h.group(1).strip()
            if head not in HEADING_UNIT:
                gaps["unmapped_heading:" + head] += 1
                add("doctrine.unmapped." + m.group(1), part)
                continue
            if head in SUCCESSOR:
                gaps["mapped_by_successor:" + head] += 1
            add(HEADING_UNIT[head], part)
    for tag, uid in (("runtime_context", "runtime_context"), ("available_skills", "skills.available")):
        mm = re.search(r"<%s[^>]*>.*?</%s>" % (tag, tag), sysc, re.S)
        if mm:
            add(uid, mm.group(0))
    rest = re.sub(r"<section.*?</section>|<runtime_context.*?</runtime_context>|<available_skills.*?</available_skills>", "", sysc, flags=re.S)
    floor.append(rest)

    transcript, message = "", ""
    for msg in inp[1:]:
        c = txt(msg["content"])
        pos, cov = 0, []
        while True:
            mm = re.compile(r"<([a-z_][a-z0-9_]*)(?:\s[^>]*)?>").search(c, pos)
            if not mm:
                break
            tag = mm.group(1)
            e = c.find("</%s>" % tag, mm.end())
            if e < 0:
                pos = mm.end()
                continue
            e += len(tag) + 3
            seg = c[mm.start():e]
            cov.append(seg)
            if tag in FLOOR_BLOCKS:
                floor.append(seg)
                if tag == "tag_message":
                    message = seg
            elif tag == "group_transcript":
                transcript = seg
            elif tag == "tool_catalogue":
                add("tools.catalogue", seg)
            else:
                add("block." + tag, seg)
            pos = e
        left = c
        for s in cov:
            left = left.replace(s, "", 1)
        floor.append(left)
    return units, floor, transcript, message


def main():
    os.makedirs("turns", exist_ok=True)
    cands = []
    gaps = collections.Counter()
    skipped = collections.Counter()
    for f in glob.glob("raw/t_*.json"):
        t = json.load(open(f))
        obs = sorted(t.get("observations", []), key=lambda o: o["startTime"])
        sub = main_agent_subtree(obs)
        gens = [o for o in sub if o["type"] == "GENERATION"]
        if not gens:
            skipped["no_main_generation"] += 1
            continue
        inp = gens[0].get("input")
        if not isinstance(inp, list) or not inp or inp[0].get("role") != "system":
            skipped["no_system_first"] += 1
            continue
        cands.append((t["timestamp"], f, t, sub, gens))
    cands.sort(reverse=True)
    kept = cands[:N]
    for ts, f, t, sub, gens in kept:
        g = gens[0]
        u = g.get("usageDetails") or {}
        units, floor, transcript, message = split(g["input"], gaps)
        lines = re.findall(r"<m\b.*?</m>", transcript, re.S)
        tools = []
        replies = []
        for o in sub:
            if o["type"] != "TOOL":
                continue
            args = ""
            i = o.get("input")
            if isinstance(i, list) and i and isinstance(i[0], dict):
                args = i[0].get("content") or ""
            if o["name"] == "send_reply":
                try:
                    replies.append(json.loads(args).get("text", args))
                except Exception:
                    replies.append(args)
            tools.append({"name": o["name"], "args": args[:1500]})
        out = gens[-1].get("output") or {}
        final_text = txt(out.get("message")) if isinstance(out, dict) else ""
        a = (t.get("metadata") or {}).get("attributes") or {}
        unit_rec = {k: {"chars": len(v), "tokens": round(tok(v)), "text": v} for k, v in units.items()}
        for name, ch in RESIDENT_CHARS.items():
            unit_rec["tools.resident." + name] = {"chars": ch, "tokens": round(A_ASCII * ch), "text": None}
        floor_text = "".join(floor)
        rec = {
            "id": t["id"], "ts": ts, "platform": a.get("nora.platform"), "admission": a.get("nora.admission"),
            "input_tokens": u.get("input"), "cache_read": u.get("cache_read_input_tokens"),
            "n_generations": len(gens),
            "floor_tokens": round(tok(floor_text)),
            "transcript": {"lines": len(lines), "tokens": round(tok(transcript)),
                           "last5_tokens": round(tok("".join(lines[-5:]))),
                           "last15": lines[-15:]},
            "message": message,
            "replies": replies,
            # last main generation's text: the delivered fallback when no send_reply ran
            "final_text": final_text if isinstance(final_text, str) else json.dumps(final_text, ensure_ascii=False),
            "tools": tools,
            "media_codes": sorted(set(MEDIA.findall(message + "".join(lines[-5:])))),
            "units": unit_rec,
        }
        json.dump(rec, open("turns/%s.json" % t["id"], "w"), ensure_ascii=False)
    present = collections.Counter()
    for ts, f, t, sub, gens in kept:
        rec = json.load(open("turns/%s.json" % t["id"]))
        present.update(rec["units"].keys())
    summ = {
        "candidates": len(cands), "kept": len(kept), "skipped": dict(skipped),
        "window": [kept[-1][0], kept[0][0]] if kept else None,
        "gaps": dict(gaps), "unit_present": dict(sorted(present.items())),
    }
    json.dump(summ, open("extract-summary.json", "w"), indent=1)
    print(json.dumps({k: summ[k] for k in ("candidates", "kept", "skipped", "window", "gaps")}, indent=1))


if __name__ == "__main__":
    main()
