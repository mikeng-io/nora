# MiniMax CN Token Plan — 語音覆蓋深度研究

調查日期：2026-09-14
調查方法：官方文檔 + **實測 API**（非只讀文檔）

---

## 一、直接答案

**Token Plan 包語音，而且係所有檔位（Plus / Max / Ultra）都包。**

語音用量**同文字／圖像共用同一個額度池**（`general` bucket），唔係獨立額度。

| 檔位 | 月費 | 語音生成 | 說明 |
|---|---|---|---|
| Plus | ¥49 | ✓ | 3-4 Agent 併發 |
| Max | ¥119 | ✓ | 4-5 Agent 併發，+3 條/日 影片 |
| Ultra | ¥469 | ✓ | 6-7 Agent 併發，+5 條/日 影片 |

來源：`platform.minimax.cn/subscribe/token-plan` 比較表 —「語音生成 ✓ / ✓ / ✓」

---

## 二、你手上嘅 Key 類型（實測）

```
prefix: sk-cp-...   len=125
```

`sk-cp-` = **Token Plan 訂閱 Key**（唔係按量計費 key）。

關鍵限制（官方文檔原文）：
> 訂閱 Key 與普通按量計費 API Key 相互獨立，**不能混用**。

即係呢條 key 直接食 Token Plan 額度，唔會扣你 API 餘額。

---

## 三、實測：TTS 真係扣 Token Plan 額度

我做咗 before/after 額度對比（唔係睇文檔推論）：

```
BEFORE: general = 96% (5h) / 99% (weekly)
合成 3900 billed chars（Cantonese_CuteGirl, speech-2.8-turbo, 459.7s 音頻）
AFTER:  general = 93% (5h) / 99% (weekly)

→ 5 小時窗口 96% → 93%，跌 3 個百分點
```

**結論：語音 100% 由 Token Plan 額度支付，確認。**

### 額度換算（實測，帶誤差）

- 兩次獨立測試：2880 chars → 3 pts；3900 chars → 3 pts
- 即 **~1,000–1,300 billed chars = 1 個百分點**
- **5 小時窗口 ≈ 100,000–130,000 billed chars** 語音
- 按 turbo 價（¥2.00/萬字符）折算 ≈ **¥20–26 / 5 小時窗口**

⚠️ 誤差來源：額度 API 只回整數百分比，每次讀數有 ±0.5pt 誤差。
⚠️ **每週窗口量唔到** — 兩次讀數都係 99%，顆粒度太粗。

---

## 四、覆蓋邊啲語音模型

### TTS（語音合成）

| 模型 | 單價（元/萬字符） | 接口 |
|---|---|---|
| `speech-2.8-turbo` | ¥2.00 | T2A v2（同步）/ T2A Async |
| `speech-2.8-hd` | ¥3.50 | 同上 |
| `speech-2.6-*` / `speech-02-*` | 同上（歷史模型） | 同上 |

- 同步 T2A：單次最長 10,000 字符
- 異步 T2A：單次最大 100 萬字符

### ASR（語音識別）

| 模型 | 單價（元/萬字符） |
|---|---|
| （語音識別） | ¥2.50 |

**⚠️ 實測更正：ASR 模型名係 `asr-1.0`，唔係 `speech-2.6-ASR`。**

實測結果（用 TTS 生成嘅粵語音頻回喂 ASR）：
```
輸入: 而家為你播放陳奕迅嘅富士山下。（TTS 生成）
輸出: 而家為你播放陳奕迅嘅《富士山下》。✓ 完全正確
```
粵語識別正常，`language=yue` 生效。

---

## 五、音色：327 個，廣東話只有 6 個

實測全部 6 個粵語音色，`sk-cp-` key **全部可用**（status=0）：

| Voice ID | 名稱 | 實測 |
|---|---|---|
| `Cantonese_CuteGirl` | 可愛女孩 | ✓ |
| `Cantonese_GentleLady` | 温柔女聲 | ✓ |
| `Cantonese_KindWoman` | 善良女聲 | ✓ |
| `Cantonese_PlayfulMan` | 活潑男聲 | ✓ |
| `Cantonese_ProfessionalHost（F)` | 專業女主持 | ✓ |
| `Cantonese_ProfessionalHost（M)` | 專業男主持 | ✓ |

