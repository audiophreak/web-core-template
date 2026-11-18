"""Gate A: PostgreSQL in Docker with SQL migrations, proven against a real database.

Needs Docker (marker `db`; CI runs these in their own job). Every test uses its own disposable Compose
project and volume, so the destructive checks never touch anything else.
"""

from __future__ import annotations

import json
import re
import shutil
import socket
import subprocess
from pathlib import Path

import pytest
import yaml

from conftest import render
from dbharness import LocalApp, install, require_docker

pytestmark = pytest.mark.db

MIGRATION = "supabase/migrations/20260101000000_create_items.sql"


@pytest.fixture(scope="module")
def installed(template_repo: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    require_docker()
    project = tmp_path_factory.mktemp("pg-installed") / "pg-app"
    render(template_repo, project, app_name="pg-app", zod=True, database="postgres")
    install(project)
    return project


@pytest.fixture()
def app(installed: Path, tmp_path: Path):
    local = LocalApp(installed, tmp_path / "work")
    try:
        yield local
    finally:
        local.destroy()


@pytest.fixture()
def running(app: LocalApp) -> LocalApp:
    app.start()
    return app


# ------------------------------------------------------------------ configuration files (no Docker)


def test_compose_file_pins_the_image_by_digest_and_publishes_only_on_loopback(installed: Path) -> None:
    compose = yaml.safe_load((installed / "compose.yaml").read_text())
    db = compose["services"]["db"]
    assert re.fullmatch(r"postgres:17\.\d+@sha256:[0-9a-f]{64}", db["image"])
    assert db["ports"] == ["127.0.0.1:${POSTGRES_PORT:-5432}:5432"]
    assert db["healthcheck"]["test"][0] == "CMD-SHELL" and "pg_isready" in db["healthcheck"]["test"][1]
    assert db["volumes"] == ["db-data:/var/lib/postgresql/data"] and "db-data" in compose["volumes"]
    assert "no-new-privileges:true" in db["security_opt"]
    for name in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"):
        assert db["environment"][name].startswith("${" + name + ":?")  # required, with no default value


def test_no_credential_is_committed_and_the_local_env_file_is_ignored(installed: Path) -> None:
    text = (installed / "compose.yaml").read_text() + (installed / ".env.example").read_text()
    assert not re.search(r"(?<![{$])POSTGRES_PASSWORD[:=]\s*(?!\$\{|generated-by-npm-run-db-init)\S", text)  # a value, not a ${reference}
    gitignore = (installed / ".gitignore").read_text().splitlines()
    assert ".env*" in gitignore and "!.env.example" in gitignore
    assert not (installed / ".env.local").exists()


def test_the_command_line_tool_has_no_command_that_deletes_data() -> None:
    from conftest import TEMPLATE_ROOT

    source = (TEMPLATE_ROOT / "template/{% if has_database %}scripts{% endif %}/db/{% if has_database %}cli.mjs{% endif %}.jinja").read_text()
    for forbidden in ("down\", \"-v", "--volumes", "drop database", "drop schema", "truncate", "rmSync", "rm -", "reset"):
        assert forbidden not in source.lower() or forbidden == "reset" and "db reset" not in source.lower(), forbidden
    assert re.search(r'"down", *\)|compose\("down"\)', source)  # `down` stops containers and keeps the volume


# ------------------------------------------------------------------ real database


def test_init_creates_a_private_random_env_file_and_never_overwrites_it(app: LocalApp) -> None:
    first = app.npm("db:init")
    assert first.returncode == 0 and "created .env.local" in first.stdout
    env_file = app.root / ".env.local"
    assert env_file.stat().st_mode & 0o777 == 0o600
    values = app.env_file()
    assert re.fullmatch(r"[0-9a-f]{48}", values["POSTGRES_PASSWORD"]) and values["POSTGRES_PORT"] == str(app.port)
    assert values["DATABASE_URL"] == f"postgresql://app:{values['POSTGRES_PASSWORD']}@127.0.0.1:{app.port}/app"
    before = env_file.read_text()
    second = app.npm("db:init")
    assert second.returncode == 0 and "already exists" in second.stdout and env_file.read_text() == before
    assert values["POSTGRES_PASSWORD"] not in first.stdout + first.stderr  # the password is never printed


def test_up_without_init_fails_with_instructions_and_starts_nothing(app: LocalApp) -> None:
    result = app.npm("db:up")
    assert result.returncode == 1 and "run npm run db:init first" in result.stderr
    assert app.compose("-p", app.project, "ps", "--all", "--quiet").stdout.strip() == ""


def test_a_fresh_database_migrates_to_the_expected_schema(running: LocalApp) -> None:
    result = running.npm("db:migrate")
    assert result.returncode == 0 and "20260101000000_create_items.sql" in result.stdout
    assert running.psql("select table_name from information_schema.tables where table_schema='app'") == "items"
    columns = running.psql("select column_name||':'||data_type||':'||is_nullable from information_schema.columns where table_schema='app' and table_name='items' order by ordinal_position").splitlines()
    assert columns == ["id:uuid:NO", "title:text:NO", "body:text:NO", "created_at:timestamp with time zone:NO", "updated_at:timestamp with time zone:NO"]
    assert running.psql("select indexname from pg_indexes where schemaname='app' order by 1").splitlines() == ["items_pkey", "items_title_key"]
    assert running.psql("select relrowsecurity from pg_class where oid='app.items'::regclass") == "t"
    assert running.psql("select count(*) from pg_constraint where conrelid='app.items'::regclass and contype='c'") == "2"
    assert running.psql("select version||':'||name from public.schema_migrations") == "20260101000000:create_items"


def test_running_the_migrations_again_changes_nothing(running: LocalApp) -> None:
    assert running.npm("db:migrate").returncode == 0
    snapshot = running.psql("select version, name, checksum, applied_at from public.schema_migrations")
    for _ in range(2):
        again = running.npm("db:migrate")
        assert again.returncode == 0 and "already up to date" in again.stdout
    assert running.psql("select version, name, checksum, applied_at from public.schema_migrations") == snapshot


def test_a_modified_migration_is_refused_before_anything_runs_and_again_by_the_database(running: LocalApp) -> None:
    assert running.npm("db:migrate").returncode == 0
    path = running.root / MIGRATION
    original = path.read_text()
    path.write_text(original + "\n-- tampered\n")
    offline = running.npm("db:migrate")
    assert offline.returncode == 1 and "was modified after it was locked" in offline.stderr
    assert running.npm("db:verify").returncode == 1
    # Even with the lock rewritten to match, the checksum recorded by the database still refuses it.
    import hashlib

    lock = running.root / "supabase/migrations.lock/20260101000000_create_items.sha256"
    lock.write_text(hashlib.sha256(path.read_text().replace("\r\n", "\n").encode()).hexdigest() + "\n")
    refused = running.npm("db:migrate")
    assert refused.returncode == 1 and "was modified after it was applied" in refused.stderr
    assert running.psql("select count(*) from app.items") == "0" and "tampered" not in running.psql("select pg_get_viewdef('pg_tables'::regclass)")


def test_an_invalid_migration_fails_visibly_rolls_back_and_records_nothing(running: LocalApp) -> None:
    assert running.npm("db:migrate").returncode == 0
    (running.root / "supabase/migrations/20260102000000_broken.sql").write_text("create table app.broken (id int);\nselect 1/0;\n")
    assert running.npm("db:lock").returncode == 0
    result = running.npm("db:migrate")
    assert result.returncode == 1 and "20260102000000_broken.sql failed and was rolled back" in result.stderr and "division by zero" in result.stderr
    assert running.psql("select to_regclass('app.broken') is null") == "t"
    assert running.psql("select count(*) from public.schema_migrations") == "1"
    (running.root / "supabase/migrations/20260102000000_broken.sql").write_text("create table app.fixed (id int);\n")  # still locked as broken: refused
    assert running.npm("db:migrate").returncode == 1


def test_a_new_migration_older_than_an_applied_one_is_refused(running: LocalApp) -> None:
    assert running.npm("db:migrate").returncode == 0
    (running.root / "supabase/migrations/20260301000000_newer.sql").write_text("create table app.newer (id int);\n")
    assert running.npm("db:lock").returncode == 0 and running.npm("db:migrate").returncode == 0
    (running.root / "supabase/migrations/20260201000000_older.sql").write_text("create table app.older (id int);\n")
    assert running.npm("db:lock").returncode == 0
    result = running.npm("db:migrate")
    assert result.returncode == 1 and "is older than the newest applied migration" in result.stderr
    assert running.psql("select to_regclass('app.older') is null") == "t"


def test_a_malformed_migration_name_or_duplicate_version_is_an_error(running: LocalApp) -> None:
    (running.root / "supabase/migrations/not-a-migration.sql").write_text("select 1;\n")
    assert "migration files are named" in running.npm("db:migrate").stderr
    (running.root / "supabase/migrations/not-a-migration.sql").unlink()
    (running.root / "supabase/migrations/20260101000000_duplicate.sql").write_text("select 1;\n")
    result = running.npm("db:migrate")
    assert result.returncode == 1 and "used by more than one migration" in result.stderr


def test_data_persists_across_a_container_restart_and_a_down_up_cycle(running: LocalApp) -> None:
    assert running.npm("db:migrate").returncode == 0
    running.psql("insert into app.items (title, body) values ('persist me', 'still here')")
    assert running.compose("restart", "db").returncode == 0
    assert running.npm("db:up").returncode == 0
    assert running.psql("select title from app.items") == "persist me"
    assert running.npm("db:down").returncode == 0
    volumes = subprocess.run(["docker", "volume", "ls", "--format", "{{.Name}}"], capture_output=True, text=True, check=False).stdout.split()
    assert f"{running.project}_db-data" in volumes  # `down` keeps the data
    assert running.npm("db:up").returncode == 0
    assert running.psql("select title||'/'||body from app.items") == "persist me/still here"
    assert running.npm("db:migrate").returncode == 0 and running.psql("select count(*) from app.items") == "1"


def test_the_database_is_published_on_loopback_only(running: LocalApp) -> None:
    published = json.loads(subprocess.run(["docker", "inspect", f"{running.project}-db-1", "--format", "{{json .NetworkSettings.Ports}}"], capture_output=True, text=True, check=True).stdout)
    assert published["5432/tcp"] == [{"HostIp": "127.0.0.1", "HostPort": str(running.port)}]
    with socket.socket() as sock:
        sock.settimeout(3)
        assert sock.connect_ex(("127.0.0.1", running.port)) == 0
    lan = subprocess.run("ipconfig getifaddr en0 || ipconfig getifaddr en1 || hostname -I | cut -d' ' -f1", shell=True, capture_output=True, text=True, check=False).stdout.strip()
    if lan:
        with socket.socket() as sock:
            sock.settimeout(3)
            assert sock.connect_ex((lan, running.port)) != 0, "the database must not be reachable on a non-loopback address"
    listeners = subprocess.run(["lsof", "-nP", f"-iTCP:{running.port}", "-sTCP:LISTEN"], capture_output=True, text=True, check=False).stdout
    assert "127.0.0.1" in listeners and "*:" not in listeners


def test_the_status_command_reports_health_and_migrations_without_a_credential(running: LocalApp) -> None:
    assert running.npm("db:migrate").returncode == 0
    status = running.npm("db:status")
    assert status.returncode == 0 and "healthy" in status.stdout and "migrations applied" in status.stdout
    assert running.env_file()["POSTGRES_PASSWORD"] not in status.stdout + status.stderr


def test_the_runner_refuses_a_non_local_database_unless_allowed(running: LocalApp) -> None:
    result = running.node('import { assertLocalHost } from "./scripts/db/migrations.mjs"; assertLocalHost("postgresql://u:p@db.example.com:5432/x");')
    assert result.returncode != 0 and "refusing to migrate a non-local database" in result.stderr
    ok = running.node('import { assertLocalHost } from "./scripts/db/migrations.mjs"; assertLocalHost("postgresql://u:p@db.example.com:5432/x", true); assertLocalHost("postgresql://u:p@127.0.0.1:1/x");')
    assert ok.returncode == 0, ok.stderr


def test_a_second_migration_runs_in_version_order_alongside_existing_data(running: LocalApp) -> None:
    assert running.npm("db:migrate").returncode == 0
    running.psql("insert into app.items (title) values ('existing')")
    (running.root / "supabase/migrations/20260201000000_add_priority.sql").write_text("alter table app.items add column priority int not null default 0;\n")
    assert running.npm("db:lock").returncode == 0
    result = running.npm("db:migrate")
    assert result.returncode == 0 and "20260201000000_add_priority.sql" in result.stdout
    assert running.psql("select title||':'||priority from app.items") == "existing:0"  # data preserved through the upgrade
    assert running.psql("select version from public.schema_migrations order by version").splitlines() == ["20260101000000", "20260201000000"]
