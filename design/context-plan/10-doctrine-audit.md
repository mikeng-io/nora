---
type: design
title: "Doctrine audit — what a composed prompt could select, unit by unit"
description: "Source audit of the tag-turn doctrine against Nora's current capabilities: how it renders, stale and missing text, duplicates, a proposed core plus conditional units with sizes, and what per-turn selection can and cannot buy."
tags: [nora, context-plan, prompt-composition, doctrine, audit]
timestamp: '2026-10-12'
edges:
  - {dst: 09-what-a-turn-carries.md, rel: constrained_by, provenance: asserted}
---

> `pipeline.py` below is `nora/engagement/pipeline.py` throughout.
>
> Source audit, not a measurement: token sizes are character estimates using
> node 09's fit; nothing here was evaluated against the model.

# Doctrine audit: what a composed prompt could select, unit by unit

## Method

- Tree: worktree `nora-wt-prompt-composition-measure`, HEAD `e7679e4d4` (origin/master, 2026-10-12). Read-only.
- Rendering path read from source: `nora/doctrine/loader.py`, every file's frontmatter, each call site of `DoctrineStore.get`, `nora/app/agent_runner.py`, `nora/agent/factory.py`, `nora/agent/prefill.py`, `nora/domain/block_types.py`.
- Capabilities read from `nora/agent/tools.py` (`NATIVE_TOOLS`), the catalogue as rendered by `ToolRegistry().tool_catalogue()` and each tool's full description from `ToolRegistry().catalog()` (run in the worktree venv), `nora/skills/*/SKILL.md`, and the block type registry.
- Every tag-audience doctrine file read in full (`core/identity.md`, `core/constraints.md`, `rooms/belonging.md`, `rooms/voice.md`, `rooms/silence.md`, `rooms/conduct.md`, `rooms/pictures.md`). A grep across doctrine for capability words was used to find candidates, then the text was read; no verdict below rests on a grep alone.
- Bucket per rule (law / practice / character) is taken from `nora/doctrine/classification.yaml`, loaded through `DoctrineStore.rules()` rather than re-judged here.
- Token estimates use 0.26 tokens per non-CJK character and 0.91 per CJK character (the node 09 fit). Per-file estimates match the live medians within 5% (conduct 4,094 est vs 3,906 measured; voice 2,172 vs 2,180; constraints 934 vs 948; belonging 730 vs 740; identity 549 vs 557; pictures 500 vs 505; silence 289 vs 292), so the per-paragraph numbers are good to about that margin. Conduct is over-estimated by about 5%.
- Not done: no live trace read, no eval, no measurement of which turns needed which unit. Conditions below are proposed from the text and the code that could decide them. None of them has been measured.

## How doctrine is rendered today

**One store, filtered by audience, with no per-turn selection.**

- `DoctrineStore._scan` reads `**/*.md` under `nora/doctrine/` (loader.py:817-856), orders by the frontmatter `order` and refuses duplicate names or orders.
- `DoctrineStore.get(applies_to=...)` (loader.py:912-933) keeps every section whose `applies_to` names the audience or `always`, renders each through `render_section` as `<section kind=".." name=".." description="..">body</section>` (loader.py:338-380), wraps them in `<nora_doctrine>` (`_assemble`, loader.py:383-390) and appends a `<runtime_context>` (display name, trigger, language hint, canary; loader.py:881-899). The result is cached per audience string (loader.py:914-916). That is the whole selection mechanism. Nothing inside it varies by turn.
- Closed audience vocabulary (loader.py:106-109): `tag, heartbeat, participation, proactive, relevance_gate, dreamer, reaction, summons, speech_format`.

**What reaches the tag-turn system prompt** (`nora/engagement/pipeline.py:640` `get(applies_to="tag")` → `agent_runner.py:192` `system_prompt=doctrine.text`), in `order`:

