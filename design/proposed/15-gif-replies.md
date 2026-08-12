---
kind: design
title: "Nora — GIF replies: sending something she cannot read"
description: "Giphy as a second reply verb: how the choice is made, how selection works, where tone policy lives, and the guard that pixels defeat."
tags: [nora, design, giphy, gif, stickers, egress, guards, voice]
timestamp: '2026-08-06'
status: proposed
---

# GIF replies

A GIF is the first thing Nora would send that **she cannot read and no guard
can inspect**. Everything she emits today is text: it carries the canary
(R-6), it passes R-18's predicate, and if it were wrong we could see that it
was wrong by looking at it. A GIF is bytes. R-18 matches patterns in strings;
it has no opinion about pixels, and a GIF whose frames contain text can say
anything at all.

That is the design problem. The Giphy integration itself is small.

## 0. Feasibility, stated honestly

Two things are confirmed and one is not.

**Giphy works.** Verified 2026-08-06 against the operator's key:
`GET /v1/gifs/search?q=side+eye&limit=2&rating=pg` returns 500 results, each
carrying `rating` and a set of renditions. **Rendition size varies by ~50×**
for the same query — 33KB for one result's `original`, 1,661KB for the next —
so a size-bounded rendition (`downsized`, `fixed_height`) is mandatory and
`original` must never be used.

**Signal stickers proper are not this feature.** A Signal sticker is an
identifier into an *installed sticker pack*, not an arbitrary image. Sending
one requires the pack to exist and be installed. Sending a Giphy GIF means
sending an **attachment** that Signal renders inline — which is how GIFs are
actually sent on Signal. This document is about attachments; the word
"sticker" in the request maps to that.

**signal-cli attachments work — verified 2026-08-06, signal-cli 0.14.7.** A
1×1 GIF sent as `data:image/gif;filename=probe.gif;base64,…` to Nora's own
number (note-to-self, never the group) returned `type: SUCCESS` with a real
timestamp.

The positive result is only meaningful because of the negative controls: a
malformed base64 payload and a nonexistent file path **both errored** with
`AttachmentInvalidException`. A signal-cli that silently ignored an unknown
`attachments` param would have returned SUCCESS for all three, which is
exactly the "fake answers every request the same way" failure `CLAUDE.md`
records three instances of. It did not; the param is genuinely processed.

Two implementation constraints fell out of the failure text:

- signal-cli **resolves the data URI as a file path when base64 decoding
  fails** — the malformed-URI error was `No such file or directory`, naming
  the whole URI. So bad-payload and missing-file are **indistinguishable by
  error message**. The tool must therefore fall back to text on *any*
  attachment failure rather than branching on why; there is no reliable
  classification to branch on.
- Because that path exists, an attachment string is one bad input away from
  being a file read. Nora builds the data URI **internally** from fetched
  bytes and never accepts a caller- or model-supplied attachment string.

There is also a port change: `nora/platform/signal/adapter.py::send` is
`(target, text) -> SendResult`. It has no attachment concept, and neither does
the domain port. That is a deliberate, reviewable widening of the narrowest
interface Nora has.

## 1. How she decides text or GIF

**Two terminal tools; the model picks.** `send_reply(text)` and
`search_gifs(query)` followed by `send_gif(candidate_id, caption)`. No classifier in front, for the reasons
`design/14` §1 gives at length: choosing among declared capabilities is what
a function-calling model does, its taxonomy is derived from the tools and
cannot drift, and the one measured mis-selection this deployment has seen was
fixed by `reasoning_effort="low"`, not by a router.

The mechanical consequence is the part that will break if it is missed.
`send_reply` and `send_gif` are **terminal reply verbs**. A successful GIF-only
send counts as a reply: "must send a reply" means Nora must perform a visible
outbound act, not that the act must contain text. A plain question normally
gets one terminal act; existing multi-message policy may permit more, bounded
by the same per-turn message cap. `send_gif` must be terminal *in exactly the
same way*, or two failures follow immediately:

- she sends a GIF and the loop continues, so she sends a text reply too —
  R-2 (one reply per tag) violated by construction; or
- the guard treats only `send_reply` as terminal and a GIF-only turn runs to
  the turn limit before ending, paying full latency for a joke.

So the guard's notion of "she has replied" becomes a **class of reply verbs**,
not one tool name. Adding a third later (a voice note, a poll) means adding it
to that class, and the class must be defined once, in the guard, rather than
re-listed at each call site.

