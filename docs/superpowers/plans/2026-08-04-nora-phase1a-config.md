# Nora Phase 1a — Config Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hand-rolled YAML config loader with pydantic-settings over TOML, and reshape the config tree to the target layout — without changing a single runtime **value**.

**Architecture:** Two separable concerns, deliberately split across tasks so a failure is attributable. Tasks 1–2 swap the *library and format* while keeping every key name identical. Task 3 renames keys to the target shape. Task 4 moves identity to TOML. Tasks 5–6 update deployment assets and operator docs. At no point does a runtime value change.

**Tech Stack:** Python 3.12, pydantic-settings, `tomllib` (stdlib read) + `tomli-w` (write, tests only), uv, pytest, Docker.

**Spec:** `docs/superpowers/specs/2026-08-04-nora-architecture-design.md` (revision 2), §5.1, §7, Phase 1 row in §8.

## Global Constraints

- **Value-for-value.** Every runtime value in the new `config.toml` must equal today's `config.yaml`. Specifically preserved: `account = "+85290000100"` (NOT `""`), tracing `exporter = "file"` with empty `endpoint` (NOT otlp), and the 30-second invocation timeout. Spec §7: *"stage 1 preserves every current config value, not just every key."*
- **Strictness preserved.** `extra = "forbid"` on every model. A typo in `trigger.text` silently disables Nora — that is why unknown keys must remain a hard startup failure, and missing keys too.
- **Base-relative path resolution preserved.** Relative paths resolve against the **config file's own directory**, with `~` expansion, exactly as `nora/config.py:144-146` does today. This is what lets the same file work on the host and at `WORKDIR /app` in the container.
- **The secret is never in config.** Only `api_key_env` (the variable NAME). `load()` must still refuse to start when that variable is unset.
- **`config.yaml` is git-tracked AND bind-mounted** (`docker-compose.yml:22`). Renaming it changes what the live container mounts — Task 5 handles compose and the file rename together, and `config.yaml.pre-toml` is the backup.
- Dependencies via **`uv add`**, never pip. Container builds need **`docker build --network=host`**.
- The **signal-cli daemon is neutral and shared — never touched.**
- `/health` JSON is additive only; `deploy/watchdog.sh` parses `body['sse']['last_event_age_seconds']`.
- Baseline: `uv run pytest -q` → **454 passed, 1 deselected**. `uv run ruff check nora/ evals/ tests/` clean. Both must hold at every commit.

---

### Task 1: Add pydantic-settings and build the TOML loader alongside the old one

Build the new loader as a **parallel** implementation first, verified to produce a config equal to the old one. Nothing switches over in this task, so the suite cannot regress.

**Files:**
- Modify: `pyproject.toml`, `uv.lock` (add `pydantic-settings`, `tomli-w`)
- Create: `nora/config_toml.py` (temporary name; Task 2 renames it into place)
- Create: `config.toml` (value-for-value translation of `config.yaml`)
- Create: `config.example.toml`
- Create: `tests/test_config_toml.py`

**Interfaces:**
- Produces: `nora.config_toml.load(path) -> Config`, `nora.config_toml.ConfigError` — same public shape as `nora.config.load`, same attribute paths (`cfg.trigger.text`, `cfg.buffer.db_path`, …). Task 2 swaps the module in; Task 3 renames the attribute paths.

- [ ] **Step 1: Add the dependencies**

```bash
uv add "pydantic-settings>=2.4"
uv add --group dev "tomli-w>=1.0"
uv run python -c "import pydantic_settings, tomli_w; print('ok')"
```

`tomllib` is stdlib on 3.11+ for reading; `tomli-w` is dev-only, for tests that need to *write* a mutated config.

- [ ] **Step 2: Write `config.toml` as a value-for-value translation**

Translate `config.yaml` key-for-key and value-for-value. Keep the SAME key names in this task — no reshaping yet.

