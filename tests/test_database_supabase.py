"""Gate C: the local Supabase provider, proven against a real, running local stack.

Needs Docker and the pinned Supabase CLI (installed by `npm ci`; marker `db`). One stack is started per
module on random loopback-mapped ports under a unique project id and removed, with its volumes, at the end.
The CLI publishes its ports on every interface (see docs/database.md); the tests opt in to that explicitly
(SUPABASE_ALLOW_LAN_EXPOSURE) because a CI runner or a developer machine is the test's own throwaway host.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest

from conftest import render
from dbharness import free_port, install, plain, require_docker, run

pytestmark = pytest.mark.db

DEFAULT_JWT_SECRET = b"super-secret-jwt-token-with-at-least-32-characters-long"  # the documented local default
POSTGRES_IMAGE = "postgres:17.10@sha256:7958605b474b3d264a969cb3a123d6aa00ad1e1fe9da8a69984dabb704d93317"
SNAPSHOT_JS = """
import pg from "pg";
const client = new pg.Client({ connectionString: process.env.TARGET });
await client.connect();
const q = async (sql) => (await client.query(sql)).rows;
console.log(JSON.stringify({
  columns: await q("select column_name, data_type, is_nullable, column_default from information_schema.columns where table_schema = 'app' order by table_name, ordinal_position"),
  indexes: await q("select indexname, indexdef from pg_indexes where schemaname = 'app' order by 1"),
  constraints: await q("select conname, contype, pg_get_constraintdef(oid) as def from pg_constraint where connamespace = 'app'::regnamespace order by 1"),
  // A NULL ACL means "the owner's default privileges". Revoking from a role that exists (Supabase's API
  // roles) makes PostgreSQL store that default explicitly, so compare effective grants, not the stored text.
  rls: await q("select relname, relrowsecurity, relforcerowsecurity, coalesce(relacl::text, '{' || pg_get_userbyid(relowner) || '=arwdDxtm/' || pg_get_userbyid(relowner) || '}') as acl from pg_class where relnamespace = 'app'::regnamespace and relkind in ('r','S') order by 1"),
  schemaAcl: await q("select nspacl::text as acl from pg_namespace where nspname = 'app'"),
}));
await client.end();
"""


class SupabaseApp:
    """A generated application with its own Supabase project id and ports."""

    def __init__(self, installed: Path) -> None:
        self.root = installed
        self.project = f"wp{uuid.uuid4().hex[:8]}"
        self.api_port, self.db_port = free_port(), free_port()
        config = (installed / "supabase/config.toml").read_text()
        self._original = config
        config = re.sub(r'project_id = "[^"]+"', f'project_id = "{self.project}"', config)
        config = config.replace("port = 54321", f"port = {self.api_port}").replace("port = 54322", f"port = {self.db_port}")
        (installed / "supabase/config.toml").write_text(config)
        self.env = {"SUPABASE_ALLOW_LAN_EXPOSURE": "true"}
        (installed / ".env.local").write_text(f"DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:{self.db_port}/postgres\n")

    def npm(self, *args: str, env: dict[str, str] | None = None, timeout: int = 900) -> subprocess.CompletedProcess[str]:
        return run(["npm", "run", "--silent", *args], self.root, {**self.env, **(env or {})}, timeout)

    def cli(self, *args: str, timeout: int = 900) -> subprocess.CompletedProcess[str]:
        return run(["npx", "--no-install", "supabase", *args], self.root, self.env, timeout)

    def psql(self, sql: str) -> str:
        result = run(["docker", "exec", f"supabase_db_{self.project}", "psql", "-U", "postgres", "-d", "postgres", "-At", "-c", sql], self.root)
        return (result.stdout + result.stderr).strip()

    @property
    def database_url(self) -> str:
        return f"postgresql://postgres:postgres@127.0.0.1:{self.db_port}/postgres"

    def containers(self) -> list[str]:
        out = subprocess.run(["docker", "ps", "-a", "--filter", f"label=com.supabase.cli.project={self.project}", "--format", "{{.Names}}"], capture_output=True, text=True, check=False)
        return out.stdout.split()

    def destroy(self) -> None:
        """Remove this project's containers and volumes (scoped by its unique project id), then restore the config."""
        self.cli("stop", "--no-backup")
        (self.root / "supabase/config.toml").write_text(self._original)
        (self.root / ".env.local").unlink(missing_ok=True)


