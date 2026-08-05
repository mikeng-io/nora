"""`EvalSink` port (Phase 7, DEC-6): what `evals/run.py` needs to record eval
results to an external store, and the two implementations that satisfy it.

Kept narrow on purpose — four methods, not a Langfuse SDK wrapper:
`upsert_dataset`/`upsert_item` map the corpora onto Langfuse datasets
(`evals/datasets.py`); `start_run`/`record_score` map one gate invocation onto
one Langfuse dataset run with per-case scores (`evals/run.py`). Nothing else —
reading scores back, deleting runs, annotation queues, and the rest of
Langfuse's real API surface are all out of scope because nothing here needs
them; widening this back into a general Langfuse client is exactly what this
port exists to resist.

`NullEvalSink` is the DEFAULT construction path (see `evals/run.py::_build_sink`)
— the eval gate is `uv run pytest`-adjacent and must produce its verdict with no
Langfuse configured at all. `LangfuseEvalSink` talks to the public HTTP API
directly over aiohttp, in the same shape `nora.infra.honcho.client.HonchoClient`
already uses for a best-effort, never-load-bearing network dependency: every
method logs a warning and swallows the failure instead of raising, because a
sink failure must never change what `evals/run.py`'s gate returns (the PROBE in
`tests/test_evals_gate.py` points a `LangfuseEvalSink` at an unreachable host and
confirms the gate still returns its verdict; `tests/contract/
test_eval_sink_contract.py` pins the same "never raises" invariant for BOTH
implementations, not just Langfuse's, so it can't silently stop being true of
`NullEvalSink` either).

**Credential decision** (see also `nora.config.LangfuseEvalConfig`'s docstring):
the app already exposes a working Langfuse Basic-auth credential as
`LANGFUSE_OTLP_HEADERS`, consumed by `nora.obs.tracing` for the OTLP exporter.
Its raw value (`Authorization=Basic%20<base64>`) is OTel's own
`OTEL_EXPORTER_OTLP_HEADERS` shape — `key=value`, URL-encoded — and is NOT a
valid HTTP header value by itself (the credential trap the plan calls out).
`evals.langfuse.headers_env` reuses that same variable NAME rather than adding
a second `*_env` for the identical secret; `resolve_headers_from_env` below
decodes it the same way `nora.obs.tracing._resolve_headers` does, via the same
third-party `parse_env_headers` helper — duplicated here (not imported from
`nora.obs`) because `evals/` may only import `nora.app` and `nora.testing`
(`tests/test_boundaries.py`), and `parse_env_headers` itself is third-party, not
`nora.*`, so reusing it directly does not widen that boundary.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Literal, Protocol, runtime_checkable

import aiohttp
from opentelemetry.util.re import parse_env_headers

log = logging.getLogger("nora.evals.sink")

ScoreDataType = Literal["NUMERIC", "BOOLEAN", "CATEGORICAL", "TEXT"]


def resolve_headers_from_env(headers_env: str) -> dict[str, str]:
    """Decode `headers_env`'s value into a plain header dict, e.g.
    `{"Authorization": "Basic ..."}`. Empty name or empty/unset value both mean
    "no headers" — never an error; a sink with no headers just sends
    unauthenticated requests, which Langfuse's own API rejects with 401, which
    `LangfuseEvalSink` swallows exactly like any other failure.
    """
    if not headers_env:
        return {}
    raw = os.environ.get(headers_env, "")
    if not raw:
        return {}
    return dict(parse_env_headers(raw))


@runtime_checkable
class EvalSink(Protocol):
    """Narrow port both implementations satisfy — see module docstring."""

    async def upsert_dataset(self, *, name: str) -> None: ...

    async def upsert_item(
        self, *, dataset_name: str, item_id: str, input: Any, expected_output: Any = None
    ) -> None: ...

    async def start_run(self, *, dataset_name: str, run_name: str) -> str: ...

    async def record_score(
        self,
        *,
        run_id: str,
        item_id: str,
        name: str,
        value: float | str,
        data_type: ScoreDataType,
    ) -> None: ...


class NullEvalSink:
    """The default. Every method is a genuine no-op — no logging, no state, no
    network. This is the structural guarantee behind "a missing credential
    degrades to no export, never to a failure": there is nothing here that
    *could* fail.
    """

    async def upsert_dataset(self, *, name: str) -> None:
        return None

    async def upsert_item(
        self, *, dataset_name: str, item_id: str, input: Any, expected_output: Any = None
    ) -> None:
        return None

    async def start_run(self, *, dataset_name: str, run_name: str) -> str:
        return run_name

    async def record_score(
        self,
        *,
        run_id: str,
        item_id: str,
        name: str,
        value: float | str,
        data_type: ScoreDataType,
    ) -> None:
        return None


class LangfuseEvalSink:
    """Talks to Langfuse's public HTTP API. `headers` is an already-decoded auth
    header dict (see `resolve_headers_from_env`) — this class does no env or
    credential resolution of its own, only HTTP, mirroring
    `nora.infra.honcho.client.HonchoClient`'s split between config resolution
    (the caller's job) and the network calls themselves (this class's job).

    `start_run` does not itself call the network: Langfuse has no dedicated
    "create a dataset run" endpoint — a run is created implicitly by the first
    dataset-run-item posted under its name (confirmed against the public API
    reference: `POST /api/public/dataset-run-items`, and the "run experiments"
    cookbook's `item.link(trace, run_name)` pattern). So `start_run` just
    remembers which dataset a run name belongs to; the real POST happens lazily
    inside `record_score`, once per (run, item) pair, the first time that case
    is scored.

    Scores attach via `datasetRunId` (Langfuse's 2025-05-07 "dataset-experiment-
    run-level scores" v2 API), not `traceId`: the offline/scripted eval gate
    (`evals/harness.py`) never produces a real Strands trace to attach a score
    to, so there is no `traceId` to use. A synthetic per-item `observationId`
    (a fresh UUID) is what links each case into the run's item list; the score
    itself rides on the run, not that synthetic observation.
    """

    def __init__(self, *, host: str, headers: dict[str, str], timeout: float = 5.0) -> None:
        self._host = host.rstrip("/")
        self._headers = headers
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._run_datasets: dict[str, str] = {}
        self._dataset_run_ids: dict[str, str] = {}

    async def upsert_dataset(self, *, name: str) -> None:
        await self._post("/api/public/v2/datasets", {"name": name})

    async def upsert_item(
        self, *, dataset_name: str, item_id: str, input: Any, expected_output: Any = None
    ) -> None:
        await self._post(
            "/api/public/dataset-items",
            {
                "id": item_id,
                "datasetName": dataset_name,
                "input": input,
                "expectedOutput": expected_output,
            },
        )

    async def start_run(self, *, dataset_name: str, run_name: str) -> str:
        self._run_datasets[run_name] = dataset_name
        return run_name

    async def record_score(
        self,
        *,
        run_id: str,
        item_id: str,
        name: str,
        value: float | str,
        data_type: ScoreDataType,
    ) -> None:
        dataset_run_id = await self._link_item(run_id, item_id)
        if dataset_run_id is None:
            return
        await self._post(
            "/api/public/v2/scores",
            {"datasetRunId": dataset_run_id, "name": name, "value": value, "dataType": data_type},
        )

    async def _link_item(self, run_id: str, item_id: str) -> str | None:
        """POST the dataset-run-item that both creates/extends the run and links
        `item_id` into it, returning the real `datasetRunId` the score attaches
        to. Falls back to a previously-seen id for this run if this particular
        POST fails — the run's id doesn't change between calls, only reachability
        does — so a transient failure on item N doesn't lose the run entirely for
        items N+1 onward.
        """
        dataset_name = self._run_datasets.get(run_id)
        body = None
        if dataset_name is not None:
            body = await self._post(
                "/api/public/dataset-run-items",
                {"runName": run_id, "datasetItemId": item_id, "observationId": uuid.uuid4().hex},
            )
        dataset_run_id = body.get("datasetRunId") if body else None
        if isinstance(dataset_run_id, str):
            self._dataset_run_ids[run_id] = dataset_run_id
            return dataset_run_id
        return self._dataset_run_ids.get(run_id)

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        url = f"{self._host}{path}"
        try:
            async with (
                aiohttp.ClientSession(timeout=self._timeout, headers=self._headers) as session,
                session.post(url, json=payload) as r,
            ):
                if r.status >= 400:
                    log.warning("langfuse eval sink POST %s -> %s", url, r.status)
                    return None
                data = await r.json()
                return data if isinstance(data, dict) else None
        except Exception as exc:  # noqa: BLE001 -- never load-bearing, see class docstring
            log.warning("langfuse eval sink POST %s failed: %s", url, exc)
            return None
