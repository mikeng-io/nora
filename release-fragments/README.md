# Release Fragments

Each branch that changes Nora in a user-observable way adds one JSON fragment
under `unreleased/`. Fragments are the source for the next version tag's
annotation. They preserve product intent at implementation time; do not attempt
to reconstruct it from commit subjects at release time.

The supported `type` values are `feature`, `fix`, `change`, `security`, and
`none`. Use `none` only for internal-only work, with a concise `reason`.

```json
{
  "type": "fix",
  "summary": "Nora falls back to a text reply when a GIF cannot be delivered.",
  "details": [],
  "limitations": []
}
```

Fragments accumulate after the latest release. They do not tag, build, deploy,
or announce anything. At an explicitly chosen release, use them to prepare the
manual `docs/releases/V<n>.md` record that `scripts/release.py` requires, then
archive the consumed JSON files alongside that release record. Ordinary
deployments leave fragments untouched.

Fragments never create a tag or deploy a release themselves.

```bash
uv run python scripts/render_release_notes.py
uv run python scripts/check_release_fragments.py --base origin/master
```

`check_release_fragments.py` is a branch-level gate: changed code with no new
fragment is refused. A `type: none` fragment is the explicit, reviewable
exception; it is not rendered into the user-facing note.

After deployment, run the real GIF smoke test from the `nora` container. It
requires a disposable group ID and sends one message there:

```bash
docker cp scripts/smoke_giphy_signal.py nora:/tmp/giphy-smoke.py
docker compose exec -T -e GIPHY_SMOKE_GROUP_ID=<disposable-group-id> nora \
  uv run --no-sync python /tmp/giphy-smoke.py
```
