"""Fetch recent `engagement` traces from Langfuse ON norty (design/context-plan/14 §5).

Run on norty from ~/composer-labels. Reads the Langfuse key from the deploy
checkout's .env (never printed, never copied). Writes raw/t_<id>.json; room text
stays on norty. Selection is node 09's: newest `engagement` traces whose first
GENERATION input starts with a system message; here also nora.trace_kind is
recorded so non-tag kinds can be dropped in extract.py.
"""
import base64, json, os, re, sys, urllib.request

ENV = os.path.expanduser("~/workspace/nora/.env")
BASE = "http://127.0.0.1:3100/api/public"
WANT = int(sys.argv[1]) if len(sys.argv) > 1 else 260


def auth():
    for line in open(ENV):
        if line.startswith("LANGFUSE_OTLP_HEADERS=Authorization=Basic%20"):
            return line.split("Basic%20", 1)[1].strip().replace("%3D", "=")
    raise SystemExit("LANGFUSE_OTLP_HEADERS not found")


H = {"Authorization": "Basic " + auth()}


def get(path):
    req = urllib.request.Request(BASE + path, headers=H)
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


os.makedirs("raw", exist_ok=True)
ids, page = [], 1
while len(ids) < WANT:
    d = get(f"/traces?name=engagement&limit=100&page={page}")
    ids += [t["id"] for t in d["data"]]
    if page >= d["meta"]["totalPages"]:
        break
    page += 1
ids = ids[:WANT]
n = 0
for tid in ids:
    p = f"raw/t_{tid}.json"
    if os.path.exists(p):
        continue
    json.dump(get(f"/traces/{tid}"), open(p, "w"), ensure_ascii=False)
    n += 1
print("listed", len(ids), "fetched new", n)
