from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import copier
import pytest
from copier import Settings

TEMPLATE_ROOT = Path(__file__).resolve().parents[1]
NEUTRAL_DESCRIPTION = "A neutral sample application"

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
    """Copy copier.yml and template/ (never .git or .venv) into a new committed git repo.

    Tests render from a snapshot, so they do not depend on the real repository's commits,
    tags or uncommitted state.
    """
    destination.mkdir(parents=True)
    shutil.copy2(TEMPLATE_ROOT / "copier.yml", destination / "copier.yml")
    shutil.copytree(TEMPLATE_ROOT / "template", destination / "template")
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
