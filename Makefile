# stapel-video — contract emission + drift gate (contract-pipeline.md §2-3).
#
# This module emits its OWN contract triad (schema.json + flows.json + errors.json)
# + capabilities.json per-module, from a single-module {video + core} Django
# instance mounted at the canonical /video/api/ prefix (see _codegen.py /
# _codegen_settings.py / codegen_urls.py).
#
# PYTHON must have the module + its deps importable (the workspace venv, or a CI
# venv). The authoritative CI gate is tests/test_contract.py (run under pytest);
# these targets are the dev-loop convenience.
PYTHON ?= python3

.PHONY: contract contract-check test lint

# Emit the contract triad + capabilities.json into docs/.
contract:
	$(PYTHON) -m stapel_video._codegen --out docs
	$(PYTHON) -m stapel_video._capabilities --out docs

# Drift gate: regenerate into a temp dir and diff against the committed docs/*.json.
contract-check:
	@tmp=$$(mktemp -d); \
	$(PYTHON) -m stapel_video._codegen --out "$$tmp" || { rm -rf "$$tmp"; exit 1; }; \
	$(PYTHON) -m stapel_video._capabilities --out "$$tmp" || { rm -rf "$$tmp"; exit 1; }; \
	rc=0; \
	for f in schema.json flows.json errors.json capabilities.json; do \
		if ! diff -q "docs/$$f" "$$tmp/$$f" >/dev/null 2>&1; then \
			echo "DRIFT: docs/$$f is stale — run 'make contract' and commit it"; \
			diff "docs/$$f" "$$tmp/$$f" | head -20; rc=1; \
		fi; \
	done; \
	rm -rf "$$tmp"; \
	if [ $$rc -eq 0 ]; then echo "contract-check: docs/{schema,flows,errors,capabilities}.json up to date"; fi; \
	exit $$rc

test:
	$(PYTHON) -m pytest tests/ -v

lint:
	ruff check . --select E,F,W --ignore E501


.PHONY: migration-lint

# Expand/contract gate for Django migrations (release-management.md §3;
# stapel_tools.migration_lint). Requires stapel-tools importable.
migration-lint:
	$(PYTHON) -m stapel_tools.migration_lint . --strict
