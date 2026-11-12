# Prompting contract

A video prompt connects creative intent to the exact media passed with the
request. It must not invent what a reference contains or describe a different
subject, setting or opening frame. When interpretation depends on held media,
Nora reads that media before writing the prompt.

## References

References are named by media kind and connection order, independently for
photos, clips and audio. At its first meaningful use, each reference says what
it supplies: subject identity, setting, motion, camera, voice or sound. This
keeps a multi-reference request unambiguous.

A referenced subject is described consistently with its picture, including the
visible features that preserve identity. The prompt states that identity stays
the same while allowing the requested new action or setting. A clip's look is
not assumed to be irrelevant: the prompt distinguishes the motion or camera to
borrow from visual characteristics that should not transfer.

Nora's canonical picture is a reference only when Nora is meant to appear. It
is not attached by default. A room member's picture represents that member,
never Nora. When several pictures could identify the intended person, the
request needs clarification rather than a guess.

## First frame

First-frame mode means the supplied photograph itself comes alive. The prompt
describes the motion, camera and sound that follow that frame rather than
restating its contents. Subjects are identified by their position in the frame.
A new scene or an action requiring a different composition uses reference mode
instead.

The two modes are never blended. This preserves the distinction between
keeping an opening image exact and using media as evidence for a new scene.

## Scene, camera and sound

A short clip asks for one scene and one clear physical action in temporal
order. Concrete visible detail is preferable to generic quality adjectives;
additional subjects and events are avoided unless the request depends on them.
The prompt uses the deployment's chosen prompting language while preserving
dialogue and lyrics in their original language.

Camera movement is a natural-language sentence with direction, size and speed,
or an explicit static shot. Stacked camera labels are avoided because they do
not express how the move unfolds.

Spoken and sung words are written verbatim, attributed to a subject and marked
with their language. Background sound is described explicitly, including the
absence of music when requested. Text that must appear on screen is quoted
verbatim; otherwise the prompt says there is no text. Language fidelity that
the provider does not promise is disclosed at handoff rather than implied by
the prompt.

## Responsibility

The runtime skill turns these rules into model-facing guidance. The backend
owns provider syntax and limits; the prompt does not duplicate them. Provider
prompt-enhancement APIs and negative-prompt conventions from other model
families are not assumed to apply to this design.