全部語言庫音色分佈：葡文 73、普通話 58、韓文 49、西班牙文 47、英文 16、日文 15…**粵語 6**。

---

## 六、⚠️ 唔包嘅嘢（重要）

官方原文（Token Plan 定價頁）：
> 可用模型覆蓋 MiniMax 全系模型（M3 / M2.7 / 圖像 / 語音），少量特殊模型
> （**MiniMax H3、音色設計、快速複刻** 等）暂不支持。

即係：

- ❌ **音色設計（Voice Design）** — 唔包
- ❌ **快速複刻（Voice Cloning）** — 唔包
- ❌ **MiniMax H3**（影片）— 唔包
- ❌ **Music 全系列** — 2026-08-20 起停用

**對 Nora 嘅影響：** 如果你想整一個**專屬 Nora 音色**（語音複製），
Token Plan 唔覆蓋。要用返系統 6 個粵語音色之一，或者另購語音資源包
（會**贈送**快速克隆音色：套餐一送 10 個、套餐二 30 個、套餐三 300 個）。

---

## 七、計費規則（實測驗證）

官方規則：**1 個漢字 = 2 個字符**（英文字母/標點/空格 各 1）

實測驗證：
```
'測試'      (2 漢字)         → usage_characters = 4    ✓
'你好世界'   (4 漢字)         → usage_characters = 8    ✓
'AB'        (2 拉丁)         → usage_characters = 2    ✓
'測試AB'    (2 漢字 + 2 拉丁) → usage_characters = 6    ✓
```

**實際成本例子：**
- Nora 一句典型回覆 ~50 漢字 = 100 billed chars
- turbo：100/10000 × ¥2.00 = **¥0.02 / 句**
- 1,000 句 ≈ **¥20**

---

## 八、額度機制

- **兩個窗口並行**：5 小時固定窗口 + 週窗口
- **只有 2 個 bucket**（實測 `/v1/token_plan/remains`）：
  - `general` ← 文字 / 圖像 / **語音** 全喺呢度
  - `video`
- 用完之後：① 扣已購積分 → ② 升級 → ③ 轉按量計費 → ④ 等窗口重置
- **未用完嘅額度唔會結轉**

---

## 九、對 Nora voice-channel 嘅實際含意

1. ✅ **Provider 定案冇問題** — MiniMax CN + Token Plan key 行得通，TTS 已實測成功
2. ✅ **粵語音色夠用** — 6 個，`Cantonese_CuteGirl` 實測正常
3. ✅ **STT 都覆蓋** — `asr-1.0` 粵語識別實測正確
4. ⚠️ **同 M3 搶額度** — 語音同文字共用 `general`，重度 Agent 使用會壓縮語音空間
5. ⚠️ **冇得 clone 音色** — 專屬 Nora 聲音要另購資源包
6. ⚠️ **5 小時窗口係真實限制** — 約 10-13 萬字符，即 ~1000-1300 句回覆

---

## 十、殘留不確定

- 每週窗口實際容量（顆粒度太粗，量唔到）
- 音色設計/快速複刻係「完全封鎖」定「轉按量計費」— 文檔話「暫不支持」，
  我實測 `voice_design` endpoint 有回應（trial audio），但**未驗證係扣套餐、
  扣按量餘額、定係純免費試聽**
- Plus 檔月度 6 億+ token 係「M3 換算」，語音實際可用量官方冇直接列出

---

# 補充調查（第二輪）

## 附錄 A：HD vs Turbo — 實測對比

### 官方文檔冇寫分別

查過 `pricing-speech.md` / `guides/speech-synthesis.md`，官方**只講價格同 RPM**，
完全冇描述兩個系列聲學上有咩分別：

| | Turbo 系列 | HD 系列 |
|---|---|---|
| 單價 | ¥2.00/萬字符 | ¥3.50/萬字符（**1.75x**） |
| 接口 | T2A v2 / async v2 | T2A v2 / async v2 |
| RPM | 60/200/500（按資源包） | 60/200/500（按資源包） |