**Both in one message, not two.** A GIF with a caption is one Signal message
with an attachment and a body — `send_gif(candidate_id, caption="又係你")` is a
single send. This is why `caption` belongs on `send_gif` rather than being
achieved by calling both tools: two calls would be two messages and two
notifications, which is not what a person does.

The three deliberate shapes are therefore:

- `send_reply(text)`: text only.
- `send_gif(candidate_id)`: GIF only. This is ordinary and allowed.
- `send_gif(candidate_id, caption)`: one message containing both.

When the model has a real two-beat response, it may call `send_reply(text)`
then `send_gif(candidate_id)` under the ordinary multi-message cap. That is a choice
for the voice policy, not a workaround for a missing caption field. It must
not split one ordinary answer into text plus a decorative GIF by default.

### 1.1 Multi-message replies (operator intent, 2026-08-06 — needs its own document)

The operator wants Nora to answer in **two, three or four separate messages**
rather than one, so a GIF can land on its own or alongside text. That is how
people actually text, and it makes §1's "both in one message" resolution
obsolete.

It is recorded here because it changes this feature's shape, but it **is not a
GIF change** — it relaxes R-2 (one reply per tag) for every reply Nora sends,
so it needs its own design document and its own decision-index entry.

The crux, and it is not obvious:

**`tool_choice="required"` means the model can never stop on its own.** It is
forced to call a tool on every pass, which is precisely why `send_reply` is
terminal via `ReplyGuard` today — the guard cancelling the loop is the ONLY
thing that ends a turn. Make `send_reply` non-terminal and the loop cannot
terminate naturally; it runs to the turn limit, sending a message each pass.
Every turn would produce the maximum number of messages.

So a message *cap* alone does not work — it would become a message *quota*,
always spent in full. Something has to let her stop.

The cleanest resolution available: **force the first call, then relax.**
`tool_choice="required"` until the first reply lands — which is what
guarantees she answers at all, and the reason it is set — then
`tool_choice="auto"` for the remainder of the turn, letting her stop by simply
not calling another tool. The cap survives as a backstop against a model that
will not stop, not as the primary mechanism.

**Measured 2026-08-06 on `opencode-zen/deepseek-v4-flash`,
`reasoning_effort="low"`. The design holds, and the ORDER is load-bearing:**

    A. auto, fresh question          plain_text 5/6, called_tool 1/6
    B. auto, already replied         STOPPED 8/8
    C. required, already replied     kept_going 6/6          (control)
    D. auto throughout, invited      0 messages sent 6/6

    PROPOSAL — required until first reply, then auto:
       neutral prompt                1 message  6/6
       invited to multi-send         3 msg ×1, 4 msg ×3, 5 msg ×2

**A and D are the finding that matters.** Under `auto` on the *first* call she
answers in **plain text** 5/6 — no tool call, so no message is ever sent and
she is silent in the group while appearing to have worked. D is the same
failure end to end: zero messages, 6/6. Relaxing `tool_choice` globally would
therefore reproduce R-5's exact failure shape. `required` on the first call is
not a preference; it is what makes her speak at all, and this is the
measurement that says so.

**B is the permission.** Once a reply is in history, `auto` lets her stop
cleanly, 8/8 — against C's control showing `required` can never stop, 6/6. So
the loop terminates naturally rather than by cap.

**The message count is a VOICE decision, not a mechanism one.** Same
mechanism, two prompts: neutral gives exactly one message 6/6 (today's
behaviour, no regression), and an invitation to chat like a person gives 3–5.
`voice.md` therefore controls how talkative she is, which is the right home
for it — the mechanism only makes multi-message *possible*.

**The cap is still required.** Two of six runs hit the probe's ceiling of five
hops, so she does not always stop on her own within a sensible number. Five
messages for 「講下你自己」 is also more than a person sends — a tuning
problem for `voice.md`, with the cap as the backstop that keeps a bad prompt
from flooding the group.

What else moves with R-2:

- **R-6's canary and R-18** run per message, not per turn.
- **A9 record-first** applies to each message — a multi-message reply that
  records one row leaves a hole in both audit and Honcho.
- **The 👀 → ✅ lifecycle** resolves once per *tag*, not per message, so
  `indicate.resolve` moves to after the last message rather than the first.
- **`_reply_turns`-style tests** and every "exactly one reply" assertion in
  the suite encode R-2 directly and will need rewriting — they are the
  specification of the current rule, so changing them IS the change.

## 2. Lookup and selection

