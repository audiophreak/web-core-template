"""The optional Zod feature: what a project gets with it, and what it must not get without it."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from conftest import render

SCHEMA_FILES = ("app-info.ts", "app-info.test.ts", "index.ts")


@pytest.fixture(scope="module")
def zod_app(template_repo: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    destination = tmp_path_factory.mktemp("zod") / "zod-app"
    render(template_repo, destination, app_name="zod-app", zod=True)
    return destination


def _package(project: Path) -> dict:
    return json.loads((project / "package.json").read_text())


# ------------------------------------------------------------------ disabled (the baseline)


def test_disabled_projects_have_no_zod_dependency_files_or_guidance(generated_app: Path) -> None:
    assert "zod" not in _package(generated_app)["dependencies"]
    assert not (generated_app / "src/lib/schemas").exists()
    for relative in ("README.md", "AGENTS.md"):
        assert "Zod" not in (generated_app / relative).read_text(), relative
    answers = yaml.safe_load((generated_app / ".copier-answers.yml").read_text())
    assert answers["zod"] is False


def test_disabled_projects_contain_no_empty_directories(generated_app: Path) -> None:
    empty = [p for p in generated_app.rglob("*") if p.is_dir() and not any(p.iterdir())]
    assert empty == []


def test_disabled_lock_has_zod_only_as_a_development_dependency(generated_app: Path) -> None:
    lock = json.loads((generated_app / "package-lock.json").read_text())
    assert lock["packages"]["node_modules/zod"].get("dev") is True  # via the shadcn CLI
    assert "zod" not in lock["packages"][""]["dependencies"]


# ------------------------------------------------------------------ enabled


def test_enabled_project_pins_zod_exactly_and_adds_nothing_else(
    zod_app: Path, generated_app: Path
) -> None:
    enabled, baseline = _package(zod_app), _package(generated_app)
    assert enabled["dependencies"]["zod"] == "4.6.7"
    without_zod = {k: v for k, v in enabled["dependencies"].items() if k != "zod"}
    assert without_zod == baseline["dependencies"]
    assert enabled["devDependencies"] == baseline["devDependencies"]
    assert enabled["scripts"] == baseline["scripts"]


def test_enabled_lock_promotes_zod_to_a_production_dependency_at_the_pinned_version(
    zod_app: Path,
) -> None:
    lock = json.loads((zod_app / "package-lock.json").read_text())
    entry = lock["packages"]["node_modules/zod"]
    assert entry["version"] == "4.6.7" and "dev" not in entry
    assert lock["packages"][""]["dependencies"]["zod"] == "4.6.7"
    assert "hasInstallScript" not in entry


def test_enabled_project_gets_the_shared_schema_module_and_tests(zod_app: Path) -> None:
    for name in SCHEMA_FILES:
        assert (zod_app / "src/lib/schemas" / name).is_file(), name
    schema = (zod_app / "src/lib/schemas/app-info.ts").read_text()
    assert 'from "zod"' in schema and "appInfoSchema" in schema and "strictObject" in schema
    index = (zod_app / "src/lib/schemas/index.ts").read_text()
    assert "appInfoSchema" in index and "AppInfo" in index


def test_the_generated_tests_show_both_acceptance_and_rejection(zod_app: Path) -> None:
    tests = (zod_app / "src/lib/schemas/app-info.test.ts").read_text()
    assert "success).toBe(true)" in tests and "success).toBe(false)" in tests
    assert tests.count('["a') >= 8, "expected a table of invalid inputs"
    assert "app-info.json" in tests  # the schema is exercised against the app's real data


def test_enabled_project_documents_the_feature(zod_app: Path) -> None:
    assert "## Validation (Zod)" in (zod_app / "README.md").read_text()
    agents = (zod_app / "AGENTS.md").read_text()
    assert "src/lib/schemas/" in agents and "safeParse" in agents


def test_enabled_selection_is_recorded_in_the_answers_file(zod_app: Path) -> None:
    answers = yaml.safe_load((zod_app / ".copier-answers.yml").read_text())
    assert answers["zod"] is True and answers["cache_components"] is True
    assert "lock_key" not in answers


def test_zod_composes_with_the_other_option(template_repo: Path, tmp_path: Path) -> None:
    project = tmp_path / "both"
    render(template_repo, project, app_name="both-app", zod=True, cache_components=False)
    assert "cacheComponents" not in (project / "next.config.ts").read_text()
    assert _package(project)["dependencies"]["zod"] == "4.6.7"
    assert (project / "src/lib/schemas/index.ts").is_file()
