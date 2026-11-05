# Mini Nora showcase candidate

Public character asset for the authenticated portal's `/nora/showcase` page.
This is a review candidate, not the accepted final character rig. The normal
2D avatars remain unchanged. No private identities, runtime data or credentials
are embedded. The loader uses this fixed same-origin path only.

`character.glb`: 27,917,440 bytes, 307,377 triangles, 25 joints, embedded 1K PNG
textures, `Walk` and `Run` clips. Rest nodes come from the existing static body
rig. Geometry, UVs, normals, tangents, skin accessor bytes and original run
animation data are preserved from the earlier PBR/hair candidate. The walk
animation is appended by bone name. Textures are resized to 1K; base colour is
preserved in content, rather than regenerated. No geometry repair is claimed.

Supported: standing pose, walk/run playback, modest head turn and left arm lift.
Run retains the earlier single-side-lock hair prototype. No eyelid, gaze,
expression or finger controls exist. These require local surface separation,
retopology and deformation work; adding named bones alone would not deliver them.
The base colour can contain baked lighting and source faceting remains visible.

The deployable GLB is a regular Git binary, like the portal's existing shipped
art, so the existing Docker/CI checkout delivers real bytes without an LFS pointer.
The much larger editable Blender sources remain outside Git. Showreel's LFS
rules remain scoped to its source media. Revisit model delivery with a measured
mobile budget; the current 307K mesh is not certified for mobile performance.

## Provenance and evidence

Local delivery folder: `/Users/mike/Workplace/nora-artifacts/mini-nora-showcase-2026-11-06/`.
It contains `REPORT.md`, editable `mini-nora-showcase.blend`, matching GLB,
source-preservation hashes, packaging script, validator output, Blender reimport
checks and matched studio renders. Earlier artifacts are immutable.

Earlier source task IDs are retained in the local delivery report.
This run made no paid submission: balance remained 1,050 credits.

Focused validation: app typecheck/build, seven selected asset/locale tests,
Khronos GLB validator (zero errors; one inherited non-root skinned-mesh warning),
fresh Blender import and finite evaluated geometry at three phases per clip.
Browser interaction, real mobile frame rate, continuous-motion acceptance and
final operator visual acceptance remain unavailable. See issue #245.
