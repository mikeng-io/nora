# Earphones in — the brief

A 20-second motion showreel for Nora, in English and in Hong Kong Cantonese,
cut for 16:9, 9:16, 4:5 and 1:1. This file is the creative decision record:
the idea, the beat sheet, the script in both languages, and why each choice
was made. How to rebuild it is in `README.md` beside this file.

## Where the idea comes from

Everything she is comes from the repo, read as data:

- `README.md` and `site/src/i18n/en.ts`: she lives in one circle of friends'
  group chats (Signal, WhatsApp, Discord, Instagram), listens, remembers at
  five speeds, answers when tagged, and would rather say nothing than say
  something wrong.
- `design/self-reference/05-visual-identity-spec.md` and
  `design/self-reference/07-native-and-embodied-form.md`: Mini Nora is her
  native form on every internet surface. The eight anchors (proportions, messy
  bun, minimal face, blush, grey hoodie, white wired earphones, blue-grey
  jeans, white sneakers) hold in every frame; the embodied form never appears.
- `DESIGN.md`: the two lightings (Studio, Late night), denim for action, blush
  for Nora and nothing else, sentence case, and the Cable Rule: her one
  decorative device is a single thin line, like the earphone cable.

## The idea

**One line runs through the whole reel: her earphone cable.** It drops from
her earbud in the first shot, becomes the thread the group's messages hang
on, splits into the five lanes of her memory when the lights come on, turns
into a route, rain, a waveform, the scan across a photo, and is
pulled back into her hand at the end, where she presses the remote and the
music stops.

The arc is lighting as much as story: late night (the chat, lit by her
phone), lights on at the drop (memory and senses in Studio), late night again
for the quiet and the end card.

The punchline is her manner, told as a joke she would make: *"And when
there's nothing worth saying… I don't."* The click of the remote cuts the
music to silence before she says it.

## Beat sheet (120 bpm; a bar is 2 s)

| time | scene | picture | sound |
|---|---|---|---|
| 0.0–2.0 | cold open | macro of her ear and earbud, focus pull, the camera tilts down the cable | a chord heard through earphones, the earbud seating, the cable drawn |
| 2.0–6.0 | the room | the cable is the thread; five messages land on it; the room reskins Signal → WhatsApp → Instagram → Discord while her member pill never changes | groove enters muffled and opens up; each message pops on a chord tone |
| 6.0–8.0 | the tag | "@nora" lands; thinking dots; her answer types on at 6.85, then "Ken, did you?"; the note she read it from | break, denim ping, ticks, riser, a light switch |
| 8.0–11.0 | memory | lights on: the line splits into five lanes moving at five speeds; she writes in her notebook; "still open" is stamped on the plan | the drop |
| 11.0–14.0 | senses | one beat each: where you are, the sky, what's playing, what you sent; then two beats for what you forgot, the message she sends on her own | map ping, rain, a needle drop, a shutter, her chime |
| 14.0–16.6 | the quiet | everything collapses into one line; she is holding the cable, eyes closed, and the camera pushes in on the remote | the filter closes back to her earphones |
| 16.6 | the click | the remote clicks, the frame goes dark | the music stops dead |
| 16.6–20.0 | end | "I don't." in the silence, held for a beat; at 17.8 the late-night portrait, the wordmark, the tagline underlined by the cable | the home chord, one chime for her |

## Script

Nora speaks in the first person, calm and slightly deadpan. The lines are
short so the picture carries the reel; each is also set on screen as display
type, word by word as it is spoken, so the reel reads with the sound off.

| id | English | Cantonese |
|---|---|---|
| vo1 | I live in your group chat. | 我住喺你哋個 group 度。 |
| vo2 | Mostly, I listen. | 大部分時間，我都係聽緊。 |
| vo3 | I remember what matters. | 重要嘅嘢，我會記住。 |
| vo4 | And when there's nothing worth saying… | 冇嘢值得講嘅時候…… |
| vo5 | I don't. | 我就收聲。 |

The Cantonese is written for the ear, not translated: "group" stays English
because that is what the group calls it; "收聲" (shut up, applied to herself)
is the local deadpan that "I don't" carries in English. On-screen copy in the
Cantonese cut comes from the site's own zh-Hant voice
(`site/src/i18n/zh-hant.ts`): 星期六班友, 打邊爐, 元朗嗰間, 訂枱. Where the site says
房 for a chat, the reel says group, as the voice-over does, so the chat has one
spoken name; 群組 stays only in the tagline, the site's own line.

## Decisions

- **Her voice is the one she already has.** Both languages use
  `Cantonese_CuteGirl`, the MiniMax voice she speaks with in the Discord voice
  channel today, so the reel sounds like her and the two cuts sound like the
  same person. Four voices were auditioned for English; all four transcribed
  back word-perfect (0/21 errors), so intelligibility did not decide it and
  identity did. Model `speech-2.8-hd` (the warmer one the voice-lab findings
  recommend for a demo), emotion `calm`.
- **The music is composed in code, not generated.** MiniMax's Music models are
  switched off on this account's plan (voice-lab findings, 2026-08-20), and a
  generated track would make the picture chase its beats. The score is a
  lo-fi half-time groove in F at 120 bpm, written against the same clock as
  the picture, so every cut and every message lands on the beat it was
  written for. It is reproducible byte for byte from `pipeline/score.py`.
- **"Through her earphones."** The score is low-passed whenever we are close
  to her (the open, the quiet) and opens fully when the lights come on.
- **One format is not cropped into another.** Each frame has its own layout:
  the room is a column beside her in 16:9, a thread above her in 9:16 and
  the two squares; memory's labels sit beside the lanes in 16:9 and above
  them elsewhere.
- **Blush only means her**: her presence dot, her bubble, her open note, the
  scan across the photo (she is the one looking), her chime. The @-mention
  and the route are denim; people keep the portal's per-person hue.
- **Invented people, invented chat**: Ken, Mei and Jo are the site's scripted
  example group, not real members.
- **A review changed the cut.** An independent reviewer went through all eight
  files frame by frame and found 20 defects. All 20 were fixed. The largest
  were timing: the punchline and her reply were on screen too briefly to read
  with the sound off. That cost the senses montage its bus beat, so her
  unprompted reminder holds for a full second. The rest were layout: an
  end-card portrait with a hard edge, captions over her hands, chips under
  labels, and Cantonese lines breaking inside a word.

