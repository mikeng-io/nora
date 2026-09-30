"""Documents: what Nora publishes to the portal instead of hand-written HTML.

A document is a JSON spec over a closed catalog of components, defined once in
`app/src/lib/documents/catalog.ts` and rendered by the web app (ADR 0020,
`design/artifact/00-design.md`). The files beside this module are GENERATED from
that catalog by `npm run documents:emit`; do not edit them:

- `document.schema.json`  the JSON Schema a document must satisfy.
- `catalog-prompt.md`     the prompt fragment the document-creation skill embeds.
- `corpus.json`           documents that must be accepted and refused, the same
                          verdicts in Zod, ajv and (`tests/unit/test_document_schema.py`)
                          Python.

`check` is the one place a document is judged on Nora's side.
"""