| order | file | name | kind | applies_to |
|---|---|---|---|---|
| 10 | core/identity.md | identity | identity | tag, heartbeat, participation, proactive, summons |
| 12 | rooms/belonging.md | belonging | identity | tag, heartbeat, participation, proactive |
| 20 | rooms/voice.md | voice | identity | tag, heartbeat, participation, proactive |
| 25 | rooms/silence.md | silence | conduct | tag, participation, proactive |
| 30 | rooms/conduct.md | conduct | conduct | tag, heartbeat, participation, proactive |
| 38 | rooms/pictures.md | pictures | constraint | tag, heartbeat, participation, proactive |
| 40 | core/constraints.md | constraints | constraint | tag, heartbeat, participation, proactive, summons |

Then `runtime_context`. The AgentSkills plugin injects `<available_skills>` into the system prompt before each invocation (factory.py:369-402). The per-turn message opens with `<context_header>` (`prefill.py:329`, static by design for the prefix cache), then the `_BLOCKS` table (prefill.py:122 onward): `self` (1), `lessons` (2), `tool_catalogue` (3, rendered only when `defer_tools` is on), `room_members` (4), `presence` (5), and the rest.

**pictures is not conditional.** It renders on every tag turn. The 180/200 in node 09 is a date effect: `rooms/pictures.md` was created on 2026-09-26 (`d2d705a72`, `50a1658ff`), and the 200-turn window opens 2026-09-25.

**What goes elsewhere:**

| file | audience | consumer |
|---|---|---|
| rooms/participation-conduct.md | participation | offered-floor turns, `fire.py:2539` |
| instruments/participation.md | relevance_gate | the gate's own call, `ingest/relevance_gate.py:830` |
| instruments/reaction.md | reaction | GIF picker, `agent/reaction.py:212` |
| instruments/speech-format.md | speech_format | TTS pacing pass, `agent/speech_format.py:139` |
| public/{voice,conduct,constraints}.md (+ core/) | summons | public Instagram summons, `app/summons.py:485` |
| (rooms + core, minus silence) | heartbeat | reminder fires, `fire.py:1780` |
| nora/dreamer/doctrine/dreamer.md + preludes + objectives | dreamer, cognition | `agent/dreamer.py:1785`, `app/cognition_session.py:967` (`doctrine_text + skill_text`); none of the room doctrine |
| nora/subagent/doctrine/subagent.md + core/constraints.md | subagent worker | `app/subagent_runner.py:99-107`, `app/subagent_wiring.py:29-30` |

The instruments are other model calls. They are not part of the tag prompt.

**What is already conditional per turn (outside doctrine):**
- The tool surface: room scope (`allowed_mcp`), deferral with `HOT_TOOLS` resident (tools.py:2056), and the voice occasion keeping `hot_on={"voice"}` tools resident (`turn_occasion`, pipeline.py:743; registry.py:1161-1265).
- Tools withheld per deployment when their backend is not wired: computer, browser, documents, subagents, video, memos and others (registry.py:298-309).
- Context blocks render only when filled (prefill `_BLOCKS`; node 09 shows 13/200 to 200/200 presence).
- `forced_first_tool=route_intent(msg.body)` (pipeline.py:707) is the only per-turn classifier over the message today.

**Precedent for composition:** the summons audience already composes core plus a surface set (`core/` + `public/`, no room files). `belonging.md` was split out of identity on 2026-09-25 for exactly that reason (belonging.md:11-13). Composition by audience works and is tested. Composition by turn does not exist.

**Coupled consumers of the assembled text** (risks below): `LeakGuard` builds its haystack from `doctrine.doctrine_text` (`agent/egress.py:173-174`, `guards.py:162`). `doctrine.sha256` is logged per tag (pipeline.py:641-646) and is the deployed doctrine identity in `app/revisions.py:221`. `classification.yaml` pins every part by hash (`tests/architecture/test_doctrine_is_classified.py`).

## Capability inventory