```toml
# Nora configuration (local, real deployment — tracked in git, per-deployment; kept
# out of the image by .dockerignore, mounted read-only into the container by
# docker-compose.yml instead). Paths are relative to this file's own directory so the
# same config works unmodified on the host or in the container (WORKDIR /app mirrors
# this repo's layout).

[signal_cli]
base_url = "http://127.0.0.1:18080"
account = "+85290000100"         # Nora's Signal number (the linked account)
nora_sender_id = "nora"          # how Nora's own rows are tagged in the audit

[honcho]
base_url = "http://127.0.0.1:8000"
workspace_id = "nora"

[llm]
base_url = "http://localhost:20128/v1"   # OmniRoute OpenAI-compatible
api_key_env = "OMNIROUTE_API_KEY"        # env var NAME, never the value

[llm.default]
model = "deepseek-v4-flash"
max_tokens = 400
temperature = 0.7

[llm.deep]
model = "deepseek-v4-pro"
max_tokens = 1200
temperature = 0.5

[llm.limits]
turns = 4
total_tokens = 24000

[trigger]
text = "nora.000"
min_interval_seconds = 5
invocation_timeout_seconds = 30

[buffer]
db_path = "data/messages.db"
max_per_conversation = 500
context_window = 40

[persona]
dir = "nora/persona"
reload = "mtime"
strict = true
chinese_script = "traditional"
canary = "NORA-CANARY-00000000"
display_name = "Nora"

persons_file = "persons.yaml"

[logging]
level = "INFO"
path = "data/nora.log"
format = "json"

[metrics]
port = 9100

[observability.tracing]
enabled = true
exporter = "file"
endpoint = ""
```

**TOML ordering trap:** a bare key like `persons_file` must appear BEFORE any `[table]` header, or it becomes a member of the preceding table. Put it at the very top of the file, above `[signal_cli]`, or the parse will silently place it under `[persona]`. Verify with:

```bash
uv run python -c "import tomllib;d=tomllib.load(open('config.toml','rb'));print('persons_file' in d, list(d))"
```

Expected: `True` and `persons_file` present at top level.

Then create `config.example.toml` the same way from `config.example.yaml` — identical **except** `account = ""` (the example derives from the daemon) and `db_path = "~/workspace/nora/data/messages.db"` (the example uses the tilde form, which is what exercises `~` expansion in tests).

- [ ] **Step 3: Write the failing test**

Create `tests/test_config_toml.py`:

```python
"""The TOML loader must be observationally identical to the YAML loader it replaces."""

import shutil
from pathlib import Path

import pytest

from nora.config import load as load_yaml
from nora.config_toml import ConfigError, load

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def toml_path(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    dst = tmp_path / "config.toml"
    shutil.copy(ROOT / "config.example.toml", dst)
    return dst


def test_toml_loader_matches_the_yaml_loader_field_for_field(tmp_path, monkeypatch):
    """The whole point of this task: same values, different format and library."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    yaml_cfg = load_yaml(_copy(ROOT / "config.example.yaml", tmp_path / "config.yaml"))
    toml_cfg = load(_copy(ROOT / "config.example.toml", tmp_path / "config.toml"))

    assert toml_cfg.signal_cli.base_url == yaml_cfg.signal_cli.base_url
    assert toml_cfg.signal_cli.account == yaml_cfg.signal_cli.account
    assert toml_cfg.signal_cli.nora_sender_id == yaml_cfg.signal_cli.nora_sender_id
    assert toml_cfg.honcho.workspace_id == yaml_cfg.honcho.workspace_id
    assert toml_cfg.llm.api_key_env == yaml_cfg.llm.api_key_env
    assert toml_cfg.llm.default.model == yaml_cfg.llm.default.model
    assert toml_cfg.llm.deep.model == yaml_cfg.llm.deep.model
    assert toml_cfg.llm.limits.turns == yaml_cfg.llm.limits.turns
    assert toml_cfg.trigger.text == yaml_cfg.trigger.text
    assert toml_cfg.trigger.invocation_timeout_seconds == yaml_cfg.trigger.invocation_timeout_seconds
    assert toml_cfg.buffer.max_per_conversation == yaml_cfg.buffer.max_per_conversation
    assert toml_cfg.buffer.context_window == yaml_cfg.buffer.context_window
    assert toml_cfg.persona.canary == yaml_cfg.persona.canary
    assert toml_cfg.persona.strict == yaml_cfg.persona.strict
    assert toml_cfg.logging.level == yaml_cfg.logging.level
    assert toml_cfg.metrics.port == yaml_cfg.metrics.port
    assert toml_cfg.observability.tracing.exporter == yaml_cfg.observability.tracing.exporter
    # Paths resolve against each file's own directory, so compare the tails.
    assert toml_cfg.buffer.db_path.name == yaml_cfg.buffer.db_path.name
    assert toml_cfg.persona.dir.name == yaml_cfg.persona.dir.name


def _copy(src, dst):
    shutil.copy(src, dst)
    return dst


def test_unknown_key_is_a_startup_failure(toml_path):
    toml_path.write_text(toml_path.read_text() + '\n[tagg]\ntrigger = "x"\n')
    with pytest.raises(ConfigError, match="tagg"):
        load(toml_path)


def test_unknown_nested_key_is_a_startup_failure(toml_path):
    toml_path.write_text(toml_path.read_text().replace(
        'text = "nora.000"', 'text = "nora.000"\ntriger = "oops"'))
    with pytest.raises(ConfigError, match="triger"):
        load(toml_path)


def test_missing_required_key_is_a_startup_failure(toml_path):
    toml_path.write_text(toml_path.read_text().replace('text = "nora.000"\n', ""))
    with pytest.raises(ConfigError, match="text"):
        load(toml_path)


def test_tilde_expands_and_paths_are_absolute(toml_path):
    cfg = load(toml_path)
    assert cfg.buffer.db_path.is_absolute()
    assert "~" not in str(cfg.buffer.db_path)
    assert cfg.logging.path.is_absolute()
    assert cfg.persona.dir.is_absolute()


def test_relative_paths_resolve_against_the_config_file_directory(toml_path):
    cfg = load(toml_path)
    assert cfg.persona.dir == (toml_path.parent / "nora" / "persona").resolve()


def test_missing_env_secret_is_a_startup_failure(toml_path, monkeypatch):
    monkeypatch.delenv("OMNIROUTE_API_KEY", raising=False)
    with pytest.raises(ConfigError, match="OMNIROUTE_API_KEY"):
        load(toml_path)


def test_config_never_holds_the_secret_value(toml_path):
    cfg = load(toml_path)
    assert cfg.llm.api_key_env == "OMNIROUTE_API_KEY"
    assert "test-key" not in repr(cfg)


def test_invalid_persona_reload_mode_is_a_startup_failure(toml_path):
    toml_path.write_text(toml_path.read_text().replace(
        'reload = "mtime"', 'reload = "inotify"'))
    with pytest.raises(ConfigError, match="reload"):
        load(toml_path)


def test_invalid_tracing_exporter_is_a_startup_failure(toml_path):
    toml_path.write_text(toml_path.read_text().replace(
        'exporter = "file"', 'exporter = "jaeger"'))
    with pytest.raises(ConfigError, match="exporter"):
        load(toml_path)


def test_otlp_exporter_without_an_endpoint_is_a_startup_failure(toml_path):
    toml_path.write_text(toml_path.read_text().replace(
        'exporter = "file"', 'exporter = "otlp"'))
    with pytest.raises(ConfigError, match="endpoint"):
        load(toml_path)


def test_config_is_immutable(toml_path):
    cfg = load(toml_path)
    with pytest.raises(Exception):        # pydantic raises ValidationError on frozen models
        cfg.persons_file = Path("/other")
```

