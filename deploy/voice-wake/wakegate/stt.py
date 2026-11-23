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
_READ_STATUS = {"ok": 200, "nothing_found": 200, "unavailable": 503}
_NO_ENVELOPE = frozenset({400, 401, 404, 409, 422})
# A lost or unsettled answer is read back by its operation_id, never resent;
# the bound keeps a slow Nora from holding an utterance task for long.
_READ_BACK_ON = frozenset({"pending", "uncertain", "lost"})
_READ_BACKS = 3
_READ_BACK_GAP_S = 0.2
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

    True only for a whole envelope whose outcome is `complete`, answered
    with that outcome's status. On `pending`, `uncertain` or a lost or
    invalid answer the record is read back by the same `operation_id`, a
    bounded number of times; only a settled `complete` there is True.
    Every other answer, a redirect included, is False after one POST.

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
    operation_id = str(uuid.uuid4())
    request = {"operation_id": operation_id, "kind": "record", "effect": effect}
    headers = {"Authorization": f"Bearer {token}"}
    outcome = await _submit(session, url, request, headers)
    if outcome in _READ_BACK_ON:
        outcome = await _read_back(session, f"{url}/{operation_id}", headers)
    if outcome == "complete":
        return True
    log.warning("nora did not record the utterance (%s)", outcome)
    return False


async def _answer(session: aiohttp.ClientSession, method: str, url: str,
                  **kwargs) -> tuple[int, object] | str:
    """(status, JSON body or None), or a fixed label for no answer."""
    try:
        async with session.request(method, url, allow_redirects=False, **kwargs) as response:
            try:
                body = await response.json(content_type=None)
            except ValueError:
                body = None
            return response.status, body
    except asyncio.CancelledError:
        raise
    except (TimeoutError, aiohttp.ClientError):
        return "lost"


async def _submit(session: aiohttp.ClientSession, url: str, request: dict,
                  headers: dict[str, str]) -> str:
    """The POST's validated outcome, or a fixed label: `lost`, `redirect`, `http_<n>`."""
    answer = await _answer(session, "POST", url, json=request, headers=headers)
    if isinstance(answer, str):
        return answer
    status, body = answer
    if 300 <= status < 400:
        return "redirect"
    if status in _NO_ENVELOPE:
        return f"http_{status}"
    outcome = _operation_outcome(body)
    return outcome if outcome is not None and _STATUS[outcome] == status else "lost"


async def _read_back(session: aiohttp.ClientSession, url: str,
                     headers: dict[str, str]) -> str:
    """The record's settled outcome by GET, or the last label once the bound runs out."""
    label = "lost"
    for attempt in range(_READ_BACKS):
        if attempt:
            await asyncio.sleep(_READ_BACK_GAP_S)
        answer = await _answer(session, "GET", url, headers=headers)
        if isinstance(answer, str):
            continue
        status, body = answer
        label = _read_outcome(status, body)
        if label == "nothing_found" or (label in _STATUS and label != "pending"):
            return label
    return label


def _read_outcome(status: int, body: object) -> str:
    """The read-back's answer: the record's outcome, `nothing_found`, or `lost`."""
    if not isinstance(body, dict) or body.get("outcome") not in _READ_STATUS:
        return "lost"
    outcome = body["outcome"]
    when, from_ = body.get("when"), body.get("from")
    well_formed = (
        _READ_STATUS[outcome] == status
        and isinstance(when, dict) and isinstance(when.get("known_by"), str)
        and isinstance(when.get("observed_at_ms"), int)
        and isinstance(from_, dict) and isinstance(from_.get("source"), str)
        and isinstance(from_.get("healthy"), bool)
        and _scope_and_trace(body)
    )
    if not well_formed or outcome == "unavailable":
        return "lost"
    if outcome == "nothing_found":
        return "nothing_found"
    return _operation_outcome(body.get("payload")) or "lost"
