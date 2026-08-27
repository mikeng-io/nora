# Thin wrappers around the commands CLAUDE.md/README.md already document as
# the gate and the deploy/release workflow, plus the fast-pass targets
# documented in docs/superpowers/specs/2026-08-21-fast-test-pass-design.md.
# Nothing here is a new command in spirit — every recipe is one line (or a
# small dispatch over one script's output) pulled from those docs, kept here
# so it does not have to be retyped. Edit the docs first if the underlying
# command changes; this file follows them, not the other way round.

.PHONY: test test-fast test-affected lint typecheck imports gate \
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
