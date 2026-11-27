---
class: operations
subject: backup and restore of the computer's scoped workspaces
owns:
  - "computer backup: the host archives of every scope's workspace, taken on a schedule and before a destructive lifecycle action"
links:
  - {to: docs/design/computer/sandbox.md, rel: uses}
---

# Computer backup and restore

Host backup preserves scoped workspaces before destructive lifecycle actions
and on its operational schedule. It is not an off-host guarantee, and it never
publishes private browser profiles or credentials to the artifact catalog.

The scheduled archive excludes every scope's `browser/` directory — a Chromium
profile is a credential store — while the archive taken before a destructive
lifecycle action is complete, browser profiles included, and stays
host-private. It accounts for every regular file, including user-authored
files in environments; unsafe links, sockets and FIFOs may be omitted only
with exact receipts. Source inventory reconciles against actual entries and
allowed omissions, so a missing regular file fails rather than creating a
plausible-looking backup.

Restore preserves scope ownership and content digests, recreates interpreter
and runtime links through their owning tools and refuses unaccounted paths.
A failed backup or restore leaves the prior archive and working state intact.

A destructive recreate requires a verified backup or an explicit operator
decision. Recovery never remaps one scope into another or trusts a VM-supplied
manifest over the source inventory.
