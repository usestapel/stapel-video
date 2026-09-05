"""Per-module contract triad + drift gate (contract-pipeline.md §2-3).

stapel-video emits its **own** contract triad — ``docs/schema.json``
(drf-spectacular OpenAPI), ``docs/flows.json`` (generate_flow_docs machine
artifact — empty here, video has no ``@flow_step`` annotations) and
``docs/errors.json`` (generate_error_keys registry) — plus ``capabilities.json``
(the fourth artifact) from a single-module ``{video + core}`` Django instance
mounted at the canonical ``/video/api/v1/`` prefix.

video is not mounted in stapel-example-monolith, so there is no aggregate slice
to diff against for byte-identity. Standalone validation (contract-pipeline.md
§9 fallback) substitutes: determinism, self-contained ``$ref`` closure, JWT
security on protected endpoints, canonical-prefix paths.

Regenerate after any serializer/view/url/error change:

    make contract        # or: python -m stapel_video._codegen --out docs

The harness runs in a subprocess: this test process already configured Django
(bare test urlconf) and the harness needs its own canonical-prefix urlconf +
drf-spectacular singleton — a clean interpreter is the honest way to exercise
exactly what ``make contract`` runs.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

_PY = sys.version_info[:2]
if _PY != (3, 12):
    pytest.skip(
        "stapel-video contract tests require Python 3.12 (the CI/monolith pin) "
        f"— running {_PY[0]}.{_PY[1]}. drf-spectacular renders component "
        "descriptions differently across Python minors, so drift/identity "
        "checks are only defined on 3.12.",
        allow_module_level=True,
    )

REPO = Path(__file__).resolve().parent.parent
DOCS = REPO / "docs"
TRIAD = ("schema.json", "flows.json", "errors.json")
# The fifth artifact (badge-canon §3): docs/llms.txt, rendered from
# docs/capabilities.json (+schema/errors/flows) by stapel_tools.llms_txt.
ARTIFACTS = TRIAD + ("capabilities.json", "llms.txt")
#: Must match the Makefile's `contract` / `contract-check` targets. Raised to
#: 8500 in 0.11.0 (from 6000 in 0.7.0, from 5000 in 0.6.0, from the
#: generator's 4000 default) when 1:1 calls added a second lifecycle to the
#: module — nine surface entries, four config axes and six error keys. Same
#: deliberate exception stapel-calendar (5000), stapel-workspaces (4500) and
#: stapel-auth (8000) already take. The budget stays enforced, just at 8500;
#: this constant existing is what makes a silent raise impossible.
LLMS_TXT_BUDGET = "8500"


def _emit(out_dir: Path) -> None:
    for module in ("stapel_video._codegen", "stapel_video._capabilities"):
        subprocess.run(
            [sys.executable, "-m", module, "--out", str(out_dir)],
            cwd=str(REPO),
            check=True,
            capture_output=True,
        )
    # llms.txt is rendered from the REAL committed docs/capabilities.json (not
    # the just-regenerated tmp one) — same as `make contract-check` — so this
    # step also catches a stale llms.txt independently of the loop above.
    subprocess.run(
        [
            sys.executable, "-m", "stapel_tools.llms_txt", ".",
            "--out", str(out_dir), "--budget", LLMS_TXT_BUDGET,
        ],
        cwd=str(REPO),
        check=True,
        capture_output=True,
    )


def test_contract_artifacts_committed():
    for name in ARTIFACTS:
        assert (DOCS / name).is_file(), f"missing docs/{name} — run `make contract`"
    assert (DOCS / "capabilities.meta.json").is_file()


def test_contract_has_no_drift(tmp_path):
    _emit(tmp_path)
    for name in ARTIFACTS:
        committed = (DOCS / name).read_bytes()
        regenerated = (tmp_path / name).read_bytes()
        assert committed == regenerated, (
            f"docs/{name} drifted — run `make contract` and commit docs/{name}"
        )


def test_emission_is_deterministic(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    _emit(a)
    _emit(b)
    for name in ARTIFACTS:
        assert (a / name).read_bytes() == (b / name).read_bytes()


def test_paths_carry_canonical_prefix():
    schema = json.loads((DOCS / "schema.json").read_text())
    assert schema["paths"], "schema has no paths"
    assert all(p.startswith("/video/api/v1/") for p in schema["paths"])


def test_flows_are_empty_no_flow_step_annotations():
    flows = json.loads((DOCS / "flows.json").read_text())
    assert flows == []


def _all_refs(obj) -> set:
    return set(re.findall(r'"#/components/schemas/([^"]+)"', json.dumps(obj)))


def test_schema_refs_are_self_contained():
    schema = json.loads((DOCS / "schema.json").read_text())
    comps = schema.get("components", {}).get("schemas", {})
    seen: set = set()
    stack = list(_all_refs(schema["paths"]))
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        if name in comps:
            stack.extend(_all_refs(comps[name]))
    dangling = seen - set(comps)
    assert not dangling, f"dangling $ref(s): {dangling}"


def test_protected_paths_carry_jwt_security():
    """Every authenticated operation carries JWTCookieAuth. The webhook is
    deliberately unauthenticated (provider-signed) and is exempt."""
    schema = json.loads((DOCS / "schema.json").read_text())
    missing = []
    for path, operations in schema["paths"].items():
        if path.endswith("/webhook"):
            continue
        for method, op in operations.items():
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            security = op.get("security") or []
            if not any("JWTCookieAuth" in entry for entry in security):
                missing.append(f"{method.upper()} {path}")
    assert not missing, f"operations missing JWTCookieAuth security: {missing}"


# --- capabilities.json content sanity (capability-config.md §2) ---------------


def _capabilities() -> dict:
    return json.loads((DOCS / "capabilities.json").read_text())


def test_capabilities_axes():
    """The exact axis set, restated here so a new one is a decision.

    An axis is what a CTO conversation asks about, and the emitter derives the
    list from `_capabilities._AXES`. Asserting the set — rather than a
    membership check — is what stops a tuning knob drifting into the CTO
    catalogue, and what makes adding a real axis a two-file edit somebody has
    to mean.
    """
    axes = {a["key"]: a for a in _capabilities()["axes"]}
    assert set(axes) == {
        "VIDEO_PROVIDER",
        "DEFAULT_ACCESS_LEVEL",
        "DEFAULT_ADMIT_REQUIRED",
        # 0.11.0 — each of the four is visible to the two people on the call:
        # how long their phone rings, how long a call may run, whether it
        # reaches them out of the app at all, and who may reach them.
        "CALL_RING_TIMEOUT_SECONDS",
        "CALL_MAX_DURATION_SECONDS",
        "CALL_NOTIFY_ON_RING",
        "CALL_AUTHORIZER",
    }
    assert axes["DEFAULT_ACCESS_LEVEL"]["default"] == "restricted"
    assert axes["DEFAULT_ADMIT_REQUIRED"]["kind"] == "bool"
    assert axes["CALL_RING_TIMEOUT_SECONDS"]["default"] == 45
    for axis in axes.values():
        # Behavioral, not gating: they change behavior, not which endpoints exist.
        assert axis["gates"]["operations"] == []
        assert axis["curated"]["business_label"]


def test_capabilities_extension_points_cover_the_seams():
    names = {e["name"] for e in _capabilities()["extension_points"]}
    assert {
        "VIDEO_PROVIDER",
        "SCOPE_PROVIDER",
        "video.egress_ended",
        # The call seams: who may ring whom, and where the thread line goes.
        "CALL_AUTHORIZER",
        "CALL_THREAD_MESSAGE_FUNCTION",
    } <= names


def test_capabilities_operations_total_matches_schema():
    schema = json.loads((DOCS / "schema.json").read_text())
    methods = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
    total = sum(1 for item in schema["paths"].values() for m in item if m in methods)
    assert _capabilities()["operations_total"] == total


def test_capabilities_envelope():
    import tomllib

    doc = _capabilities()
    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text())
    assert doc["module"] == pyproject["project"]["name"]
    assert doc["version"] == pyproject["project"]["version"]
    assert doc["provides"] and doc["extension_points"] and doc["requires"]


def test_capabilities_meta_declares_alpha_maturity():
    meta = json.loads((DOCS / "capabilities.meta.json").read_text())
    assert meta["maturity"] == "alpha"


# --- README.md — the sixth artifact (tracker #257) ---------------------------
#
# README.md is assembled by ``stapel_tools.readme`` from docs/readme.md (the
# human half: what this module is and how to think about it) plus the contract
# documents above (badges, version, surface counts, doc links). Everything a
# hand-written README used to restate — and therefore used to get wrong one
# release later — is generated here and gated below.

def test_readme_is_assembled_and_has_no_drift():
    from stapel_tools.readme import load_inputs, render, static_languages

    inputs = load_inputs(REPO)
    languages = static_languages(REPO)
    assert languages == ["en"], "expected exactly the English static body docs/readme.md"
    committed = (REPO / "README.md").read_text()
    assert committed == render(REPO, inputs, "en", languages), (
        "README.md drifted — run `make contract` and commit README.md "
        "(edit prose in docs/readme.md, never README.md itself)"
    )


def test_readme_version_matches_the_package():
    """The #226 gate, at the point where the number is published."""
    import tomllib

    from stapel_tools.readme import load_inputs, resolve_version

    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text())
    assert resolve_version(load_inputs(REPO)) == pyproject["project"]["version"]
