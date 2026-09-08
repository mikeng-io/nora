# Thin wrappers around the commands CLAUDE.md/README.md already document as
# the gate and the deploy/release workflow, plus the fast-pass targets
# documented in docs/superpowers/specs/2026-08-21-fast-test-pass-design.md.
# Nothing here is a new command in spirit — every recipe is one line (or a
# small dispatch over one script's output) pulled from those docs, kept here
# so it does not have to be retyped. Edit the docs first if the underlying
# command changes; this file follows them, not the other way round.

.PHONY: test test-fast test-affected lint typecheck imports gate mutate \
        eval-participation \
        release-check release-notes release \
        build build-release up stop logs ps

# --- gate (CLAUDE.md: run before claiming anything is done, and before every commit) ---

test:
	uv run pytest -q

lint:
	uv run ruff check nora/ evals/ tests/

typecheck:
	uv run mypy nora/domain nora/config.py nora/app nora/infra nora/presence nora/engagement

imports:
	uv run lint-imports

gate: lint typecheck imports test

# --- proving a fix is guarded (scripts/mutate.py) ---------------------------
# `gate` green says the suite passes. It does not say the suite would have
# NOTICED the bug you just fixed. Revert each fix, require its test to go RED.
# Deliberately NOT part of `gate`: a table names exact source text, so it is
# per-change and disposable, and a stale table failing the gate would teach
# people to delete tables rather than write them.
mutate:
	@test -n "$(TABLE)" || { echo 'usage: make mutate TABLE=scripts/mutations/<name>.json'; exit 2; }
	uv run python scripts/mutate.py $(TABLE)

# --- fast pass (docs/superpowers/specs/2026-08-21-fast-test-pass-design.md) ---
# For the edit-run-edit loop only. `make gate` above is unchanged and stays
# serial — these do not shrink or replace it.

test-fast:
	uv run pytest -q -n 8

# Selects pytest targets from what actually changed (scripts/affected_tests.py,
# spec §3), fails closed to the full parallel suite on anything it can't map
# with confidence.
test-affected:
	@targets="$$(uv run python scripts/affected_tests.py)"; \
	if [ "$$targets" = "ALL" ]; then \
		echo "affected_tests: unmapped or broad change, running the full suite"; \
		uv run pytest -q -n 8; \
	elif [ -z "$$targets" ]; then \
		echo "affected_tests: docs-only change, nothing to run"; \
	else \
		echo "affected_tests: $$targets"; \
		uv run pytest -q -n 8 $$targets; \
	fi

# --- eval gates (evals/run.py; the live ones need the LLM key env set) ---

# The ambient-participation flip gate (design/dreamer-agent-loop/35 §9): all
# 15 cases must reach their expected verdict — zero false-speaks on the
# knowledge-cut twins, zero speaks on the pure-No five.
eval-participation:
	uv run python -m evals.run --suite participation

# The note-candidates scenario suite (design/dreamer-agent-loop/37 §7, issue
# #118): ten batches through the real Dreamer runtime + adaptor, N samples
# each, graded on the notebook. `make eval-note-candidates SAMPLES=5
# DREAMER_DIR=/path/to/variant` measures a prompt variant held as files.
SAMPLES ?= 3
eval-note-candidates:
	uv run python -m evals.run --suite note_candidates --samples $(SAMPLES) \
		$(if $(DREAMER_DIR),--dreamer-dir $(DREAMER_DIR),) $(if $(EFFORT),--effort $(EFFORT),) \
		$(if $(MODEL),--model $(MODEL),) $(if $(PER_FIRE),--samples-per-fire $(PER_FIRE),)

# --- release fragments ---

release-check:
	uv run python scripts/check_release_fragments.py --base origin/master

release-notes:
	uv run python scripts/render_release_notes.py

release:
	uv run python scripts/release.py

# --- build / deploy (README.md "Deployment") ---

build:
	scripts/build-image.sh

# make build-release VERSION=V4
build-release:
	@if [ -z "$(VERSION)" ]; then \
		echo "usage: make build-release VERSION=V<n>" >&2; \
		exit 2; \
	fi
	scripts/build-release-image.sh $(VERSION)

up:
	docker compose up -d

stop:
	docker compose stop nora

logs:
	docker compose logs -f nora

ps:
	docker compose ps