**The model writes the query, then chooses from described candidates.**
`search_gifs(query="眼碌碌 side eye")` returns a bounded list of Giphy ids with
their `title`, `alt_text`, available tags, and rating. The model chooses one of
those ids with `send_gif(candidate_id, caption)`. It never sends the first
search result merely because it passed a rating and size filter.

This first version deliberately does **not** use vision. DeepSeek cannot read
pixels, and adding a vision model plus multi-frame sampling costs a second
provider, latency, and money for a reaction image. The model therefore knows
only Giphy's textual metadata, not the GIF's pixels. That limitation is stated
in the tool result and is preferable to pretending a query establishes what an
unseen GIF contains.

Selection, in order:

1. `rating` capped at `pg` in the request **and** re-checked on each result —
   the parameter is Giphy's promise, the field is Giphy's claim, and neither
   is our judgment (see §4).
2. Candidates with no title, alt text, or tags are excluded: there is nothing
   for Nora to evaluate. Tags are optional provider metadata, never assumed.
3. The selected candidate is fetched again and must match the candidate's id
   and metadata exactly. The bytes come only from Giphy's `media*.giphy.com`
   CDN, through a bounded `downsized` rendition; redirects, untrusted hosts,
   oversized content-lengths, and streams beyond the ceiling are refused.
4. No acceptable result, fetch failure, or transport failure → **fall back to
   text, do not fail the turn** (R-5). Search returns a legible outcome; a
   failed terminal send raises a tool error so `ReplyGuard` does not consume
   its terminal budget and the next model pass can call `send_reply`.

`NATIVE_TOOL_BUDGETS` gives both `search_gifs` and `send_gif` a budget of one.
The search is non-terminal; sending is terminal. R-7 vetoes either otherwise.

## 3. Tone — sarcastic, teasing, loving, doubting

**No `emotion` parameter.** An `emotion: Literal["sarcastic", "teasing",
"loving", "doubting"]` argument is DEC-24's inert-parameter trap wearing new
clothes: it forces the model to produce a classification, which it must then
reconcile with the query text it also wrote, and the two can disagree. It also
freezes an emotional taxonomy in a schema — and the register that actually
matters here (氹, 寸, 串, 陰陽怪氣) does not survive being flattened into four
English enum values.

The model already encodes tone *in the query*. "side eye", "眼碌碌",
"eye roll", "aww hug" — that is what a GIF search query IS. Adding a parallel
channel for the same information creates a contradiction to resolve, not a
capability.

Tone policy splits across two homes, along `design/12` §1's rule:

**`voice.md` — when a GIF is in register at all.** This is needed on every
turn, so it is prompt, not tool. It is also the half that decides whether the
feature is charming or exhausting, and it is a *doctrine* judgment the operator
should write rather than one I should invent. The questions it must answer:
does she GIF a serious question (no), does she GIF when someone is upset
(almost never, and never instead of words), how often is too often, and does a
GIF ever stand alone without a caption.

**The `send_gif` docstring — how to search well.** Selection policy lives in
the schema the model actually reads (`extract_metadata` derives description
and per-parameter text from it), not in another prose block in the prompt.
That keeps it from drifting away from the tool the way a prompt catalogue can.

The register itself — sarcastic, teasing, loving, doubting — is already
`identity.md` and `voice.md`'s job and needs no new mechanism. A GIF is a way of
speaking in a register she already has, not a new register.

## 4. What no guard can see

Stated plainly, because it is an accepted risk and not a solved problem:

**R-18 cannot inspect a GIF.** It matches phone numbers, ACIs, key-shaped
strings and model names in *text*. A GIF is opaque to it. If a GIF's frames
contain text — and reaction GIFs very often do — that text reaches the group
having passed no guard at all. Nothing in this design changes that, and no
realistic amount of engineering would: frame OCR on every send is neither
reliable nor affordable, and an unreliable filter we trusted would be exactly
the decorative gate this repo keeps finding.

### 4.1 Should she read the GIF first?

Yes — **read the words, not the pixels.**

Giphy returns `title` and `alt_text` for some results. This metadata lets the
model compare bounded candidate descriptions before choosing, but it is
**untrusted provider text**, not a pixel-level claim and not text that may be
promoted into Nora's authored memory. The result labels this explicitly.

Reading the *pixels* is a different proposition and the answer is not yet:

- **The model cannot.** DeepSeek-V4-Flash is a text model. Vision means a
  second model, a second provider dependency, and another round trip added to
  a joke.
