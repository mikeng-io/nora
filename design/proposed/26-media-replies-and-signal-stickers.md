---
kind: design
title: "Nora media replies and Signal sticker reading"
description: "An adapter boundary for Giphy GIF lookup and Signal sticker retrieval, without teaching the core either vendor's protocol."
tags: [nora, design, giphy, gif, signal, stickers, media, adapters]
timestamp: '2026-08-11'
status: proposed
---

# Media replies and Signal stickers

This elaborates `15-gif-replies.md`. That document establishes the behavioural
decision: Giphy is a terminal reply verb, it sends a normal platform attachment,
and a GIF's metadata is the inspectable part of its content. This document
defines the boundary so that neither Giphy nor Signal sticker-pack semantics
leak into Nora's core. Discord is the first required second implementation;
WhatsApp and Telegram must fit without changing the contracts again.

There are two deliberately separate flows:

1. **Outbound GIF:** an intent (`send_gif(query, caption)`) becomes a selected,
   bounded media payload, then a platform delivery.
2. **Inbound sticker:** a Signal-specific sticker reference becomes held bytes,
   then an optional media description. It never becomes a special case in the
   turn, custody, memory, or model code.

They share generic media contracts, but neither one is implemented by making
"Giphy", "Signal sticker", "Discord attachment", or a future platform's media
object a concept in `nora.agent` or `nora.domain`.

## 1. Boundary and ownership

The core owns only three vendor-neutral facts:

- an outbound message may carry a bounded `OutboundMedia` value;
- an inbound `MediaRef` may yield bytes through the existing `MediaSource`
  port; and
- bytes may be described by a `MediaInterpreter` port before a description is
  placed in model context or memory.

An `OutboundMedia` contains bytes, a verified media type, a generated filename,
and an internal provenance record. It never contains a URL or a caller-supplied
attachment string. `PlatformAdapter.send` widens once, with an optional tuple of
such values. A platform adapter that cannot deliver media returns an explicit
unsupported result; it does not silently discard the media.

`MediaRef` remains a claim made by the platform. To represent things which do
not have a normal attachment id, it needs an opaque, adapter-owned fetch token
in addition to the existing display metadata. The domain transports and stores
that token but never parses it. Signal may encode an attachment id or the pair
`(pack_id, sticker_id)` in it; Discord may encode an expiring CDN attachment
reference; another platform is free to use a different scheme. The token is not
rendered, fed to Honcho, logged at info level, or made available to the model.

Media sources must be selected by the message's platform, not by a global
`SignalMediaSource`. The composition root owns a `MediaSourceRegistry` keyed by
the platform name already carried on `NormalizedMessage`. It returns the
matching `MediaSource` for custody, or an explicit `not_supported` answer when
that adapter cannot retrieve bytes. This mirrors `AdapterRegistry`: a Discord
adapter brings `DiscordMediaSource`; it does not make the ingest pipeline learn
Discord URLs, permissions, or attachment fields.

The resulting ownership is intentionally narrow:

| Concern | Owner |
| --- | --- |
| Choosing whether a GIF is in register | doctrine and the existing reply tool |
| Querying, filtering, downloading, and attributing a Giphy result | `GiphyGifCatalog` adapter |
| Turning generic outbound bytes into a platform upload/attachment | platform sender adapter |
| Parsing Signal's `sticker` envelope object and resolving its pack/index | `SignalMediaSource` adapter |
| Parsing Discord attachment/sticker events and fetching permitted bytes | `DiscordMediaSource` adapter |
| Limits, custody, model context, audit ordering, and text-only fallback | generic core pipeline |
| Understanding pixels | pluggable `MediaInterpreter`, not Signal or Giphy |

This is not an abstraction for its own sake. Giphy is a search provider, and
Signal, Discord, WhatsApp, and Telegram are delivery/ingest platforms. They
fail, evolve, and authorize access independently, and must be replaceable
independently.

### 1.1 Cross-platform media contract

The shared contract is deliberately capability-based, not a list of platforms:

```python
class PlatformMedia(Protocol):
    platform: str

    async def fetch(self, *, ref: MediaRef) -> MediaBytes: ...

    async def deliver(
        self, *, target: ConversationTarget, message: OutboundMessage
    ) -> SendResult: ...
```

`OutboundMessage` contains text, existing decorations, and zero or more
`OutboundMedia` items. The platform adapter decides whether it can upload each
item. It may convert bytes to a Signal data URI, a Discord multipart upload, a
Telegram file upload, or a WhatsApp media message. It may not reinterpret the
content, fetch a caller-controlled URL, or silently remove an unsupported item.

The generic layer permits only portable claims: declared and measured MIME type,
byte size, generated filename, a short safe description, and provenance. It
does not promise a native GIF picker, native stickers, embeds, reactions,
ephemeral media, spoilers, or platform-specific formatting. Those are separate
capabilities, proposed only when Nora has a cross-platform use for them.

For every enabled platform, the contract suite must prove four behaviours:

1. Text-only messages remain unchanged.
2. A supported bounded GIF is delivered as one message with its caption.
3. An unsupported or failed media delivery returns a visible outcome, allowing
   the terminal reply tool to fall back to text without a duplicate send.
4. Inbound media either produces held bytes or one reasoned unavailable answer;
   it is never silently omitted from the transcript.

## 2. Giphy: provider adapter, then delivery adapter

`send_gif` remains the model-facing terminal reply verb described in document
15. Its implementation should depend on a small `GifCatalog` port, not on an
HTTP client or Giphy response dictionaries:

```python
class GifCatalog(Protocol):
    async def select(self, *, query: str) -> GifSelection | MediaUnavailable: ...
```

`GifSelection` carries a provider id, textual metadata (`title`, `alt_text`,
rating), and a bounded `OutboundMedia`. `GiphyGifCatalog` is the only
implementation that knows `/v1/gifs/search`, Giphy rendition names, API keys,
or attribution fields.

The tool sequence is:

1. Apply the existing outbound-text predicate to `query` before it leaves Nora.
2. Ask `GifCatalog.select` for one acceptable candidate.
3. Apply the same predicate to returned textual metadata and reject candidates
   whose rating or metadata does not pass document 15's policy.
4. Record a described act before delivery: query, provider id, selected
   rendition, public attribution URL, metadata, and caption. Do not record GIF
   bytes or the API key.
5. Deliver the caption and `OutboundMedia` as one platform message. Signal
   builds the data URI itself from those bytes.
6. On no result, provider failure, download failure, or attachment failure,
   return a legible text-fallback outcome to the tool. The turn still ends via
   the ordinary text reply path; it must not become silent.

The `GiphyGifCatalog` must enforce the existing `pg` cap twice (request and
response), select only an allowlisted non-`original` rendition, and verify the
actual downloaded byte length after checking the provider's declared size.
Redirects must be bounded and HTTPS-only. The adapter must not trust a
provider's declared MIME type: it verifies the GIF signature and returns a
media-type failure on mismatch.

The core does not send a Giphy URL. Each platform adapter receives verified
attachment bytes and uses its own upload mechanism. Signal renders a GIF inline
only when its adapter receives attachment bytes. This also preserves the
existing hard rule that an arbitrary bad data URI must never turn into a host
file-path read.

## 3. Signal stickers: retrieve first, understand second

A received Signal sticker is not an attachment with a normal attachment id.
signal-cli exposes a separate `getSticker(packId, stickerId)` operation, while
outbound proper stickers use an installed local sticker pack. Nora needs only
the former for this proposal. It is explicitly **not** a proposal to make Nora
send Signal-native stickers.

The Signal parser already distinguishes a sticker-only message and renders
`[sticker]`. It should additionally produce one generic `MediaRef` with a
Signal-owned opaque fetch token. The parser, not the core, recognises the
platform's `dataMessage.sticker` fields. `SignalMediaSource.fetch` dispatches
on its own token and calls either `getAttachment` or `getSticker`; the generic
custody stage still makes exactly one `fetch(ref)` call and applies its existing
size, encryption, retention, and failure accounting rules unchanged.

The distinction matters:

- `getAttachment` with an empty id is a known non-fetchable answer today.
- A sticker with a valid pack/index is fetchable by a different Signal RPC.
- Treating a sticker as a fake attachment loses it before the adapter can make
  the supported call.

Once bytes are held, an optional `MediaInterpreter` receives a bounded media
blob and returns a short, plain-text `MediaDescription` or an explicit
unavailable answer. The Signal code has finished before this point. A vision
implementation can support WebP, PNG, and animated sticker formats; a text
only deployment simply preserves `[sticker]` and does not claim to understand
the image.

Descriptions are untrusted content. They are bounded, marked as a description
rather than a sender utterance, and pass the normal prompt-injection treatment
before inclusion in the model context. They are useful context, not commands.
For example, a safe transcript representation is:

```
[sticker: visual description unavailable]
[sticker: a person giving a thumbs-up]
```

The raw sticker bytes belong to encrypted custody. The durable conversation
record and Honcho receive only this compact description, the generic
`[sticker]` fallback, and a non-secret provenance id. They do not receive pack
keys, opaque fetch tokens, base64, or image bytes.

## 4. Probe before contract

The current repository fixture uses `sticker={"packId": "x", "stickerId": 3}`;
it is a parser regression test, not evidence from a real received sticker. Do
not freeze the fetch-token encoding until a real probe has produced all of:

1. a redacted JSON-RPC receive envelope for a sticker from an installed pack;
2. the exact `getSticker` request and success response on the deployed
   signal-cli version;
3. the decoded byte type, measured size, and behaviour for static and animated
   stickers; and
4. negative controls for an unknown pack and an unknown sticker index.

The implementation must treat malformed fields, unsupported content types,
oversize declared values, oversize decoded values, transport failure, and a
missing pack/index as explicit unavailable reasons. No branch may convert one
of those answers into a successful but blank description.

## 5. Rollout slices

1. **Portable media foundation:** add `OutboundMedia`, `MediaSourceRegistry`,
   and the cross-platform contract suite. Implement the Signal sender/source
   first, but keep the interfaces free of Signal terms.
2. **Giphy delivery:** add `GifCatalog` and bind its generic media to the
   selected platform adapter. Keep visual inspection out of scope; metadata
   filtering and text fallback are the release gate from document 15.
3. **Discord adapter:** implement `DiscordMediaSource` and Discord media
   delivery against the same contract tests. Its attachment URLs, authorization,
   and event payloads remain private to `nora.platform.discord`.
4. **Sticker custody:** capture the real Signal envelope, add Signal's opaque
   sticker fetch token, and extend `SignalMediaSource` with `getSticker`. The
   context remains `[sticker]` until bytes are demonstrably held.
5. **Sticker interpretation:** add one bounded `MediaInterpreter` adapter,
   initially disabled by configuration. Only after its failure modes and prompt
   boundary are tested may descriptions replace the generic placeholder.

Each slice needs contract tests at its boundary and an integration probe that
uses the real Signal JSON-RPC shape. The tests must prove that a missing or
unsupported capability becomes a visible fallback, that a provider/platform
adapter is the only code naming its protocol fields, and that no outbound media
can bypass the record-first, guard, and terminal-reply rules.

## 6. Decisions retained and deferred

- Retained: Giphy GIFs are Signal attachments, never native Signal stickers.
- Retained: Giphy metadata is the inspectable release-time control; frame OCR
  is not a substitute for a real safety guarantee.
- Retained: caption-less GIFs remain permitted under document 15's voice and
  memory rules.
- Retained: provider lookup is platform-independent; the current conversation's
  platform selects the delivery adapter at the final send boundary.
- Retained: Discord is an adapter implementation target, not a second media
  pipeline or a Signal compatibility layer.
- Deferred: which vision provider interprets sticker bytes, its retention
  terms, and its maximum animated-frame sampling budget.
- Deferred: whether descriptions are generated synchronously before a reply or
  asynchronously for future context. Start synchronously only if the measured
  latency fits the turn budget; otherwise the honest first release is custody
  plus `[sticker]`.
- Out of scope: sending native Signal stickers, managing installed sticker
  packs, and using a Signal sticker pack as a Giphy substitute.
