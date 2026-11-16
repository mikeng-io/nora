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
class RoomAuthentication:
    """Transport provenance for one fresh room message (`management.md`
    «In-room management»): the trusted ingress path's assertion of WHICH
    account sent it, created only by code that owns the normalized message.

    It is not an `AccountAuthentication`: a platform message carries no
    session and no login method, so none is modelled. `binding` is a fixed
    `None` property — a message can never carry a binding claim, and the
    current binding is what the mutation gate resolves live. The check is
    the same `AccountSessions.check` the session path takes: account →
    canonical person → hold → lifecycle → identity registry, revalidated
    under the writer's lock at invocation time.

    `issuer` names the trusted ingress that built it (for audit), never the
    sender; `message_key` is the message's own durable identity, so the
    journal entry is pinned to the message that raised it.
    """

    issuer: str
    message_key: str
    account: Identifier
    authenticated_at: datetime
    purpose: Literal["message"] = "message"

    @property
    def binding(self) -> SessionBinding | None:
        """Always `None` — a room message never carries a binding claim."""
        return None


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    authentication: AccountAuthentication | RoomAuthentication
    binding: SessionBinding
    display_name: str


@dataclass(frozen=True)
class AuthenticationResult:
    verdict: Verdict
    principal: AuthenticatedPrincipal | None = None

    def __post_init__(self) -> None:
        if (self.verdict is Verdict.ALLOWED) != (self.principal is not None):
            raise ValueError("only authenticated results carry a principal")
