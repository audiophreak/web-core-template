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
    assert "pull_request_target" not in text and "secrets." not in text


def test_every_action_is_pinned_to_a_full_commit_sha_with_a_version_comment() -> None:
    text, workflow = _load()
    steps = [s for job in workflow["jobs"].values() for s in job["steps"]]
    uses = [s["uses"] for s in steps if "uses" in s]
    assert uses
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
    runs = [s["run"] for s in job["steps"] if "run" in s]
    assert runs[1:] == ["uv sync --locked", "uv run --locked pytest"]
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
