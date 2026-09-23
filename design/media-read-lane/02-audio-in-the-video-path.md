---
type: design
title: "Audio in the Video Path — the `-an` that throws away half the evidence"
description: "Every video Nora sends is silent by construction: `_video_args` passes `-an` unconditionally. Audio is present in real clips (see the CORRECTED note in SS1: the original 9-of-11 count was unreproducible and its arithmetic impossible). The operator has directed that audio be enabled, overriding the earlier no-verbatim-transcript decision."
tags: [nora, media, video, audio, stt, egress, pii, superseded]
timestamp: '2026-09-22'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
  - {dst: 02-audio-in-the-video-path, dst: 04-timeout-and-models, rel: depends_on, provenance: asserted}
---

# Audio in the Video Path

## 1. The defect

```
nora/infra/vision/preprocess.py:195        "-an",
```

`_video_args` passes `-an` with **no flag, no branch, no config key**. Every chunk
of every video Nora has ever described was silent. She has only ever been able to
*see* video, never *hear* it — and nothing in the code says that was a choice.

**The audio is really there.** Of the 11 stored clips in
`state/signal-cli/attachments/`, **9 carry an `aac` audio stream**; only 3 have
none. This is the common case, not an edge case.

> **⚠️ CORRECTED 2026-09-23 (independent audit) — the counts above do not add up and
> do not reproduce.** 9 + 3 = 12, not 11. Worse, a live probe of that directory today
> finds **164 video-capable files, 8 with an audio stream and 156 without** — the
> opposite ratio from the sentence above. The original count is not reproducible and
> its arithmetic is impossible, so **treat "audio is the common case" as UNVERIFIED
> for the stored-clip population.** What IS measured and does not depend on this
> count: the 9-of-11 statement came from an early survey whose scope was not
> recorded, and the ingest population has plainly grown since (164 files now).
>
> **The design does not rest on this number.** The argument for keeping audio is that
> (a) the flag is unconditional by construction (`-an` with no branch), (b) the
> measured token delta on a real audible clip is small (§2), and (c) the operator
> directed it. The clip-ratio sentence was supporting colour and is now marked as
> unreliable rather than quietly dropped.

## 2. What is being thrown away — measured

Same 35-second clip, same 720p/24fps/CRF26 geometry, one variable changed:

| | prompt tokens | seconds | what it produced |
|---|---|---|---|
| `-an` (today) | 8,172 Δ | 29.6 | visual + on-screen text only |
| audio intact | 8,503 | 43.8 | **+ verbatim Cantonese dialogue, timecoded** |

The audio version produced:

> **00:00–00:04** Male (Jason): 「…都好正啊」 / Female Host: 「多謝」 / Male: 「好彩係360…」
> **00:09–00:15** Female Host: 「今日我地後台唯一比Jason既Brief就係…」

It also **named the performer** (Jason Cortez / 陳詠謙) and recovered signage the
silent version missed (`GONDOLA`). Cost: **~289 tokens, +3.5 %.**

On a 7 s concert clip with audio, the model transcribed song lyrics and crowd
noise, with timestamps.

## 3. Standalone audio also works

A real Signal voice note (`.m4a`, 8.1 s) through the same provider via
`input_audio`:

```
prompt_tokens=89  8.8s  →  "male speaking Cantonese in an aggressive,
                            threatening, confrontational tone… issuing a
                            warning or threat"
```

Correct language, correct tone, correct register. So `[media.describe.audio]`
— `enabled = false` today (`config.toml`) — is **fully wired and inert**. The
`_AUDIO_FORMATS` table (`client.py:112`) already maps `m4a`/`mp3`/`wav`/`ogg`/
`opus`/`webm`/`flac`, and `_content_block` (`client.py:179`) already builds the
`input_audio` block. Enabling is a config change plus the prompt work below.

## 4. 🔴 This OVERRIDES a prior decision — recorded, not silently reversed

The operator's direction is explicit: 「audio 亦都 enable 佢啦，直接啟動佢 override
之前個 decision」. Two prior decisions are being overridden, and both must be
marked, not deleted:

**(a) The video prompt deliberately excludes speech.**
```
nora/infra/vision/client.py:84-88   "video": (
    "Describe what happens in this video, and transcribe any text on screen. "
    "If people speak, say briefly what it is about rather than writing out "
    "what was said.")
```
The comment above it says speech is *"deliberately summarised rather than
transcribed"* (the same block notes that verbatim belongs in a future
`[media.transcribe]` with "its own egress analysis" — see §5, which is exactly
that concern surfacing in the original design).

