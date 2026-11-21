"""Managed registration reads a unique violation from the driver's SQLSTATE.

asyncpg's error message omits the code, so a substring check never matches;
both the register and bind paths must decide on `orig.sqlstate`.
"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError

from nora.infra.sql.managed_registration import is_unique_violation


class _DriverError(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__('duplicate key value violates unique constraint "uq_x"')
        self.sqlstate = sqlstate


def _integrity(sqlstate: str) -> IntegrityError:
    return IntegrityError("INSERT", {}, _DriverError(sqlstate))


def test_a_unique_violation_is_read_from_sqlstate_not_the_message() -> None:
    assert is_unique_violation(_integrity("23505"))


def test_a_check_violation_is_not_a_duplicate() -> None:
    assert not is_unique_violation(_integrity("23514"))
