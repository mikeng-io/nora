# Release Records (archive)

`V3.md`, `V4.md` and `V5.md` are the records of the manual release scheme:
hand-written notes, tagged `V<n>` by the retired scripts/release.py.

Releases are now cut by release-please on GitHub (DEC-314): the notes are the
Conventional Commit subjects, collected into the root `CHANGELOG.md` and the
`vX.Y.Z` GitHub Release. Every releasable master push ships a patch through
generated metadata, required-check auto-merge and publication. Minor and major
are milestones the owner selects at an exact SHA. A breaking marker lists a host
change in the notes and does not stop the release; the deploy refuses a host
that is not ready. Follow `nora-release` for details
and recovery. Nothing new is written here.