- [ ] **Step 4: Run it to verify it fails**

```bash
uv run pytest tests/test_config_toml.py -q 2>&1 | tail -5
```

Expected: collection error — `ModuleNotFoundError: No module named 'nora.config_toml'`.

- [ ] **Step 5: Write `nora/config_toml.py`**

Mirror the existing tree in `nora/config.py` as pydantic models. Requirements:

- Every model: `model_config = ConfigDict(extra="forbid", frozen=True)`.
- Path fields typed `Path`, with a **model-level** validator that resolves them against the config file's directory. pydantic has no ambient knowledge of that directory, so pass it through validation context: `Config.model_validate(data, context={"base": path.parent})` and read it in a `@model_validator(mode="after")`. Reuse the exact semantics of `nora/config.py:144-146`:
  ```python
  def _resolve(value: Path, base: Path) -> Path:
      expanded = Path(str(value)).expanduser()
      return expanded.resolve() if expanded.is_absolute() else (base / expanded).resolve()
  ```
  The path fields are `buffer.db_path`, `logging.path`, `persona.dir`, `persons_file`.
- Enum-ish validation as `Literal[...]`: `persona.reload: Literal["mtime", "startup"]`, `persona.chinese_script: Literal["traditional", "simplified"]`, `observability.tracing.exporter: Literal["console", "otlp", "file"]`.
- A `@model_validator(mode="after")` on the tracing model enforcing that `exporter == "otlp"` requires a non-empty `endpoint`.
- `load(path)` reads with `tomllib.load(open(path, "rb"))`, validates, then checks `os.environ.get(cfg.llm.api_key_env)` and raises `ConfigError` if unset — same message as `nora/config.py:191`.
- **`ConfigError` must wrap pydantic's `ValidationError`**, so callers keep catching one exception type. Catch `ValidationError` in `load` and re-raise as `ConfigError(str(exc))`. The tests above match on substrings (`"tagg"`, `"triger"`, `"endpoint"`) that pydantic's message text does contain — do not try to reproduce the old message wording.
- Do NOT import or subclass anything from `nora/config.py`. The two must be independent so Task 2's deletion is clean.

- [ ] **Step 6: Run the tests to verify they pass**

```bash
uv run pytest tests/test_config_toml.py -q 2>&1 | tail -3
uv run pytest -q 2>&1 | tail -1
uv run ruff check nora/ evals/ tests/ && echo "RUFF CLEAN"
```

