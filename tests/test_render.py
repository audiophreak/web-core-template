from __future__ import annotations

import getpass
import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml

from conftest import NEUTRAL_DESCRIPTION, TEMPLATE_ROOT, render

EXACT_VERSION = re.compile(r"\d+\.\d+\.\d+")


def _text_files(project: Path):
    for path in project.rglob("*"):
        if path.is_file() and path.suffix != ".ico":
            yield path, path.read_text(encoding="utf-8")


# ------------------------------------------------------------------ generated content


def test_expected_files_exist(generated_app: Path) -> None:
    for relative in [
        "package.json", "package-lock.json", ".nvmrc", "AGENTS.md", "CLAUDE.md", "README.md",
        "components.json", "tsconfig.json", "eslint.config.mjs", "next.config.ts",
        "vitest.config.mts", ".prettierrc.json", ".copier-answers.yml",
        ".github/workflows/ci.yml", "src/app/page.tsx", "src/app/layout.tsx",
        "src/app/globals.css", "src/components/ui/button.tsx", "src/lib/utils.ts",
        "src/lib/app-info.json", "src/app/page.test.tsx", "src/components/ui/button.test.tsx",
    ]:  # fmt: skip
        assert (generated_app / relative).is_file(), relative


def test_metadata_is_substituted(generated_app: Path) -> None:
    package = json.loads((generated_app / "package.json").read_text())
    assert package["name"] == "sample-app"
    assert package["description"] == NEUTRAL_DESCRIPTION
    assert package["private"] is True
    assert package["engines"] == {"node": ">=26 <27"}
    info = json.loads((generated_app / "src/lib/app-info.json").read_text())
    assert info == {"name": "sample-app", "description": NEUTRAL_DESCRIPTION}
    readme = (generated_app / "README.md").read_text()
    assert readme.startswith("# sample-app\n")
    assert NEUTRAL_DESCRIPTION in readme
    assert (generated_app / ".nvmrc").read_text().strip() == "26"


def test_dependencies_are_exact_and_lockfile_is_in_sync(generated_app: Path) -> None:
    package = json.loads((generated_app / "package.json").read_text())
    lock = json.loads((generated_app / "package-lock.json").read_text())
    assert lock["name"] == "sample-app" == lock["packages"][""]["name"]
    for section in ("dependencies", "devDependencies"):
        for name, version in package[section].items():
            assert EXACT_VERSION.fullmatch(version), f"{name} is not pinned exactly: {version}"
            assert lock["packages"][f"node_modules/{name}"]["version"] == version
        assert lock["packages"][""][section] == package[section]


def test_scripts_define_the_validation_gate(generated_app: Path) -> None:
    scripts = json.loads((generated_app / "package.json").read_text())["scripts"]
    assert scripts["check"] == (
        "npm run lint && npm run typecheck && npm run format:check && npm run test && npm run build"
    )
    for name in ("lint", "typecheck", "test", "format", "format:check", "build"):
        assert name in scripts


def test_ci_workflow_is_minimal_and_pinned(generated_app: Path) -> None:
    text = (generated_app / ".github/workflows/ci.yml").read_text()
    workflow = yaml.safe_load(text)
    assert workflow["permissions"] == {"contents": "read"}
    steps = workflow["jobs"]["check"]["steps"]
    assert [step["run"] for step in steps if "run" in step] == ["npm ci", "npm run check"]
    for step in steps:
        if "uses" in step:
            assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", step["uses"]), step["uses"]
    assert "secrets." not in text


def test_agent_instructions_are_included(generated_app: Path) -> None:
    assert (generated_app / "CLAUDE.md").read_text().strip() == "@AGENTS.md"
    agents = (generated_app / "AGENTS.md").read_text()
    assert "npm run check" in agents and "Secure coding" in agents


def test_no_unrendered_template_syntax(generated_app: Path) -> None:
    for path, text in _text_files(generated_app):
        assert "{{" not in text and "{%" not in text, path
        assert "_copier_conf" not in text, path


def _template_source_text() -> str:
    parts = []
    for path in (TEMPLATE_ROOT / "template").rglob("*"):
        if path.is_file() and path.suffix != ".ico":
            parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def test_no_references_to_other_applications_or_the_developer(generated_app: Path) -> None:
    # Never legitimate in a generated project.
    always_forbidden = {"my-first-app", "/Users/", str(Path.home())}
    # Machine identity values: a leak only if they appear in the output but NOT in the template
    # source (then they must have come from the machine). A username like "build" or "test"
    # legitimately appears in template text, so it is not evidence of a leak there.
    template_text = _template_source_text()
    identities = {getpass.getuser()}
    for key in ("user.name", "user.email"):
        value = subprocess.run(
            ["git", "config", "--get", key], capture_output=True, text=True, check=False
        ).stdout.strip()
        identities.add(value)
    identities = {v for v in identities if len(v) >= 3 and v not in template_text}

    for path, text in _text_files(generated_app):
        if path.name == ".copier-answers.yml":
            # `_src_path` legitimately records where the template came from (here a
            # temporary directory); the answers themselves are checked below.
            text = "\n".join(l for l in text.splitlines() if not l.startswith("_src_path:"))
        for needle in always_forbidden | identities:
            assert needle not in text, f"{needle!r} found in {path.relative_to(generated_app)}"


