"""`identity.toml`'s on-disk shape — the pydantic layer, and nothing else.

Split out of `nora.persons` for size. The seam is a real one either way:
validating the FILE FORMAT is a separate job from building and serving the
reverse index, and only `load_persons` needs both.

Nothing here is public. `nora.persons` is still the only import site.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from .domain.identity import (
    ACCOUNT_LABEL_KEY,
    DISPLAY_NAME_KIND,
    id_kinds,
    metadata_keys,
)

# Honcho requires ^[a-zA-Z0-9_-]+$ for peer ids, and honcho_peer_id == person_id.
_PERSON_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

# The platform sub-table names `_PersonEntry` accepts, in the order the reverse
# index is built — every platform with a declared kind vocabulary.
PLATFORM_FIELDS: tuple[str, ...] = (
    "signal", "telegram", "discord", "whatsapp", "instagram", "life360", "pulse",
)


def _check_account(platform: str, account: dict[str, str | int]) -> None:
    """Validate one account table's keys against `platform`'s vocabulary.

    Called from `_PersonEntry`'s single `model_validator`, which walks
    `PLATFORM_FIELDS` — so the platform name comes from the loop variable and
    this never needs pydantic's `ValidationInfo.field_name` (typed
    `str | None`, which would need an `assert` and trip ruff S101).
    """
    allowed = id_kinds(platform)
    metadata = metadata_keys(platform)
    matchable = 0
    for kind in account:
        if kind == ACCOUNT_LABEL_KEY or kind in metadata:
            continue                      # non-matchable: label, or life360 circle_id
        if kind == DISPLAY_NAME_KIND:
            raise ValueError(
                f'{platform}'
                '.'
                f'{DISPLAY_NAME_KIND}'
                ' is never matchable (design/10 §1.3) — display names are user-'
                'controlled and spoofable; they are shown to the operator, never '
                'used to resolve a sender to a person'
            )
        if kind not in allowed:
            raise ValueError(
                f'{platform}'
                '.<value> is not a valid id kind (design/10 §3.1); expected one of'
                ' '
                f'{sorted(allowed)}'
            )
        matchable += 1
    if not matchable:
        # An account table holding only a `label` contributes nothing to the
        # reverse index, so it would sit in the file looking like a mapping
        # while resolving no one — silent, and invisible under R-5.
        raise ValueError(
            'the '
            f'{platform}'
            ' account <value> lists no matchable id — give it at least one of '
            f'{sorted(allowed)}'
            ', or remove the block'
        )


class _PersonEntry(BaseModel):
    """One `[persons.<id>]` table. Each platform sub-table's keys are
    validated against that platform's fixed id-kind vocabulary
    (`nora.domain.identity.id_kinds`) — a typo, or
    `display_name`, is a startup error, not a silently-ignored key.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    # Names the group uses for this person. Comprehension only — see
    # `PersonRef.aliases`. They are deliberately NOT added to the reverse
    # index: making a hand-written nickname resolve a sender would hand
    # identity to anyone who can set a profile name.
    aliases: list[str] = Field(default_factory=list)
    # Prior `person_id`s this SAME person was known under. A rename changes
    # the TOML section header itself, which would split one continuous
    # presence-fixes track into two identities. Declared here, never
    # inferred — an operator states a merge happened; the analytics rollup
    # (nora.analytics) never guesses one from timing/proximity alone.
    former_person_ids: list[str] = Field(default_factory=list)
    # The social stream's per-person off switch (design/cognition-loop/
    # 20-observe.md §2). `false` stops the poller probing or reading any of
    # this person's accounts on any network. On by default: a member is
    # anyone listed in a room, and this is how one is taken back out.
    social: bool = True
    # One dict per ACCOUNT (the baseline case, not an edge case); the keys
    # inside are that account's id KINDS plus the never-matched `label`.
    #
    # The leaf type stays `str | int` ON PURPOSE: it is what makes pydantic
    # reject `phone = ["+852…", "+852…"]` at startup instead of letting
    # `str(list)` reach the reverse index and build a key no sender can ever
    # present — a mute Nora with no error, invisible under R-5. Multiplicity
    # lives at the table level, never at the leaf.
    signal: list[dict[str, str | int]] | None = None
    telegram: list[dict[str, str | int]] | None = None
    discord: list[dict[str, str | int]] | None = None
    whatsapp: list[dict[str, str | int]] | None = None
    # Instagram carries TWO matchable ids per account, which no other platform
    # here does: `fbid` is `interop_messaging_user_fbid` — what a mention
    # carries and what a message's sender is — and `igid` is the public/social
    # id that profile and story reads take. Both belong to the same human and
    # both resolve to this person. `username` is accepted as metadata and is
    # NEVER matchable, for the reason every platform here refuses a handle: it
    # renames, and an identity that renames is an identity that silently stops
    # resolving.
    instagram: list[dict[str, str | int]] | None = None
    # A location account, not a chat one, but the same array-of-accounts shape:
    # one block per Life360 account with a `member_id = "…"` (the circle-member
    # UUID). It resolves to this person the same way their Signal number does,
    # which is what lets the presence collector name a live
    # circle member as one of Nora's people.
    life360: list[dict[str, str | int]] | None = None
    # A game-presence account, same shape as life360: one block per account,
    # matched on `steam_id` (SteamID64) — stable across every Steam-launched
    # game, unlike Pulse's own per-game `player_key` — so the web portal's
    # Games Hub can resolve a Pulse player back to this person.
    pulse: list[dict[str, str | int]] | None = None
    # Accepted here ONLY so the retired `[persons.<id>.platforms]` shorthand
    # gets a specific, readable error naming its replacement (a hard break,
    # deliberately: identity.toml is gitignored and
    # single-operator, so there is no installed base to support two shapes
    # for) instead of pydantic's generic "extra fields not permitted".
    platforms: dict[str, str | int] | None = None

    @field_validator("platforms")
    @classmethod
    def _reject_old_shorthand(cls, value: dict[str, str | int] | None) -> None:
        if value is not None:
            raise ValueError(
                "the old `[persons.<id>.platforms]` shorthand (one id per "
                "platform) is no longer supported (design/10 §7.3) — use one "
                "typed table per platform instead, e.g. "
                '`[persons.<id>.signal]` with `phone = "…"` / `uuid = "…"` '
                "keys; see design/10-preview-identity.toml for the target "
                "shape. (`username` is accepted but never matched on — it "
                "cannot be a block's only id.)"
            )
        return None

    @model_validator(mode="before")
    @classmethod
    def _reject_single_table_form(cls, data: Any) -> Any:
        """Hard break on the pre-multi-account `[persons.<id>.signal]` form.

        pydantic's own "Input should be a valid list" names neither the new
        shape nor why it changed. Like `_reject_old_shorthand`, this file's
        breaks get a bespoke error; same reasoning —
        identity.toml is gitignored and single-operator, so supporting two
        shapes forever buys nothing.
        """
        if not isinstance(data, dict):
            return data
        for platform in PLATFORM_FIELDS:
            if isinstance(data.get(platform), dict):
                raise ValueError(
                    '`[persons.<id>.'
                    f'{platform}'
                    ']` is now an array of tables, one per ACCOUNT — write '
                    '`[[persons.<id>.'
                    f'{platform}'
                    ']]` (double brackets). A person may hold several accounts on one '
                    'platform (a dual-SIM personal/work pair, a second Discord), and '
                    'they all resolve to the same person_id and share one Honcho peer '
                    "card (A2). List that account's id forms inside the block; see "
                    'design/10-preview-identity.toml.'
                )
        return data

    @model_validator(mode="after")
    def _check_accounts(self) -> _PersonEntry:
        """Validate every account on every platform.

        One validator walks `PLATFORM_FIELDS`, so a new platform costs one
        field.
        """
        for platform in PLATFORM_FIELDS:
            accounts = getattr(self, platform) or ()
            for account_no, account in enumerate(accounts):
                try:
                    _check_account(platform, account)
                except ValueError as exc:
                    raise ValueError(f"{platform}[{account_no}]: {exc}") from None
            if len(accounts) < 2:
                continue
            # A second, independent tripwire on the silent copy-paste failure
            # in `load_persons`: it makes the operator look at the block he
            # just duplicated.
            labels = [str(a.get(ACCOUNT_LABEL_KEY, "")).strip() for a in accounts]
            if not all(labels):
                raise ValueError(
                    f'{platform}'
                    ' has '
                    f'{len(accounts)}'
                    ' accounts, so each needs a `'
                    f'{ACCOUNT_LABEL_KEY}'
                    ' = "…"` naming it (e.g. "personal", "business"). It is never '
                    'matched on — it is what `nora persons pending` prints back to '
                    'you.'
                )
            if len(set(labels)) != len(labels):
                raise ValueError(
                    f'{platform}'
                    ' accounts have duplicate '
                    f'{ACCOUNT_LABEL_KEY}'
                    's <value> — give each one a distinct name, or you cannot tell '
                    'them apart in `nora persons pending`'
                )
        return self


