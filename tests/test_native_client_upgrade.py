"""native_client and `copier update`: enabling it on a real, published v0.6.1 authentication project.

The old side is the real, published `v0.6.1` (taken from this repository's tags); the working tree is committed on
top of it as the next release. Skips without the tag (a shallow clone); the release gate runs with full history.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import TEMPLATE_ROOT, render, run_git
from test_auth_upgrade import _extract, _no_markers, _user_work_is_intact, OWN_ROUTE  # noqa: F401
from test_upgrade import USER_PAGE_EDIT, _adopt, _answers, _update

PUBLISHED = "v0.6.1"
NATIVE_FILES = ("openapi/openapi.json", "docs/native-client.md", "scripts/openapi.mjs", "src/lib/openapi/document.ts", "src/lib/openapi/document.test.ts")


def _available() -> bool:
    try:
        run_git(TEMPLATE_ROOT, "rev-parse", "-q", "--verify", f"{PUBLISHED}^{{commit}}")
    except subprocess.CalledProcessError:
        return False
    return True


def build_history(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "web-core-template"
    repo.mkdir()
    run_git(repo, "init", "-q", "-b", "main")
    _extract(PUBLISHED, repo)
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-q", "-m", f"release {PUBLISHED}")
    run_git(repo, "tag", PUBLISHED)
    for path in sorted(repo.rglob("*")):  # files only: nothing is removed recursively
        if path.is_file() and ".git" not in path.relative_to(repo).parts and path.name != ".git":
            path.unlink()
    shutil.copy2(TEMPLATE_ROOT / "copier.yml", repo / "copier.yml")
    shutil.copytree(TEMPLATE_ROOT / "template", repo / "template", dirs_exist_ok=True)
    shutil.copytree(TEMPLATE_ROOT / "locks", repo / "locks", dirs_exist_ok=True)
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-q", "-m", "release v0.7.0")
    run_git(repo, "tag", "v0.7.0")
    remote = tmp_path / "published" / "web-core-template.git"
    remote.parent.mkdir()
    run_git(tmp_path, "clone", "-q", "--bare", str(repo), str(remote))
    return repo, remote


@pytest.fixture()
def history(tmp_path: Path):
    if not _available():
        pytest.skip(f"the published tag {PUBLISHED} is not in this checkout (shallow clone)")
    return build_history(tmp_path)


@pytest.mark.parametrize("provider", ["postgres", "supabase"])
def test_native_client_can_be_enabled_on_a_real_v0_6_1_auth_project_without_losing_user_work(history, tmp_path: Path, provider: str) -> None:
    repo, remote = history
    project = tmp_path / "app"
    render(str(remote), project, ref=PUBLISHED, app_name="up-native", zod=True, rest_api=True, database=provider, auth=True)
    assert "native_client" not in _answers(project) and not (project / "openapi").exists()
    _adopt(project)
    (project / "src/app/api/health").mkdir(parents=True)
    (project / "src/app/api/health/route.ts").write_text(OWN_ROUTE)
    run_git(project, "add", "-A")
    run_git(project, "commit", "-q", "-m", "feat: health route")
    lock_before = (project / "package-lock.json").read_text()
    package_before = json.loads((project / "package.json").read_text())

    result = _update(project, "--vcs-ref", "v0.7.0", "--data", "native_client=true")

    assert result.returncode == 0, result.stderr
    answers = _answers(project)
    assert (answers["native_client"], answers["auth"], answers["database"], answers["_commit"]) == (True, True, provider, "v0.7.0")
    for name in NATIVE_FILES:
        assert (project / name).is_file(), name
    assert "bearer({ requireSignature: true })" in (project / "src/server/auth/auth.ts").read_text()
    assert (project / "package-lock.json").read_text() == lock_before  # no dependency is added
    package = json.loads((project / "package.json").read_text())
    assert package["dependencies"] == package_before["dependencies"] and package["scripts"]["openapi:generate"] == "node scripts/openapi.mjs"
    assert (project / "src/app/api/health/route.ts").read_text() == OWN_ROUTE
    assert not (project / ".env.local").exists()
    for name in ("package.json", "AGENTS.md", "README.md", ".gitignore"):
        assert "<<<<<<<" not in (project / name).read_text(), name


@pytest.mark.parametrize(
    ("answers", "message"),
    [({"zod": True, "rest_api": True, "database": "postgres"}, "native_client requires auth")],
    ids=["no auth"],
)
def test_enabling_native_client_without_auth_is_refused_and_changes_nothing(history, tmp_path: Path, answers: dict, message: str) -> None:
    repo, remote = history
    project = tmp_path / "app"
    render(str(remote), project, ref=PUBLISHED, app_name="refused-native", **answers)
    _adopt(project)
    head, recorded = run_git(project, "rev-parse", "HEAD"), _answers(project)

    result = _update(project, "--vcs-ref", "v0.7.0", "--data", "native_client=true")

    assert result.returncode != 0 and message in result.stderr + result.stdout
    assert run_git(project, "status", "--porcelain") == "" and run_git(project, "rev-parse", "HEAD") == head and _answers(project) == recorded
    assert not (project / "openapi").exists()