Expected: new file passes; full suite 454 + the new tests; ruff clean.

- [ ] **Step 7: Prove the strictness guard actually fires**

A silent-typo guard that has never been seen to fail is not a guard:

```bash
cp config.toml /tmp/cfg-probe.toml
sed -i 's/^text = "nora.000"/txet = "nora.000"/' /tmp/cfg-probe.toml
OMNIROUTE_API_KEY=x uv run python -c "
from nora.config_toml import load, ConfigError
try:
    load('/tmp/cfg-probe.toml'); print('!! ACCEPTED A TYPO — strictness is broken')
except ConfigError as e:
    print('rejected as expected:', str(e)[:120])
"
rm -f /tmp/cfg-probe.toml
```

Expected: `rejected as expected: …`. If it prints the failure line, `extra="forbid"` is not applied to every model — fix before committing.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml uv.lock nora/config_toml.py config.toml config.example.toml tests/test_config_toml.py
git commit -m "feat: pydantic-settings TOML config loader, parallel to the YAML one

Built alongside nora/config.py rather than replacing it, so this commit cannot
regress the suite. A field-for-field equality test against the YAML loader is
the proof that the translation is value-for-value.

Preserves the three behaviours that are easy to lose in a pydantic port:
extra='forbid' strictness (a typo in trigger.text would silently disable Nora),
base-relative path resolution with ~ expansion, and the refusal to start when
the API-key env var is unset."
```

---

### Task 2: Switch every caller to the TOML loader and delete the YAML one

**Files:**
- Delete: `nora/config.py`, `config.yaml`, `config.example.yaml`
- Rename: `nora/config_toml.py` → `nora/config.py`
- Modify: `nora/__main__.py:37`, `evals/run.py:47-48,107-118`, `tests/builders.py`, `tests/integration/harness.py`
- Delete: `tests/test_config.py` (replaced by `tests/test_config_toml.py`, which Task 2 renames to `tests/test_config.py`)
- Modify: `tests/test_env_template.py`, `tests/test_main.py`

**Interfaces:**
- Consumes: `nora.config_toml.load` from Task 1.
- Produces: `nora.config.load(path) -> Config` reading TOML. Attribute paths unchanged from today.

- [ ] **Step 1: Move the module and its tests into place**

```bash
git rm nora/config.py tests/test_config.py config.yaml config.example.yaml
git mv nora/config_toml.py nora/config.py
git mv tests/test_config_toml.py tests/test_config.py
```

In the moved test file, delete `test_toml_loader_matches_the_yaml_loader_field_for_field` and the `from nora.config import load as load_yaml` import — the YAML loader no longer exists, so the equality test has served its purpose and cannot run. Change `from nora.config_toml import` to `from nora.config import`.

**Before deleting `config.yaml`, confirm the backup exists** (Phase 0 created it):
```bash
ls -l config.yaml.pre-toml && diff <(grep -v '^#' config.yaml) /dev/null >/dev/null; echo "backup present"
```

- [ ] **Step 2: Update the callers**

- `nora/__main__.py`: `parser.add_argument("--config", default="config.yaml")` → `default="config.toml"`.
- `evals/run.py`: `_load_eval_config`'s default `candidates` tuple → `(REPO_ROOT / "config.toml", REPO_ROOT / "config.example.toml")`. Update the docstring's filenames.
- `tests/builders.py`: `load_example_config` copies `config.example.toml` to `tmp_path / "config.toml"`. **This is the seam — it should be the only place in `tests/` naming the file.**
- `tests/integration/harness.py`: already routed through `load_example_config`; verify no residual `config.yaml` string remains.
- `tests/test_main.py`: its `env` fixture builds config as a YAML dict and writes with `yaml.safe_dump`. Rewrite to load `config.example.toml` with `tomllib`, mutate the dict, and write with `tomli_w.dump`. Keep every assertion identical.
- `tests/test_env_template.py`: assert on `config.example.toml` parsed with `tomllib`; the expected top-level key set is unchanged in this task (`signal_cli, honcho, llm, trigger, buffer, persona, persons_file, logging, metrics, observability`).

- [ ] **Step 3: Verify nothing references the old files**

```bash
grep -rn "config\.yaml\|config\.example\.yaml" --include='*.py' --include='*.toml' --include='*.yml' --include='*.md' . \
  | grep -v '\.superpowers/' | grep -v 'docs/superpowers/' | grep -v '\.pre-toml'
