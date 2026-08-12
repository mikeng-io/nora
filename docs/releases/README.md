# Release Records

`V<n>.md` is created only when the operator explicitly decides to release.
It is a curated, standalone account of the accumulated user-visible changes
since the prior version. It is not a deployment log and is not generated from
Git commit subjects.

Use the JSON files in `release-fragments/unreleased/` as source material. They
accumulate across ordinary merges and deployments until a release is chosen.
At that point, review and edit the generated draft into this file's `Summary`
and `Group message` sections. After a successful release, archive the consumed
fragments under `release-fragments/released/V<n>/`.

Do not create this file, tag a version, or run `scripts/release.py` merely to
deploy code. `scripts/build-image.sh` is the ordinary unversioned deployment
path.
