# Current flow and model findings

Snapshot: 2026-10-07. Code observations, live configuration reads, provider
documentation and executed probes are different evidence types below.
Configuration proves intended routing, not successful inference by a provider.

## Nora already has readers and several retrieval paths

The code read-back used the codebase graph and the corresponding source.
The main anchors are [agent tools](../../nora/agent/tools.py),
[media search storage](../../nora/infra/sql/media_objects.py),
[comprehension](../../nora/infra/vision/comprehend.py),
[video preprocessing](../../nora/infra/vision/preprocess.py), and
[the architecture map](../../ARCHITECTURE.md).

Acquisition keeps encrypted original bytes with a content hash and runs
comprehension asynchronously. A compact textual reading can enter the
transcript; the full reading and original remain separately available.
The reply model therefore often reasons from text about media rather than
receiving the original image or video directly.

`media_fetch` resolves a specific code or URL. With an empty question it can
return stored comprehension; with a specific question it makes a fresh reader
call against the original, without replacing the generic stored reading.
Its result is a textual structured tool result, not an original attachment to
the main model. It is not an arbitrary-file reader: PDF and ZIP inputs are not
handled as images.

`media_search` currently searches words in titles, content, descriptions and
provenance. Its storage lookup ANDs substring terms. Its inbound-room search
has a 90-day window. It is **not** using the experimental WeMM vectors.
`load_transcript` retrieves message history; `about` provides stored personal
conclusions; `ask_memory` asks Honcho to reason about a person. Honcho also has
semantic message search scoped to its session/unit. These jobs should keep
their distinct source meanings; a personal conclusion is not a meal record.

This establishes separate available paths, not a measured failure of every
existing conversational journey. No end-to-end recall evaluation was run.
Nor was comprehensive Instagram acquisition established: an embedding cannot
retrieve an image that Nora never captured. Authorship, depicted subjects,
capture time, publication time and event time must not be conflated.

## Image and video routing

The live configuration had comprehension enabled, image reader alias
`nora-image`, and audio/video readers `mimo-v2.6-flash` through `opencode-go`.
Both `nora` and `nora-image` routed first to
`ollama-cloud/deepseek-v4.1-flash`, with an
`opencode-go/deepseek-v4.1-flash` fallback. The image alias used fill-first
routing. The video limits were 90-second chunks, at most ten chunks and a
100 MiB input limit. The optional text-only video headline summarizer was off.

Thus the intended division is DeepSeek for the main reasoning/image-reading
jobs and **MiMo** for audio/video comprehension. “Memo” in the discussion refers
to MiMo. This is routing observed in configuration, not a fresh successful
provider call measured in this investigation.

