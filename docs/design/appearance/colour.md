---
class: design
subject: the colour system, Paper and Thread, its scales, roles and rules
owns:
  - Thread Rule: "copper means something will happen: an action you can take or something Nora will do, never decoration"
  - Petal Rule: "petal means Nora and nothing else"
  - Person-Hue Rule: "each person in the portal keeps one stable hue, apart from copper, petal and the state colours"
area: appearance
links:
  - {to: docs/design/appearance/README.md, rel: uses}
---

# Colour

**Palette A, "Paper & Thread".** Every family is one 12-step ramp per lighting,
built in OKLCH, with lighter steps turned slightly toward yellow and darker
toward red or blue. The step number is the job:

| steps | job |
|---|---|
| 1-2 | backgrounds |
| 3-5 | component fills: normal, hover, pressed |
| 6-8 | lines and borders: quiet, interactive, strong or focus |
| 9-10 | solids and their hover |
| 11-12 | text: secondary, primary |

Night is tuned on its own, never inverted. Paper is warm ivory by day; by night
it is the navy of her vest, so the whole page darkens into indigo rather than
charcoal. The portal and the site carry these values one for one.

| scale | paper (light), steps 1 to 12 | night, steps 1 to 12 |
|---|---|---|
| **paper** (neutral) | `#FEFDFA #FAF9F4 #F4F1EA #EDE9E1 #E6E2D8 #DED9CD #D3CCBE #BFB6A5 #908572 #817664 #6B6253 #25211B` | `#0D0E10 #121417 #1B1E23 #22252C #282D35 #303640 #3E4552 #525B6C #6A758A #798399 #BBC4D8 #E8EDF8` |
| **indigo** | `#FCFDFE #F5F9FF #E9F2FF #DEEBFF #D3E4FD #C8DBF9 #B7CEF3 #9DB8E6 #324575 #263767 #304169 #1A2134` | `#0D0E12 #11131B #1A1E29 #202533 #262C3E #2D344B #3A4461 #4C597F #A1B7E8 #AFC7F9 #B0C4EE #E3EEFF` |
| **copper** | `#FFFCFA #FFF7F2 #FFEDE2 #FEE4D2 #FFDAC2 #FFCEAE #FCBD93 #F2A271 #AF5321 #9F440F #974A25 #391509` | `#140C09 #1D100B #2C1810 #381D12 #432314 #512916 #68351C #884622 #F4A16D #FEB482 #F8B284 #FFE8D6` |
| **petal** | `#FFFCFB #FFF7F3 #FEEDE6 #FFE3D8 #FED9CA #FDCDBA #F8BDA7 #ECA38A #CC7962 #BB6954 #914E3F #361813` | `#130D0B #1C110F #2A1916 #351E1A #40241F #4D2B24 #63372F #82493D #EDA28D #FEB29B #F3B29F #FFE7DE` |
| **danger** | `#FFFCFC #FEF7F6 #FEECEB #FFE2E1 #FED8D6 #FECBC9 #FEB7B7 #FE9498 #BD1F46 #AA003B #AB2D47 #410818` | `#170A0C #210C10 #331217 #40141C #4E1721 #5D1927 #781F31 #9E283F #EB596C #FD6A78 #FFAAAA #FFE6E5` |
| **success** | `#F7FFFB #ECFEF6 #DDF9ED #D0F3E4 #C3EEDB #B4E7D0 #9EDDC1 #7BCBA8 #0B764D #036640 #216D4B #092917` | `#09100B #0A170F #0D2416 #0D2C1B #0C3520 #0B4026 #085331 #006D42 #4CBD88 #5BCE99 #7DDBAE #C9FAE3` |

## Roles

| role | is | from |
|---|---|---|
| **canvas** | the page | paper 3 (night paper 1) |
| **surface**, **raised** | panels; inputs and sheets above them | paper 2, 1 (night 2, 3) |
| **hairline**, **border** | dividers; input and control borders | paper 6, 7 |
| **ink** | primary text: indigo, not black | indigo 12 (night paper 12) |
| **ink-soft** | secondary text, metadata | paper 11 |
| **indigo** | structure: dark bands, large type fills, night | indigo 9 |
| **action** | the one action colour: primary buttons, the focus ring, the current selection | copper 9, hover 10 |
| **accent-text** | links and the one emphasised word | copper 11 |
| **petal**, **petal-wash** | Nora herself | petal 10, 3 (night 9, 3) |
| **chestnut** | the wordmark and nothing else | her hair |
| **success**, **danger** | state only, always with an icon and a word | step 11 |

Every text role clears WCAG 2.2 AA (4.5:1) and APCA Lc 60 on canvas, surface
and raised in its own lighting; body ink reaches Lc 95 or more. Petal clears
3:1 as a non-text mark on all three.

## Rules

- **60-30-10.** About 60% paper, 30% indigo, 10% accent; the accent splits
  between copper (action, the one emphasised word, the thread) and petal plus
  state. No fourth colour family is added; importance is carried by size,
  weight and position.
- **Thread.** Copper means *something will happen*. On the stage, copper and
  warm light appear only where Nora is present and acting; a scene without her
  stays paper, indigo and mist.
- **Petal.** Petal means Nora: her presence dot, her avatar ring, her words,
  her own marks. Copper and petal sit close in hue, so petal never fills a
  control and copper never marks Nora. Danger sits close to both and always
  carries an icon and a word. Petal is never used for errors, emphasis or
  decoration.
- **Person-hue.** People in the portal keep a stable OKLCH hue at one lightness
  and chroma per lighting, at least 25 degrees from copper, petal and the state
  colours, and always travel with their initials or name.
- **No raw values.** No `#000`, no `#fff`, no slate. A colour continuing a
  photograph or render, such as a backdrop behind her, is sampled from that
  image and says where it was sampled; it is the one raw value allowed.
