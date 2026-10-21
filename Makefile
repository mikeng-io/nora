# Thin wrappers around the commands AGENTS.md/README.md already document as
# the gate and the deploy/release workflow, plus the fast-pass targets
# documented in CONTRIBUTING.md and AGENTS.md.
# Nothing here is a new command in spirit — every recipe is one line (or a
# small dispatch over one script's output) pulled from those docs, kept here
# so it does not have to be retyped. Edit the docs first if the underlying
# command changes; this file follows them, not the other way round.

.PHONY: bridge test test-fast test-affected lint typecheck imports gate full-gate mutate \
        setup hooks schema-parity heads decision-numbers \
        eval-participation eval-adversarial-corpora eval-gate \
        build up stop logs ps \
        branches

# --- setup (AGENTS.md: a fresh worktree needs this once, or pytest dies at
# COLLECTION on the eval modules — `strands-agents-evals` is an optional extra
# and each worktree has its own venv) ---

setup:
	uv sync --extra evals

# --- hooks (CONTRIBUTING.md §Commit messages: the commit-msg/pre-push check; .githooks/README.md: the identifier scan) ---

hooks:
	git config core.hooksPath .githooks
	@echo "core.hooksPath -> $$(git config core.hooksPath) (repo-level: one run per clone covers all worktrees; a hook fires only where the branch carries .githooks/)"
	@for h in pre-commit commit-msg pre-push; do if [ -x ".githooks/$$h" ] && [ ! -L ".githooks/$$h" ]; then echo "hook $$h: installed"; else echo "hook $$h: MISSING or not executable"; exit 1; fi; done
	@if [ -f "$${NORA_PII_DENYLIST:-$$HOME/.config/nora/pii-denylist.txt}" ]; then echo "pii denylist: present"; else echo "pii denylist: MISSING - every commit is an ERROR until you run scripts/pii_denylist_build.py (see .githooks/README.md)"; fi
	@if [ -f "$${NORA_PII_KEEP_PHRASES:-$$HOME/.config/nora/pii-keep-phrases.txt}" ]; then echo "pii keep-phrases: present"; else echo "pii keep-phrases: none (optional)"; fi
	@if [ -f "$${NORA_PII_PSEUDONYMS:-$$HOME/.config/nora/pii-pseudonyms.txt}" ]; then echo "pii pseudonyms: present"; else echo "pii pseudonyms: none (optional)"; fi

# --- gate (AGENTS.md: `make gate` once per PR, on the final head, before
# claiming it done; `make test-affected` or the tests you touched while
# iterating — never a gate per fix round; `make full-gate` for the whole
# serial suite unconditionally) ---

test:
	uv run pytest -q

lint:
	uv run ruff check nora/ evals/ tests/

typecheck:
	uv run mypy nora/domain nora/config.py nora/app nora/infra nora/presence nora/engagement nora/language nora/ingest nora/analytics nora/doctrine nora/skills nora/instructions nora/web

imports:
	uv run lint-imports

# `gate` checks what the BRANCH changed: ruff, mypy and lint-imports in full
# (fast), then only the tests scripts/affected_tests.py maps from the diff
# against origin/master, INCLUDING uncommitted edits and untracked files (the
# gate must judge the tree as it stands, not just the tree at HEAD — an
# uncommitted fix is still untested if the gate skips it). It fails closed: an
# unmapped or broad change, or a selector that fails or says nothing, runs the
# whole suite, so it is never weaker than full-gate on a change it cannot
# scope. `full-gate` is the whole suite, serial, unconditionally.
gate: lint typecheck imports
	@if ! git diff --quiet origin/master -- deploy/instagram-bridge 2>/dev/null \
		|| git ls-files --others --exclude-standard -- deploy/instagram-bridge | grep -q .; then \
		$(MAKE) --no-print-directory bridge; \
	else echo "bridge: deploy/instagram-bridge untouched, skipped"; fi
	@$(MAKE) --no-print-directory test-affected BASE=origin/master INCLUDE_WORKTREE=--include-worktree

full-gate: lint typecheck imports bridge test

# --- the Go instagram-bridge -------------------------------------------------
# Nothing else builds it before an image does: the 2026-10-14 history scrub
# rewrote every `chan` keyword in it to a pseudonym and master stayed red for
# two days without a signal. `gate` runs it when the diff touches the bridge,
# or when that diff cannot be computed (fail closed). Needs a Go toolchain.
bridge:
	cd deploy/instagram-bridge && go vet ./... && go test ./...

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
# For the edit-run-edit loop only. `make full-gate` above stays serial.

test-fast:
	uv run pytest -q -n 8

