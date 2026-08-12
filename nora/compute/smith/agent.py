"""Smith's bootstrap script — the ENTRYPOINT of the sandbox image (Nora
Compute spec §3). Reads its task, runs a bare Strands agent (code-exec plus
one raw artifact-registration tool, no skill library yet — that's a later
phase), and writes its result to the bind-mounted workspace for
`LocalDockerBackend` (nora/infra/compute/docker_backend.py) to read once this
process exits.

Deliberately NOT importing anything from the top-level `nora` package — this
image is a separate, isolated environment (its own `pyproject.toml`), and an
arbitrary-code-execution sandbox should not hold a copy of Nora's own runtime
dependencies just because both happen to be Python.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import requests
from strands import Agent, tool
from strands.models.openai import OpenAIModel

WORKSPACE = Path("/workspace")
DROP_INTERNAL_URL = os.environ["DROP_INTERNAL_URL"]
DROP_JOB_TOKEN = os.environ["DROP_JOB_TOKEN"]
ARTIFACT_ID = os.environ.get("ARTIFACT_ID") or None
# Smith's own LLM credential — the same OmniRoute-shaped (api_key, base_url,
# model name) triple `LocalDockerBackend` mints Nora's own from, NOT a copy
# of Nora's own key (nora/infra/compute/docker_backend.py's own docstring on
# why: reused SHAPE, not a shared secret, since this sandbox runs
# LLM-written code and should hold its own bounded credential).
MODEL_API_KEY = os.environ["SMITH_MODEL_API_KEY"]
MODEL_BASE_URL = os.environ["SMITH_MODEL_BASE_URL"]
MODEL_NAME = os.environ["SMITH_MODEL_NAME"]


@tool
def run_python(code: str) -> str:
    """Run a Python snippet in the workspace directory and return its stdout
    (and stderr, if any). Files this snippet writes should go under
    /workspace so they persist after this container exits."""
    result = subprocess.run(  # noqa: S603
        # S603/S607: this call running arbitrary LLM-authored `code` by
        # design IS `run_python`'s whole job (it's Smith's one code-exec
        # tool, Nora Compute spec §3) -- the untrusted-input containment
        # boundary is the Docker sandbox this process runs inside
        # (`LocalDockerBackend`'s per-job container, network + mem limits),
        # not a check at this call site. `"python3"` (a partial path, S607)
        # deliberately resolves via this image's own `PATH`, matching this
        # same Dockerfile's own `ENTRYPOINT` -- this image never runs on a
        # host where that could resolve to something other than the
        # `python:3.12-slim` base's interpreter.
        ["python3", "-c", code],  # noqa: S607
        cwd=WORKSPACE,
        capture_output=True,
        text=True,
        timeout=120,
    )
    output = result.stdout
    if result.stderr:
        output += f"\n[stderr]\n{result.stderr}"
    return output


@tool
def register_artifact(file_path: str, title: str, media_type: str) -> str:
    """Register a file under /workspace with Drop, returning its URL. Use
    this exactly once, on the one file this task was asked to produce."""
    data = (WORKSPACE / file_path).read_bytes()
    files = {"file": (file_path, data, media_type)}
    form = {"title": title, "media_type": media_type}
    headers = {"Authorization": f"Bearer {DROP_JOB_TOKEN}"}

    if ARTIFACT_ID:
        response = requests.patch(
            f"{DROP_INTERNAL_URL}/api/artifacts/{ARTIFACT_ID}",
            data=form, files=files, headers=headers, timeout=30,
        )
    else:
        response = requests.post(
            f"{DROP_INTERNAL_URL}/api/artifacts",
            data=form, files=files, headers=headers, timeout=30,
        )
    response.raise_for_status()
    return str(response.json()["url"])


def main() -> None:
    task_file = Path(os.environ["TASK_FILE"])
    task = task_file.read_text()
    doctrine = Path(__file__).parent.joinpath("doctrine.md").read_text()

    # Without this, Smith has NO way to call an LLM at all — a real gap an
    # earlier draft of this plan shipped (caught only by a subagent review
    # that actually tried to run this code). Mirrors how Nora's own agent is
    # built (nora/app/composition.py's OMNIROUTE_API_KEY -> AsyncOpenAI
    # client -> Agent(model=...) in nora/agent/factory.py) — confirmed
    # against the installed `strands-agents==1.50.2`: `OpenAIModel.__init__`
    # accepts `client_args={"api_key": ..., "base_url": ...}` plus
    # `model_id=...`/`params=...` as its "legacy" (non-shared-client)
    # construction path, which is exactly right for Smith, which has no
    # process-wide `AsyncOpenAI` client to share the way Nora's own
    # `ModelSource` does.
    model = OpenAIModel(
        client_args={"api_key": MODEL_API_KEY, "base_url": MODEL_BASE_URL},
        model_id=MODEL_NAME,
    )
    agent = Agent(
        model=model,
        system_prompt=doctrine,
        tools=[run_python, register_artifact],
    )
    result = agent(task)

    summary_path = WORKSPACE / "summary.txt"
    if not summary_path.exists():
        # The agent's own final text is the fallback summary if it never
        # explicitly wrote one — better than leaving `LocalDockerBackend`'s
        # `_read_result` to report a bare "no result" failure for a job that
        # otherwise completed.
        summary_path.write_text(str(result))


if __name__ == "__main__":
    main()
