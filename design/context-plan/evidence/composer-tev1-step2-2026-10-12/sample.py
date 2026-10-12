"""Stratified turn sample for arm A (writes sample.txt: turn ids, one per line; ids only).

Strata, in priority order (a turn joins its first matching stratum): greeting-like
(aggregate.is_greeting), then the rarest evidence bundle it has, then `none`
(no evidence). Each stratum is shuffled with a fixed seed and the strata are
interleaved round-robin, so ANY prefix of the file is itself stratified, and
Cantonese (CJK share of the message >= 0.3) is reported per stratum.
"""
import collections, json, random

from common import AG, cjk_share, evidence, load_turns

turns = load_turns()
ev = {r["id"]: evidence(r, aug)[0] for r, aug in turns}
freq = collections.Counter(b for items in ev.values() for b in {i["bundle"] for i in items})
strata = collections.defaultdict(list)
for r, aug in turns:
    bs = {i["bundle"] for i in ev[r["id"]]}
    if AG.is_greeting(r):
        k = "greeting"
    elif bs:
        k = min(bs, key=lambda b: freq[b])
    else:
        k = "none"
    strata[k].append(r["id"])
sizes = {k: len(v) for k, v in strata.items()}
of = {i: k for k, v in strata.items() for i in v}
rng = random.Random(20261012)
for k in strata:
    rng.shuffle(strata[k])
order, keys = [], sorted(strata)
while any(strata[k] for k in keys):
    for k in keys:
        if strata[k]:
            order.append(strata[k].pop(0))
open("sample.txt", "w").write("\n".join(order) + "\n")
cjk = {r["id"]: cjk_share(r) >= 0.3 for r, _ in turns}
print(json.dumps({"strata": sizes, "first40": dict(collections.Counter(of[i] for i in order[:40])),
                  "first40_cantonese": sum(cjk[i] for i in order[:40]), "n": len(order)}))
