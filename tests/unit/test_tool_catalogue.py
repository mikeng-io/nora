"""`ToolRegistry.tool_catalogue()` — the index the model selects from.

The catalogue is the selection half of `design/tool-catalogue/`: one line per
tool, every tool, resident in every prompt, so that the full schemas need not
be. These tests are the line discipline that keeps it an INDEX. Without them
it rots back into a second copy of the descriptions, which is precisely what
DEC-150 deleted the generated capability block for.
"""

from __future__ import annotations

import tiktoken

from nora.agent.registry import ToolRegistry
from nora.agent.surface import DREAMER_ONLY_TOOLS, ToolScope
from nora.agent.tools import HOT_TOOLS, LOADER_TOOL, NATIVE_TOOLS, on_memory_surface

ENC = tiktoken.get_encoding("cl100k_base")

# The block ceiling. This is gate 2a of `design/tool-catalogue/05-measurement.md`
# and it is the one that actually protects the change: the catalogue is paid on
# EVERY call of EVERY turn, so aggregate growth is what turns a 1k index back
# into a 22k schema dump. Measured at 1,021 when this landed — 27% of headroom,
# deliberately not sized to the current value.
#
# RE-SET 2026-09-23, 1,400 -> 2,000, because the LINE changed definition, not
# because lines drifted. It was each tool's first sentence — the «what» alone —
# and a deferred turn judging from «Work out a number exactly» skipped
# `calculate` (tool_selection arm B). It is now ADR 0007's L1 paragraph, what +
# WHEN, pinned by `test_the_opening_paragraph_is_an_l1`; that measures 1,702.
# Still an index: 7.7% of the 22,130 tokens of schemas it replaces. Headroom
# ~18%, again not sized to the current value.
#
# RE-SET 2026-09-29, 2,000 -> 2,100, for a NEW TOOL, not for drift:
# `record_query` (design/cognition-loop/15-map.md §4) adds one line of 35
# tokens to a block measured at exactly 2,000 over 63 lines. With it, 64
# lines at a mean of 30.9, under `MEAN_CEILING` below, which is what binds
# per-line drift; no existing line changed. The headroom the 2026-09-23 re-set
# left had been spent by tools added since, one line each. 2,100 holds this
# line and roughly two more; the next tool that needs more re-sets this with
# its own line count, never a line rewritten to fit.
#
# RE-SET 2026-09-30, 2,100 -> 2,250, for FOUR NEW TOOLS, not for drift:
# Nora's computer (design/sandbox-runtime §4, DEC-243) adds `computer_run`
# (39 tokens), `computer_read` (30), `computer_write` (31) and
# `computer_edit` (29), 132 tokens on a block measured at 2,045 over 64
# lines, total 2,177 over 68 at a mean of 31.1, under `MEAN_CEILING`. No
# existing line changed. 2,250 holds these and roughly two more lines.
#
# RE-SET 2026-10-01, 2,250 -> 2,300, for TWO NEW TOOLS, not for drift: the
# Cognition session's `rollup_read` (38 tokens) and `standing_read` (26) on top
# of master's block (2,216 over 69 lines): total 2,280 over 71 at a
# mean of 32.1, under `MEAN_CEILING`. No existing line changed. Both are
# Dreamer-only (`DREAMER_LOOP_TOOLS`): `ToolScope.admits` keeps them off every
# tag turn's tools and catalogue, so a tag turn pays NOTHING for their lines.
# They were counted only because `ToolRegistry.tool_catalogue()` lists every
# registered tool (`test_every_registered_tool_has_a_catalogue_line`), which is
# not what any turn is shown. (The sentence that stood here, «a tag turn pays
# their lines», was wrong, and raised the ceiling for a cost nobody pays.)
#
# RE-SET 2026-10-01 again, 2,300 -> 2,400, for TWO MORE NEW TOOLS, not for
# drift: S3a (design/sandbox-runtime §6, §7) adds `computer_export` (32
# tokens) and `computer_import` (28) on top of the two reads above: total
# 2,342 over 73 lines at a mean of 31.1. No existing line changed. The artifact tool's
# line (38) left in S3b, which gave some of this back.
#
# RE-SET 2026-10-01 a third time, 2,400 -> 2,500, for TWO NEW TOOLS, from the
# MEASURED block, not raised blindly: `document_publish` (35 tokens) and
# `document_read` (34), design/artifact §26, on master's 2,344 over 73 lines:
# total 2,413 over 75 at a mean of 32.2, under `MEAN_CEILING`. No existing line
# changed. Both ship withheld (`[documents] enabled = false`), so a live turn
# pays nothing for them until the portal pages exist. 2,500 holds these and
# roughly two more lines.
#
# RE-SET 2026-10-09, 2,500 -> 2,600, for ONE NEW TOOL, from the MEASURED block:
# `video_create` (25 tokens, design/video-generation, DEC-305) on master's 2,485
# over 77 Discord lines: total 2,510 over 78. The line was cut from 35 to 25
# before this re-set; no existing line changed. It ships withheld
# (`[video_gen] enabled = false`), so a live turn pays nothing for it until the
# operator turns it on. 2,600 holds it and roughly three more lines.
#
# MEASURED 2026-10-01 against what a tag turn is actually shown (fix PR C, review
# of 2026-09-30, minor 3): the ceiling is asserted over the LARGEST PER-PLATFORM
# tag catalogue (`_tag_catalogue`: the registered tools `ToolScope` admits for
# a tag turn, every optional tool wired). That is Discord's, 2,310 tokens over
# 72 lines; Signal, WhatsApp and Instagram are 2,150 over 66. 2,250 does NOT hold
# Discord's (by 60), so it is not restored; 2,500 stays, which is the ceiling
# from the row above and leaves 190 over the real block, about five lines.
BLOCK_CEILING = 2_600

