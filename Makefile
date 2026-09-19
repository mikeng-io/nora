# Thin wrappers around the commands AGENTS.md/README.md already document as
# the gate and the deploy/release workflow, plus the fast-pass targets
# documented in CONTRIBUTING.md and AGENTS.md.
# Nothing here is a new command in spirit — every recipe is one line (or a
# small dispatch over one script's output) pulled from those docs, kept here
# so it does not have to be retyped. Edit the docs first if the underlying
# command changes; this file follows them, not the other way round.

.PHONY: test test-fast test-affected lint typecheck imports gate mutate \
        setup hooks schema-parity heads decision-numbers \
        eval-participation eval-note-candidates eval-adversarial-corpora eval-gate \
        release-check release-notes release \
        build build-release up stop logs ps \
        branches

# --- setup (AGENTS.md: a fresh worktree needs this once, or pytest dies at
# COLLECTION on the eval modules — `strands-agents-evals` is an optional extra
# and each worktree has its own venv) ---

setup:
	uv sync --extra evals

# --- hooks (CONTRIBUTING.md §Commit messages: the commit-msg/pre-push check) ---

hooks:
	git config core.hooksPath .githooks
	@echo "core.hooksPath -> $$(git config core.hooksPath) (repo-level: one run per clone covers all worktrees; a hook fires only where the branch carries .githooks/)"

# --- gate (AGENTS.md: run before claiming anything is done, and before every commit) ---

test:
	uv run pytest -q

lint:
	uv run ruff check nora/ evals/ tests/

typecheck:
	uv run mypy nora/domain nora/config.py nora/app nora/infra nora/presence nora/engagement nora/language nora/ingest nora/analytics nora/doctrine nora/skills nora/instructions nora/web

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

# --- fast pass (for the edit-run-edit loop) ---
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

# --- eval gates (evals/scenarios, the SDK-native spine; the live ones need
# the LLM key env set) ---
#
# `python -m evals` runs a NAMED corpus (or `--class`, or `--all`) rather than
# a file path — `design/evaluation/10-the-scenario-map.md` §15's port put the
# 150 corpus cases behind `evals.scenarios.experiment_for()`, and adding a
# corpus adds a dict entry there, not a target here.

# The ambient-participation flip gate (design/dreamer-agent-loop/35 §9): all
# 15 cases must reach their expected verdict — zero false-speaks on the
# knowledge-cut twins, zero speaks on the pure-No five.
eval-participation:
	uv run python -m evals participation

# The note-candidates scenario suite (design/dreamer-agent-loop/37 §7, issue
# #118): 18 batches through the real Dreamer runtime + adaptor, N samples
# each, graded on the notebook. `make eval-note-candidates SAMPLES=5`
# measures with that many draws per case.
SAMPLES ?= 3
eval-note-candidates:
	uv run python -m evals note_candidates --sample $(SAMPLES)

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

# --- branch hygiene (CONTRIBUTING.md: "Cleanup") ---
# Reports only. Deleting is a decision, and a branch with a live worktree or an
# uncommitted change belongs to whoever is working in it.

branches:
	@echo "== worktrees =="; git worktree list
	@echo; echo "== local, merged into master (candidates; '+' = checked out elsewhere) =="
	@git branch --merged master | grep -v -E '^\*|master$$' || echo "  none"
	@echo; echo "== local, NOT merged =="
	@git branch --no-merged master || echo "  none"
	@echo; echo "== remote, merged into origin/master (candidates) =="
	@git branch -r --merged origin/master | grep -v -E 'HEAD|master$$' || echo "  none"
	@echo; echo "== remote, NOT merged =="
	@git branch -r --no-merged origin/master || echo "  none"
	@echo; echo "Delete with: git branch -d <b> ; git push origin --delete <b>"

# --- merge-time checks (AGENTS.md "Never"; each of these has a dated incident
# behind it, and until now each existed only as a command to retype by hand).
# Reports, not assertions: `uniq -d` exits 0 with no output, so read the output
# rather than trusting the exit code — that noisy-gate trap is why the real
# duplicate (two R-29 rows) sat on master for 15 days. ---

# Exactly ONE line, or the container fails at boot on `upgrade "head"`.
heads:
	uv run alembic heads

# Empty output under both headings is what clean looks like.
decision-numbers:
	@echo "== duplicate DEC numbers =="
	@grep -oE '^\| DEC-[0-9]+ \|' design/09-decision-index.md | grep -oE '[0-9]+' | sort -n | uniq -d
	@echo "== duplicate R numbers =="
	@grep -oE '^\| R-[0-9]+ \|' design/09-decision-index.md | grep -oE '[0-9]+' | sort -n | uniq -d

# The schema gate on its own (DEC-11: `alembic check` is blind to the CHECK
# constraint and the `ts DESC` index ordering, so this is the real one).
schema-parity:
	uv run pytest -q tests/infra/test_schema_parity.py

# --- evals, tier 2 (docs/eval-sop.md; network-marked, so excluded from `make
# test` by pyproject's addopts and never part of the gate) ---

# The ROUTING tier has no counterpart in the rebuilt layer (DEC-76 is marked
# NOT ENFORCED). This target now runs the corpora that DO exist, so it is not a
# command that lies about what it measures.
eval-adversarial-corpora:
	uv run python -m evals injections near_miss transcript_injection \
	  identity_grounding source_scope being_contradicted --judge

eval-gate:
	uv run python -m evals relevance_gate
