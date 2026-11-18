"""Authentication end to end, for both providers: a real database, the real least-privilege login and a real
production server (`next start` of the production build).

Needs Docker (marker `db`; CI runs these in their own job). Each provider gets one disposable environment per
module (a unique Compose project or Supabase project id on random loopback ports), removed with its volumes at
the end. The Supabase CLI publishes its ports on every interface; these tests opt in explicitly
(SUPABASE_ALLOW_LAN_EXPOSURE) because the host is the test's own throwaway machine.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

import pytest

from authharness import Client, Server, sign_up
from conftest import render
from dbharness import free_port, install, plain, require_docker, run

pytestmark = pytest.mark.db

PROVIDERS = ["postgres", "supabase"]
DEFAULT_JWT_SECRET = b"super-secret-jwt-token-with-at-least-32-characters-long"  # the documented local default
ADMIN_ROLE = {"postgres": "app", "supabase": "postgres"}
TRANSCRIPTS: dict[str, list] = {}
LOGIN_JS = """
import pg from "pg";
const client = new pg.Client({ connectionString: process.env.TARGET });
await client.connect();
const out = {};
for (const [name, sql] of Object.entries(JSON.parse(process.env.STATEMENTS))) {
  try { const r = await client.query(sql); out[name] = r.rowCount === null ? "ok" : `ok:${r.rowCount}`; } catch (e) { out[name] = e.code; }
}
out.identity = (await client.query("select current_user as c, session_user as s")).rows[0];
console.log(JSON.stringify(out));
await client.end();
"""


class Env:
    """One provider's disposable database and generated application, built in place."""

    def __init__(self, provider: str, project: Path) -> None:
        self.provider, self.root = provider, project
        self.project_id = f"wpauth{uuid.uuid4().hex[:8]}"
        self.logs = project.parent / "logs"
        self.logs.mkdir()
        self.env = {"SUPABASE_ALLOW_LAN_EXPOSURE": "true"}
        self.original_config: str | None = None
        if provider == "postgres":
            self.env |= {"COMPOSE_PROJECT_NAME": self.project_id, "POSTGRES_PORT": str(free_port())}
        else:
            self.api_port, self.db_port = free_port(), free_port()
            self.original_config = (project / "supabase/config.toml").read_text()
            config = re.sub(r'project_id = "[^"]+"', f'project_id = "{self.project_id}"', self.original_config)
            config = config.replace("port = 54321", f"port = {self.api_port}").replace("port = 54322", f"port = {self.db_port}")
            (project / "supabase/config.toml").write_text(config)

    # ---- running things
    def npm(self, *args: str, env: dict[str, str] | None = None, timeout: int = 900) -> subprocess.CompletedProcess[str]:
        return run(["npm", "run", "--silent", *args], self.root, {**self.env, **(env or {})}, timeout)

    def node(self, code: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        return run(["node", "--input-type=module", "-e", code], self.root, {**self.env, **env})

    def cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return run(["npx", "--no-install", "supabase", *args], self.root, self.env)

    def start(self) -> None:
        if self.provider == "supabase":  # an existing, older-style environment file: init must complete it, not replace it
            (self.root / ".env.local").write_text(f"DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:{self.db_port}/postgres\n")
        for command in ("db:init", "db:up", "db:migrate"):
            result = self.npm(command)
            assert result.returncode == 0, f"{command}: {plain(result.stdout)[-1500:]}{plain(result.stderr)[-1500:]}"

    def psql(self, sql: str) -> str:
        """As the administrator, inside the database container."""
        if self.provider == "postgres":
            result = run(["docker", "compose", "--env-file", ".env.local", "exec", "-T", "db", "sh", "-c", 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -c "$1"', "sh", sql], self.root, self.env)
        else:
            result = run(["docker", "exec", f"supabase_db_{self.project_id}", "psql", "-U", "postgres", "-d", "postgres", "-At", "-c", sql], self.root)
        return (result.stdout + result.stderr).strip()

    def stop_database(self) -> None:
        assert self.npm("db:down").returncode == 0

    def start_database(self) -> None:
        result = self.npm("db:up")
        assert result.returncode == 0, plain(result.stdout)[-1500:]

    def destroy(self) -> None:
        if self.provider == "postgres":
            if (self.root / ".env.local").exists():
                run(["docker", "compose", "--env-file", ".env.local", "-p", self.project_id, "down", "--volumes", "--remove-orphans"], self.root, self.env)
        else:
            self.cli("stop", "--no-backup")
            (self.root / "supabase/config.toml").write_text(self.original_config or "")

    # ---- what init wrote
    def file_env(self) -> dict[str, str]:
        lines = (self.root / ".env.local").read_text().splitlines()
        return dict(line.split("=", 1) for line in lines if "=" in line and not line.startswith("#"))

    @property
    def admin_url(self) -> str:
        return self.file_env()["DATABASE_URL"]

    @property
    def runtime_url(self) -> str:
        return self.file_env()["DATABASE_RUNTIME_URL"]

    def secrets(self) -> dict[str, str]:
        values = self.file_env()
        found = {"secret": values["BETTER_AUTH_SECRET"], "runtime password": re.search(r"://app_runtime:([^@]+)@", values["DATABASE_RUNTIME_URL"]).group(1)}
        if self.provider == "postgres":
            found["admin password"] = values["POSTGRES_PASSWORD"]
        return found

    def server_env(self, **overrides: str) -> dict[str, str]:
        values = self.file_env()
        base = {"DATABASE_RUNTIME_URL": values["DATABASE_RUNTIME_URL"], "BETTER_AUTH_SECRET": values["BETTER_AUTH_SECRET"]}
        return {**base, **overrides}

    def server(self, name: str, **overrides: str) -> Server:
        return Server(self.root, self.server_env(**overrides), self.logs / f"{name}.log")


@pytest.fixture(scope="module", params=PROVIDERS)
def env(request, template_repo: Path, tmp_path_factory: pytest.TempPathFactory):
    require_docker()
    provider = request.param
    project = tmp_path_factory.mktemp(f"auth-{provider}") / f"{provider}-auth-app"
    render(template_repo, project, app_name=f"{provider}-auth-app", zod=True, rest_api=True, database=provider, auth=True)
    install(project)
    clean = run(["npm", "run", "--silent", "build"], project, {})  # no .env.local exists: a build must need no secret
    assert clean.returncode == 0, plain(clean.stdout)[-2000:] + plain(clean.stderr)[-2000:]
    environment = Env(provider, project)
    try:
        environment.start()
        built = environment.npm("build", env=environment.server_env())  # with every secret present: none may end up in the output
        assert built.returncode == 0, plain(built.stdout)[-2000:] + plain(built.stderr)[-2000:]
        yield environment
    finally:
        environment.destroy()


@pytest.fixture(scope="module")
def main(env: Env):
    """The server the flows run against: production mode, sign-up open on purpose, one trusted proxy header."""
    with env.server("main", AUTH_ALLOW_SIGN_UP="true", AUTH_TRUSTED_IP_HEADER="x-real-ip") as server:
        yield server


# ------------------------------------------------------------------ the environment file


def _fresh_scripts(env: Env, tmp: Path) -> Path:
    shutil.copytree(env.root / "scripts", tmp / "scripts")
    (tmp / "node_modules").symlink_to(env.root / "node_modules")
    return tmp


def test_init_writes_a_complete_private_file_and_never_overwrites_it(env: Env, tmp_path: Path) -> None:
    work = _fresh_scripts(env, tmp_path / "fresh")
    first = run(["node", "scripts/db/cli.mjs", "init"], work, {"POSTGRES_PORT": "55432"})
    assert first.returncode == 0, first.stderr
    path = work / ".env.local"
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    values = dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line and not line.startswith("#"))
    assert re.fullmatch(r"[0-9a-f]{64}", values["BETTER_AUTH_SECRET"]) and values["BETTER_AUTH_URL"] == "http://localhost:3000"
    runtime = re.fullmatch(r"postgresql://app_runtime:([0-9a-f]{48})@127\.0\.0\.1:(\d+)/(\w+)", values["DATABASE_RUNTIME_URL"])
    assert runtime, "the runtime URL names the app_runtime role with a random password"
    assert values["DATABASE_RUNTIME_URL"] != values["DATABASE_URL"] and runtime.group(1) not in values["DATABASE_URL"]
    assert first.stdout.count("created") == 1 and not any(value in first.stdout + first.stderr for value in (values["BETTER_AUTH_SECRET"], runtime.group(1)))
    before = path.read_bytes()
    again = run(["node", "scripts/db/cli.mjs", "init"], work, {"POSTGRES_PORT": "55432"})
    assert again.returncode == 0 and "already complete" in again.stdout and path.read_bytes() == before


def test_init_completes_an_older_file_by_appending_only_what_is_missing(env: Env, tmp_path: Path) -> None:
    work = _fresh_scripts(env, tmp_path / "older")
    old = (
        "POSTGRES_USER=app\nPOSTGRES_PASSWORD=" + "ab" * 24 + "\nPOSTGRES_DB=app\nPOSTGRES_PORT=55999\n# my own note\nDATABASE_URL=postgresql://app:" + "ab" * 24 + "@127.0.0.1:55999/app\n"
        if env.provider == "postgres"
        else "# my own note\nDATABASE_URL=postgresql://postgres:postgres@127.0.0.1:55999/postgres"
    )
    (work / ".env.local").write_text(old)
    result = run(["node", "scripts/db/cli.mjs", "init"], work, {})
    assert result.returncode == 0, result.stderr
    new = (work / ".env.local").read_text()
    assert new.startswith(old) and "# my own note" in new  # every existing byte survives, in place
    assert "added DATABASE_RUNTIME_URL, BETTER_AUTH_SECRET, BETTER_AUTH_URL" in result.stdout and "existing values were not changed" in result.stdout
    values = dict(line.split("=", 1) for line in new.splitlines() if "=" in line and not line.startswith("#"))
    assert values["DATABASE_RUNTIME_URL"].endswith("@127.0.0.1:55999/" + ("app" if env.provider == "postgres" else "postgres"))  # same server and database as the administrative URL
    assert run(["node", "scripts/db/cli.mjs", "init"], work, {}).stdout.strip().endswith("already complete; not changed")


def test_the_environment_file_of_this_run_is_private_and_ignored(env: Env) -> None:
    assert oct((env.root / ".env.local").stat().st_mode & 0o777) == "0o600"
    assert ".env*" in (env.root / ".gitignore").read_text().splitlines()
    assert env.runtime_url != env.admin_url


# ------------------------------------------------------------------ the least-privilege role, as a real login


def test_migrate_gives_the_runtime_role_a_login_and_nothing_else(env: Env) -> None:
    attributes = env.psql("select rolcanlogin, rolsuper, rolcreaterole, rolcreatedb, rolreplication, rolbypassrls, rolconnlimit from pg_roles where rolname = 'app_runtime'")
    assert attributes == "t|f|f|f|f|f|20"
    owned = env.psql("select count(*) from pg_class c join pg_roles r on r.oid = c.relowner where r.rolname = 'app_runtime'")
    assert owned == "0"
    status = plain(env.npm("db:status").stdout)
    assert "runtime role: app_runtime, can log in" in status
    again = env.npm("db:migrate")
    assert again.returncode == 0 and "runtime role app_runtime is ready" in again.stdout  # migrating again changes nothing that matters
    assert env.secrets()["runtime password"] not in again.stdout + again.stderr + status


def _login(env: Env, url: str, statements: dict[str, str]) -> dict:
    result = env.node(LOGIN_JS, {"TARGET": url, "STATEMENTS": json.dumps(statements)})
    assert result.returncode == 0, result.stderr[-800:]
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_the_runtime_login_cannot_escalate_administer_or_reach_other_data(env: Env) -> None:
    admin = ADMIN_ROLE[env.provider]
    history = "public.schema_migrations" if env.provider == "postgres" else "supabase_migrations.schema_migrations"
    env.psql("create schema if not exists other; create table if not exists other.secret (x int)")
    statements = {
        "set role admin": f"set role {admin}",
        "set session authorization": f"set session authorization {admin}",
        "create schema": "create schema evil",
        "create table in app": "create table app.evil (id int)",
        "create table in app_auth": "create table app_auth.evil (id int)",
        "create table in public": "create table public.evil (id int)",
        "create role": "create role evil login",
        "create database": "create database evil",
        "make itself superuser": "alter role app_runtime superuser",
        "bypass rls": "alter role app_runtime bypassrls",
        "read password hashes": "select * from pg_catalog.pg_authid",
        "read the migration history": f"select * from {history}",
        "read another schema": "select * from other.secret",
        "drop the items": "drop table app.items",
        "truncate the items": "truncate app.items",
        "switch rls off": "alter table app.items disable row level security",
        "add a policy": "create policy evil on app.items using (true)",
        "write without a user": "insert into app.items (owner_id, title) values ('someone', 'x')",
        "change an owner": "update app.items set owner_id = 'someone'",
        "copy to a program": "copy (select 1) to program 'true'",
        "read a file": "select pg_read_file('/etc/passwd')",
    }
    outcome = _login(env, env.runtime_url, statements)
    assert outcome.pop("identity") == {"c": "app_runtime", "s": "app_runtime"}  # a real login as the role, not the administrator with a role set
    assert {name: code for name, code in outcome.items()} == {name: "42501" for name in statements}, outcome
    allowed = _login(env, env.runtime_url, {"no user, no rows": "select count(*) from app.items", "own tables": 'select count(*) from app_auth."user"'})
    assert allowed["no user, no rows"].startswith("ok") and allowed["own tables"].startswith("ok")
    wrong = env.runtime_url.replace(env.secrets()["runtime password"], "0" * 48)
    assert _login_failure(env, wrong) in ("28P01", "28000")  # a wrong password is refused


def _login_failure(env: Env, url: str) -> str:
    result = env.node("import pg from 'pg'; const c = new pg.Client({ connectionString: process.env.TARGET }); try { await c.connect(); console.log('connected'); } catch (e) { console.log(e.code); }", {"TARGET": url})
    return result.stdout.strip()


def test_the_projects_own_database_tests_pass(env: Env) -> None:
    result = env.npm("test:db")
    output = plain(result.stdout + result.stderr)
    assert result.returncode == 0, output[-3000:]
    assert re.search(r"Tests\s+(\d+) passed \(\1\)", output), output[-600:]
    assert "failed" not in output.lower().split("tests")[-1]
    assert env.psql("select count(*) from pg_database where datname like 'app_test_%'") == "0"  # every test database was dropped


def test_the_provider_specific_exposure_rules_still_hold(env: Env) -> None:
    if env.provider == "postgres":
        bound = subprocess.run(["docker", "ps", "--filter", f"label=com.docker.compose.project={env.project_id}", "--format", "{{.Ports}}"], capture_output=True, text=True, check=False).stdout
        assert "127.0.0.1:" in bound and "0.0.0.0" not in bound
        return
    running = subprocess.run(["docker", "ps", "--filter", f"label=com.supabase.cli.project={env.project_id}", "--format", "{{.Names}}"], capture_output=True, text=True, check=False).stdout.split()
    assert running and not [name for name in running if name.startswith(("supabase_auth_", "supabase_gotrue_"))]  # Supabase Auth is not running


def _jwt(role: str) -> str:
    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    head = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = b64(json.dumps({"role": role, "iss": "supabase-demo", "exp": int(time.time()) + 600}).encode())
    return f"{head}.{body}." + b64(hmac.new(DEFAULT_JWT_SECRET, f"{head}.{body}".encode(), hashlib.sha256).digest())


@pytest.mark.parametrize("env", ["supabase"], indirect=True)  # Supabase's own Data API: there is no such thing on the postgres provider
def test_supabase_data_api_cannot_reach_the_application_or_auth_schemas(env: Env) -> None:
    for role in ("anon", "authenticated", "service_role"):
        token = _jwt(role)
        for schema in ("app", "app_auth"):
            request = urllib.request.Request(f"http://127.0.0.1:{env.api_port}/rest/v1/items", headers={"apikey": token, "Authorization": f"Bearer {token}", "Accept-Profile": schema})
            try:
                status = urllib.request.urlopen(request, timeout=15).status
            except urllib.error.HTTPError as error:
                status = error.code
            assert status in (404, 406), (role, schema, status)  # the schema is not exposed: PGRST106
        for schema, table in (("app", "items"), ("app_auth", '"user"'), ("app_auth", "session")):
            denied = env.psql(f"set role {role}; select count(*) from {schema}.{table}")
            assert "permission denied" in denied, (role, schema, table, denied)


# ------------------------------------------------------------------ the production build


def _walk(directory: Path):
    for path in directory.rglob("*"):
        if path.is_file():
            yield path


def test_the_production_build_embeds_no_secret_and_no_connection_string(env: Env) -> None:
    secrets = env.secrets()
    needles = {name: value.encode() for name, value in secrets.items()} | {
        "a connection string": b"postgresql://app_runtime",
        "the administrative role": b"postgresql://postgres:postgres" if env.provider == "supabase" else f"postgresql://app:{secrets['admin password']}".encode(),
    }
    leaks = [(str(path.relative_to(env.root)), name) for path in _walk(env.root / ".next") for name, needle in needles.items() if needle in path.read_bytes()]
    assert leaks == [], leaks
    static = b"".join(path.read_bytes() for path in _walk(env.root / ".next/static") if path.suffix in (".js", ".css", ".html", ".json"))
    for word in (b"BETTER_AUTH_SECRET", b"DATABASE_RUNTIME_URL", b"DATABASE_URL", b"service_role", b"app_runtime", b"-----BEGIN"):
        assert word not in static, word


def test_a_client_page_that_uses_the_auth_client_ships_no_server_code_or_secret(env: Env) -> None:
    page = env.root / "src/app/auth-demo"
    page.mkdir()
    (page / "page.tsx").write_text('"use client";\nimport { authClient } from "@/lib/auth-client";\nexport default function Page() {\n  const session = authClient.useSession();\n  return <p>{session.data?.user.email ?? "signed out"}</p>;\n}\n')
    try:
        built = env.npm("build", env=env.server_env())
        assert built.returncode == 0, plain(built.stdout)[-2000:] + plain(built.stderr)[-2000:]
        static = [path for path in _walk(env.root / ".next/static") if path.suffix == ".js"]
        text = b"".join(path.read_bytes() for path in static)
        assert b"/api/auth" in text or b"better-auth" in text or len(text) > 50_000  # the client really is in the bundle
        for name, value in env.secrets().items():
            assert value.encode() not in text, name
        # (the library names the variable it may read in a server process; what matters is that no value, no
        # connection string and none of this application's server-side names are in what the browser gets)
        for word in (b"DATABASE_RUNTIME_URL", b"DATABASE_URL", b"postgresql://", b"service_role", b"app_runtime", b"set_config"):
            assert word not in text, word
    finally:
        (page / "page.tsx").unlink()
        page.rmdir()
        rebuilt = env.npm("build", env=env.server_env())
        assert rebuilt.returncode == 0


def test_importing_the_server_modules_from_a_client_component_breaks_the_build(env: Env) -> None:
    page = env.root / "src/app/leak-demo"
    page.mkdir()
    (page / "page.tsx").write_text('"use client";\nimport { getAuth } from "@/server/auth";\nexport default function Page() {\n  return <p>{typeof getAuth}</p>;\n}\n')
    try:
        built = env.npm("build", env=env.server_env())
        assert built.returncode != 0 and "server-only" in plain(built.stdout + built.stderr)
    finally:
        (page / "page.tsx").unlink()
        page.rmdir()
        assert env.npm("build", env=env.server_env()).returncode == 0


# ------------------------------------------------------------------ the production server


ROUTES_NEEDING_A_SESSION = [
    ("GET", "/api/v1/items", None), ("POST", "/api/v1/items", {"title": "no"}), ("GET", "/api/v1/items/00000000-0000-4000-8000-000000000000", None),
    ("PATCH", "/api/v1/items/00000000-0000-4000-8000-000000000000", {"title": "no"}), ("DELETE", "/api/v1/items/00000000-0000-4000-8000-000000000000", None),
    ("GET", "/api/v1/session", None), ("GET", "/api/v1/sessions", None),
]  # fmt: skip


def test_without_a_session_every_protected_route_answers_the_stable_401(main: Server, env: Env) -> None:
    client = main.client("x-real-ip")
    for method, path, body in ROUTES_NEEDING_A_SESSION:
        reply = client.request(method, path, body)
        assert (reply.status, reply.code) == (401, "unauthenticated"), (method, path)
        assert reply.header("content-type") == "application/problem+json; charset=utf-8"
    assert client.get("/api/v1/app-info").status == 200  # the public example is unchanged
    assert client.post("/api/v1/examples/validate", {"title": "still public"}).status == 200
    assert env.psql("select count(*) from app.items where title = 'no'") == "0"


def test_registration_session_cookie_and_hashing_over_http(main: Server, env: Env) -> None:
    client = main.client("x-real-ip")
    reply = client.post("/api/auth/sign-up/email", {"email": "cookie@example.test", "password": "a very unusual passphrase 7261", "name": "Cookie"})
    assert reply.status == 200
    cookie = next(line for line in reply.set_cookies() if "session_token" in line)
    assert cookie.startswith("better-auth.session_token=") and re.search(r";\s*HttpOnly", cookie, re.I) and re.search(r";\s*SameSite=Lax", cookie, re.I)
    assert re.search(r";\s*Path=/", cookie, re.I) and re.search(r"Max-Age=604800", cookie, re.I) and not re.search(r";\s*Secure", cookie, re.I)  # plain http on loopback
    session = client.get("/api/v1/session").json()
    assert session["user"]["email"] == "cookie@example.test" and set(session) == {"user", "session"}
    token = env.psql("select token from app_auth.session limit 1")
    assert token and token not in client.transcript[-1]
    stored = env.psql("select password from app_auth.account where \"accountId\" = (select id from app_auth.\"user\" where email = 'cookie@example.test')")
    assert "unusual" not in stored and re.fullmatch(r"[0-9a-f]+:[0-9a-f]+", stored)  # salt:hash
    assert env.psql("select count(*) from app_auth.\"user\" where email = 'cookie@example.test'") == "1"
    bad = main.client("x-real-ip").post("/api/auth/sign-in/email", {"email": "cookie@example.test", "password": "wrong wrong wrong wrong"})
    unknown = main.client("x-real-ip").post("/api/auth/sign-in/email", {"email": "nobody@example.test", "password": "wrong wrong wrong wrong"})
    assert (bad.status, unknown.status) == (401, 401) and bad.text == unknown.text


def test_items_belong_to_their_owner_over_http(main: Server, env: Env) -> None:
    alice, bob = main.client("x-real-ip"), main.client("x-real-ip")
    sign_up(alice, "alice")
    sign_up(bob, "bob")
    created = alice.post("/api/v1/items", {"title": "Alice only", "body": "secret notes"})
    assert created.status == 201
    item = created.json()
    assert set(item) == {"id", "title", "body", "createdAt", "updatedAt"} and created.header("location") == f"/api/v1/items/{item['id']}"
    assert alice.get(f"/api/v1/items/{item['id']}").json() == item
    assert [row["id"] for row in alice.get("/api/v1/items").json()["items"]] == [item["id"]]
    assert bob.get("/api/v1/items").json()["items"] == []
    missing = bob.get("/api/v1/items/00000000-0000-4000-8000-000000000000")
    for reply in (bob.get(f"/api/v1/items/{item['id']}"), bob.request("PATCH", f"/api/v1/items/{item['id']}", {"title": "mine"}), bob.request("DELETE", f"/api/v1/items/{item['id']}")):
        assert (reply.status, reply.code, reply.text) == (404, "not_found", missing.text)  # indistinguishable from an item that does not exist
    assert env.psql(f"select title from app.items where id = '{item['id']}'") == "Alice only"
    patched = alice.request("PATCH", f"/api/v1/items/{item['id']}", {"body": "changed"})
    assert patched.status == 200 and patched.json()["body"] == "changed"
    assert alice.post("/api/v1/items", {"title": "alice only"}).code == "validation_failed"  # a duplicate among her own
    assert bob.post("/api/v1/items", {"title": "alice only"}).status == 201  # but not a conflict with someone else's
    assert alice.request("DELETE", f"/api/v1/items/{item['id']}").status == 204
    assert alice.get(f"/api/v1/items/{item['id']}").status == 404


def test_validation_and_injection_attempts_over_http(main: Server, env: Env) -> None:
    client = main.client("x-real-ip")
    sign_up(client, "probe")
    for body in ({}, {"title": ""}, {"title": "ok", "owner_id": "someone"}, {"title": "ok", "id": "x"}, {"title": "x" * 101}):
        assert client.post("/api/v1/items", body).code == "validation_failed", body
    assert client.post("/api/v1/items", raw=b"{oops", headers={"content-type": "application/json"}).code == "invalid_json"
    assert client.post("/api/v1/items", raw=b"{}", headers={"content-type": "text/plain"}).code == "unsupported_media_type"
    assert client.post("/api/v1/items", raw=json.dumps({"title": "x", "body": "y" * 9000}).encode(), headers={"content-type": "application/json"}).code == "payload_too_large"
    for hostile in ("1 or 1=1", "'; drop table app.items; --", "../../etc/passwd", "%27%20or%20%271%27%3D%271"):
        assert client.get(f"/api/v1/items/{urllib.parse.quote(hostile, safe='%')}").status == 404
    for query in ("limit=1;drop table app.items", "limit=0", "offset=-1", "other=1"):
        assert client.get(f"/api/v1/items?{urllib.parse.quote(query, safe='=&;')}").code == "validation_failed", query
    stored = client.post("/api/v1/items", {"title": "x'); drop table app.items; --", "body": "' or '1'='1"})
    assert stored.status == 201 and client.get("/api/v1/items").json()["items"][0]["title"] == "x'); drop table app.items; --"
    assert env.psql("select to_regclass('app.items') is not null") == "t"


def test_origins_are_checked_for_every_state_changing_request(main: Server, env: Env) -> None:
    client = main.client("x-real-ip")
    sign_up(client, "origin")
    for origin in ("https://evil.example", "http://localhost:3001", "null"):
        assert client.post("/api/v1/items", {"title": f"via {origin}"}, origin=origin).code == "origin_not_allowed"
        assert client.post("/api/auth/sign-out", {}, origin=origin).status == 403
    assert client.post("/api/v1/items", {"title": "no origin header"}, origin=None).status == 201  # a non-browser client sends none
    assert client.get("/api/v1/session").status == 200  # the rejected sign-out did nothing
    assert env.psql("select count(*) from app.items where title like 'via %'") == "0"


def test_signing_out_ends_the_session_and_the_cookie_is_cleared(main: Server, env: Env) -> None:
    client = main.client("x-real-ip")
    sign_up(client, "leaver")
    stale = dict(client.jar)
    assert client.post("/api/auth/sign-out", {}).status == 200 and client.jar == {}
    ghost = main.client("x-real-ip")
    ghost.jar.update(stale)
    assert ghost.get("/api/v1/session").code == "unauthenticated"  # the old cookie is dead server side, not just cleared
    assert ghost.get("/api/v1/items").code == "unauthenticated"


def test_changing_the_password_ends_the_other_sessions_over_http(main: Server, env: Env) -> None:
    first, second = main.client("x-real-ip"), main.client("x-real-ip")
    email, password = sign_up(first, "changer")
    assert second.post("/api/auth/sign-in/email", {"email": email, "password": password}).status == 200
    assert second.get("/api/v1/session").status == 200
    assert len(first.get("/api/v1/sessions").json()["sessions"]) == 2
    changed = first.post("/api/auth/change-password", {"currentPassword": password, "newPassword": "another long passphrase 17", "revokeOtherSessions": True})
    assert changed.status == 200 and first.get("/api/v1/session").status == 200
    assert second.get("/api/v1/session").code == "unauthenticated"


def test_the_default_production_configuration_refuses_open_sign_up_and_shares_one_rate_limit(env: Env) -> None:
    with env.server("default") as server:
        client = server.client()
        refused = client.post("/api/auth/sign-up/email", {"email": "closed@example.test", "password": "a long local passphrase 42", "name": "Closed"})
        assert refused.status >= 400 and refused.status != 500
        assert env.psql("select count(*) from app_auth.\"user\" where email = 'closed@example.test'") == "0"
        statuses = []
        for attempt in range(7):  # the client address is not trusted, so a spoofed header gives no fresh allowance
            reply = client.post("/api/auth/sign-in/email", {"email": "limit@example.test", "password": "wrong wrong wrong wrong"}, headers={"x-forwarded-for": f"203.0.113.{attempt + 1}", "x-real-ip": f"203.0.113.{attempt + 50}"})
            statuses.append(reply.status)
        assert 429 in statuses and statuses.count(401) <= 3, statuses


def test_a_misconfiguration_fails_closed_without_leaking_anything(env: Env) -> None:
    secrets = list(env.secrets().values())
    cases = {
        "no secret": {"BETTER_AUTH_SECRET": ""},
        "a short secret": {"BETTER_AUTH_SECRET": "short"},
        "the administrator as the runtime role": {"DATABASE_RUNTIME_URL": env.admin_url},
        "a base URL with a path": {"BETTER_AUTH_URL": "http://127.0.0.1:1/path"},
    }
    for name, override in cases.items():
        with env.server(f"bad-{name.replace(' ', '-')}", **override) as server:
            client = server.client()
            assert client.get("/api/v1/app-info").status == 200, name  # the public API does not depend on authentication
            reply = client.post("/api/auth/sign-in/email", {"email": "x@example.test", "password": "a long local passphrase 42"})
            assert (reply.status, reply.code) == (500, "internal_error"), (name, reply.status)
            assert not re.search(r"BETTER_AUTH|DATABASE|app_runtime|owner|postgres", reply.text, re.I), name
            for value in secrets:
                assert value not in reply.text and value not in server.logs(), name


def test_sessions_and_data_survive_a_restart_of_the_server_and_of_the_database(env: Env) -> None:
    with env.server("restart-a", AUTH_ALLOW_SIGN_UP="true", AUTH_TRUSTED_IP_HEADER="x-real-ip") as server:
        client = server.client("x-real-ip")
        email, password = sign_up(client, "survivor")
        assert client.post("/api/v1/items", {"title": "Still here"}).status == 201
        jar = dict(client.jar)
    env.stop_database()
    env.start_database()
    with env.server("restart-b", AUTH_ALLOW_SIGN_UP="true", AUTH_TRUSTED_IP_HEADER="x-real-ip") as server:
        again = server.client("x-real-ip")
        again.jar.update(jar)
        assert again.get("/api/v1/session").json()["user"]["email"] == email  # the same cookie, a new process, a restarted database
        assert [row["title"] for row in again.get("/api/v1/items").json()["items"]] == ["Still here"]
        assert again.post("/api/auth/sign-in/email", {"email": email, "password": password}).status == 200


def test_no_secret_or_token_reached_a_log_or_a_response(main: Server, env: Env) -> None:
    tokens = [token for token in env.psql("select token from app_auth.session").splitlines() if token]
    assert tokens
    logs = main.logs() + "".join(path.read_text(errors="replace") for path in env.logs.glob("*.log"))
    for name, value in {**env.secrets(), "a user's password": "a long local passphrase 42"}.items():
        assert value not in logs, name
    for token in tokens:
        assert token not in logs


def test_the_scenario_has_the_same_outcome_on_both_providers(main: Server, env: Env) -> None:
    steps: list[tuple] = []

    def note(label: str, reply) -> None:
        steps.append((label, reply.status, reply.code, sorted(reply.json()) if reply.text.startswith("{") else None))

    anyone, alice, bob = main.client("x-real-ip"), main.client("x-real-ip"), main.client("x-real-ip")
    note("anonymous list", anyone.get("/api/v1/items"))
    sign_up(alice, "scenario-a")
    note("session", alice.get("/api/v1/session"))
    first = alice.post("/api/v1/items", {"title": "Scenario item"})
    note("create", first)
    note("list", alice.get("/api/v1/items"))
    sign_up(bob, "scenario-b")
    item = first.json()["id"]
    note("foreign read", bob.get(f"/api/v1/items/{item}"))
    note("foreign patch", bob.request("PATCH", f"/api/v1/items/{item}", {"title": "x"}))
    note("foreign delete", bob.request("DELETE", f"/api/v1/items/{item}"))
    note("duplicate", alice.post("/api/v1/items", {"title": "scenario item"}))
    note("untrusted origin", alice.post("/api/v1/items", {"title": "evil"}, origin="https://evil.example"))
    note("sign out", alice.post("/api/auth/sign-out", {}))
    note("after sign out", alice.get("/api/v1/session"))
    TRANSCRIPTS[env.provider] = steps
    assert [step[1] for step in steps] == [401, 200, 201, 200, 404, 404, 404, 422, 403, 200, 401]
    if len(TRANSCRIPTS) == 2:
        assert TRANSCRIPTS["postgres"] == TRANSCRIPTS["supabase"]  # the same behaviour on either provider