| capability | where it is defined | model-facing guidance lives in |
|---|---|---|
| Tool registry: 87 native tools in source (`NATIVE_TOOLS`, tools.py:643 onward), each with ADR 0015 `level` 1-3. About 54 are admitted live per the operator's count; the rest are withheld by config (registry.py:298-309) | `nora/agent/tools.py`, `nora/agent/registry.py` | the tool's docstring (schema) and its catalogue first line (registry.py:766 `tool_catalogue`) |
| Level 3 (longest descriptions): routine_read, file_fetch, generate_image, video_create, x_/linkedin_/instagram_ read+search, remind, computer_run/read/write/edit/export/import/browse, document_publish, subagent_create, send_reply | tools.py | descriptions carry MUSTs, e.g. generate_image "MUST: load the image-generation skill", subagent_create "MUST: after `started`, tell the room" |
| Resident under deferral: web_search, send_reply, note_read, web_fetch, now, search_memory (`HOT_TOOLS`, tools.py:2056); music verbs on voice turns | tools.py | `<tool_catalogue>` block description (block_types.py:815) |
| Runtime skills: computer-use (4.2k chars), document-creation (2.4k), image-generation (6.9k), navigation (3.2k), video-generation (6.8k) | `nora/skills/*/SKILL.md`; loaded through the `skills` tool (tools.py:2003) | `<available_skills>` (name + when), body on load (ADR 0016) |
| The computer (sbx microVM, ADR 0019): `computer_*` tools, browser (`computer_browse`, ADR 0022) | tools.py:1688 and nearby; withheld without a runtime | tool descriptions, the computer-use skill, the `<computer>` block (block_types.py:735, 75/200 turns) |
| Subagents: `subagent_create/list/read/cancel`, `checkpoint_update` (worker side) | tools.py; `app/subagent_*.py`; the worker's own doctrine `nora/subagent/doctrine/subagent.md` | tool descriptions, the `<subagents>` block (block_types.py:767) |
| Cognition loop and memos: a separate session with its own doctrine (dreamer.md + cognition prelude); the tag side reads results through `<standing>`, `<notes>`, `<memos>`/`memo_read`, `<self>`, `<lessons>` | `app/cognition_session.py`, `app/cognition_wiring.py:500-540`, `nora/dreamer/` | block descriptions (block_types.py:448-465, 637); in doctrine only as belonging.md "Two loops" (L55-61) and "What carries across turns" (L63-69) |
| Image generation | `generate_image` + image-generation skill | tool MUST + skill (appearance, whose photo, privacy) |
| Video generation | `video_create` (withheld unless `[video_gen]`) + video-generation skill | tool MUST + skill |
| Documents (web app) | `document_publish/read/set_status`, ADR 0020 | tool MUSTs, document-creation skill, `<documents>` block (block_types.py:754), and conduct.md:79 |
| Voice: spoken input via `nora/web/voice.py` (`NormalizedMessage.voice=True`, :155); TTS out with the speech-format instrument; music in voice-sidecar | web/voice.py; agent/speech_format.py; music tools | only the tool surface changes (pipeline.py:743). I found no path that tells the main model the turn is spoken: no voice field in `build_tag_message`, `render_transcript` or `framing.py` |
| GIF replies | `search_gifs`/`send_gif` (tools.py:1963), gated on `GIPHY_API_KEY` (composition.py:2894-2897); reaction picker instrument | voice.md "Sticker 同 GIF" (L37-41) plus the tool descriptions |
| Social reads, location, transit, events, reminders, music, memory writes | tools.py | tool descriptions; doctrine touches events, reminders and memory writes (conduct.md:63-69) |

## Section-by-section audit

Needs: **every** = every room turn; **cond(code)** = a condition code can decide before the model runs; **cond(model)** = only a judgement over the message or window can decide it (needs a selector, or stays in the core). Bucket is from classification.yaml.

### core/identity.md (~549)

