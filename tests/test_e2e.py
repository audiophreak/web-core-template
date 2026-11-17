"""End-to-end: generate a real app, then run `npm ci` and `npm run check` inside it.

Slow and needs network access plus the Node major the template pins (template/.nvmrc).
Run with `uv run pytest -m e2e`, or skip with `-m "not e2e"`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import NEUTRAL_DESCRIPTION, TEMPLATE_ROOT, render

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


def _npm(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["npm", *args], cwd=project, capture_output=True, text=True, timeout=900, check=False
    )
    assert result.returncode == 0, (
        f"`npm {' '.join(args)}` exited {result.returncode}\n"
        f"--- stdout (tail) ---\n{result.stdout[-3000:]}\n"
        f"--- stderr (tail) ---\n{result.stderr[-3000:]}"
    )
    return result


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
    ],
    ids=["defaults", "awkward-description", "cache-components-off", "zod", "zod-and-cache-components-off"],
)
def test_generated_app_installs_and_passes_the_validation_gate(
    template_repo: Path, tmp_path: Path, answers: dict, name: str
) -> None:
    project = tmp_path / name
    render(template_repo, project, app_name=name, **answers)
    _npm(project, "ci", "--no-audit", "--no-fund")
    check = _npm(project, "run", "check")
    _record_evidence(name, project, check)
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