- **The coverage is partial in a way that reads as complete.** Giphy's `still`
  renditions are ONE frame. Reaction GIFs routinely put their text on the
  *last* frame — the punchline caption. A still-frame vision check would pass
  a GIF whose final frame says anything at all, while producing a log line
  that looks like the content was verified. That is precisely the decorative
  gate this repo keeps finding: `CLAUDE.md`'s rule is that if reverting the
  check does not turn something red, the check is decoration.

If pixel-reading is ever added, it must either sample across frames or be
named honestly as a first-frame check — never described as "Nora looked at
the GIF".

So: metadata filtering is in scope now; frame inspection stays out, and §4's
residual risk is the frame-text hole that metadata cannot close.

What we actually have:

- **`title`, `alt_text`, and tags**, bounded provider metadata for a model
  choice, not a deterministic safety filter and not a pixel inspection.
- **Giphy's `rating`**, capped at `pg`. This is a third party's content
  judgment, applied to a third party's corpus. It is the primary control and
  it is not ours.
- **A small, private group.** Six known people, deny-by-default policy. The
  blast radius of a bad GIF is a group chat among friends, not a public
  surface. This is what makes the residual risk acceptable — and it is the
  reason this design would need revisiting before Nora ever joined a room
  that was not this one.

**There is no canary problem — corrected 2026-08-06.** An earlier draft of
this section claimed R-6's canary "rides in text", so a caption-less GIF would
leave with none, and treated that as the design's one blocking decision. That
is backwards. `LeakGuard.check` (`nora/agent/egress.py`) reads:

    if not text:
        return None
    if self._canary and self._canary in text:
        return "canary"

The canary is a **tripwire planted in the doctrine**, not a watermark she
emits: it fires when she regurgitates her own system prompt. Empty text returns
`None` immediately — there is nothing to leak and nothing to check, and a GIF
cannot leak the doctrine. R-18 is the same shape: no text, nothing to scan.

So captioned and caption-less differ in only two ways, and neither is a
guardrail:

- **Memory (A9).** A caption-less GIF is recorded as `[GIF via giphy]` with
  provider and candidate id in audit metadata, but is not fed as authored
  textual self-memory. A provider description must not become Nora's words.
- **Feel.** Replying with just a GIF is ordinary human behaviour, and forcing a
  caption every time is the stiffness `voice.md` spends a section warning
  against.

**Resolution: caption-less is allowed**, with an explicit media marker and
provenance in the audit row. The frame-text hole above is unaffected either way
— a caption never made the pixels scannable.

**Outbound egress is deliberately narrow.** The search query leaves the host.
It is length-bounded and identifier-screened before the call, but that cannot
prove a natural-language phrase is not private. The tool schema therefore
requires a generic visual reaction phrase and explicitly tells the model that
the query reaches Giphy. This is a residual privacy limitation, not a claim
that identifier screening can solve content classification.

## 5. Record-first

A9 says record first. A GIF reply must not leave a hole where a reply was.

- **Audit**: provider, resolved GIF id, media type, and a visible
  `[GIF via giphy]` marker (plus any caption), never the bytes or the private
  search query.
- **Honcho**: a caption-less GIF is not fed as authored text. A captioned GIF
  contributes only its caption; the audit row preserves the media provenance.
- The inbound side already has this shape — `nora/platform/signal/parser.py`
  renders incoming attachments as `[attachment: …]` and stickers as
  `[sticker]`, so the transcript convention exists and outbound should match
  it rather than invent a second one.

## 6. What must be true before this ships

1. signal-cli attachment sending is **verified against a non-group
   recipient** (§0). Nothing else starts until this is settled.
2. `ReplyGuard` treats `send_gif` and `send_reply` as one class of terminal
   reply verb, defined in the guard, with a probe proving a GIF turn ends the
   loop and cannot be followed by a text reply.
3. `original` renditions are unreachable; a size ceiling is enforced.
4. `rating` is capped in the request and re-checked per result.
5. No results → text fallback, legible to the model, counted; failed sends do
   not terminal-latch the agent before it can send that fallback.
6. The Giphy query passes the outbound predicate before the call.
7. `send_gif` has a `NATIVE_TOOL_BUDGETS` entry.
8. The canary decision in §4 is made explicitly.
9. `voice.md` says when a GIF is in register — **operator-authored**.
10. A GIF reply is recorded per §5 without treating provider metadata as
    Nora-authored text.
11. New rules registered in `design/09-decision-index.md` and
    `tests/test_acceptance_rules.py::RULE_TESTS`, each probed failing first.

## 7. Operator decision

Caption-less GIFs are allowed. They satisfy the reply requirement themselves;
captions and a preceding text message are voice choices, not transport or
guard requirements.
