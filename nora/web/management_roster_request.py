"""The bridge reports classification and collection completeness explicitly."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nora.domain.identity import Identifier
from nora.domain.management import Scope


class ObservedAccount(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    account: Identifier
    classification: Literal["person", "bot", "unknown"]


class RosterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    operation_id: str = Field(min_length=1, pattern=r"\S")
    collection: str = Field(min_length=1, pattern=r"\S")
    platform: str = Field(min_length=1, pattern=r"\S")
    scope: Scope
    audience: Literal["collection", "room"]
    members: tuple[ObservedAccount, ...]
    complete: bool

    @model_validator(mode="after")
    def coherent(self) -> "RosterRequest":
        if any(member.account.platform != self.platform for member in self.members):
            raise ValueError("a roster names one platform collection")
        if len({member.account for member in self.members}) != len(self.members):
            raise ValueError("a roster cannot classify one account twice")
        return self
