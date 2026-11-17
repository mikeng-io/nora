"""The bridge reports account identifiers and collection completeness."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nora.domain.identity import Identifier
from nora.domain.management import Scope


class ObservedAccount(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    account: Identifier
    classification: Literal["person", "bot", "unknown"] = "unknown"
    #: The platform-role labels this account holds in the roster's scope.
    roles: tuple[str, ...] = Field(default=())


class RosterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    operation_id: str = Field(min_length=1, pattern=r"\S")
    collection: str = Field(min_length=1, pattern=r"\S")
    platform: str = Field(min_length=1, pattern=r"\S")
    scope: Scope
    audience: Literal["collection", "room"]
    members: tuple[ObservedAccount, ...]
    complete: bool
    # The producer's own upstream-read instant — absent means undated
    # evidence the shared mutation boundary refuses (`unavailable`), never
    # a receipt-time stamp invented here.
    observed_ms: int = 0

    @model_validator(mode="after")
    def coherent(self) -> "RosterRequest":
        if any(member.account.platform != self.platform for member in self.members):
            raise ValueError("a roster names one platform collection")
        if len({member.account for member in self.members}) != len(self.members):
            raise ValueError("a roster cannot classify one account twice")
        if any(not role.strip() or "/" in role
               for member in self.members for role in member.roles):
            raise ValueError("a platform role names one label, not a path")
        return self
