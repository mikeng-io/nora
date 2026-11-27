"""The words the wake's `agenda`, `reads` and `notes` blocks teach the model.

`nora/app/wake_pass.py` declares these block types for the wake prompt, and the model reads
their description and field glosses as written. Each phrase below is a sentence a reader
needs; losing one leaves a field or a line shape unexplained.
"""

import pytest

from nora.app import wake_pass
from nora.domain.block_types import BLOCK_TYPES

PHRASES = [
    ("agenda", "its sections run Decided, Open, Owed, Proposed, Ruled out. An Owed"),
    ("agenda", "marked `own` holds what you sent. Chat data"),
    ("agenda", "counted. A rollup is a fact"),
    ("agenda", "An Owed line is `<name> -> <what> -> <due> [<ids>]`:"),
    ("agenda", "A Ruled out line is `<option>: <reason> (<names>) [<ids>]`."),
    ("agenda", "citing them, as the `n` and `from` fields say;"),
    ("agenda", "use this exact name in parentheses before cited ids"),
    ("agenda", "`yes` if its words changed after they were said"),
    ("agenda", "the row's citation id (negative for non-message records)"),
    ("notes", "its handle in this list; real note id when id_kind is real"),
    ("reads", "as `platform:conversation_id` beside its label. Code wrote it;"),
]


def _text(name: str) -> str:
    block = BLOCK_TYPES[name]
    parts = [block.description, *(gloss for _, gloss in block.annotations)]
    return " ".join(" ".join(parts).split())


def test_the_wake_declares_the_blocks_whose_words_are_pinned_here():
    declared = {block.name for block in wake_pass._BLOCKS}
    assert {"agenda", "reads"} <= declared


@pytest.mark.parametrize(("name", "phrase"), PHRASES)
def test_the_block_type_keeps_the_sentence_a_reader_needs(name, phrase):
    assert phrase in _text(name)