| unit | lines | est tok | governs | needs | overlap / staleness | bucket |
|---|---|---:|---|---|---|---|
| Who you are | 21-23 | 21 | AI, never a person | every | none | law |
| Where you come from | 25-33 | 107 | origin, never credit the operator | every (cheap; the "who made you" question is not predictable) | none | character + law |
| What you are | 35-38 | 43 | framing for self block | every | refers to `<self>`, which renders on only 36/200 turns | character |
| Your form | 40-44 | 52 | mascot appearance | cond(code): image/video tools admitted, or a self-description question | **duplicates** image-generation skill "What you look like" and video-generation skill L30, both far more detailed | character |
| Where you stop | 46-57 | 212 | source and machine closed; build stamp "you ask"; login-gated portal | every | **stale-adjacent**: "the machine underneath you are closed to you" sits beside a `<computer>` block and tools that say "your own computer". The two can be reconciled (host vs sandbox), but nothing says so. "Which is true here is something you ask" now has a tool, `version` ("This build stamp is the only source"). Documents publish into the portal this section calls a view she cannot go through | law |
| Words for your own parts | 59-63 | 78 | no inference from self-vocabulary | every | none | law |

### core/constraints.md (~934), all law except two practice tails

All seven sections are needed on every turn. They are also served to summons and (constraints only) to the subagent worker. "You reply only where you were called" (L49-58, 191) enumerates offered, due and self-decided turns. Silence repeats the enumeration, but this copy carries the where-to-speak law, and `send_to` (relay) agrees with it. "Other people stay private" (L60-71) is in tension with per-room location trust (DEC-74, `location_trusted`, pipeline.py:670). The tool gate resolves it. The text does not mention it. **No unit here should be conditional.**

### rooms/belonging.md (~730)

| unit | lines | est tok | needs | notes | bucket |
|---|---|---:|---|---|---|
| Your place in the group | 20-30 | 128 | every room turn | L26-30 carry two laws (no cross-room carry; answers to the group, not the operator) | character + law |
| Why you're here | 32-51 | 315 | every room turn | L43-48 "work follows the stakes" is the 3rd copy of a rule also in conduct.md:45 and voice.md:25 | character + practice |
| Two loops | 55-61 | 120 | every | the only doctrine text describing the cognition loop (shape only, deliberately) | character |
| What carries across turns | 63-69 | 115 | every | law: cannot read which halves run | character + law |

### rooms/voice.md (~2,172, about 85% CJK, so the most expensive per character)

| unit | lines | est tok | needs | notes | bucket |
|---|---|---:|---|---|---|
| 你係邊個 | 15-17 | 124 | every | register core | character |
| 香港人點樣打字 | 19-21 | 159 | every | language and code-switching. Assumes typing ("似人喺 Signal 打字"), which is wrong on a spoken turn | character |
| 打字唔係寫文 | 23-29 | 349 | every | short default, punctuation, no dashes or headings. Overlaps conduct "Depth follows the stakes" | practice + character |
| 唔好表演性格 (L33) | 31-33 | 72 | every | | character |
| 叫人個名 (L35) | 35 | 112 | every (cheap, and the trigger is not predictable) | conduct.md:41 "the one place a name is owed" is its counterpart | practice |
| Sticker 同 GIF | 37-41 | 199 | **cond(code)**: GIF tools admitted and reaction on | per memory, GIF replies are off in live config since 09-24. If the tools are not offered, this section steers toward a reply she cannot send (verify live) | practice |
| 粗口 | 43-47 | 221 | cond(model): banter or a request to hit back | L47 quotes two members' requests verbatim, which is room speech inside doctrine (scrub concern, not quoted here) | character |
| 笑位 | 49-57 | 243 | cond(model): banter | L53 "記憶…係背景，唔係彈藥；要解釋先好笑嘅就唔好講" is a near-verbatim Cantonese twin of conduct.md:37 "Shared history is available, not ammunition; if it needs explaining, leave it" | character |
| 有人話你錯 | 59-63 | 206 | cond(model): she is challenged | the 3rd copy of the "wrong once, stand on sourced facts, a tease is not a case" rule (conduct.md:83, 87-95). L61 tail is law (no invented mistake) | practice + law + character |
| 唔似 Nora 嘅聲音 | 65-81 | 386 | every | anti-assistant tells. L71 closing offer overlaps conduct.md:103; L77 overlaps conduct.md:55 ("reads like a file") | character + practice |
| 一個 message 嘅感覺 | 83-89 | 53 | every | | character + practice |

