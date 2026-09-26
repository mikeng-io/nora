#!/usr/bin/env bash
# Generate showreel art with Codex CLI's built-in image tool.
#
#   pipeline/gen_art.sh <name> [<name> ...]
#
# Reads art/prompts/<name>.txt (plus the shared _brief.txt unless the prompt
# carries its own tool instruction), attaches Nora's two canonical references,
# runs `codex exec --json`, and copies the image out of THAT run's own
# ~/.codex/generated_images/<thread_id>/ into art/raw/<name>.png.
#
# The copy is done here, from the run's thread id, and never by the model:
# asked to "copy the image it made", parallel runs copied each other's newest
# file (2026-09-26, two names came back byte-identical). Output is
# nondeterministic: a kept image is committed, never silently regenerated.
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)
REPO=$(cd "$ROOT/.." && pwd)
GEN="$HOME/.codex/generated_images"
for name in "$@"; do
  prompt="$ROOT/art/prompts/$name.txt"
  log="$ROOT/art/raw/$name.jsonl"
  refs=(-i "$REPO/assets/nora-canonical-self.png" -i "$REPO/assets/nora-character-reference-sheet.png")
  if grep -q "built-in image generation tool" "$prompt"; then
    body=$(cat "$prompt"); refs=()          # self-contained, no character in it
  else
    body="$(cat "$ROOT/art/prompts/_brief.txt")"$'\n\n'"$(cat "$prompt")"
  fi
  body+=$'\n\n'"Generate the image once and stop. Do not run commands or write files."
  codex exec --json --ephemeral -C "$ROOT" -s read-only \
    -c model_reasoning_effort='"low"' "${refs[@]}" -- "$body" > "$log" 2>"$log.err"
  tid=$(grep -m1 -o '"thread_id":"[^"]*"' "$log" | cut -d'"' -f4)
  img=$(ls -t "$GEN/$tid"/*.png 2>/dev/null | head -1)
  if [ -n "$tid" ] && [ -n "$img" ]; then
    cp "$img" "$ROOT/art/raw/$name.png"
    echo "OK $name thread=$tid $(md5 -q "$ROOT/art/raw/$name.png")"
  else
    echo "MISSING $name thread=${tid:-none}"
  fi
done