```

Expected: only `docker-compose.yml`, `Dockerfile`, `.dockerignore`, `deploy/nora.service`, `README.md`, `docs/runbook.md` — all handled in Tasks 5 and 6. **Any `.py` hit is a missed caller.**

- [ ] **Step 4: Verify the suite and the linters**

```bash
uv run pytest -q 2>&1 | tail -1
uv run ruff check nora/ evals/ tests/ && echo "RUFF CLEAN"
```

Expected: same count as baseline ±the net test change; ruff clean.

- [ ] **Step 5: Verify the app still starts against the real config**

```bash
OMNIROUTE_API_KEY=$(grep '^OMNIROUTE_API_KEY=' .env | cut -d= -f2-) \
  uv run python -c "
import asyncio
from pathlib import Path
from nora.config import load
cfg = load(Path('config.toml'))
print('account:', cfg.signal_cli.account)
print('trigger:', cfg.trigger.text, '| timeout:', cfg.trigger.invocation_timeout_seconds)
print('exporter:', cfg.observability.tracing.exporter)
print('db:', cfg.buffer.db_path)
"
```

Expected exactly: `account: +85290000100`, `trigger: nora.000 | timeout: 30`, `exporter: file`, and an absolute `db` path ending `data/messages.db`. **Any deviation is a value-for-value violation — stop and fix.**

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor: switch to the TOML config loader, delete the YAML one

Same key names, same values, same attribute paths — only the format and the
validation library change. The field-for-field equality test from the previous
commit is deleted here because the loader it compared against is gone; its job
was to prove this switch is safe, and it did.

Verified against the real config.toml: account, trigger text, invocation
timeout and tracing exporter all match the pre-migration values."
```

---

### Task 3: Reshape the config tree to the target layout

Key renames only. **Every value stays.** Separated from Task 2 so that if a value moves by accident, `git bisect` lands on the reshape rather than on the library swap.

**Files:**
- Modify: `nora/config.py`, `config.toml`, `config.example.toml`
- Modify: `nora/__main__.py`, `nora/handler.py`, `nora/agent/factory.py` call sites, `nora/obs/*`, `evals/run.py`, `evals/judge.py`
- Modify: `tests/test_config.py`, `tests/test_env_template.py`, `tests/test_main.py`, `tests/builders.py`, `tests/integration/harness.py`

**Interfaces:**
- Produces the target attribute paths every later phase uses:
  `cfg.platforms.signal.{base_url,account,nora_sender_id,enabled}` · `cfg.memory.{base_url,workspace_id}` ·
  `cfg.store.{db_path,max_per_conversation,context_window}` · `cfg.llm.tiers.{flash,deep}.{model,max_tokens,temperature,timeout_seconds,limits}` ·
  `cfg.engagement.{policy,trigger.{text,min_interval_seconds}}` · `cfg.obs.{logging,metrics,tracing}` · `cfg.identity_file`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_config.py`:

```python
def test_config_uses_the_target_shape(toml_path):
    cfg = load(toml_path)
    # platforms replaces signal_cli, keyed by adapter name
    assert cfg.platforms.signal.base_url.startswith("http")
    assert cfg.platforms.signal.enabled is True
    assert cfg.platforms.signal.nora_sender_id == "nora"
    # memory replaces honcho
    assert cfg.memory.workspace_id == "nora"
    # store replaces buffer
    assert cfg.store.max_per_conversation == 500
    assert cfg.store.context_window == 40
    # tiers replace default/deep, and carry their own timeout
    assert cfg.llm.tiers.flash.model == "deepseek-v4-flash"
    assert cfg.llm.tiers.deep.model == "deepseek-v4-pro"
    assert cfg.llm.tiers.flash.timeout_seconds == 30
    assert cfg.llm.tiers.flash.limits.turns == 4
    # engagement owns the trigger; policy is declared even though only one exists
    assert cfg.engagement.policy == "trigger_text"
    assert cfg.engagement.trigger.text == "nora.000"
    assert cfg.engagement.trigger.min_interval_seconds == 5
    # obs groups the three signals
    assert cfg.obs.metrics.port == 9100
    assert cfg.obs.tracing.exporter == "file"
    assert cfg.obs.logging.level == "INFO"
    # identity_file replaces persons_file
    assert cfg.identity_file.name in ("persons.yaml", "identity.toml")
