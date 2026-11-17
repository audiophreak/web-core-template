"""The repository's own CI workflow stays minimal, pinned and least-privilege."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from conftest import TEMPLATE_ROOT

WORKFLOW = TEMPLATE_ROOT / ".github" / "workflows" / "ci.yml"


def _load() -> tuple[str, dict]:
    text = WORKFLOW.read_text(encoding="utf-8")
    return text, yaml.safe_load(text)


def test_triggers_are_push_to_main_and_pull_request_only() -> None:
    _, workflow = _load()
    triggers = workflow[True] if True in workflow else workflow["on"]  # YAML reads `on:` as True
    assert set(triggers) == {"push", "pull_request"}
    assert triggers["push"] == {"branches": ["main"]}


def test_permissions_are_read_only_and_never_widened() -> None:
    text, workflow = _load()
    assert workflow["permissions"] == {"contents": "read"}
    for job in workflow["jobs"].values():
        assert "permissions" not in job
    assert "pull_request_target" not in text and "secrets." not in text and "GITHUB_TOKEN" not in text


def test_every_action_is_pinned_to_a_full_commit_sha_with_a_version_comment() -> None:
    text, workflow = _load()
    steps = [s for job in workflow["jobs"].values() for s in job["steps"]]
    uses = [s["uses"] for s in steps if "uses" in s]
    assert len(uses) == 4 and any(u.startswith("actions/upload-artifact@") for u in uses)
    for reference in uses:
        assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", reference), reference
        assert re.search(re.escape(reference) + r" # v\d+\.\d+\.\d+", text), reference


def test_checkout_does_not_persist_credentials() -> None:
    _, workflow = _load()
    for job in workflow["jobs"].values():
        for step in job["steps"]:
            if step.get("uses", "").startswith("actions/checkout@"):
                assert step["with"]["persist-credentials"] is False


def test_the_job_has_a_timeout_and_runs_the_documented_commands() -> None:
    _, workflow = _load()
    (job,) = workflow["jobs"].values()
    assert 0 < job["timeout-minutes"] <= 60
    runs = [s["run"] for s in job["steps"] if "run" in s and "name" not in s]
    assert runs[1:] == ["uv sync --locked"]
    assert re.fullmatch(r"python -m pip install uv==\d+\.\d+\.\d+", runs[0])  # exact uv version


def test_the_matrix_covers_the_supported_python_floor_and_the_newest_tested_version() -> None:
    _, workflow = _load()
    (job,) = workflow["jobs"].values()
    assert job["strategy"]["fail-fast"] is False  # a failure on one version must not hide the other
    versions = job["strategy"]["matrix"]["python-version"]
    project = (TEMPLATE_ROOT / "pyproject.toml").read_text()
    floor = re.search(r'requires-python = ">=(\d+)\.(\d+)"', project)
    assert versions[0] == f"{floor[1]}.{floor[2]}", "the declared floor must be tested"
    assert versions == ["3.12", "3.14"]
    setup = next(s for s in job["steps"] if s.get("uses", "").startswith("actions/setup-python@"))
    assert setup["with"]["python-version"] == "${{ matrix.python-version }}"
    assert job["runs-on"] == "ubuntu-latest"



def test_node_comes_from_the_templates_own_pin() -> None:
    _, workflow = _load()
    (job,) = workflow["jobs"].values()
    node = next(s for s in job["steps"] if s.get("uses", "").startswith("actions/setup-node@"))
    assert node["with"] == {"node-version-file": "template/.nvmrc"}
    assert (TEMPLATE_ROOT / "template" / ".nvmrc").is_file()


# ------------------------------------------------------------------ evidence (docs/ci-evidence.md)

REPORT_STEPS = ("Record run metadata", "Summarize results", "Check reports contain no token-shaped strings", "Upload test reports")


def _job() -> dict:
    (job,) = _load()[1]["jobs"].values()
    return job


def _step(name_start: str) -> dict:
    return next(s for s in _job()["steps"] if s.get("name", "").startswith(name_start))


def _names() -> list[str]:
    return [s.get("name", "") for s in _job()["steps"]]


def test_no_run_script_dumps_the_environment_or_traces_commands() -> None:
    for step in _job()["steps"]:
        script = step.get("run", "")
        assert not re.search(r"(^|[\s;|&])(env|printenv|export)\s*($|[|>;&])", script, re.MULTILINE), script
        assert not re.search(r"\bset\s+-[a-z]*x|\bset\s+-o\s+xtrace", script), script
        assert "echo $" not in script and "toJSON(secrets" not in script


def test_the_commit_under_test_is_the_pull_request_head_not_the_synthetic_merge_commit() -> None:
    env = _job()["env"]
    assert env["EVIDENCE_COMMIT"] == "${{ github.event.pull_request.head.sha || github.sha }}"
    assert env["WEB_CORE_EVIDENCE_DIR"] == "reports/generated-apps"  # inside the uploaded directory


def test_the_test_step_writes_junit_and_a_console_record_and_keeps_the_exit_code() -> None:
    text, _ = _load()
    step = _step("Test")
    script = step["run"]
    assert step["shell"] == "bash"  # bash -eo pipefail: pytest's failure survives the pipe to tee
    assert "continue-on-error" not in text
    assert "uv run --locked pytest" in script
    assert "--junitxml=reports/junit.xml" in script
    assert "tee reports/pytest-output.txt" in script and "2>&1" in script
    assert "-rfEs" in script
    assert "junit_logging=no" in script and "junit_family=xunit2" in script
    assert "${EVIDENCE_COMMIT}" in script and "matrix.python-version" in script


def test_reports_are_written_after_the_tests_and_always_run() -> None:
    names = _names()
    assert [n for n in names if n in REPORT_STEPS] == list(REPORT_STEPS)
    test_index = next(i for i, n in enumerate(names) if n.startswith("Test"))
    for name in REPORT_STEPS:
        assert names.index(name) > test_index
        assert _step(name)["if"].startswith("always()"), f"{name} must run even when the tests fail"


def test_metadata_ties_the_reports_to_the_commit_python_node_and_dependencies() -> None:
    script = _step("Record run metadata")["run"]
    for needle in ("commit_under_test=${EVIDENCE_COMMIT}", "github_sha=${GITHUB_SHA}", "run_id=", "run_attempt=",
                   "matrix_python=", "node=$(node --version", "uv_lock_sha256=", "locks_base_sha256=", "locks_zod_sha256=",
                   "uv pip freeze --exclude-editable"):  # fmt: skip
        assert needle in script, needle


def test_reports_are_scanned_for_tokens_before_upload_and_a_hit_blocks_the_upload() -> None:
    scan, upload = _step("Check reports contain no token-shaped strings"), _step("Upload test reports")
    assert scan["id"] == "scan" and "exit 1" in scan["run"]
    for shape in ("gh[pousr]_", "github_pat_", "PRIVATE KEY", "AKIA"):
        assert shape in scan["run"], shape
    assert upload["if"] == "always() && steps.scan.outcome == 'success'"


def test_the_artifact_is_unique_per_python_and_commit_bounded_and_fails_if_empty() -> None:
    with_ = _step("Upload test reports")["with"]
    assert with_["path"] == "reports/"
    assert "matrix.python-version" in with_["name"] and "env.EVIDENCE_COMMIT" in with_["name"]
    assert with_["if-no-files-found"] == "error"
    assert 1 <= with_["retention-days"] <= 30