[DeepSeek's vision guide](https://api-docs.deepseek.com/guides/vision/)
documents image inputs; it does not document a native video input there.
[MiMo's video guide](https://mimo.mi.com/docs/en-US/quick-start/usage-guide/multimodal-understanding/video-understanding)
documents video understanding. Absence of a documented video API is narrower
than declaring a model incapable of reasoning about video-derived frames/text.

The actual video preprocessing keeps audio, with a silent retry if audio
processing fails. The example config's older “audio stripped” comment disagrees
with this code; it is not evidence of current behaviour. Chunked video readings
carry time windows; reading failure or omitted audio limits what can be claimed.

## Candidate embedding models

| Candidate | Published native inputs | Published weight size | Evidence here |
|---|---|---|---|
| WeMM-Embedding-2B | Text, image, video, visual documents, interleaved content | 5.44 GB repository weights | Quantized local probe below |
| Qwen3-VL-Embedding-2B | Text, image, video | 4.26 GB | Documentation only |
| Qwen3-VL-Reranker-2B | Multimodal query/candidate relevance scoring | 4.26 GB | Not downloaded or benchmarked |

Sources: [Tencent repository](https://github.com/Tencent/WeMM-Embedding),
[WeMM weights](https://huggingface.co/tencent/WeMM-Embedding-2B/tree/main),
[Qwen repository](https://github.com/QwenLM/Qwen3-VL-Embedding),
[Qwen embedding weights](https://huggingface.co/Qwen/Qwen3-VL-Embedding-2B/tree/main),
[Qwen reranker weights](https://huggingface.co/Qwen/Qwen3-VL-Reranker-2B/tree/main).
Weight size excludes runtime overhead; the Qwen pair totals 8.52 GB of
published weights, not a guaranteed RAM footprint.

Neither embedding family supplies a native audio path here. Audio needs a
transcript; ordinary documents need extraction/chunking or rendered pages;
video needs the supported temporal representation. “All files” is an ingestion
and coverage contract, not a model capability inferred from its parameter count.

Tencent's reported retrieval results make WeMM worth evaluating first, but
they do not prove superiority on Nora's corpus. Its 2B output can be shortened
from 2048 dimensions; the reported 256-dimensional image/video result retains
98.7% of the reported benchmark performance. That reduces vector storage,
not model weight size or image encoding cost. A Qwen reranker after WeMM remains
a possible separately measured choice; no pair was tested here.

## Norty and the executed probe

The live host read showed Ryzen 7 PRO 6850U (8 cores/16 threads), Radeon 680M
integrated graphics, about 27 GiB usable RAM and about 15 GiB available around
the trial. Swap was already used (about 5.3 GiB initially, 6.3 GiB later).
The 937 GB root disk had about 313 GB available. There was no discrete GPU in
the hardware read. Nora shares this machine with other services.

Installed Ollama was **0.35.0**. Existing BGE-M3 and the experimental WeMM text
model both showed 100% GPU in `ollama ps`. The earlier CPU-only assessment is
therefore corrected: **text embeddings did offload on this iGPU**. The backend
was not established. The [Ollama GPU documentation](https://raw.githubusercontent.com/ollama/ollama/main/docs/gpu.mdx)
does not list the 680M among official ROCm targets; Vulkan availability and a
successful existing service are separate evidence. The SSH user lacked device
groups while the Ollama service user had them; native GPU image execution was
not verified. This is not proof that GPU image execution is unsupported.

The [community Ollama build](https://ollama.com/milkey/wemm-embedding-2b)
is advertised as text-only. Its Q4_K_M weights were used. A separate
[GGUF projector](https://huggingface.co/DreamBlooms/WeMM-Embedding-2B-GGUF/raw/main/README.md)
was downloaded (671,373,120 bytes). A temporary Ollama alias advertised both
vision and embedding after adding it, but the installed `/api/embed` rejected
map-shaped multimodal input with `invalid input type`. Advertised capabilities
did not establish a working image embedding endpoint. Upstream API examples
must not be treated as evidence for this installed version.

A native `llama-server` build `b1-161755f29` successfully embedded images on
CPU with the model and projector, last-token pooling, 2048 context/batch and
four threads. A full original image required 2774 tokens and exceeded that
context. Resizing to a maximum side of 512 pixels allowed the tested inputs.

| Probe | Observation | Limit |
|---|---|---|
| Four real available images, bounded thumbnails | 2.885, 3.204, 3.086, 5.122 seconds; finite normalized 2048-dimensional vectors | CPU; no full-resolution or bulk workload |
| Four associated captions queried against those four images | Matching image top-ranked in all four cases | Tiny closed set; stored captions were not independently labelled ground truth |
| One 17.376-second video | Frames at 10%, 50%, 90% embedded in 3.680, 4.187, 3.909 seconds (11.776 total) | Independent frame encoding, not joint temporal/video embedding; no audio test |
| Wrapped four-caption batch, warm CPU | 2.681 seconds | One warm repetition, 167 tokens |
| Same batch, warm Ollama iGPU | 0.413 seconds; GPU offload observed | Text only; concurrent CPU frame work may affect comparison |

The CPU batch's first run was 5.435 seconds including 2.821 seconds loading;
the GPU first run was 4.670 including 4.070 loading. One native-server RSS
sample was about 3.31 GiB; no peak memory was measured. The warm timing ratio
is about 6.5, not a production throughput promise. There was no tail latency,
concurrency, service contention or independent retrieval-quality measurement.

The probe fetched available originals read-only and verified plaintext hashes.
No raw images, captions, participant identifiers or vectors are committed.
The native server was stopped, temporary alias removed, experimental models
unloaded and decrypted sample copies removed. The downloaded community model
and projector remained. Nora's health endpoint responded afterwards; this is
not an exhaustive service-health measurement. Runtime configuration was not
changed and no embedding index was deployed.
