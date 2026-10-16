"""Documents: what Nora publishes to the portal instead of hand-written HTML.

A document is a JSON spec over a closed catalog of components, defined once in
`app/src/lib/documents/catalog.ts` and rendered by the web app (ADR 0020).
The files beside this module are GENERATED from that catalog by
`npm run documents:emit`; do not edit them:

- `document.schema.json`  the JSON Schema a document must satisfy.
- `catalog-prompt.md`     the prompt `document_publish` returns when it is called with no spec.
- `corpus.json`           documents that must be accepted and refused, the same
                          verdicts in Zod, ajv and (`tests/unit/test_document_schema.py`)
                          Python.

`check` is the one place a document is judged on Nora's side.
"""

from importlib import resources


def catalog_prompt() -> str:
    """The generated catalog prompt: what a model is told to write a document against."""
    return resources.files(__name__).joinpath("catalog-prompt.md").read_text("utf-8")