@pytest.fixture(scope="module")
def installed(template_repo: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    require_docker()
    project = tmp_path_factory.mktemp("sb-installed") / "sb-app"
    render(template_repo, project, app_name="sb-app", zod=True, database="supabase")
    install(project)
    return project


@pytest.fixture(scope="module")
def stack(installed: Path):
    app = SupabaseApp(installed)
    try:
        started = app.npm("db:up")
        assert started.returncode == 0, started.stdout[-2000:] + started.stderr[-2000:]
        yield app
    finally:
        app.destroy()


def _jwt(role: str) -> str:
    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    head = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = b64(json.dumps({"role": role, "iss": "supabase-demo", "exp": int(time.time()) + 600}).encode())
    return f"{head}.{body}." + b64(hmac.new(DEFAULT_JWT_SECRET, f"{head}.{body}".encode(), hashlib.sha256).digest())


def _api(app: SupabaseApp, method: str, path: str, role: str, headers: dict[str, str] | None = None, body: bytes | None = None) -> tuple[int, str]:
    token = _jwt(role)
    request = urllib.request.Request(f"http://127.0.0.1:{app.api_port}/rest/v1{path}", method=method, data=body, headers={"apikey": token, "Authorization": f"Bearer {token}", **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()


# ------------------------------------------------------------------ configuration (no Docker)


def test_the_config_pins_postgres_17_exposes_no_extra_schema_and_leaves_unneeded_services_off(installed: Path) -> None:
    import tomllib

    config = tomllib.loads((installed / "supabase/config.toml").read_text())
    assert config["db"]["major_version"] == 17 and config["db"]["port"] == 54322 and config["api"]["port"] == 54321
    assert "app" not in config["api"]["schemas"] and config["api"]["max_rows"] <= 100
    for service in ("auth", "realtime", "storage", "studio", "edge_runtime", "local_smtp", "analytics"):
        assert config[service]["enabled"] is False, service  # Auth is reserved for the authentication work package
    assert config["db"]["seed"]["enabled"] is False and config["project_id"] == "sb-app"


def test_the_cli_is_a_pinned_exact_development_dependency_with_no_install_script(installed: Path) -> None:
    package = json.loads((installed / "package.json").read_text())
    assert package["devDependencies"]["supabase"] == "2.122.1" and "supabase" not in package["dependencies"]
    lock = json.loads((installed / "package-lock.json").read_text())["packages"]
    assert lock["node_modules/supabase"]["version"] == "2.122.1" and "hasInstallScript" not in lock["node_modules/supabase"]
    platform = {name: entry for name, entry in lock.items() if name.startswith("node_modules/@supabase/cli-")}
    assert len(platform) == 8 and all(entry.get("optional") and "integrity" in entry and "hasInstallScript" not in entry for entry in platform.values())
    assert not (installed / "compose.yaml").exists()  # the stack is the CLI's, not a second compose file


# ------------------------------------------------------------------ the running stack


def test_the_stack_started_and_applied_the_canonical_migration(stack: SupabaseApp) -> None:
    assert {f"supabase_{kind}_{stack.project}" for kind in ("db", "kong", "rest")} <= set(stack.containers())
    assert stack.psql("select version||' '||name from supabase_migrations.schema_migrations") == "20260101000000 create_items"
    assert stack.psql("select table_name from information_schema.tables where table_schema='app'") == "items"
    assert stack.psql("show server_version").startswith("17.")
    status = stack.npm("db:status")
    assert status.returncode == 0 and "network exposure" in status.stdout


def test_migrating_again_applies_nothing_and_the_lock_still_guards_history(stack: SupabaseApp) -> None:
    again = stack.npm("db:migrate")
    assert again.returncode == 0 and ('"applied":[]' in again.stdout or "up to date" in again.stdout.lower())  # the CLI words an empty run differently by environment
    assert stack.psql("select count(*) from supabase_migrations.schema_migrations") == "1"
    path = stack.root / "supabase/migrations/20260101000000_create_items.sql"
    original = path.read_text()
    try:
        path.write_text(original + "\n-- tampered\n")
        refused = stack.npm("db:migrate")
        assert refused.returncode == 1 and "was modified after it was locked" in refused.stderr
    finally:
        path.write_text(original)
    assert stack.npm("db:verify").returncode == 0


def test_a_new_migration_is_applied_by_the_cli_in_order_and_keeps_data(stack: SupabaseApp) -> None:
    stack.psql("insert into app.items (title) values ('kept through a migration')")
    path = stack.root / "supabase/migrations/20260201000000_add_priority.sql"
    lock = stack.root / "supabase/migrations.lock/20260201000000_add_priority.sha256"
    path.write_text("alter table app.items add column priority int not null default 0;\n")
    try:
        assert stack.npm("db:lock").returncode == 0
        applied = stack.npm("db:migrate")
        assert applied.returncode == 0, applied.stdout + applied.stderr
        assert stack.psql("select title||':'||priority from app.items") == "kept through a migration:0"
        assert stack.psql("select version from supabase_migrations.schema_migrations order by version").splitlines() == ["20260101000000", "20260201000000"]
    finally:  # leave the application as it was generated; the next reset rebuilds the database from the baseline
        path.unlink(missing_ok=True)
        lock.unlink(missing_ok=True)


def test_the_same_repository_tests_pass_against_the_supabase_database(stack: SupabaseApp) -> None:
    result = stack.npm("test:db", env={"DATABASE_URL": stack.database_url})
    output = plain(result.stdout + result.stderr)
    assert result.returncode == 0, output[-3000:]
    assert "Test Files  3 passed (3)" in output and re.search(r"Tests\s+5\d passed", output)
    assert stack.psql("select count(*) from pg_database where datname like 'app_test_%'") == "0"


def test_the_data_api_does_not_expose_the_protected_sample_data_to_any_role(stack: SupabaseApp) -> None:
    for role in ("anon", "service_role"):  # service_role bypasses row level security, and still gets nothing
        status, body = _api(stack, "GET", "/items", role)
        assert status == 404 and "public.items" in body
        status, body = _api(stack, "GET", "/items", role, {"Accept-Profile": "app"})
        assert status == 406 and "Invalid schema: app" in body
        status, body = _api(stack, "POST", "/items", role, {"Content-Profile": "app", "Content-Type": "application/json"}, b'{"title": "x"}')
        assert status == 406 and "Invalid schema: app" in body
        status, body = _api(stack, "GET", "/", role)
        assert status == 200 and "items" not in json.loads(body).get("paths", {}) and "/items" not in body
    assert "kept through a migration" not in json.dumps(_api(stack, "GET", "/items", "anon"))


def test_api_roles_hold_no_privilege_on_the_schema_or_the_table(stack: SupabaseApp) -> None:
    for role in ("anon", "authenticated", "service_role"):
        assert stack.psql(f"select has_schema_privilege('{role}', 'app', 'usage'), has_table_privilege('{role}', 'app.items', 'select'), has_table_privilege('{role}', 'app.items', 'insert')") == "f|f|f"
        assert "permission denied for schema app" in stack.psql(f"set role {role}; select count(*) from app.items")
    assert stack.psql("select relrowsecurity from pg_class where oid = 'app.items'::regclass") == "t"
    assert stack.psql("select nspacl is null from pg_namespace where nspname = 'app'") in ("f", "t")


def test_no_privileged_key_or_secret_appears_in_application_source(stack: SupabaseApp) -> None:
    """Application code never mentions the JWT secret, a service-role key or the database password.

    The build output is checked for the same things in test_database_data_access.py; the data layer
    reads DATABASE_URL on the server and nothing in `src` handles an API key.
    """
    for path in sorted((stack.root / "src").rglob("*")):
        if path.is_file() and not path.name.endswith((".test.ts", ".test.tsx")) and "test-support" not in path.parts:
            text = path.read_text(errors="ignore")
            for forbidden in (DEFAULT_JWT_SECRET.decode(), "service_role", "SERVICE_ROLE", "postgres:postgres", "SUPABASE_"):
                assert forbidden not in text, f"{path.relative_to(stack.root)} mentions {forbidden}"
    committed = (stack.root / "supabase" / "config.toml").read_text() + (stack.root / ".env.example").read_text()
    assert DEFAULT_JWT_SECRET.decode() not in committed and "service_role" not in committed.replace("service_role", "", 0)


def test_the_loopback_check_fails_closed_without_the_explicit_opt_in(stack: SupabaseApp) -> None:
    ports = subprocess.run(["docker", "inspect", f"supabase_db_{stack.project}", "--format", "{{json .NetworkSettings.Ports}}"], capture_output=True, text=True, check=True).stdout
    exposed = any(binding["HostIp"] not in ("127.0.0.1", "::1") for bindings in json.loads(ports).values() for binding in bindings or [])
    stack.psql("insert into app.items (title) values ('survives the stop')")
    refused = stack.npm("db:up", env={"SUPABASE_ALLOW_LAN_EXPOSURE": "false"})
    if exposed:  # the CLI's default: published on every interface
        assert refused.returncode == 1 and "reachable from other machines" in refused.stderr and "the stack was stopped" in refused.stderr
        assert not [name for name in stack.containers() if name.startswith("supabase_db_") and subprocess.run(["docker", "ps", "-q", "--filter", f"name={name}"], capture_output=True, text=True, check=False).stdout.strip()]
        restarted = stack.npm("db:up")  # with the explicit opt-in
        assert restarted.returncode == 0 and "continuing on your responsibility" in restarted.stderr
    else:
        assert refused.returncode == 0
    assert "survives the stop" in stack.psql("select string_agg(title, ',') from app.items")  # stopping keeps the data


def test_a_database_reset_recreates_only_the_local_database_from_the_migrations(stack: SupabaseApp) -> None:
    stack.psql("insert into app.items (title) values ('lost on reset')")
    reset = stack.cli("db", "reset")
    assert reset.returncode == 0, reset.stdout[-1500:] + reset.stderr[-1500:]
    assert stack.psql("select count(*) from app.items") == "0"  # a disposable local database is recreated from the migrations
    assert stack.psql("select version from supabase_migrations.schema_migrations order by version").splitlines()[0] == "20260101000000"
    assert stack.psql("select relrowsecurity from pg_class where oid = 'app.items'::regclass") == "t"


def test_the_schema_is_identical_to_standalone_postgres(stack: SupabaseApp, installed: Path) -> None:
    """The canonical migrations produce the same schema on Supabase and on plain PostgreSQL 17.

    The one stored difference is cosmetic and compared as effective privileges: see SNAPSHOT_JS.
    """
    stack.cli("db", "reset")  # only the baseline migration: a fair comparison with a fresh database
    for extra in ("20260201000000_add_priority.sql",):
        (installed / "supabase/migrations" / extra).unlink(missing_ok=True)
    name, port = f"wpstandalone-{uuid.uuid4().hex[:8]}", free_port()
    try:
        started = subprocess.run(["docker", "run", "-d", "--name", name, "-p", f"127.0.0.1:{port}:5432", "-e", "POSTGRES_PASSWORD=standalone-test", POSTGRES_IMAGE], capture_output=True, text=True, check=False)
        assert started.returncode == 0, started.stderr
        for _ in range(60):
            if subprocess.run(["docker", "exec", name, "pg_isready", "-U", "postgres"], capture_output=True, check=False).returncode == 0:
                break
            time.sleep(1)
        time.sleep(2)
        standalone = f"postgresql://postgres:standalone-test@127.0.0.1:{port}/postgres"
        migrated = run(["node", "--input-type=module", "-e", 'import { migrate } from "./scripts/db/migrations.mjs"; await migrate({ connectionString: process.env.TARGET });'], installed, {"TARGET": standalone})
        assert migrated.returncode == 0, migrated.stderr
        def snapshot(url: str) -> dict:
            result = run(["node", "--input-type=module", "-e", SNAPSHOT_JS], installed, {"TARGET": url})
            assert result.returncode == 0, result.stderr
            return json.loads(result.stdout)
        on_supabase, on_postgres = snapshot(stack.database_url), snapshot(standalone)
        assert on_supabase == on_postgres
        assert on_postgres["columns"] and on_postgres["indexes"] and on_postgres["constraints"]  # the comparison is not vacuous
    finally:
        subprocess.run(["docker", "rm", "--force", "--volumes", name], capture_output=True, check=False)


# ------------------------------------------------------------------ provider independence (no Docker)


def test_switching_provider_changes_no_application_code(template_repo: Path, tmp_path: Path) -> None:
    """Domain code is the same for both providers; only provider tooling and configuration differ."""
    render(template_repo, tmp_path / "pg", app_name="same-app", zod=True, database="postgres")
    render(template_repo, tmp_path / "sb", app_name="same-app", zod=True, database="supabase")

    def files(root: Path) -> dict[str, bytes]:
        return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}

    pg, sb = files(tmp_path / "pg"), files(tmp_path / "sb")
    differing = {name for name in pg.keys() | sb.keys() if pg.get(name) != sb.get(name)}
    provider_files = {".copier-answers.yml", ".env.example", "compose.yaml", "supabase/config.toml", "scripts/db/cli.mjs", "package.json", "package-lock.json", "README.md", "AGENTS.md", "docs/database.md", ".github/workflows/ci.yml"}
    assert differing <= provider_files, sorted(differing - provider_files)
    assert {"compose.yaml", "supabase/config.toml", "scripts/db/cli.mjs", ".env.example"} <= differing  # the provider tooling really differs
    for shared in ("src", "scripts/db/migrations.mjs", "supabase/migrations", "vitest.db.config.mts"):
        assert {n: pg[n] for n in pg if n.startswith(shared)} == {n: sb[n] for n in sb if n.startswith(shared)}, shared
