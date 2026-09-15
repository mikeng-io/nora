---
type: design
title: "Voice Channel — TTS Playback & the Ducking Mixer"
description: "SpeakResult and the Speaker port; DuckSource generalising CrossfadeSource to attenuate-and-restore; how speak() composes with the existing MusicPlayer volume."
tags: [nora, discord, voice, tts, ducking, minimax, mixer, ports]
timestamp: '2026-09-14'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
---

> **PARTLY DEPRECATED 2026-09-16 — the ducking half is superseded.**
> Phase 1 of the audio-owner controller (`design/voice-channel/07-audio-owner-controller.md`, DEC-187) replaced duck-and-restore with a hard cut: speech preempts music inside the controller, `DuckSource`/`CrossfadeSource` are deleted, and `VoiceSession` no longer carries `speak()`. The `Speaker` port, `SpeakResult`, the speakable gate (§7) and the MiniMax decision stand — speech audio reaches the controller over `POST /stream`, which owns the mixing decision.

# Voice Channel — TTS Playback & the Ducking Mixer

## 1. The port (domain — pure contract, no I/O)

`domain/ports.py` gains a `Speaker` Protocol placed beside `Transcriber`
(`:917`) and an outcome dataclass beside `SttResult` (`:939`). It copies the
established posture exactly — **fail-closed, and an ERROR is never a PASS**
(R-27):

```python
@runtime_checkable
class Speaker(Protocol):
    """Text to speech audio (voice-channel TTS).

    Bound to `[speech.tts]`: `enabled = false` (or an unset provider) leaves
    the composition root with no speaker, and nothing about Nora's behaviour
    changes. Implementations NEVER raise into a caller's turn: any failure
    (network, timeout, unknown voice, empty audio) returns an unavailable
    `SpeakResult`, and the turn proceeds text-only — a reply that fails to
    sound is still a reply, never a dropped turn.
    """

    async def speak(self, *, text: str, voice: str = "") -> SpeakResult: ...


@dataclass(frozen=True)
class SpeakResult:
    """Outcome of one synthesis call. `available` is the single truth a
    caller reads. `reason` names why not (`error`, `timeout`, `empty`,
    `unsupported`) and `empty` stays distinct from `error` — the same
    distinction `SttResult` makes, for the same reason: a silent synthesis
    and an outage must not collapse into one metric."""

    available: bool
    audio: bytes = b""
    media_type: str = ""
    reason: str = ""
```

**Why bytes and not a path or URL:** the mixer consumes PCM frames in memory.
Returning a file path would force every caller to do disk lifecycle, and
Discord's player has no file concept — it reads an `AudioSource`. Bytes keep
the port free of both storage and `discord.py`.

`media_type` rides along because MiniMax emits MP3 while a future local
provider may emit WAV; the adapter that decodes to PCM needs to know which,
and discovering it by sniffing is a guess.

## 2. The adapter (infra — one folder per concern)

`nora/infra/speech.py` already holds `SenseVoiceTranscriber`, whose docstring
pattern is "one endpoint, never load-bearing, never raises". The TTS adapter
is its sibling. Two shapes are defensible:

- **`MiniMaxTtsSpeaker` appended to `nora/infra/speech.py`** — one file for
  "speech in, speech out". Simpler, and the file is small.
- **A new `nora/infra/tts.py`** — cleaner if a second provider is plausible.

**Recommendation: a new `nora/infra/tts.py`.** `speech.py`'s module docstring
and `__all__` are scoped to transcription specifically, and MiniMax is a
different dependency with a different failure surface (an outbound HTTPS POST
to a cloud vendor vs a local HTTP POST to a service we run). Merging them
would make one module own both an inbound local service and an outbound
cloud API call — two reasons to change.

## 3. The mixer (`DuckSource`) — the actual engineering

### Why `CrossfadeSource` cannot be reused as-is

Reading `crossfade.py` as shipped:

> wraps the OUTGOING track's still-live source and the INCOMING track's fresh
> source behind a single source that reads both every 20ms frame and blends
> them with a linear crossfade, then — once the fade window elapses —
> delegates purely to the incoming source.

