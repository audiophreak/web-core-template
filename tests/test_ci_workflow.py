"""The repository's own CI workflow stays minimal, pinned and least-privilege."""

from __future__ import annotations

import re
import subprocess
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
    assert len(uses) == 8 and sum(u.startswith("actions/upload-artifact@") for u in uses) == 2  # four per job
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
    assert list(workflow["jobs"]) == ["test", "database"]
    for job in workflow["jobs"].values():
        assert 0 < job["timeout-minutes"] <= 60
    job = workflow["jobs"]["test"]
    for each in workflow["jobs"].values():
        runs = [s["run"] for s in each["steps"] if "run" in s and "name" not in s]
        assert runs[1:] == ["uv sync --locked"]
        assert re.fullmatch(r"python -m pip install uv==\d+\.\d+\.\d+", runs[0])  # exact uv version


def test_the_matrix_covers_the_supported_python_floor_and_the_newest_tested_version() -> None:
    _, workflow = _load()
    job = workflow["jobs"]["test"]
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
    for job in workflow["jobs"].values():
        node = next(s for s in job["steps"] if s.get("uses", "").startswith("actions/setup-node@"))
        assert node["with"] == {"node-version-file": "template/.nvmrc"}
    assert (TEMPLATE_ROOT / "template" / ".nvmrc").is_file()


# ------------------------------------------------------------------ evidence (docs/ci-evidence.md)

REPORT_STEPS = ("Record run metadata", "Check reports contain no token-shaped strings", "Summarize results", "Upload test reports")


def _job(name: str = "test") -> dict:
    return _load()[1]["jobs"][name]


def _step(name_start: str, job: str = "test") -> dict:
    return next(s for s in _job(job)["steps"] if s.get("name", "").startswith(name_start))


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
    # Nothing derived from the reports may reach the run page before the scan has passed.
    assert _step("Summarize results")["if"] == "always() && steps.scan.outcome == 'success'"


def test_the_scan_never_prints_what_it_matches() -> None:
    script = _step("Check reports contain no token-shaped strings")["run"]
    grep = re.search(r"^\s*grep (.*)$", script, re.MULTILINE)
    assert grep, "the scan must use grep"
    flags = grep[1].split(" '", 1)[0].split()
    assert "-q" in flags, "grep must be quiet so matching text is never printed"
    assert not {"-n", "-o", "-H", "-A", "-B", "-C", "--only-matching", "--line-number"} & set(flags)
    assert not re.search(r"\b(cat|head|tail|sed|awk|echo \"?\$\(|tee)\b[^\n]*reports", script)  # no other reader of reports/
    assert script.count("::error::") == 2 and all("$" not in line for line in script.splitlines() if "::error::" in line)


def test_the_scan_rejects_a_credential_shaped_string_without_printing_it(tmp_path: Path) -> None:
    """Runs the workflow's own scan script, in a disposable directory, on fake credential-shaped data."""
    script = _step("Check reports contain no token-shaped strings")["run"]
    fakes = {
        "github-token": "ghp_" + "Zx9Q" * 9,
        "fine-grained": "github_pat_" + "Ab1_" * 8,
        "aws-key-id": "AKIA" + "QWERTY0123456789"[:16],
        "private-key": "-----BEGIN RSA PRIVATE KEY-----",
    }

    def run(workspace: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["bash", "-e", "-o", "pipefail", "-c", script], cwd=workspace, capture_output=True, text=True, check=False)

    clean = tmp_path / "clean"
    (clean / "reports").mkdir(parents=True)
    (clean / "reports" / "pytest-output.txt").write_text("231 passed, 9 skipped\n", encoding="utf-8")
    assert run(clean).returncode == 0

    for name, fake in fakes.items():
        workspace = tmp_path / name
        (workspace / "reports" / "generated-apps").mkdir(parents=True)
        (workspace / "reports" / "generated-apps" / "app.check.txt").write_text(f"line one\nleaked {fake} here\n", encoding="utf-8")
        result = run(workspace)
        assert result.returncode == 1, name
        output = result.stdout + result.stderr
        assert "::error::" in output, name
        assert fake not in output and fake[:12] not in output, f"{name}: the matching text was printed"
        assert "leaked" not in output, f"{name}: surrounding report text was printed"

    missing = tmp_path / "no-reports"
    missing.mkdir()
    assert run(missing).returncode == 1  # a scan that cannot run fails closed


def test_the_artifact_is_unique_per_python_and_commit_bounded_and_fails_if_empty() -> None:
    with_ = _step("Upload test reports")["with"]
    assert with_["path"] == "reports/"
    assert "matrix.python-version" in with_["name"] and "env.EVIDENCE_COMMIT" in with_["name"]
    assert with_["if-no-files-found"] == "error"
    assert 1 <= with_["retention-days"] <= 30


# ------------------------------------------------------------------ the database job (real Docker tests)


def test_the_matrix_job_leaves_out_the_docker_tests_and_the_database_job_runs_exactly_those() -> None:
    assert '-m "not db"' in _step("Test")["run"]
    database = _step("Test", "database")["run"]
    assert "-m db" in database and '"not db"' not in database
    assert "--junitxml=reports/junit.xml" in database and "tee reports/pytest-output.txt" in database
    assert "-database" in database and "${EVIDENCE_COMMIT}" in database  # its own suite name, tied to the commit


def test_the_database_job_requires_docker_instead_of_skipping_and_runs_once_on_the_newest_python() -> None:
    job = _job("database")
    assert job["env"]["REQUIRE_DOCKER"] == "1" and "strategy" not in job and job["runs-on"] == "ubuntu-latest"
    setup = next(s for s in job["steps"] if s.get("uses", "").startswith("actions/setup-python@"))
    assert setup["with"]["python-version"] == "3.14"
    assert job["env"]["EVIDENCE_COMMIT"] == _job()["env"]["EVIDENCE_COMMIT"]
    assert "continue-on-error" not in _load()[0]


def test_the_database_job_keeps_the_evidence_steps_and_an_artifact_of_its_own() -> None:
    names = [s.get("name", "") for s in _job("database")["steps"]]
    assert [n for n in names if n in REPORT_STEPS] == list(REPORT_STEPS)
    for name in REPORT_STEPS:
        assert _step(name, "database")["if"].startswith("always()")
    upload = _step("Upload test reports", "database")["with"]
    assert upload["name"] == "test-reports-database-${{ env.EVIDENCE_COMMIT }}" and upload["path"] == "reports/"
    assert upload["name"] != _step("Upload test reports")["with"]["name"]  # the two jobs never collide on an artifact name
    assert 1 <= upload["retention-days"] <= 30 and upload["if-no-files-found"] == "error"
    scan = _step("Check reports contain no token-shaped strings", "database")["run"]
    assert "grep -r -q" in scan and "exit 1" in scan  # the same quiet scan, and a hit blocks the upload
    assert _step("Upload test reports", "database")["if"] == "always() && steps.scan.outcome == 'success'"
