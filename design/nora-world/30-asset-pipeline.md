---
type: design
title: "Nora World: the asset pipeline, from a spec to a 3D item"
description: "Nora writes a short spec; code resolves it cheapest first: library search, a picture on a stock object, then generation (text to image, Hunyuan3D image to 3D on Modal), automatic clean-up, a fail-closed vision check, and a cache. Runs as a deferred job on the compute outbox, with no agent and no Claude Code."
tags: [nora, world, 3d, asset, hunyuan3d, modal, compute-jobs, generation]
timestamp: '2026-10-16'
status: proposed
governance: pre-governance-input
edges:
  - {dst: 10-world-as-a-view.md, rel: depends_on, provenance: asserted}
  - {dst: 20-presentation-and-avatars.md, rel: realizes, provenance: asserted}
  - {dst: ../video-generation/00-design.md, rel: relates_to, provenance: asserted}
---

# The asset pipeline

## 1. Nora decides; the pipeline makes

Nora never models anything. Through `world_place` (`10-world-as-a-view.md`
§4) she writes a spec of a few lines, e.g. "hiking boots, worn, orange laces,
held in the right hand". Everything after that is code and models with no
agent in the loop, run as a deferred job on the compute outbox
(`compute_jobs`, `ComputeJobPoller`) the way `video_create` runs
(`design/video-generation/00-design.md`). The placement shows the closest
library item until the job finishes, then swaps.

## 2. Resolution order, cheapest first

| step | does | cost | expected share |
|---|---|---|---|
| 1. library | embedding search of the spec over the asset library | ~0 | most specs, once the library has grown |
| 2. picture on a stock object | frame, poster, ticket, sign, card, mug: generate an image and apply it as a texture | one image call | most memory objects |
| 3. generate | text → image → Hunyuan3D image-to-3D → GLB | one image call + one GPU job | the rest |

Step 2 matters more than it looks: a large share of "memory" objects are
pictures on a standard object, and a texture swap never produces a broken
mesh. Nora already has image generation (`design/proposed/29-image-generation.md`);
the world's text-to-image prompt carries the fixed world style so the result
matches the palette.

The library starts from CC0 low-poly packs (Kenney, Quaternius, Poly Pizza),
restyled once through the clean-up step, and grows with every accepted
generation (§4).

## 3. Generation on Modal

**Model.** Tencent Hunyuan3D, open weights (2.1 at the time of writing):
image to a textured 3D mesh, with a shape stage and a texture stage.
Reported VRAM, from a secondary source not yet checked: ~10 GB for shape,
~21 GB for texture, ~29 GB for both in one process. That fits one L40S
(48 GB) or A100 on Modal.

**Why Modal.** Serverless GPU billed per second with scale to zero, so a
world that needs a handful of new items a day pays for minutes, not for an
idle GPU. norty has no GPU for this. Weights live on a Modal Volume so a cold
start does not re-download them.

**Shape of the service.** One Modal function `generate(image) -> glb`, called
by the outbox backend over Modal's API with a shared secret; Modal never
reaches into norty. Its inputs are a generated image and a job id, never
anything from the Record.

**To measure before choosing settings** (none of these is known yet):
cold-start time with weights on a Volume; warm time per item, shape and
texture; cost per item; the share of items that pass the check in §4.

## 4. Clean-up and the check

**Clean-up (code, on the same Modal function or a CPU step):**
decimate to a face budget, apply the world's toon material and palette,
normalise scale to the grid, set the pivot (on the ground, or at the attach
point for avatar items), render a thumbnail.

**The check (fail-closed).** A vision model looks at the thumbnail with the
spec and answers PASS or FAIL. FAIL, an ERROR from any stage, or a timeout
all mean the item is not placed and the library's closest item stands in. A
bad generation never reaches the world, and an ERROR is never read as a pass.

**Cache.** An item that passes joins the library with its spec, embedding and
thumbnail, so the next similar spec ends at step 1.

## 5. Before building

- **License.** The Hunyuan3D community license reportedly excludes the EU,
  the UK and South Korea from its territory and has its own terms on use.
  Read the license text itself (not a summary) for where Modal runs the job
  and where Nora serves the result.
- **Version.** Newer Hunyuan3D versions may be hosted-only; confirm which
  version's weights are open when this is built.
- **Daily cap.** As with video, a hard daily cap on generation jobs, read
  from `compute_jobs` before every create.

## 6. What would reopen this

- Measured pass rate of generated items so low that step 3 mostly falls back:
  then drop generation and grow the library by hand instead.
- Cost per item measured high enough that the daily cap binds every day: then
  generation moves to a nightly batch on one warm container.
