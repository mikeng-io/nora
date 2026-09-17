import json, os, time, urllib.error, urllib.request
key = os.environ["MINIMAX_CN_API_KEY"]
H = {"Content-Type":"application/json","Authorization":f"Bearer {key}"}

def get(url):
    try:
        r = urllib.request.Request(url, headers=H)
        with urllib.request.urlopen(r, timeout=30) as x: return json.load(x)
    except Exception as e: return {"_err": str(e)[:150]}

tid = "441630896283904"
for i in range(30):
    d = get(f"https://api.minimaxi.com/v1/query/video_generation?task_id={tid}")
    st = d.get("status")
    print(f"poll {i:2d}: {st}  file_id={d.get('file_id','')!r}")
    if st in ("Success", "Fail"):
        print("\nFINAL:", json.dumps(d, ensure_ascii=False, indent=2))
        if st == "Success" and d.get("file_id"):
            fid = d["file_id"]
            r = urllib.request.Request(f"https://api.minimaxi.com/v1/files/retrieve?file_id={fid}", headers=H)
            with urllib.request.urlopen(r, timeout=30) as x: fd = json.load(x)
            print("\nFILE:", json.dumps(fd, ensure_ascii=False, indent=2)[:600])
            url = (fd.get("file") or {}).get("download_url")
            if url:
                out = "/tmp/minimax_test_video.mp4"
                urllib.request.urlretrieve(url, out)
                print(f"\nDOWNLOADED -> {out}  size={os.path.getsize(out)} bytes")
        break
    time.sleep(10)
