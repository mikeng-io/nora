# Release Records (archive)

`V3.md`, `V4.md` and `V5.md` are the records of the manual release scheme:
hand-written notes, tagged `V<n>` by the retired scripts/release.py.

Releases are now cut by release-please on GitHub (DEC-314): the notes are the
Conventional Commit subjects, collected into the root `CHANGELOG.md` and the
`vX.Y.Z` GitHub Release. Follow `nora-release` to dispatch prepare, review and
merge the metadata PR, then dispatch publish separately; merging alone publishes
nothing. Nothing new is written here.
