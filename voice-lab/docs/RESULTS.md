# Voice Lab — Cantonese STT evaluation (2026-08-21)

Host: GMKtec NucBox M7 Ultra (Ryzen 7 PRO 6850U, CPU-only, no CUDA)
Purpose: verify SenseVoiceSmall for Nora's Cantonese STT before any repo change.

## Latency (CPU-only, this host) — 2026-08-21

| audio length | inference wall-clock | RTF |
|---|---|---|
| 7.0s | 0.278s | 0.040 |
| 6.9s | 0.295s | 0.043 |
| 8.6s | 0.335s | 0.039 |
| 57.0s | 2.575s | 0.045 |

- **RTF ≈ 0.04**（即 1 秒音頻 ≈ 40ms 處理，快過即時 25 倍）
- model cold load: ~4.8s（從 modelscope cache；server 起一次就冇，唔影響 per-request）
- OpenCC 繁化 post-process: ~0.0003s（可忽略）
- 典型 Discord voice note（5–30s）→ STT 約 0.2–1.3s

## Setup

- workspace: ~/workspace/voice-lab (outside nora repo, per Miles)
- venv: .venv (Python 3.13, torch 2.13.0+cpu, funasr 1.4.2)
- model: iic/SenseVoiceSmall (936MB, modelscope cache ~/.cache/modelscope)
- test audio: edge-tts zh-HK-HiuGaaiNeural, 5 clips (casual / numbers / mixed-EN / classic / news)

## Results (reference → hypothesis)

| clip | ref | hyp (raw) | notes |
|---|---|---|---|
| casual_convo | 喂，你今日得唔得閒呀？我想約你去食飯。 | 喂，你今日得唔得闲啊，我想约你去食饭。 | ✅ 口語「唔」保留；呀→啊 (ITN)；簡體輸出 |
| numbers_dates | 我聽日三點喺地鐵站等你，記住帶埋張八達通。 | 我听日三点喺地铁站等你记住带埋张八达通。 | ✅ 「喺」保留；數字/標點 ITN 正常 |
| mixed_eng | 你部 iPhone 幾多錢？我想 check 下個 email 先。 | 你部 iphone 几多钱，我想 check 下个 email 先。 | ✅ 中英混合完整；英文變小寫 |
| classic | 大家好，我係 Nora，好開心見到你哋！ | 大家好，我系 nora 好开心见到你哋。 | ✅ 「哋」保留；「係」變簡體系 |
| formal_news | 天文台表示，本港今日天氣炎熱，最高氣溫達三十二度。 | 天文台表示本港今日天气炎热最高气温达三十二度。 | ✅ 數字/正式語全對 |

- LID 全部正確 tag `yue`（非 zh）
- RTF 0.037–0.054（CPU）：1 秒音頻 ≈ 40ms 推理
- 輸出帶 tag 前綴 `<|yue|><|NEUTRAL|><|Speech|><|withitn|>`，需 strip

## Post-processing: OpenCC s2t → 繁體

全部 5 句轉繁體成功，廣東話用字保留（閒/嘅/喺/哋/係）：
- 「得唔得闲啊」→「得唔得閒啊」
- 「喺地铁站」→「喺地鐵站」（喺 本來就繁體，冇被改）
- 「我系」→「我係」
- 注意：s2t 出「天文臺」vs 香港慣例「天文台」— 可用 s2t 變體/詞表修正，低優先

## Verdict

- ✅ SenseVoiceSmall 原生識別 Cantonese (yue)，保留口語特徵，CPU 快
- ✅ OpenCC s2t post-process 完全可行，輸出適合 Nora (yue-Hant-HK)
- ⚠️ ITN 會「呀→啊」呢類正規化 — 如需原字保留可試 use_itn=False（未測）
- 未測: 真實人聲（edge-tts 係合成聲）、長音頻、faster-whisper 對比
- 部署形狀：FunASR server (Docker, OpenAI-compatible) 或直接 AutoModel 入 Nora describer

## 未決 (for Miles)
1. Nora [media.describe.audio] 係經 OmniRoute — OmniRoute catalogue 冇 audio key，需唔需要獨立 endpoint
2. TTS part 未測試 — xAI (ara) vs MiniMax 廣東話質素係另一 part
