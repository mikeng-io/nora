"""The Dreamer adaptors' prompts and the renderers they share: the note tier,
`<standing>`, and the Cognition session's agenda.

**The package name is misleading and kept deliberately.** It once held the
room-language tier (lexeme scorer, observer, consolidator); none of that
exists. What is here never had anything to do with language: `note_render`
renders `<notes>`, `standing_render` renders `<standing>` for a tag-path
stage, and `cognition_render` builds the Cognition session's prompt (it lives
here, not in `nora.domain`, because it uses the renderers above).

Renaming the package would churn imports across `nora.agent`, `nora.app` and
the layer contract; `nora.infra.sql.language` carries the same note for the
same reason.
"""
