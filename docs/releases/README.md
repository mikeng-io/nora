# Release Records (archive)

`V3.md`, `V4.md` and `V5.md` are the records of the manual release scheme:
hand-written notes, tagged `V<n>` by the retired scripts/release.py.

Releases are now cut by release-please on GitHub (DEC-314): the notes are the
Conventional Commit subjects, collected into the root `CHANGELOG.md` and the
`vX.Y.Z` GitHub Release. Ordinary master pushes automatically handle patch/minor
metadata, required-check auto-merge and publication. `Release-Bump: minor` marks
an authored product milestone; `feat` alone stays patch. Breaking ranges stop for
owner-only exact-candidate major preparation. Follow `nora-release` for details
and recovery. Nothing new is written here.