→ **所以只能自己實測。**

### 實測（n=6 each，同文本同參數）

```
文本：41 漢字 → billed 78 字符（兩邊一樣）
```

| 指標 | turbo | hd | 判斷 |
|---|---|---|---|
| 延遲 median | 1.49s | 1.61s | 幾乎一樣（+8%） |
| MP3 bytes median | 148,596 | 149,172 | 一樣 |
| **頻譜質心 median** | **3,942 Hz** | **3,478 Hz** | **差 464 Hz** |
| 質心 within-model sd | 215 Hz | 204 Hz | pooled 210 Hz |

**質心差距 464 Hz > 組內標準差 210 Hz → 可分離（真差異，唔係噪音）**

### 頻段能量分佈

| 頻段 | turbo | hd |
|---|---|---|
| 0-1 kHz | 41.3% | **45.4%** |
| 1-4 kHz | 18.8% | **21.1%** |
| 4-8 kHz | 18.5% | 16.8% |
| 8-16 kHz | **21.3%** | 16.8% |

**結論：HD 高頻少咗 4.5 個百分點，能量移向中低頻 → 音色更「暖」更「滑」，
Turbo 高頻多 → 更「亮」更「脆」更「貼耳」。**

### 重要警告：TTS 係隨機嘅

波形對齊相關分析結果：
```
control（同模型跑兩次）：corr = +0.046 / +0.055
test  （turbo vs hd）  ：corr = +0.043
→ cross ≈ control → 波形層面完全睇唔出分別
```
每次生成都唔同（隨機採樣），**所以 waveform/byte 級對比冇意義，只有統計分佈有意義**。

### 對 Nora 嘅建議

- **用 turbo** — Nora 係對話 agent，1.75x 價錢買「暖啲」唔值
- 要「廣播級」質感（片頭、demo）先用 hd
- 兩者延遲差 0.12s，**唔影響實時性**

---

## 附錄 B：影片生成 — 實測可用，但有重大限制

### ✅ 真係行得通（已下載驗證）

```
task_id: 441630896283904 -> Success
輸出: 1280x720 h264, 25fps, 5.64s, 1,399,284 bytes
file: /tmp/minimax_test_video.mp4  (md5 c435786740a52eb82c664a98c2d19fb9)
```

唔係「API 回 200」——係真下載到一條**可播放嘅 mp4**。

### ❌ 但 H3 / H3-Max 被封鎖（API 明文拒絕）

```
POST /v2/video_generation  {"model":"MiniMax-H3"}
-> invalid params, TokenPlan 或 Credit 暂不支持 MiniMax-H3 系列模型 (2013)
```

同官方定價頁「少量特殊模型（**MiniMax H3**、音色設計、快速複刻等）暫不支持」**完全對上**。

### 可用影片模型清單（逐個實測）

| 模型 | 狀態 |
|---|---|
| `T2V-01` | ✅ 可用（實測出片） |
| `T2V-01-Director` | ✅ 可用 |
| `MiniMax-Hailuo-2.3` | ✅ 可用 |
| `MiniMax-Hailuo-02` | ✅ 可用 |
| `MiniMax-Hailuo-2.3-Fast` | ⚠️ 只支援圖生視頻（T2V 被拒） |
| `I2V-01` / `-Director` / `-live` | ⚠️ 需 `first_frame_image`（參數問題，非權限） |
| `S2V-01` | ⚠️ 需 `subject_reference`（參數問題，非權限） |
| `MiniMax-H3` | ❌ TokenPlan 明確不支援 |
| `MiniMax-H3-Max` | ❌ TokenPlan 明確不支援 |

### ⚠️ 影片額度係獨立 bucket

```
general  5h_status=1  窗口 5 小時
video    5h_status=3  窗口 24 小時  <- 唔同！
```

`video` bucket 係 **24 小時窗口**，唔係 `general` 嘅 5 小時。
**影片唔會食文字／語音額度。**

### ⚠️ 但成本無法確認（重要，唔想 fake）

實測：跑一條 T2V-01（渲染 240 秒），前後額度對比：

