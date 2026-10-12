"""LLM labeller: one call per recorded turn (design/context-plan/14 §5, step 2).

Run on norty in ~/composer-labels after extract.py. Calls norty's OmniRoute
(the eval judge's gateway, evals/__main__.py --judge-base-url) with the judge
alias, key read from the deploy checkout's .env. Raw request metadata and raw
outputs go to labels/<id>.json on norty (room text: never copied off).

  python3 label.py --limit 5            # sanity pass
  python3 label.py --limit 200 --budget 215

`--budget` is a hard cap on HTTP calls across runs (calls.log counts them);
a turn whose label file is already valid is never re-called.
"""
import argparse, concurrent.futures as cf, glob, json, os, re, threading, time, urllib.request

ENV = os.path.expanduser("~/workspace/nora/.env")
URL = "http://127.0.0.1:20128/v1/chat/completions"
MODEL = "nora-judge"

# Absent-unit menu: inventory ids that a recorded turn can lack, with what they carry.
ABSENT_MENU = {
    "block.self": "Nora's own self-description and growth notes",
    "block.lessons": "lessons Nora learned from past mistakes",
    "block.room_members": "who is in this room (names, handles)",
    "block.peer_card": "what Nora knows about the person who tagged her",
    "block.peer_history": "recent history with the tagger",
    "block.self_card": "how the tagger sees Nora / their relationship",
    "block.notes": "the room's notes (dated plans, facts written down)",
    "block.culture": "the room's culture: running jokes, norms",
    "block.unit_culture": "culture of the wider group unit",
    "block.character": "the room's character profile",
    "block.computer": "state of Nora's computer (files, sessions)",
    "block.documents": "index of documents Nora wrote for this room",
    "block.standing": "the room's standing summary (who, what is going on)",
    "block.standing_working_set": "open items the room is working on",
    "block.presence": "members' current locations / presence",
    "doctrine.conduct.document": "rule: a written piece is published as a document",
    "doctrine.conduct.sorting_out": "practice: when the group is sorting something out",
    "doctrine.belonging.place": "Nora's place in the group",
    "doctrine.belonging.serve": "how Nora serves a room",
    "doctrine.voice.gif": "voice practice for stickers and GIFs",
}

SYSTEM = """You label training data for a prompt composer.

WHAT YOU ARE READING. Nora is an AI member of Hong Kong friends' group chats (Signal, Discord, WhatsApp, Instagram); she speaks Cantonese and English. When somebody tags her, her model receives one large prompt built from UNITS: doctrine units (instructions on who she is and how she behaves), memory and room blocks (what she knows about this room and these people), and tool units (the tools she can call). Below is ONE recorded turn: the occasion, the message that tagged her, the last lines of the room transcript before it (oldest first; `age` = how long before the tag), what she replied, every tool call she made in the turn with its arguments, and then the full text of every unit that was in her prompt.

A composer will later include only the units a turn needs, to make the prompt smaller. Some text is always included and is NOT yours to label: the context header, the tagged message, and the core hard constraints.

YOUR JOB. For EVERY unit listed, decide `needed`: true if this turn would plausibly have gone worse without that unit in the prompt (a wrong or missing fact, a broken rule, a worse tone or voice, context missed, a tool she could not have called). Judge from what the turn actually asked and did.
- A rule-type unit (law, safety, a "when X" practice) is needed only if this turn touched what it governs. The rule about writing things down is not needed for a greeting; the picture rule is needed when a picture, clip or voice note is in play.
- A memory block is needed if its content was used, or if a good reply should have used it.
- A tool unit is needed if the tool was called, or should have been.
- If you are uncertain, say needed=true. Missing a needed unit costs far more than including an extra one.
Also say how much transcript the turn needed: "last_few" (the last ~5 lines suffice), "default_window" (the lines given, about 40), or "extended" (it needed history beyond the window). And list units that were NOT in the prompt but should have been, from the absent menu, if any.

Answer with ONE JSON object and nothing else:
{"units": {"<unit id>": {"needed": true|false, "why": "<at most 12 English words>"}, ...every listed unit id exactly once...},
 "transcript_extent": "last_few"|"default_window"|"extended",
 "missing": [{"unit": "<absent menu id>", "why": "<at most 12 words>"}]}"""