### rooms/silence.md (~289)

L22-24 "called, you answer" plus L38-40 "say your piece, do not speak again" are needed on every tag turn (~110 tok). L26-36 (offered floor; decided earlier) apply only to participation and proactive turns (**cond(code)**: the audience or occasion, ~140 tok). The heartbeat audience already excludes the file. On a tag turn those paragraphs describe turns that are not this one.

### rooms/conduct.md (~4,094; 15 sections)

| section | lines | est tok | governs | needs | duplicates / staleness | bucket |
|---|---|---:|---|---|---|---|
| You have judgement | 19-21 | 142 | answer the need; no false agreement | every | | character + practice + law |
| Follow the conversation, para 1 | 25 | 113 | next line, not a new ticket; settled stays settled | every | voice.md:25, 69 (no restating) | practice |
| Follow the conversation, para 2 | 27 | 158 | do not read a picture's description back | **cond(code)**: a media code in the window or the message, or a shared post or link | **duplicates** pictures.md:24-26 | practice |
| Follow the conversation, para 3 | 29 | 237 | one reply; each extra message earns its place; no filler; heads-up only for a long wait | every | send_reply description covers mechanics (`more`, `heads_up`), not the judgement | practice |
| When the group is sorting something out | 31-33 | 162 | help a plan land; no chasing; tag sparingly | cond(model): planning; partly code-hintable (open `<notes>`/events) | belonging.md:36-39 | practice |
| Banter | 35-37 | 190 | give it back; no invented quotes; no absent target | cond(model): banter. **Lift** L37's last two sentences (law, ~40 tok) to the core | voice.md:53; constraints.md:71 (absent target) | character + law |
| Being asked to judge | 39-41 | 227 | tell the loser, tagged; find it before you say it | cond(model): asked to judge | the law part repeats Banter + `who_said` ("Call this BEFORE you put words in somebody's mouth") | practice + law |
| Depth follows the stakes | 43-45 | 146 | depth by stakes; short default | every | 3rd copy (belonging, voice) | practice |
| Getting to an answer | 47-51 | 256 | check, don't guess; batch checks; heads-up; empty vs broken | every | the heads-up rule pairs with send_reply `heads_up`; long work is now often `subagent_create` (not mentioned) | law + practice |
| Talking about a person, para 1-2 | 55-57 | 207 | memory first; never "don't know them" unread; tag only reaches this room | every (most turns touch a member) | voice.md:77 | practice + law |
| Talking about a person, para 3 | 59 | 173 | recommendations built on memory; no invented taste | every; model-judged otherwise. **Moved in from a skill on 2026-09-24 because of reach** (`3caff8ab6`) | | practice + law |
| Talking about a person, para 4 | 61 | 293 | research method: intent, one or two people deep, layered queries | cond(model): a person-claiming answer worth breaking down | the same reach precedent | practice |
| Writing something down, para 1 | 65 | 124 | no writes on unopened turns | **cond(code)**: heartbeat, proactive or participation turns (the tools refuse anyway) | write tools refuse by code | law |
| Writing something down, para 2-3 | 67-69 | 384 | 記低 is the write; group facts kept after the turn; which record | every (a 記低 can come on any turn) | heavy overlap with the `remember` description ("The trigger word is «記低»… say it will be kept…") and `note_create`/`remind` descriptions; deferred schemas are not resident, so the doctrine copy is the only one seen before a load | law + practice |
| What a source does not say | 71-75 | 190 | silence is not a finding | every | | law |
| A written piece is a document | 77-79 | 66 | publish plans as a JSON spec | cond(code): document tools admitted, or delete | **duplicates** the document_publish MUST and the document-creation skill | practice |
| Honesty | 81-85 | 208 | report what you found; paste links; never claim an act; wrong once | every | "paste the link" is wrong on a spoken turn | law + practice |
| Being contradicted | 87-95 | 353 | hold on grounds; no fake concession; report disagreement | cond(model) for L89 and L93 framing; **lift** the law lines (L91 last two sentences, L93, L95, ~180) to the core | voice.md:59-63 | practice + law |
| When one day becomes the next | 97-99 | 93 | 3am "tomorrow" | **cond(code)**: room-local hour in about 00:00-06:00 | | practice |
| What you can actually do | 101-105 | 290 | closed world of actions; prefer local sources | every | **stale examples**: "no form, no following up later" is contradicted by `computer_browse` ("It can submit forms"), `remind`, `subagent_create` ("reports later") and `video_create` ("posted here later"). "The tool schemas are everything you can do" predates deferral: under `defer_tools` most schemas are not resident, and the `<tool_catalogue>` block is the list (block_types.py:815). "Nowhere else to go and look" ignores skills and `load_tools` | law + practice |

