---
name: room-language-verification
kind: objective
version: 1
applies_to: [room_language_adaptor]
description: >-
  The known-entity check the room-language adaptor runs over web-search
  results before a candidate may become a row. Lifted VERBATIM from
  `nora.language.observer._VERIFICATION_PROMPT_TEMPLATE`. **The body is a
  `str.format` TEMPLATE, not the finished prompt**: `{surface_form}`,
  `{sense}` and `{results}` are substituted in Python, and the literal
  braces of the JSON example are doubled (`{{`/`}}`) because that is what
  `str.format` requires. The escaping stays in the file and the formatting
  stays in `_build_verification_prompt`. Probably stops existing at the
  re-partition: 'is this an established public thing' is a question a
  research loop asks with its own tools, not a second one-shot prompt.
change_policy: operator-only
---
A room-language extractor flagged the phrase below as slang a Signal group chat has made its own. Your ONLY job is to check whether it is actually already a KNOWN PUBLIC thing — a real game, place, brand, meme, or other name with an established meaning OUTSIDE this specific group — rather than something this group coined.

Phrase: {surface_form}
Claimed local sense: {sense}

Web search results for the phrase:
{results}

Respond with ONLY a JSON object of this exact shape, nothing else — no prose, no markdown fence:
{{"known_entity": <true if the results show this is an established public thing whose own meaning the room's use matches or refers to; false if the results are irrelevant, sparse, or establish no public meaning>}}
