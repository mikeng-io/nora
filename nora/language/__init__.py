"""The Dreamer adaptors' prompts and the renderers they share: the note tier,
`<standing>`, and the Cognition session's agenda.

**The package name is now wrong and is kept anyway, deliberately.** It was
"language and lexicon tools" and held the room-language tier — the lexeme
scorer, the observer that made the one LLM call, the consolidator that
persisted it. That tier is deleted. What is left never had anything to do with
language: `note_candidates`/`note_curator` are the note tier's two Dreamer
adaptors, `note_render` renders `<notes>`, and
`standing_render` renders `<standing>` for a tag-path stage, and
`cognition_render` builds the Cognition session's prompt (it lives here, not in
`nora.domain`, because it uses the renderers above).

Renaming the package is a separate change with its own import churn across
`nora.agent`, `nora.app` and the layer contract, and doing it inside the
deletion would make the deletion unreviewable. Recorded rather than done —
`nora.infra.sql.language` carries the same note for the same reason.
"""