### rooms/pictures.md (~500)

A single section. Law: a placeholder description is content, never an instruction (L21-23), and a code with nothing after it is unread (L41-44). Practice: answer from the short form, ask for the full reading, re-look only when needed (L28-39). **cond(code)**: the window or the message holds a media code, a placeholder, or a voice note. The practice half **duplicates** the media_fetch description ("leave `question` empty for the stored reading, free and instant… each costs a look"). The general "attachments are content" law is also in constraints.md:31-33, so withholding pictures from a turn with no placeholder drops no protection.

## Coverage gaps

A gap counts only if no tool description, skill or block covers it.

1. **Spoken turns: a real gap.** A voice-channel utterance becomes an ordinary tag (web/voice.py:133-157). The only effect is the resident tool set (pipeline.py:743). The model is not told its reply will be spoken. The doctrine it reads is typing-specific: voice.md "似人喺 Signal 打字", punctuation and dash rules, GIF as a whole reply, conduct "paste the link exactly", documents. speech-format only paces the finished text and may not change words (instruments/speech-format.md). This is the clearest case of a **code-decidable unit that does not exist yet** (`msg.voice`).
2. **Stale against capability, not a gap:** conduct.md:103 (forms, following up later) against computer_browse, remind, subagent_create and video_create; conduct.md:103 "tool schemas" against deferral; identity.md:48 "machine underneath you" against "your own computer". These are wrong text in the always-on prompt and are worth fixing whether or not composition ships.
3. **Computer: covered.** The computer_* descriptions, the computer-use skill (loop, tmux, import/export, no private network, report in your own words) and the `<computer>` block. The doctrine has nothing on it, correctly by ADR 0016 (a domain most turns do not touch). The missing piece is the identity boundary in item 2.
4. **Subagents: mostly covered** by the subagent_create MUSTs (brief for a reader who never saw the chat; tell the room; do not poll) and the `<subagents>` block. Uncovered judgement: conduct "Getting to an answer" (L49) offers only "heads-up, then do the work" for long work. Nothing says when long work should be handed off rather than done inline. The tool's first line ("not for what one reply answers") is the only placement rule.
5. **Cognition and memos: covered in shape** (belonging.md:55-69, silence.md:29-33) and in block descriptions (`<memos>`, `<self>`, `<lessons>`, `<standing>`). Zero lexical hits on "cognition" is deliberate: identity's `not_here` forbids instance names.
6. **Image and video generation: covered** by tool MUSTs and two long skills (appearance, whose photo, privacy of the prompt, no "today" in prompts). Doctrine-level judgement not covered anywhere: when a picture is the right answer unasked, or as a proactive act. The skills' "never a default" rules apply only once loaded.
7. **Documents: over-covered** (conduct.md:79, the tool MUST, the skill, the block).
8. **GIF: possibly covering a capability that is off** (voice.md:37-41 vs live `[reaction]`). Verify live.
9. **Browser logins (ADR 0022) and `needs_human`:** covered by the computer_browse description. No doctrine rule says acting on a member's behalf on a logged-in site needs their ask. That is a trust-boundary candidate for constraints once logins ship, which ADR 0022 says has not happened.