```

- [ ] **Step 2: Run it to verify it fails**

```bash
uv run pytest tests/test_config.py::test_config_uses_the_target_shape -q 2>&1 | tail -4
```

Expected: `AttributeError: 'Config' object has no attribute 'platforms'`.

- [ ] **Step 3: Apply the rename map**

| Old | New | Note |
|---|---|---|
| `signal_cli` | `platforms.signal` | gains `enabled = true` |
| `honcho` | `memory` | |
| `buffer` | `store` | keeps `db_path`; Phase 2 turns it into a SQLAlchemy `url` |
| `llm.default` | `llm.tiers.flash` | gains `timeout_seconds = 30` |
| `llm.deep` | `llm.tiers.deep` | gains `timeout_seconds = 30` |
| `llm.limits` | `llm.tiers.<tier>.limits` | **duplicated into both tiers** with today's values (`turns = 4`, `total_tokens = 24000`) — per-tier limits are the target shape |
| `trigger` | `engagement.trigger` | loses `invocation_timeout_seconds` → per-tier `timeout_seconds` |
| — | `engagement.policy = "trigger_text"` | new; only one policy exists |
| `logging` / `metrics` / `observability.tracing` | `obs.logging` / `obs.metrics` / `obs.tracing` | |
| `persons_file` | `identity_file` | still points at `persons.yaml` until Task 4 |

`platforms` is a model with one declared field `signal` — **not** a free-form dict, because `extra="forbid"` must keep rejecting an unknown platform name. A later phase widens this to a registry.

- [ ] **Step 4: Update every call site**

Find them all, then fix them:

```bash
grep -rn "cfg\.signal_cli\|cfg\.honcho\|cfg\.buffer\|cfg\.trigger\|cfg\.logging\|cfg\.metrics\|cfg\.observability\|cfg\.persons_file\|llm\.default\|llm\.deep\|llm\.limits" \
  --include='*.py' nora/ evals/ tests/
```

Known sites: `nora/__main__.py:42,47,51,54,59,71,95,99`, `nora/handler.py:38,71,106,132,165-179,186`, `evals/run.py`, `evals/judge.py:98`, `tests/integration/harness.py`, `tests/builders.py`.

`invocation_timeout_seconds` has one consumer, `nora/handler.py:192` — it becomes `self.cfg.llm.tiers.flash.timeout_seconds`. **The value is still 30.** Do not take the opportunity to change it.

- [ ] **Step 5: Update `tests/test_env_template.py`'s key-set assertion**

```python
assert set(data) == {
    "platforms", "llm", "memory", "store", "engagement",
    "persona", "identity_file", "obs",
}
```

- [ ] **Step 6: Verify values did not move**

```bash
uv run pytest -q 2>&1 | tail -1
uv run ruff check nora/ evals/ tests/ && echo "RUFF CLEAN"
OMNIROUTE_API_KEY=$(grep '^OMNIROUTE_API_KEY=' .env | cut -d= -f2-) \
  uv run python -c "
from pathlib import Path
from nora.config import load
c = load(Path('config.toml'))
print('account       :', c.platforms.signal.account)
print('trigger       :', c.engagement.trigger.text)
print('debounce      :', c.engagement.trigger.min_interval_seconds)
print('flash timeout :', c.llm.tiers.flash.timeout_seconds)
print('flash model   :', c.llm.tiers.flash.model)
print('deep model    :', c.llm.tiers.deep.model)
print('limits        :', c.llm.tiers.flash.limits.turns, c.llm.tiers.flash.limits.total_tokens)
print('window        :', c.store.context_window, '| cap:', c.store.max_per_conversation)
print('metrics port  :', c.obs.metrics.port)
print('exporter      :', c.obs.tracing.exporter)
print('canary        :', c.persona.canary)
"
```

Expected exactly: `+85290000100` · `nora.000` · `5` · `30` · `deepseek-v4-flash` · `deepseek-v4-pro` · `4 24000` · `40 | cap: 500` · `9100` · `file` · `NORA-CANARY-00000000`. **Any difference is a value-for-value violation.**

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor: reshape the config tree to the target layout

Key renames only — every runtime value is unchanged, verified by printing the
eleven load-bearing ones before and after. Split from the library swap so a
bisect lands on the reshape if a value moves.

platforms.signal replaces the top-level signal_cli key, which is what stops a
second adapter from needing a schema change. llm.tiers.{flash,deep} each carry
their own timeout_seconds and limits, replacing the single global
trigger.invocation_timeout_seconds — a tool-using deep invocation legitimately
needs a longer budget than a flash reply, and one global timeout cannot say so."
```

---

### Task 4: Move person mapping to `identity.toml`

**Files:**
- Create: `identity.toml`, `identity.example.toml`
- Delete: `persons.yaml`, `persons.example.yaml`
- Modify: `nora/persons.py`, `config.toml`, `config.example.toml`
- Modify: `tests/test_persons.py`, `tests/integration/harness.py`, `tests/test_main.py`