#: The platforms a tag turn runs on. The catalogue differs only by the tools
#: scoped to one (`DISCORD_ONLY_TOOLS`) and by the Dreamer-only ones, which no
#: tag turn admits.
TAG_PLATFORMS = ("signal", "discord", "whatsapp", "instagram")

# The per-line ceiling, and it was REVISED after measurement. The bundle
# asserted 20 from an estimate; rendering the real thing put eleven lines
# between 21 and 31, and reading them showed the lines were right and the
# number was wrong — the extra tokens ARE the discriminator in exactly the
# pairs that need one (`search_memory` «in this group's history» against
# `search_memory_workspace` «across EVERY room»; `create_event` «a NEW
# gathering» against `update_event` «ALREADY proposed»). Cutting those to fit
# would have removed the reason the line exists.
#
# So this is set where it catches the failure it is FOR — a line that has
# become a description — rather than where it forces cosmetic surgery on lines
# doing real work. A description is 100+ tokens; nothing legitimate is near 40.
# The block ceiling and the mean below are what bind ordinary drift.
LINE_CEILING = 40
# 25 -> 33 on the same 2026-09-23 re-definition: a when-clause costs ~8-12
# tokens and every line now carries one (measured mean 30.0; the 16 openings
# that ALREADY carried one averaged 29.6 before any rewrite).
MEAN_CEILING = 33


def _lines() -> list[str]:
    return ToolRegistry().tool_catalogue().splitlines()


def _tag_catalogue(platform: str, *, graph: bool = False) -> list[str]:
    """The catalogue lines a TAG turn on `platform` is shown: every registered
    tool its scope admits, never a Dreamer-only one, on ONE memory surface
    (DEC-316: `graph` is `[memory_index] enabled`)."""
    scope = ToolScope(platform=platform, dreamer=False)
    return [
        line for line in _lines()
        if scope.admits(name := line.split(" — ")[0][2:])
        and on_memory_surface(name, graph=graph)
    ]


def test_every_registered_tool_has_a_catalogue_line():
    """The catalogue is the ONLY thing telling the model a tool exists once the
    schemas are deferred. A tool missing from it is a capability that silently
    cannot be reached — DEC-28's failure, where a model was read as refusing to
    use a tool it had never been shown."""
    named = {line.split(" — ")[0][2:] for line in _lines()}
    # `load_tools` is plumbing, not a capability: the block header says how to
    # load, and a line for the loader would be a line spent on itself.
    expected = {
        name for name, spec in NATIVE_TOOLS.items()
        if not spec.plugin and name != LOADER_TOOL
    }
    assert named == expected


def test_every_line_carries_a_description():
    """A bare name discriminates nothing. If a tool's docstring cannot produce
    a first sentence, the catalogue would ship its name alone and the model
    would have to guess what it does."""
    assert [line for line in _lines() if " — " not in line] == []


def test_the_catalogue_stays_an_index_and_not_a_second_copy_of_the_schemas():
    """Gate 2a, over what a tag turn pays: the LARGEST per-platform catalogue.
    Re-pointing this at a larger number instead of fixing the lines is how the
    block becomes the thing it replaced."""
    sizes = {
        f"{p}{' (graph)' if graph else ''}": len(
            ENC.encode("\n".join(_tag_catalogue(p, graph=graph))))
        for p in TAG_PLATFORMS for graph in (False, True)
    }
    platform, total = max(sizes.items(), key=lambda kv: kv[1])
    assert total <= BLOCK_CEILING, (
        f"the {platform} tag catalogue is {total} tokens, ceiling {BLOCK_CEILING} "
        f"({sizes}). Lines are being written as descriptions — fix the lines, not "
        "the ceiling."
    )


