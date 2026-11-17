"""End-to-end: generate a real app, then run `npm ci` and `npm run check` inside it.

Slow and needs network access plus the Node major the template pins (template/.nvmrc).
Run with `uv run pytest -m e2e`, or skip with `-m "not e2e"`.
"""

from __future__ import annotations

import inspect
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from conftest import NEUTRAL_DESCRIPTION, NUMERIC_COMMIT, TEMPLATE_ROOT, render, with_numeric_commit

pytestmark = pytest.mark.e2e

AWKWARD_DESCRIPTION = "It's a \"quoted\" app (v1) & more: a/b, c+d!"


@pytest.fixture(scope="module", autouse=True)
def _node_available() -> None:
    if shutil.which("node") is None or shutil.which("npm") is None:
        pytest.skip("node and npm are required for end-to-end tests")
    required = int((TEMPLATE_ROOT / "template" / ".nvmrc").read_text().strip().split(".")[0])
    result = subprocess.run(["node", "--version"], capture_output=True, text=True, check=False)
    major = int(result.stdout.strip().lstrip("v").split(".")[0])
    if major != required:
        pytest.skip(f"Node {major} found; the template pins {required}")


def _run_npm(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["npm", *args], cwd=project, capture_output=True, text=True, timeout=900, check=False)


def _assert_ok(result: subprocess.CompletedProcess[str], *args: str) -> subprocess.CompletedProcess[str]:
    assert result.returncode == 0, (
        f"`npm {' '.join(args)}` exited {result.returncode}\n"
        f"--- stdout (tail) ---\n{result.stdout[-3000:]}\n"
        f"--- stderr (tail) ---\n{result.stderr[-3000:]}"
    )
    return result


