---
kind: spec
title: "Sticker comprehension: reading the booster, on every platform"
description: "Stickers become fetchable media on Signal and Discord, described once by a bounded vision call at ingest, and deduplicated by a two-level durable cache keyed first on sticker identity and then on content hash. The description records what the sticker DEPICTS; the conversational force it carries is left to Nora, who has the transcript ingest does not. Stickers only — images, audio and video keep today's pull-based media_fetch behaviour."
tags: [nora, spec, stickers, media, vision, custody, dedup, signal, discord]
timestamp: '2026-08-15'
status: accepted
derives_from:
  - design/proposed/26-media-replies-and-signal-stickers.md
  - design/proposed/15-gif-replies.md
  - design/09-decision-index.md
---

# Sticker comprehension

## 0. What this document is

The operator's brief: *"Some users might send several messages, then send a
sticker as a sort of booster to the conversation. The system does not need to
respond to or even fully read the sticker, but it should understand what it
means as a whole within the context of the entire message. This can actually be
cached."* Plus two corrections made during design, both of which narrowed it:

1. **"take care for deduplication."** Dedup turned out to have four distinct
   axes, only two of which existed. §4 is that answer.
2. **"sticker only. NOT image or audio or video."** The automatic interpreter
   never sees anything but a sticker. §5.3 makes that a tested invariant rather
   than a convention.

This elaborates `design/proposed/26-media-replies-and-signal-stickers.md`
slices 3–5. Document 26's outbound half (Giphy, `send_gif`, `OutboundMedia`) is
**not** in scope: the operator's second implementation step was explicitly
deferred — *"there is no need to send for now, just focus on reading them."*

It supersedes document 26 §4 ("Probe before contract"), whose four requirements
are satisfied by §1 below.

## 1. Evidence

Document 26 refused to freeze a fetch-token encoding until a real probe
produced four things. All four were produced on 2026-08-15 against the deployed
stack, and the design below is bound to them rather than to a guess.

**1. A real receive envelope.** 23 rows in `messages` whose `raw_json` mentions
a sticker; 17 carrying `dataMessage.sticker`, across 10 distinct stickers. The
payload is exactly what `nora/platform/signal/parser.py` claimed:

```json
"sticker": {"packId": "8c6ce85a3156b8e498e1ba0e97bc6620", "stickerId": 15}
```

No name, no emoji, no title. `listStickerPacks` reports 12 installed packs, all
with `title: ""`, `author: ""`, `cover: null`, `stickers: []` — the pack
manifests are not downloaded, so there is **no free text anywhere** describing
a Signal sticker. Vision is the only way in. This is the reverse of Discord,
where `StickerItem.name` is handed over for nothing.

**2. The exact RPC, with a negative control.** signal-cli 0.14.7, over the same
loopback `POST /api/v1/rpc` Nora already uses:

```
noSuchMethodXyz                        -> -32601 "Method not implemented"
getSticker {}                          -> -32603 NullPointerException on a null String
getSticker {packId, id}                -> -32603 NullPointerException on a null stickerId
getSticker {pack, sticker}             -> -32603 NullPointerException on a null String
getSticker {packId, stickerId}         -> {"result": {"data": "<base64>"}}
```

Three different errors and one success. The method genuinely exists and
genuinely reads both parameters, rather than a daemon that answers everything
alike — the distinction CLAUDE.md's "never write a test whose fake answers
every request the same way" rule exists to force.

**3. Decoded bytes, both formats.** Three real received stickers:

| pack | id | container | bytes |
| --- | --- | --- | --- |
| `8c6ce85a…` | 15 | animated WebP (`RIFF`…`ANIM`) | 208 014 |
| `3cfef758…` | 1 | PNG | 257 258 |
| `8c6ce85a…` | 6 | static WebP | 11 798 |

All three are two orders of magnitude below `media.vision_max_bytes` (8 MiB),
so the byte ceiling is not the binding constraint. The animated container is,
and §7 says why that is still open.

**4. Negative controls on the lookup.** An unknown pack and an unknown sticker
index:

