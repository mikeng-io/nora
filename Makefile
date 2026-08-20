# Thin wrappers around the commands CLAUDE.md/README.md already document as
# the gate and the deploy/release workflow. Nothing here is a new command —
# every recipe is one line pulled from those docs, kept here so it does not
# have to be retyped. Edit the docs first if the underlying command changes;
# this file follows them, not the other way round.

.PHONY: test lint typecheck imports gate \
        release-check release-notes release \
        build build-release up stop logs ps

# --- gate (CLAUDE.md: run before claiming anything is done, and before every commit) ---

test:
	uv run pytest -q

lint:
	uv run ruff check nora/ evals/ tests/

typecheck:
	uv run mypy nora/domain nora/config.py nora/app nora/infra nora/presence

imports:
	uv run lint-imports

gate: lint typecheck imports test

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
