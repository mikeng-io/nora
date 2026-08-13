"""Smith's bootstrap script — the ENTRYPOINT of the sandbox image (Nora
Compute spec §3). Reads its task, runs a Strands agent equipped with the
full skill library (`run_python`, `register_artifact`, `render_mermaid`,
`render_markdown`, `render_map`, `render_html_report`), and writes its
result to the bind-mounted workspace for `LocalDockerBackend`
(nora/infra/compute/docker_backend.py) to read once this process exits.

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

from budget import ToolCallBudget, guarded
from skills.html_report.render import render_html_report as _render_html_report
from skills.maps.render import render_map as _render_map
from skills.markdown.render import render_markdown as _render_markdown
from skills.mermaid.render import render_mermaid as _render_mermaid
from tracing import setup_tracing

WORKSPACE = Path("/workspace")
DROP_INTERNAL_URL = os.environ["DROP_INTERNAL_URL"]
DROP_JOB_TOKEN = os.environ["DROP_JOB_TOKEN"]
ARTIFACT_ID = os.environ.get("ARTIFACT_ID") or None
# Smith's own LLM credential — the same OmniRoute-shaped (api_key, base_url,
# model name) TRIPLE FORMAT Nora is configured with, so this module builds its
# model client the way she builds hers. The reused thing is the SHAPE, not
# necessarily the secret: whether the value handed in here is Nora's own key
# or a separate, Smith-scoped one is decided by whoever constructs
# `LocalDockerBackend` (composition wiring, a later phase) — this module
# treats it as opaque either way and never assumes it carries Nora's
# authority. See nora/infra/compute/docker_backend.py's constructor for the
# same statement from the other side.
MODEL_API_KEY = os.environ["SMITH_MODEL_API_KEY"]
MODEL_BASE_URL = os.environ["SMITH_MODEL_BASE_URL"]
MODEL_NAME = os.environ["SMITH_MODEL_NAME"]

MAX_TOOL_CALLS = int(os.environ.get("SMITH_MAX_TOOL_CALLS", "40"))
_budget = ToolCallBudget(max_calls=MAX_TOOL_CALLS)


@tool
@guarded(_budget)
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
        check=False,
    )
    output = result.stdout
    if result.stderr:
        output += f"\n[stderr]\n{result.stderr}"
    return output


@tool
@guarded(_budget)
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
    url = str(response.json()["url"])

    # Written here, not left to the model choosing to call `run_python` with
    # code that does it (doctrine.md's own instruction, but nothing enforces
    # the model actually follows it). Without this, a job whose model skips
    # that step registers the artifact successfully in Drop -- the URL comes
    # back from THIS call just fine -- but `LocalDockerBackend._read_result`
    # still reports `FAILED "job exited without producing a result"` because
    # `/workspace/url.txt` was never written, and the artifact is now
    # orphaned in Drop with nothing pointing at it. Mirrors the same
    # guaranteed-write-in-code fallback `main()` already uses for
    # `summary.txt`, so both output files are equally guaranteed rather than
    # one being tool-guaranteed and the other model-dependent.
    (WORKSPACE / "url.txt").write_text(url)
    return url


@tool
@guarded(_budget)
def render_mermaid(diagram_source: str) -> str:
    """Render Mermaid diagram source to a self-contained SVG string. Raises
    if the source is malformed -- see skills/mermaid/SKILL.md."""
    # Let `MermaidRenderError` propagate rather than catching it here --
    # exactly what skills/mermaid/SKILL.md already tells Smith to expect
    # ("raises rather than returning something broken -- catch it and
    # either fix the source or fall back to prose"). A prior version of
    # this wrapper caught the error and returned the string
    # "[render_mermaid failed: ...]" as if it were a successful render --
    # a `status: "success"` ToolResult carrying a failure message as if it
    # were a diagram, the ONLY one of the six tools here that swallowed its
    # own real error instead of letting Strands turn it into a proper
    # `status: "error"` ToolResult the model can actually react to (the same
    # behavior render_map's `HTTPError` and render_html_report's `ValueError`
    # already have by simply not catching anything).
    return _render_mermaid(diagram_source=diagram_source)


@tool
@guarded(_budget)
def render_markdown(text: str) -> str:
    """Render GFM markdown (with syntax-highlighted code blocks) to
    self-contained HTML. See skills/markdown/SKILL.md."""
    return _render_markdown(text=text)


@tool
@guarded(_budget)
def render_map(*, locations: list[tuple[float, float]], title: str = "") -> str:
    """Render a static map with markers at the given (lat, lng) locations,
    embedded inline. See skills/maps/SKILL.md."""
    return _render_map(locations=locations, title=title)


@tool
@guarded(_budget)
def render_html_report(
    *, title: str, sections: list[str], accent: str | None = None
) -> str:
    """Compose rendered HTML fragments into one responsive, self-contained
    page. Call this LAST, once, with everything the artifact needs. See
    skills/html_report/SKILL.md."""
    return _render_html_report(title=title, sections=sections, accent=accent)


def main() -> None:
    # Before anything else touches Strands -- its own instrumentation reads
    # whatever global TracerProvider exists at call time, not just at
    # Agent() construction, so tracing.py's own "never load-bearing" try/
    # except is the only thing standing between a bad OTLP endpoint and a
    # job that otherwise runs fine.
    setup_tracing(
        enabled=os.environ.get("SMITH_OTLP_ENABLED", "") == "1",
        endpoint=os.environ.get("SMITH_OTLP_ENDPOINT", ""),
        headers=os.environ.get("SMITH_OTLP_HEADERS", ""),
    )

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
        tools=[
            run_python, register_artifact, render_mermaid, render_markdown, render_map,
            render_html_report,
        ],
    )
    # Strands' own NATIVE per-invocation ceiling -- enforced inside the event
    # loop itself (`strands/event_loop/event_loop.py`'s `_check_limits`,
    # confirmed against the installed `strands-agents==1.50.2`), independent
    # of `_budget` above. This is the PRIMARY, actually-effective ceiling: a
    # cap here ends the job by returning a normal `AgentResult` with a
    # `limit_*` stop_reason, no exception involved, so it cannot be
    # swallowed the way a tool-raised exception can be.
    #
    # `turns` (one model call plus whatever tool calls follow from it) is set
    # a bit above `MAX_TOOL_CALLS` (40 by default): most turns spend at most
    # one tool call, so 50 turns comfortably brackets 40 tool calls plus a
    # handful of turns of slack for the model to see a budget-exhausted tool
    # error and wrap up -- without handing a thrashing job meaningfully more
    # room than `_budget` already allows. `total_tokens` bounds runaway
    # *cost* independent of call count: 300k tokens is a generous multiple
    # of what a well-behaved ~40-tool-call job should need (a few thousand
    # tokens per turn), while still being a hard ceiling on spend for a job
    # that is thrashing rather than working.
    result = agent(task, limits={"turns": 50, "total_tokens": 300_000})

    if _budget.exceeded:
        # `BudgetExceededError` (raised by `guarded` when `_budget` trips)
        # does NOT propagate out of `agent(task)` -- Strands' own
        # `DecoratedFunctionTool.stream()` catches it like any other tool
        # exception and turns it into an ordinary `status: "error"`
        # ToolResult, so the call above returns normally either way. This
        # checks `_budget`'s own state instead of trusting an exception that
        # can never arrive: if the tool-call ceiling was actually spent, the
        # job used its whole allowance on some mix of real work and
        # thrashing, so it is reported as a failure the same way a
        # propagating exception would have -- no summary.txt beyond what
        # already happened, and a clear reason in the container's own logs.
        print(f"job exhausted its {MAX_TOOL_CALLS}-call tool budget")
        return

    summary_path = WORKSPACE / "summary.txt"
    if not summary_path.exists():
        # The agent's own final text is the fallback summary if it never
        # explicitly wrote one — better than leaving `LocalDockerBackend`'s
        # `_read_result` to report a bare "no result" failure for a job that
        # otherwise completed.
        summary_path.write_text(str(result))


if __name__ == "__main__":
    main()
