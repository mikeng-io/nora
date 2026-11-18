"""Account provenance retained by a one-time-code challenge, never link consent."""

from dataclasses import dataclass
from datetime import datetime

from nora.domain.authentication import SessionBinding
from nora.domain.identity import Identifier

CODE_ISSUER = "nora-otp"


@dataclass(frozen=True)
class CodeAccount:
    account: Identifier
    binding: SessionBinding | None = None

    def __post_init__(self) -> None:
        if self.account.platform not in {"signal", "whatsapp"}:
            raise ValueError("one-time codes require a direct messaging account")


@dataclass(frozen=True)
class CodeAttempt:
    person_id: str
    platform: str
    account: CodeAccount | None

    def __post_init__(self) -> None:
        if self.account is not None:
            if self.account.account.platform != self.platform:
                raise ValueError("code account does not match its delivery platform")
            binding = self.account.binding
            if binding is not None and binding.person_id != self.person_id:
                raise ValueError("code binding does not match its original person")


@dataclass(frozen=True)
class VerifiedAccountCode:
    challenge_id: str
    person_id: str
    account: Identifier
    binding: SessionBinding
    verified_at: datetime
