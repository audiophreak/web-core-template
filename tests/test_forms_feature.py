"""The optional Forms feature: what a project gets with it, and what it must not get without it."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from conftest import render
from test_answer_types import _copier

SCHEMAS = "src/lib/schemas"
NEW_FILES = {
    "src/app/forms-example/page.tsx",
    "src/app/forms-example/page.test.tsx",
    "src/components/forms/example-form.tsx",
    "src/components/forms/example-form.test.tsx",
    f"{SCHEMAS}/example-form.ts",
    f"{SCHEMAS}/example-form.test.ts",
}
SHARED_FILES_THAT_CHANGE = {
    ".copier-answers.yml",
    "AGENTS.md",
    "README.md",
    "package.json",
    "package-lock.json",
    f"{SCHEMAS}/index.ts",
}
PRODUCTION = {"@hookform/resolvers": "5.9.2", "react-hook-form": "7.91.0"}
TESTING = {"@testing-library/dom": "10.4.2", "@testing-library/react": "16.3.3", "jsdom": "30.1.3"}
ZOD_ONLY_INDEX = 'export { appInfoSchema, type AppInfo } from "./app-info";\n'  # byte-for-byte as in v0.2.0
PERMISSIVE = {"MIT", "MIT-0", "ISC", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0", "0BSD", "BlueOak-1.0.0", "CC0-1.0"}


@pytest.fixture(scope="module")
def forms_app(template_repo: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    destination = tmp_path_factory.mktemp("forms") / "forms-app"
    render(template_repo, destination, app_name="forms-app", zod=True, forms=True)
    return destination


@pytest.fixture(scope="module")
def zod_only_app(template_repo: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    destination = tmp_path_factory.mktemp("zod-only") / "zod-app"
    render(template_repo, destination, app_name="forms-app", zod=True)  # same name: only `forms` differs
    return destination


def _files(project: Path) -> dict[str, bytes]:
    return {str(p.relative_to(project)): p.read_bytes() for p in sorted(project.rglob("*")) if p.is_file()}


def _package(project: Path) -> dict:
    return json.loads((project / "package.json").read_text())


def _lock(project: Path) -> dict:
    return json.loads((project / "package-lock.json").read_text())


# ------------------------------------------------------------------ disabled: the baseline


def test_disabled_projects_have_no_forms_dependencies_files_or_guidance(generated_app: Path, zod_only_app: Path) -> None:
    for project in (generated_app, zod_only_app):
        package = _package(project)
        assert not ({*PRODUCTION, *TESTING} & {*package["dependencies"], *package["devDependencies"]})
        assert not any(name in _lock(project)["packages"][""].get("dependencies", {}) for name in PRODUCTION)
        for name in NEW_FILES:
            assert not (project / name).exists(), name
        for existing in (project / "src" / "components", project / "src" / "app"):
            assert not any("forms" in p.name for p in existing.iterdir()), existing
        for relative in ("README.md", "AGENTS.md"):
            text = (project / relative).read_text()
            assert "React Hook Form" not in text and "forms-example" not in text, relative
        answers = yaml.safe_load((project / ".copier-answers.yml").read_text())
        assert answers["forms"] is False


def test_the_schema_index_is_byte_identical_to_v0_2_0_when_forms_is_off(zod_only_app: Path) -> None:
    assert (zod_only_app / SCHEMAS / "index.ts").read_text() == ZOD_ONLY_INDEX


def test_disabled_projects_contain_no_empty_directories(generated_app: Path, zod_only_app: Path) -> None:
    for project in (generated_app, zod_only_app):
        assert [p for p in project.rglob("*") if p.is_dir() and not any(p.iterdir())] == []


# ------------------------------------------------------------------ the rules: forms requires zod


@pytest.mark.parametrize("answers", [{"forms": True}, {"forms": True, "zod": False}, {"forms": "yes"}], ids=str)
def test_forms_without_zod_is_rejected_before_anything_is_written(template_repo: Path, tmp_path: Path, answers: dict) -> None:
    with pytest.raises(Exception, match="forms requires zod"):
        render(template_repo, tmp_path / "no", **answers)
    assert not (tmp_path / "no").exists()


def test_the_real_copier_command_line_rejects_forms_without_zod(template_repo: Path, tmp_path: Path) -> None:
    for data in (("forms=true",), ("forms=true", "zod=false")):
        result = _copier(template_repo, tmp_path / "cli", *data)
        assert result.returncode != 0 and "forms requires zod" in result.stderr
        assert not (tmp_path / "cli").exists()


def test_forms_never_turns_zod_on_by_itself(template_repo: Path, tmp_path: Path) -> None:
    project = tmp_path / "ok"
    render(template_repo, project, zod=True)  # zod alone leaves forms off
    answers = yaml.safe_load((project / ".copier-answers.yml").read_text())
    assert (answers["zod"], answers["forms"]) == (True, False)
    with pytest.raises(Exception, match="forms requires zod"):
        render(template_repo, tmp_path / "forms-only", forms=True)  # and forms alone does not enable zod


def test_forms_with_an_invalid_zod_answer_is_still_rejected(template_repo: Path, tmp_path: Path) -> None:
    with pytest.raises(Exception, match="zod"):
        render(template_repo, tmp_path / "bad", zod="banana", forms=True)
    assert not (tmp_path / "bad").exists()


# ------------------------------------------------------------------ enabled


def test_the_selection_is_recorded_and_the_lock_key_is_not(forms_app: Path) -> None:
    answers = yaml.safe_load((forms_app / ".copier-answers.yml").read_text())
    assert (answers["zod"], answers["forms"], answers["cache_components"]) == (True, True, True)
    assert "lock_key" not in answers


def test_enabled_project_adds_exactly_the_reviewed_dependencies(forms_app: Path, zod_only_app: Path) -> None:
    enabled, baseline = _package(forms_app), _package(zod_only_app)
    assert {k: v for k, v in enabled["dependencies"].items() if k not in PRODUCTION} == baseline["dependencies"]
    assert {k: v for k, v in enabled["devDependencies"].items() if k not in TESTING} == baseline["devDependencies"]
    assert {k: enabled["dependencies"][k] for k in PRODUCTION} == PRODUCTION
    assert {k: enabled["devDependencies"][k] for k in TESTING} == TESTING
    assert enabled["scripts"] == baseline["scripts"] and enabled["engines"] == baseline["engines"]
    for version in (*PRODUCTION.values(), *TESTING.values()):
        assert re.fullmatch(r"\d+\.\d+\.\d+", version)  # exact pins


def test_the_lockfile_matches_the_manifest_and_marks_testing_libraries_as_development_only(forms_app: Path) -> None:
    package, lock = _package(forms_app), _lock(forms_app)
    root = lock["packages"][""]
    assert root["dependencies"] == package["dependencies"] and root["devDependencies"] == package["devDependencies"]
    for name, version in {**PRODUCTION, **TESTING}.items():
        entry = lock["packages"][f"node_modules/{name}"]
        assert entry["version"] == version and "hasInstallScript" not in entry, name
        assert entry.get("dev") is (name in TESTING) or (name in PRODUCTION and "dev" not in entry), name
    for name in TESTING:
        assert lock["packages"][f"node_modules/{name}"]["dev"] is True
    for name in PRODUCTION:
        assert "dev" not in lock["packages"][f"node_modules/{name}"]


def test_every_package_the_feature_adds_has_a_permissive_license_and_no_install_script() -> None:
    from conftest import TEMPLATE_ROOT

    def load(key: str) -> dict:
        text = (TEMPLATE_ROOT / "locks" / f"{key}.json").read_text()
        return json.loads(text.replace("{{ app_name | to_json }}", '"x"'))["packages"]

    def name(path: str) -> str:
        return path.rsplit("node_modules/", 1)[-1]

    base = {(name(p), e["version"]) for p, e in load("zod").items() if p}
    added = {(name(p), e["version"]): e for p, e in load("forms+zod").items() if p and (name(p), e["version"]) not in base}
    assert added, "the forms lock should add packages"
    assert {e.get("license") for e in added.values()} <= PERMISSIVE
    assert not [k for k, e in added.items() if e.get("hasInstallScript")]
    assert {k[0] for k, e in added.items() if "dev" not in e} == {"@hookform/resolvers", "@standard-schema/utils", "react-hook-form"}


def test_enabling_forms_adds_exactly_the_expected_files_and_changes_only_shared_files(forms_app: Path, zod_only_app: Path) -> None:
    enabled, baseline = _files(forms_app), _files(zod_only_app)
    assert set(enabled) - set(baseline) == NEW_FILES
    assert set(baseline) - set(enabled) == set()
    assert {n for n in baseline if enabled[n] != baseline[n]} == SHARED_FILES_THAT_CHANGE


def test_the_schema_index_exports_the_form_schema(forms_app: Path) -> None:
    index = (forms_app / SCHEMAS / "index.ts").read_text()
    assert index.startswith(ZOD_ONLY_INDEX) and "exampleFormSchema" in index and "ExampleFormValues" in index


# ------------------------------------------------------------------ the example itself


def test_the_form_uses_the_shared_schema_and_never_redefines_its_rules(forms_app: Path) -> None:
    component = (forms_app / "src/components/forms/example-form.tsx").read_text()
    assert component.startswith('"use client";')
    assert 'from "@hookform/resolvers/zod"' in component and "zodResolver(exampleFormSchema)" in component
    assert 'from "@/lib/schemas"' in component
    assert 'from "zod"' not in component and not re.search(r"\bz\.", component), "rules belong in the schema"
    assert not re.search(r"\.(min|max|email|regex)\(", component), "no validation rules in the component"
    assert "useForm<ExampleFormValues>" in component  # types come from the schema
    schema = (forms_app / SCHEMAS / "example-form.ts").read_text()
    assert "z.infer<typeof exampleFormSchema>" in schema and "z.email(" in schema


def test_the_example_is_client_only_and_clearly_does_not_save_or_send(forms_app: Path) -> None:
    sources = {n: (forms_app / n).read_text() for n in NEW_FILES if not n.endswith(".test.tsx") and not n.endswith(".test.ts")}
    for name, text in sources.items():
        for forbidden in ("fetch(", "XMLHttpRequest", "sendBeacon", '"use server"', "'use server'", "localStorage", "sessionStorage", "/api/", "process.env", "cookies("):
            assert forbidden not in text, f"{name} must not use {forbidden}"
    component = " ".join(sources["src/components/forms/example-form.tsx"].split())  # JSX wraps lines
    assert "Nothing you enter is saved or sent anywhere." in component
    assert "Nothing was saved or sent." in component
    assert not (forms_app / "src/app/api").exists() and not (forms_app / "src/server").exists()


def test_the_form_is_accessible_by_construction(forms_app: Path) -> None:
    component = (forms_app / "src/components/forms/example-form.tsx").read_text()
    for needle in ("htmlFor=", "aria-invalid", "aria-describedby", 'role="alert"', 'role="status"', "noValidate", "aria-busy", 'type="email"', "autoComplete="):
        assert needle in component, needle
    assert component.count("<label") == 3 and component.count("htmlFor=") == 3


def test_the_shipped_tests_cover_the_required_behaviors(forms_app: Path) -> None:
    component_tests = (forms_app / "src/components/forms/example-form.test.tsx").read_text()
    assert component_tests.startswith("// @vitest-environment jsdom")
    for behavior in ("requires every field", "aria-invalid", "aria-describedby", "Submitting…", "passes trimmed, validated values",
                     "makes no network request", "toHaveBeenCalled", "fetchSpy", "XMLHttpRequest", "sendBeacon", "Thanks, Ada Lovelace"):  # fmt: skip
        assert behavior in component_tests, behavior
    schema_tests = (forms_app / SCHEMAS / "example-form.test.ts").read_text()
    assert "success).toBe(false)" in schema_tests and "expectTypeOf" in schema_tests
    assert "ada@example" in schema_tests and schema_tests.count('["email"') >= 6


def test_the_vitest_configuration_is_unchanged_so_only_form_tests_use_a_dom(forms_app: Path, generated_app: Path) -> None:
    assert (forms_app / "vitest.config.mts").read_bytes() == (generated_app / "vitest.config.mts").read_bytes()


def test_enabled_project_documents_the_feature(forms_app: Path) -> None:
    readme = (forms_app / "README.md").read_text()
    assert "## Forms (React Hook Form)" in readme and "/forms-example" in readme and "does not\nsave or send anything" in readme
    agents = (forms_app / "AGENTS.md").read_text()
    assert "React Hook Form" in agents and "zodResolver" in agents and "@vitest-environment jsdom" in agents


# ------------------------------------------------------------------ composition


def test_forms_composes_with_cache_components_off(template_repo: Path, tmp_path: Path) -> None:
    project = tmp_path / "plain"
    render(template_repo, project, app_name="plain-forms", zod=True, forms=True, cache_components=False)
    assert "cacheComponents" not in (project / "next.config.ts").read_text()
    assert _package(project)["dependencies"]["react-hook-form"] == "7.91.0"
    assert (project / "src/components/forms/example-form.tsx").is_file()
    assert _lock(project)["packages"][""]["dependencies"] == _package(project)["dependencies"]