def test_a_tag_catalogue_holds_no_dreamer_only_line():
    """The ceiling is measured over a tag turn's lines, so a Dreamer-only tool
    must not be among them: if it were, the block would be paid where it is not
    meant to be, and the comment above would be true after all."""
    for platform in TAG_PLATFORMS:
        named = {line.split(" — ")[0][2:] for line in _tag_catalogue(platform)}
        assert not named & DREAMER_ONLY_TOOLS, (platform, named & DREAMER_ONLY_TOOLS)
    assert len(_tag_catalogue("discord")) > len(_tag_catalogue("signal"))


def test_no_single_line_has_become_a_description():
    lines = _lines()
    over = [(len(ENC.encode(line)), line) for line in lines]
    worst = max(over)
    assert worst[0] <= LINE_CEILING, f"{worst[1]!r} is {worst[0]} tokens"

    mean = sum(n for n, _ in over) / len(over)
    assert mean <= MEAN_CEILING, (
        f"mean line is {mean:.1f} tokens. No single line tripped, so this is "
        "drift across many of them — the shape the per-line gate cannot see."
    )


def test_confusable_pairs_are_discriminated_in_the_catalogue_itself():
    """The whole risk of a one-line catalogue is selection between neighbours,
    so the neighbours are pinned HERE rather than left to a model eval.

    `where_is`/`where_been` is the pair that measured badly and was repaired at
    the docstring: «was last seen» never said RIGHT NOW, so both lines read as
    questions about the past. The other three already discriminated; they are
    pinned so a docstring edit cannot quietly remove the distinction.
    """
    by_name = {line.split(" — ")[0][2:]: line for line in _lines()}

    assert "RIGHT NOW" in by_name["where_is"]
    assert "HAS BEEN" in by_name["where_been"]

    assert "named" in by_name["parking_status"]
    assert "nearest" in by_name["find_parking"]

    assert "this group's" in by_name["search_memory"]
    assert "EVERY room" in by_name["search_memory_workspace"]

    assert "NEW" in by_name["event_propose"]
    assert "ALREADY" in by_name["event_revise"]


def test_the_hot_set_is_marked_and_matches_the_one_declaration():
    """A marker saying «already loaded» while the builder did not load it sends
    the model to fetch something it holds, or worse, stops it fetching
    something it does not. The marker and the builder read the same
    `HOT_TOOLS`, and this is what stops a second list appearing."""
    marked = {line.split(" — ")[0][2:] for line in _lines() if line.startswith("*")}
    assert marked == set(HOT_TOOLS)


def test_send_reply_is_resident_whatever_its_usage_share():
    """Structural, not measured. `send_reply` is the terminal verb — the turn
    ends by `ReplyGuard` observing it (DEC-21/DEC-32) — so a turn that had to
    fetch it before it could reply is a turn that can fail to end. Re-deriving
    the hot set from fresh call counts must never drop it."""
    assert "send_reply" in HOT_TOOLS


def test_the_catalogue_and_the_gate_digest_render_the_same_way():
    """Two renderings of one thing is the drift DEC-150 deleted the capability
    block for. `capability_digest` (the Relevance Gate's read-only view) and
    `tool_catalogue` (the model's full view) share `_digest_lines`, so a tool
    in both must render identically apart from the resident marker."""
    registry = ToolRegistry()
    digest = {
        line.split(" — ")[0][2:]: line.split(" — ", 1)[-1]
        for line in registry.capability_digest().splitlines()
    }
    catalogue = {
        line.split(" — ")[0][2:]: line.split(" — ", 1)[-1]
        for line in registry.tool_catalogue().splitlines()
    }
    shared = set(digest) & set(catalogue)
    assert shared, "the two renderings share no tools — one of them is empty"
    assert {name: digest[name] for name in shared} == {
        name: catalogue[name] for name in shared
    }


def test_the_catalogue_covers_more_than_the_gate_digest():
    """They are scoped to different readers and must not collapse into one.
    The gate asks «could an answer exist», so it sees read-only tools; the
    model is about to choose, so it sees the act verbs too."""
    registry = ToolRegistry()
    assert len(registry.tool_catalogue().splitlines()) > len(
        registry.capability_digest().splitlines()
    )
