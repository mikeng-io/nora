# Mini Nora character study

Public asset for the shared `/nora/showcase` page, still a partial candidate for
issue #245. Mini Nora keeps her approved Bauhinia appearance and original eyes.

`character.glb`: 64,950,252 bytes, about 316,622 rendered character triangles,
55 unique joints, three facial morphs, embedded PNG textures, and `Walk`, `Run`
and `IdlePilotCalibrated` clips. Meshopt compression requires the decoder in
`stage.ts`. Download remains opt-in.

## Texture master

The accepted high-resolution restoration preserves the retained source maps:

| Surface | Base colour and normal | Metallic/roughness |
|---|---|---|
| Body atlas | 4096 × 5632 | 2048 × 2816 |
| Head | 4096 × 4096 | 2048 × 2048 |

Body charts retain the source's native 4096-pixel sampling density. Lossless PNG
re-encoding preserves exact decoded pixels while reducing package size. Complete
source interiors are extended into complete chart gutters with nearest valid
texels. This avoids neighbouring-chart bleed under browser mipmapping and the
black streaks caused by bounded dilation on thin branches. Geometry, weights,
Smile/Happy and existing animation data are preserved; Puzzled is localized below.
Normal strength remains 0.22. Texture resolution takes priority over the previous 14 MB package
budget; the asset check now caps this full-quality package at 90 MB and verifies
map dimensions. Real-device loading and memory acceptance remain outstanding.

## Rig and expressions

- Both reconstructed hands retain ten individual digit controls, three joints
  per digit, thumb opposition, and open/fist/point presets. Maximum-curl creases
  remain.
- Smile/Happy/Puzzled remain restrained expressions. Puzzled uses a localized
  asymmetric mouth/cheek deformation; its upper-face/fringe movement is removed.
- Head/arm controls and the existing side-lock hair movement remain. No new
  whole-body rig or remesh.
- `IdlePilotCalibrated` carries a four-second idle calibrated against the target
  Stand pose. The showcase exposes Stand/Idle/Walk/Run with a 250 ms pose blend
  when switching. Playback still starts only on request.
- The viewer retains Three.js, broad key/fill and rim lights, a restrained studio
  environment and explicit sRGB output with ACES tone mapping. A height-faded,
  blurred contact projection replaces the hard directional floor shadow. It
  updates for pose changes and is reused for camera-only movement. The default
  camera shows a slight three-quarter view. No post-processing dependency is added.

## Remaining acceptance

Eye gaze, blink and eye/lid reconstruction are parked. The original eye geometry
and appearance remain; no failed reconstruction or fake eye controls are exposed.
Fine head/hair faceting and source-painted shading remain.

Texture-transfer review passed in matched fresh-import stills from front, back
and both sides. Eight idle poses were visually inspected; all 97 clip frames were
evaluated for finite mesh coordinates. This is not continuous collision proof.

Generic asset QA reports unchanged baseline issues: hand helper surfaces without
UV maps (flat materials need no image UVs).
The Icosphere warning comes from Blender's generated bone-display shape and
is not an exported GLB object. Collapsed UV faces were repaired with shifts
below 0.012 texture pixel, preserving geometry and image resolution. Its collision
checks skip the full model above the default triangle cap.
Desktop and phone-viewport browser checks pass. Full character acceptance and
real-phone GPU/memory/thermal acceptance remain unmet. Keep issue #245 open.

## Source and editable files

External editable Blend, build scripts, transfer diagnostics, matched renders and
checks are retained in:
`/Users/mike/Workplace/nora-artifacts/mini-nora-completion-2026-11-13/`.
`mini-nora-final.blend` preserves the editable geometry and decoded RGB texture
content; `ready-encoding-check.json` records the final lossless PNG re-encoding.
Earlier restoration evidence is in
`/Users/mike/Workplace/nora-artifacts/mini-nora-hires-cleanup-2026-11-13/`.
Earlier hand/expression source remains in
`/Users/mike/Workplace/nora-artifacts/mini-nora-character-completion-2026-11-06/`.
