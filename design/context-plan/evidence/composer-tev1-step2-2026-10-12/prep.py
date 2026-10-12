"""Add what the first pass's turns/ did not keep: <replying_to> and whether the sender is listed.

Run on norty in ~/composer-labels/step2. Reads ../raw/t_<id>.json (the first main
generation's input, as extract.py selects it) and writes aug/<id>.json (room text,
stays on norty). sender_listed = the sender's name appears in that turn's
<room_members> block (the room's listed people); absent block -> False.
"""
import json, os, re, sys

sys.path.insert(0, os.path.expanduser("~/composer-labels"))
import extract as EX  # noqa: E402
from common import AUG, inner, load_turns, PARENT  # noqa: E402

os.makedirs(AUG, exist_ok=True)
n_rt = n_listed = 0
for r, _ in load_turns():
    t = json.load(open(os.path.join(PARENT, "raw", "t_%s.json" % r["id"])))
    obs = sorted(t.get("observations", []), key=lambda o: o["startTime"])
    gens = [o for o in EX.main_agent_subtree(obs) if o["type"] == "GENERATION"]
    rt = None
    for msg in gens[0]["input"][1:]:
        m = re.search(r"<replying_to\b([^>]*)>(.*?)</replying_to>", EX.txt(msg["content"]), re.S)
        if m:
            a = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
            rt = {"from": a.get("from"), "text": inner(m.group(2))[:800]}
            raw_rt = m.group(0)
            break
    mm = re.match(r"<tag_message\b([^>]*)>", r["message"] or "")
    sender = dict(re.findall(r'(\w+)="([^"]*)"', mm.group(1))).get("from") if mm else None
    members = ((r["units"].get("block.room_members") or {}).get("text")) or ""
    listed = bool(sender) and sender.lower() in members.lower()
    n_rt += rt is not None
    n_listed += listed
    json.dump({"replying_to_rec": rt, "replying_to": raw_rt if rt else "", "sender_listed": listed},
              open(os.path.join(AUG, r["id"] + ".json"), "w"), ensure_ascii=False)
print(json.dumps({"turns": len(load_turns()), "with_replying_to": n_rt, "sender_listed": n_listed}))