That is **A→B handover**: `old` ramps 1→0, `new` ramps 0→1, then `old` is
dropped forever. Ducking is **A stays, B over it**: music attenuates to a
floor and *holds* there, TTS plays on top, music returns to its previous
gain. Different gain law, different lifetime, and the ducked source must
survive the whole speech segment.

### The shape

```python
class DuckSource(discord.AudioSource):
    """Reads `music` and `speech` simultaneously. While `speech` has frames,
    `music` is scaled to `duck_gain`; when `speech` is exhausted, `music`
    returns to 1.0 and reading continues from `music` alone.

    Generalises `CrossfadeSource`'s mechanic (two sources blended per frame,
    because Discord's player reads exactly one) to a different gain law:
    attenuate-and-restore instead of hand-over. Same `audioop` sample
    arithmetic, same 20ms frame cadence, same `keep_alive` requirement.
    """
```

Three properties this must hold, all inherited from the shipped class:

1. **`keep_alive`.** `CrossfadeSource`'s docstring documents why the
   previously-referenced `PCMVolumeTransformer` must be kept referenced — a
   dropped last reference triggers a CPython GC bug that sounds like a hard
   cut (fixed in `35c401c2`). `DuckSource` inherits the same requirement.
2. **Frame cadence.** 20 ms / 16-bit, matching `discord.opus.Encoder`.
3. **No gap.** The parent `AudioPlayer` thread must never see the source
   vanish — the existing code swaps via `VoiceClient.source = ...` live.

### Gain law

Linear scaling of music while speech is present, with a short ramp rather
than a step (a hard step is audible as a click). Ramp length is a config
value, not a constant: `[speech.tts].duck_ramp_ms`, default 150.

### Interaction with the existing volume

This is the subtle part and must be stated or it will be got wrong.
`DiscordVoiceSession` already wraps the whole mix in **one outer
`PCMVolumeTransformer`** at `self._volume` (`voice_session.py:221`), and that
volume is **persisted per guild** and deliberately *"survives across tracks"*
(`:150-154`).

So there are two independent gains:

- **User volume** (`/music volume`, 0.0–2.0) — the listener's choice, applied
  outer, must not be mutated by ducking.
- **Duck gain** — a transient multiplier applied *inside* the mix, to the
  music source only.

**Ducking must not call `set_volume()`.** Doing so would clobber the user's
persisted volume and it would come back wrong. `DuckSource` multiplies the
music frames by `duck_gain` *before* the outer transformer, then restores its
own factor to 1.0. The user's volume is untouched throughout.

This is the single most likely implementation error in the whole stage, which
is why it is called out here rather than left to the code.

## 4. `VoiceSession` gains `speak`

The port (`music_player.py:44`) currently declares `is_playing/play/pause/
resume/stop/set_volume/disconnect`. It gains:

```python
def speak(self, speech_audio: bytes, *, media_type: str, duck: bool = True) -> None: ...
```

**Why on `VoiceSession` and not inside `MusicPlayer`:** the session owns the
voice connection and the one audio source Discord will read. Only the session
can swap `.source`, and only it knows whether music is currently playing — the
exact precondition for ducking. `MusicPlayer` owns *music*, and a spoken
answer is not music; putting `speak` there would make the music domain own a
non-music concern.

`duck=False` is the honest default-when-nothing-plays path: if no music is
playing there is nothing to duck, and the session plays speech directly with
no mixer allocated. **Ducking is conditional on `is_playing()`**, never
unconditional.

## 5. Wiring (app — composition only)

`nora/app/composition.py` gains the speaker beside the transcriber, on the
same gate:

- `[speech.tts].enabled = false` → `speaker = None` → no speaker exists.
- `[speech.tts].enabled = true` → construct `MiniMaxTtsSpeaker(...)` once at
  boot, as `SenseVoiceTranscriber` is (`composition.py:1495`). The CN plan key
  is read from env `MINIMAX_CN_API_KEY` (already present for Hermes' own
  MiniMax script; the nora container must have it passed through).

The composition root stays the only place that knows which provider is real.
Nothing turns itself on because a config key exists — the same convention
`[presence]`/`[search]`/`[weather]`/`[compute]` follow.

## 6. Verification (what "done" means for stage 1)

Each item is a real command or a real observation, not a claim:

