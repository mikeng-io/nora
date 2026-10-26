# showreel — Nora, "Earphones in"

A 20-second motion showreel for Nora in English and Hong Kong Cantonese, for
four frames. The idea, the beat sheet, the script and the decisions behind
them are in `BRIEF.md`; this file is how it is made and how to make it again.

## Historical renders — 2026-09-26, superseded production appearance

`renders/` retains eight original 20-second MP4s with the earlier 3D Mini
appearance. They are archival outputs, **not current production exports** or
recommendations for public use. The former distribution targets below record
the September export contract only; no website embed or root README route to
these MP4s was found in the repository. External publication is unmeasured.

The 2D image sources and processor sync are integrated. `Thread.tsx` now joins
the accessory-free listening frame to the message thread; `Quiet.tsx` holds its
camera/pause pulse on the Bauhinia clasp. Audio timing is unchanged. A future
current export still needs picture composition acceptance, a Remotion render
and mux with the retained language mix. No new image generation, TTS, score or
alignment run is required by an art-only update. Render only the composition
being published; all current language/format exports would require all eight.
`pipeline/render.sh` writes these same archive filenames, so preserve the
originals and use separate versioned 2D output paths before making a new current
export catalogue. Relabeling archives does not turn their frames into 2D.
No video was rerendered in this integration. The pre-existing
`src/data/timeline.json` and `src/data/vo-{en,yue}.json` are absent from this
checkout; restore their original timing data before typechecking/rendering.
No substitute timings, alignment or audio regeneration were invented.

Original encoding: 30 fps, H.264 (CRF 16, yuv420p, BT.709), AAC 320k,
-14 LUFS integrated, peak at or under -1 dBFS:

| historical file | frame | original intended distribution |
|---|---|---|
| `renders/nora-showreel-en-16x9.mp4` | 1920×1080 | the master: web, YouTube, a screen |
| `renders/nora-showreel-en-9x16.mp4` | 1080×1920 | Reels, Stories, Shorts, TikTok |
| `renders/nora-showreel-en-4x5.mp4` | 1080×1350 | Instagram and Threads feeds |
| `renders/nora-showreel-en-1x1.mp4` | 1080×1080 | square slots, X, LinkedIn |
| `renders/nora-showreel-yue-*.mp4` | the same four | the Cantonese cut |

Each frame is its own composition, not a crop of the master: the chat, the
memory lanes, the senses stage and the end card are laid out per frame
(`src/scenes/*.tsx`, the `pick(kind, …)` tables).

The MP4s, the raw Codex images and the WAVs are stored with Git LFS
(`.gitattributes` here). Without git-lfs a checkout holds pointer files.

## How it is made

```text
src/copy/{en,yue}.json ─┬─> pipeline/tts.py ──> audio/vo/<lang>/*.mp3 (MiniMax)
   the words, once      │        │
                        │        └─> pipeline/align.py ──> src/data/vo-<lang>.json
                        │              read-back + timing     (errors, speech span, word times)
src/data/timeline.json ─┼─> pipeline/score.py ──> audio/stems/{music,sfx}.wav
   the one clock        │
                        ├─> pipeline/mix.py ──> audio/mix/<lang>.wav (-14 LUFS)
art/prompts/*.txt ──────┼─> pipeline/gen_art.sh ──> art/raw/*.png (Codex CLI)
                        │        └─> pipeline/process_art.py ──> public/art/*.webp, src/data/art.json
                        └─> src/ (Remotion) ──> pipeline/render.sh ──> renders/*.mp4
```

The words live in one place (`src/copy/`); the clock lives in one place
(`src/data/timeline.json`); the voice's timing is measured once
(`src/data/vo-*.json`) and both the captions and the mix read that. Nothing
downstream re-reads the raw audio to guess where a word is.

## Rebuild

From this directory. Each step is independent; rerun only what you changed.

```bash
npm ci                                                        # Remotion 4.0.529, pinned

# the voice (stochastic: a kept take is committed; rerunning replaces it)
export MINIMAX_CN_API_KEY=...                                 # never written to a file here
python3 pipeline/tts.py --lang en  --voice Cantonese_CuteGirl --emotion calm
python3 pipeline/tts.py --lang yue --voice Cantonese_CuteGirl --emotion calm
python3 pipeline/tts.py --lang en  --voice Cantonese_CuteGirl --emotion calm --speed 1.05 --only vo4
uvx --with mlx-whisper --with numpy python pipeline/align.py en yue   # exit 1 if any take mishears

# the score (deterministic, seeded) and the mix
uv run --no-project --with numpy --with scipy python pipeline/score.py
uv run --no-project --with numpy --with scipy --with pyloudnorm python pipeline/mix.py en yue

# art (stochastic) and fonts
pipeline/gen_art.sh <name> ...                                # one Codex run per name
uv run --no-project --with pillow --with numpy python pipeline/process_art.py
uv run --no-project --with fonttools --with brotli python pipeline/fonts.py

# look, then render
npm run studio                                                # scrub any composition
node pipeline/stills.mjs nora-en-16x9 out/stills 2 6.5 9 12.5 17.9   # a contact sheet
pipeline/render.sh                                            # all eight, or: pipeline/render.sh yue 9x16
```

