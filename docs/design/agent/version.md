---
class: design
subject: how Nora answers what version she is, and what has been released
owns:
  - running version: "the build stamp baked into the image, the only statement of what this process is"
area: agent
links:
  - {to: docs/design/agent/tools.md, rel: uses}
---

# Version

Nora answers two questions about versions: what she is running, and what has been published.

- **The running version is a stamp baked at build time.** A lookup would describe the repository, and
  the repository and the running image disagree from a merge until its deploy, which is exactly when
  someone asks. The build refuses a missing stamp and one that disagrees with the version it was
  built for.
- **The published releases are read live,** from the public release list, on request and with a short
  deadline. A published release says nothing about whether its capabilities exist in this process.
- **The two are separate observations with separate states.** An unavailable one never erases the
  other's known facts. Running against published compares only for stable versions; anything else
  is unknown.
- **A missing stamp is unavailable, never nothing found.** An image that forgot to stamp must not
  make her say, with confidence, that she has no versions; that is a false claim about herself made
  from a wiring gap. A source checkout is unstamped by nature.
- **Release text is reference data,** capped, with truncation marked, and never an instruction. A
  summary covers only the releases actually read and never claims a complete history from one page.
- **Nothing is announced to a room.** A version is told when asked.

The result states follow [tools](tools.md).
