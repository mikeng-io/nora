"""`OllamaEmbedder` — pass 4's one outbound call (`25` §5).

**What is embedded is the RENDERED TRIPLE, not the object string** —
`subject │ predicate │ object`, measured in `23-retrieval.md` §6.2 to retrieve
correctly across languages, and already the context-prefix shape the prior art
constructs by hand.

**R-27 on the service.** A batch that fails is `unavailable` — never
`nothing_found`, and never a partial list silently shorter than its input. An
embedder that returned three vectors for four triples would write three rows and
leave the fourth looking un-embeddable forever, which is a `nothing_found`
wearing a shape nobody would check.

**Local, and that is the whole cost argument.** `bge-m3` runs on the host's own
Ollama; there is no per-call price and no external egress, so re-embedding a
changed row is cheap enough that the fingerprint can be conservative.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Any, Literal, get_args

import aiohttp

log = logging.getLogger(__name__)

#: R-27, on the embedder. `nothing_found` is a real state — an empty batch —
#: and is deliberately distinct from a service that could not be reached.
EmbedStatus = Literal["ok", "nothing_found", "unavailable"]
EMBED_STATUSES: frozenset[str] = frozenset(get_args(EmbedStatus))

#: MEASURED against the live endpoint on 2026-08-28, not read off a model card:
#: `bge-m3:latest` returns 1024 floats. The schema's `vector(N)` has to agree
#: with this exactly, and a column built against a guessed width fails on the
#: first write rather than at deploy.
BGE_M3_DIMENSIONS = 1024


@dataclass(frozen=True)
class EmbedResult:
    vectors: tuple[tuple[float, ...], ...]
    status: EmbedStatus


def fingerprint(*, model: str, text: str) -> str:
    """What was embedded, by what — the re-embedding predicate (`25` §5).

    Covers **model identity and the exact text**. The predicate downstream is
    `fingerprint != current`, and one comparison then catches a model swap and
    a changed row with no second mechanism and no ordering trap.

    > **A correction to `25` §5, driven by code.** That section justifies
    > hashing the text on the grounds that *"promotion rewrites the triple's
    > rendered text, so a promoted row's stored vector is stale the moment pass
    > 3 touches it"* — and since migration 0039 that is no longer true.
    > Promotion adds `object_entity_id` and leaves `object_literal` alone
    > precisely so the words the room used survive resolution, which is the
    > property `23` §6.2 depends on. The rendered triple is therefore UNCHANGED
    > by promotion, and "pass 3 before pass 4, always" is no longer load-bearing
    > for that reason.
    >
    > Hashing the text stays right for the reasons that remain: a supersession's
    > successor is a different row with different words, and an operator edit or
    > a future writer can change one. What changes is that this is now a
    > cheap-and-general guard rather than the fix for a specific known rewrite.
    """
    digest = hashlib.sha256()
    digest.update(model.encode("utf-8"))
    digest.update(b"\\x00")
    digest.update(text.encode("utf-8"))
    return digest.hexdigest()


def render_for_embedding(
    *, subject: str, predicate: str, obj: str,
) -> str:
    """`subject │ predicate │ object` — ONE renderer, so the text that is
    embedded and the text that is fingerprinted cannot drift apart.

    Not the prompt's rendering: that one carries the warrant, the source
    excerpt and now the dispute marker, none of which are the claim. Embedding
    those would make two facts with the same content but different sources land
    in different places.
    """
    return f"{subject} │ {predicate} │ {obj}"


class OllamaEmbedder:
    """`bge-m3` over the host's Ollama, batched.

    `__init__` takes the endpoint and the model rather than reading config, so
    a test can point it at a stub and production points it at
    `[facts] embedding_url`.
    """

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout_seconds: float = 30.0,
        dimensions: int = BGE_M3_DIMENSIONS,
        metrics: Any = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout_seconds
        self._dimensions = dimensions
        self._metrics = metrics

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, texts: tuple[str, ...]) -> EmbedResult:
        """One batch. `ok` only when the reply is complete and correctly
        shaped.

        **A short or mis-shaped batch is `unavailable`, not a partial
        success.** Writing three vectors for four triples leaves the fourth
        looking un-embeddable forever — a `nothing_found` wearing a shape
        nobody would check — so the whole batch is refused and retried next
        pass, which costs one local call.
        """
        if not texts:
            self._count("nothing_found")
            return EmbedResult(vectors=(), status="nothing_found")
        try:
            timeout = aiohttp.ClientTimeout(total=self._timeout)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.post(
                    f"{self._base_url}/api/embed",
                    json={"model": self._model, "input": list(texts)},
                ) as response,
            ):
                response.raise_for_status()
                payload = await response.json()
        except Exception:
            log.exception("embedding unavailable from %s", self._base_url)
            self._count("unavailable")
            return EmbedResult(vectors=(), status="unavailable")

        raw = payload.get("embeddings")
        if not isinstance(raw, list) or len(raw) != len(texts):
            log.warning(
                "embedding returned %s vectors for %d texts",
                len(raw) if isinstance(raw, list) else type(raw).__name__,
                len(texts),
            )
            self._count("unavailable")
            return EmbedResult(vectors=(), status="unavailable")
        vectors = []
        for vec in raw:
            if not isinstance(vec, list) or len(vec) != self._dimensions:
                log.warning(
                    "embedding width %s, expected %d",
                    len(vec) if isinstance(vec, list) else type(vec).__name__,
                    self._dimensions,
                )
                self._count("unavailable")
                return EmbedResult(vectors=(), status="unavailable")
            vectors.append(tuple(float(x) for x in vec))
        self._count("ok")
        return EmbedResult(vectors=tuple(vectors), status="ok")

    def _count(self, status: EmbedStatus) -> None:
        if status not in EMBED_STATUSES:
            raise ValueError(
                f"embed status must be one of {sorted(EMBED_STATUSES)}, "
                f"got {status!r}"
            )
        if self._metrics is not None:
            self._metrics.inc("nora_fact_embed_total", status=status)
