---
type: design
title: "Self-Reference — Mini option08 Balanced"
description: "Operator-approved Mini eye refresh and integrated repository asset inventory, 2026-10-26. Human Bauhinia stays unchanged."
tags: [nora, identity, appearance, source]
timestamp: '2026-10-26'
edges:
  - {src: 09-mini08-balanced, dst: 05-visual-identity-spec, rel: supersedes, provenance: asserted}
  - {src: 09-mini08-balanced, dst: 02-canonical-assets, rel: depends_on, provenance: asserted}
---

# Mini option08 Balanced

**Approved by the operator, 2026-10-26.** Option08 Balanced replaces Mini's
open dark oval eyes across the canonical image, references, T-pose, setting
art, icons, static app/site art and the site's procedural face. Open eyes
have warm visible sclera, large brown irises, dark pupils, restrained
highlights and a defined upper lid line, following the approved image.
There is no nose marker and exactly one tiny mouth. Closed-eye quiet
expressions remain compatible; every open-eyed Mini source must follow 08.

All other Mini anchors remain: 2.5–3 heads tall, dark-brown messy high bun
and face-framing locks, blush, oversized grey zip hoodie, white top, loose
blue-grey jeans, white sneakers and wired earphones when composition permits.
Mini's modeling T-pose keeps the existing no-earphones modeling exception.
The [front/side/back turnaround](../../assets/nora-mini-turnaround.png)
complements the [front T-pose](../../assets/nora-mini-tpose.png) and
[character sheet](../../assets/nora-character-reference-sheet.png); it is
reference art, not a validated model or rig.
The [human Bauhinia anchor](08-bauhinia-human-form.md) is unchanged.
[07](07-native-and-embodied-form.md) and 08 retain one identity, two forms;
this is a Mini eye revision, not another identity or a human rollout.

The approved original PNG is retained in the operator's local archive. The
repository canonical is `assets/nora-canonical-self.png`: generation text,
C2PA and EXIF chunks are omitted for privacy without re-encoding its IDAT
pixels. Its sanitized byte digest pins the image-generation skill's full
self handle. The September digest remains dated history in 02.

The operator requested refreshed source artwork, generated in the parent
session from the approved option08 canonical and the previous compositions.
`assets/visual-assets.json` records each image's role, eye version, master,
derivative recipe, consumers and retained-art reason. `assets/README.md` is
the generated readable inventory. `scripts/visual_assets.py sync` reuses the
existing icon, OG, showreel and rig processors; it never calls an image
provider or renders videos. Notebook has one retained shared source in
`assets/nora-notebook.webp` rather than a missing site input.

The live face uses the same sclera/iris/pupil/highlight/lid structure, with
iris gaze, blink, happy, closed and lidded states. Source-dependent mattes
are keyed by image bytes; hand-placed pivots and feature regions must be
checked when art changes. Static reduced-motion and no-WebGL fallbacks stay.
This 2D shader rig is not a finished 3D model or a claim of modeling readiness.

Runtime media ingest, runtime skill release/deployment, external social
profile publication and video rerenders are **not performed** by this work.
The runtime handle only becomes usable after a separate media ingest and
release. Existing rendered MP4s remain explicitly dated historical exports;
they are not evidence of current art. The two PWA install screenshots were
recaptured on 2026-10-26 through the documented synthetic-data workflow in
`app/README.md`; they contain no real personal data.

This design holds unless the operator approves another design or a measured
rendering constraint requires an explicit amendment. Deterministic inventory
checks establish coverage and byte synchronization, not visual acceptance;
the parent performs browser and final visual inspection.
