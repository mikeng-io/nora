"""Resolve and revalidate the exact account an OTP is delivered to."""

import re
from dataclasses import dataclass

from nora.domain.authentication import SessionBinding
from nora.domain.identity import BoundAccount
from nora.domain.managed import PersonLifecycle, Verdict
from nora.domain.managed_ports import BindingStore, IdentityRegistry
from nora.domain.otp import OtpUnavailableError
from nora.domain.otp_account import CodeAccount, CodeAttempt
from nora.persons import Persons

_PHONE_PUNCTUATION = re.compile(r"[\s\-().]")


@dataclass(frozen=True)
class OtpRecipient:
    account: BoundAccount
    proof: CodeAccount


class OtpIdentities:
    def __init__(self, persons: Persons, bindings: BindingStore | None = None,
                 registry: IdentityRegistry | None = None) -> None:
        if (bindings is None) != (registry is None):
            raise ValueError("managed OTP requires both identity authorities")
        self._persons = persons
        self._bindings = bindings
        self._registry = registry

    @property
    def managed(self) -> bool:
        return self._bindings is not None

    async def resolve(self, platform: str, value: str) -> OtpRecipient | None:
        for candidate in dict.fromkeys((value.strip(), _PHONE_PUNCTUATION.sub("", value.strip()))):
            account = (self._persons.account_for(platform, candidate) if self._bindings is None
                       else await self._bindings.resolve(platform, candidate))
            if account is None:
                continue
            identifier = next(ident for ident in sorted(account.identifiers,
                key=lambda ident: ident.kind) if ident.value == candidate)
            binding = await self._binding(CodeAccount(identifier), account.person_id)
            if self.managed and binding is None:
                return None
            return OtpRecipient(account, CodeAccount(identifier, binding))
        return None

    async def _binding(self, proof: CodeAccount, person_id: str) -> SessionBinding | None:
        if self._bindings is None or self._registry is None:
            return None
        bound = await self._bindings.record_for(account=proof.account)
        if bound is None or bound.person_id != person_id:
            return None
        if await self._bindings.canonical_person(person_id) != person_id:
            return None
        if await self._bindings.identity_held(person_id) is not False:
            raise OtpUnavailableError("identity publication incomplete")
        person = await self._bindings.person(person_id)
        if person is None:
            raise OtpUnavailableError("person state unavailable")
        if person.lifecycle is not PersonLifecycle.ACTIVE:
            return None
        state = await self._registry.identity_state(external_id=person_id)
        if state.verdict is Verdict.DENIED:
            return None
        if state.verdict is not Verdict.ALLOWED:
            raise OtpUnavailableError("identity registry unavailable")
        return SessionBinding(person_id, str(person.revision),
                              bound.account_id, str(bound.revision))

    async def current(self, receipt: CodeAttempt) -> bool:
        if receipt.account is None or receipt.account.binding is None or not self.managed:
            return False
        binding = await self._binding(receipt.account, receipt.person_id)
        return binding is not None and binding == receipt.account.binding
