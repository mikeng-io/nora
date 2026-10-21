# openWakeWord internals and alternatives for 「喂 Nora」 (2026-09-16)

Source: local clone `/home/<deploy-user>/workspace/voice-lab/oww-src` (upstream `368c037`, 2025-12-30, plus **uncommitted local edits** to `train.py`/`data.py`), GitHub API, linked docs.

## 1. How openWakeWord works

**Frontend (frozen).** 16 kHz int16 → `melspectrogram.onnx` (32 bins, ~97 frames/s) → Google `speech_embedding` (`embedding_model.onnx`, input `[N,76,32,1]`; 76 frames ≈ 0.78 s, step 8 frames = 80 ms) → one 96-dim vector per 80 ms (`utils.py:225-235`). Unchanged Google weights trained on English-heavy data; whether Cantonese tone cues survive this bottleneck is unverified but is the most plausible cause of your recall ceiling.

**Head.** `train.py:26-83`: `dnn` = Flatten(16×96) → Linear(→`layer_size`, default 32) → LayerNorm/ReLU → Sigmoid; `rnn` = 2-layer BiLSTM(64). Frame count follows clip length: `total_length` = median positive + 750 ms, min 2 s (`:665-672`). Inference scores the **last N embeddings** every 80 ms (`model.py:287-300`).

**Data synthesis** (`train.py:571-645`). Positives: `piper-sample-generator` (English multi-speaker VITS). Adversarial negatives: `generate_adversarial_texts` uses **English CMU-dict phoneme overlap** (`data.py:896`) — meaningless for Cantonese. Bulk negatives: precomputed embeddings of ~2,000 h ACAV100M (HF `davidscripka/openwakeword_features`). Augmentation (`data.py:558`): EQ, distortion, pitch ±3 st, band-stop, coloured noise, background at **−10…15 dB SNR**, RIR p=0.5.

**End alignment.** `create_fixed_size_clip` (`data.py:700`) left-pads so the phrase ends within `end_jitter` of the clip end. Upstream `end_jitter=0.200` s; **your clone set 1.500 s**, so positives float anywhere in the 2 s window — a wider positive region that plausibly raises FP. Revert as an ablation.

**auto_train** (`train.py:283-410`). Three sequences: 50k steps @ lr 1e-4, 5k @ 1e-5, 5k @ 1e-6; negative weight ramps 1→`max_negative_weight` (1500) per sequence; cosine LR, 20 % warm-up, 33 % hold. Hard-example mining (`:459-465`) keeps negatives ≥0.001 and positives <0.999. Checkpoints kept when val-FP ≤ median and recall ≥ 5th pct; final = weight-average of the top decile.

**Bugs verified in source.**
- `best_val_fp` set to 1000 (`:37`) and **never assigned** (only lines 37/291/311 reference it) → `max_negative_weight` doubles unconditionally in sequences 2 and 3.
- Upstream accumulation (`origin/main` `:483-510`): mined batches <128 samples **never call `backward()`** — dropped, not accumulated — and larger batches divide loss by `accumulation_steps`. Training stalls once examples become "easy". Your local patch (backward every batch) is correct.
- `val_set_hrs=11.3` hard-coded (`:293`); `self.fp` counts **every 80 ms frame ≥0.5** (`:107`), whereas `metrics.get_false_positives` groups a 50-frame window into one event. Your 482/245 FP/h are frame-level. The ~11 h `validation_set_features.npy` is English speech/noise/music, so it measures neither event-level FP nor Cantonese exposure.
- Issue #335 (2026-06-20): documented recipe gives 177 FP/h, traced to the −10 dB background mix; no maintainer reply.

**Repo health.** 2,777★, 341 forks, 111 open issues, 12 PRs. Last release **v0.6.0, 2024-02-11**; last commit 2025-12-30; maintainer commented 5× in 2026, last 2026-01-02. README: "only supports English". Non-English threads: #52 (maintainer last 2023-10-14), #266 (community voice-conversion recipe, 2025-05, recall 0.41). Derivatives: `rhasspy/wyoming-openwakeword`; LiveKit's trainer adds VoxCPM 30-language TTS ("multilingual accuracy is currently lower than English"). No fork adds Cantonese.

## 2. Alternatives