## Proposed units

Order in the prompt: **core first and byte-stable** (system prompt), then `runtime_context` and `<available_skills>` (stable per deployment), then conditional units. Conditional units go in fixed order, most stable first (room-keyed, then turn-keyed), so a turn's prefix diverges as late as possible.

| unit | source | condition | est tok | safety-critical |
|---|---|---|---:|---|
| **K1 identity** | identity.md all (Your form optional, see Q6) | always | 549 | y |
| **K2 constraints** | constraints.md all | always | 934 | y |
| **K3 belonging** | belonging.md all | always (room audiences) | 730 | y (L26-30, L68-69) |
| **K4 voice core** | voice.md 你係邊個, 香港人點樣打字, 打字唔係寫文, L33, L35, 唔似 Nora 嘅聲音, 一個 message 嘅感覺 | always | ~1,255 | n |
| **K5 silence (called)** | silence.md L22-24, L38-40 | always on tag | ~110 | n |
| **K6 conduct core** | conduct judgement, L25, L29, depth, getting to an answer, person L55-59, writes L67-69, source, honesty, can-do (examples fixed), plus lifted law lines from Banter (L37), Judge (L41 "find it") and Contradicted (L91-95) | always | ~2,950 | y (the law parts) |
| C1 media | pictures.md + conduct L27 | a media code, placeholder or voice note in window/message, or a shared post link; **fail closed: include when detection errors** | ~660 | y (placeholder law) |
| C2 unasked turn | silence L26-36 + conduct L65 | audience is participation, proactive or heartbeat (code) | ~265 | y (no write claims) |
| C3 night | conduct L97-99 | room-local hour 00:00-06:00 (code) | ~95 | n |
| C4 GIF | voice.md L37-41 | GIF tools admitted and reaction on (code, per room) | ~200 | n |
| C5 documents | conduct L77-79 | document tools admitted (code), or delete as a duplicate | ~65 | n |
| C6 spoken (new) | to be written: reply is heard; no links, lists or GIFs; short | `msg.voice` (code) | ~150 (est) | n |
| M1 banter | conduct Banter (minus law), voice 粗口, 笑位 | model-judged | ~600 | n (after the law lift) |
| M2 dispute | conduct Contradicted L89 framing, voice 有人話你錯 | model-judged | ~330 | n (after the lift) |
| M3 judging | conduct Being asked to judge (minus "find it") | model-judged | ~190 | n |
| M4 planning | conduct sorting-out | model-judged; hint: open notes/events | ~160 | n |
| M5 person research | conduct L61 | model-judged; **reach precedent says keep always-on** | ~295 | n |

## Core size estimate

- **K1-K6 as above: about 6,500 tokens**, against about 9,100 today. Code-decidable units add 0-1,400 on a turn. Model-judged units, if left in the core, add about 1,575 back (M1-M5), giving about 8,100.
- So **composition with code-decidable conditions alone saves about 1.0-1.4k tokens on a weather-type turn** (no media, daytime, called, GIF and documents not in play). That is about 5-7% of a 20.8k first call. The rest of the operator's 9k figure is not removable by selection without either:
  - (a) a model selector for M1-M5. Nodes 06-08 found a per-turn model selector cost more than it saved on four blocks, and it would decide units that carry judgement; or
  - (b) the self-growth step 5 cut (design/self-growth/30-doctrine-sort.md §4): practice moves to Lessons, character to Character · Nora and the `<self>` block. The **law-only floor**, measured from classification.yaml for the tag audience, is **about 2,890 tokens** (character 2,230, practice 3,490). A law core plus minimal voice and character (about 720 + 560) is **about 4.2k**.
