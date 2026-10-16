"""One-time codes sent to a person in a direct message on Signal or WhatsApp.

The substrate the portal's sign-in is checked against, and later adopting
somebody: `request` sends a six-digit code to a person's own account and answers
a challenge id; `verify` takes that id and the code back and answers who it was
for. Nothing here knows about sessions or cookies; the caller decides what a
verified person may do.

**Only somebody in `identity.toml`.** The identifier
a caller sends is resolved against the file, and the address is built from the
file (`Persons.direct_target`); there is no way to name a phone number that is
not in it, and the senders refuse one anyway (`Persons.direct_addresses`).

**Through `deliver.build`, the fourth caller R-1b names.** The message is a
fixed template and no model writes it, but sending it through the `Deliverer`
keeps every guard on the wire (R-6, R-18) and the audit-first write. The audit
row records the message with the code blanked (`record_text`), so the code is
never at rest anywhere: the store keeps only its HMAC.

**Every request answers the same.** A challenge id comes back whether the code
was sent, the identifier named nobody, or the person is rate limited, so the
endpoint cannot be used to learn who is in `identity.toml`. The difference is
on `nora_otp_requests_total`. # ponytail: the answer is uniform, its timing is
not (a real send takes longer than a refusal); the portal's own per-client rate
limit is the upgrade path if that ever matters.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import Any

from nora.domain.audience import ONE_TIME_CODE
from nora.domain.otp import PURPOSES
from nora.engagement.stages import deliver
from nora.infra.sql.otp import SqlOtpStore
from nora.persons import DIRECT_PLATFORMS, Persons

log = logging.getLogger("nora.app.otp")

_CODE = re.compile(r"[0-9]{6}")
# What a person types for a phone number: spaces, dashes and brackets dropped.
_PHONE_PUNCTUATION = re.compile(r"[\s\-().]")

_MESSAGES = {
    "sign_in": "{code} is your Nora sign-in code. It expires in {lifetime}. "
               "Nobody will ever ask you for it, not even Nora.",
    "adopt": "{code} is your Nora confirmation code. It expires in {lifetime}. "
             "Nobody will ever ask you for it, not even Nora.",
}


class OtpService:
    def __init__(
        self, *, store: SqlOtpStore, persons: Persons, adapters: Mapping[str, Any],
        records: Any, doctrine_store: Any, canary: str, metrics: Any, secret: bytes,
        ttl: timedelta, max_attempts: int, max_per_hour: int, max_per_day: int,
        clock: Callable[[], datetime],
    ) -> None:
        self._store = store
        self._persons = persons
        self._adapters = adapters
        self._records = records
        self._doctrine_store = doctrine_store
        self._canary = canary
        self._metrics = metrics
        self._secret = secret
        self._ttl = ttl
        self._max_attempts = max_attempts
        self._max_per_hour = max_per_hour
        self._max_per_day = max_per_day
        self._clock = clock

    def _lifetime(self) -> str:
        """`ttl` in words for the message: whole minutes, or seconds below two."""
        seconds = int(self._ttl.total_seconds())
        return f"{seconds // 60} minutes" if seconds >= 120 else f"{seconds} seconds"

    def _hash(self, challenge_id: uuid.UUID, code: str) -> str:
        """The code under the server secret, bound to its challenge so one
        code's hash says nothing about another's."""
        message = f"{challenge_id}:{code}".encode()
        return hmac.new(self._secret, message, hashlib.sha256).hexdigest()

    def _person_for(self, platform: str, identifier: str) -> str | None:
        """The person whose `platform` id this is (a phone number, or a Signal
        ACI), as typed or with phone punctuation removed. A person_id does NOT
        resolve: it is a first name anybody could guess, and the portal hands
        this endpoint to the internet."""
        ident = identifier.strip()
        for candidate in (ident, _PHONE_PUNCTUATION.sub("", ident)):
            if person := self._persons.resolve(platform, candidate):
                return person.person_id
        return None

    async def request(self, *, identifier: str, platform: str, purpose: str) -> str:
        """Send a code to the person `identifier` names, on `platform`, and
        answer the challenge id to check it against. Raises `ValueError` only
        for a platform or purpose outside the closed vocabularies; every other
        outcome answers an id."""
        if platform not in DIRECT_PLATFORMS or purpose not in PURPOSES:
            raise ValueError(f"unknown platform {platform!r} or purpose {purpose!r}")
        challenge_id = uuid.uuid4()
        outcome = await self._request(challenge_id, identifier, platform, purpose)
        self._metrics.inc("nora_otp_requests_total", platform=platform, outcome=outcome)
        return str(challenge_id)

    async def _request(
        self, challenge_id: uuid.UUID, identifier: str, platform: str, purpose: str,
    ) -> str:
        person_id = self._person_for(platform, identifier)
        if person_id is None:
            return "unknown"
        target = self._persons.direct_target(platform, person_id)
        if target is None:
            return "no_account"
        adapter = self._adapters.get(platform)
        if adapter is None:
            return "unavailable"
        now = self._clock()
        # ponytail: the count and the insert below are two statements, so
        # requests racing in parallel can pass `max_per_hour` by a few. They
        # come through the portal behind a token; an advisory lock per person
        # is the upgrade path if that ever matters.
        # Two windows: the hour bounds a burst, the day bounds what somebody who
        # knows a number can do with it all day — both the messages the person
        # receives and the guesses (`max_attempts` per code) at their codes.
        for window, cap in ((timedelta(hours=1), self._max_per_hour),
                            (timedelta(days=1), self._max_per_day)):
            if await self._store.issued_since(
                person_id=person_id, since=now - window,
            ) >= cap:
                return "rate_limited"
        code = f"{secrets.randbelow(10**6):06d}"
        # Recorded BEFORE the send, deliberately: a send that fails still
        # supersedes the person's earlier code and counts toward the hourly cap,
        # because a send that times out may have arrived anyway.
        await self._store.issue(
            challenge_id=challenge_id, person_id=person_id, purpose=purpose,
            platform=platform, code_hash=self._hash(challenge_id, code),
            issued_at=now, expires_at=now + self._ttl,
        )
        text = _MESSAGES[purpose].format(code=code, lifetime=self._lifetime())
        try:
            identity = await adapter.resolve_identity()
            handle = deliver.build(
                store=self._records, adapter=adapter, target=target,
                doctrine=self._doctrine_store.get(), canary=self._canary,
                tag_id=f"otp-{challenge_id}", nora_sender_id=identity.primary,
                conversation_id=target.conversation_id,
                # One message, no tools: nothing here is a model turn.
                budgets={"send_reply": 1}, terminal=frozenset(),
                metrics=self._metrics, audience=ONE_TIME_CODE, composer=None,
            )
            sent = await handle.deliverer.deliver(
                text, source=ONE_TIME_CODE,
                record_text=_MESSAGES[purpose].format(code="······", lifetime=self._lifetime()),
            )
        except Exception:
            log.exception("one-time code send raised (challenge=%s)", challenge_id)
            sent = False
        return "sent" if sent else "send_failed"

    async def verify(self, *, challenge_id: str, code: str, purpose: str) -> str | None:
        """The person a live code for `purpose` was sent to, when `code` is it;
        else None, whatever the reason.

        # ponytail: a made-up id costs one SELECT and a real one several, so
        # somebody holding the API token could tell the two apart by timing,
        # and burn one of the person's attempts doing so. The token is the
        # control; a per-client limit in the portal is the upgrade path."""
        person_id = None
        try:
            parsed = uuid.UUID(challenge_id)
        except (ValueError, AttributeError, TypeError):
            parsed = None
        if parsed is not None and isinstance(code, str) and _CODE.fullmatch(code):
            person_id = await self._store.attempt(
                challenge_id=parsed, purpose=purpose, code_hash=self._hash(parsed, code),
                now=self._clock(), max_attempts=self._max_attempts,
            )
        self._metrics.inc(
            "nora_otp_checks_total", outcome="ok" if person_id else "refused",
        )
        return person_id
