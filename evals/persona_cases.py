"""Write invented writer stimuli to a PRIVATE file; never edit anchored sources.

Usage: python -m evals.persona_cases /tmp/persona-identity.yaml
The anchor is reused byte-for-byte from the existing tool-selection corpus.
The roster person is its existing pseudonym, resolved by the normal loader.
The snapshot remains the common background; these sources are additional
invented arrivals. The clock is explicitly counterfactual, not anchor history.
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml


def millis(date: str) -> int:
    return int(datetime.fromisoformat(date).replace(tzinfo=UTC).timestamp() * 1000)


def case_file() -> dict:
    rows = yaml.safe_load((Path(__file__).parent / "corpora/tool_selection.yaml").read_text())
    if isinstance(rows, dict):
        rows = rows["cases"]
    anchor = next(r["snapshot"] for r in rows if r.get("snapshot"))
    vectors = {
        "birthday_retained": [("2026-05-01", "My birthday is June 8."),
                              ("2026-05-09", "Happy birthday, Jo! Hope you have a lovely day.")],
        "birthday_corrected": [("2026-05-01", "My birthday is June 8."),
                               ("2026-05-09", "Correction: my birthday is June 9, not June 8.")],
        "residence_unknown": [("2026-09-01", "What's the weather in Kowloon?"),
                              ("2026-09-02", "I'm leaving the mall."),
                              ("2026-09-03", "I'm heading out from home.")],
        "marriage_unknown": [("2026-09-01", "Jo and I plan to get married on October 3."),
                             ("2026-10-10", "Jo sent me a funny picture.")],
        "partner_unspecified": [("2026-10-10", "我同我另一半 Jo 去食飯。")],
        "explicit_home_spouse": [("2026-10-10", "I live in Harbor District. Jo is my spouse.")],
    }
    return {name: {
        "snapshot": dict(anchor), "person": "miles_novak", "clock_ms": millis("2026-10-12"),
        "sources": [{"body": body, "ts": millis(date)} for date, body in sources],
    } for name, sources in vectors.items()}


if __name__ == "__main__":
    path = Path(sys.argv[1])
    # Refuse clobbering a private file somebody may already have adapted.
    with path.open("x") as output:
        output.write(yaml.safe_dump(case_file(), allow_unicode=True, sort_keys=False))
    path.chmod(0o600)
