# V6 plan — speaker diversity via zero-shot cloning (draft 2026-09-16, not started)

## Why
V5 recall is capped by a 6-voice MiniMax positive set (misses cluster in 3 voices;
5 % of bare clips score < 0.01 under every config). Cantonese TTS catalogues top out
at ~13 speakers (MiniMax 6, Azure/edge 3, Google 4). openWakeWord's English models
work because piper gives ~900 speakers. Zero-shot cloning seeded by real Common Voice
yue speakers is the Cantonese equivalent. Facts below are from
`cosyvoice-cantonese-plan.md` (verified against repo code/README) unless marked
UNVERIFIED.

## Model
- **`ASLP-lab/Cosyvoice2-Yue`** (Apache-2.0, HF). Drop-in `CosyVoice2(model_dir)`,
  fine-tuned on ~12k h WenetSpeech-Yue; MER 10.3 % / SIM 0.82 vs stock 14.4 % / 0.81.
- Path: `inference_zero_shot(tts_text, prompt_text, prompt_wav, speed=…)`.
  `prompt_text` = the Common Voice `sentence` (required; not normalised).
  Fallback per speaker if transcript suspect: `inference_cross_lingual` (no transcript).
- No `<|yue|>` tag in CV2; dialect is implicit via prompt voice + written Cantonese.
- Text normaliser drops the space in 喂 Nora and rewrites punctuation; a Latin "Nora"
  is pronounced English-ish. Generate BOTH `喂，Nora。` and `喂，諾拉。` variants and
  keep whichever the STT accepts. End every text with `。`.
- Very short texts (3–4 tokens) can repeat/garble: over-generate, filter by ASR +
  duration (0.6–2.0 s).
- Install: clone `FunAudioLLM/CosyVoice` recursive, `requirements.txt` pins
  torch 2.3.1 cu121, onnxruntime-gpu 1.18, transformers 4.51.3, wetext; skip
  tensorrt/deepspeed/vllm. `fp16=True` (autocast fp16, T4-safe). Output 24 kHz →
  resample 16 kHz. T4 speed: UNVERIFIED — script times its first 20 utterances.

## Seeds (real Cantonese speakers)
- `JackyHoCL/common_voice_22_yue`: `client_id` exists in **dev (6 shards) and train**
  only; the `validated*` shards in this mirror have no `client_id` (checked
  2026-09-16), so they cannot be speaker-deduplicated. dev-0 + train give 143
  speakers; pull dev-1…5 to reach ~250–300.
- Per speaker: ONE prompt clip, 3–10 s, `up_votes ≥ 2, down_votes = 0`, prefer the
  SHORTEST sentence (zero-shot warns when tts_text is shorter than prompt_text),
  plus a simple SNR gate (UNVERIFIED how much CV mic variance hurts cloning — measure).
- The remaining clips of each speaker → **real-speech negatives** (first real human
  speech in the negative pool; same speakers as the cloned positives, which forces
  phrase-not-voice).

## Texts per speaker
Positives (3): `喂，Nora。` `喂Nora。` `喂，Nora呀。` × speed {0.9, 1.1} → 6 candidates;
  expect ~50–70 % to survive the gate.
Probe bucket, NOT positive (Miles, 2026-09-16): `喂，諾拉。` `喂諾拉。` `喂，諾拉呀。` — 諾拉 is
  nok6-laai1, not the name as spoken, and neighbours the trap 喂勞拉. Generated only to see
  how the model renders it; becomes a trap negative if the STT does not hear "nora", else discarded.
Negatives (8): traps `喂勞拉。` `喂娜娜。` `喂露娜。`; mention `唔該Nora。`
  `Nora你今日忙唔忙呀。`; command `而家幾點呀。` `下一首。`; generic `我今晚返嚟食飯。`

## Gate (automatic, on this host, STT container `sensevoice`)
- Positive kept iff transcript matches /nora|nova|喂 ?ra|諾拉|娜拉/ and duration 0.6–2.0 s.
- Negative kept iff transcript does NOT match that regex.
- Listen to 10 random survivors before scaling (human check).

## Phases
0. **Pilot** (Modal T4, ~20 min GPU, $0 TTS): 20 speakers × 12 pos + 8 neg = 400 clips.
   Go if ≥ 70 % of positives pass the gate and the 10-clip listen is acceptable.
   No-go → try `Fun-CosyVoice3-0.5B-2512` (needs `You are a helpful assistant.<|endofprompt|>`
   prefix; reports of zero-shot garbling) → GPT-SoVITS v2+ (`text_lang=yue`) →
   MiniMax cloning (~$1.5/voice UNVERIFIED, needs ≥10 s prompt → concat per speaker).