**(b) Issue #117 explicitly rejected STT-on-video as out of scope.**
Recorded at `preprocess.py:186-190` and formalised in **DEC-161** as *"`-an` stays
so the chunks are silent: audio extraction plus STT was proposed and rejected as
scope."* Note the *reasoning* there was scope discipline, not a safety or quality
objection — so overriding it on operator direction is legitimate. **The scope
argument is not being refuted, it is being superseded by a later decision**, and
DEC-161 must carry a `DEPRECATED IN PART 2026-09-22` prefix pointing at DEC-197
(see `05-decisions-and-index.md §2`).

**What replaces it:** a video prompt that asks for speech to be transcribed when
it is the substance of the clip, and summarised when it is not. The `audio`
prompt's prohibition («Do NOT write out a word-for-word transcript») is likewise
revised — but see §5, which is the part that needs care.

## 5. ⏸️ CARRIED, NOT BLOCKING (status: operator-parked 2026-09-23) — and the review corrected my question, not just my answer

> **Status update 2026-09-23.** This was the bundle's second blocker. The operator
> parked it — «唔好俾呢一個 PII 呢一嚿嘢成為 blocker» — and the findings were moved to
> `~/workspace/nora-bench/spikes/pii-layer/` (README, harness, prototype; nothing
> production). The operator's direction on the substance: block **identity-bearing**
> values (phone, HK ID, account), **not** addresses by content, because «既然我哋呢啲
> group 都係 trusted 嘅 content» — and the two-part answer is `pii.engineer` plus an
> HKID pattern. **The section below stays because the gap it describes is real and
> pre-existing**; what changed is that it no longer gates this bundle. Its own
> consequence for THIS bundle is stated at the end of the section.

**The first version of this section asked the wrong question.** It asked *does the
screen run on this path?* The review answered: **yes, it does** — screens already run
at `nora/agent/tools.py:5489`, `nora/domain/media_join.py:254` and
`nora/ingest/comprehension.py:474`. The reachability question is closed.

**The real problem is what the screen DETECTS, and the answer is already written down
in this repo** — in DEC-114 itself, which I mis-characterised as a scope decision:

> **DEC-114 (3):** «The audio prompt asks for the GIST, never a transcript, and that
> is a **safety decision not a style one.** STT is excluded here because a verbatim
> transcript is a materially larger R-18/R-28 egress surface — `_serve_media`'s own
> docstring records that **`scan_identifiers` does not catch a bare local number**,
> and people say addresses and phone numbers aloud.»

And in code, `nora/agent/egress.py:45-46` and `:67`:

```
Deliberately NOT matched here:
* Free-standing digit runs.  …
_E164 = re.compile(r"\+\d{8,15}\b")   # Requires the +, so "call me at 12345678" stays sendable
```

So the gap is precise and pre-existing:

| spoken form | screened? |
|---|---|
| `+85290000103` — E.164 with `+`, **digits unbroken** | ✅ matched |
| `+852 9000 0103` — E.164 **with spaces** | ❌ **NOT matched** (`\+\d{8,15}` cannot span a space) |
| `+852****1730` — the operator's masked form | ❌ **NOT matched** |
| `9000 0103` — a bare **local** number | ❌ **deliberately not matched** |
| an address spoken in Cantonese | ❌ not in scope |
| account number, or a number said as words | ❌ not in scope |

> **⚠️ CORRECTED 2026-09-23 (independent audit).** The first version of this table
> said `+852****1730` was **matched**. **It is not, and neither is the spaced form**
> — verified by executing the real function (`nora.agent.egress.scan_identifiers`,
> repo venv): the 12-char masked string and the 14-char spaced string both return
> `None`, while the unspaced 12-char form returns `phone`. `\+\d{8,15}` is a
> contiguous digit run; **any separator kills the match.**
>
> **The finding is therefore worse than the bundle originally claimed, not better:**
> Miles's own business number written in its normal human format
> (`+852 ` + `6763` + ` ` + `1730`) is **not screened at all**, and the masked form the
> bundle cited as its "matched" control also is not. The gap this section describes
> is wider than stated — the only form the screen catches is the one nobody writes.
> (Method note: printing phone-shaped literals in this repo's stdout gets them
> redacted by the log layer, which made one run look self-contradictory; the
> corrected test printed lengths and verdicts only.)

**Enabling transcription therefore widens a surface DEC-114 already assessed and
deliberately closed.** That is not a wiring question that a test can settle; it is a
decision. **The «keep it on the video lane» fallback I proposed does NOT mitigate it** —
video transcription carries the same bare-local-number exposure, as the review pointed
out. So the fallback as written was not a mitigation at all.

**What the bundle now requires:**

1. **Record DEC-114 clause (3) as superseded**, not "extended". Operator direction
   (「直接啟動佢 override 之前個 decision」) covers it — but it must be named as the
   safety decision it is, so the override is visible to whoever reads the index later.
   A row that says "extended, not changed" would hide exactly the change that matters.