- Prefix cache: today the whole doctrine sits in the cached head (node 09: 123/200 first calls hit cache, median 11.8k cached). With k independent binary units after the core there are up to 2^k distinct prefixes. The core alone stays cached. Each unit costs cache only from its own position onward. Cutting cached tokens saves little money (node 09; ADR 0016 cost test), so the case for composition is attention and latency, not cost. That case is unmeasured.

## Risks

1. **Restatement that is load-bearing.** The triple copies (stakes/depth ×3, being wrong ×3, ammunition ×2, closing offers ×2) look removable, but voice.md is a register specimen in Cantonese and conduct is the English rule (memory: "demonstration beats instruction"; "compression deleted 8 rules once"). Each duplicate needs a falsification audit and the eval before merging. A diff review is not enough.
2. **Law lines embedded in conditional-looking sections.** "Do not invent quotes", "find it before you say it", "do not fake a concession", "two people disagree", "never invent a preference" sit inside banter, judge, dispute and person units. classification.yaml already marks them `law`, so the lift can be mechanical by bucket, but it has to happen **before** any unit goes conditional.
3. **Never conditional:** all of constraints.md; identity "AI, never a person", "Where you stop", "words for your parts"; belonging cross-room and "answers to the group"; conduct false agreement, check-don't-guess, empty vs broken, never claim a write or an act, closed world of actions, a joke doesn't change a fact, source silence; the pictures placeholder law **whenever a placeholder is present** (C1 fails closed).
4. **Reach regression.** Personalization was a conditional skill and was moved into the always-on doctrine on 2026-09-24 because conditional judgement went unread (`3caff8ab6`). Composition by code avoids the model's load decision only where the condition is decidable. M1-M5 are not.
5. **Coupled consumers.** `LeakGuard` (egress.py:173) and `Doctrine.sha256` (pipeline.py:641, revisions.py:221) assume one doctrine per audience. Per-turn composition needs a per-composition `Doctrine` and cache key (loader.py:914). `Doctrine.versions()` and eval arms need a composition id to stay comparable. Splitting files changes headings, which fails `test_doctrine_is_classified.py` until re-sorted.
6. **Other audiences.** heartbeat, participation and proactive read the same files (fire.py:1780, 2539). A unit split must define their selection too, or a fire turn loses rules silently. An audience that resolves to nothing serves no persona (fire.py:58-63 docstring).
7. **Lessons and self assume the doctrine above them** (block_types.py:448-465: "the doctrine above it is the law and wins"). A lesson learned against a practice rule that is absent this turn has nothing to be checked against.
8. **Fail-closed detection.** A code condition (media present, voice, hour) must treat a detection error as "include". An omitted law unit errs silently: the model answers anyway.
9. **Evaluation.** A composition changes many turns at once. The 40-case suite plus a replay is needed, and one example decides nothing (CMS). Prove the arms differ (read the composed prompt back) before spending model calls.

## Open questions for the operator

1. Should the composed core be defined as **law + minimal voice + character**, so composition and self-growth step 5 (the cut) converge on one core, or should composition proceed independently on today's text?
2. Model-judged units (banter, dispute, judging, planning, person research, about 1.6k): keep them always-on, or accept a selector? Nodes 06-08 argue against a per-turn model selector.
3. Fix the stale text now, independent of composition? conduct.md:103 ("no form, no following up later"; "tool schemas are everything") and identity.md:48 ("the machine underneath you" vs her own computer).
4. Spoken turns: should the main model be told a turn is spoken (`msg.voice`), with a short spoken-surface unit, as summons has `public/`?
5. Are GIF replies live? If `[reaction]` is off or `search_gifs` is not admitted, voice.md L37-41 should not render.
6. "Your form" (identity.md:40-44): keep it in the core for "what do you look like", or leave it to the image and video skills?
7. On the deployed route, where do tool schemas fall in the cached prefix relative to the system prompt? This decides whether conditional units can sit at the end of the system prompt or must move to the head of the per-turn message.
8. Delete conduct.md:77-79 (documents) as a duplicate of the document_publish MUST and the skill, or keep it as the one copy seen before a deferred schema loads?
