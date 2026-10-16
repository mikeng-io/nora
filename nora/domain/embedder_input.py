"""Qwen3-VL-Embedding-2B's input format: the ONE formatter (design/unified-memory/
65-build-step-3-vectors.md §0).

The model reads a chat sequence its own files define (Qwen/Qwen3-VL-Embedding-2B
at `9f2f7e71`): a system turn holding an instruction, the user turn, the
generation prompt, then `<|endoftext|>`, the token that is pooled. A QUERY
carries the vendor's retrieval instruction; a document or an image the
default. The text is NFC-normalised first, which is the official
tokenizer's normaliser; llama-server applies none.

llama-server appends `<|endoftext|>` itself (`--pooling last`), so a prompt
sent to it stops at `assistant\\n`: writing the end token too would pool a
doubled one. `official_sequence` is the whole sequence the model reads,
end token included, which is what the bake-off checks a server's tokens
against.

Production (`nora.infra.embedder`) and the bake-off adapter
(`evals.retrieval_bakeoff.qwen_llama`) both format through this module, so
the two cannot drift (`tests/unit/test_embedder.py` holds them equal). It is
in the domain because `evals/` may import only the domain of the runtime
packages, and `nora.infra` may never import `evals/`.

`FORMAT_VERSION` is part of every stored vector's model fingerprint: a change
to anything here that changes a prompt bumps it, and every vector embedded
under the old format then reads as another model's.
"""

from __future__ import annotations

import unicodedata
from typing import Literal

__all__ = [
    "DOCUMENT_INSTRUCTION",
    "EOT",
    "FORMAT_VERSION",
    "QUERY_INSTRUCTION",
    "TAIL",
    "Role",
    "head",
    "image_prompt",
    "official_sequence",
    "text_prompt",
]

#: Bump when a prompt this module renders changes (module docstring), or
#: what an image is sent as (`nora.infra.embedder.MAX_SEND_PIXELS`): 2 is the
#: 256-token image bound, so images embedded at 1920 tokens' worth read as
#: another model's and are re-embedded.
FORMAT_VERSION = 2

#: The default instruction: documents and images.
DOCUMENT_INSTRUCTION = "Represent the user's input."
#: The retrieval query instruction (the vendor's README example and MMEB t2i eval).
QUERY_INSTRUCTION = "Retrieve images or text relevant to the user's query."

EOT = "<|endoftext|>"
TAIL = "<|im_end|>\n<|im_start|>assistant\n"

Role = Literal["query", "document", "image"]


def head(role: Role | str) -> str:
    """The system turn and the opening of the user turn for `role`."""
    instruction = {
        "query": QUERY_INSTRUCTION, "document": DOCUMENT_INSTRUCTION,
        "image": DOCUMENT_INSTRUCTION,
    }[role]
    return "<|im_start|>system\n" + instruction + "<|im_end|>\n<|im_start|>user\n"


def text_prompt(text: str, role: Role | str) -> str:
    """What llama-server is sent for a text: the server appends the end token."""
    return head(role) + unicodedata.normalize("NFC", text) + TAIL


def image_prompt(marker: str) -> str:
    """What llama-server is sent for an image: `marker` is the server's
    multimodal marker (its /props `media_marker`), where the image goes."""
    return head("image") + marker + TAIL


def official_sequence(text: str, role: Role | str) -> str:
    """The whole sequence the model reads for a text, end token included."""
    return head(role) + text + TAIL + EOT