**Interfaces:**
- Produces: `nora.persons.load_persons(path)` reading TOML, returning the same `Persons` object with unchanged `resolve()` / `display_name()` behaviour.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_persons.py` (match the file's existing style):

```python
def test_loads_identity_toml(tmp_path):
    path = tmp_path / "identity.toml"
    path.write_text(
        '[persons.miles]\n'
        'name = "Miles"\n'
        '[persons.miles.platforms]\n'
        'signal = "+85212345678"\n'
        'telegram = 123456789\n'          # TOML integers must stringify
    )
    persons = load_persons(path)
    ref = persons.resolve("signal", "+85212345678")
    assert ref is not None
    assert ref.name == "Miles"
    assert ref.person_id == "miles"
    assert ref.honcho_peer_id == "miles"
    assert persons.resolve("telegram", "123456789") is not None
```

The `telegram` integer case matters: TOML has real integer types where YAML quoted everything. `nora/persons.py:65` already does `str(sender_id)`, so this should pass — the test pins that it keeps working.

- [ ] **Step 2: Run it to verify it fails**

```bash
uv run pytest tests/test_persons.py::test_loads_identity_toml -q 2>&1 | tail -4
```

Expected: failure from `yaml.safe_load` on TOML content.

- [ ] **Step 3: Convert the loader and add pydantic validation**

In `nora/persons.py`, replace `yaml.safe_load` with `tomllib.load(open(path,"rb"))`. Move the shape validation (persons mapping present, each person has `name` and `platforms`, `person_id` matches `^[a-zA-Z0-9_-]+$`) onto a small pydantic model, raising `PersonsError` on `ValidationError`. **The domain logic stays exactly as it is** — `resolve`, `display_name`, `sanitize_unmapped`, and duplicate detection are unchanged, per spec DEC-8.

Create `identity.toml` from `persons.yaml`, preserving the real values and the operator comments:

```toml
# Local person mapping (gitignored — never committed). Startup-only, no hot reload (Q4).
# An unmapped sender still works: Nora shows a redacted number instead of a name.

[persons.miles]
name = "Miles"
[persons.miles.platforms]
signal = "+85212345678"   # TODO(miles): replace with your real linked-account number
# telegram = 123456789    # future
```

Create `identity.example.toml` likewise. Point `identity_file = "identity.toml"` in both config files.

- [ ] **Step 4: Verify**

```bash
uv run pytest tests/test_persons.py -q 2>&1 | tail -2
uv run pytest -q 2>&1 | tail -1
uv run ruff check nora/ evals/ tests/ && echo "RUFF CLEAN"
grep -rn "persons\.yaml\|persons\.example\.yaml" --include='*.py' nora/ evals/ tests/ || echo "no stale references"
```

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor: person mapping moves to identity.toml with pydantic validation

One hand-edited format across the project. Validation moves to pydantic; the
domain logic (resolve, display_name, duplicate detection, unmapped-sender
sanitisation) stays custom, per spec DEC-8 — that logic is the part with real
semantics.

Pins that TOML integer sender IDs still resolve: persons.py already coerces
with str(), and Telegram numeric IDs will rely on it."
```

---

### Task 5: Update the deployment assets and verify the live container

**This task changes what the running production container mounts.** `docker-compose.yml:21-22` bind-mounts `./config.yaml` and `./persons.yaml`, both of which no longer exist.

**Files:**
- Modify: `docker-compose.yml`, `Dockerfile`, `.dockerignore`, `.env.example`, `deploy/nora.service`

- [ ] **Step 1: Update every asset that names the old files**

- `docker-compose.yml:21-22` → `./config.toml:/app/config.toml:ro` and `./identity.toml:/app/identity.toml:ro`
- `Dockerfile:22` `COPY config.example.yaml persons.example.yaml ./` → `COPY config.example.toml identity.example.toml ./`
- `Dockerfile:40` `CMD ["--config", "config.yaml"]` → `["--config", "config.toml"]`
- `Dockerfile:32` comment naming the mounted files
- `.dockerignore` — replace `config.yaml` / `persons.yaml` entries with `config.toml` / `identity.toml`
- `deploy/nora.service:12` `--config /etc/nora/config.yaml` → `config.toml`
- `.env.example` — no change needed, but confirm it still names `OMNIROUTE_API_KEY`

- [ ] **Step 2: Rebuild and restart**

```bash
docker build --network=host -t nora:p1a -t nora:latest .
docker compose up -d --no-build
```

`--network=host` is mandatory — BuildKit's default bridge cannot resolve `deb.debian.org` on this host.

- [ ] **Step 3: Verify the container is healthy on the new config**

