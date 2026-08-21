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

# Emit the contract triad + capabilities.json + llms.txt (the fifth contract
# artifact, stapel_tools.llms_txt) into docs/.
#
# --budget 5000: raised from the 4000 default in 0.6.0, when the presence
# meter added 14 surface entries (presence.py + tasks.py) to the 11 the room
# lifecycle already had. Raised deliberately rather than by shortening the
# intent lines: the surface section is the one part of this file an agent
# reads to avoid rewriting a mechanism that already exists, and "there is a
# sweeper, and here is why you must schedule it" does not survive being
# compressed to a clause (stapel-calendar 5000, stapel-workspaces 4500 and
# stapel-auth 8000 are the fleet's other deliberate ceilings).
#
# README.md is the SIXTH artifact (tracker #257): assembled by
# stapel_tools.readme from docs/readme.md (the human half — what this module
# is, how to think about it) plus everything emitted above. Badges, version,
# surface counts and doc links are generated, so a release cannot leave them
# behind. Edit docs/readme.md; never README.md.
contract:
	$(PYTHON) -m stapel_video._codegen --out docs
	$(PYTHON) -m stapel_video._capabilities --out docs
	$(PYTHON) -m stapel_tools.llms_txt . --out docs --budget 5000
	$(PYTHON) -m stapel_tools.readme .

# Drift gate: regenerate into a temp dir and diff against the committed docs/*.json.
contract-check:
	@tmp=$$(mktemp -d); \
	$(PYTHON) -m stapel_video._codegen --out "$$tmp" || { rm -rf "$$tmp"; exit 1; }; \
	$(PYTHON) -m stapel_video._capabilities --out "$$tmp" || { rm -rf "$$tmp"; exit 1; }; \
	$(PYTHON) -m stapel_tools.llms_txt . --out "$$tmp" --budget 5000 || { rm -rf "$$tmp"; exit 1; }; \
	rc=0; \
	for f in schema.json flows.json errors.json capabilities.json llms.txt; do \
		if ! diff -q "docs/$$f" "$$tmp/$$f" >/dev/null 2>&1; then \
			echo "DRIFT: docs/$$f is stale — run 'make contract' and commit it"; \
			diff "docs/$$f" "$$tmp/$$f" | head -20; rc=1; \
		fi; \
	done; \
	rm -rf "$$tmp"; \
	$(PYTHON) -m stapel_tools.readme . --check || rc=1; \
	if [ $$rc -eq 0 ]; then echo "contract-check: docs/{schema,flows,errors,capabilities,llms.txt} + README.md up to date"; fi; \
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
