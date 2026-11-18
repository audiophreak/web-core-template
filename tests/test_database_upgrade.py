"""The database feature and `copier update`: enabling it later, keeping it, and being refused safely.

Same published-release stand-in as tests/test_upgrade.py. Projects are made to look as if generated
before the `database` question existed, and carry committed user work: an edited page, their own API
route and schema, a line they added to .gitignore, and (where relevant) their own migration.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from conftest import GIT_TEST_ENV, TEMPLATE_ROOT, render, run_git
from test_upgrade import USER_PAGE_EDIT, _adopt, _answers, _release, _update, published  # noqa: F401

OWN_ROUTE = 'export function GET() {\n  return Response.json({ status: "ok" });\n}\n'
OWN_SCHEMA = 'import { z } from "zod";\n\nexport const contactSchema = z.object({ phone: z.string() });\n'
USER_MIGRATION = "create table app.notes (id int primary key);\n"


def _as_generated_before_the_database(project: Path) -> None:
    path = project / ".copier-answers.yml"
    path.write_text("".join(line for line in path.read_text().splitlines(keepends=True) if not line.startswith("database:")))
    assert "database" not in _answers(project)


def _commit_own_work(project: Path) -> dict[str, Path]:
    own = {"route": project / "src/app/api/health/route.ts", "schema": project / "src/lib/schemas/contact.ts"}
    own["route"].parent.mkdir(parents=True)
    own["route"].write_text(OWN_ROUTE)
    own["schema"].write_text(OWN_SCHEMA)
    gitignore = project / ".gitignore"
    gitignore.write_text(gitignore.read_text() + "\n# mine\n.vscode/\n")
    run_git(project, "add", "-A")
    run_git(project, "commit", "-q", "-m", "feat: health route, contact schema, ignore rule")
    return own


def _release_020(repo: Path, remote: Path, edit=None) -> None:
    def change(template: Path) -> None:
        (template / "CONTRIBUTING.md.jinja").write_text("# Contributing to {{ app_name }}\n")
        if edit:
            edit(template)

    _release(repo, remote, "v0.2.0", change)


def _no_markers(project: Path) -> None:
    for name in ("package.json", "package-lock.json", "README.md", "AGENTS.md", ".gitignore", "vitest.config.mts", ".github/workflows/ci.yml", "src/lib/schemas/index.ts"):
        assert "<<<<<<<" not in (project / name).read_text(), name


def _user_work_is_intact(project: Path, own: dict[str, Path]) -> None:
    assert own["route"].read_text() == OWN_ROUTE and own["schema"].read_text() == OWN_SCHEMA
    assert USER_PAGE_EDIT in (project / "src/app/page.tsx").read_text()
    assert "Local note kept across upgrades." in (project / "README.md").read_text()
    assert ".vscode/" in (project / ".gitignore").read_text()  # the user's own line survived the merge


def _expected_lock(key: str, name: str) -> str:
    return (TEMPLATE_ROOT / "locks" / f"{key}.json").read_text().replace("{{ app_name | to_json }}", json.dumps(name))


def test_postgres_can_be_enabled_on_an_existing_zod_project_without_losing_user_work(published, tmp_path: Path) -> None:  # noqa: F811
    repo, remote = published
    project = tmp_path / "app"
    render(str(remote), project, ref="v0.1.0", app_name="upgrade-app", zod=True)
    _as_generated_before_the_database(project)
    _adopt(project)
    own = _commit_own_work(project)
    _release_020(repo, remote)

    result = _update(project, "--vcs-ref", "v0.2.0", "--data", "database=postgres")

    assert result.returncode == 0, result.stderr
    answers = _answers(project)
    assert (answers["zod"], answers["database"], answers["forms"], answers["rest_api"], answers["_commit"]) == (True, "postgres", False, False, "v0.2.0")
    for name in ("compose.yaml", ".env.example", "scripts/db/cli.mjs", "supabase/migrations/20260101000000_create_items.sql", "src/server/items/repository.ts", "docs/database.md", "vitest.db.config.mts"):
        assert (project / name).is_file(), name
    assert not (project / "supabase/config.toml").exists()
    package = json.loads((project / "package.json").read_text())
    assert (package["dependencies"]["pg"], package["dependencies"]["server-only"], package["devDependencies"]["@types/pg"]) == ("8.23.1", "0.0.1", "8.23.1")
    assert "supabase" not in package["devDependencies"]
    assert (project / "package-lock.json").read_text() == _expected_lock("database-postgres+zod", "upgrade-app")
    assert "!.env.example" in (project / ".gitignore").read_text()
    _no_markers(project)
    _user_work_is_intact(project, own)


def test_supabase_can_be_enabled_on_an_existing_zod_and_forms_project(published, tmp_path: Path) -> None:  # noqa: F811
    repo, remote = published
    project = tmp_path / "app"
    render(str(remote), project, ref="v0.1.0", app_name="sb-app", zod=True, forms=True, cache_components=False)
    _as_generated_before_the_database(project)
    _adopt(project)
    own = _commit_own_work(project)
    _release_020(repo, remote)

    result = _update(project, "--vcs-ref", "v0.2.0", "--data", "database=supabase")

    assert result.returncode == 0, result.stderr
    answers = _answers(project)
    assert (answers["database"], answers["forms"], answers["cache_components"]) == ("supabase", True, False)
    assert (project / "supabase/config.toml").is_file() and not (project / "compose.yaml").exists()
    assert 'project_id = "sb-app"' in (project / "supabase/config.toml").read_text()
    assert json.loads((project / "package.json").read_text())["devDependencies"]["supabase"] == "2.122.1"
    assert (project / "package-lock.json").read_text() == _expected_lock("database-supabase+forms+zod", "sb-app")
    _no_markers(project)
    _user_work_is_intact(project, own)


def test_enabling_the_database_leaves_existing_forms_and_api_selections_and_files_exactly_as_they_were(published, tmp_path: Path) -> None:  # noqa: F811
    repo, remote = published
    project = tmp_path / "app"
    render(str(remote), project, ref="v0.1.0", app_name="all-app", zod=True, forms=True, rest_api=True)
    _as_generated_before_the_database(project)
    _adopt(project)
    own = _commit_own_work(project)
    watched = [p for p in project.rglob("*") if p.is_file() and any(part in p.parts for part in ("forms", "api", "forms-example")) and "app" not in p.parts[-1:]]
    before = {str(p.relative_to(project)): p.read_bytes() for p in watched if "health" not in str(p)}
    assert before
    _release_020(repo, remote)

    assert _update(project, "--vcs-ref", "v0.2.0", "--data", "database=postgres").returncode == 0

    answers = _answers(project)
    assert (answers["zod"], answers["forms"], answers["rest_api"], answers["database"]) == (True, True, True, "postgres")
    for name, content in before.items():
        assert (project / name).read_bytes() == content, name
    assert (project / "src/server/api/responses.ts").is_file() and (project / "src/server/db/pool.ts").is_file()  # both live in src/server now
    assert (project / "src/server/api/repository-handler.db.test.ts").is_file()
    _no_markers(project)
    _user_work_is_intact(project, own)


def test_a_database_project_keeps_its_selection_through_an_upgrade_and_keeps_template_and_user_migrations_together(published, tmp_path: Path) -> None:  # noqa: F811
    repo, remote = published
    project = tmp_path / "app"
    render(str(remote), project, ref="v0.1.0", app_name="keep-app", zod=True, database="postgres")
    _adopt(project)
    own = _commit_own_work(project)
    migration = project / "supabase/migrations/20260301000000_user_notes.sql"
    migration.write_text(USER_MIGRATION)
    (project / "supabase/migrations.lock/20260301000000_user_notes.sha256").write_text(hashlib.sha256(USER_MIGRATION.encode()).hexdigest() + "\n")
    repository = project / "src/server/items/repository.ts"
    repository.write_text(repository.read_text() + "\n// kept: my own note\n")
    run_git(project, "add", "-A")
    run_git(project, "commit", "-q", "-m", "feat: my own migration and a note")
    template_migration = "alter table app.items add column archived boolean not null default false;\n"

    def edit(template: Path) -> None:
        (template / "{% if has_database %}supabase{% endif %}/migrations/20260401000000_template_added.sql").write_text(template_migration)
        (template / "{% if has_database %}supabase{% endif %}/migrations.lock/20260401000000_template_added.sha256").write_text(hashlib.sha256(template_migration.encode()).hexdigest() + "\n")
        schema = template / "src/lib/{% if zod %}schemas{% endif %}/{% if has_database %}item.ts{% endif %}"
        schema.write_text(schema.read_text().replace("max(2000", "max(3000"))

    _release_020(repo, remote, edit)
    result = _update(project, "--vcs-ref", "v0.2.0")

    assert result.returncode == 0, result.stderr
    answers = _answers(project)
    assert (answers["zod"], answers["database"], answers["_commit"]) == (True, "postgres", "v0.2.0")
    # the template's new migration arrives next to the user's own, and each has its lock file: no shared file to conflict
    migrations = sorted(p.name for p in (project / "supabase/migrations").iterdir())
    assert migrations == ["20260101000000_create_items.sql", "20260301000000_user_notes.sql", "20260401000000_template_added.sql"]
    for sql in (project / "supabase/migrations").iterdir():
        lock = project / "supabase/migrations.lock" / sql.name.replace(".sql", ".sha256")
        assert lock.read_text() == hashlib.sha256(sql.read_text().encode()).hexdigest() + "\n", sql.name
    assert migration.read_text() == USER_MIGRATION  # the user's migration is untouched
    assert "max(3000" in (project / "src/lib/schemas/item.ts").read_text()  # the template's change
    assert "// kept: my own note" in repository.read_text()  # the user's edit
    _no_markers(project)
    _user_work_is_intact(project, own)


def test_a_project_without_a_database_stays_without_one(published, tmp_path: Path) -> None:  # noqa: F811
    repo, remote = published
    project = tmp_path / "app"
    render(str(remote), project, ref="v0.1.0", app_name="plain-app", zod=True, forms=True)
    _as_generated_before_the_database(project)
    _adopt(project)
    _release_020(repo, remote)

    assert _update(project, "--vcs-ref", "v0.2.0").returncode == 0
    answers = _answers(project)
    assert (answers["zod"], answers["forms"], answers["database"]) == (True, True, "none")
    for name in ("compose.yaml", "supabase", "scripts", ".env.example", "src/server", "docs"):
        assert not (project / name).exists(), name


def test_enabling_a_database_on_a_project_without_zod_is_refused_and_changes_nothing(published, tmp_path: Path) -> None:  # noqa: F811
    repo, remote = published
    project = tmp_path / "app"
    render(str(remote), project, ref="v0.1.0", app_name="nozod-app")
    _as_generated_before_the_database(project)
    _adopt(project)
    _release_020(repo, remote)
    head, answers = run_git(project, "rev-parse", "HEAD"), _answers(project)

    for provider in ("postgres", "supabase"):
        result = _update(project, "--vcs-ref", "v0.2.0", "--data", f"database={provider}")
        assert result.returncode != 0 and "database requires zod" in result.stderr + result.stdout
        assert run_git(project, "status", "--porcelain") == "" and run_git(project, "rev-parse", "HEAD") == head
        assert _answers(project) == answers and not (project / "supabase").exists()


def test_an_invalid_choice_through_an_update_fails_safely(published, tmp_path: Path) -> None:  # noqa: F811
    repo, remote = published
    project = tmp_path / "app"
    render(str(remote), project, ref="v0.1.0", app_name="bad-app", zod=True)
    _as_generated_before_the_database(project)
    _adopt(project)
    _release_020(repo, remote)
    head, answers = run_git(project, "rev-parse", "HEAD"), _answers(project)

    for bad in ("oracle", "Postgres", "true", "1", "SUPABASE"):
        result = _update(project, "--vcs-ref", "v0.2.0", "--data", f"database={bad}")
        assert result.returncode != 0, bad
        assert run_git(project, "status", "--porcelain") == "" and run_git(project, "rev-parse", "HEAD") == head and _answers(project) == answers, bad


def test_changing_the_provider_with_raw_copier_is_not_an_upgrade(published, tmp_path: Path) -> None:  # noqa: F811
    """What raw `copier update` does when the provider changes, pinned here so the hazard stays documented.

    A Copier validator cannot see the previous answer, so the template cannot refuse this. The tooling
    is silently swapped (compose.yaml disappears, the Supabase configuration appears) while the data of
    the old database stays where it was, no longer managed by the project. `devkit upgrade` refuses the
    change, and docs/database.md says why: moving data between providers is not automated.
    """
    repo, remote = published
    project = tmp_path / "app"
    render(str(remote), project, ref="v0.1.0", app_name="switch-app", zod=True, database="postgres")
    _adopt(project)
    own = _commit_own_work(project)
    _release_020(repo, remote)

    result = subprocess.run(
        [sys.executable, "-m", "copier", "update", "--defaults", "--skip-answered", "--quiet", "--vcs-ref", "v0.2.0", "--data", "database=supabase"],
        cwd=project, capture_output=True, text=True, env={**os.environ, **GIT_TEST_ENV}, check=False,
    )  # fmt: skip

    assert result.returncode == 0, result.stderr
    assert _answers(project)["database"] == "supabase"
    assert not (project / "compose.yaml").exists() and (project / "supabase/config.toml").is_file()  # the tooling changed under the project
    assert (project / "supabase/migrations/20260101000000_create_items.sql").is_file()  # the canonical SQL is shared, so it stays
    _user_work_is_intact(project, own)
    doc = (project / "docs/database.md").read_text()
    assert "is **not** a template upgrade" in doc