1. **Port contract** — `Speaker`/`SpeakResult` import clean; `mypy` passes on
   `ports.py`.
2. **Adapter in isolation** — call `MiniMaxTtsSpeaker.speak(text="喂，我係 Nora。", voice="Cantonese_CuteGirl")`
   and assert `available is True`, non-empty `audio`, `media_type` set. Then
   assert the **failure** path: an unreachable host / bad voice returns
   `available is False` with a reason and **does not raise**.
3. **Mixer arithmetic** — feed `DuckSource` two synthetic raw sources (a
   constant-amplitude tone as music, a short burst as speech) and assert the
   music frames are scaled by `duck_gain` *during* speech and back to 1.0
   *after*. This is testable with zero Discord and zero network — the same
   reason `CrossfadeSource` is testable.
4. **Volume independence** — set `_volume = 0.5` via `set_volume`, run a duck
   cycle, assert `_volume` is **still 0.5** afterwards. This is the regression
   test for §3's trap.
5. **Live** — in a real voice channel with music playing, trigger a speak and
   confirm by ear: music audible but lowered, Cantonese speech intelligible
   over it, music restored after. **This one cannot be automated** and is the
   actual acceptance criterion.

**Dependency note:** no new dependency. MiniMax TTS is a plain HTTPS POST via
`httpx` (already a repo dependency for the LLM path) — no SDK, no `uv add`.
Verified live from the voice-lab venv against `api.minimaxi.com` with the CN
plan key: real audio returns with `status=0`, and a quota probe shows TTS
deducts the plan's shared `general` bucket. "Verified" means the lab host had
network + key; the deployment image needs `MINIMAX_CN_API_KEY` passed through
and egress allowed — only that is shippable.

**Decided 2026-09-14 (Miles):** default voice is **`Cantonese_CuteGirl`**
(MiniMax native Cantonese; one of six verified available, all tested live).
`[speech.tts].voice` already exists as a config field. Every other MiniMax
Cantonese voice stays available via `speak(voice=...)` for per-call override —
the port carries `voice: str = ""`, and an empty string means the config
default.

Stage 1 is not done until 5 is confirmed by ear. 1–4 are the cheap gates that
make 5 likely to pass on the first attempt.

## 7. What is speakable — TTS output content rules (Miles, 2026-09-14)

The *mechanism* (TTS + ducking) is only one half of "sounds good". The other
half is **what Nora actually reads aloud**. A reply that is perfect as text
can be unlistenable as speech, and this distinction is a design rule, not a
style preference:

> **Anything that reads as a long opaque string must not be spoken.** URLs,
> links, handles, IDs, hashes, coordinates, timestamps in bare form — if it
> was pasted as a machine token, it stays a machine token. Nora says what the
> thing *is* (「個連結我 send 咗入 text」), never the thing itself (「
> https://…」).
>
> Miles's phrasing (2026-09-14): 「一啲譬如 URL 或者 link 呢類好長嘅嘢，我唔知點
> 講，即係一啲唔應該口噏出嚟嘅嘢，就唔好噏出嚟咯。」

Rules:

1. **No URL/hostname string in speech.** A `https://…` / `www…` / domain bare
   string is never read out loud. Replace with the destination's *kind*
   (「條 link 喺 text 度」), or a spoken name (「去 Nora 個 Drop」) — never the
   token.
2. **No long opaque identifiers.** message ids, user handles spelled as
   `@…`, hashes, UUIDs, `sk-…` keys, coordinates as bare numbers. These are
   text-channel objects. Speech says the class (「ID 係 1234」 for a short
   numeric id is fine; a UUID is not).
3. **The voice is a summary channel, not a data channel.** If the answer's
   payload is a list of N machine tokens, the voice says the summary and the
   *full* payload goes to the text channel in the same turn. TTS never
   competes with text; it points at it.
4. **Enforcement is in the render step, not a model wish.** The `speak()`
   caller receives text already shaped for speech. A `speakable(text)`
   helper (regex for `https?://`, `@\S+`, long hex/base64 runs) is the
   fail-closed gate: a reply that would read out a URL is either reworded by
   the caller or sent text-only. Do not rely on the model remembering this
   rule mid-turn.
