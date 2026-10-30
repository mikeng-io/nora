#!/usr/bin/env bash
# Transcribe one audio file (stdin) with Nora's own Cantonese STT: the `stt`
# service on norty, reached from inside the `nora` container because its port
# is private to the compose network. Prints the text (already OpenCC s2t).
#   pipeline/nora_stt.sh < audio/vo/yue/vo1.mp3
exec ssh norty 'cd ~/workspace/nora && python3 ~/workspace/nora/scripts/secret_delivery.py --runtime-root ~/workspace/nora --compose-exec -T nora /app/.venv/bin/python -c "
import sys, json, urllib.request
data = sys.stdin.buffer.read(); b = \"----showreel\"
body = (f\"--{b}\r\nContent-Disposition: form-data; name=\\\"model\\\"\r\n\r\nnoise-detection\r\n\"
        f\"--{b}\r\nContent-Disposition: form-data; name=\\\"language\\\"\r\n\r\nyue\r\n\"
        f\"--{b}\r\nContent-Disposition: form-data; name=\\\"file\\\"; filename=\\\"vo.mp3\\\"\r\n\"
        f\"Content-Type: audio/mpeg\r\n\r\n\").encode() + data + f\"\r\n--{b}--\r\n\".encode()
req = urllib.request.Request(\"http://stt:8000/v1/audio/transcriptions\", data=body,
      headers={\"Content-Type\": f\"multipart/form-data; boundary={b}\"})
print(json.loads(urllib.request.urlopen(req, timeout=60).read())[\"text\"])
"'