```bash
for i in $(seq 1 12); do
  code=$(curl -s -o /tmp/h.json -w '%{http_code}' --max-time 5 http://127.0.0.1:9100/health) && [ "$code" = "200" ] && break
  sleep 5
done
echo "HTTP $code"
python3 -c "
import json; d=json.load(open('/tmp/h.json'))
print('sqlite :', d['sqlite'])
print('sse    :', d['sse'])
print('honcho :', d['honcho'])
assert d['sqlite']['reachable'] is True
assert 'sse' in d and d['sse']['account'] == '+85290000100'
print('OK')
"
rm -f /tmp/h.json
```

Expected: HTTP 200, `sqlite.reachable: true`, top-level `sse` with the **same account number as before the migration** — that account now comes from `config.toml`, so it is the end-to-end proof the value survived.

**If the container does not come back healthy: STOP and report BLOCKED** with `docker logs nora --tail 50`. Do not attempt repairs.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "build: point the deployment assets at config.toml and identity.toml

docker-compose bind-mounts the config by name, so the rename is a deployment
change, not just a source change. Verified end-to-end: the container comes back
healthy and /health still reports account +85290000100, which now comes from
config.toml — proof the value survived the migration."
```

---

### Task 6: Update the operator documentation

`docs/runbook.md:64,67,100` tells the operator to run `sudo systemctl stop nora` / `start` / `restart`. **Nora runs in Docker** (`docker ps` shows the `nora` container). The runbook's rollback procedure is therefore wrong today, and `tests/test_docs.py` passes it only because that test greps for the *word* "rollback".

**Files:**
- Modify: `docs/runbook.md`, `README.md`, `deploy/README.md`

- [ ] **Step 1: Fix the runbook's control commands**

Replace the systemd invocations with the Docker equivalents actually in use:

| Intent | Correct command |
|---|---|
| stop | `docker compose stop nora` |
| start | `docker compose up -d --no-build` |
| restart | `docker compose restart nora` |
| logs | `docker logs nora --tail 100 -f` |
| roll back to the previous image | `docker compose up -d --no-build` after retagging, e.g. `docker tag nora:p0 nora:latest` |

Note in the rollback section that `OMNIROUTE_API_KEY` must be available — it now comes from the gitignored `.env`, which Compose reads automatically.

- [ ] **Step 2: Update the config filenames in the docs**

```bash
grep -rn "config\.yaml\|persons\.yaml" README.md docs/runbook.md deploy/README.md
```

Update each hit to `config.toml` / `identity.toml`. `README.md` has roughly ten.

- [ ] **Step 3: Verify the docs tests still pass**

```bash
uv run pytest tests/test_docs.py tests/test_deploy_assets.py -q 2>&1 | tail -2
uv run pytest -q 2>&1 | tail -1
```

`test_docs.py` requires the README to still contain `strands`, `nora/persona/`, `OMNIROUTE_API_KEY`, `no restart`, `voice.md`, and to NOT contain `@Nora` or `max_per_group`; the runbook must still contain `rollback` and `restore`. Do not remove those words while editing.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "docs: correct the runbook for Docker and the TOML config filenames

The runbook told operators to systemctl stop/start/restart nora, but Nora runs
in a container — the documented rollback procedure could not work. test_docs.py
missed it because it only greps for the word 'rollback'."
```

---

## Phase 1a exit gate

```bash
uv run pytest -q                                  # green
uv run ruff check nora/ evals/ tests/             # clean
ls config.toml identity.toml                      # exist
ls config.yaml persons.yaml 2>/dev/null           # must NOT exist
docker images nora --format '{{.Tag}}'            # p1a AND latest
curl -s http://127.0.0.1:9100/health              # 200, sqlite.reachable, sse.account +85290000100
git log --oneline                                 # 6 task commits, one concern each
```

## Self-review notes

- **Spec coverage:** §5.1's config shape (Task 3), §5.2's identity file (Task 4), the value-for-value mandate in §7 (Tasks 1–3, each with an explicit value-comparison step), and the Phase 1 deployment/doc scope in §8 (Tasks 5–6).
- **Deliberately deferred to Phase 1b:** `nora/domain/` models and ports, the composition root, mypy/import-linter configuration, `ARCHITECTURE.md`, `CLAUDE.md`, and the decision index. Those need `domain/` to exist and are a separate reviewable concern.
- **Deliberate deviation from spec §5.1:** `store.db_path` stays a path here rather than becoming a SQLAlchemy `url`. Phase 2 owns the persistence swap; converting to a URL now would mean converting it back to a path for the still-aiosqlite code, which is churn with a chance of error and no benefit.
- **Type consistency:** `cfg.llm.tiers.flash.timeout_seconds` replaces `cfg.trigger.invocation_timeout_seconds` (one consumer, `handler.py:192`); `cfg.identity_file` replaces `cfg.persons_file`; `cfg.platforms.signal.*` replaces `cfg.signal_cli.*`. All three names are used consistently from Task 3 onward.
