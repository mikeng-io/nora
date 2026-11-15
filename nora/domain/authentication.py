"""Account-exact authentication evidence, distinct from service authority and consent."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from nora.domain.identity import Identifier
from nora.domain.managed import Verdict

AuthenticationMethod = Literal["discord_oauth", "telegram_oidc", "signal_code", "whatsapp_code"]


@dataclass(frozen=True)
class SessionBinding:
    person_id: str
    person_revision: str
    account_id: str
    account_revision: str


@dataclass(frozen=True)
class AccountAuthentication:
    """Created only after validating the trusted authentication issuer's assertion."""

    issuer: str
    session_id: str
    account: Identifier
    method: AuthenticationMethod
    proof_issuer: str
    authenticated_at: datetime
    purpose: Literal["sign_in", "session"]
    binding: SessionBinding | None


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    authentication: AccountAuthentication
    binding: SessionBinding
    display_name: str


@dataclass(frozen=True)
class AuthenticationResult:
    verdict: Verdict
    principal: AuthenticatedPrincipal | None = None

    def __post_init__(self) -> None:
        if (self.verdict is Verdict.ALLOWED) != (self.principal is not None):
            raise ValueError("only authenticated results carry a principal")
