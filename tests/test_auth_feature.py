"""The optional auth feature: what a project gets with it, what it needs, and what it must not get without it."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import features as f
from conftest import REGISTRY, TEMPLATE_ROOT, render
from test_answer_types import _copier

DATABASE_LOCK = "database-postgres+zod"
EXPECTED_NEW_PACKAGES = {
    "@better-auth/core", "@better-auth/drizzle-adapter", "@better-auth/kysely-adapter", "@better-auth/memory-adapter",
    "@better-auth/mongo-adapter", "@better-auth/prisma-adapter", "@better-auth/telemetry", "@better-auth/utils",
    "@better-fetch/fetch", "@noble/ciphers", "@noble/hashes", "@standard-schema/spec", "better-auth", "better-call",
    "defu", "jose", "kysely", "nanostores", "rou3", "set-cookie-parser",
}  # fmt: skip
AUTH_ONLY_FILES = {
    ".npmrc", "docs/auth.md", "scripts/db/roles.mjs", "src/lib/auth-client.ts", "src/lib/schemas/v1/auth-contract.test.ts",
    "src/lib/schemas/v1/items.ts", "src/lib/schemas/v1/session.ts", "src/server/items/http.ts",
    "src/app/api/auth/[...all]/route.ts", "src/app/api/v1/items/route.ts", "src/app/api/v1/items/route.db.test.ts",
    "src/app/api/v1/items/[id]/route.ts", "src/app/api/v1/session/route.ts", "src/app/api/v1/sessions/route.ts",
    "src/server/auth/auth.db.test.ts", "src/server/auth/auth.ts", "src/server/auth/authorization.db.test.ts",
    "src/server/auth/config.test.ts", "src/server/auth/config.ts", "src/server/auth/errors.ts", "src/server/auth/guard.test.ts",
    "src/server/auth/guard.ts", "src/server/auth/handler.ts", "src/server/auth/index.ts", "src/server/auth/principal.ts",
    "src/server/auth/runtime-role.ts", "src/server/auth/schema.db.test.ts", "src/server/auth/scope.ts",
    "src/server/auth/server-only.test.ts", "src/server/auth/test-support/harness.ts",
    "supabase/migrations/20260301000000_create_auth.sql", "supabase/migrations/20260301000100_scope_items_to_owner.sql",
    "supabase/migrations.lock/20260301000000_create_auth.sha256", "supabase/migrations.lock/20260301000100_scope_items_to_owner.sha256",
}  # fmt: skip
PROVIDER_ONLY = {"postgres": {"compose.yaml"}, "supabase": {"supabase/config.toml"}}
SHARED_THAT_CHANGE = {
    ".copier-answers.yml", ".gitignore", "AGENTS.md", "README.md", "package-lock.json", "package.json",
    "scripts/db/cli.mjs", ".env.example", "docs/database.md", "docs/api-v1.md", "src/lib/schemas/index.ts",
    "src/lib/schemas/v1/index.ts", "src/lib/schemas/v1/problem.ts", "src/lib/schemas/v1/contract.test.ts",
    "src/server/api/responses.ts", "src/server/api/responses.test.ts", "src/server/db/config.ts", "src/server/db/config.test.ts",
    "src/server/db/connection.db.test.ts", "src/server/db/pool.ts", "src/server/db/server-only.test.ts",
    "src/server/items/repository.ts", "src/server/items/repository.db.test.ts", "src/server/api/repository-handler.db.test.ts",
    "supabase/config.toml", "compose.yaml",
}  # fmt: skip


@pytest.fixture(scope="module")
def apps(template_repo: Path, tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("auth")
    chosen = {
        "pg": {"zod": True, "rest_api": True, "database": "postgres"},
        "sb": {"zod": True, "rest_api": True, "database": "supabase"},
        "pg+auth": {"zod": True, "rest_api": True, "database": "postgres", "auth": True},
        "sb+auth": {"zod": True, "rest_api": True, "database": "supabase", "auth": True},
        "pg+forms+auth": {"zod": True, "forms": True, "rest_api": True, "database": "postgres", "auth": True},
        "sb+forms+auth": {"zod": True, "forms": True, "rest_api": True, "database": "supabase", "auth": True},
        "pg+nocache+auth": {"zod": True, "rest_api": True, "database": "postgres", "auth": True, "cache_components": False},
    }
    for label, answers in chosen.items():
        render(template_repo, root / label, app_name="auth-app", **answers)
    return {label: root / label for label in chosen}


def _files(project: Path) -> dict[str, bytes]:
    return {str(p.relative_to(project)): p.read_bytes() for p in sorted(project.rglob("*")) if p.is_file()}


def _package(project: Path) -> dict:
    return json.loads((project / "package.json").read_text())


def _lock(name: str) -> dict:
    return json.loads((TEMPLATE_ROOT / "locks" / f"{name}.json").read_text().replace("{{ app_name | to_json }}", '"x"'))["packages"]


# ------------------------------------------------------------------ the registry and the composition rules


def test_auth_is_a_registered_boolean_feature_with_its_prerequisites() -> None:
    feature = REGISTRY["auth"]
    assert (feature.choices, feature.requires, feature.conflicts, feature.npm) == ((), ("zod", "rest_api", "database"), (), True)


def test_the_valid_auth_configurations_are_exactly_the_four_that_have_all_prerequisites() -> None:
    selections = [s for s in f.configurations(REGISTRY) if "auth" in s]
    assert sorted(sorted(s) for s in selections) == sorted(
        sorted({"auth", "zod", "rest_api", f"database={provider}", *(["forms"] if forms else [])})
        for provider in ("postgres", "supabase")
        for forms in (False, True)
    )
    assert {f.lock_key(s, REGISTRY) for s in selections} == {
        "auth+database-postgres+zod", "auth+database-supabase+zod", "auth+database-postgres+forms+zod", "auth+database-supabase+forms+zod",
    }  # fmt: skip


def test_the_lock_matrix_stays_within_the_cap_and_every_lock_has_its_file() -> None:
    keys = f.lock_keys(REGISTRY)
    assert len(keys) == 11 <= 16
    assert sorted(path.stem for path in (TEMPLATE_ROOT / "locks").glob("*.json")) == sorted(keys)


@pytest.mark.parametrize(
    ("answers", "message"),
    [
        ({"auth": True}, "auth requires zod"),
        ({"zod": True, "auth": True}, "auth requires rest_api"),
        ({"zod": True, "database": "postgres", "auth": True}, "auth requires rest_api"),
        ({"zod": True, "rest_api": True, "auth": True}, "auth requires a database"),
        ({"zod": True, "rest_api": True, "database": "none", "auth": True}, "auth requires a database"),
        ({"zod": True, "forms": True, "auth": True}, "auth requires rest_api"),
        ({"rest_api": True, "database": "supabase", "auth": True}, "requires zod"),
    ],
    ids=str,
)
def test_missing_prerequisites_are_rejected_before_anything_is_written(template_repo: Path, tmp_path: Path, answers: dict, message: str) -> None:
    with pytest.raises(Exception, match=message):
        render(template_repo, tmp_path / "no", **answers)
    assert not (tmp_path / "no").exists()


@pytest.mark.parametrize("given", ["banana", "1", "0", "maybe", "tRuE", "", None, 2])
def test_auth_is_a_strict_boolean(template_repo: Path, tmp_path: Path, given: object) -> None:
    with pytest.raises(Exception, match="auth"):
        render(template_repo, tmp_path / "no", zod=True, rest_api=True, database="postgres", auth=given)
    assert not (tmp_path / "no").exists()


def test_the_real_copier_command_line_rejects_auth_without_its_prerequisites_and_accepts_it_with_them(template_repo: Path, tmp_path: Path) -> None:
    rejected = _copier(template_repo, tmp_path / "no", "zod=true", "rest_api=true", "auth=true")
    assert rejected.returncode != 0 and "auth requires a database" in rejected.stderr and not (tmp_path / "no").exists()
    accepted = _copier(template_repo, tmp_path / "yes", "zod=true", "rest_api=true", "database=supabase", "auth=true")
    assert accepted.returncode == 0, accepted.stderr
    assert yaml.safe_load((tmp_path / "yes/.copier-answers.yml").read_text())["auth"] is True


def test_nothing_enables_a_prerequisite_for_you(template_repo: Path, tmp_path: Path) -> None:
    render(template_repo, tmp_path / "ok", zod=True, rest_api=True, database="postgres", auth=True)
    answers = yaml.safe_load((tmp_path / "ok/.copier-answers.yml").read_text())
    assert (answers["forms"], answers["cache_components"]) == (False, True)  # forms is not required and stays off


# ------------------------------------------------------------------ disabled: the baseline


@pytest.mark.parametrize("label", ["pg", "sb"])
def test_without_auth_nothing_auth_related_is_generated(apps, label: str) -> None:
    project = apps[label]
    package = _package(project)
    assert "better-auth" not in {*package["dependencies"], *package["devDependencies"]} and "db:roles" not in package["scripts"]
    for path in AUTH_ONLY_FILES:
        assert not (project / path).exists(), path
    for path in ("src/server/auth", "src/app/api/auth", "src/app/api/v1/items", "src/app/api/v1/session"):
        assert not (project / path).exists(), path
    assert "unauthenticated" not in (project / "src/lib/schemas/v1/problem.ts").read_text()
    assert "origin_not_allowed" not in (project / "src/server/api/responses.ts").read_text()
    for relative in ("README.md", "AGENTS.md", "docs/database.md", "docs/api-v1.md", ".env.example", "scripts/db/cli.mjs"):
        text = (project / relative).read_text()
        assert "BETTER_AUTH" not in text and "app_runtime" not in text and "docs/auth.md" not in text and "withPrincipal" not in text, relative
    assert yaml.safe_load((project / ".copier-answers.yml").read_text())["auth"] is False
    assert "DATABASE_URL" in (project / "src/server/db/config.ts").read_text() and "RUNTIME" not in (project / "src/server/db/config.ts").read_text()
    assert "search_path" not in (project / "src/server/db/pool.ts").read_text()


# ------------------------------------------------------------------ enabled: what is generated


@pytest.mark.parametrize("label", ["pg+auth", "sb+auth", "pg+forms+auth", "sb+forms+auth", "pg+nocache+auth"])
def test_every_auth_file_exists_and_nothing_extra_is_added(apps, label: str) -> None:
    project, files = apps[label], _files(apps[label])
    assert AUTH_ONLY_FILES <= set(files)
    assert "src/server/api/repository-handler.db.test.ts" not in files  # the unprotected example is replaced by the real routes
    baseline = _files(apps[label.split("+")[0]]) if "forms" not in label and "nocache" not in label else None
    if baseline is not None:
        added = set(files) - set(baseline)
        assert added == AUTH_ONLY_FILES, sorted(added ^ AUTH_ONLY_FILES)
        changed = {n for n in set(files) & set(baseline) if files[n] != baseline[n]}
        assert changed <= SHARED_THAT_CHANGE, sorted(changed - SHARED_THAT_CHANGE)
    assert [p for p in project.rglob("*") if p.is_dir() and not any(p.iterdir())] == []


def test_the_application_layer_is_identical_for_both_providers(apps) -> None:
    pg, sb = _files(apps["pg+auth"]), _files(apps["sb+auth"])
    assert set(pg) - set(sb) == PROVIDER_ONLY["postgres"] and set(sb) - set(pg) == PROVIDER_ONLY["supabase"]
    different = {n for n in set(pg) & set(sb) if pg[n] != sb[n]}
    assert different <= {".copier-answers.yml", ".gitignore", "package.json", "package-lock.json", "scripts/db/cli.mjs", ".env.example", "docs/database.md", "docs/auth.md", "README.md"}, sorted(different)
    for name in pg:
        if name.startswith(("src/", "supabase/migrations")):
            assert pg[name] == sb[name], name  # every line of application code and every migration is the same
    assert pg["scripts/db/roles.mjs"] == sb["scripts/db/roles.mjs"]


def test_auth_is_the_same_with_forms_on_and_forms_adds_nothing_to_it(apps) -> None:
    plain, forms = _files(apps["pg+auth"]), _files(apps["pg+forms+auth"])
    for name in plain:
        if name.startswith(("src/server/auth", "src/app/api", "supabase/")):
            assert plain[name] == forms[name], name
    assert {n for n in forms if n not in plain} >= {"src/components/forms/example-form.tsx", "src/app/forms-example/page.tsx"}


def test_dependencies_are_exact_and_the_only_addition_is_better_auth(apps) -> None:
    database, auth = _package(apps["pg"]), _package(apps["pg+auth"])
    assert {k: v for k, v in auth["dependencies"].items() if k not in database["dependencies"]} == {"better-auth": "1.7.15"}
    assert auth["devDependencies"] == database["devDependencies"]
    assert {k: v for k, v in auth["scripts"].items() if k not in database["scripts"]} == {"db:roles": "node scripts/db/cli.mjs roles"}
    assert all(re.fullmatch(r"\d+\.\d+\.\d+", v) for v in (*auth["dependencies"].values(), *auth["devDependencies"].values()))
    assert _package(apps["sb+auth"])["devDependencies"] == {**database["devDependencies"], "supabase": "2.122.1"}


def test_the_npmrc_keeps_development_tools_out_of_the_production_audit(apps) -> None:
    assert (apps["pg+auth"] / ".npmrc").read_text().splitlines()[-1] == "legacy-peer-deps=true"
    lock = json.loads((apps["pg+auth"] / "package-lock.json").read_text())["packages"]
    for tool in ("vitest", "vite", "tinyglobby"):
        entry = lock[f"node_modules/{tool}"]
        assert entry.get("dev") is True and "devOptional" not in entry and "peer" not in entry, tool  # not reachable from production
    assert not (apps["pg"] / ".npmrc").exists()


def test_the_auth_lock_adds_exactly_the_reviewed_packages_with_no_install_script(apps) -> None:
    base, auth = _lock(DATABASE_LOCK), _lock("auth+database-postgres+zod")
    added = {path.rsplit("node_modules/", 1)[-1]: entry for path, entry in auth.items() if path and path not in base}
    assert set(added) == EXPECTED_NEW_PACKAGES
    assert {e["license"] for e in added.values()} == {"MIT"}
    assert not [name for name, entry in added.items() if entry.get("hasInstallScript") or entry.get("optional") or entry.get("peer")]
    assert added["better-auth"]["version"] == "1.7.15"
    for path, entry in base.items():
        if path:
            assert auth[path]["version"] == entry["version"], path  # no existing package moved or changed version
    for label in ("pg+auth", "sb+auth", "pg+forms+auth", "sb+forms+auth"):
        package, lock = _package(apps[label]), json.loads((apps[label] / "package-lock.json").read_text())["packages"]
        assert lock[""]["dependencies"] == package["dependencies"] and lock[""]["devDependencies"] == package["devDependencies"]
        assert lock["node_modules/better-auth"]["version"] == "1.7.15"


def test_the_runtime_url_and_the_administrative_url_are_different_settings(apps) -> None:
    config = (apps["pg+auth"] / "src/server/db/config.ts").read_text()
    assert "DATABASE_RUNTIME_URL" in config and "DATABASE_URL" not in config.replace("DATABASE_RUNTIME_URL", "")
    assert "search_path=app_auth" in (apps["pg+auth"] / "src/server/db/pool.ts").read_text()
    admin_readers = [n for n, b in _files(apps["pg+auth"]).items() if n.startswith("src/") and b"DATABASE_URL" in b and "/test-support/" not in n and ".test." not in n]
    assert admin_readers == [], admin_readers  # the running application never reads the administrative connection


def test_the_supabase_cli_state_is_ignored_for_that_provider_only(apps) -> None:
    """The CLI creates supabase/.temp and supabase/.branches when the stack first starts; untracked, they made every
    update refuse the project (an update needs a clean working tree)."""
    for label in ("sb", "sb+auth", "sb+forms+auth"):
        lines = (apps[label] / ".gitignore").read_text().splitlines()
        assert "supabase/.temp" in lines and "supabase/.branches" in lines, label
    for label in ("pg", "pg+auth"):
        assert "supabase/.temp" not in (apps[label] / ".gitignore").read_text(), label


# ------------------------------------------------------------------ the migrations


def test_the_migrations_continue_the_one_canonical_history_with_their_locks(apps) -> None:
    for label in ("pg+auth", "sb+auth"):
        names = sorted(p.name for p in (apps[label] / "supabase/migrations").glob("*.sql"))
        assert names == ["20260101000000_create_items.sql", "20260301000000_create_auth.sql", "20260301000100_scope_items_to_owner.sql"]
        for name in names:
            sql = (apps[label] / "supabase/migrations" / name).read_text()
            lock = (apps[label] / "supabase/migrations.lock" / name.replace(".sql", ".sha256")).read_text()
            assert lock == hashlib.sha256(sql.encode()).hexdigest() + "\n", name
    old = (apps["pg"] / "supabase/migrations/20260101000000_create_items.sql").read_bytes()
    assert (apps["pg+auth"] / "supabase/migrations/20260101000000_create_items.sql").read_bytes() == old  # history is never edited


def test_the_migrations_create_a_safe_role_and_deny_by_default() -> None:
    root = TEMPLATE_ROOT / "template/{% if has_database %}supabase{% endif %}/migrations"
    create = (root / "{% if auth %}20260301000000_create_auth.sql{% endif %}").read_text()
    scope = (root / "{% if auth %}20260301000100_scope_items_to_owner.sql{% endif %}").read_text()
    both = create + scope
    assert re.search(r"create role app_runtime nologin noinherit nosuperuser nocreatedb nocreaterole noreplication nobypassrls", create)
    code = re.sub(r"--.*", "", create).replace("nologin", "")
    assert not re.search(r"password\s+(?:'|\$|e')|\blogin\b", code, re.I)  # no credential is set here, and the role cannot log in as created
    assert "create extension" not in both.lower() and "security definer" not in both.lower()
    for statement in re.findall(r"grant [^;]+;", re.sub(r"--.*", "", both), re.I | re.S):
        assert not re.search(r"\bto\s+(public|anon|authenticated|service_role)\b", statement, re.I), statement
    assert "alter table app.items" not in scope.replace("alter table app.items add column", "")  # row level security was enabled earlier and is never switched off
    assert "force row level security" not in both.lower() and "disable row level security" not in both.lower()
    policies = re.findall(r"create policy (\w+) on app\.items for (\w+) to app_runtime", scope)
    assert sorted(kind for _, kind in policies) == ["delete", "insert", "select", "update"]
    assert scope.count("app.current_user_id()") >= 6
    assert "grant insert (owner_id, title, body)" in scope and "grant update (title, body, updated_at)" in scope
    assert "drop index app.items_title_key" in scope  # a title is unique per owner, not across users


# ------------------------------------------------------------------ configuration and what must not be committed


@pytest.mark.parametrize("label", ["pg+auth", "sb+auth"])
def test_no_secret_is_committed_and_the_example_holds_placeholders_only(apps, label: str) -> None:
    project = apps[label]
    example = (project / ".env.example").read_text()
    for key in ("DATABASE_RUNTIME_URL", "BETTER_AUTH_SECRET", "BETTER_AUTH_URL"):
        assert re.search(rf"^{key}=", example, re.M), key
    assert not re.search(r"^NEXT_PUBLIC_", example, re.M)
    assert "BETTER_AUTH_SECRET=generated-by-npm-run-db-init" in example and "app_runtime:generated-by-npm-run-db-init@" in example
    assert not (project / ".env.local").exists() and "!.env.example" in (project / ".gitignore").read_text()
    shapes = [r"gh[pousr]_[A-Za-z0-9]{20,}", r"github_pat_[A-Za-z0-9_]{20,}", r"AKIA[0-9A-Z]{16}", r"-----BEGIN [A-Z ]*PRIVATE KEY-----", r"BETTER_AUTH_SECRET\s*[:=]\s*[\"']?[0-9a-f]{32,}"]
    offenders = [n for n, b in _files(project).items() if n != "package-lock.json" and any(re.search(s.encode(), b) for s in shapes)]
    assert offenders == []


def test_supabase_auth_stays_disabled_and_the_data_api_does_not_expose_the_new_schemas(apps) -> None:
    config = (apps["sb+auth"] / "supabase/config.toml").read_text()
    assert re.search(r"\[auth\]\s*\n(?:#[^\n]*\n)*enabled = false", config)
    schemas = re.search(r"schemas = \[([^\]]*)\]", config).group(1)
    assert "app" not in schemas and "app_auth" not in schemas
    assert "service_role" not in "\n".join(b.decode(errors="ignore") for n, b in _files(apps["sb+auth"]).items() if n.startswith("src/") and not n.endswith(".test.ts"))


def test_the_configuration_does_not_weaken_a_protection(apps) -> None:
    source = (apps["pg+auth"] / "src/server/auth/auth.ts").read_text()
    assert "disableCSRFCheck: false" in source and "disableOriginCheck: false" in source
    assert re.search(r"telemetry: \{ enabled: false \}", source) and "cookieCache: { enabled: false }" in source
    assert "useSecureCookies: config.secureCookies" in source and 'sameSite: "lax"' in source and "httpOnly: true" in source
    assert "ipAddressHeaders: config.ipHeader ? [config.ipHeader] : []" in source  # no client-controlled header is trusted by default
    for forbidden in ('"*"', "'*'", "trustedOrigins: [\"http", "disableCSRFCheck: true", "disableOriginCheck: true", "baseURL: \"http"):
        assert forbidden not in source, forbidden
    assert "secret: config.secret" in source and not re.search(r"secret:\s*[\"']", source)


def test_the_documentation_records_the_decision_and_the_limits(apps) -> None:
    for label in ("pg+auth", "sb+auth"):
        doc = (apps[label] / "docs/auth.md").read_text()
        for needle in ("Better Auth on `pg` for both", "does not use Supabase Auth", "Not ready for the public internet", "Email verification and password reset", "Trusted proxies", "app_runtime", "404, not 403", "Disabling authentication is not automated"):
            assert needle in doc, needle
        assert "Ready for local development" in doc and "Not ready for open registration" in doc
        assert "docs/auth.md" in (apps[label] / "README.md").read_text() and "docs/auth.md" in (apps[label] / "AGENTS.md").read_text()
    assert (apps["pg+auth"] / "docs/auth.md").read_text().count("**postgres**") == 1 and (apps["sb+auth"] / "docs/auth.md").read_text().count("**supabase**") == 1


# ------------------------------------------------------------------ compatibility with the published release


def _published(tag: str) -> bool:
    return subprocess.run(["git", "-C", str(TEMPLATE_ROOT), "rev-parse", "-q", "--verify", f"{tag}^{{commit}}"], capture_output=True, check=False).returncode == 0


@pytest.mark.parametrize(
    "answers",
    [
        (), ("zod=true", "rest_api=true"), ("zod=true", "forms=true", "rest_api=true", "cache_components=false"),
        ("zod=true", "rest_api=true", "database=postgres"), ("zod=true", "forms=true", "database=supabase"),
    ],  # fmt: skip
    ids=lambda answers: "+".join(answers) or "default",
)
def test_with_auth_off_every_configuration_is_byte_identical_to_the_published_release(answers: tuple[str, ...]) -> None:
    """The release gate for "additive": rendered against the real v0.5.1 tag, nothing but `.copier-answers.yml` differs
    (and, for the Supabase provider, the two new `.gitignore` lines for the CLI's local state)."""
    if not _published("v0.5.1"):
        pytest.skip("the published tag v0.5.1 is not in this checkout (shallow clone)")
    arguments = [sys.executable, str(TEMPLATE_ROOT / "tools/compare_release.py"), "v0.5.1", *[part for answer in answers for part in ("--set", answer)]]
    if "database=supabase" in answers:
        arguments += ["--allow-change", ".gitignore"]  # the one deliberate change: it now ignores the Supabase CLI's local state (see the changelog)
    result = subprocess.run(arguments, capture_output=True, text=True, cwd=TEMPLATE_ROOT, check=False)
    assert result.returncode == 0 and "identical to v0.5.1" in result.stdout, result.stdout + result.stderr
