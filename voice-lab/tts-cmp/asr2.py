import json, os, subprocess, urllib.request, uuid
key = os.environ["MINIMAX_CN_API_KEY"]

# multipart ASR against the documented endpoint
mp3 = open("/tmp/asr_live.mp3","rb").read()
b = uuid.uuid4().hex
body = b""
def part(h, d):
    return h + b"\r\n" + d + b"\r\n"
body += part(f'--{b}\r\nContent-Disposition: form-data; name="model"\r\n'.encode(), b'asr-1.0')
body += part(f'--{b}\r\nContent-Disposition: form-data; name="language"\r\n'.encode(), b'yue')
body += part(f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="a.mp3"\r\nContent-Type: audio/mpeg\r\n'.encode(), mp3)
body += f"--{b}--\r\n".encode()
r = urllib.request.Request("https://api.minimaxi.com/v1/speech_to_text", data=body, method="POST",
  headers={"Authorization":f"Bearer {key}","Content-Type":f"multipart/form-data; boundary={b}"})
try:
    with urllib.request.urlopen(r, timeout=90) as x: out = json.load(x)
    print("ASR RESULT:", json.dumps(out, ensure_ascii=False)[:500])
except urllib.error.HTTPError as e:
    print("ASR HTTP", e.code, e.read().decode()[:400])
except Exception as e:
    print("ASR FAIL", type(e).__name__, str(e)[:200])
