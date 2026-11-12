# Documents

A document is a small, closed JSON specification Nora writes for a reader. The
portal renders it with its own primitives and theme; it is never model-written
HTML, CSS, JavaScript or a file exported from the computer.

**Status:** built

[Open issues](https://github.com/mikeng-io/nora/issues?q=is%3Aopen+%22design%2Fartifact%2F%22)

## Parts

- **The catalog** defines the components, their meanings and constraints. One
  source feeds the checker, the renderer and the model's catalog guidance.
- **The checker** parses, normalizes, validates and extracts canonical text and
  hashes in Nora's container. Every transformation precedes the judgement that
  stores it.
- **The document store and publish service** own room scope, revisions,
  lifecycle, media references and link resolution. Only checked forms persist.
- **The document tools** let a person publish, read, revise and change state.
  They resolve the current document and base revision rather than trusting
  memory of an earlier turn.
- **The portal** lists, searches, renders and prints documents, exposes media
  under the same read gate, and presents one document as pages, slides and
  history without creating another format.
- **The creation skill and catalog guidance** teach what a finished document is
  for and how to compose it. The catalog itself is the authority for components
  and limits, not a second scenario-specific rulebook.

The subjects are [catalog and checking](catalog.md) and [publication and
lifecycle](lifecycle.md).

## Invariants

- The model supplies intent and structured content, never markup, styles,
  scripts, arbitrary classes or external code. A fixed renderer supplies all of
  that.
- A document exists only when publication returned `ok`. A link is minted from
  the stored document by code; the model never invents one.
- Every stored form is the form that was checked. Canonicalization cannot change
  a valid document into a different valid document or into an invalid one.
- A document is readable by its creator and people named by its room at read
  time. Other viewers and other rooms get the same not-found answer as an
  unknown id, never an existence oracle.
- Revisions are append-only and edits name their base. Restoration creates a
  new revision; it never rewrites what an earlier reader saw.
- Media enters by custody reference, not URL. Bytes and page responses are
  served only when the viewer may read the document and the referenced media.
- A refusal is structured and repairable; a checker failure is unavailable, not
  a pass. A link to a missing document is not treated as a saved document.