class _WorkspaceBinding(BaseModel):
    """One `[workspaces.<nora workspace>]` table: the HONCHO workspace that
    Nora workspace is bound to (DEC-214).

    Here and not in `policy.toml` because it is an external id, not a trust
    answer: `policy.toml` says which workspace a unit belongs to, this file
    says where that workspace's person models live. A Nora workspace with no
    table here uses its own name as its Honcho workspace id
    (`nora.app.memory_routing`)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    honcho_workspace: str

    @field_validator("honcho_workspace")
    @classmethod
    def _check_honcho_workspace(cls, value: str) -> str:
        if not _PERSON_ID_RE.match(value):
            raise ValueError(
                'honcho_workspace <value> must match ^[a-zA-Z0-9_-]+$ — Honcho '
                'refuses any other workspace id with a 422, at feed time and off '
                'the reply path, so every write would be lost without a sound'
            )
        return value


class _PersonsFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    persons: dict[str, _PersonEntry] = Field(default_factory=dict)
    # Nora workspace -> its Honcho binding (DEC-214). Empty is the shipped
    # state and the back-compat one: every workspace is then bound by name,
    # and the implicit one to `memory.workspace_id`.
    workspaces: dict[str, _WorkspaceBinding] = Field(default_factory=dict)

    @field_validator("persons")
    @classmethod
    def _check_person_ids(
        cls, value: dict[str, _PersonEntry]
    ) -> dict[str, _PersonEntry]:
        for person_id in value:
            if not _PERSON_ID_RE.match(person_id):
                raise ValueError(
                    'person_id <value> must match ^[a-zA-Z0-9_-]+$ (it is used '
                    'verbatim as the Honcho peer id)'
                )
        return value
