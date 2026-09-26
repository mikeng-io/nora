#!/usr/bin/env bash
# Render every variant and mux it with its language's mix.
#
#   pipeline/render.sh                 # all 8: {en,yue} x {16x9,9x16,4x5,1x1}
#   pipeline/render.sh en 16x9         # one
#
# Picture: Remotion, H.264 CRF 16, yuv420p, BT.709. Sound: audio/mix/<lang>.wav
# (pipeline/mix.py) as AAC 320k. Output: renders/nora-showreel-<lang>-<format>.mp4.
# Each file is read back with ffprobe: size, frame rate, duration, streams.
set -u
cd "$(dirname "$0")/.."
LANGS=${1:-"en yue"}
FORMATS=${2:-"16x9 9x16 4x5 1x1"}
mkdir -p out renders
fail=0
for lang in $LANGS; do
  for fmt in $FORMATS; do
    id="nora-$lang-$fmt"
    video="out/$id.mp4"
    final="renders/nora-showreel-$lang-$fmt.mp4"
    npx remotion render src/index.ts "$id" "$video" --codec=h264 --crf=16 \
      --pixel-format=yuv420p --color-space=bt709 --muted --log=error > "out/$id.log" 2>&1
    rc=$?
    if [ $rc -ne 0 ]; then echo "FAIL render $id (rc=$rc, out/$id.log)"; fail=1; continue; fi
    ffmpeg -v error -y -i "$video" -i "audio/mix/$lang.wav" -map 0:v:0 -map 1:a:0 \
      -c:v copy -c:a aac -b:a 320k -ar 48000 -movflags +faststart -shortest \
      -metadata title="Nora — Earphones in ($lang, $fmt)" "$final"
    rc=$?
    if [ $rc -ne 0 ]; then echo "FAIL mux $id (rc=$rc)"; fail=1; continue; fi
    probe=$(ffprobe -v error -show_entries stream=codec_type,codec_name,width,height,r_frame_rate:format=duration,size \
      -of compact=p=0:nk=1 "$final" | tr '\n' ' ')
    echo "OK $final  $probe"
  done
done
exit $fail