def key():
    for line in open(ENV):
        if line.startswith("OMNIROUTE_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"')
    raise SystemExit("OMNIROUTE_API_KEY not found")


RESIDENT_TEXT = "Schema of the resident tool `%s` (always callable without loading)."


def build_user(r):
    occ = "platform=%s; time=%s; media codes in message/last lines=%s; generations=%d" % (
        r["platform"], r["ts"], ",".join(r["media_codes"]) or "none", r["n_generations"])
    parts = ["<occasion>%s</occasion>" % occ, "<tagged_message>\n%s\n</tagged_message>" % r["message"],
             "<last_transcript_lines>\n%s\n</last_transcript_lines>" % "\n".join(r["transcript"]["last15"])]
    if r["replies"]:
        parts.append("<replies_sent>\n%s\n</replies_sent>" % "\n---\n".join(r["replies"]))
    else:
        parts.append("<final_text note=\"no send_reply call; this was her final text\">\n%s\n</final_text>" % r["final_text"][:3000])
    parts.append("<tool_calls>\n%s\n</tool_calls>" % ("\n".join(
        "%s %s" % (t["name"], t["args"][:800]) for t in r["tools"]) or "none"))
    us = []
    for uid in sorted(r["units"]):
        u = r["units"][uid]
        text = u["text"] if u["text"] is not None else RESIDENT_TEXT % uid.rsplit(".", 1)[1]
        us.append('<unit id="%s" tokens="%d">\n%s\n</unit>' % (uid, u["tokens"], text))
    parts.append("<units>\n%s\n</units>" % "\n".join(us))
    absent = [k for k in ABSENT_MENU if k not in r["units"]]
    parts.append("<absent_menu>\n%s\n</absent_menu>" % ("\n".join("%s: %s" % (k, ABSENT_MENU[k]) for k in absent) or "none"))
    parts.append("Label every one of these unit ids: %s" % ", ".join(sorted(r["units"])))
    return "\n\n".join(parts)


def parse(content, ids):
    m = re.search(r"\{.*\}", content or "", re.S)
    if not m:
        return None, "no_json"
    try:
        j = json.loads(m.group(0))
    except Exception:
        return None, "bad_json"
    u = j.get("units") or {}
    # sanity pass 2026-10-12: 2 of 5 answers spelled the booleans as strings
    for v in u.values():
        if isinstance(v, dict) and isinstance(v.get("needed"), str) and v["needed"].strip().lower() in ("true", "false"):
            v["needed"] = v["needed"].strip().lower() == "true"
    missing = [i for i in ids if i not in u or not isinstance(u[i], dict) or not isinstance(u[i].get("needed"), bool)]
    if missing:
        return j, "unlabelled:%d" % len(missing)
    return j, "ok"


LOCK = threading.Lock()


def spend():
    with LOCK:
        with open("calls.log", "a") as f:
            f.write("%s\n" % time.time())
        return sum(1 for _ in open("calls.log"))


def call(k, r, budget):
    n = spend()
    if n > budget:
        return None, "budget"
    body = {"model": MODEL, "temperature": 0.1, "max_tokens": 16000,
            # measured 2026-10-12: without it one turn reasoned 12,000 tokens and never answered
            "reasoning_effort": "low",
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": build_user(r)}]}
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), headers={
        "Authorization": "Bearer " + k, "Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=900) as resp:
            out = json.load(resp)
    except Exception as e:
        return {"error": repr(e)[:300], "seconds": time.time() - t0}, "http_error"
    out["seconds"] = round(time.time() - t0, 1)
    return out, "ok"


def one(k, f, budget, attempts_max):
    r = json.load(open(f))
    dst = "labels/%s.json" % r["id"]
    ids = sorted(r["units"])
    if os.path.exists(dst):
        old = json.load(open(dst))
        if old.get("status") == "ok":
            return r["id"], "cached"
        try:  # re-parse a recorded answer before spending a call
            content = old["attempts"][-1]["raw"]["choices"][0]["message"]["content"]
            parsed, status = parse(content, ids)
            if status == "ok":
                old.update(status="ok", parsed=parsed)
                json.dump(old, open(dst, "w"), ensure_ascii=False)
                return r["id"], "reparsed"
        except Exception:
            pass
    attempts = []
    status = "none"
    for _ in range(attempts_max):
        out, st = call(k, r, budget)
        if st == "budget":
            status = "budget"
            break
        content = None
        if st == "ok":
            try:
                content = out["choices"][0]["message"]["content"]
            except Exception:
                st = "no_choice"
        parsed, status = parse(content, ids) if content else (None, st)
        attempts.append({"raw": out, "status": status})
        json.dump({"id": r["id"], "status": status, "model_alias": MODEL, "ids": ids,
                   "parsed": parsed, "attempts": attempts}, open(dst, "w"), ensure_ascii=False)
        if status == "ok":
            break
    return r["id"], status


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--budget", type=int, default=215)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--attempts", type=int, default=1)
    a = ap.parse_args()
    os.makedirs("labels", exist_ok=True)
    k = key()
    files = sorted(glob.glob("turns/*.json"), key=lambda f: json.load(open(f))["ts"], reverse=True)[: a.limit]
    with cf.ThreadPoolExecutor(a.workers) as ex:
        for tid, st in ex.map(lambda f: one(k, f, a.budget, a.attempts), files):
            print(tid[:8], st, flush=True)


if __name__ == "__main__":
    main()