```
{packId: "deadbeef…", stickerId: 1}    -> -1 "Could not find sticker with ID: 1 in pack StickerPackId{deadbeef…}"
{packId: "8c6ce85a…", stickerId: 9999} -> -1 "Could not find sticker with ID: 9999 in pack StickerPackId{8c6ce85a…}"
```

Identical code, identical message shape. They are **not distinguishable at this
seam**, so §3.2 gives them one reason rather than two labels implying a
precision the daemon does not offer.

**5. The dedup case, measured rather than assumed.** 17 sticker sightings, 10
distinct stickers — so 7 of 17 sightings (41%) are already repeats, before the
group's sticker vocabulary has had time to settle. 41% is a floor, not a
steady state: a group reuses a fixed sticker set, so the distinct count
saturates while the sighting count does not.

**6. A latent defect this design has to sit on.** `_build_media_custody`
(`nora/app/composition.py`) constructs a single `SignalMediaSource`, and
`nora/ingest/stages/media.py` does not gate on platform. A Discord attachment is
therefore fetched by calling signal-cli's `getAttachment` with a Discord
snowflake, which can only return `not_found`. The counters are declared but sit
at zero since the last restart, so this is a code-read finding rather than an
observed failure. It is in scope because it is the seam sticker fetch must sit
on, and building on top of the cross-wiring would deepen it.

## 2. What is decided

- A sticker becomes a first-class `MediaRef`, on both platforms, and takes the
  same byte custody every other attachment takes.
- At ingest, before the audit row is written, a bounded vision call records
  **what the sticker depicts**. Not what it means.
- The result is deduplicated by a two-level durable cache: sticker identity
  first (checkable before any bytes move), content hash second (shared with the
  `media_fetch` tool, so one byte-string has one interpretation forever).
- Interpretation runs for **stickers only**. Images, audio, video and documents
  keep exactly today's behaviour: custody, a bare `[image a3f2c1]`, and a
  description only if Nora chooses to call `media_fetch`.
- Every failure degrades to a placeholder and is counted. No branch produces a
  blank description, and none produces a guessed one.

### 2.1 Why the depiction and not the intent

Ingest holds the sticker. It does not hold the conversation — the transcript,
the peer cards, and who is in the room are assembled later, for the turn. An
intent label written at ingest is therefore a judgment about a conversation the
labeller never saw, it cannot be checked against anything, and because it
replaces the depiction in the body it destroys the evidence that would let
anything downstream disagree with it. A confidently wrong "playful
exasperation" over a message that was real panic is a durable lie in the record
that Nora cannot see past.

Recording the depiction keeps the claim checkable against the bytes and puts
the judgment where the information is:

```
Miles:  ok so the venue cancelled
Miles:  three days out
Miles:  [sticker a3f2c1: a cartoon cat collapsing face-first onto a table]
```

Nora reads the run and decides for herself. This is comprehend-once at the
correct grain — the pixels are interpreted exactly once and never re-derived;
the conversational force is a different measurement, taken where its inputs
are.

### 2.2 Why stickers only

Two reasons, and the second is the load-bearing one.

*Cost.* An automatic vision call on every image would run on every photo the
group sends, most of which Nora never needs to have seen.

*Consent.* A sticker is pack art — published, shared, and identical for
everyone who has the pack. A photo is a photograph of somebody's life. Reading
one automatically and permanently into the durable record, the Honcho feed, and
every future transcript is a materially different act from reading the other,
and it is not what was asked for. `media_fetch` already exists for the case
where Nora has a reason to look at a picture, and it leaves a trace when she
does.

## 3. Comprehend: the parsers and the sources

### 3.1 `MediaRef` gains two fields

```python
sticker: bool = False      # a separate axis from the media type
fetch_token: str = ""      # opaque, adapter-owned; the domain moves it, never parses it
```

`sticker` follows the precedent of the existing `voice_note` flag exactly.
`nora/domain/media.py` already argues that `voice_note` must be a separate axis
from `audio/*` because the media type cannot tell a song from somebody
speaking; the media type equally cannot tell a WebP sticker from a WebP photo,
and a sticker's type is frequently absent altogether. `label_for` returns
`"sticker"` when the flag is set, which is what finally makes the `"sticker"`
member of `LABELS` a thing that is produced rather than merely declared.

