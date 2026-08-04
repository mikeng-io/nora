"""The ONE place that reads Settings. Everything else receives dependencies.

A stage never sees Config; it receives an AuditStore, a MemoryStore, a Persons.
That is what makes stages testable in isolation and what stops the god object
from reforming. Enforced by import-linter (DEC-12).

Nothing is rewired by this module yet: `nora.__main__.build_app` keeps
constructing its own graph exactly as it does today. This is the seam the
pipelines phase lands on, and the back door tests and evals no longer need.
"""

from __future__ import annotations

from dataclasses import dataclass

from nora.config import Config
from nora.obs.metrics import METRICS, Metrics
from nora.persons import Persons, load_persons
from nora.store.honcho import HonchoClient


@dataclass(frozen=True)
class Dependencies:
    """The wired collaborators a stage receives instead of `Config`.

    Deliberately **no** `config`/`settings` field. A stage that can reach the
    whole config is a stage that eventually will — that is precisely how the
    229-line `Handler` god object grew. Add a named collaborator here instead
    of widening this type's reach.

    Only collaborators that exist today are built: the persistence phase owns
    `AuditStore`, so it is not invented here.
    """

    memory: HonchoClient
    persons: Persons
    metrics: Metrics


def build_dependencies(cfg: Config) -> Dependencies:
    """Build the object graph from `cfg`.

    This function — and `nora.__main__.build_app`, until the pipelines phase
    replaces it — is the only place in the codebase allowed to know `Config`'s
    shape. Everything downstream of here takes a `Dependencies`.
    """
    return Dependencies(
        memory=HonchoClient(cfg.memory.base_url, cfg.memory.workspace_id),
        persons=load_persons(cfg.identity_file),
        metrics=METRICS,
    )