- **microWakeWord** — real home `OHF-Voice/micro-wake-word` (932★, pushed 2026-07-06, 34 issues; `kahrendt/` is a 15★ stub). MixConv streaming CNN on micro_speech features, Piper TTS, README: training "is still very difficult". `OHF-Voice/pymicro-wakeword` runs on Linux. No language statement. *Verdict: same English-TTS problem, harder training, nothing gained.*
- **Porcupine** — EN/FR/DE/IT/JA/KO/**Mandarin**/PT/ES; Cantonese absent, "contact sales". Free plan: non-commercial, 3 MAU, 3 custom keywords/30 days; paid ≈$500–899/mo. *Verdict: no Cantonese without a sales deal; licence excludes this bot.*
- **sherpa-onnx KWS** — Zipformer transducer, open-vocabulary via `text2token` (pinyin/phone/BPE), per-keyword boost + threshold. Models: wenetspeech-zh, gigaspeech-en, zh-en-3M (2025-12-20). **No Cantonese KWS model**; icefall `KWS` recipes exist only for wenetspeech/gigaspeech; `mdcc` (Cantonese) is ASR-only. Cantonese ASR models do exist (`zipformer-cantonese-2024-03-13`, `wenetspeech-yue-u2pp`). *Verdict: right architecture, but Cantonese means training your own Zipformer KWS — a GPU project.*
- **STT-as-wake (VAD → SenseVoice → fuzzy match)** — SenseVoice-small lists Cantonese; the 2025-09-09 int8 build is "fine-tuned with 21.8k h Cantonese". Published speed: **RTF 0.099, 1 thread int8, Cortex-A76** (5.6 s audio → 0.55 s); no x86 figure — measure your container. Non-autoregressive, so cost scales with speech seconds. Prior art: Mana (#618) fuzzy-matches continuous whisper.cpp and is criticised for cost; Home Assistant, LiveKit, Rhasspy all gate STT behind a classifier. *Verdict: the only path with verified Cantonese today; CPU under many concurrent speakers is the risk.*
- **Others.** Silero: VAD only. EfficientWord-Net (pushed 2026-02): few-shot Siamese, "trained on single words", 1 s window — wrong for a two-word phrase. Vosk: Mandarin only. Dead: Snowboy (2021), Mycroft Precise (2023).

## 3. Recommendation matrix

| Option | Cantonese | Training effort | CPU/stream | FP control | Maintenance risk |
|---|---|---|---|---|---|
| (a) Keep OWW, fix training | Poor: frozen English embedding, English adversarials, 2 failures | Medium: revert `end_jitter`, fix `best_val_fp`, raise SNR floor, add Cantonese negatives | Very low (RPi3 core: 15–20 models) | Weak: English val set, frame metric | High: 1 release since 2024 |
| (b) OWW frontend + own head | Same frontend ceiling | High | Very low | Medium | Medium |
| (c) STT-based wake | **Yes, verified** | Low (match rules) | Medium–high, unmeasured on x86 | Good: text-level, tunable | Low: reuses running container |
| (d) sherpa-onnx KWS | Only with a self-trained yue Zipformer | High, GPU | Low (3 M params) | Good: per-keyword threshold | Low: 14.8k★, pushed 2026-09-14 |
| (e) Porcupine | No | None | Very low | Good | Commercial; free tier excludes this use |

**Recommendation:** ship (c) now — per-user VAD segments → SenseVoice-yue → fuzzy match on 喂/威/餵 + Nora/諾拉/娜拉 variants — and benchmark per-stream CPU on the 16-core box before committing. Keep (d) as the medium-term path if CPU binds. Do not run a third (a) round without fixing the three verified defects and adding Cantonese speech to the FP validation set.

Sources: github.com/dscripka/openWakeWord (#335, discussions #52/#266); github.com/OHF-Voice/micro-wake-word; picovoice.ai/docs/porcupine/, /docs/faq/porcupine/; k2-fsa.github.io/sherpa/onnx/kws/, /onnx/sense-voice/pretrained.html; github.com/k2-fsa/icefall/tree/master/egs; home-assistant.io/voice_control/about_wake_word/; docs.livekit.io/agents/multimodality/audio/wakeword/; alphacephei.com/vosk/models; github.com/Ant-Brain/EfficientWord-Net.