`fetch_token` is document 26's opaque token. The domain transports and stores
it and never parses it. It is never rendered, never fed to Honcho, never put in
model context, and never logged above debug.

**Signal** (`nora/platform/signal/parser.py`). The sticker branch stops
appending a bare string and emits:

```python
MediaRef(platform_id="", fetch_token=f"sticker:{pack_id}:{sticker_id}", sticker=True)
```

The existing comment says a `MediaRef` was deliberately withheld because "there
is no attachment id to fetch and no bytes to take custody of, so inventing one
would create a custody failure to count on every sticker." That reasoning was
correct and is now obsolete: §1 shows `getSticker` exists and returns bytes.
The comment gets replaced with a pointer here rather than deleted.

**Discord** (`nora/platform/discord/parser.py`). Each `StickerItem` emits:

```python
MediaRef(
    platform_id=str(item.id),
    fetch_token=f"sticker:{item.id}:{item.format.name}",
    media_type=_STICKER_MEDIA_TYPE[item.format],   # png/apng/gif -> image/*; lottie -> application/json
    sticker=True,
)
```

`StickerItem.name` is deliberately **not** used. A guild sticker's name is
unbounded text set by a server admin, and rendering it into the body is
precisely what `nora/domain/media.py` stopped doing when it removed
sender-supplied filenames: "a filename is unbounded text: it can hold a phone
number, an ACI, or an instruction." A sticker name is the same object wearing a
friendlier hat. It may be used later as a *tie-breaker* in evaluation, never as
body content.

### 3.2 The behaviour change: a captioned sticker stops vanishing

Both parsers today render `[sticker]` **only as a fallback when nothing else
rendered**. `tests/test_discord_parser.py::test_text_still_wins_over_a_placeholder`
pins this, and Signal's `_body_parts` has the same `if not parts` guard. A
sticker sent with a caption therefore disappears from the record entirely.

That is exactly the operator's scenario — the message plus the booster — and
today the booster is the half that is lost. Once stickers are refs they render
in the ordinary trailing placeholder run, after the text:

```
before:  "haha"
after:   "haha [sticker a3f2c1: a cartoon otter slow-clapping]"
```

The old test's premise — "the placeholder is the fallback for an empty body,
not an annotation on every message" — was right while a sticker was an
unreadable blank. Once it is understood it is not an annotation; it is content,
and the message means something different without it. The test is **rewritten
to assert the new behaviour with the reason in its docstring**, not deleted.

This also places the sticker inside the trailing run that
`domain.media.body_with_handles` already owns, so no second body-assembly rule
appears anywhere.

### 3.3 `MediaSourceRegistry`

The single `media_source` on `IngestPipeline` becomes a registry keyed on
`NormalizedMessage.platform`, mirroring `AdapterRegistry`. It returns the source
for that platform, or an explicit `not_supported` that is counted. This closes
§1's finding 6: a Discord ref is never again handed to signal-cli.

### 3.4 `SignalMediaSource` dispatches on the token

```
token == ""                          -> getAttachment(id=platform_id)     [unchanged]
token == "sticker:<pack>:<index>"    -> getSticker(packId=…, stickerId=…)
```

`getSticker` returns `{"data": "<base64>"}` — the object shape `_decode`
already accepts, so no new decoder branch is needed. Per §1's finding 4, an
unknown pack and an unknown index both become the single reason `not_found`.

### 3.5 `DiscordMediaSource`

New, and **stickers only**. It fetches sticker bytes over the CDN URL
`StickerItem` exposes. A Discord *attachment* ref returns an explicit
`not_supported`, counted.

That is a deliberate narrowing. It is strictly more honest than today, where
the same ref gets a misleading `not_found` from the wrong daemon, and it adds
no byte-storage scope beyond what was asked for. Discord attachment custody
means storing the group's actual photographs and is a separate decision with a
separate consent question; it is listed in §8 as a follow-on.

`lottie` stickers are JSON, not an image — recorded as `application/json`, not
as an empty type, so the stored row says what the bytes actually are. They take
custody like anything else, and the interpreter refuses them with
`unsupported_format` on the resolved media type — never an attempt to hand JSON
to a vision model.

## 4. Measure: dedup, four axes

Dedup here is four different things. Two already exist; two do not.