def _npm(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return _assert_ok(_run_npm(project, *args), *args)


def _record_evidence(name: str, project: Path, check: subprocess.CompletedProcess[str]) -> None:
    """With WEB_CORE_EVIDENCE_DIR set (CI does), keep each app's gate output and production audit.

    Recording never changes a verdict: the audit is stored with its exit code, not asserted.
    """
    target = os.environ.get("WEB_CORE_EVIDENCE_DIR")
    if not target:
        return
    out = Path(target)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{name}.check.txt").write_text(check.stdout + check.stderr, encoding="utf-8")
    (out / f"{name}.check-exit-code.txt").write_text(f"{check.returncode}\n", encoding="utf-8")
    audit = subprocess.run(
        ["npm", "audit", "--omit=dev", "--json"], cwd=project, capture_output=True, text=True, timeout=300, check=False
    )
    (out / f"{name}.audit.json").write_text(audit.stdout, encoding="utf-8")
    (out / f"{name}.audit-exit-code.txt").write_text(f"{audit.returncode}\n", encoding="utf-8")


@pytest.mark.parametrize(
    ("answers", "name"),
    [
        ({"app_description": NEUTRAL_DESCRIPTION}, "e2e-app"),
        ({"app_description": AWKWARD_DESCRIPTION}, "e2e-quoted-app"),
        ({"app_description": NEUTRAL_DESCRIPTION, "cache_components": False}, "e2e-plain-app"),
        ({"app_description": NEUTRAL_DESCRIPTION, "zod": True}, "e2e-zod-app"),
        ({"app_description": AWKWARD_DESCRIPTION, "zod": True, "cache_components": False}, "e2e-zod-plain-app"),
        ({"app_description": NEUTRAL_DESCRIPTION, "zod": True, "forms": True}, "e2e-forms-app"),
        ({"app_description": AWKWARD_DESCRIPTION, "zod": True, "forms": True, "cache_components": False}, "e2e-forms-plain-app"),
        ({"app_description": NEUTRAL_DESCRIPTION, "zod": True, "rest-api": True}, "e2e-api-app"),
        ({"app_description": AWKWARD_DESCRIPTION, "zod": True, "rest-api": True, "cache_components": False}, "e2e-api-plain-app"),
        ({"app_description": NEUTRAL_DESCRIPTION, "zod": True, "forms": True, "rest-api": True}, "e2e-forms-api-app"),
        ({"app_description": NEUTRAL_DESCRIPTION, "zod": True, "forms": True, "rest-api": True, "cache_components": False}, "e2e-forms-api-plain-app"),
    ],
    ids=["defaults", "awkward-description", "cache-components-off", "zod", "zod-and-cache-components-off", "forms", "forms-and-cache-components-off",
         "rest-api", "rest-api-awkward-and-cache-components-off", "forms-and-rest-api", "forms-and-rest-api-cache-components-off"],
)
def test_generated_app_installs_and_passes_the_validation_gate(
    template_repo: Path, tmp_path: Path, answers: dict, name: str
) -> None:
    project = tmp_path / name
    render(template_repo, project, app_name=name, **answers)
    _npm(project, "ci", "--no-audit", "--no-fund")
    check = _run_npm(project, "run", "check")
    _record_evidence(name, project, check)  # before the verdict: a failing gate keeps its evidence
    _assert_ok(check, "run", "check")
    if answers.get("zod"):
        # The feature's own tests must actually have run inside the gate: accepted and rejected input.
        result = subprocess.run(
            ["npm", "run", "-s", "test", "--", "src/lib/schemas", "--reporter=verbose"],
            cwd=project, capture_output=True, text=True, timeout=300, check=False,
        )  # fmt: skip
        assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
        assert "app-info.test.ts" in result.stdout
        assert "accepts this application's own app-info.json" in result.stdout
        assert result.stdout.count("rejects") >= 10
    if answers.get("forms"):
        # The feature's own DOM tests must have run inside the gate, in a DOM environment.
        result = subprocess.run(
            ["npm", "run", "-s", "test", "--", "src/components/forms", "src/app/forms-example", "src/lib/schemas/example-form", "--reporter=verbose"],
            cwd=project, capture_output=True, text=True, timeout=300, check=False,
        )  # fmt: skip
        assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
        for name in ("example-form.test.tsx", "page.test.tsx", "example-form.test.ts"):
            assert name in result.stdout, name
        for behavior in ("requires every field", "makes no network request", "shows a submitting state", "passes trimmed, validated values"):
            assert behavior in result.stdout, behavior
        assert "skipped" not in result.stdout.lower().replace("0 skipped", "")
    if answers.get("rest-api"):
        # The API's own tests must have run inside the gate: handlers called with Request objects.
        result = subprocess.run(
            ["npm", "run", "-s", "test", "--", "src/app/api", "src/server", "src/lib/schemas/v1", "--reporter=verbose"],
            cwd=project, capture_output=True, text=True, timeout=300, check=False,
        )  # fmt: skip
        assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
        for name in ("validate/route.test.ts", "app-info/route.test.ts", "[...path]/route.test.ts", "request.test.ts", "responses.test.ts",
                     "boundary.test.ts", "contract.test.ts"):  # fmt: skip
            assert name in result.stdout, name
        for behavior in ("rejects malformed JSON", "enforces the limit while streaming", "is never reached by a client component",
                         "keeps the error codes", "does not echo the submitted text", "makes no network request"):  # fmt: skip
            assert behavior in result.stdout, behavior
        assert "skipped" not in result.stdout.lower().replace("0 skipped", "")


def test_evidence_is_recorded_before_the_gate_verdict() -> None:
    source = inspect.getsource(test_generated_app_installs_and_passes_the_validation_gate)
    assert source.index("_record_evidence(") < source.index("_assert_ok(check")


# ------------------------------------------------------------------ numeric commit identifiers


@pytest.fixture(scope="module", params=[{}, {"zod": True}], ids=["default", "zod"])
def numeric_app(request, template_repo: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """An installed app whose answers file records the all-digit `_commit` that once broke Prettier."""
    project = tmp_path_factory.mktemp("numeric") / "numeric-app"
    render(template_repo, project, app_name="numeric-app", app_description=NEUTRAL_DESCRIPTION, **request.param)
    with_numeric_commit(project)
    _npm(project, "ci", "--no-audit", "--no-fund")
    return project


def _answers_text(project: Path) -> str:
    return (project / ".copier-answers.yml").read_text(encoding="utf-8")


def test_an_app_recording_a_numeric_commit_passes_the_complete_validation_gate(numeric_app: Path) -> None:
    before = _answers_text(numeric_app)
    assert f"_commit: '{NUMERIC_COMMIT}'" in before
    _npm(numeric_app, "run", "check")  # lint, typecheck, format:check, tests, build
    assert _answers_text(numeric_app) == before  # the gate never rewrites Copier's file
    answers = yaml.safe_load(before)
    assert answers["_commit"] == NUMERIC_COMMIT and "zod" in answers


def test_without_the_ignore_prettier_rejects_the_numeric_commit(numeric_app: Path) -> None:
    """Negative control: the failure is real, and the ignore entry is what prevents it."""
    ignore = numeric_app / ".prettierignore"
    original = ignore.read_text(encoding="utf-8")
    try:
        ignore.write_text("package-lock.json\n", encoding="utf-8")
        result = _run_npm(numeric_app, "run", "format:check")
        assert result.returncode != 0
        assert ".copier-answers.yml" in result.stdout + result.stderr
    finally:
        ignore.write_text(original, encoding="utf-8")
    _npm(numeric_app, "run", "format:check")


def test_other_source_files_are_still_format_checked(numeric_app: Path) -> None:
    probe = numeric_app / "src" / "lib" / "format-probe.ts"
    probe.write_text("export const   probe  =   1\n", encoding="utf-8")  # valid TypeScript, not Prettier style
    try:
        result = _run_npm(numeric_app, "run", "format:check")
        output = result.stdout + result.stderr
        assert result.returncode != 0
        assert "format-probe.ts" in output
        assert ".copier-answers.yml" not in output  # only the real offender is reported
    finally:
        probe.unlink()
    _npm(numeric_app, "run", "format:check")


def test_a_failing_gate_keeps_its_evidence_and_cannot_be_mistaken_for_success(
    numeric_app: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deliberately fails `npm run check` in a disposable generated app, the way the e2e test sees it."""
    probe = numeric_app / "src" / "lib" / "format-probe.ts"
    probe.write_text("export const   probe  =   1\n", encoding="utf-8")
    evidence = tmp_path / "evidence"
    monkeypatch.setenv("WEB_CORE_EVIDENCE_DIR", str(evidence))
    try:
        check = _run_npm(numeric_app, "run", "check")
        _record_evidence("failing-app", numeric_app, check)  # exactly what the e2e test does before asserting
        with pytest.raises(AssertionError, match="exited [1-9]"):
            _assert_ok(check, "run", "check")  # the verdict is still a test failure
    finally:
        probe.unlink()
    assert check.returncode != 0
    assert "format-probe.ts" in (evidence / "failing-app.check.txt").read_text(encoding="utf-8")
    assert (evidence / "failing-app.check-exit-code.txt").read_text(encoding="utf-8").strip() == str(check.returncode)
    assert (evidence / "failing-app.audit.json").is_file() and (evidence / "failing-app.audit-exit-code.txt").is_file()
    for path in evidence.iterdir():  # reports carry command output only, never the environment
        text = path.read_text(encoding="utf-8")
        assert "GITHUB_TOKEN" not in text and "PATH=" not in text
    _npm(numeric_app, "run", "check")  # the app is healthy again once the probe is gone