2. **Decide the screen's coverage for spoken content** — extend the pattern (a bare
   10-digit local number, HK formats, spoken-word digits) or accept transcription with
   the known exposure stated. **This is an operator decision with the finding attached,
   not an implementer's call.**
3. The named test is *not* "a spoken digit string does not survive" (the current code
   guarantees it *will* survive for a bare local number). It is: **a bare local number
   in a transcript either does not reach the description, or the accepted exposure is
   documented in the DEC row.**

**This supersedes the section's earlier text, which is retained below only to show what
was wrong.** My original framing — "verify whether the screen covers speech transcripts,
and if not, narrow the scope" — treated a decided safety boundary as an unknown wiring
detail. That is the error worth remembering: *a repo that writes down WHY it excluded
something is telling you what breaks when you include it.*

## 6. Prompt changes required — BOTH prompt families

The first version of this section listed only `client.py`'s prompts. **That misses the
automatic path, which is the one that matters most for Instagram** — the review caught it.

**There are TWO prompt families, and they are separate constants:**

| family | location | who uses it |
|---|---|---|
| `_PROMPTS` (per modality) | `nora/infra/vision/client.py:59-90` | live `media_fetch`, the deliberate look |
| `COMPREHEND_QUESTIONS` | `nora/infra/vision/comprehend.py:105-113` | **automatic ingest comprehension** |

The automatic video question today (`comprehend.py:105-107`):

```
"video": (
    "Describe what happens in this segment of a video, and transcribe any "
    "text on screen."
)
```

— **on-screen text only, no speech at all.** Automatic audio
(`comprehend.py:109-113`) asks for the gist and explicitly says *"Do not repeat any
phone number, address or account number you hear."* And the automatic video read
**re-templates the question at the call site** (`comprehend.py:314-318`), appending
"Answer in plain prose, not JSON" — so editing the constant alone is not sufficient
care either.

**Editing only `client.py` would therefore implement nothing for the automatic path**,
which is where an Instagram Story/Reel actually gets read. Both families need the
change, and both need a behavioural check through the real entry points.

**Related trap:** `_describe_models` requires **enabled AND a named model**
(`composition.py:610-613`). So «enable audio» alone leaves it `unavailable`, and naming a
model without also setting `provider` sends it down the OmniRoute route — which drops
nothing for audio but is a different transport than intended. **The complete audio
change is three settings**, not one:

```toml
[media.describe.audio]
enabled  = true
model    = "mimo-v2.6-flash"
provider = "opencode-go"
```

## 7. Cost

Audio adds ~3.5 % tokens on a video read (measured) and a large share on a
standalone voice note. Both are negligible against the video token totals
(13,166 for a 57 s clip). The real cost is **latency** — 29.6 s → 43.8 s on the
measured clip — which is why this bundles with `04-timeout-and-models.md` rather
than shipping alone: **enabling audio on the current 36 s budget would make the
lane fail more often, not less.**

Also: `VIDEO_CRF = 26` (`preprocess.py:69`) is a *video* quality knob. Re-encoding
audio should specify its own codec and bitrate explicitly (the benchmark used
`-c:a aac -b:a 64k`) rather than inheriting ffmpeg's default, which differs by
container and would make token cost depend on the input format.

## 8. ⚠️ Already-comprehended media will keep its old silent description

**Raised by the review; I had not considered it.** Turning audio on and moving the
provider does **not** re-read anything already comprehended:

- Automatic comprehension **skips existing readings** — `nora/ingest/comprehension.py:285-291`
  and `:496-509`.
- The live path's layer-one **returns stored comprehensions** before reaching the
  describer — `nora/agent/tools.py:5213-5224`.
- The video exclusion from the vision cache (`tools.py:5298`, `cacheable_modality`) does
  **not** cover either of those.

So the same clip can keep returning its old silent description indefinitely, and the
change's headline benefit ("Nora can hear video now") will appear not to work on any
clip already in the store. Conversely, **reverting** the provider or prompts does not
remove transcripts already persisted.

**This is a persisted-data policy the bundle was missing.** It needs a stated
treatment — re-read on demand, a one-time backfill, or explicit acceptance that
history keeps its old readings — plus tests for both directions. No schema migration is
inherently required, which is presumably why it was easy to overlook.

## 9. Rollback

Reverting requires more than restoring `config.toml`: under `extra="forbid"`
(`nora/config.py:745-749`) an older build **refuses to start** if the config carries the
new `provider` Literal value or the new `chunk_concurrency` key. Rollback is therefore
**config-and-image together**, not config alone. State this in the PR body.
