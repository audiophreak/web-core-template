"""The optional native_client feature: what a project gets with it, what it needs, and what it must not get without it."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import render

NATIVE_ONLY_FILES = {"docs/native-client.md", "openapi/openapi.json", "scripts/openapi.mjs", "src/lib/openapi/document.ts", "src/lib/openapi/document.test.ts", "src/server/api/openapi-contract.test.ts"}
AUTH = {"zod": True, "rest_api": True, "auth": True}


@pytest.fixture(scope="module")
def apps(template_repo: Path, tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("native")
    chosen = {
        "pg": {**AUTH, "database": "postgres"},
        "sb": {**AUTH, "database": "supabase"},
        "pg+native": {**AUTH, "database": "postgres", "native_client": True},
        "sb+native": {**AUTH, "database": "supabase", "native_client": True},
        "pg+forms+native": {**AUTH, "database": "postgres", "forms": True, "native_client": True},
    }
    apps = {}
    for label, answers in chosen.items():
        apps[label] = root / label.replace("+", "-")
        render(template_repo, apps[label], app_name="native-app", **answers)
    return apps


def files(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize(
    "answers, message",
    [
        ({"native_client": True}, "native_client requires auth"),
        ({"native_client": True, "zod": True, "rest_api": True, "database": "postgres"}, "native_client requires auth"),
    ],
)
def test_native_client_without_auth_is_rejected_before_anything_is_written(template_repo: Path, tmp_path: Path, answers: dict, message: str) -> None:
    destination = tmp_path / "rejected"
    with pytest.raises(Exception) as caught:
        render(template_repo, destination, **answers)
    assert message in str(caught.value) and not destination.exists()


@pytest.mark.parametrize("given", ["banana", "1", "0", "maybe", "tRuE", "", None, 2])
def test_native_client_is_a_strict_boolean(template_repo: Path, tmp_path: Path, given: object) -> None:
    with pytest.raises(Exception, match="native_client"):
        render(template_repo, tmp_path / "x", **AUTH, database="postgres", native_client=given)
    assert not (tmp_path / "x").exists()


def test_without_native_client_nothing_native_is_generated(apps) -> None:
    for label in ("pg", "sb"):
        assert not (files(apps[label]) & NATIVE_ONLY_FILES), label
        assert "bearer" not in (apps[label] / "src/server/auth/auth.ts").read_text()
        assert "openapi" not in (apps[label] / "package.json").read_text()


def test_every_native_file_exists_and_nothing_else_is_added(apps) -> None:
    for plain_label, label in (("pg", "pg+native"), ("sb", "sb+native")):
        added = files(apps[label]) - files(apps[plain_label])
        assert added == NATIVE_ONLY_FILES, (label, added ^ NATIVE_ONLY_FILES)
        assert files(apps[plain_label]) - files(apps[label]) == set()


def test_the_only_dependency_files_that_differ_are_the_script_and_no_package_is_added(apps) -> None:
    plain, native = (json.loads((apps[k] / "package.json").read_text()) for k in ("pg", "pg+native"))
    assert native["dependencies"] == plain["dependencies"] and native["devDependencies"] == plain["devDependencies"]
    assert {k for k in native["scripts"] if k not in plain["scripts"]} == {"openapi:generate"}
    assert (apps["pg"] / "package-lock.json").read_text() == (apps["pg+native"] / "package-lock.json").read_text()  # same lock key, same lock


def test_the_bearer_plugin_requires_the_signature_and_nothing_else_changes_in_auth(apps) -> None:
    plain = (apps["pg"] / "src/server/auth/auth.ts").read_text()
    native = (apps["pg+native"] / "src/server/auth/auth.ts").read_text()
    assert "bearer({ requireSignature: true })" in native and "better-auth/plugins" in native
    assert native.replace('import { bearer } from "better-auth/plugins";\n', "").replace(native[native.index("    // Native clients"):native.index("    emailAndPassword")], "") == plain
    assert "cookieCache: { enabled: false }" in native and "disableCSRFCheck: false" in native and "disableOriginCheck: false" in native


def test_the_contract_is_identical_for_both_providers_and_with_forms(apps) -> None:
    contract = (apps["pg+native"] / "openapi/openapi.json").read_text()
    assert contract == (apps["sb+native"] / "openapi/openapi.json").read_text() == (apps["pg+forms+native"] / "openapi/openapi.json").read_text()
    for name in ("src/lib/openapi/document.ts", "src/lib/openapi/document.test.ts", "scripts/openapi.mjs", "docs/native-client.md"):
        assert (apps["pg+native"] / name).read_text() == (apps["sb+native"] / name).read_text(), name


def test_the_contract_describes_the_api_it_promises(apps) -> None:
    document = json.loads((apps["pg+native"] / "openapi/openapi.json").read_text())
    assert document["openapi"] == "3.1.0"
    operations = {f"{m.upper()} {p}": op for p, item in document["paths"].items() for m, op in item.items() if m in ("get", "post", "patch", "delete", "put")}
    assert sorted(operations) == sorted([
        "GET /api/v1/app-info", "POST /api/v1/examples/validate", "GET /api/v1/session", "GET /api/v1/sessions",
        "GET /api/v1/items", "POST /api/v1/items", "GET /api/v1/items/{id}", "PATCH /api/v1/items/{id}", "DELETE /api/v1/items/{id}",
    ])  # fmt: skip
    assert document["components"]["securitySchemes"]["bearerAuth"]["scheme"] == "bearer"
    for key, op in operations.items():
        assert op["security"] == ([] if "app-info" in key or "examples" in key else [{"bearerAuth": []}]), key
        assert op["operationId"], key
    codes = {c for c in json.loads(json.dumps(document["components"]["schemas"]["Problem"]))["properties"]["code"]["examples"]}  # an open string in responses: the codes are listed, a new one must not break a client
    assert "enum" not in document["components"]["schemas"]["Problem"]["properties"]["code"]
    assert {"unauthenticated", "origin_not_allowed", "validation_failed", "not_found", "internal_error"} <= codes
    assert "token" not in json.dumps(document["components"]["schemas"]).lower()


def test_the_documentation_states_the_boundary_and_the_rules(apps) -> None:
    doc = (apps["pg+native"] / "docs/native-client.md").read_text()
    for phrase in ("One session system", "requireSignature", "set-auth-token", "Boundary", "openapi:generate", "Keychain", "Adopt a new token"):
        assert phrase in doc, phrase
    agents = (apps["pg+native"] / "AGENTS.md").read_text()
    assert "openapi:generate" in agents and "docs/native-client.md" in agents
    assert "native-client" not in (apps["pg"] / "AGENTS.md").read_text()


def test_the_web_tooling_leaves_the_native_ios_layer_alone_only_when_native_client_is_on(apps) -> None:
    native, plain = apps["pg+native"], apps["pg"]
    assert "/ios\n" in (native / ".prettierignore").read_text() and "/.copier-answers.ios.yml" in (native / ".prettierignore").read_text()
    assert '"ios/**"' in (native / "eslint.config.mjs").read_text()
    assert "ios" not in (plain / ".prettierignore").read_text() and "ios" not in (plain / "eslint.config.mjs").read_text()
