"""One-time codes: issue one, count recent ones, check one (`nora.app.otp`).

Shaped like `nora.infra.sql.announcements`: the engine comes through `__init__`,
each verb is one transaction, and the decision that must not race is made by
Postgres rather than by Python.

Both tables are APPEND-ONLY (`0097_otp`). What happened to a code is the attempts
recorded against it, never an update to the challenge row, so a check is a read
plus one INSERT:

- **spent** — an `ok` attempt exists. `uq_otp_attempt_ok` is a unique partial
  index, so two requests carrying the right code at once cannot both insert one;
  the loser's INSERT does nothing and it is told no.
- **dead** — `max_attempts` failed attempts already, or past `expires_at`.
- **superseded** — a challenge recorded later exists for the same person and
  purpose.
  Asking for a new code kills the old one without touching it.

`FOR UPDATE` on the challenge row serialises checks of the same code, so two
wrong guesses at once cannot both be counted as the fifth. It locks a row that is
never updated, which Postgres allows.

There is ONE implementation and therefore no port and no fake, for the reason
`nora.infra.sql.announcements` gives: every test points this class at a real
throwaway Postgres.
"""

from __future__ import annotations

import hmac
import uuid
from dataclasses import asdict
from datetime import datetime

import sqlalchemy as sa
from pydantic import TypeAdapter
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.otp_account import CodeAccount, CodeAttempt
from nora.infra.sql.schema import OTP_ATTEMPT, OTP_CHALLENGE


class SqlOtpStore:
    """The `otp_challenge` and `otp_attempt` tables, as three verbs."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def issue(
        self, *, challenge_id: uuid.UUID, person_id: str, purpose: str, platform: str,
        code_hash: str, issued_at: datetime, expires_at: datetime,
        account: CodeAccount | None = None,
    ) -> None:
        """Record a code. Any earlier code for this person and purpose is
        superseded by this row existing; nothing else is written."""
        async with self._engine.begin() as conn:
            await conn.execute(sa.insert(OTP_CHALLENGE).values(
                challenge_id=challenge_id, person_id=person_id, purpose=purpose,
                platform=platform, code_hash=code_hash,
                account_proof=asdict(account) if account is not None else None,
                issued_at=issued_at, expires_at=expires_at,
            ))

    async def issued_since(self, *, person_id: str, since: datetime) -> int:
        """How many codes this person has been sent since `since`, any purpose:
        the rate limit is on how often somebody's phone can be made to buzz."""
        stmt = sa.select(sa.func.count()).select_from(OTP_CHALLENGE).where(
            OTP_CHALLENGE.c.person_id == person_id,
            OTP_CHALLENGE.c.issued_at >= since,
        )
        async with self._engine.connect() as conn:
            return int((await conn.execute(stmt)).scalar_one())

    async def attempt(
        self, *, challenge_id: uuid.UUID, purpose: str, code_hash: str, now: datetime,
        max_attempts: int,
    ) -> str | None:
        receipt = await self.attempt_account(challenge_id=challenge_id, purpose=purpose,
            code_hash=code_hash, now=now, max_attempts=max_attempts)
        return receipt.person_id if receipt is not None else None

    async def attempt_account(
        self, *, challenge_id: uuid.UUID, purpose: str, code_hash: str, now: datetime,
        max_attempts: int,
    ) -> CodeAttempt | None:
        """Check one code. The person it was for when it matches a live code,
        else None — and None tells the caller nothing about why.

        A check of a live code is always recorded, right or wrong; a check of a
        spent, dead, superseded or unknown code records nothing, since there is
        nothing left to spend."""
        c = OTP_CHALLENGE
        async with self._engine.begin() as conn:
            row = (await conn.execute(
                sa.select(c.c.person_id, c.c.purpose, c.c.code_hash, c.c.recorded_at,
                          c.c.expires_at, c.c.platform, c.c.account_proof)
                .where(c.c.challenge_id == challenge_id)
                .with_for_update()
            )).one_or_none()
            # A code is good only for what it was asked for: an `adopt` code
            # does not sign anybody in.
            if row is None or row.purpose != purpose or row.expires_at <= now:
                return None
            newer = (await conn.execute(
                sa.select(sa.func.count()).select_from(c).where(
                    c.c.person_id == row.person_id, c.c.purpose == row.purpose,
                    # The database's own insert time, not `issued_at`: two codes
                    # asked for within one tick of the caller's clock must still
                    # be ordered, or neither would supersede the other.
                    c.c.recorded_at > row.recorded_at,
                )
            )).scalar_one()
            if newer:
                return None
            a = OTP_ATTEMPT
            spent, failed = (await conn.execute(
                sa.select(
                    sa.func.count().filter(a.c.ok),
                    sa.func.count().filter(sa.not_(a.c.ok)),
                ).where(a.c.challenge_id == challenge_id)
            )).one()
            if spent or failed >= max_attempts:
                return None
            ok = hmac.compare_digest(row.code_hash, code_hash)
            result = await conn.execute(
                pg_insert(a)
                .values(challenge_id=challenge_id, ok=ok, attempted_at=now)
                .on_conflict_do_nothing(
                    index_elements=[a.c.challenge_id], index_where=a.c.ok,
                )
            )
        if not ok or not result.rowcount:
            return None
        account = (None if row.account_proof is None else
                   TypeAdapter(CodeAccount).validate_python(row.account_proof, extra="forbid"))
        return CodeAttempt(row.person_id, row.platform, account)
