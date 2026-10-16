"""R-B: a test gets its config from the template, through the one fixture; only
a test whose SUBJECT is the deployment reads the real file.

A suite that acquires a `Config` by many routes — the `tests.builders` helper,
`shutil.copy(config.example.toml)`, `tomllib.load` on the example,
`load_eval_config((real, example))`, a direct read of the real `config.toml`, a
hand-built `Config(...)`, inline TOML — has a site to change whenever the config
file's name, format or a key moves, and a read of the real file couples unit
tests to the DEPLOYMENT's values: a unit test of the eval harness goes red the
moment the operator enables an export. One fixture (`example_config_path` /
`example_config` in `tests/conftest.py`, built on
`tests.builders.copy_example_config`) collapses this to two sources: the
template, or — for the pinned files below — the real file, because comparing the
template against the deployment is the test.

The three files allowed to read the real `config.toml` (and to name the
template beside it) are pinned in `_DEPLOYMENT_SUBJECTS`. A fixture cannot
substitute in any of them: point the drift gate at the template and it
compares the template to itself, green forever, measuring nothing.

What the gate reads, off the AST rather than a grep, so a message string that
mentions the file («config.example.toml no longer declares [media]») is not an
offence but a path to it is:

- `<repo root> / "config.toml"` or `/ "config.example.toml"`, where «repo
  root» is any module-level name bound through `__file__` (`ROOT`,
  `REPO_ROOT`, `repo_root`, ...) or an inline `Path(__file__)...` chain.
- `Path("config.toml")` / `Path("config.example.toml")` — a cwd-relative read
  that only works from the repo root.
- Importing `load_eval_config` — it tries the deployment file FIRST, which is
  the eval CLI's correct behaviour and a unit test's false dependency.
- A module-level constant naming either file — the variable-spelled read
  (`CONFIGS = ("config.toml", ...)` joined to the root inside a helper).

Positive control: every pinned file must still contain at least one such read,
so a detector that goes blind fails here instead of passing green with an
empty offender list — and a pin whose file stopped reading the real config is
stale and fails for the same reason.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILES = {"config.toml", "config.example.toml"}

# Files whose SUBJECT is the deployment, or which are the one copier. Each
# entry says why a fixture cannot stand in.
_DEPLOYMENT_SUBJECTS: dict[str, str] = {
    "tests/architecture/test_the_eval_subject_is_the_deployed_nora.py": (
        "Its subject IS the rule that builds the eval subject's config FROM "
        "`config.example.toml` plus the deployment's own values, so it must "
        "read the real example — a fixture would compare the template to a "
        "copy of itself and measure nothing, which is this gate's own stated "
        "failure mode. Added 2026-09-21 with the fix for the suite composing "
        "the example instead of the deployment (65 values differed, including "
        "the model and most features)."
    ),
    "tests/offline_eval_harness.py": (
        "The offline eval harness, re-homed from `evals/harness.py` on "
        "2026-09-18. Its subject IS the deployment: `load_eval_config` prefers "
        "the operator's real `config.toml` and falls back to the template, "
        "deliberately, because an eval run must measure the doctrine, models "
        "and budgets the deployment actually has. A fixture here would make it "
        "measure the template instead — which is the exact failure this gate "
        "exists to prevent, one directory over."
    ),
    "tests/builders.py": (
        "the one place that knows the template's name — copy_example_config / "
        "load_example_config, which the conftest fixtures wrap"
    ),
    "tests/test_env_template.py": (
        "the F11 drift gate: compares config.example.toml against the deployment's "
        "config.toml key by key; pointed at the template it would compare the "
        "template to itself"
    ),
    "tests/test_turn_budget.py": (
        "R-24: the turn budgets are asserted against BOTH files because a fix "
        "applied to one is half a fix — the deployment's values are the subject"
    ),
}


def _root_names(tree: ast.Module) -> set[str]:
    """Module-level names whose value is derived from `__file__`."""
    names: set[str] = set()
    for node in tree.body:
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        if value is None or not _mentions_file(value):
            continue
        names.update(t.id for t in targets if isinstance(t, ast.Name))
    return names


def _mentions_file(node: ast.AST) -> bool:
    return any(isinstance(n, ast.Name) and n.id == "__file__" for n in ast.walk(node))


def _is_repo_root(node: ast.AST, roots: set[str]) -> bool:
    return _mentions_file(node) or any(
        isinstance(n, ast.Name) and n.id in roots for n in ast.walk(node)
    )


def config_reads(path: Path) -> list[str]:
    """Every place `path` reaches a config file at the repo root, as `line: what`."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots = _root_names(tree)
    out: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)
            and isinstance(node.right, ast.Constant) and node.right.value in CONFIG_FILES
            and _is_repo_root(node.left, roots)
        ):
            out.append(f"{node.lineno}: <repo root> / {node.right.value!r}")
        elif (
            isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "Path" and node.args
            and isinstance(node.args[0], ast.Constant) and node.args[0].value in CONFIG_FILES
        ):
            out.append(f"{node.lineno}: Path({node.args[0].value!r})")
        elif isinstance(node, ast.ImportFrom) and any(
            a.name == "load_eval_config" for a in node.names
        ):
            out.append(f"{node.lineno}: import load_eval_config (prefers the deployment file)")
    # A module-level constant NAMING a config file — `CONFIGS = ("config.toml",
    # ...)` — is a read by another spelling: the name is joined to the root
    # inside a helper, where the literal is a variable and the rule above is
    # blind. Module scope only; a literal inside a test body is a value under
    # test (`select(["config.toml"])`), not a path.
    for node in tree.body:
        value = getattr(node, "value", None)
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and value is not None:
            for const in ast.walk(value):
                if isinstance(const, ast.Constant) and const.value in CONFIG_FILES:
                    out.append(f"{node.lineno}: module-level constant names {const.value!r}")
    return out


def _test_modules() -> list[Path]:
    here = Path(__file__).resolve()
    return sorted(p for p in (ROOT / "tests").rglob("*.py") if p != here)


def test_only_a_deployment_subject_reads_the_real_config() -> None:
    offenders: dict[str, list[str]] = {}
    for path in _test_modules():
        rel = path.relative_to(ROOT).as_posix()
        if rel in _DEPLOYMENT_SUBJECTS:
            continue
        reads = config_reads(path)
        if reads:
            offenders[rel] = reads
    assert not offenders, (
        "test module(s) reaching config.toml / config.example.toml at the repo "
        "root (R-B, issue #113). Use the `example_config_path` / `example_config` "
        "fixtures from tests/conftest.py; only a test whose subject IS the "
        "deployment reads the real file, and it is pinned in _DEPLOYMENT_SUBJECTS:\n  "
        + "\n  ".join(f"{k}\n    " + "\n    ".join(v) for k, v in sorted(offenders.items()))
    )


def test_every_pinned_deployment_subject_still_reads_the_config() -> None:
    """The positive control, and the stale-pin check in one: a pinned file with
    no read means either the detector went blind or the pin is a leftover."""
    blind = {
        rel for rel in _DEPLOYMENT_SUBJECTS
        if not config_reads(ROOT / rel)
    }
    assert not blind, f"pinned but no config read detected — stale pin or blind detector: {blind}"
