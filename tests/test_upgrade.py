"""Upgrade a generated project from an older template tag to a newer one with `copier update`.

This is the documented upgrade path (docs/upgrading.md). A bare repository stands in for the
published remote, so the project records a portable origin rather than a working-copy path.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from conftest import GIT_TEST_ENV, TEMPLATE_ROOT, render, run_git, snapshot_repo

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


def _update(project: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "copier", "update", "--defaults", "--skip-answered", "--quiet", *extra],
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


# ------------------------------------------------------------------ optional features


def _answers(project: Path) -> dict:
    return yaml.safe_load((project / ".copier-answers.yml").read_text())


def _lock(project: Path) -> dict:
    return json.loads((project / "package-lock.json").read_text())


SCHEMA_DIR = "src/lib/{% if zod %}schemas{% endif %}"


def test_a_selected_feature_survives_an_upgrade_with_user_work_intact(
    published, tmp_path: Path
) -> None:
    repo, remote = published
    project = tmp_path / "app"
    render(str(remote), project, ref="v0.1.0", app_name="feat-app", zod=True)
    assert _answers(project)["zod"] is True

    _adopt(project)
    own = project / "src/lib/schemas/contact.ts"  # the user's own schema, next to the template's
    own.write_text('import { z } from "zod";\n\nexport const contactSchema = z.object({ email: z.email() });\n')
    run_git(project, "add", "-A")
    run_git(project, "commit", "-q", "-m", "feat: contact schema")

    def release_020(template: Path) -> None:
        schema = template / SCHEMA_DIR / "app-info.ts"
        schema.write_text(schema.read_text().replace(".max(64)", ".max(48)"))
        (template / "CONTRIBUTING.md.jinja").write_text("# Contributing to {{ app_name }}\n")

    _release(repo, remote, "v0.2.0", release_020)
    result = _update(project, "--vcs-ref", "v0.2.0")
    assert result.returncode == 0, result.stderr

    answers = _answers(project)
    assert (answers["zod"], answers["_commit"]) == (True, "v0.2.0")  # the selection was kept
    assert ".max(48)" in (project / "src/lib/schemas/app-info.ts").read_text()  # template change
    assert own.read_text().count("contactSchema") == 1  # the user's file is untouched
    assert (project / "CONTRIBUTING.md").is_file()
    assert json.loads((project / "package.json").read_text())["dependencies"]["zod"] == "4.6.7"
    assert _lock(project)["packages"][""]["dependencies"]["zod"] == "4.6.7"
    assert USER_PAGE_EDIT in (project / "src/app/page.tsx").read_text()


def test_a_project_without_the_feature_stays_without_it(published, tmp_path: Path) -> None:
    repo, remote = published
    project = tmp_path / "app"
    render(str(remote), project, ref="v0.1.0", app_name="plain-app")
    _adopt(project)
    _release(repo, remote, "v0.2.0", lambda t: (t / "CONTRIBUTING.md.jinja").write_text("# x\n"))

    assert _update(project, "--vcs-ref", "v0.2.0").returncode == 0
    assert _answers(project)["zod"] is False
    assert not (project / "src/lib/schemas").exists()
    assert "zod" not in json.loads((project / "package.json").read_text())["dependencies"]
    assert "zod" not in _lock(project)["packages"][""]["dependencies"]


def test_a_feature_can_be_switched_on_later_through_an_update(published, tmp_path: Path) -> None:
    """`copier update --data zod=true` adds the files, the dependency and the matching lockfile."""
    repo, remote = published
    project = tmp_path / "app"
    render(str(remote), project, ref="v0.1.0", app_name="later-app")
    _adopt(project)
    _release(repo, remote, "v0.2.0", lambda t: (t / "CONTRIBUTING.md.jinja").write_text("# x\n"))

    result = _update(project, "--vcs-ref", "v0.2.0", "--data", "zod=true")
    assert result.returncode == 0, result.stderr
    assert _answers(project)["zod"] is True
    assert (project / "src/lib/schemas/app-info.ts").is_file()
    assert json.loads((project / "package.json").read_text())["dependencies"]["zod"] == "4.6.7"
    lock = _lock(project)
    assert lock["packages"][""]["dependencies"]["zod"] == "4.6.7"
    assert "dev" not in lock["packages"]["node_modules/zod"]
    for path in (project / "package.json", project / "package-lock.json"):
        assert "<<<<<<<" not in path.read_text()
    assert USER_PAGE_EDIT in (project / "src/app/page.tsx").read_text()


def test_projects_made_with_the_old_lenient_questions_upgrade_to_the_strict_ones(tmp_path: Path) -> None:
    """Answers recorded by `type: bool` questions are real booleans, so they survive the switch."""
    from test_answer_types import _legacy

    strict = (TEMPLATE_ROOT / "copier.yml").read_text()
    repo = snapshot_repo(tmp_path / "web-core-template")
    (repo / "copier.yml").write_text(_legacy(strict))  # the release before strictness
    run_git(repo, "commit", "-q", "-am", "lenient booleans")
    run_git(repo, "tag", "v0.1.0")
    remote = tmp_path / "published" / "web-core-template.git"
    remote.parent.mkdir()
    run_git(tmp_path, "clone", "-q", "--bare", str(repo), str(remote))

    project = tmp_path / "app"
    render(str(remote), project, ref="v0.1.0", app_name="legacy-app", zod=True, cache_components=False)
    assert _answers(project)["zod"] is True and _answers(project)["cache_components"] is False
    _adopt(project)

    (repo / "copier.yml").write_text(strict)  # the strict release
    run_git(repo, "commit", "-q", "-am", "strict booleans")
    run_git(repo, "tag", "v0.2.0")
    run_git(repo, "push", "-q", str(remote), "main", "v0.2.0")

    result = _update(project, "--vcs-ref", "v0.2.0")
    assert result.returncode == 0, result.stderr
    answers = _answers(project)
    assert (answers["zod"], answers["cache_components"], answers["_commit"]) == (True, False, "v0.2.0")
    assert (project / "src/lib/schemas/index.ts").is_file()
    assert "cacheComponents" not in (project / "next.config.ts").read_text()
    assert USER_PAGE_EDIT in (project / "src/app/page.tsx").read_text()
