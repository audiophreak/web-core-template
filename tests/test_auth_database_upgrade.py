"""Enabling auth on an existing project with existing data, against a real database, for both providers.

The project is generated from the real published v0.5.1 (database on, no auth), migrated and filled with rows,
then updated to this working tree with `auth=true`. The data must survive, must be invisible to the application
(rows from before authentication have no owner), and the database must end up with exactly the schema a fresh
database gets. Both providers must converge on the same schema. Needs Docker (marker `db`) and the published tag.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from conftest import render, run_git
from dbharness import install, plain, require_docker
from test_auth_database import Env
from test_auth_upgrade import PUBLISHED, _published_tag_available, build_history
from test_upgrade import _adopt, _answers, _update

pytestmark = pytest.mark.db

SNAPSHOT = {}  # provider -> the schema snapshot of a fresh database
SNAPSHOT_JS = """
import pg from "pg";
const client = new pg.Client({ connectionString: process.env.TARGET });
await client.connect();
await client.query("set search_path to pg_catalog"); // names print schema-qualified whatever the database's own search_path is
const q = async (sql) => (await client.query(sql)).rows;
const roles = (await q("select rolname from pg_roles where rolname in ('app_runtime','anon','authenticated','service_role') order by 1")).map((r) => r.rolname);
const tables = await q("select n.nspname as schema, c.relname as name from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname in ('app','app_auth') and c.relkind = 'r' order by 1, 2");
const privileges = {};
for (const role of roles) {
  for (const t of tables) {
    const row = (await q(`select ${["select","insert","update","delete","truncate","references","trigger"].map((p) => `has_table_privilege('${role}', '${t.schema}.\\"${t.name}\\"', '${p}') as ${p}`).join(", ")}`))[0];
    privileges[`${role}:${t.schema}.${t.name}`] = Object.entries(row).filter(([, v]) => v).map(([k]) => k).join(",");
  }
  privileges[`${role}:schema-app`] = (await q(`select has_schema_privilege('${role}', 'app', 'usage') as u, has_schema_privilege('${role}', 'app', 'create') as c`))[0];
  privileges[`${role}:schema-app_auth`] = (await q(`select has_schema_privilege('${role}', 'app_auth', 'usage') as u, has_schema_privilege('${role}', 'app_auth', 'create') as c`))[0];
}
console.log(JSON.stringify({
  columns: await q("select table_schema, table_name, column_name, ordinal_position, data_type, is_nullable, column_default from information_schema.columns where table_schema in ('app','app_auth') order by 1, 2, ordinal_position"),
  indexes: await q("select schemaname, indexname, indexdef from pg_indexes where schemaname in ('app','app_auth') order by 1, 2"),
  constraints: await q("select connamespace::regnamespace::text as schema, conrelid::regclass::text as tbl, conname, contype, pg_get_constraintdef(oid) as def from pg_constraint where connamespace in ('app'::regnamespace, 'app_auth'::regnamespace) order by 1, 2, 3"),
  rls: await q("select n.nspname as schema, c.relname as name, c.relrowsecurity, c.relforcerowsecurity from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname in ('app','app_auth') and c.relkind = 'r' order by 1, 2"),
  policies: await q("select schemaname, tablename, policyname, cmd, roles::text as roles, qual, with_check from pg_policies where schemaname in ('app','app_auth') order by 1, 2, 3"),
  functions: await q("select n.nspname as schema, p.proname as name, pg_get_functiondef(p.oid) as def from pg_proc p join pg_namespace n on n.oid = p.pronamespace where n.nspname in ('app','app_auth') order by 1, 2"),
  role: await q("select rolsuper, rolcreaterole, rolcreatedb, rolreplication, rolbypassrls from pg_roles where rolname = 'app_runtime'"),
  privileges,
}));
await client.end();
"""
MIGRATE_JS = """
import { migrate } from "./scripts/db/migrations.mjs";
const result = await migrate({ connectionString: process.env.TARGET });
console.log(JSON.stringify(result.applied));
"""
IMPERSONATE_JS = """
import pg from "pg";
const client = new pg.Client({ connectionString: process.env.TARGET });
await client.connect();
const out = {};
out.without = (await client.query("select count(*)::int as n from app.items")).rows[0].n;
await client.query("begin");
await client.query("select set_config('app.user_id', $1, true)", [process.env.USER_ID]);
out.with = (await client.query("select title from app.items order by title")).rows.map((r) => r.title);
await client.query("rollback");
console.log(JSON.stringify(out));
await client.end();
"""


class UpgradeEnv(Env):
    """An Env whose project may be updated: the Supabase config edit is undone and redone around the update."""

    def restore_config(self) -> None:
        if self.original_config is not None:
            (self.root / "supabase/config.toml").write_text(self.original_config)

    def reapply_config(self) -> None:
        if self.original_config is not None:
            config = re.sub(r'project_id = "[^"]+"', f'project_id = "{self.project_id}"', self.original_config)
            config = config.replace("port = 54321", f"port = {self.api_port}").replace("port = 54322", f"port = {self.db_port}")
            (self.root / "supabase/config.toml").write_text(config)


@pytest.fixture(scope="module", params=["postgres", "supabase"])
def upgraded(request, tmp_path_factory: pytest.TempPathFactory):
    require_docker()
    if not _published_tag_available():
        pytest.skip(f"the published tag {PUBLISHED} is not in this checkout (shallow clone)")
    provider = request.param
    base = tmp_path_factory.mktemp(f"upgrade-{provider}")
    (base / "history").mkdir()
    repo, remote = build_history(base / "history")
    project = base / f"{provider}-upgrade-app"
    render(str(remote), project, ref=PUBLISHED, app_name=f"{provider}-upgrade-app", zod=True, rest_api=True, database=provider)
    assert "auth" not in _answers(project)
    _adopt(project)
    install(project)  # the lockfile of the old release
    environment = UpgradeEnv(provider, project)
    try:
        environment.start()  # the old tooling: init, up, migrate
        yield environment
    finally:
        environment.destroy()


def _comparable(snapshot: dict) -> dict:
    """What both providers share: Supabase's API roles exist only there (and are asserted above to hold nothing), and
    the two images sort names differently (their collations differ), so rows are compared without their order. Each
    column keeps its position, so a reordered table still shows."""
    canonical = {key: sorted(value, key=lambda row: json.dumps(row, sort_keys=True)) if isinstance(value, list) else value for key, value in snapshot.items()}
    return {**canonical, "privileges": {key: value for key, value in snapshot["privileges"].items() if key.startswith("app_runtime:")}}


def _snapshot(env: Env, url: str) -> dict:
    result = env.node(SNAPSHOT_JS, {"TARGET": url})
    assert result.returncode == 0, result.stderr[-1000:]
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_a_project_with_data_is_upgraded_and_enabling_auth_keeps_the_data_invisible_until_assigned(upgraded: Env) -> None:
    env = upgraded
    env.psql("insert into app.items (title, body) values ('legacy one', 'from before auth'), ('legacy two', '')")
    assert env.psql("select count(*) from app.items") == "2"
    before = env.file_env()
    assert "DATABASE_RUNTIME_URL" not in before and not (env.root / "src/server/auth").exists()
    env.restore_config()
    if env.provider == "supabase":
        # The documented step for a project from before 0.6.0: the CLI's local state is untracked and an update
        # needs a clean working tree, so the user ignores it (templates from 0.6.0 do) and commits that first.
        with (env.root / ".gitignore").open("a") as handle:
            handle.write("\n# mine\nsupabase/.temp\nsupabase/.branches\n")
        run_git(env.root, "add", "-A")
        run_git(env.root, "commit", "-q", "-m", "chore: ignore the Supabase CLI's local state")

    result = _update(env.root, "--vcs-ref", "v0.6.0", "--data", "auth=true")

    assert result.returncode == 0, result.stderr
    assert _answers(env.root)["auth"] is True and (env.root / "src/server/auth/auth.ts").is_file()
    env.reapply_config()
    install(env.root)  # the lockfile now includes Better Auth
    init = env.npm("db:init")
    assert init.returncode == 0 and "existing values were not changed" in init.stdout
    after = env.file_env()
    assert {key: value for key, value in after.items() if key in before} == before  # every earlier setting is exactly as it was
    assert {"DATABASE_RUNTIME_URL", "BETTER_AUTH_SECRET", "BETTER_AUTH_URL"} <= set(after)
    migrated = env.npm("db:migrate")
    assert migrated.returncode == 0 and "runtime role app_runtime is ready" in migrated.stdout, plain(migrated.stdout)[-800:]

    assert env.psql("select count(*) from app.items") == "2"  # nothing was lost
    assert env.psql("select count(*) from app.items where owner_id is null") == "2"
    assert env.psql("select string_agg(title, ',' order by title) from app.items") == "legacy one,legacy two"
    env.psql("insert into app_auth.\"user\" (id, name, email, \"emailVerified\", \"updatedAt\") values ('legacy-owner', 'Owner', 'owner@example.test', false, now())")
    login = env.node(IMPERSONATE_JS, {"TARGET": env.runtime_url, "USER_ID": "legacy-owner"})
    assert json.loads(login.stdout.strip().splitlines()[-1]) == {"without": 0, "with": []}  # ownerless rows are hidden from everyone
    env.psql("update app.items set owner_id = 'legacy-owner' where title = 'legacy one'")
    login = env.node(IMPERSONATE_JS, {"TARGET": env.runtime_url, "USER_ID": "legacy-owner"})
    assert json.loads(login.stdout.strip().splitlines()[-1]) == {"without": 0, "with": ["legacy one"]}  # assigned by an administrator, then visible to its owner only
    other = env.node(IMPERSONATE_JS, {"TARGET": env.runtime_url, "USER_ID": "someone-else"})
    assert json.loads(other.stdout.strip().splitlines()[-1])["with"] == []


def test_the_upgraded_database_has_exactly_the_schema_of_a_fresh_one_and_both_providers_agree(upgraded: Env) -> None:
    env = upgraded
    assert env.psql("select count(*) from pg_roles where rolname = 'app_runtime'") == "1"
    fresh = f"fresh_{env.project_id}"
    assert "CREATE DATABASE" in env.psql(f"create database {fresh}")
    url = re.sub(r"/[^/]+$", f"/{fresh}", env.admin_url)
    created = env.node(MIGRATE_JS, {"TARGET": url})  # the project's own runner, on a database that never saw the old release
    assert created.returncode == 0, created.stderr[-800:]
    assert len(json.loads(created.stdout.strip().splitlines()[-1])) == 3  # items, auth and the ownership migration
    upgraded_snapshot, fresh_snapshot = _snapshot(env, env.admin_url), _snapshot(env, url)
    assert upgraded_snapshot == fresh_snapshot
    assert [table["table_name"] for table in upgraded_snapshot["columns"] if table["table_schema"] == "app_auth"] and upgraded_snapshot["role"] == [
        {"rolsuper": False, "rolcreaterole": False, "rolcreatedb": False, "rolreplication": False, "rolbypassrls": False}
    ]
    assert upgraded_snapshot["privileges"]["app_runtime:app.items"] == "select,delete"  # INSERT and UPDATE are granted per column
    assert upgraded_snapshot["privileges"].get("anon:app.items", "") == "" and upgraded_snapshot["privileges"].get("service_role:app_auth.user", "") == ""
    env.psql(f"drop database {fresh}")
    SNAPSHOT[env.provider] = _comparable(fresh_snapshot)
    if len(SNAPSHOT) == 2:
        assert SNAPSHOT["postgres"] == SNAPSHOT["supabase"], "the two providers must converge on the same application and authentication schema"


def test_the_upgraded_project_passes_its_own_checks_and_database_tests(upgraded: Env) -> None:
    env = upgraded
    check = subprocess.run(["npm", "run", "check"], cwd=env.root, capture_output=True, text=True, timeout=900, check=False)
    assert check.returncode == 0, plain(check.stdout)[-2500:] + plain(check.stderr)[-1500:]
    tests = env.npm("test:db")
    output = plain(tests.stdout + tests.stderr)
    assert tests.returncode == 0, output[-3000:]
    assert re.search(r"Tests\s+(\d+) passed \(\1\)", output) and "failed" not in output.lower().split("tests")[-1]
