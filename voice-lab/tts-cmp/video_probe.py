import json, os, urllib.error, urllib.request
key = os.environ["MINIMAX_CN_API_KEY"]
H = {"Content-Type":"application/json","Authorization":f"Bearer {key}"}

def try_post(name, url, body):
    try:
        r = urllib.request.Request(url, data=json.dumps(body).encode(), headers=H)
        with urllib.request.urlopen(r, timeout=45) as x:
            d = json.load(x)
        print(f"  {name}: {json.dumps(d, ensure_ascii=False)[:400]}")
    except urllib.error.HTTPError as e:
        print(f"  {name}: HTTP {e.code} {e.read().decode()[:350]}")
    except Exception as e:
        print(f"  {name}: FAIL {type(e).__name__} {str(e)[:150]}")

print("=== V2 video (multimodal content array) ===")
try_post("v2 /video_generation", "https://api.minimaxi.com/v1/video_generation",
  {"model":"MiniMax-H3","prompt":"a neon data center, camera slowly pans","duration":6,"resolution":"768P"})

print("\n=== legacy t2v ===")
try_post("legacy t2v", "https://api.minimaxi.com/v1/video_generation",
  {"model":"T2V-01","prompt":"a neon data center"})

print("\n=== what models does the account see? ===")
try_post("H3 Max", "https://api.minimaxi.com/v1/video_generation",
  {"model":"MiniMax-H3-Max","prompt":"test"})

print("\n=== query task list (cheap read, shows entitlements+history) ===")
try:
    r = urllib.request.Request("https://api.minimaxi.com/v1/query/video_generation?page_num=1&page_size=5", headers=H)
    with urllib.request.urlopen(r, timeout=30) as x: d = json.load(x)
    print("  ", json.dumps(d, ensure_ascii=False)[:400])
except urllib.error.HTTPError as e:
    print(f"   HTTP {e.code} {e.read().decode()[:300]}")
except Exception as e:
    print("   FAIL", type(e).__name__, str(e)[:150])

print("\n=== video quota status code meaning: status=3 ===")
print("   (from /token_plan/remains: video had current_interval_status=3, 100%)")
