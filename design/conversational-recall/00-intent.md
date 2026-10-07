# Conversational recall

Status: **proposed**, recorded 2026-10-07. Implementation remains tracked in
[issue 733](https://github.com/mikeng-io/nora/issues/733). Saving this bundle
does not approve a model migration or change runtime behaviour. No decision
number is allocated: there is no new landed behavioural rule to enforce.

The missing connection is between a conversational question and the evidence
that answers it. Embedding can find related media; a reader can describe it;
neither alone establishes which occasion the user means, who participated,
whether a plan changed, or whether Nora ever captured the relevant source.

The proposed conceptual unit is **a remembered experience retrievable with its
evidence**. This does not require a new persistent experience table. First use
the existing message, media, event and memory surfaces, retaining their source
links and their different meanings.

## Three journeys

These are synthetic examples, not records of actual meals or plans.

| User question | What must be resolved | Evidence needed |
|---|---|---|
| “What do you see in that image?” | The exact attached or previously referenced object | Its stored reading, or the original for a specific detail |
| “Do you remember where we're going?” | Which outing, who “we” refers to, and the current agreement | Active plan state and recent corrections, with older proposals distinguished |
| “What did we have for dinner at that time?” | Date/occasion and participants | Relevant messages and media, plus evidence connecting the pictured food to that occasion and those people |

A social post showing sushi can support “the post around that date shows
sushi.” It does not by itself prove that the author ate it, that everyone in
the conversation did, or that the meal occurred at publication time. Unknown
relationships must remain unknown.

## Scope and direction

The direction is shared reference resolution, permitted evidence retrieval,
and grounded answer assembly over existing stores. Exact references and current
canonical state take precedence over fuzzy similarity. A visual index is a
candidate addition where the existing text path misses relevant visual content.

This holds unless the existing tools, given sufficient context, already pass
the three journeys reliably with acceptable cost and source grounding. In that
case improve their assembly and stop; a new retrieval layer need not exist.
WeMM is a model candidate, not an architectural prerequisite.

The bundle preserves [current findings](10-findings.md), the
[proposed coordination](20-orchestration.md), and the
[measurement plan and unresolved questions](30-measurement.md). It neither
claims all files have been indexed nor revives the deprecated knowledge tier.
