"""Serialize a normalised message for the Postgres Record."""

import json

from nora.domain.external_files import external_files_json
from nora.domain.media import media_json
from nora.domain.models import DeliveryStatus, NormalizedMessage, mentions_json


def record_insert_values(
    message: NormalizedMessage, is_nora: bool, delivery_status: DeliveryStatus,
    person_id: str | None, resolved_mentions: str | None,
) -> dict[str, object]:
    return {
        "platform": message.platform,
        "conversation_id": message.conversation_id,
        "conversation_type": message.conversation_type,
        "sender_id": message.sender_id,
        "sender_name": message.sender_name,
        "person_id": person_id,
        "ts": message.timestamp,
        "body": message.body,
        "is_nora": int(is_nora),
        "delivery_status": delivery_status.value,
        "raw_json": json.dumps(message.raw),
        "quoted_ts": message.quote.quoted_ts if message.quote else None,
        "media_json": media_json(message.media_blobs),
        "external_files_json": external_files_json(message.external_files),
        "platform_message_id": message.message_id or None,
        "mentions_json": mentions_json(message.mentions),
        "mention_persons_json": resolved_mentions,
    }