# Selects pytest targets from what actually changed (scripts/affected_tests.py,
# spec §3). BASE is the diff base: HEAD~1 (the last commit) by default,
# origin/master when `gate` runs it for the whole branch. INCLUDE_WORKTREE is
# empty by default (loop: committed history only, at HEAD~1, unchanged); `gate`
# sets it to `--include-worktree` so uncommitted edits and untracked files are
# judged too.
#
# FAILS CLOSED. The selector's one line of stdout is `ALL`, `NONE` or a list of
# targets (its module docstring). Only an explicit `NONE` runs nothing: a
# nonzero exit status, or an EMPTY line (a script that died before it could
# answer), runs the whole suite. This recipe used to read empty as "docs-only
# change", ignore the exit status, and pass with zero tests run when the script
# raised. SELECTOR and PYTEST are variables so tests/test_affected_tests.py can
# drive this exact recipe against a stub from the COMMAND LINE. They are
# assigned with `:=`, not `?=`, so an exported SELECTOR='echo NONE' in someone's
# shell cannot replace the gate's command: only `make SELECTOR=...` can.
BASE ?= HEAD~1
INCLUDE_WORKTREE ?=
SELECTOR := uv run python scripts/affected_tests.py
PYTEST := uv run pytest -q -n 8
test-affected:
	@targets="$$($(SELECTOR) --base $(BASE) $(INCLUDE_WORKTREE))"; rc=$$?; \
	if [ "$$rc" -ne 0 ] || [ -z "$$targets" ]; then \
		echo "affected_tests: selector failed (exit $$rc) or printed nothing, running the full suite"; \
		$(PYTEST); \
	elif [ "$$targets" = "ALL" ]; then \
		echo "affected_tests: unmapped or broad change, running the full suite"; \
		$(PYTEST); \
	elif [ "$$targets" = "NONE" ]; then \
		echo "affected_tests: no test reads this diff (NONE), nothing to run"; \
	else \
		echo "affected_tests: $$targets"; \
		$(PYTEST) $$targets; \
	fi

# --- eval gates (evals/scenarios, the SDK-native spine; the live ones need
# the LLM key env set) ---
#
# `python -m evals` runs a NAMED corpus (or `--class`, or `--all`) rather than
# a file path — `design/evaluation/10-the-scenario-map.md` §15's port put the
# 150 corpus cases behind `evals.scenarios.experiment_for()`, and adding a
# corpus adds a dict entry there, not a target here.
#
# WHERE IT RUNS: on the deploy host, against the deployment's clone
# (`nora_eval`, made by `scripts/refresh_eval_clone.sh`). Every case names a
# real room at a real instant, is seeded from the clone into a sandbox
# database of its own, and composes the deployment's own config, policy and
# roster (design/evaluation/11-the-sandbox-rebuild.md §1.2). Off the deploy
# host there is no source and no deployment to compose, and the run says so.

# Refresh the eval source from production. Deploy host only.
eval-source:
	./scripts/refresh_eval_clone.sh

# The ambient-participation flip gate (design/dreamer-agent-loop/35 §9): all
# 15 cases must reach their expected verdict — zero false-speaks on the
# knowledge-cut twins, zero speaks on the pure-No five.
eval-participation:
	uv run python -m evals participation

# Releases are cut on GitHub by release-please (DEC-314, the nora-release
# skill); there is no release target here.

# --- build / deploy (README.md "Deployment") ---

build:
	scripts/build-image.sh

up:
	docker compose up -d

stop:
	docker compose stop nora

logs:
	docker compose logs -f nora

ps:
	docker compose ps

# --- branch hygiene (docs/maintainers/conventions.md: "Cleanup") ---
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

# Empty output under both headings is what clean looks like. A sub-lettered
# row (`R-6b`) is its own id: the trailing `\|` keeps it from reading as its
# parent, and the optional letter keeps a duplicated `R-6b` from going unseen.
decision-numbers:
	@echo "== duplicate DEC numbers =="
	@grep -oE '^\| DEC-[0-9]+[a-z]? \|' design/09-decision-index.md | grep -oE '[0-9]+[a-z]?' | sort -V | uniq -d
	@echo "== duplicate R numbers =="
	@grep -oE '^\| R-[0-9]+[a-z]? \|' design/09-decision-index.md | grep -oE '[0-9]+[a-z]?' | sort -V | uniq -d

# The schema gate on its own (DEC-11: `alembic check` is blind to the CHECK
# constraint and the `ts DESC` index ordering, so this is the real one).
schema-parity:
	uv run pytest -q tests/infra/test_schema_parity.py

# --- live evals (docs/eval-sop.md "What gates a change"; they need the model
# and run on the deploy host, never part of the gate) ---

# Routing is the `tool_selection` corpus (since 21e5405d); this target runs the
# adversarial corpora, judged.
eval-adversarial-corpora:
	uv run python -m evals injections near_miss transcript_injection \
	  identity_grounding source_scope being_contradicted --judge

eval-gate:
	uv run python -m evals relevance_gate
