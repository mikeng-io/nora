import json, os, time, urllib.error, urllib.request
key = os.environ["MINIMAX_CN_API_KEY"]
H = {"Content-Type":"application/json","Authorization":f"Bearer {key}"}

def post(url, body):
    try:
        r = urllib.request.Request(url, data=json.dumps(body).encode(), headers=H)
        with urllib.request.urlopen(r, timeout=45) as x: return json.load(x), None
    except urllib.error.HTTPError as e: return None, f"HTTP {e.code} {e.read().decode()[:300]}"
    except Exception as e: return None, f"{type(e).__name__} {str(e)[:150]}"

def get(url):
    try:
        r = urllib.request.Request(url, headers=H)
        with urllib.request.urlopen(r, timeout=30) as x: return json.load(x), None
    except urllib.error.HTTPError as e: return None, f"HTTP {e.code} {e.read().decode()[:300]}"
    except Exception as e: return None, f"{type(e).__name__} {str(e)[:150]}"

print("=== H3 on the CORRECT /v2/ endpoint ===")
d, err = post("https://api.minimaxi.com/v2/video_generation",
  {"model":"MiniMax-H3","content":[{"type":"text","text":"a neon data center, slow camera pan"}],
   "duration":6,"resolution":"768P"})
print("  H3 v2:", json.dumps(d, ensure_ascii=False)[:300] if d else err)

print("\n=== H3-Max on /v2/ ===")
d2, err2 = post("https://api.minimaxi.com/v2/video_generation",
  {"model":"MiniMax-H3-Max","content":[{"type":"text","text":"test"}],"duration":6,"resolution":"768P"})
print("  H3-Max v2:", json.dumps(d2, ensure_ascii=False)[:300] if d2 else err2)

print("\n=== POLL the legacy T2V-01 task that was accepted ===")
tid = "441630896283904"
for i in range(12):
    d3, err3 = get(f"https://api.minimaxi.com/v1/query/video_generation?task_id={tid}")
    if d3:
        st = d3.get("status") or d3.get("base_resp",{}).get("status_msg")
        print(f"  poll {i}: status={st}  {json.dumps(d3, ensure_ascii=False)[:260]}")
        if st in ("Success","Fail","Failed"): break
    else:
        print(f"  poll {i}: {err3}"); break
    time.sleep(8)

print("\n=== quota after video attempt ===")
d4, _ = get("https://api.minimaxi.com/v1/token_plan/remains")
if d4:
    for m in d4["model_remains"]:
        print(f"  {m['model_name']:8s} 5h={m['current_interval_remaining_percent']}% "
              f"(status {m['current_interval_status']})  weekly={m['current_weekly_remaining_percent']}%")
