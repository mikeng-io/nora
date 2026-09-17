import json, os, urllib.request
key = os.environ["MINIMAX_CN_API_KEY"]
def post(host, path, payload):
    req = urllib.request.Request(f"https://{host}{path}",
        data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type":"application/json","Authorization":f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=60) as r: return json.load(r)

for host in ["api.minimax.chat"]:
    d = post(host,"/v1/get_voice",{"voice_type":"all"})
    sv = d.get("system_voice", [])
    ids = [v.get("voice_id","") for v in sv]
    yue = [v for v in sv if "antonese" in v.get("voice_id","")]
    print(f"=== {host} ===")
    print(f"  system_voice total = {len(ids)}")
    print(f"  Cantonese-matching  = {len(yue)}")
    for v in yue:
        print(f"    {v['voice_id']:34s} {v.get('voice_name','')}")
