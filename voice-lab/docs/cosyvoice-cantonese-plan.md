# CosyVoice 2 for mass Cantonese 「喂 Nora」 generation — findings (2026-09-16)

## 1. Cantonese support and how it is selected
- CosyVoice2/3 list Cantonese only as one of "18+ Chinese dialects/accents (Guangdong…)", not as one of the 9 languages (https://github.com/FunAudioLLM/CosyVoice, https://huggingface.co/FunAudioLLM/CosyVoice2-0.5B).
- **No `<|yue|>` tag in CV2/CV3.** `<|yue|>` exists only in the v1 (CosyVoice-300M, Whisper-style) tokenizer. `CosyVoice2Tokenizer` (Qwen BPE) adds only `<|endofprompt|>`, `[breath]`, `[laughter]`, `<strong>`, `[noise]`… (https://github.com/FunAudioLLM/CosyVoice/blob/main/cosyvoice/tokenizer/tokenizer.py). Dialect is implicit: prompt speaker + written-Cantonese script (嘅/咗/嚟), or an instruct string.
- **Zero-shot** `inference_zero_shot(tts_text, prompt_text, prompt_wav, zero_shot_spk_id='', stream=False, speed=1.0, text_frontend=True)` — `prompt_text` (transcript) is required and is tokenised *without* normalisation (`frontend_zero_shot` → `_extract_text_token`). Script: whatever the clip says; CV yue sentences (Traditional, written Cantonese) are the right input. Whether Trad vs Simp affects quality: unverified. It logs "synthesis text … too short than prompt text …, this may lead to bad performance" whenever tts_text is shorter than prompt_text — will fire for every 「喂 Nora」; pick clips with SHORT sentences.
- **Cross-lingual** `inference_cross_lingual(tts_text, prompt_wav)` = zero-shot with `prompt_text` and `llm_prompt_speech_token` deleted from the LLM input (flow still gets the prompt mel for timbre) (cosyvoice/cli/frontend.py). No transcript needed and no length warning, but weaker similarity/prosody; intended for prompt≠target language. Use zero-shot for yue→yue; keep cross-lingual as a fallback for suspect transcripts.
- **Instruct2** `inference_instruct2(tts_text, instruct_text, prompt_wav)`: frontend does NOT append `<|endofprompt|>`; current example.py passes `'用四川话说这句话<|endofprompt|>'`, so use `'用粤语说这句话<|endofprompt|>'`. Drops the prompt speech tokens from the LLM (timbre via flow only).
- **Best Cantonese checkpoint: `ASLP-lab/Cosyvoice2-Yue`** (Apache-2.0, drop-in `CosyVoice2('ASLP-lab/Cosyvoice2-Yue')`, fine-tuned on ~12k h WenetSpeech-Yue). MER 10.33% / SIM 0.821 vs base CosyVoice2 14.38% / 0.812 (https://huggingface.co/ASLP-lab/Cosyvoice2-Yue, https://arxiv.org/html/2509.03959). Also `ASLP-lab/Cosyvoice2-Yue-ZoengJyutGaai` (higher quality, single-voice fine-tune — unverified for cloning).

## 2. Text formatting
- `text_normalize`: if text contains Chinese → wetext `zh_tn` normalise, `replace_blank` (drops the space in 喂 Nora → 喂Nora), `.`→`。`, `remove_bracket`, trailing `，`→`。`, then `split_paragraph`. wetext keeps Latin words ("你好 WeTextProcessing 1.0"→"…一点零"; https://github.com/pengzhendong/wetext). Any `<|…|>` in the text disables normalisation entirely. `text_frontend=False` bypasses it.
- "Nora" reaches the Qwen tokenizer as Latin; pronunciation is the model's guess (English-ish). To force Cantonese phonology write 諾拉/娜拉. No jyutping input exists (CV3 has Mandarin pinyin tokens `[nuò]` only). Plan: generate both `喂，Nora。` and `喂，諾拉。`, keep what ASR accepts.
- Length: LLM enforces `min_len=(text_tokens)*2`, `max_len=*20` speech tokens at 25 Hz (EOS forbidden before min_len) (cosyvoice/llm/llm.py) — a 3–4-token text yields 0.24–3.2 s. Repetition/omission (#1015) and CV3 zero-shot garbling (#1735, open) are documented. Mitigations: end with `。`, add a particle (`喂，Nora呀。`), `speed` (mel interpolation, non-stream only), over-generate and filter by ASR + duration.

## 3. Reference clips
- `load_wav(path, 16000)`; frontend resamples internally (24 kHz mel for flow, 16 kHz for speech tokenizer + CAM++). README: ≥3 s clear audio. Filter CV clips to 3–10 s, `up_votes≥2, down_votes=0`, and an SNR/DNSMOS gate (WenetSpeech-Yue used DNSMOS>2.5, SNR>25 dB for TTS). Noisy prompts clone the noise — unverified how much CV mic variance hurts; measure.
- Output `cosyvoice.sample_rate` = 24000 for CV2/CV3 → resample to 16 kHz. (ASLP card says "16kHz"; likely the prompt rate — unverified.)

## 4. Runtime (Modal T4)
- Install: `git clone --recursive https://github.com/FunAudioLLM/CosyVoice && pip install -r requirements.txt`, `PYTHONPATH=third_party/Matcha-TTS`. No official `pip install cosyvoice` (unverified). Python 3.10 per README.
- Pins (requirements.txt): torch==2.3.1, torchaudio==2.3.1 (`--extra-index-url …/whl/cu121`), onnxruntime-gpu==1.18.0, transformers==4.51.3, modelscope==1.20.0, wetext==0.0.4 (no pynini). Skip tensorrt-cu12, deepspeed, vllm.
- Weights: HF `FunAudioLLM/CosyVoice2-0.5B` / ModelScope `iic/CosyVoice2-0.5B`; `ASLP-lab/Cosyvoice2-Yue` (HF).
- `fp16=True` = `torch.cuda.amp.autocast` on LLM and token2wav — fp16 not bf16, so T4 is fine. `load_jit` = TorchScript flow encoder; `load_trt` needs TensorRT — skip. VRAM: no measured T4 figure; third-party estimate ~1.2 GB weights fp16; 16 GB is ample. Speed on T4: **unverified**, no published RTF; budget for measuring. No batch API — loop per utterance.
- Licence: Apache-2.0 code and weights.

## 5. Newer models
Fun-CosyVoice3-0.5B-2512 and `_RL` (Dec 2025, Apache-2.0, open weights HF/ModelScope; https://huggingface.co/FunAudioLLM/Fun-CosyVoice3-0.5B-2512). API change: prompt_text must be prefixed `'You are a helpful assistant.<|endofprompt|>'`; Cantonese instruct `'请用广东话表达。<|endofprompt|>'`; `CosyVoice3` has no `load_jit`. Paper gives no Cantonese metrics; #1735 reports frequent garbled zero-shot output. A/B against Cosyvoice2-Yue.

## 6. Fallbacks
- **GPT-SoVITS** v2+ (v2/v2Pro/v2ProPlus/v3/v4): `text_lang`/`prompt_lang` ∈ `all_yue|yue|auto_yue`; ref audio enforced 3–10 s; prompt_text optional except v3; MIT; weights `lj1995/GPT-SoVITS`. Zero-shot works, no training needed.
- **Fish/OpenAudio**: S1-mini lists 13 languages, no Cantonese (CC-BY-NC-SA-4.0). S2-Pro (5B, Fish Audio Research License, non-commercial) lists 80+ languages without yue. Not viable.
- **MiniMax**: `POST https://api.minimax.io/v1/voice_clone` (file_id from `/v1/files/upload purpose=voice_clone`, voice_id, `language_boost:"Chinese,Yue"`); audio must be **10 s–5 min** (CV clips too short → concatenate per speaker); unused voices deleted after 7 days. Price: $1.5/voice, T2A speech-2.8-turbo $60/M chars (third-party quoting official page — not fetched); Token Plan doc confirms rapid voice cloning excluded. 300 voices ≈ $450.

## 7. Common Voice yue on HF
`JackyHoCL/common_voice_22_yue`: features `client_id, path, audio (16 kHz cast, mp3), sentence, sentence_id, sentence_domain, up_votes, down_votes, age, gender, accents, variant, locale, segment`; splits validated 191,152 / train 7,419 / validation 5,130 / test 5,130 / other 79,908 / invalidated 8,075; ~7 GB parquet. Card says "All Right reserved by mozilla-foundation" — upstream Common Voice is CC0 (https://en.wikipedia.org/wiki/Common_Voice; now hosted at Mozilla Data Collective, CV 24.0 Cantonese: 1,174 speakers, 307 h). Pick one 3–10 s clip per `client_id` from `validated`, use `sentence` as prompt_text.
