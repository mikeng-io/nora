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

At release time, review the rendered note, use it as the annotated `V<n>` tag
message, then move the consumed fragments to `release-fragments/released/V<n>/`.

```bash
uv run python scripts/render_release_notes.py
uv run python scripts/check_release_fragments.py --base origin/master
```

`check_release_fragments.py` is a branch-level gate: changed code with no new
fragment is refused. A `type: none` fragment is the explicit, reviewable
exception; it is not rendered into the user-facing note.
