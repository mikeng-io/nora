# Her voice: provenance

The two clips behind "Hear her" in the Senses section
(`src/components/Senses.astro`), one per page language. They are her voice as
it sounds in a Discord voice channel: synthesised on 2026-09-27 with MiniMax
TTS and exactly the settings her voice-channel speaker uses (`nora/infra/tts.py`,
`nora/app/composition.py`, the live `config.toml` `[speech.tts]`):

| setting | value |
|---|---|
| model | `speech-2.8-turbo` |
| voice | `Cantonese_CuteGirl` (composition's default; the live config sets none) |
| speed | 1.3 (live config) |
| emotion | `fluent` (live config) |
| pitch, volume | 0, 1.0 |
| audio | MP3, 32 kHz mono, 128 kbps |
| language boost | `Chinese,Yue` for Cantonese (hard-set in the adapter); `English` for the English line |

The call ran on the deploy host, where the key lives (`.env`,
`MINIMAX_CN_API_KEY`), so the key never left it.

| file | the words | length |
|---|---|---|
| `nora-zh-hant.mp3` | 喺度。星期日七點，元朗嗰間。Ken，你訂咗枱未呀？ | 3.2 s |
| `nora-en.mp3` | I'm here. Sunday at seven, the Yuen Long place. Ken, did you book yet? | 3.5 s |

The words continue the page's own chat demo (the plan, the place, the one who
said he would book), and the page shows them as a transcript beside the
button, so the clip is never the only way to get them.

Both were checked by transcribing them back through her own speech-to-text
service (the deploy host's `stt`, language `yue`): the Cantonese came back
as 「喺度星期日七點元朗嗰間 k你訂咗臺未啊」 (Ken as "k", 枱 as its homophone
臺), the English as "i'm here sunday at seven the u n long place ken did you
book yet". An adapter configured with another voice or speed makes these
stale: regenerate them with it.