def test_answers_file_records_answers_and_source(generated_app: Path, template_repo: Path) -> None:
    answers = yaml.safe_load((generated_app / ".copier-answers.yml").read_text())
    assert answers["app_name"] == "sample-app"
    assert answers["app_description"] == NEUTRAL_DESCRIPTION
    assert answers["cache_components"] is True
    assert answers["_src_path"] == str(template_repo)
    assert "_commit" in answers


def test_awkward_descriptions_render_as_valid_data(template_repo: Path, tmp_path: Path) -> None:
    description = "It's a \"quoted\" app (v1) & more: a/b, c+d!"
    app = tmp_path / "quote-app"
    render(template_repo, app, app_name="quote-app", app_description=description)
    assert json.loads((app / "package.json").read_text())["description"] == description
    assert json.loads((app / "src/lib/app-info.json").read_text())["description"] == description
    assert description in (app / "README.md").read_text()


# ------------------------------------------------------------------ validation


@pytest.mark.parametrize("name", ["demo", "my-app", "a1", "app-2-x", "a" * 64, "next", "react"])
def test_valid_names_are_accepted(template_repo: Path, tmp_path: Path, name: str) -> None:
    render(template_repo, tmp_path / "x", pretend=True, app_name=name)


@pytest.mark.parametrize(
    "name",
    ["", "Demo", "my_app", "-app", "app-", "a--b", "1app", "app.js", "a b", "../x",
     "@scope/x", "a" * 65, "ünï"],
)  # fmt: skip
def test_invalid_names_are_rejected(template_repo: Path, tmp_path: Path, name: str) -> None:
    with pytest.raises(ValueError, match="Validation error for question 'app_name'"):
        render(template_repo, tmp_path / "x", pretend=True, app_name=name)
    assert not (tmp_path / "x").exists()


@pytest.mark.parametrize(
    "description",
    ["A Next.js application", "Rock & roll (v1), \"quoted\" it's a/b + c-d!", "X"],
)
def test_valid_descriptions_are_accepted(template_repo: Path, tmp_path: Path, description: str) -> None:
    render(template_repo, tmp_path / "x", pretend=True, app_description=description)


@pytest.mark.parametrize(
    "description",
    ["", " leading", "trailing ", "double  space", "1 starts with digit", "has *star",
     "has `tick`", "has <tag>", "has [link]", "has {{ x }}", "has $(cmd)", "tab\there",
     "naïve", "x" * 201, "pipe | bar", "back\\slash", "hash # tag"],
)  # fmt: skip
def test_invalid_descriptions_are_rejected(
    template_repo: Path, tmp_path: Path, description: str
) -> None:
    with pytest.raises(ValueError, match="Validation error for question 'app_description'"):
        render(template_repo, tmp_path / "x", pretend=True, app_description=description)
    assert not (tmp_path / "x").exists()


# ------------------------------------------------------------------ options


def test_cache_components_enabled_sets_both_flags(generated_app: Path) -> None:
    config = (generated_app / "next.config.ts").read_text()
    assert "cacheComponents: true," in config
    assert "partialPrefetching: true," in config


def test_cache_components_disabled_omits_both_flags(template_repo: Path, tmp_path: Path) -> None:
    app = tmp_path / "plain-app"
    render(template_repo, app, app_name="plain-app", cache_components=False)
    config = (app / "next.config.ts").read_text()
    # partialPrefetching without cacheComponents makes `next build` throw, so never one alone.
    assert "cacheComponents" not in config and "partialPrefetching" not in config
    assert "@tailwindcss/turbopack" in config  # Tailwind wiring is unconditional
    answers = yaml.safe_load((app / ".copier-answers.yml").read_text())
    assert answers["cache_components"] is False


# ------------------------------------------------------------------ template safety


def test_template_runs_no_code_when_generating() -> None:
    config = yaml.safe_load((TEMPLATE_ROOT / "copier.yml").read_text())
    for key in ("_tasks", "_migrations", "_jinja_extensions"):
        assert key not in config, f"{key} would force --trust"