1. **Scale** (Modal, ~1 h UNVERIFIED): 250–300 speakers → ~1,800 pos candidates,
   ~2,400 TTS negs, ~2,000 real-speech negs. Tar back to the worktree lab dir.
   Nothing written to `voice-lab/oww-data` without an explicit OK.
2. **Train V6 locally** (~30 min, capped): positives end-aligned ×3 rounds; negatives
   full + prefix windows; RIR off; V5h negative set + new sets; 50k steps, weight
   1500, 100 positives/batch; 3 seeds. Evaluate with `accept.py` and `real_eval3.py`.
   Bar unchanged: AUC > 0.97, FP/hr < 1.0, trap FP < 1 %, recall > 95 %, real ≥ 0.6.

## Where it runs
Modal generates audio (this host has no GPU and carries 13–16 GB of other services).
Everything else stays local under `systemd-run … MemoryMax`, one job at a time.

## Needs a decision from Miles
1. OK to spend ~1.5 Modal T4 hours (pilot + scale).
2. Where the generation script lives: this lab dir (default) or `voice-lab/scripts/`.
3. Whether 「Nora 你今日忙唔忙呀」-style mentions stay negatives (V5h/V5j assume yes).

## Phase 0 pilot result (2026-09-16 ~23:30) — NO-GO for Yue zero-shot with Latin "Nora"
- Modal T4, `ASLP-lab/Cosyvoice2-Yue`, fp16: model load 15 s (cached), **2.46 s per utterance**
  → 278 speakers × 14 texts ≈ 2.7 T4-hours, above the 1.5 h estimate (fan out to 4 containers
  for wall-clock; cost ≈ $1.6 either way). Four build fixes were needed and are in the script:
  setuptools<75 before torch; openai-whisper/pyworld with `--no-build-isolation`
  (pkg_resources removed in setuptools≥80); full upstream requirements (cosyvoice.flow imports gdown);
  strip `epoch`/`step` from the Yue `llm.pt`; the API now takes `prompt_wav` as a PATH.
- 340/340 utterances generated (20 speakers). STT gate (SenseVoice, `language=yue`):
  - negatives: 189/220 kept; every trap / command / mention / generic text 20/20 correct.
  - **positives: 2/120** — 「喂，Nora。」「喂Nora。」 0/40 each; STT hears 喂啦 / 喂嬲 / 喂 na / 衛南 /
    喂 now. The Yue fine-tune does not render the Latin token as the name.
  - 諾拉 → 喂諾拉 / 韋諾拉 / 歡諾拉 (nok-laai, as Miles predicted): 29/60 usable as *trap* negatives.
- **Gate control**: 28/30 MiniMax bare 「喂 Nora」 pass the same regex (gate is fair);
  **1/10 edge-tts 「喂Nora啦喂Nora啦」 pass** (維諾羅啦, 餵我啦啦) → those 500 clips, used as
  positives in V5, are mispronounced and must leave the positive set in V6.
- Listening sample for Miles: `pilot-listen-yue.wav` (8 Nora, 4 諾拉, 2 勞拉).
- Next (running): 4-arm mini-pilot, 5 speakers each — `base:zero_shot`, `base:cross_lingual`,
  `yue:cross_lingual`, `yue:zero_shot` with 「挪拉」(no4 laai1). Go = any arm ≥ 70 % positives
  through the gate; otherwise fall back to GPT-SoVITS (`text_lang=yue`) or MiniMax cloning.

## 4-arm mini-pilot (5 speakers each, 2026-09-17 00:xx) → GO with stock CosyVoice2 cross-lingual
| arm | positives through gate | notes |
|---|---|---|
| **base : cross_lingual** | **10/30** — 「喂，Nora。」5/10, 「喂Nora。」5/10, 「喂，Nora呀。」0/10 | STT hears 「喂 nora」; mentions render "nora" too |
| base : zero_shot | 3/30 | worse prosody for the Latin token |
| yue : cross_lingual | 0/30 | 喂唔嬲啊 / 喂嗱 / 喂撈 |
| yue : zero_shot, 「挪拉」 | 0/30 | 喂羅拉 / 餵我啦 (= the trap, not the name) |
Decisions: drop the 呀 text; 諾拉 clips from the base model are clean trap negatives (STT never
hears "nora"); gate rule fixed so 「唔該Nora」-class mentions must CONTAIN "nora".
Yield 50 % on 2 texts × 3 speeds × 278 speakers ≈ 550–800 gated positives over ~250 speakers,
which is the diversity target. Phase 1 launched: 278 speakers, base cross-lingual, 4 T4 containers,
≈ 2.3 T4-hours total (above the 1.5 h estimate; ≈ $1.4).
