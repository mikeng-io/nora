import json, os, urllib.error, urllib.request
key = os.environ["MINIMAX_CN_API_KEY"]
H = {"Content-Type":"application/json","Authorization":f"Bearer {key}"}

def post(body, url="https://api.minimaxi.com/v1/video_generation"):
    try:
        r = urllib.request.Request(url, data=json.dumps(body).encode(), headers=H)
        with urllib.request.urlopen(r, timeout=40) as x: return json.load(x), None
    except urllib.error.HTTPError as e:
        try: return None, json.loads(e.read().decode()).get("base_resp",{}).get("status_msg") or f"HTTP{e.code}"
        except: return None, f"HTTP {e.code}"
    except Exception as e: return None, str(e)[:120]

print("=== which LEGACY video models are usable with TokenPlan key? ===")
for m in ["T2V-01","T2V-01-Director","I2V-01","I2V-01-Director","I2V-01-live",
          "MiniMax-Hailuo-2.3","MiniMax-Hailuo-2.3-Fast","MiniMax-Hailuo-02",
          "MiniMax-Hailuo-02-Fast","S2V-01","MiniMax-H3","MiniMax-H3-Max"]:
    d, err = post({"model":m,"prompt":"neon data center, slow camera pan"})
    if d and d.get("base_resp",{}).get("status_code") == 0:
        print(f"  ✅ {m:26s} ACCEPTED task_id={d.get('task_id')}")
    else:
        msg = (d or {}).get("base_resp",{}).get("status_msg") or err
        print(f"  ❌ {m:26s} {str(msg)[:95]}")
