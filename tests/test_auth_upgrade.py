"""Authentication and `copier update`: enabling it on a real published release, keeping it, and being refused safely.

Unlike the stand-in releases in the other upgrade tests, the old side here is the real, published `v0.5.1`
(the last release before `auth`), taken from this repository's tags: the working tree is committed on top
of it as the next release. The tests skip when that tag is not available (a shallow clone); the release
gate runs them with the full history.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import TEMPLATE_ROOT, render, run_git
from test_upgrade import USER_PAGE_EDIT, _adopt, _answers, _update  # noqa: F401

PUBLISHED = "v0.5.1"
OWN_ROUTE = 'export function GET() {\n  return Response.json({ status: "ok" });\n}\n'
USER_MIGRATION = "create table app.notes (id int primary key);\n"
AUTH_FILES = (
    "src/server/auth/auth.ts", "src/server/auth/scope.ts", "src/app/api/auth/[...all]/route.ts", "src/app/api/v1/items/route.ts",
    "scripts/db/roles.mjs", "docs/auth.md", ".npmrc", "supabase/migrations/20260301000000_create_auth.sql",
    "supabase/migrations/20260301000100_scope_items_to_owner.sql", "src/lib/auth-client.ts",
)  # fmt: skip


def _published_tag_available() -> bool:
    try:
        run_git(TEMPLATE_ROOT, "rev-parse", "-q", "--verify", f"{PUBLISHED}^{{commit}}")
    except subprocess.CalledProcessError:
        return False
    return True


def _extract(tag: str, destination: Path) -> None:
    archive = subprocess.run(["git", "-C", str(TEMPLATE_ROOT), "archive", tag], capture_output=True, check=True)
    subprocess.run(["tar", "-x", "-C", str(destination)], input=archive.stdout, check=True)


def build_history(tmp_path: Path) -> tuple[Path, Path]:
    """(working repo, bare remote): the real published v0.5.1, then this working tree as v0.6.0."""
    repo = tmp_path / "web-core-template"
    repo.mkdir()
    run_git(repo, "init", "-q", "-b", "main")
    _extract(PUBLISHED, repo)
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-q", "-m", f"release {PUBLISHED}")
    run_git(repo, "tag", PUBLISHED)
    for path in sorted(repo.rglob("*")):  # replace the template's inputs with the working tree's (files only: nothing is removed recursively)
        if path.is_file() and ".git" not in path.relative_to(repo).parts and path.name != ".git":
            path.unlink()
    shutil.copy2(TEMPLATE_ROOT / "copier.yml", repo / "copier.yml")
    shutil.copytree(TEMPLATE_ROOT / "template", repo / "template", dirs_exist_ok=True)
    shutil.copytree(TEMPLATE_ROOT / "locks", repo / "locks", dirs_exist_ok=True)
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-q", "-m", "release v0.6.0")
    run_git(repo, "tag", "v0.6.0")
    remote = tmp_path / "published" / "web-core-template.git"
    remote.parent.mkdir()
    run_git(tmp_path, "clone", "-q", "--bare", str(repo), str(remote))
    return repo, remote


@pytest.fixture()
def history(tmp_path: Path):
    if not _published_tag_available():
        pytest.skip(f"the published tag {PUBLISHED} is not in this checkout (shallow clone)")
    return build_history(tmp_path)


def _release(repo: Path, remote: Path, tag: str, edit) -> None:
    edit(repo / "template")
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-q", "-m", f"release {tag}")
    run_git(repo, "tag", tag)
    run_git(repo, "push", "-q", str(remote), "main", tag)


def _commit_own_work(project: Path, provider: str) -> dict[str, Path]:
    own = {"route": project / "src/app/api/health/route.ts", "migration": project / "supabase/migrations/20260215000000_user_notes.sql"}
    own["route"].parent.mkdir(parents=True)
    own["route"].write_text(OWN_ROUTE)
    own["migration"].write_text(USER_MIGRATION)
    (project / "supabase/migrations.lock/20260215000000_user_notes.sha256").write_text(hashlib.sha256(USER_MIGRATION.encode()).hexdigest() + "\n")
    gitignore = project / ".gitignore"
    gitignore.write_text(gitignore.read_text() + "\n# mine\n.vscode/\n")
    run_git(project, "add", "-A")
    run_git(project, "commit", "-q", "-m", "feat: health route, my own migration, ignore rule")
    return own


def _no_markers(project: Path) -> None:
    for name in ("package.json", "package-lock.json", "README.md", "AGENTS.md", ".gitignore", "src/lib/schemas/index.ts", "src/lib/schemas/v1/index.ts", "scripts/db/cli.mjs", ".env.example"):
        assert "<<<<<<<" not in (project / name).read_text(), name


def _user_work_is_intact(project: Path, own: dict[str, Path]) -> None:
    assert own["route"].read_text() == OWN_ROUTE and own["migration"].read_text() == USER_MIGRATION
    assert USER_PAGE_EDIT in (project / "src/app/page.tsx").read_text() and "Local note kept across upgrades." in (project / "README.md").read_text()
    assert ".vscode/" in (project / ".gitignore").read_text()


def _expected_lock(key: str, name: str) -> str:
    return (TEMPLATE_ROOT / "locks" / f"{key}.json").read_text().replace("{{ app_name | to_json }}", json.dumps(name))


@pytest.mark.parametrize(
    ("provider", "forms", "lock"),
    [("postgres", False, "auth+database-postgres+zod"), ("supabase", True, "auth+database-supabase+forms+zod")],
)
def test_auth_can_be_enabled_on_a_real_v0_5_1_project_without_losing_user_work(history, tmp_path: Path, provider: str, forms: bool, lock: str) -> None:
    repo, remote = history
    project = tmp_path / "app"
    render(str(remote), project, ref=PUBLISHED, app_name="up-app", zod=True, rest_api=True, forms=forms, database=provider)
    assert "auth" not in _answers(project) and not (project / "src/server/auth").exists()
    _adopt(project)
    own = _commit_own_work(project, provider)
    old_migration = (project / "supabase/migrations/20260101000000_create_items.sql").read_bytes()

    result = _update(project, "--vcs-ref", "v0.6.0", "--data", "auth=true")

    assert result.returncode == 0, result.stderr
    answers = _answers(project)
    assert (answers["auth"], answers["database"], answers["forms"], answers["rest_api"], answers["_commit"]) == (True, provider, forms, True, "v0.6.0")
    for name in AUTH_FILES:
        assert (project / name).is_file(), name
    assert not (project / "src/server/api/repository-handler.db.test.ts").exists()  # the unprotected example gave way to the real routes
    package = json.loads((project / "package.json").read_text())
    assert package["dependencies"]["better-auth"] == "1.7.15" and package["scripts"]["db:roles"] == "node scripts/db/cli.mjs roles"
    assert (project / "package-lock.json").read_text() == _expected_lock(lock, "up-app")
    migrations = sorted(p.name for p in (project / "supabase/migrations").iterdir())
    assert migrations == ["20260101000000_create_items.sql", "20260215000000_user_notes.sql", "20260301000000_create_auth.sql", "20260301000100_scope_items_to_owner.sql"]
    assert (project / "supabase/migrations/20260101000000_create_items.sql").read_bytes() == old_migration  # history is never rewritten
    for sql in (project / "supabase/migrations").iterdir():
        assert (project / "supabase/migrations.lock" / sql.name.replace(".sql", ".sha256")).read_text() == hashlib.sha256(sql.read_bytes()).hexdigest() + "\n", sql.name
    assert "owner_id" in (project / "supabase/migrations/20260301000100_scope_items_to_owner.sql").read_text()
    assert "withPrincipal" in (project / "src/server/items/repository.ts").read_text() or "OwnerScope" in (project / "src/server/items/repository.ts").read_text()
    assert not (project / ".env.local").exists()  # an update never writes secrets: `npm run db:init` does, on the developer's machine
    _no_markers(project)
    _user_work_is_intact(project, own)


@pytest.mark.parametrize(
    ("answers", "message"),
    [
        ({"zod": True, "rest_api": True, "database": "none"}, "auth requires a database"),
        ({"zod": True, "database": "postgres"}, "auth requires rest_api"),
        ({"zod": True, "rest_api": True, "forms": True}, "auth requires a database"),
    ],
    ids=["no database", "no rest_api", "forms but no database"],
)
def test_enabling_auth_without_its_prerequisites_is_refused_and_changes_nothing(history, tmp_path: Path, answers: dict, message: str) -> None:
    repo, remote = history
    project = tmp_path / "app"
    render(str(remote), project, ref=PUBLISHED, app_name="refused-app", **answers)
    _adopt(project)
    head, recorded = run_git(project, "rev-parse", "HEAD"), _answers(project)

    result = _update(project, "--vcs-ref", "v0.6.0", "--data", "auth=true")

    assert result.returncode != 0 and message in result.stderr + result.stdout
    assert run_git(project, "status", "--porcelain") == "" and run_git(project, "rev-parse", "HEAD") == head and _answers(project) == recorded
    assert not (project / "src/server/auth").exists() and not (project / ".npmrc").exists()


def test_an_invalid_answer_through_an_update_fails_safely(history, tmp_path: Path) -> None:
    repo, remote = history
    project = tmp_path / "app"
    render(str(remote), project, ref=PUBLISHED, app_name="bad-app", zod=True, rest_api=True, database="postgres")
    _adopt(project)
    head, recorded = run_git(project, "rev-parse", "HEAD"), _answers(project)
    for bad in ("banana", "1", "maybe", "tRuE"):
        result = _update(project, "--vcs-ref", "v0.6.0", "--data", f"auth={bad}")
        assert result.returncode != 0, bad
        assert run_git(project, "status", "--porcelain") == "" and run_git(project, "rev-parse", "HEAD") == head and _answers(project) == recorded, bad


def test_a_project_without_auth_stays_without_it_through_the_upgrade(history, tmp_path: Path) -> None:
    repo, remote = history
    project = tmp_path / "app"
    render(str(remote), project, ref=PUBLISHED, app_name="plain-app", zod=True, rest_api=True, database="supabase")
    _adopt(project)
    own = _commit_own_work(project, "supabase")

    result = _update(project, "--vcs-ref", "v0.6.0")

    assert result.returncode == 0, result.stderr
    answers = _answers(project)
    assert (answers["auth"], answers["database"], answers["_commit"]) == (False, "supabase", "v0.6.0")
    for name in AUTH_FILES:
        assert not (project / name).exists(), name
    assert not (project / "src/server/auth").exists() and "better-auth" not in (project / "package.json").read_text()
    _no_markers(project)
    _user_work_is_intact(project, own)


def test_an_auth_project_keeps_its_selection_and_both_sides_edits_through_a_later_release(history, tmp_path: Path) -> None:
    repo, remote = history
    project = tmp_path / "app"
    render(str(remote), project, ref="v0.6.0", app_name="keep-app", zod=True, rest_api=True, database="postgres", auth=True)
    _adopt(project)
    own = _commit_own_work(project, "postgres")
    guard = project / "src/server/auth/guard.ts"
    guard.write_text(guard.read_text() + "\n// kept: my own note\n")
    run_git(project, "add", "-A")
    run_git(project, "commit", "-q", "-m", "chore: note")

    def edit(template: Path) -> None:
        schema = template / "src/lib/{% if zod %}schemas{% endif %}/{% if rest_api %}v1{% endif %}/{% if auth %}items.ts{% endif %}"
        schema.write_text(schema.read_text().replace("max(100)", "max(150)"))
        (template / "CONTRIBUTING.md.jinja").write_text("# Contributing to {{ app_name }}\n")

    _release(repo, remote, "v0.6.1", edit)
    result = _update(project, "--vcs-ref", "v0.6.1")

    assert result.returncode == 0, result.stderr
    answers = _answers(project)
    assert (answers["auth"], answers["database"], answers["_commit"]) == (True, "postgres", "v0.6.1")
    assert "max(150)" in (project / "src/lib/schemas/v1/items.ts").read_text()  # the template's change arrived
    assert "// kept: my own note" in guard.read_text()  # the user's edit survived
    assert (project / "CONTRIBUTING.md").is_file()
    assert sorted(p.name for p in (project / "supabase/migrations").iterdir()) == ["20260101000000_create_items.sql", "20260215000000_user_notes.sql", "20260301000000_create_auth.sql", "20260301000100_scope_items_to_owner.sql"]
    _no_markers(project)
    _user_work_is_intact(project, own)


def test_disabling_auth_with_raw_copier_is_not_a_supported_upgrade(history, tmp_path: Path) -> None:
    """The template cannot refuse it (a Copier validator cannot see the previous answer); `devkit upgrade` does.

    This pins what raw `copier update` does so the documentation's warning stays true: it removes the
    generated authentication files, and leaves the user's own work and the data in the database alone.
    """
    repo, remote = history
    project = tmp_path / "app"
    render(str(remote), project, ref="v0.6.0", app_name="off-app", zod=True, rest_api=True, database="postgres", auth=True)
    _adopt(project)
    own = _commit_own_work(project, "postgres")

    result = _update(project, "--vcs-ref", "v0.6.0", "--data", "auth=false")

    assert result.returncode == 0, result.stderr
    assert _answers(project)["auth"] is False
    assert not (project / "src/server/auth").exists() and "better-auth" not in (project / "package.json").read_text()
    _user_work_is_intact(project, own)  # their own route, migration and edits are untouched
    doc = (TEMPLATE_ROOT / "template/{% if has_server %}docs{% endif %}/{% if auth %}auth.md{% endif %}.jinja").read_text()
    assert "Disabling authentication is not automated" in doc and "devkit upgrade" in doc