| axis | what repeats | today |
| --- | --- | --- |
| **identity** | the same pack+sticker sent again — 41% of real sightings | **absent**; the only axis checkable *before* the bytes move |
| **content** | the same art in two packs, or on two platforms | partial — `FilesystemBlobStore` is content-addressed, so custody already dedups |
| **description** | the same bytes described twice | partial — `VisionCache` is sha256-keyed, but in-memory, 256 entries, and owned by `ToolRegistry` on the agent side, which ingest cannot reach |
| **in-flight** | two people send the same new sticker seconds apart | **absent**; Signal ingest is serialized but the Discord listener is a separate task, so the race is real |

### 4.1 `MediaDescriptionStore`

A new port. Deliberately not methods added to `RecordStore`, which is about the
audit record and should not grow a cache.

```python
class MediaDescriptionStore(Protocol):
    async def resolve(self, *, platform: str, token: str) -> str | None      # -> sha256
    async def describe(self, *, sha256: str) -> str | None                   # -> raw description
    async def remember(self, *, platform: str, token: str, sha256: str,
                       description: str, model: str) -> None
```

**None of these ever raise.** The port sits on the ingest path; a store failure
degrades to a cache miss, counted, and never costs the message. This is the
same contract `MediaSource` carries and for the same reason.

Two tables, written in one transaction:

- **`sticker_index`** — `(platform, fetch_token)` primary key, `sha256`,
  `first_seen`, `last_seen`, `hits`. The only level checkable before the bytes
  move, and therefore the entire reason a repeat costs about a millisecond
  instead of a 250 KB RPC on the path the listener awaits.
- **`media_descriptions`** — `sha256` primary key, `description`, `model`,
  `created_at`.

The second is keyed on content and not on sticker identity because a
description is a pure function of the bytes: the same art in two packs, or on
both platforms, is one row and one vision call.

It is also, deliberately, **the durable backing for the `VisionCache` that
`media_fetch` already uses**. The in-memory LRU becomes a read-through in front
of it. So the push path and the pull path resolve the same sha256 to the same
sentence, permanently. Two caches that could disagree about what one image is
would be exactly the drift the comprehend-once rule exists to prevent, and
would be visible to the operator as Nora describing one picture two ways.

**The raw `fetch_token` is stored, not hashed.** A Signal pack id is not a
person identifier — R-18/R-28 screen for phone numbers, service ids and
coordinates — it lives in Nora's own Postgres, and it never leaves that table.
Hashing it would buy nothing and would make "why does this sticker keep
missing?" permanently undebuggable.

### 4.2 Single-flight

An in-process map of in-flight futures keyed exactly as `sticker_index` is, so
the Signal and Discord listeners racing on the same new sticker produce one
fetch and one vision call, with the loser awaiting the winner. Per process;
there is one Nora process.

### 4.3 The resulting cost

```
identity hit    0 RPC, 0 vision   ~1 ms         41% of sightings today, rising
content hit     1 RPC, 0 vision   sub-second    same art, new pack or platform
in-flight hit   0 RPC, 0 vision   awaits winner
cold            1 RPC, 1 vision   bounded by §5.2, then free forever
```

One edge worth naming, because it is the case that looks like it needs an RPC
and does not: an **identity hit whose description row is missing** — the
`remember` write failed, or the description was written before this feature
existed. `sticker_index` yields the sha256, and the bytes are already in Nora's
own content-addressed custody, so `BlobStore.get(key=sha256)` supplies them.
That path costs one local read and one vision call, and still zero platform
RPC.

## 5. Serialize: the interpreter and the body

### 5.1 The port

```python
class MediaInterpreter(Protocol):
    async def describe(self, *, data: bytes, media_type: str) -> MediaDescription

@dataclass(frozen=True)
class MediaDescription:
    available: bool
    text: str = ""
    reason: str = ""
```

The two-axis shape `MediaBytes`, `Roster` and `Recollection` all carry, for the
reason they all carry it: "could not describe" must stay distinguishable from
"described nothing".

The implementation is a sticker-configured wrapper over the existing
`VisionClient` — the same process-wide OmniRoute client, its own prompt, its
own budget. The prompt is authored, fixed, and never model-supplied, like the
existing one:

