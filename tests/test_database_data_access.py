"""Gate B: the typed, server-only data-access layer, proven against a real database.

The generated application ships its own real-database tests (`npm run test:db`: CRUD, missing records,
invalid input, SQL injection, duplicates and constraints, transactions, timeouts, connection failures).
These tests run that suite against a real PostgreSQL container, and prove the build-time and bundle
guarantees that unit tests cannot: a client component cannot import the data layer, and no credential
reaches client output.
"""

from __future__ import annotations

import contextlib
import re
import uuid
from pathlib import Path

import pytest

from conftest import render
from dbharness import LocalApp, install, plain, require_docker, run
from test_database_postgres import app, installed, running  # noqa: F401  (fixtures shared with Gate A)

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def installed_full(template_repo: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Postgres with every other feature: the REST API helpers must compose with the repository."""
    require_docker()
    project = tmp_path_factory.mktemp("pg-full") / "pg-full"
    render(template_repo, project, app_name="pg-full", zod=True, forms=True, rest_api=True, database="postgres")
    install(project)
    return project


def _summary(output: str) -> tuple[int, int]:
    output = plain(output)
    passed = re.search(r"Tests\s+(?:\d+ failed \| )?(\d+) passed", output)
    failed = re.search(r"Tests\s+(\d+) failed", output)
    return int(passed.group(1)) if passed else 0, int(failed.group(1)) if failed else 0


def _test_databases(local: LocalApp) -> str:
    return local.psql("select count(*) from pg_database where datname like 'app_test_%'")


def test_the_generated_real_database_tests_pass_and_leave_no_database_behind(running: LocalApp) -> None:  # noqa: F811
    result = running.npm("test:db", timeout=900)
    passed, failed = _summary(result.stdout + result.stderr)
    assert result.returncode == 0 and failed == 0 and passed >= 50, (result.stdout + result.stderr)[-3000:]
    assert _test_databases(running) == "0"  # every disposable database was dropped
    assert running.psql("select to_regclass('app.items') is null") == "t"  # the application's own database was never touched


def test_the_suite_also_passes_with_every_other_feature_enabled(installed_full: Path, tmp_path: Path) -> None:
    local = LocalApp(installed_full, tmp_path / "work")
    try:
        local.start()
        result = local.npm("test:db", timeout=900)
        passed, failed = _summary(result.stdout + result.stderr)
        assert result.returncode == 0 and failed == 0, (result.stdout + result.stderr)[-3000:]
        assert "Test Files  4 passed (4)" in plain(result.stdout + result.stderr) and passed >= 55  # the repository-handler composition test is the fourth file
        assert _test_databases(local) == "0"
    finally:
        local.destroy()


def test_the_database_tests_refuse_a_non_local_database(installed: Path, tmp_path: Path) -> None:
    local = LocalApp(installed, tmp_path / "work")
    result = run(["npm", "run", "--silent", "test:db"], local.root, {"DATABASE_URL": "postgresql://u:s3cr3t@db.example.com:5432/x"})
    assert result.returncode != 0 and "refusing to migrate a non-local database" in result.stdout + result.stderr
    assert "s3cr3t" not in result.stdout + result.stderr


def test_without_a_database_the_real_database_tests_fail_with_instructions_not_a_silent_pass(installed: Path, tmp_path: Path) -> None:
    local = LocalApp(installed, tmp_path / "work")
    result = run(["npm", "run", "--silent", "test:db"], local.root, {"DATABASE_URL": ""})
    assert result.returncode != 0 and "DATABASE_URL is not set" in result.stdout + result.stderr


# ------------------------------------------------------------------ build-time server-only enforcement


CLIENT = """"use client";

import { listItems } from "@/server/items/repository";

export function Client() {
  void listItems;
  return <p>client</p>;
}
"""
CLIENT_PAGE = """import { Client } from "./client";

export default function Page() {
  return <Client />;
}
"""
SERVER_PAGE = """import { Suspense } from "react";
import { connection } from "next/server";
import { listItems } from "@/server/items/repository";

async function ItemCount() {
  await connection();
  return <p>{(await listItems({ limit: 1 })).length}</p>;
}

export default function Page() {
  return (
    <Suspense fallback={<p>Loading</p>}>
      <ItemCount />
    </Suspense>
  );
}
"""


@contextlib.contextmanager
def _demo_page(project: Path, **files: str):
    """Adds src/app/items-demo for one build and removes exactly the files it added, even on failure."""
    directory = project / "src/app/items-demo"
    directory.mkdir()
    try:
        for name, text in files.items():
            (directory / name).write_text(text)
        yield
    finally:
        for name in files:
            (directory / name).unlink(missing_ok=True)
        directory.rmdir()


@pytest.mark.e2e
def test_a_client_component_importing_the_data_layer_fails_the_production_build(installed: Path) -> None:
    with _demo_page(installed, **{"client.tsx": CLIENT, "page.tsx": CLIENT_PAGE}):
        build = run(["npm", "run", "build"], installed)
        output = build.stdout + build.stderr
        assert build.returncode != 0
        assert 'depends on "server-only"' in output and "Client Component" in output
        # the import-graph unit test is the second, independent layer
        graph = run(["npx", "--no-install", "vitest", "run", "src/server/db/server-only.test.ts"], installed)
        assert graph.returncode != 0 and "src/app/items-demo/client.tsx" in graph.stdout + graph.stderr


@pytest.mark.e2e
def test_a_server_component_can_use_the_data_layer_and_no_credential_reaches_client_output(installed: Path) -> None:
    sentinel = f"s3ntinel-{uuid.uuid4().hex}"
    with _demo_page(installed, **{"page.tsx": SERVER_PAGE}):
        build = run(["npm", "run", "build"], installed, {"DATABASE_URL": f"postgresql://app:{sentinel}@127.0.0.1:5432/app"})
        assert build.returncode == 0, (build.stdout + build.stderr)[-2000:]
        static = installed / ".next" / "static"
        assert [p for p in static.rglob("*") if p.is_file() and sentinel.encode() in p.read_bytes()] == []
        client_code = b"\n".join(p.read_bytes() for p in static.rglob("*.js"))
        for needle in (b"postgres://", b"postgresql://", b"pg-pool", b"pg-protocol", b"DATABASE_URL", b"statement_timeout"):
            assert needle not in client_code, needle
        assert [p for p in (installed / ".next").rglob("*") if p.is_file() and sentinel.encode() in p.read_bytes()] == []  # not even the server output


def test_no_shipped_route_reaches_the_database_before_authentication_exists(installed_full: Path) -> None:
    for route in (installed_full / "src/app").rglob("route.ts"):
        text = route.read_text()
        assert "@/server/db" not in text and "@/server/items" not in text and "pg" not in re.findall(r'from "([^"]+)"', text), route
    assert not [p for p in (installed_full / "src/app").rglob("*") if p.is_file() and "items" in p.name]  # no items endpoint
