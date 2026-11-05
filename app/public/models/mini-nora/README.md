# Mini Nora character study

Public asset for the portal's existing `/nora/showcase` page. This is still a
partial character candidate for issue #245. The approved 2D surfaces are unchanged.

`character.glb`: 10,730,088 bytes, 316,622 rendered triangles, 55 unique joints,
three facial morphs, embedded 1K PNG textures, original `Walk`/`Run` clips.
Meshopt compression and mesh quantization require the decoder configured in
`stage.ts`. The download remains opt-in; a portrait covers unavailable 3D.

## Implemented

- Local reconstruction replaces the fused hand surfaces with separate digits,
  closed palm surfaces, three weighted joints per digit, and thumb opposition.
  Both hands support open/fist/point and individual curl sliders. Each new hand
  has 8,082 triangles after local reduction. The intact cuffs hide the wrist joins.
- Neutral keeps the original face. `Smile`, `Happy`, and `Puzzled` are local
  mouth/cheek/brow morphs; their effect is deliberately restrained. They do not
  supply eye gaze, eyelid closure, or an open mouth cavity.
- Original body animation is retained. Walk adds a small loop-closed side-lock
  rotation; Run retains the earlier hair follow-through. Head and arm controls remain.
- PBR normal strength is reduced to 0.22 to soften the source's harsh normal detail.
  The original image content remains; baked base-colour shading and fine hair
  seams are not fully repaired.

The reconstruction was combined with the input GLB by preserving original body
position, normal, tangent, UV and skin attribute bytes before compression. Only
hand triangle references were removed; facial morphs are sparse additions.
Final compression quantizes positions/UVs/weights to 16 bits and normals/tangents
to 14 bits. Final accessor bytes are therefore not claimed identical to the source.

## Unfinished and diagnostic work

Separate iris/sclera and deforming eyelid geometry was actually built and tested,
including blink and four gaze directions. Its perimeter joins remain visibly
wrong and alter the approved neutral. That reconstruction is retained outside
Git and is excluded from this asset and its controls. The full character plan
remains incomplete. Do not close issue #245 on this delivery.

## Evidence

Local folder:
`/Users/mike/Workplace/nora-artifacts/mini-nora-character-completion-2026-11-06/`.
`REPORT.md` covers acceptance and limitations. It includes editable Blender
files, compressed/uncompressed GLBs, source hashes, scripts, 24 fps motion MP4s,
decoded contact sheets, matched neutral images, reimport/control checks, validator
reports and browser screenshots/timings. Historic sources are unchanged.

Final compressed and decoded GLBs validate with zero errors. One inherited
non-root skinned-mesh warning remains; the validator cannot itself decode Meshopt,
so the decoded file and the real Three.js loader were also checked. Both body
clips, three morphs and all ten digit controls survive decoding/reimport.

Playwright using Chrome and this Mac's M3 Max GPU measured roughly 60 fps over
five-second run samples at desktop and iPhone-size emulation, with zero draw calls
while paused. This is not real-phone performance or thermal/load testing.
Normal-speed videos were encoded at 24 fps and decoded frames inspected;
continuous playback and final operator visual acceptance are not claimed.

No Meshy generation was needed for the missing custom face/hand surfaces.
New paid submissions: zero; balance read back at 1,050 credits. No deployment.
