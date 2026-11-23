"""STT + Nora forwarding for completed utterances.

One POST against the local `stt` service's OpenAI-compatible
`/v1/audio/transcriptions` (private `nora` network — audio never leaves the
box), multipart built by hand exactly like `nora/infra/speech.py`'s
`SenseVoiceTranscriber`: the OpenAI contract wants the audio under a `file`
part with a filename. `model` names the engine in `deploy/stt/server.py`'s
`MODEL_CONFIGS` (`noise-detection`, the primary).

Failure posture is the wake gate's own: **no wake, no cost** — an STT
failure retries ONCE with `language` unset (auto-LID instead of the pinned
`yue`, in case the pin is what the engine choked on) and then drops the
utterance silently. Nothing is queued and nothing is retried later: audio
is transient by design.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

import aiohttp

log = logging.getLogger("wakegate.stt")


def _multipart(model: str, language: str | None, wav: bytes) -> tuple[str, bytes]:
    boundary = "----wakegate-stt"
    parts = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="model"\r\n\r\n'
        f"{model}\r\n"
    )
    if language is not None:
        parts += (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="language"\r\n\r\n'
            f"{language}\r\n"
        )
    parts += (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="utterance.wav"\r\n'
        f"Content-Type: audio/wav\r\n\r\n"
    )
    return boundary, parts.encode() + wav + f"\r\n--{boundary}--\r\n".encode()


async def transcribe(
    session: aiohttp.ClientSession,
    *,
    stt_url: str,
    model: str,
    language: str | None,
    wav: bytes,
) -> str | None:
    """One utterance -> transcript text, or None (dropped). Never raises:
    a dead STT service must not take the wake loop down with it."""
    url = stt_url.rstrip("/") + "/v1/audio/transcriptions"
    for attempt_language in (language, None) if language is not None else (None,):
        boundary, body = _multipart(model, attempt_language, wav)
        try:
            async with session.post(
                url,
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                data=body,
            ) as response:
                if response.status != 200:
                    log.warning("stt refused (http %s, language=%r)", response.status, attempt_language)
                    continue
                payload = await response.json(content_type=None)
                text = (payload or {}).get("text") or ""
                if not text:
                    log.info("stt returned an empty transcript (language=%r)", attempt_language)
                    continue
                return str(text)
        except asyncio.CancelledError:
            raise  # shutdown is not a transcription failure
        except (TimeoutError, aiohttp.ClientError) as exc:
            log.warning("stt unreachable (%s, language=%r)", type(exc).__name__, attempt_language)
        except Exception:
            log.exception("transcribe failed (language=%r)", attempt_language)
    return None


# The canonical service API's operation envelope (design/service-api/
# conventions.md), mirrored because this service may not import nora.
_STATUS = {"complete": 200, "pending": 202, "denied": 403, "rejected": 200,
           "unsupported": 200, "incomplete": 200, "uncertain": 200,
           "unavailable": 503}
_NO_ENVELOPE = frozenset({400, 401, 404, 409, 422})
_STATES = frozenset({"prepared", "started", "settled"})
_LEVELS = frozenset({"room", "unit", "agent", "identity", "workspace",
                     "installation", "none"})


def _optional(value: object, kind: type) -> bool:
    return value is None or (isinstance(value, kind) and not isinstance(value, bool))


def _scope_and_trace(body: dict) -> bool:
    scope = body.get("scope")
    return (isinstance(scope, dict) and scope.get("level") in _LEVELS
            and _optional(scope.get("id"), str) and isinstance(body.get("trace"), str))


def _operation_outcome(body: object) -> str | None:
    """The outcome of a well-formed operation envelope, else None."""
    if not isinstance(body, dict) or body.get("outcome") not in _STATUS:
        return None
    outcome = body["outcome"]
    when, from_ = body.get("when"), body.get("from")
    well_formed = (
        isinstance(body.get("payload"), dict)
        and isinstance(when, dict) and when.get("state") in _STATES
        and (when["state"] == "settled") != (outcome == "pending")
        and _optional(when.get("started_at_ms"), int)
        and _optional(when.get("settled_at_ms"), int)
        and isinstance(from_, dict) and isinstance(from_.get("target"), str)
        and _optional(from_.get("platform_ref"), str)
        and _scope_and_trace(body)
    )
    return outcome if well_formed else None


async def forward_utterance(
    session: aiohttp.ClientSession,
    *,
    nora_url: str,
    user_id: str,
    text: str,
    guild_id: str,
    token: str,
    voice_channel_id: str | None = None,
) -> bool:
    """The transcript -> Nora's `POST /v1/intake/operations` as a `record`
    operation. Text only — audio never crosses this boundary. Never raises;
    a refused forward drops the utterance (transient by design) and is
    visible in the log.

    True only for a 200 whose envelope's outcome is `complete`; any other
    outcome, a status that disagrees with it, a body with no envelope, a
    redirect or a transport failure is False.

    `token` is the shared service bearer; Nora refuses the utterance without it.

    The GUILD is what is sent, not a conversation id: nora resolves it to the
    room the operator marked `voice = true`, so this service never has to know
    — or be reconfigured when — a guild's rooms change.

    `voice_channel_id` is provenance and OPTIONAL. It is whatever the
    controller reports it is CURRENTLY connected to; omitted when that could
    not be read, because a stale channel id in a trace is worse than none."""
    url = nora_url.rstrip("/") + "/v1/intake/operations"
    effect: dict[str, str] = {
        "event_id": str(uuid.uuid4()),
        "kind": "utterance",
        "guild_id": guild_id,
        "user_id": user_id,
        "text": text,
    }
    if voice_channel_id:
        effect["voice_channel_id"] = voice_channel_id
    request = {"operation_id": str(uuid.uuid4()), "kind": "record", "effect": effect}
    try:
        async with session.post(
            url, json=request, headers={"Authorization": f"Bearer {token}"},
            allow_redirects=False,
        ) as response:
            try:
                body = await response.json(content_type=None)
            except ValueError:
                body = None
            outcome = _operation_outcome(body)
            if outcome is None or _STATUS[outcome] != response.status:
                log.warning("nora answered without a valid envelope (http %s)",
                            response.status)
                return False
            if outcome != "complete":
                log.warning("nora did not record the utterance (%s)", outcome)
                return False
            return True
    except asyncio.CancelledError:
        raise
    except (TimeoutError, aiohttp.ClientError) as exc:
        log.warning("nora unreachable (%s)", type(exc).__name__)
        return False
