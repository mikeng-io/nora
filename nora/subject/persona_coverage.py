"""Read-only coverage export seam; uses canonical heads and eligible Record pages.

No subjects, model clients or writes. Evals consume serialized values through
this subject-layer port rather than importing infrastructure or roster internals.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from nora.infra.sql.character import SqlCharacterEvidence
from nora.infra.sql.engine import create_readonly_sql_engine
from nora.infra.sql.persona import SqlPersonaStore
from nora.language.persona_render import authored_identity
from nora.persons import load_persons


async def read_accounts(
    *, roster: list[dict], identity: Path | None, identity_hash: str | None, database_url: str
) -> tuple:
    people = load_persons(identity) if identity else None
    engine = create_readonly_sql_engine(database_url)
    store = SqlPersonaStore(engine, screen=lambda _: None)
    reader = SqlCharacterEvidence(engine)
    accounts, fresh, all_sources, authored = [], [], [], []
    try:
        for idx, account in enumerate(roster):
            boundary = next(
                w["last_message_id"]
                for w in account["watermarks"]
                if w["consolidator_name"] == "persona-person:history-boundary"
            )
            accounts.append(
                {**account, "boundary": boundary, "rooms": [r["key"] for r in account["rooms"]]}
            )
            heads = await store.account_heads(key=account["key"])
            # account_heads already resolves chain heads. State still matters:
            # a retired chain head must never stand in for a current assertion.
            fresh.append([asdict(h) for h in heads if h.state == "current"])
            sources, after = {}, 0
            for _ in range(100):
                page = await reader.historical_page(
                    rooms=[(r["platform"], r["key"]) for r in account["rooms"]],
                    person_id=account["person"],
                    after=after,
                    through=boundary,
                    limit=500,
                )
                if not page:
                    break
                sources.update({s.row.id: asdict(s) for s in page})
                after = max(s.row.id for s in page)
            else:
                raise ValueError("eligible source page bound exhausted")
            all_sources.append(sources)
            lines = []
            if people is not None:
                person = people.by_person.get(account["person"])
                if person is None:
                    raise ValueError("roster person absent from authored identity")
                # The actual prompt's `of` is the authored display name.
                lines = [
                    {
                        "id": f"authored-{idx}",
                        "unit": account["unit"],
                        "person": account["person"],
                        "of": person.name,
                        "lines": authored_identity(name=person.name, aliases=person.aliases),
                        "identity_hash": identity_hash,
                    }
                ]
            authored.append(lines)
    finally:
        await engine.dispose()
    return accounts, fresh, all_sources, authored
