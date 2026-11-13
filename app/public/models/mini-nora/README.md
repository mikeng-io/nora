# Mini Nora character study

Public asset for the shared `/nora/showcase` page, still a partial candidate for
issue #245. Mini Nora keeps her approved Bauhinia appearance and original eyes.

`character.glb`: 74,713,100 bytes, about 316,622 rendered character triangles,
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
source rectangles and their surrounding texels replace the previous eroded-mask
transfer, which left artificial black streaks on thin UV regions. Geometry,
weights, morph targets and existing animation data are preserved. Normal strength
remains 0.22. Texture resolution takes priority over the previous 14 MB package
budget; the asset check now caps this full-quality package at 90 MB and verifies
map dimensions. Real-device loading and memory acceptance remain outstanding.

## Rig and expressions

- Both reconstructed hands retain ten individual digit controls, three joints
  per digit, thumb opposition, and open/fist/point presets. Maximum-curl creases
  remain.
- Smile/Happy/Puzzled remain restrained mouth/cheek/brow morphs. Puzzled also
  moves fringe vertices and still needs a localized mask repair.
- Head/arm controls and the existing side-lock hair movement remain. No new
  whole-body rig or remesh.
- `IdlePilotCalibrated` carries a four-second idle calibrated against the target
  Stand pose. The showcase currently exposes Stand/Walk/Run; exposing the new
  idle and checking transitions is a separate remaining task.
- Broad key/fill lights, rim light, balanced environment and the existing shadow
  setup remain unchanged.

## Remaining acceptance

Eye gaze, blink and eye/lid reconstruction are parked. The original eye geometry
and appearance remain; no failed reconstruction or fake eye controls are exposed.
Fine head/hair faceting and source-painted shading remain.

Texture-transfer review passed in matched fresh-import stills from front, back
and both sides. Eight idle poses were visually inspected; all 97 clip frames were
evaluated for finite mesh coordinates. This is not continuous collision proof.

Generic asset QA reports unchanged baseline issues: four zero-area UV faces,
hand helper surfaces without UV maps, and a missing material on an Icosphere
helper. Its collision checks skip the full model above the default triangle cap.
Full character acceptance, browser acceptance of this high-resolution package,
and real-phone GPU/memory/thermal acceptance remain unmet. Keep issue #245 open.

## Source and editable files

External editable Blend, build scripts, transfer diagnostics, matched renders and
checks are retained in:
`/Users/mike/Workplace/nora-artifacts/mini-nora-hires-cleanup-2026-11-13/`.
`REPORT.html` describes the repair and limits; `receipt.json` records exact
candidate checks. Earlier hand/expression source remains in
`/Users/mike/Workplace/nora-artifacts/mini-nora-character-completion-2026-11-06/`.