```
general  5h 86% -> 86%   (d0)   weekly 98% -> 98%   (d0)
video    5h 100% -> 100% (d0)   weekly 100% -> 100% (d0)
```

**兩個 bucket 都零變化。**

可能性（**未證實**，`/v1/get_balance`、`/v1/user/balance`、`/v1/query/credits`
全部 404，冇辦法查餘額）：

1. 影片由**已購積分包**扣，唔顯示喺呢兩個 bucket
2. `video status=3` 代表「未訂閱／不適用」
3. 計費有延遲
4. 推廣期免費

**⚠️ 我唔會講「影片係免費」—— 證據唔夠。** 要確認只有睇控制台
`console/usage` 頁面嘅明細。

### 對 Miles 嘅實際含意

- 想用影片做宣傳片 -> **H3（最新最靚）用唔到**，只能用 Hailuo-2.3 / 02
- Hailuo-2.3 768P 6s 按量價 ¥1.35/條
- Token Plan FAQ 原話：「文本、圖像、語音等資源可由已購積分覆蓋；
  **視頻等資源以對應套餐權益和控制台展示為準**」<- 即影片唔包喺共享池

---

# DECISION (2026-09-14) — TTS model: `speech-2.8-turbo`

**Status:** DECIDED by Miles, 2026-09-14.
**Decision:** Nora / voice-lab TTS uses **`speech-2.8-turbo`**. `speech-2.8-hd` is NOT used.
Video generation: **parked** ("skip video things").

## Rationale (measured, not assumed)

| | turbo | hd |
|---|---|---|
| price | ¥2.00/萬字符 | ¥3.50/萬字符 (**1.75x**) |
| latency (median, n=6) | 1.49s | 1.61s |
| billed chars | 78 | 78 (identical) |
| spectral centroid | 3,942 Hz | 3,478 Hz |

- Observable difference is **only 4.5 percentage points of 8-16 kHz energy**
  (turbo 21.3% vs hd 16.8%) — i.e. hd is marginally darker/warmer.
- That difference is **real but small**: 464 Hz centroid gap vs 210 Hz pooled
  within-model sd (separable, not noise).
- **1.75x price for a 4.5pp high-frequency shift is not worth it** for a
  conversational agent. Reserve `hd` for broadcast-grade output (idents, demos)
  if ever needed.

## Methodology caveat (recorded so it isn't re-litigated)

TTS output is **stochastically sampled** — every generation differs.
A waveform-alignment correlation test is therefore meaningless without a
control group. Measured:

```
control (same model, two runs): corr = +0.046 / +0.055
test    (turbo vs hd)         : corr = +0.043
```

`cross ≈ control` ⇒ waveform-level comparison shows nothing. **Only
distribution-level statistics (centroid, band energy, with n>1 and an sd) are
valid for TTS A/B.** Anyone re-running a single-sample byte/waveform diff will
get a wrong answer.

## Verified voice IDs on turbo (all 6 generated OK)

`Cantonese_CuteGirl`, `Cantonese_GentleLady`, `Cantonese_KindWoman`,
`Cantonese_PlayfulMan`, `Cantonese_ProfessionalHost（F)`,
`Cantonese_ProfessionalHost（M)`

→ **Voice selection is still OPEN** (not covered by this decision).
Samples: `samples/all6_cantonese_turbo.mp3`,
AB: `samples/AB_cutegirl_turbo_then_hd.mp3`

## Where this lands in Nora

`nora/config.py:1528 SpeechTtsConfig` is currently **deliberately inert**:

```python
enabled: bool = False
provider: str = ""   # <- MiniMax/edge-tts/xAI undecided
voice: str = ""      # <- open
```

Its docstring says *"No provider is chosen or committed here (MiniMax /
edge-tts / xAI undecided; operator deferred the TTS part as more complex)."*

This decision resolves the **model** half (`speech-2.8-turbo`). Still open
before that slot can be filled:
1. **provider** — MiniMax confirmed reachable, but is it the choice vs edge-tsd/xAI?
2. **voice** — 6 candidates tested, none picked
3. **trigger policy** — WHEN Nora speaks vs posts text (the actual hard part)
