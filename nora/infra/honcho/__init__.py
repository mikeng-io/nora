"""The semantic store. Honcho is never load-bearing: every method logs and
returns a benign value on any failure.

Deliberately a separate package from `infra/sql/`: the audit store's contract
is the opposite one — a failed append drops the message rather than degrading
quietly. Sharing a package would invite sharing an error policy.
"""

from nora.infra.honcho.client import HonchoClient, WorkspaceRouter

__all__ = ["HonchoClient", "WorkspaceRouter"]
