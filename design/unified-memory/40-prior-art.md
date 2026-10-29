# Prior art, October 2026: identity, scope, taxonomy, graph

Status: **record**, 2026-10-14. This extends [cognition-loop/80](../cognition-loop/80-prior-art.md),
which read Honcho, Graphiti/Zep, Letta, Generative Agents and Mem0 from source
on 2026-09-28. This node adds what that one did not ask: how systems form
**identity**, whether memory can be about **non-humans and groups**, whether
**one interface** spans every memory type, and how **graph and hybrid
search** combine. Two research passes on 2026-10-14 read primary sources
(repositories, documentation, papers). Vendor feature lists are marked as
such, and "not found" means not found.

## 1. Agent-memory frameworks

| system | identity / profile | non-human and group scope | one interface? | graph and search |
|---|---|---|---|---|
| **Honcho** 3.3.0 (2026-10-09) | peer card: `list[str]`, max 40, each `IDENTITY:` / `ATTRIBUTE:` / `RELATIONSHIP:` / `INSTRUCTION:`; stable facts only, no behaviour; `INSTRUCTION` explicit-only; rewritten whole by the dream's deduction and card specialists ([specialists.py](https://github.com/plastic-labs/honcho/blob/main/src/dreamer/specialists.py)) | a peer is any participant: human, agent, team; representations per (observer, observed); scopes are named session sets enforced server-side ([scopes](https://github.com/plastic-labs/honcho/blob/main/docs/v3/documentation/features/advanced/scopes.mdx)) | peer chat and representation over conclusions and messages | no graph; `source_ids` lineage; still **no abductive level in code** |
| **Mem0** (OSS v3) | none: flat facts, ADD-only | `user_id` / `agent_id` / `app_id` / `run_id`; group chat needs one `add()` per participant or facts land on the wrong entity ([group chat](https://github.com/mem0ai/mem0/blob/main/docs/platform/features/group-chat.mdx)) | one search: semantic + BM25 + entities | OSS graph **removed**, entities moved to a vector collection ([migration](https://github.com/mem0ai/mem0/blob/main/docs/migration/oss-v2-to-v3.mdx)) |
| **Letta** | `human` and `persona` blocks (`label`, `value`, `limit`, `read_only`), whole-value replace | blocks shared across agents | different per tier; git-backed memory files | none |
| **Zep / Graphiti** | user summary steered by ≤5 instructions | per-user and shared context graphs | one graph search | Neo4j / FalkorDB / Kuzu / Neptune; cosine + BM25 + BFS, reranked by RRF, MMR or cross-encoder ([search_config.py](https://github.com/getzep/graphiti/blob/main/graphiti_core/search/search_config.py)); **bi-temporal** facts |
| **Memobase** | `topic` / `sub_topics` slots with per-slot update rules | per user only | separate APIs | none; Postgres + pgvector |
| **MIRIX** | core blocks | ids per user, agent, org | six types (core, episodic, semantic, procedural, resource, knowledge vault) under a meta-agent ([arXiv](https://arxiv.org/abs/2507.07957)) | none |
| **MemOS** | `explicit_preference` / `implicit_preference` | MemCube per user, project or agent (README claim) | unified API | Neo4j + Qdrant: vector seeds, then graph-hop expansion |
| **Cognee** | none; entities and ontology | principals, roles, tenants, ACL rows | `remember` / `recall` / `improve` / `forget`; loaders for text, image, audio, video | graph + vector; `FactConflict` nodes marked current, superseded, disputed ([improve](https://docs.cognee.ai/core-concepts/main-operations/improve)) |
| **A-MEM** | none | single store | one note type | per-note links an LLM chooses ([arXiv](https://arxiv.org/abs/2502.12110)) |
| **LangMem** | one Pydantic profile document, or a collection | namespace tuples | semantic, episodic, procedural | none |
| **Supermemory** | profile with `static[]` / `dynamic[]` per namespace, "usually a user, can be anything" | namespace isolation | memories and documents in one hybrid query | relations `updates` / `extends` / `derives`; store not stated |
| **Hindsight** v1.0.0 (2026-10-13) | "mental models": standing questions re-answered in the background | banks, tag-scoped callers | one `recall` over world facts, experiences, observations, mental models | **Postgres**; semantic + BM25 + graph + temporal in parallel, fused by RRF and a cross-encoder ([repository](https://github.com/vectorize-io/hindsight)) |

## 2. Products and companions

| product | identity | stated vs inferred | groups |
|---|---|---|---|
| **ChatGPT** | reverse-engineered: saved facts, recent conversations, and a dense inferred profile ("Assistant Response Preferences", "Helpful User Insights", …) ([Willison](https://simonwillison.net/2025/May/21/chatgpt-new-memory/)) | inferred layer long hidden; now shown as a summary | group chats use **no** personal memory and create none ([OpenAI](https://openai.com/index/group-chats-in-chatgpt/)) |
| **Claude** | individual topics (role and projects, people and places, style, preferences), per project ([help](https://support.claude.com/en/articles/11817273-use-claude-s-chat-search-and-memory-to-build-on-previous-context)) | every topic visible and editable; some categories never saved | per project |
| **Copilot** | saved memories and inferred chat-history details as separate controls ([Learn](https://learn.microsoft.com/microsoft-365/copilot/copilot-personalization-memory)) | separate | none found |
| **Kindroid** | persistent backstory and directives; auto-written "Learned Context" with **per-section character caps**, oldest and least important evicted; journal entries proposed at most one a day **for the user to approve** ([docs](https://kindroid.ai/v2/docs/memory/)) | the user sees which memories fed a reply and can demote them | group memory consolidated per member, with separate switches for forming and recalling |
| **Character.AI**, **Replika**, **Nomi** | backstory plus auto-captured facts (third-party reports) | an editable visible layer over a hidden one | not found |

## 3. Research

- **Generative Agents**: reflection fires on accumulated importance, and
  reflections cite their evidence. Removing reflection measurably lowered
  believability ([arXiv](https://arxiv.org/html/2304.03442)).
- **PersonaMem**: frontier models answer about 50% on evolving profiles.
  Tracking change, not recall, is the weak point ([arXiv](https://arxiv.org/pdf/2504.14225)).
- **GroupMemBench**, the first multi-party memory benchmark: the best system
  scores 46%, a BM25 baseline 43%. Ingestion erases who said what; Mem0
  scores 4.7% on knowledge updates ([arXiv](https://arxiv.org/html/2605.14498)).
- **EverMemBench**: multi-hop recall collapses across speakers and groups,
  and communication style is the hardest profile field ([arXiv](https://arxiv.org/html/2602.01313v1)).
- **Privacy scoping review**: in persistent multi-user memory, one person's
  disclosure surfaces in another's session ([arXiv](https://arxiv.org/html/2609.14062)).
- **No work found** that stores a group's identity or culture as its own
  artefact. Group-norm benchmarks (SCENE, LoSoNA) infer norms; they do not
  keep them.
- **Personalisation and sycophancy**: a condensed stored profile had the
  largest effect on agreeableness ([arXiv](https://arxiv.org/html/2509.12517v2),
  via a search summary).

## 4. What this bundle takes

Re-pointed 2026-10-15: node 30's proposals were superseded by
[35](35-persona.md) and [45](45-persona-adaptor.md), which are where each
taken idea now lives.

> 2026-10-21 (18 / ADR 0026): the identity/behaviour vocabulary below is historical; [ADR 0026](../../docs/adr/0026-the-character-tier-names-its-entry-parts-profile-traits-hypotheses.md) names Character entries’ parts profile, traits and hypotheses. [18](18-extraction.md) places source statements in Record readings and keeps the entry as the bounded card.

| taken | from | where |
|---|---|---|
| identity as a separate part from behaviour; stable only; capped; instructions never inferred | Honcho 3.3 peer card | [35](35-persona.md) §1 (a part of Persona, not a separate record), [45](45-persona-adaptor.md) §2–§3 |
| any participant can be the subject, agents included | Honcho | 30 §6, under Persona (35) |
| conflicts as rows marked current / superseded / disputed / outranked | Cognee | [45](45-persona-adaptor.md) §3 |
| keep the speech act; never record exploration as fact | ChatGPT's failure | 45 §3 |
| per-part caps with a stated eviction | Kindroid | 45 §2 |
| show which memory fed a reply; let the subject correct it | Kindroid, Claude | 30 §7 «Seeing it», 35 §2 |
| semantic + lexical + graph, fused by RRF, then an optional reranker, all in Postgres | Hindsight, Graphiti | [25](25-memory-graph.md) §4 |
| scope before ranking, and test every read path under every scope | Honcho scopes; governed-memory reviews | 25 §4 |
| media is text plus a pointer back to the original | every system checked | [15](15-memory-map.md) |
| one provenance model with separate encodings and separate validity rules; entity, activity, agent; a binary edge qualified by a side record; plan, revision, quotation (added 2026-10-29) | W3C PROV (PROV-DM, PROV-O, PROV-CONSTRAINTS) | [28](28-provenance.md) |

## 5. What this bundle refuses

> 2026-10-28 (ratified, [27](27-the-memory-is-a-graph.md)): the first refusal is answered rather than reversed. Relations may be model-proposed, but each must carry relation_evidence rows citing what the session fetched; an edge with no evidence is not written. See [27](27-the-memory-is-a-graph.md#2-what-this-supersedes-and-why-the-old-reasons-are-answered).

- **Model-extracted triples as graph edges** (Graphiti, Cognee, MemOS). The
  edge says nothing about its evidence. cognition-loop/80 refused this already;
  Mem0's removal of its graph is corroboration.
- **Switching memory off in groups** (ChatGPT). Nora lives in groups; the
  answer is scope on every item, not absence.
- **A hidden inferred layer** (ChatGPT, Replika). Every line Nora holds about
  a person is visible to that person on the portal.
- **Whole-value replacement of a profile** (Letta, LangMem). It loses the
  history a correction needs; Nora keeps a revision chain.

## 6. Where Nora is ahead, and where it is not

**Ahead.** Collective subjects (a room's and a unit's Persona, its identity
and character parts, [35](35-persona.md), [45](45-persona-adaptor.md)) where
no shipped system stores a group's identity. Reasoning modes including abduction where Honcho has it in its docs
only. Citations to Record rows on every derived line.

**Not ahead.** GroupMemBench's finding applies to Nora: speaker attribution
and knowledge updates are where multi-party memory fails. Nora's Record keeps
the speaker on every row. Whether its derivations keep it is untested, and
belongs in the eval.
