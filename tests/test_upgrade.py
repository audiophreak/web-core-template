"""Upgrade a generated project from an older template tag to a newer one with `copier update`.

This is the documented upgrade path (docs/versioning.md). A bare repository stands in for the
published remote, so the project records a portable origin rather than a working-copy path.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from conftest import GIT_TEST_ENV, render, run_git, snapshot_repo

USER_PAGE_EDIT = "Read the docs"


@pytest.fixture()
def published(tmp_path: Path):
    """(working repo, bare remote) with v0.1.0 pushed."""
    repo = snapshot_repo(tmp_path / "web-core-template")
    run_git(repo, "tag", "v0.1.0")
    remote = tmp_path / "published" / "web-core-template.git"
    remote.parent.mkdir()
    run_git(tmp_path, "clone", "-q", "--bare", str(repo), str(remote))
    return repo, remote


def _release(repo: Path, remote: Path, tag: str, edit) -> None:
    edit(repo / "template")
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-q", "-m", f"release {tag}")
    run_git(repo, "tag", tag)
    run_git(repo, "push", "-q", str(remote), "main", tag)


def _update(project: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "copier", "update", "--defaults", "--skip-answered", "--quiet"],
        cwd=project,
        capture_output=True,
        text=True,
        env={**os.environ, **GIT_TEST_ENV},
        check=False,
    )


def _adopt(project: Path) -> None:
    """The user's own work, committed (Copier requires a clean git repository to update)."""
    run_git(project, "init", "-q", "-b", "main")
    page = project / "src/app/page.tsx"
    assert "Next.js documentation" in page.read_text()
    page.write_text(page.read_text().replace("Next.js documentation", USER_PAGE_EDIT))
    readme = project / "README.md"
    readme.write_text(readme.read_text() + "\nLocal note kept across upgrades.\n")
    run_git(project, "add", "-A")
    run_git(project, "commit", "-q", "-m", "initial")


def test_upgrade_between_tags_preserves_user_modified_files(published, tmp_path: Path) -> None:
    repo, remote = published
    project = tmp_path / "app"
    render(str(remote), project, ref="v0.1.0", app_name="upgrade-app")
    answers = yaml.safe_load((project / ".copier-answers.yml").read_text())
    assert (answers["_src_path"], answers["_commit"]) == (str(remote), "v0.1.0")
    _adopt(project)

    def release_020(template: Path) -> None:
        (template / "CONTRIBUTING.md.jinja").write_text("# Contributing to {{ app_name }}\n")
        readme = template / "README.md.jinja"
        readme.write_text(
            readme.read_text().replace(
                "- npm (bundled with Node.js)", "- npm 11 or newer (bundled with Node.js)"
            )
        )

    _release(repo, remote, "v0.2.0", release_020)

    result = _update(project)
    assert result.returncode == 0, result.stderr

    page = (project / "src/app/page.tsx").read_text()
    assert USER_PAGE_EDIT in page and "Next.js documentation" not in page  # user edit preserved
    readme = (project / "README.md").read_text()
    assert "npm 11 or newer" in readme  # template change merged in
    assert "Local note kept across upgrades." in readme  # user change kept
    assert "<<<<<<<" not in readme and "<<<<<<<" not in page
    assert (project / "CONTRIBUTING.md").read_text() == "# Contributing to upgrade-app\n"
    answers = yaml.safe_load((project / ".copier-answers.yml").read_text())
    assert (answers["_src_path"], answers["_commit"]) == (str(remote), "v0.2.0")


def test_upgrade_with_conflicting_edits_leaves_inline_markers_for_the_user(
    published, tmp_path: Path
) -> None:
    repo, remote = published
    project = tmp_path / "app"
    render(str(remote), project, ref="v0.1.0")
    run_git(project, "init", "-q", "-b", "main")
    (project / ".nvmrc").write_text("26.2\n")  # the user changes the same line the template will
    run_git(project, "add", "-A")
    run_git(project, "commit", "-q", "-m", "initial")

    _release(repo, remote, "v0.2.0", lambda template: (template / ".nvmrc").write_text("26.0\n"))

    assert _update(project).returncode == 0
    merged = (project / ".nvmrc").read_text()
    assert "<<<<<<<" in merged and "26.2" in merged and "26.0" in merged  # nothing silently lost


def test_upgrade_refuses_a_dirty_project_and_changes_nothing(published, tmp_path: Path) -> None:
    repo, remote = published
    project = tmp_path / "app"
    render(str(remote), project, ref="v0.1.0")
    _adopt(project)
    (project / "src/app/page.tsx").write_text("uncommitted\n")
    _release(repo, remote, "v0.2.0", lambda template: (template / ".nvmrc").write_text("26.0\n"))

    result = _update(project)
    assert result.returncode != 0
    assert "dirty" in (result.stderr + result.stdout).lower()
    assert (project / "src/app/page.tsx").read_text() == "uncommitted\n"
    assert (project / ".nvmrc").read_text().strip() == "26"
