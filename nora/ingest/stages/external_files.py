"""Shared file links become handles in the record.

Every link in an inbound message that a provider recognises as one of its
files is attached to the message as an `ExternalFileRef`, and the audit stage
writes them to `messages.external_files_json` in the same INSERT as the row,
for `media_blobs`' reason. On EVERY platform: the text is all it reads.

**No network call, and no config.** Recognising a URL needs no account,
so this runs whether or not `[external_files]` is enabled; that switch is
the open tool's. Nothing is fetched, named or read here: all that is
known about the file is that somebody sent this link. The body is left
verbatim; what a model sees is rendered from it
(`nora.domain.external_files.render_file_links`).

# ponytail: an EDIT that adds a link is not recorded here (an edit is a
# `message_edit` row with no refs column); its link still renders as a handle,
# and resolving it is the upgrade if somebody edits a link in and asks.
"""

from __future__ import annotations

import dataclasses

from nora.domain.external_files import file_refs_in
from nora.domain.models import NormalizedMessage


async def run(*, message: NormalizedMessage) -> NormalizedMessage:
    """`message` with `external_files` set to the file links in its body, or
    `message` unchanged when there are none."""
    refs = file_refs_in(message.body)
    if not refs:
        return message
    return dataclasses.replace(message, external_files=refs)
