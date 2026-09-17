import json, os, time, urllib.request, urllib.error
key = os.environ["MINIMAX_CN_API_KEY"]
H = {"Content-Type":"application/json","Authorization":f"Bearer {key}"}

def q():
    r = urllib.request.Request("https://api.minimaxi.com/v1/token_plan/remains", headers=H)
    with urllib.request.urlopen(r, timeout=25) as x: d = json.load(x)
    return {m["model_name"]: dict(h5=m["current_interval_remaining_percent"],
                                  wk=m["current_weekly_remaining_percent"],
                                  st5=m["current_interval_status"],
                                  stw=m["current_weekly_status"]) for m in d["model_remains"]}

def post(body):
    r = urllib.request.Request("https://api.minimaxi.com/v1/video_generation",
                               data=json.dumps(body).encode(), headers=H)
    with urllib.request.urlopen(r, timeout=45) as x: return json.load(x)

b4 = q(); print("BEFORE:", json.dumps(b4))
print("\nFiring ONE T2V-01 video (5s)...")
d = post({"model":"T2V-01","prompt":"neon tron data center, slow camera pan, dark blue"})
print("  task:", d.get("task_id"), d.get("base_resp"))
tid = d["task_id"]

for i in range(24):
    time.sleep(10)
    r = urllib.request.Request(f"https://api.minimaxi.com/v1/query/video_generation?task_id={tid}", headers=H)
    with urllib.request.urlopen(r, timeout=30) as x: s2 = json.load(x)
    if s2.get("status") in ("Success","Fail"):
        print(f"  -> {s2['status']} after {(i+1)*10}s  {s2.get('video_width')}x{s2.get('video_height')}")
        break

time.sleep(5)
af = q(); print("\nAFTER: ", json.dumps(af))
print("\n=== DELTA ===")
for k in af:
    dh = b4[k]["h5"]-af[k]["h5"]; dw = b4[k]["wk"]-af[k]["wk"]
    print(f"  {k:8s} 5h {b4[k]['h5']}% -> {af[k]['h5']}%  (Δ{dh})   weekly {b4[k]['wk']}% -> {af[k]['wk']}%  (Δ{dw})")
