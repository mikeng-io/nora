# Measurement and next design questions

The executed probe supports feasibility of **iGPU text embeddings and bounded
CPU image embeddings** on norty. It does not establish a complete indexing
service or a working conversational recall feature.

## Repeatable probe shape

The quantized model blob used was
`46b85372ad4f197cbdb00753e7ed0a1d12b251f20626f92865a044bb733e0dea`.
The projector came from the DreamBlooms export linked in
[the findings](10-findings.md). That projector's content hash was not recorded;
the model hash alone does not pin the full multimodal runtime. Record both
hashes and the runtime build in any next comparison.

The native CPU server used `--embedding --pooling last -c 2048 -b 2048
-ub 2048 -t 4 -ngl 0`, bound to loopback port 11439, with both model and
`--mmproj`. Inputs were resized to a maximum side of 512 pixels. The tested
`/embedding` request used this shape:

```json
{
  "content": {
    "prompt_string": "<|im_start|>user\n<media marker><|im_end|>\n<embedding>",
    "multimodal_data": ["<base64 thumbnail>"]
  }
}
```

Use the runtime's actual media marker from its response/interface, not the
placeholder above. Text queries used the same user wrapper and final
`<embedding>` token without a media marker. Last-token pooling and matching
query/document formatting matter. This records the tested shape, not a
copy-and-run installation script or a current upstream API guarantee.

The four-image caption check used associated stored text; its provenance was
not independently labelled, and generated-prompt association could make the
match easier. Original private samples were deleted, so this result is not a
reproducible public quality benchmark. Use generated or consented evaluation
media with independent relevance labels for a repeatable comparison. Do not
copy production messages, images or identities into repository fixtures.

## Measure the user journey before building more

First assemble synthetic cases for the three questions in
[the intent](00-intent.md), including a late plan correction, food absent from
the caption, ambiguous dates, an unrelated author's food post, missing capture
coverage, unreadable media, and a provider/index outage. Read the baseline's
actual model input and tool trajectory before changing its instruction.

Compare the existing path with one integrated candidate. Use Nora's existing
eval/context-assembly machinery when a candidate is built: a live-model
trajectory with trace-bound evidence, not the author's self-assessment.
Wrong source, ungrounded participant/event association, stale destination,
empty reply or excessive budget is a failure; timeout or unavailable dependency
is an error, never a pass. Distinguish an honest uncertainty response from a
retrieval failure on evidence the subject was permitted and expected to find.

Measure retrieval on independently labelled candidates, answer support and
coverage, latency, calls/cost, and host contention. For model/runtime selection
include native GPU image support, cold/warm behaviour, long images, multi-page
documents, temporal video queries and realistic incremental volume. Compare
Qwen embedding/reranking only where the candidate needs them. Do not extrapolate
the four-caption GPU timing to “all multimedia”.

The candidate must pass the five Cs separately: **coherence** (one meaning and
owner per source/state), **consistency** (reuse existing stores and assembly),
**conformance** (scope/disclosure and repo rules), **correctness** (measured
retrieval and grounded answers), **completeness** (all three journeys and their
unavailable/ambiguous cases). A benchmark speed result establishes none of the
other four by itself.

## Questions still open

- What media does Nora actually acquire from each social surface, for how long,
  and how can an answer report that coverage without claiming a full archive?
- Which existing reference and state surfaces already bind “that image” and
  the active outing correctly? What measured case needs more assembly?
- What minimum evidence associates a media object with an occasion and its
  participants, without duplicating canonical events or inferring identity?
- What result contract can existing stores supply for shared source, time,
  freshness, relationship and unavailable outcomes?
- Can the installed runtime embed images on the iGPU, and what does that cost
  alongside Nora, speech services and existing embeddings?
- Which existing index/storage facility can hold scoped visual vectors? What
  volume and retrieval failure justify adding it?
- How are model/preprocessing versions represented so changed vectors can be
  recomputed without mixing incompatible representations? What existing
  content hashes and custody records already cover this?
- What bulk/incremental budget is acceptable, and which media need full clips,
  frames, transcripts, pages or text extraction?

Resolve these against existing code and measured cases. They are not permission
to scaffold new registries, planners, memory tiers or migration machinery.
Issue 733 remains open until the intended journey is implemented and measured;
merging this record alone does not resolve it.
