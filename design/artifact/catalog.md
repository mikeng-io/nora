# Catalog and checking

## One source of meaning

The catalog defines the document envelope and the closed component vocabulary:
structure, prose, data, sources, media and presentation components. Each has a
purpose, typed inputs and a renderer. A sheet or brief is content to transform,
not a template to imitate.

The same source generates the machine schema, model-facing catalog and renderer
registry. Drift between these is a build failure. New components are admitted
only when a real document requires them and they have a schema, renderer, demo,
negative fixture and accessibility coverage.

A document is a nested tree, not a graph of arbitrary ids. Its title comes from
the Page, and its content remains source text that can be searched without
re-interpretation. Unknown component types, props and structural keys are
refused.

## Security boundary

The catalog allows no raw HTML, style strings, classes, actions, arbitrary URLs,
scripts, embedded code or custom diagram configuration. Markdown is content,
not an escape into active markup. Images and links are constrained to the
schemes and custody the document needs.

Mermaid and other rich figures are passive by construction. Source allowlists
and output checks prevent external requests, event handlers, HTML labels and
active SVG. Rendering happens within a fixed component frame with accessibility
fallbacks and resource bounds; a parser or diagram failure shows a safe
fallback rather than leaking a browser error or freezing the portal.

A document is data, not a program. Interactive behavior is limited to local
presentation state such as navigation and device-local checklist ticks; it
never creates shared state or writes back to Nora.

## Checking pipeline

Checking runs in Nora's container, not in an untrusted workspace. It proceeds
through parsing, structural bounds, character and encoding checks, safe
normalization, schema validation and domain rules. Every mutation happens
before the corresponding validation, so the stored form and the judged form
cannot diverge.

The pipeline bounds nodes, depth, width, text and media. It handles duplicate
keys, non-finite numbers, control and bidi characters, malformed encodings,
recursive structures and hostile regular-expression inputs without turning
resource exhaustion into a pass. Refusals point to a small, useful location in
document terms rather than dumping hostile content.

On success it returns canonical JSON, normalized title and content, searchable
text and content hashes. On refusal it returns structured findings; on an
instrument failure it returns error. A stored document is rechecked whenever a
revision or restored revision is published.

## Catalog and model guidance

The catalog returned by the publication service is the one home for component
rules, finished-document expectations and refusal repair. A model can read it
before composing instead of paying for the full instruction on every turn.
The skill teaches when a document is warranted, how to use evidence and how to
revise safely; it does not duplicate component details.

A catalog read creates no document and no link. Publication returns a document
only on `ok`, and an empty reply cannot become a publication claim by
implication.
