# Mini Nora character study

Public asset for the shared `/nora/showcase` page, still a partial candidate for
issue #245. The approved 2D surfaces are unchanged.

`character.glb`: 13,799,764 bytes, 316,622 rendered triangles, 55 unique joints,
three facial morphs, embedded PNG textures, original `Walk`/`Run` clips.
Meshopt compression requires the decoder in `stage.ts`. Download remains opt-in.

## Implemented

- Both reconstructed hands retain ten individual digit controls, three joints per
  digit, thumb opposition, and open/fist/point presets. Maximum-curl creases remain.
- Neutral retains the original head geometry, UVs and texture payloads before
  compression. Smile/Happy/Puzzled remain restrained mouth/cheek/brow morphs.
- Original body clips, head/arm controls and the existing side-lock hair movement
  remain. No new whole-body rig or remesh.
- Body UV charts are repacked with padded, locally extended colour and PBR maps.
  This removes bright atlas-edge bleed that made the navy trousers look cracked.
  Lower-cloth normals are averaged across coincident seam vertices within a
  60-degree neighbourhood, with tangents re-orthogonalized. Silhouette, positions,
  skin weights and motion are preserved before compression. Head textures are
  excluded from this repair to preserve eyelash/iris detail.
- The showcase uses broad key/fill lights, a rim light, balanced environment and
  one 512-square blurred shadow caster. Other lights do not cast shadows.

The body uses three 2K maps; the original head keeps its 1K maps. Original normal
strength remains 0.22. The texture and download cost increased; the asset test
caps the current package at 14 MB. Position/UV/weight quantization is 16-bit;
normal/tangent quantization is 14-bit. One compressed stream is stored as its exact
decoded bytes to avoid an accidental identifier-shaped binary run. No scanner
bypass or acknowledgement was used.

## Unfinished

Eye gaze and blink are not shipped. A new joined eye/lid implementation corrected
boundary ordering and retained separately intersecting hair components, but its
lid ridges and iris/sclera occlusion still fail neutral and animation appearance.
It remains diagnostic only. No fake eye controls are exposed. Fine head/hair
faceting and baked shading remain. Full character acceptance is unmet; do not
close issue #245. Real-phone GPU, memory and thermal acceptance is unavailable.

## Evidence

Latest external source/evidence folder:
`/Users/mike/Workplace/nora-artifacts/mini-nora-eye-finish-2026-11-06/`.
Its `REPORT.md` records the diagnosis, accepted/rejected local work, exact files,
checks and limits. It contains the editable final reimport, separate failed-eye
Blend/GLB, source scripts, matched browser controls and before/after frames,
24 fps body and failed-eye motion with decoded frames, and validator reports.
Earlier hand/expression evidence remains in
`/Users/mike/Workplace/nora-artifacts/mini-nora-character-completion-2026-11-06/`.

Focused app typecheck/build and seven selected tests pass. Both compressed and
fully decoded GLBs validate with zero errors; the inherited non-root skinned-mesh
warning remains. Fresh Blender reimport checks retain all ten digit controls and
three expressions with measured vertex movement. Browser desktop and phone-size
viewport checks are desktop GPU evidence only. No paid Meshy stage, independent
review, merge or deployment was performed by this implementation worker.