> This is a chat sticker. In one sentence, describe what it depicts, including
> any text written in it. Describe only what is shown — do not interpret the
> sender's mood or intent.

The final clause is what holds §2.1 in place at the point where it could most
easily erode.

### 5.2 The timeout does not inherit `derive_vision_timeout`

`derive_vision_timeout`'s invariant is `budget × derived < llm.timeout_seconds`,
and it protects the **agent loop** from a hung vision call becoming the loop's
silent `invocation_timeout`. This call runs on the ingest path, before the loop
exists. That invariant does not apply, and reusing the function would be
borrowing a guarantee about the wrong thing.

The real constraint is different and worth naming: the ingest stage is inline
and the listener awaits it, so this stall delays the next message in the room —
and if the sticker arrives *on the tagging message*, the stall is added directly
to Nora's reply latency. So the sticker call gets its own small budget
(`sticker_timeout_seconds`, default 6.0) and a histogram, so the cost is
measured rather than assumed.

Extending the custody stage rather than adding a stage after it is deliberate:
two stages would each rewrite the same trailing placeholder run, and
`body_with_handles` refuses to touch a body whose tail does not match what it
predicts, so the second rewrite would have to re-derive the first one's output.
The stage's responsibility is unchanged in words — *make the body's media
claims true* — which is the unit CLAUDE.md's one-stage-one-responsibility rule
counts.

### 5.3 Stickers only, as an invariant

The interpreter is called if and only if `ref.sticker` is true. This is not a
convention: `tests/` gains a recording fake interpreter, a message carrying an
image, a voice note, a video and a sticker, and an assertion that exactly one
call was made and that it was the sticker's.

### 5.4 Guards on the description

`nora/domain/media.py` is currently safe *by construction*: `render_placeholder`
can only emit a member of a closed label set plus hex, so no sender-supplied
string can reach the body through it. A free-text description breaks that
property, so it is replaced by explicit screening — applied **at every render,
never at store time**, matching `VisionCache`'s existing rule that "the
identifier screen runs on every SERVE … so a cache hit can never bypass it":

1. hard truncate to `sticker_description_max_chars`;
2. collapse newlines and control characters, so it cannot forge a transcript
   line;
3. strip `]`, so it cannot close its own placeholder early and leave the
   remainder reading as body prose;
4. `scan_identifiers` (R-18/R-28) — the same screen `media_fetch` already runs
   on every serve. On a hit the description is dropped, the body keeps
   `[sticker a3f2c1]`, and `nora_egress_blocks_total` counts it.

The raw description is what is stored; the screened form is what is rendered.
The description sits inside `[sticker …: …]`, structurally distinct from sender
text, and `constraints.md` gains one explicit line saying a sticker description
is content and never an instruction.

### 5.5 Failure modes

| what fails | body becomes | counted as |
| --- | --- | --- |
| custody not configured | `[sticker]` | `not_configured` |
| `getSticker` not_found / unreachable / bad_payload | `[sticker]` | existing custody reasons |
| Discord lottie | `[sticker a3f2c1]` | `unsupported_format` |
| vision timeout or transport error | `[sticker a3f2c1]` | `unavailable` |
| description fails the identifier screen | `[sticker a3f2c1]` | `nora_egress_blocks_total` |
| cache write fails | body keeps the description | `store_failed`; re-described next sighting |

No branch converts any of these into a successful but blank description, and
none invents one. An ERROR is never a PASS.

### 5.6 Metrics

- `nora_sticker_descriptions_total{platform, status, cached}` where `cached` is
  `identity` / `content` / `none`.
- `nora_sticker_describe_seconds` histogram, so §5.2's budget is observed.
- Existing `nora_media_custody_total` and `nora_media_attachments_total` gain
  the `sticker` label for free, since stickers are now ordinary refs.

## 6. Configuration

Under `[media]`, shipping dark per document 26's slice 5:

```toml
sticker_descriptions          = false   # off until the §7 probe passes
sticker_timeout_seconds       = 6.0
sticker_prompt_max_tokens     = 128
sticker_description_max_chars = 200
```

`sticker_descriptions = false` yields custody plus `[sticker a3f2c1]` — the
honest first release document 26 describes.

