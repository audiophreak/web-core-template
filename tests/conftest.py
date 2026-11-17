from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import copier
import pytest
import yaml
from copier import Settings

import features as feature_registry  # tools/ is on pytest's pythonpath

TEMPLATE_ROOT = Path(__file__).resolve().parents[1]
NEUTRAL_DESCRIPTION = "A neutral sample application"

REGISTRY = feature_registry.load_registry()
CONFIGURATIONS = feature_registry.configurations(REGISTRY)  # every supported feature selection


def config_id(selection: frozenset[str]) -> str:
    return "+".join(sorted(selection)) or "default"

# Hermetic git identity so tests never read (or depend on) the machine's git configuration.
GIT_TEST_ENV = {
    "GIT_AUTHOR_NAME": "Test User",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "Test User",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}


def run_git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        env={**os.environ, **GIT_TEST_ENV},
        check=True,
    )
    return result.stdout


def snapshot_repo(destination: Path) -> Path:
    """Copy copier.yml, template/ and locks/ (never .git or .venv) into a new committed git repo.

    Tests render from a snapshot, so they do not depend on the real repository's commits,
    tags or uncommitted state.
    """
    destination.mkdir(parents=True)
    shutil.copy2(TEMPLATE_ROOT / "copier.yml", destination / "copier.yml")
    shutil.copytree(TEMPLATE_ROOT / "template", destination / "template")
    shutil.copytree(TEMPLATE_ROOT / "locks", destination / "locks")  # included by package-lock.json.jinja
    run_git(destination, "init", "-q", "-b", "main")
    run_git(destination, "add", "-A")
    run_git(destination, "commit", "-q", "-m", "snapshot")
    return destination


def render(
    source: Path | str,
    destination: Path,
    *,
    ref: str | None = "HEAD",
    pretend: bool = False,
    **answers: object,
):
    data = {"app_name": "sample-app", "app_description": NEUTRAL_DESCRIPTION, **answers}
    return copier.run_copy(
        str(source),
        destination,
        data=data,
        defaults=True,
        vcs_ref=ref,
        settings=Settings(),  # ignore any user-level Copier settings
        unsafe=False,
        quiet=True,
        pretend=pretend,
    )


@pytest.fixture(scope="session")
def template_repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return snapshot_repo(tmp_path_factory.mktemp("snapshot") / "web-core-template")


@pytest.fixture(scope="session")
def generated_app(template_repo: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    destination = tmp_path_factory.mktemp("generated") / "sample-app"
    render(template_repo, destination)
    return destination


@pytest.fixture(scope="session", params=CONFIGURATIONS, ids=config_id)
def config_app(request, template_repo: Path, tmp_path_factory: pytest.TempPathFactory):
    """(selection, path) for every supported configuration in features.yml, rendered once."""
    selection: frozenset[str] = request.param
    destination = tmp_path_factory.mktemp("config") / "sample-app"
    render(template_repo, destination, **feature_registry.answers(selection, REGISTRY))
    return selection, destination


NUMERIC_COMMIT = "1234567"


def with_numeric_commit(project: Path) -> str:
    """Rewrite `_commit` in the project's answers file to an all-digit string, as Copier records it
    when the abbreviated hash of an untagged commit happens to be numeric. Serialized the way
    Copier's `to_nice_yaml` does (PyYAML safe_dump), which single-quotes it so it stays a string.
    Returns the new file text.
    """
    path = project / ".copier-answers.yml"
    answers = yaml.safe_load(path.read_text(encoding="utf-8"))
    answers["_commit"] = NUMERIC_COMMIT
    text = "# Changes here will be overwritten by Copier; NEVER EDIT MANUALLY\n" + yaml.safe_dump(
        answers, default_flow_style=False, sort_keys=True, allow_unicode=True
    )
    path.write_text(text, encoding="utf-8")
    return text


def answer_expression(name: str) -> str:
    """How a validator refers to a question's answer: by name, or through `_copier_answers` when the
    name is not a valid Jinja variable (the hyphenated `rest-api`)."""
    return name if name.isidentifier() else f"_copier_answers['{name}']"
