"""The optional database feature: what a project gets with it, and what it must not get without it."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

import features as f
from conftest import CONFIGURATIONS, REGISTRY, TEMPLATE_ROOT, render
from test_answer_types import _copier

DIGEST = "postgres:17.10@sha256:7958605b474b3d264a969cb3a123d6aa00ad1e1fe9da8a69984dabb704d93317"
VITEST_V0_4_0 = 'import { defineConfig } from "vitest/config";\n\nexport default defineConfig({\n  // Resolve the "@/*" import alias from tsconfig.json.\n  resolve: { tsconfigPaths: true },\n});\n'  # byte for byte as in the published v0.4.0
COMMON_FILES = {
    ".env.example", "docs/database.md", "scripts/db/cli.mjs", "scripts/db/migrations.db.test.ts", "scripts/db/migrations.mjs",
    "scripts/db/migrations.test.ts", "src/lib/schemas/item.test.ts", "src/lib/schemas/item.ts", "src/server/db/config.test.ts",
    "src/server/db/config.ts", "src/server/db/connection.db.test.ts", "src/server/db/errors.test.ts", "src/server/db/errors.ts",
    "src/server/db/index.ts", "src/server/db/pool.ts", "src/server/db/server-only.test.ts", "src/server/db/test-support/database.ts",
    "src/server/db/test-support/server-only.ts", "src/server/db/transaction.ts", "src/server/items/repository.db.test.ts",
    "src/server/items/repository.ts", "supabase/migrations.lock/20260101000000_create_items.sha256",
    "supabase/migrations/20260101000000_create_items.sql", "vitest.db.config.mts",
}  # fmt: skip
PROVIDER_FILES = {"postgres": {"compose.yaml"}, "supabase": {"supabase/config.toml"}}
SHARED_THAT_CHANGE = {".copier-answers.yml", ".github/workflows/ci.yml", ".gitignore", "AGENTS.md", "README.md", "package-lock.json", "package.json", "src/lib/schemas/index.ts", "vitest.config.mts"}
SCRIPTS = {
    "db:init": "node scripts/db/cli.mjs init", "db:up": "node scripts/db/cli.mjs up", "db:down": "node scripts/db/cli.mjs down",
    "db:status": "node scripts/db/cli.mjs status", "db:logs": "node scripts/db/cli.mjs logs", "db:migrate": "node scripts/db/cli.mjs migrate",
    "db:verify": "node scripts/db/cli.mjs verify", "db:lock": "node scripts/db/cli.mjs lock", "test:db": "vitest run --config vitest.db.config.mts",
}  # fmt: skip
CONFIG_VARIABLES = {"DATABASE_URL", "DATABASE_POOL_MAX", "DATABASE_CONNECT_TIMEOUT_MS", "DATABASE_STATEMENT_TIMEOUT_MS"}


@pytest.fixture(scope="module")
def apps(template_repo: Path, tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """The same application name everywhere, so only the selected features differ."""
    root = tmp_path_factory.mktemp("database")
    chosen = {
        "default": {}, "zod": {"zod": True}, "forms": {"zod": True, "forms": True}, "api": {"zod": True, "rest_api": True},
        "pg": {"zod": True, "database": "postgres"}, "sb": {"zod": True, "database": "supabase"},
        "pg+all": {"zod": True, "forms": True, "rest_api": True, "database": "postgres"},
        "sb+all": {"zod": True, "forms": True, "rest_api": True, "database": "supabase"},
    }  # fmt: skip
    for label, answers in chosen.items():
        render(template_repo, root / label, app_name="db-app", **answers)
    return {label: root / label for label in chosen}


def _files(project: Path) -> dict[str, bytes]:
    return {str(p.relative_to(project)): p.read_bytes() for p in sorted(project.rglob("*")) if p.is_file()}


def _package(project: Path) -> dict:
    return json.loads((project / "package.json").read_text())


# ------------------------------------------------------------------ disabled: the baseline


@pytest.mark.parametrize("label", ["default", "zod", "forms", "api"])
def test_without_a_database_nothing_database_related_is_generated(apps, label: str) -> None:
    project = apps[label]
    package = _package(project)
    assert not {"pg", "server-only", "@types/pg", "supabase"} & {*package["dependencies"], *package["devDependencies"]}
    assert not [name for name in package["scripts"] if name.startswith("db:") or name == "test:db"]
    for path in (".env.example", "compose.yaml", "scripts", "supabase", "docs/database.md", "vitest.db.config.mts", "src/server/db", "src/server/items", "src/lib/schemas/item.ts"):
        assert not (project / path).exists(), path
    assert (project / "vitest.config.mts").read_text() == VITEST_V0_4_0
    assert "!.env.example" not in (project / ".gitignore").read_text()
    assert list(yaml.safe_load((project / ".github/workflows/ci.yml").read_text())["jobs"]) == ["check"]
    for relative in ("README.md", "AGENTS.md"):
        text = (project / relative).read_text()
        assert "## Database" not in text and "db:init" not in text and "src/server/db" not in text and "docs/database.md" not in text, relative
    assert yaml.safe_load((project / ".copier-answers.yml").read_text())["database"] == "none"
    assert [p for p in project.rglob("*") if p.is_dir() and not any(p.iterdir())] == []


def test_database_none_is_exactly_the_default(template_repo: Path, tmp_path: Path, apps) -> None:
    render(template_repo, tmp_path / "explicit", app_name="db-app", database="none")
    assert _files(tmp_path / "explicit") == _files(apps["default"])


def test_no_server_directory_exists_unless_the_api_or_the_database_needs_it(apps) -> None:
    assert not (apps["zod"] / "src/server").exists() and not (apps["forms"] / "src/server").exists()
    assert (apps["api"] / "src/server/api").is_dir() and not (apps["api"] / "src/server/db").exists()
    assert (apps["pg"] / "src/server/db").is_dir() and not (apps["pg"] / "src/server/api").exists()


# ------------------------------------------------------------------ the rule: database requires zod


@pytest.mark.parametrize("answers", [{"database": "postgres"}, {"database": "supabase"}, {"database": "postgres", "zod": False}, {"database": "supabase", "forms": False, "rest_api": False}], ids=str)
def test_a_database_without_zod_is_rejected_before_anything_is_written(template_repo: Path, tmp_path: Path, answers: dict) -> None:
    with pytest.raises(Exception, match="database requires zod"):
        render(template_repo, tmp_path / "no", **answers)
    assert not (tmp_path / "no").exists()


@pytest.mark.parametrize("given", ["oracle", "Postgres", "POSTGRES", "supabase ", "", " ", "true", "false", "1", "0", "none ", "mysql", "sqlite"])
def test_only_the_declared_choices_are_accepted(template_repo: Path, tmp_path: Path, given: str) -> None:
    with pytest.raises(Exception, match="database"):
        render(template_repo, tmp_path / "no", zod=True, database=given)
    assert not (tmp_path / "no").exists()


@pytest.mark.parametrize("given", [True, False, 1, 0, None, ["postgres"]], ids=repr)
def test_non_text_values_are_rejected(template_repo: Path, tmp_path: Path, given: object) -> None:
    with pytest.raises(Exception):
        render(template_repo, tmp_path / "no", zod=True, database=given)
    assert not (tmp_path / "no").exists()


@pytest.mark.parametrize("data", [("database=postgres",), ("database=supabase", "zod=false")])
def test_the_real_copier_command_line_rejects_a_database_without_zod(template_repo: Path, tmp_path: Path, data: tuple[str, ...]) -> None:
    result = _copier(template_repo, tmp_path / "cli", *data)
    assert result.returncode != 0 and "database requires zod" in result.stderr and not (tmp_path / "cli").exists()


@pytest.mark.parametrize("provider", ["postgres", "supabase"])
def test_the_real_copier_command_line_accepts_each_provider_and_records_it(template_repo: Path, tmp_path: Path, provider: str) -> None:
    result = _copier(template_repo, tmp_path / "cli", "zod=true", f"database={provider}")
    assert result.returncode == 0, result.stderr
    assert yaml.safe_load((tmp_path / "cli/.copier-answers.yml").read_text())["database"] == provider


def test_the_database_never_turns_zod_or_anything_else_on(template_repo: Path, tmp_path: Path) -> None:
    project = tmp_path / "ok"
    render(template_repo, project, zod=True, database="postgres")
    answers = yaml.safe_load((project / ".copier-answers.yml").read_text())
    assert (answers["zod"], answers["forms"], answers["rest_api"], answers["database"]) == (True, False, False, "postgres")
    assert "has_database" not in answers and "has_server" not in answers and "lock_key" not in answers


# ------------------------------------------------------------------ enabled: files, dependencies, scripts


@pytest.mark.parametrize(("label", "base", "provider"), [("pg", "zod", "postgres"), ("sb", "zod", "supabase")])
def test_each_provider_adds_exactly_the_expected_files_and_changes_only_shared_files(apps, label: str, base: str, provider: str) -> None:
    enabled, baseline = _files(apps[label]), _files(apps[base])
    assert set(enabled) - set(baseline) == COMMON_FILES | PROVIDER_FILES[provider]
    assert set(baseline) - set(enabled) == set()
    assert {n for n in baseline if enabled[n] != baseline[n]} == SHARED_THAT_CHANGE


def test_with_the_api_the_database_adds_one_composition_test_and_leaves_the_api_untouched(apps) -> None:
    pg_all, pg = _files(apps["pg+all"]), _files(apps["pg"])
    extra = set(pg_all) - set(pg)
    assert "src/server/api/repository-handler.db.test.ts" in extra and "src/server/api/responses.ts" in extra
    api_files = {n: b for n, b in _files(apps["api"]).items() if n.startswith("src/server/api/") or n.startswith("src/app/api/")}
    assert {n: pg_all[n] for n in api_files} == api_files  # every REST API file is byte-identical with the database on


def test_dependencies_are_exact_and_only_what_the_provider_needs(apps) -> None:
    zod, pg, sb = _package(apps["zod"]), _package(apps["pg"]), _package(apps["sb"])
    assert {k: v for k, v in pg["dependencies"].items() if k not in zod["dependencies"]} == {"pg": "8.23.1", "server-only": "0.0.1"}
    assert {k: v for k, v in pg["devDependencies"].items() if k not in zod["devDependencies"]} == {"@types/pg": "8.23.1"}
    assert {k: v for k, v in sb["devDependencies"].items() if k not in pg["devDependencies"]} == {"supabase": "2.122.1"}  # only Supabase adds its CLI
    assert sb["dependencies"] == pg["dependencies"]
    assert {k: v for k, v in pg["scripts"].items() if k not in zod["scripts"]} == SCRIPTS == {k: v for k, v in sb["scripts"].items() if k not in zod["scripts"]}
    for package in (pg, sb):
        assert all(re.fullmatch(r"\d+\.\d+\.\d+", version) for version in (*package["dependencies"].values(), *package["devDependencies"].values()))
        assert package["engines"] == zod["engines"] and {k: v for k, v in package["scripts"].items() if k in zod["scripts"]} == zod["scripts"]


def test_the_rest_api_and_forms_add_no_dependency_to_a_database_project(apps) -> None:
    pg, full = _package(apps["pg"]), _package(apps["pg+all"])
    assert {k for k in full["dependencies"]} - {k for k in pg["dependencies"]} == {"@hookform/resolvers", "react-hook-form"}
    assert {k for k in full["devDependencies"]} - {k for k in pg["devDependencies"]} == {"@testing-library/dom", "@testing-library/react", "jsdom"}


def test_the_lockfile_matches_the_manifest_and_adds_no_install_script(apps) -> None:
    for label, expected in (("pg", {"pg", "server-only"}), ("sb", {"pg", "server-only"}), ("pg+all", {"pg", "server-only"})):
        package, lock = _package(apps[label]), json.loads((apps[label] / "package-lock.json").read_text())["packages"]
        assert lock[""]["dependencies"] == package["dependencies"] and lock[""]["devDependencies"] == package["devDependencies"]
        for name in expected:
            assert lock[f"node_modules/{name}"]["version"] == package["dependencies"][name]
            assert "hasInstallScript" not in lock[f"node_modules/{name}"] and "dev" not in lock[f"node_modules/{name}"]
        assert lock["node_modules/@types/pg"]["dev"] is True


def test_the_committed_migration_is_the_one_canonical_schema_with_its_lock(apps) -> None:
    import hashlib

    for label in ("pg", "sb", "pg+all"):
        sql = (apps[label] / "supabase/migrations/20260101000000_create_items.sql").read_text()
        lock = (apps[label] / "supabase/migrations.lock/20260101000000_create_items.sha256").read_text()
        assert lock == hashlib.sha256(sql.encode()).hexdigest() + "\n"
        assert "create table app.items" in sql and "enable row level security" in sql and "revoke all on schema app from public" in sql
        assert "anon" in sql and "service_role" in sql  # the API roles are explicitly stripped where they exist
    assert {n: b for n, b in _files(apps["pg"]).items() if n.startswith("supabase/migrations")} == {n: b for n, b in _files(apps["sb"]).items() if n.startswith("supabase/migrations")}


# ------------------------------------------------------------------ configuration files and documentation


def test_the_ignore_file_allows_only_the_env_example_to_be_committed(apps) -> None:
    lines = (apps["pg"] / ".gitignore").read_text().splitlines()
    assert lines.index(".env*") + 1 == lines.index("!.env.example")
    for label in ("pg", "sb"):
        example = (apps[label] / ".env.example").read_text()
        keys = {line.split("=", 1)[0] for line in example.splitlines() if "=" in line and not line.startswith("#")}
        assert "DATABASE_URL" in keys and not (keys & {"NEXT_PUBLIC_DATABASE_URL"})
    assert "generated-by-npm-run-db-init" in (apps["pg"] / ".env.example").read_text()


def test_the_image_digest_is_the_same_everywhere_it_appears(apps) -> None:
    compose = yaml.safe_load((apps["pg"] / "compose.yaml").read_text())["services"]["db"]["image"]
    workflow = yaml.safe_load((apps["pg"] / ".github/workflows/ci.yml").read_text())["jobs"]["database"]["services"]["postgres"]["image"]
    assert compose == workflow == DIGEST


@pytest.mark.parametrize("label", ["pg", "sb"])
def test_the_generated_workflow_adds_one_database_job_with_pinned_actions_and_no_secrets(apps, label: str) -> None:
    text = (apps[label] / ".github/workflows/ci.yml").read_text()
    workflow = yaml.safe_load(text)
    assert list(workflow["jobs"]) == ["check", "database"] and workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["database"]
    assert job["env"] == {"DATABASE_URL": "postgresql://postgres@127.0.0.1:5432/postgres"} and job["timeout-minutes"] <= 30
    for step in job["steps"]:
        if "uses" in step:
            assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", step["uses"]), step["uses"]
    assert [step.get("run") for step in job["steps"] if "run" in step] == ["npm ci", "npm run db:verify", "npm run test:db"]
    assert "secrets." not in text and "password" not in text.lower()


@pytest.mark.parametrize("label", ["pg", "sb"])
def test_the_documentation_matches_the_project(apps, label: str) -> None:
    project, doc = apps[label], (apps[label] / "docs/database.md").read_text()
    scripts = _package(project)["scripts"]
    for command in set(re.findall(r"npm run (db:[a-z]+|test:db)", doc + (project / "README.md").read_text())):
        assert command in scripts, command
    for heading in ("How the pieces fit", "Prerequisites", "First run", "Everyday commands", "Configuration", "Migrations", "Data access", "Tests",
                    "Security boundaries and limitations", "Changing provider", "Troubleshooting and recovery"):  # fmt: skip
        assert f"## {heading}" in doc, heading
    for variable in CONFIG_VARIABLES:
        assert variable in doc and variable in (project / "src/server/db/config.ts").read_text()
    assert "```mermaid" in doc and "**Not production-ready on its own.**" in doc
    assert "authentication" in doc and "NEXT_PUBLIC_" in doc and "supabase/migrations/" in doc
    assert "## Database" in (project / "README.md").read_text() and "docs/database.md" in (project / "README.md").read_text()
    assert "import \"server-only\"" in (project / "AGENTS.md").read_text() and "db:lock" in (project / "AGENTS.md").read_text()
    assert ("docker compose --env-file .env.local down --volumes" in doc) == (label == "pg")  # the manual, explicit way to discard data
    if label == "sb":
        assert "SUPABASE_ALLOW_LAN_EXPOSURE" in doc and "every network interface" in doc and "api.schemas" in doc
    assert not re.search(r"\{\{|\{%", doc)


def test_the_provider_switch_boundary_is_documented_in_the_generated_project(apps) -> None:
    for label in ("pg", "sb"):
        doc = (apps[label] / "docs/database.md").read_text()
        assert "is **not** a template upgrade" in doc and "not automated" in doc and "devkit upgrade" in doc


# ------------------------------------------------------------------ composition and the whole matrix


def test_every_supported_configuration_renders_with_exactly_its_selection_and_its_lockfile(template_repo: Path, tmp_path: Path) -> None:
    assert len(CONFIGURATIONS) == 17 and len(f.lock_keys(REGISTRY)) == 11
    for index, selection in enumerate(CONFIGURATIONS):
        project = tmp_path / f"c{index}"
        render(template_repo, project, **f.answers(selection, REGISTRY))
        answers = yaml.safe_load((project / ".copier-answers.yml").read_text())
        recorded = {n if not REGISTRY[n].choices else f"{n}={answers[n]}" for n in REGISTRY if answers[n] not in (False, "none")}
        assert recorded == set(selection)
        assert (project / "src/server/db").is_dir() == any(token.startswith("database=") for token in selection)
        assert (project / "src/server").is_dir() == bool({"rest_api"} & set(selection) or any(token.startswith("database=") for token in selection))
        expected_lock = (TEMPLATE_ROOT / "locks" / f"{f.lock_key(selection, REGISTRY)}.json").read_text().replace("{{ app_name | to_json }}", '"sample-app"')
        assert (project / "package-lock.json").read_text() == expected_lock


def test_the_lock_matrix_stays_bounded(template_repo: Path) -> None:
    assert sorted(f.lock_keys(REGISTRY)) == [
        "auth+database-postgres+forms+zod", "auth+database-postgres+zod", "auth+database-supabase+forms+zod", "auth+database-supabase+zod",
        "base", "database-postgres+forms+zod", "database-postgres+zod", "database-supabase+forms+zod", "database-supabase+zod", "forms+zod", "zod",
    ]  # fmt: skip
    assert len(f.lock_keys(REGISTRY)) <= 16
