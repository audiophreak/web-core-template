"""The optional REST API feature: what a project gets with it, and what it must not get without it."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

import features as f
from conftest import CONFIGURATIONS, REGISTRY, TEMPLATE_ROOT, render
from test_answer_types import _copier

API = "rest_api"
SCHEMAS = "src/lib/schemas"
FEATURE_FILES = {
    "docs/api-v1.md",
    "src/app/api/v1/[...path]/route.ts",
    "src/app/api/v1/[...path]/route.test.ts",
    "src/app/api/v1/app-info/route.ts",
    "src/app/api/v1/app-info/route.test.ts",
    "src/app/api/v1/examples/validate/route.ts",
    "src/app/api/v1/examples/validate/route.test.ts",
    f"{SCHEMAS}/v1/app-info.ts",
    f"{SCHEMAS}/v1/app-info.test.ts",
    f"{SCHEMAS}/v1/contract.test.ts",
    f"{SCHEMAS}/v1/index.ts",
    f"{SCHEMAS}/v1/problem.ts",
    f"{SCHEMAS}/v1/problem.test.ts",
    f"{SCHEMAS}/v1/validate-example.ts",
    f"{SCHEMAS}/v1/validate-example.test.ts",
    "src/server/api/expect-problem.ts",
    "src/server/api/request.ts",
    "src/server/api/request.test.ts",
    "src/server/api/responses.ts",
    "src/server/api/responses.test.ts",
    "src/server/boundary.test.ts",
}
SHARED_FILES_THAT_CHANGE = {".copier-answers.yml", "AGENTS.md", "README.md", f"{SCHEMAS}/index.ts"}
ZOD_ONLY_INDEX = 'export { appInfoSchema, type AppInfo } from "./app-info";\n'  # byte-for-byte as in v0.3.0
FORMS_INDEX = ZOD_ONLY_INDEX + 'export { exampleFormSchema, type ExampleFormValues } from "./example-form";\n'
SPELLINGS_TRUE = ["true", "True", "TRUE", "yes", "Yes", "YES", "on", "On", "ON"]


@pytest.fixture(scope="module")
def apps(template_repo: Path, tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """The same application name everywhere, so only the selected features differ."""
    root = tmp_path_factory.mktemp("rest_api")
    chosen = {
        "default": {},
        "zod": {"zod": True},
        "forms": {"zod": True, "forms": True},
        "api": {"zod": True, API: True},
        "forms+api": {"zod": True, "forms": True, API: True},
    }
    for label, answers in chosen.items():
        render(template_repo, root / label, app_name="api-app", **answers)
    return {label: root / label for label in chosen}


def _files(project: Path) -> dict[str, bytes]:
    return {str(p.relative_to(project)): p.read_bytes() for p in sorted(project.rglob("*")) if p.is_file()}


# ------------------------------------------------------------------ disabled: the baseline


@pytest.mark.parametrize("label", ["default", "zod", "forms"])
def test_disabled_projects_have_no_api_files_directories_or_guidance(apps, label: str) -> None:
    project = apps[label]
    for name in FEATURE_FILES:
        assert not (project / name).exists(), name
    for directory in ("src/app/api", "src/server", f"{SCHEMAS}/v1", "docs"):
        assert not (project / directory).exists(), directory
    for relative in ("README.md", "AGENTS.md"):
        text = (project / relative).read_text()
        assert "REST API" not in text and "API v1" not in text and "api-v1" not in text, relative
    assert yaml.safe_load((project / ".copier-answers.yml").read_text())[API] is False
    assert [p for p in project.rglob("*") if p.is_dir() and not any(p.iterdir())] == []


def test_the_schema_index_is_byte_identical_to_v0_3_0_when_the_api_is_off(apps) -> None:
    assert (apps["zod"] / SCHEMAS / "index.ts").read_text() == ZOD_ONLY_INDEX
    assert (apps["forms"] / SCHEMAS / "index.ts").read_text() == FORMS_INDEX


# ------------------------------------------------------------------ the rule: rest_api requires zod


@pytest.mark.parametrize("answers", [{API: True}, {API: True, "zod": False}, {API: "yes"}, {API: "ON"}, {API: True, "forms": False}], ids=str)
def test_the_api_without_zod_is_rejected_before_anything_is_written(template_repo: Path, tmp_path: Path, answers: dict) -> None:
    with pytest.raises(Exception, match="rest_api requires zod"):
        render(template_repo, tmp_path / "no", **answers)
    assert not (tmp_path / "no").exists()


@pytest.mark.parametrize("given", SPELLINGS_TRUE)
def test_every_true_spelling_is_rejected_without_zod_through_the_real_command_line(template_repo: Path, tmp_path: Path, given: str) -> None:
    result = _copier(template_repo, tmp_path / "cli", f"{API}={given}")
    assert result.returncode != 0 and "rest_api requires zod" in result.stderr
    assert not (tmp_path / "cli").exists()


@pytest.mark.parametrize("given", ["false", "False", "no", "off", False])
def test_switching_the_api_off_never_needs_zod(template_repo: Path, tmp_path: Path, given) -> None:
    project = tmp_path / "ok"
    render(template_repo, project, **{API: given})
    answers = yaml.safe_load((project / ".copier-answers.yml").read_text())
    assert (answers[API], answers["zod"]) == (False, False) and not (project / "src/app/api").exists()


def test_the_api_never_turns_zod_on_by_itself(template_repo: Path, tmp_path: Path) -> None:
    project = tmp_path / "ok"
    render(template_repo, project, zod=True)  # zod alone leaves the API off
    answers = yaml.safe_load((project / ".copier-answers.yml").read_text())
    assert (answers["zod"], answers[API]) == (True, False)
    with pytest.raises(Exception, match="rest_api requires zod"):
        render(template_repo, tmp_path / "api-only", **{API: True})


def test_the_question_is_a_plain_identifier_with_no_alias_or_special_validation() -> None:
    """The feature is named like the other options: a valid Jinja identifier, so the ordinary strict
    boolean question and validator are enough and nothing has to be computed or hidden."""
    text = (TEMPLATE_ROOT / "copier.yml").read_text()
    questions = yaml.safe_load(text)
    assert "rest-api" not in text and "_copier_answers" not in text  # no hyphenated name
    assert API.isidentifier() and API in REGISTRY
    question = questions[API]
    assert (question["type"], question["default"], question["choices"]) == ("yaml", False, [False, True])
    assert question["validator"].count("{% if rest_api is not boolean %}") == 1 and "zod" in question["validator"]
    computed = [name for name, q in questions.items() if isinstance(q, dict) and q.get("when") is False]
    assert computed == ["has_database", "has_server", "lock_key"]  # readable names for file-name conditions, and the lock selector


def test_only_the_answer_is_recorded_and_it_always_matches_the_generated_files(apps) -> None:
    for label, project in apps.items():
        answers = yaml.safe_load((project / ".copier-answers.yml").read_text())
        assert "lock_key" not in answers and "rest-api" not in answers, label
        assert (project / "src/app/api").is_dir() == answers[API], label
        assert (project / "src/server").is_dir() == answers[API], label


# ------------------------------------------------------------------ enabled: files and dependencies


def test_enabling_the_api_adds_exactly_the_expected_files_and_changes_only_shared_files(apps) -> None:
    for with_api, without in (("api", "zod"), ("forms+api", "forms")):
        enabled, baseline = _files(apps[with_api]), _files(apps[without])
        assert set(enabled) - set(baseline) == FEATURE_FILES, with_api
        assert set(baseline) - set(enabled) == set(), with_api
        assert {n for n in baseline if enabled[n] != baseline[n]} == SHARED_FILES_THAT_CHANGE, with_api


def test_the_api_adds_no_dependency_and_shares_the_existing_lockfile(apps) -> None:
    for with_api, without in (("api", "zod"), ("forms+api", "forms")):
        for name in ("package.json", "package-lock.json"):
            assert (apps[with_api] / name).read_bytes() == (apps[without] / name).read_bytes(), f"{with_api}: {name}"
    assert f.lock_key(frozenset({"zod", API}), REGISTRY) == "zod"
    assert f.lock_key(frozenset({"forms", "zod", API}), REGISTRY) == "forms+zod"


def test_the_schema_index_exports_the_v1_contracts_next_to_the_other_features(apps) -> None:
    assert (apps["api"] / SCHEMAS / "index.ts").read_text() == ZOD_ONLY_INDEX + 'export * as v1 from "./v1";\n'
    assert (apps["forms+api"] / SCHEMAS / "index.ts").read_text() == FORMS_INDEX + 'export * as v1 from "./v1";\n'


def test_the_forms_feature_is_untouched_by_the_api(apps) -> None:
    forms_files = {n: b for n, b in _files(apps["forms+api"]).items() if "forms" in n or "example-form" in n}
    baseline = {n: b for n, b in _files(apps["forms"]).items() if "forms" in n or "example-form" in n}
    assert forms_files == baseline and baseline


# ------------------------------------------------------------------ the architecture, as generated


def _read(project: Path, relative: str) -> str:
    return (project / relative).read_text()


def test_handlers_validate_input_and_output_and_use_the_native_request_and_response(apps) -> None:
    project = apps["api"]
    validate = _read(project, "src/app/api/v1/examples/validate/route.ts")
    assert "validateExampleRequestSchema.safeParse(" in validate and "readJson(request, MAX_BODY_BYTES)" in validate
    assert "validateExampleResponseSchema" in validate and "problem(" in validate
    assert "export const POST = guarded(" in validate
    info = _read(project, "src/app/api/v1/app-info/route.ts")
    assert 'from "@/lib/app-info.json"' in info and "appInfoResponseSchema" in info  # the existing metadata
    responses = _read(project, "src/server/api/responses.ts")
    assert "schema.safeParse(data)" in responses and "application/problem+json" in responses
    for name in ("src/app/api/v1/examples/validate/route.ts", "src/app/api/v1/app-info/route.ts", "src/server/api/request.ts", "src/server/api/responses.ts"):
        text = _read(project, name)
        assert "NextResponse" not in text and "NextRequest" not in text, f"{name}: native Request and Response only"


def test_the_api_code_uses_nothing_but_zod_and_the_application_itself(apps) -> None:
    """Application code imports only zod and the project's own modules; tests may add vitest, and the
    boundary test reads the source tree with node:fs and node:path. No new package is needed."""
    project = apps["api"]
    application = re.compile(r"^(zod|@/.*|\./.*)$")
    tests = re.compile(r"^(zod|vitest|@/.*|\./.*|node:fs|node:path)$")
    for name in sorted(FEATURE_FILES):
        if not name.endswith(".ts"):
            continue
        allowed = tests if name.endswith(".test.ts") or name.endswith("expect-problem.ts") else application
        statements = re.findall(r'^(?:import|export)\b[^;]*?\bfrom\s*["\']([^"\']+)["\']', _read(project, name), re.M)
        for specifier in statements:  # real import statements only, not sample source inside strings
            assert allowed.match(specifier), f"{name} imports {specifier}"


def test_no_server_actions_cors_logging_or_environment_access(apps) -> None:
    project = apps["api"]
    for name in sorted(FEATURE_FILES):
        if not name.endswith(".ts"):
            continue
        text = _read(project, name)
        if name.endswith("boundary.test.ts"):
            continue  # it names these things in order to forbid them
        for forbidden in ('"use server"', "'use server'", "Access-Control-Allow-Origin", "console.", "process.env", "headers().get", "cookies("):
            assert forbidden not in text, f"{name}: {forbidden}"
    assert not (project / "src/middleware.ts").exists() and not (project / "src/proxy.ts").exists()


def test_the_server_directory_holds_only_server_helpers_and_the_contracts_stay_client_importable(apps) -> None:
    project = apps["api"]
    for path in (project / "src/lib/schemas").rglob("*.ts"):
        text = path.read_text()
        assert "@/server" not in text and "next/" not in text and "node:" not in text, path.name
    assert {p.name for p in (project / "src/server/api").iterdir() if not p.name.endswith(".test.ts")} == {"expect-problem.ts", "request.ts", "responses.ts"}


def test_the_shipped_tests_cover_the_required_behaviors(apps) -> None:
    project = apps["api"]
    validate_tests = _read(project, "src/app/api/v1/examples/validate/route.test.ts")
    for behavior in ("malformed JSON", "text/plain", "413", "415", "422", "invalid_json", "validation_failed", "payload_too_large",
                     "does not echo", "makes no network request", "missing title", "unexpected fields", "405"):  # fmt: skip
        assert behavior in validate_tests, behavior
    assert "expectProblem" in validate_tests and "validateExampleResponseSchema.parse" in validate_tests
    info_tests = _read(project, "src/app/api/v1/app-info/route.test.ts")
    assert "appInfoResponseSchema.parse" in info_tests and "makes no network request" in info_tests
    helper_tests = _read(project, "src/server/api/request.test.ts")
    assert "enforces the limit while streaming" in helper_tests and "valid UTF-8" in helper_tests
    assert "rejects" in _read(project, "src/server/boundary.test.ts") or "is never reached" in _read(project, "src/server/boundary.test.ts")
    # handlers are called with Request objects: no server, no network
    for text in (validate_tests, info_tests):
        assert "new Request(" in text or "GET()" in text
        assert "createServer" not in text and "listen(" not in text and "http://127" not in text


def test_the_contract_test_pins_every_v1_field_and_error_code(apps) -> None:
    contract = _read(apps["api"], f"{SCHEMAS}/v1/contract.test.ts")
    problem = _read(apps["api"], f"{SCHEMAS}/v1/problem.ts")
    codes = re.findall(r'^\s+"([a-z_]+)",$', problem.split("export const problemCodes = [")[1].split("] as const")[0], re.M)
    assert len(codes) == 7
    for code in codes:
        assert f'"{code}"' in contract, code
    assert "toJSONSchema" in contract  # the schemas can emit JSON Schema or OpenAPI later


def test_the_api_document_states_every_endpoint_status_and_error_code(apps) -> None:
    doc = _read(apps["api"], "docs/api-v1.md")
    problem = _read(apps["api"], f"{SCHEMAS}/v1/problem.ts")
    codes = re.findall(r'^\s+"([a-z_]+)",$', problem.split("export const problemCodes = [")[1].split("] as const")[0], re.M)
    for code in codes:
        assert f"`{code}`" in doc, code
    for needle in ("GET", "POST", "/api/v1/app-info", "/api/v1/examples/validate", "application/problem+json", "RFC 9457", "additive",
                   "/api/v2", "ignore response fields", "8 KiB", "Accept-Post", "Allow", "CORS"):  # fmt: skip
        assert needle in doc, needle
    for status in ("400", "404", "405", "413", "415", "422", "500"):
        assert re.search(rf"\|\s*{status}\s*\|", doc), status
    readme, agents = _read(apps["api"], "README.md"), _read(apps["api"], "AGENTS.md")
    assert "## REST API (v1)" in readme and "docs/api-v1.md" in readme
    assert "additive-only" in agents and "docs/api-v1.md" in agents


def test_the_selection_is_recorded_and_composes_with_every_other_selection(apps, template_repo: Path, tmp_path: Path) -> None:
    for label, expected in (("api", (True, False)), ("forms+api", (True, True))):
        answers = yaml.safe_load((apps[label] / ".copier-answers.yml").read_text())
        assert (answers[API], answers["forms"]) == expected and answers["zod"] is True
    plain = tmp_path / "plain"
    render(template_repo, plain, app_name="plain-api", zod=True, forms=True, cache_components=False, **{API: True})
    assert "cacheComponents" not in (plain / "next.config.ts").read_text()
    assert (plain / "src/app/api/v1/app-info/route.ts").is_file() and (plain / "src/components/forms/example-form.tsx").is_file()


def test_every_supported_configuration_renders_and_records_exactly_its_selection(template_repo: Path, tmp_path: Path) -> None:
    assert len(CONFIGURATIONS) == 17
    for index, selection in enumerate(CONFIGURATIONS):
        project = tmp_path / f"c{index}"
        render(template_repo, project, **f.answers(selection, REGISTRY))
        answers = yaml.safe_load((project / ".copier-answers.yml").read_text())
        recorded = {f"{name}={answers[name]}" if REGISTRY[name].choices and answers[name] != "none" else name for name in REGISTRY if answers[name] not in (False, "none")}
        assert recorded == set(selection)
        assert (project / "src/app/api/v1").is_dir() == (API in selection)
        assert (project / "src/components/forms").is_dir() == ("forms" in selection)
        assert (project / "src/lib/schemas").is_dir() == ("zod" in selection)