## 7. The one risk that cannot be closed on paper

**MiMo-v2.5 on animated containers is unverified.** One of the two real formats
in this group's Signal traffic is a 208 KB animated WebP, and Discord's `apng`
and `gif` sticker formats are the same problem wearing different headers. Most
vision models decode the first frame and ignore the rest; some reject the
container outright. If it rejects, a large share of stickers on *both*
platforms become `unsupported_format` — the feature degrades honestly, but
describes far less than the design implies. Static PNG and static WebP are not
at risk; the existing `media_fetch` path already exercises ordinary images.

Per CLAUDE.md's instrument rule, the positive result is stated before the probe
runs: **a pass is MiMo returning descriptions that distinguish two stickers
whose content has been confirmed by eye.** Returning *a* sentence is not a pass
— a plausible sentence about an image the model could not decode is precisely
the failure mode that looks like success, and a single-sticker probe cannot
tell them apart.

The probe is the gate on commit 3 (§8). If it fails, the fallback is to
transcode the first frame to PNG before the call, which is a new dependency and
a separate decision — not a silent addition.

## 8. Rollout

1. **Custody.** `MediaSourceRegistry`, `MediaRef.sticker` and `fetch_token`,
   both parsers, `DiscordMediaSource`, Signal `getSticker` dispatch. Body is
   `[sticker a3f2c1]`. Provable by reading back the audit row. Closes §1's
   finding 6 on the way past.
2. **The store.** `MediaDescriptionStore`, both tables, the contract suite,
   `media_fetch`'s `VisionCache` re-pointed at it as a read-through. No
   user-visible behaviour change.
3. **The interpreter.** Behind `sticker_descriptions`, default off. Run §7's
   probe, then turn it on.

Follow-ons, explicitly not in this spec: Discord attachment custody (§3.5),
outbound media and Giphy (document 26 §2), native Signal sticker sending
(document 26 §6, out of scope there too).

## 9. Testing

- **Parsers, both platforms:** sticker-only, sticker-with-caption (the §3.2
  change), lottie, and a sticker alongside a real attachment so placeholder
  ordering is pinned.
- **`MediaSource` contract suite:** Signal and Discord both pass it. This is the
  second implementation of that port, and CLAUDE.md forbids a second
  implementation without a shared suite — which is also why both platforms are
  in one spec rather than sequenced.
- **`MediaDescriptionStore` contract suite:** the SQL implementation and the
  fake, bound together.
- **Fakes grounded in §1's observed shapes**, not invented ones: `{"data":
  "<b64>"}` on success, `-1 Could not find sticker…` for both unknown-pack and
  unknown-index, `-32601` for an unknown method. A fake that answered every
  request the same way would make these tests decorative.
- **Ingest stage:** one test per row of §5.5.
- **The §5.3 invariant test.**
- **Dedup:** identity hit makes zero calls to the source; content hit makes zero
  calls to the interpreter; single-flight makes one of each under a concurrent
  double sighting.
- **Schema:** `tests/infra/test_schema_parity.py`, not `alembic check`. One
  hand-written revision.
- **Live gate:** send a real sticker on each platform, read back the audit row
  and the Langfuse trace.

## 10. Decision index and release

New entries in `design/09-decision-index.md`, continuing from DEC-106 / R-50:

- **DEC-107** — stickers are fetchable media on every platform; an opaque
  adapter-owned `fetch_token` carries what an attachment id cannot.
- **DEC-108** — the interpreter records what a sticker depicts, never what it
  means; the conversational force is Nora's judgment, made where the transcript
  is (§2.1).
- **DEC-109** — automatic interpretation is stickers only; images, audio and
  video stay pull-based through `media_fetch` (§2.2).
- **DEC-110** — dedup is two-level and durable, and the content level is shared
  with `media_fetch` so one byte-string has one interpretation (§4.1).
- **R-51** — a sticker description is never invented: an unavailable, timed-out,
  unsupported or screened-out interpretation leaves the placeholder, and is
  counted. Named test in `tests/test_acceptance_rules.py::RULE_TESTS`.

A release fragment accompanies the branch: a feature entry for sticker reading,
and a behaviour-change entry for §3.2 (a captioned sticker now appears in the
transcript).
