
cd /home/norty/workspace/voice-lab-data/clips/wake
for f in pos_wai_nora pos_wai_nora2 pos_nora_alone neg_mention neg_other neg_weinora_quote; do
  ffmpeg -v error -y -i $f.mp3 -ar 16000 -ac 1 -sample_fmt s16 /tmp/$f.wav
done
docker cp /tmp/pos_wai_nora.wav stt:/tmp/ >/dev/null 2>&1
for f in pos_wai_nora pos_wai_nora2 pos_nora_alone neg_mention neg_other neg_weinora_quote; do
  docker cp /tmp/$f.wav stt:/tmp/ >/dev/null 2>&1
  out=$(docker exec stt sh -lc "curl -s -F file=@/tmp/$f.wav -F model=sensevoice -F language=yue http://localhost:8000/v1/audio/transcriptions")
  echo "$f :: $out"
done
