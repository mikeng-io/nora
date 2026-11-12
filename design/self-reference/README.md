# Self reference

Nora has an authoritative visual identity and a canonical image she can use when
a requested picture includes her. Appearance is grounded in owned references,
not inferred from whichever person or meme happens to be in the transcript.

**Status:** built

[Open issues](https://github.com/mikeng-io/nora/issues?q=is%3Aopen+%22design%2Fself-reference%2F%22)

## Parts

- **Identity and form** define one Nora across her native Mini representation
  and approved adult representation, including the rendering treatment each
  surface uses. [Identity](identity.md) holds the design-level anchors; the
  visual system owns its exact art direction.
- **The canonical self asset** is an operator-provided image in Nora's media
  custody, marked with an exact self role and pinned by its full content handle.
- **The image-generation skill** decides whether Nora belongs in the picture,
  which supplied images show which people, and when references are appropriate.
  [References](references.md) defines selection and resolver precedence without
  duplicating the skill's prompt text.
- **Modeling references** support possible modeled and animated forms without
  pretending that an illustration is a mesh, a turnaround is calibrated or a rig
  has passed. [Modeling](modeling.md) keeps those evidence levels separate.

## Terms and promises

Mini is Nora's ordinary internet-native visual representation, not a mascot
standing in for a hidden human. The adult form is another representation of the
same identity, not a more real Nora. Voice-only and abstract surfaces need no
visual embodiment.

Her canonical reference is used only when she is actually in the requested
picture. It preserves identity, not a blank composition to copy, and it never
becomes a house style for other subjects. A user-supplied person is not Nora;
when more than one image could be the requested person, she asks rather than
guesses.

Only the exact canonical self role crosses conversation custody. This exception
does not apply to an operator, another character, a user photo or a URL. It
changes which held blob may resolve, not which provider may be used or what
room content may leave the room.

## Change and failure

Artwork, providers, reference limits and derived site or social assets may
change together through the visual-asset inventory. The canonical bytes,
appearance summary, runtime handle and derivatives must move as one reviewed
identity change; repository synchronization alone is not media ingest or
external publication.

Missing or ambiguous references resolve to nothing rather than a guessed blob.
If reference images are disabled, Nora can describe her anchors instead of
smuggling the canonical image through another path. A failed reference or media
read is unavailable, never proof that an image shows a particular person.
