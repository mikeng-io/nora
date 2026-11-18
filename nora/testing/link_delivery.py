"""In-memory private challenge transport, bound to the Signal implementation by contract."""

from dataclasses import dataclass, field

from nora.domain.identity import Identifier


@dataclass
class FakeLinkChallengeDelivery:
    sent: list[tuple[Identifier, str]] = field(default_factory=list)
    fail: bool = False

    async def deliver(self, *, account: Identifier, text: str) -> bool:
        if account.platform != "signal" or not account.value.strip() or self.fail:
            return False
        self.sent.append((account, text))
        return True