`pipeline/align.py` scores Cantonese with Nora's own speech-to-text on norty
(`pipeline/nora_stt.sh`, the `stt` service reached through the `nora`
container), because Whisper writes spoken Cantonese as written Mandarin and
would fail every correct take. It needs `ssh norty`.

## What was measured, 2026-09-26

- **The voice says the script.** Every take is transcribed and compared to
  its line: English with Whisper, Cantonese with Nora's STT. 10/10 PASS with
  0 errors. The read-back caught one bad take on the way: English line 1 came
  back "I'll leave in your group chat" and was retaken until it passed.
- **The voice survives the music.** The delivered MP4s were transcribed
  again, with the music under them: all five lines in each language recovered
  verbatim.
- **Loudness**, read from the finished MP4s with ffmpeg's `ebur128`:
  -14.0 LUFS integrated in all eight, peak -1.2 dBFS (en) and -1.0 dBFS (yue).
- **The score's arc**, in LUFS per section: intro -23, the room -19 then -17,
  the break -18, the drop -13, senses -13, the quiet -15, silence after the
  click, the end chord -14.
- **An independent review** (a separate agent, frames extracted from all
  eight files) found 20 defects; all were fixed and the fixed frames were
  checked in every format. `BRIEF.md` lists what changed.
- **Measured points** the camera moves use, from the renders with numpy: the
  cable's x at 80% of each macro plate's height (`MACRO` in
  `src/scenes/Thread.tsx`) and the inline remote in each close-up (`QUIET` in
  `src/scenes/Quiet.tsx`).

## Art provenance

Every new image was made by Codex CLI (`codex-cli 0.157.0`, `codex exec`, its
built-in image tool) from Nora's two canonical references,
`assets/nora-canonical-self.png` and `assets/nora-character-reference-sheet.png`,
the shared brief in `art/prompts/_brief.txt` and its own prompt in
`art/prompts/`. Each was checked by eye against the eight anchors of
`design/self-reference/05-visual-identity-spec.md` before it was used; none is
a `role: self` asset. The run's JSONL event log sits beside each image in
`art/raw/`, carrying its thread id.

`pipeline/gen_art.sh` copies each image out of its own run's
`~/.codex/generated_images/<thread_id>/`. The first version asked the model to
copy "the image it made", and parallel runs copied each other's newest file:
two names came back byte-identical (caught by md5). `macro-earbud-wide` and
`room-night-wide` come from that first version; both were checked by eye to be
the right picture, and their run logs were not kept.

Borrowed as-is: `group-chat`, `notebook`, `quiet`, `night-portrait` and
`wave` from `site/src/assets/art/`, and `nora-mini-icon` from `assets/`.
`nora-canonical-self` is processed too but no scene uses it.

## Voice provenance

MiniMax T2A v2 (CN endpoint), model `speech-2.8-hd`, voice
`Cantonese_CuteGirl` for both languages (the voice she uses in the Discord
voice channel), emotion `calm`, speed 1.0 (English line 4 at 1.05).
`audio/vo/<lang>/manifest.json` records each take's text, settings, duration,
sha256 and MiniMax's own usage and subtitle data.

## Music and sound

All synthesized in `pipeline/score.py`, seeded, no samples: FM electric
piano, detuned additive pads, sub bass, a mallet hook with a ping-pong delay,
synthesized drums, vinyl dust, and every effect (the earbud seating, message
pops tuned to the chord, the light switch, the drop, a map ping, rain, a
needle drop, a shutter, the inline remote's click, her chime).
MiniMax's music models are not available on this account's plan.

## Fonts

`public/fonts/`, subset by `pipeline/fonts.py` from Google Fonts' variable
sources (all OFL): Inter, Space Grotesk (the wordmark only), JetBrains Mono,
Noto Sans HK. The Noto subset holds exactly the CJK characters in
`src/copy/*.json` (`public/fonts/noto-sans-hk.txt`); change the Cantonese
copy, rerun it.

## Bauhinia production transition — 2026-10-26

The later operator ruling in `design/self-reference/09-mini08-balanced.md`
requires 2D anime Bauhinia art for all non-3D uses, including closed-eye art.
All 23 production sources and their image derivatives are integrated through
`scripts/visual_assets.py sync`, with public filenames preserved. Props, empty
plates, grain, original archives and historical videos retain bytes. Parent
browser inspection accepted the current 2D site artwork and rig composition.
Legacy `macro-earbud-*` paths now contain bare-ear/hair-clasp listening details,
with no earbuds or cable. `Thread.tsx` and `Quiet.tsx` use remeasured clasp-based
focal coordinates. Future video exports still require render acceptance; the
existing MP4s and historical generation records remain unchanged. Runtime
ingest and deployment remain separate.

## Mini option08 refresh, 2026-10-26

Current character inputs follow option08 Balanced eyes. The shared brief and
listening-detail prompts describe the integrated 2D Bauhinia design; adjacent raw JSONL files
record the historical September generation, not the replacement's provenance.
See `assets/visual-assets.json` for masters, consumers and unchanged reasons.
Notebook now comes from `assets/nora-notebook.webp`. Run
`uv run python scripts/visual_assets.py sync` from the repository root to
refresh image outputs. Existing `renders/*.mp4` remain historical exports;
this work does not rerender videos or claim their frames match current art.
