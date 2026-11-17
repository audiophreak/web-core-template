"""End-to-end: generate a real app, then run `npm ci` and `npm run check` inside it.

Slow and needs network access plus the Node major the template pins (template/.nvmrc).
Run with `uv run pytest -m e2e`, or skip with `-m "not e2e"`.
"""

from __future__ import annotations

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


def _npm(project: Path, *args: str) -> None:
    result = subprocess.run(
        ["npm", *args], cwd=project, capture_output=True, text=True, timeout=900, check=False
    )
    assert result.returncode == 0, (
        f"`npm {' '.join(args)}` exited {result.returncode}\n"
        f"--- stdout (tail) ---\n{result.stdout[-3000:]}\n"
        f"--- stderr (tail) ---\n{result.stderr[-3000:]}"
    )


@pytest.mark.parametrize(
    ("answers", "name"),
    [
        ({"app_description": NEUTRAL_DESCRIPTION}, "e2e-app"),
        ({"app_description": AWKWARD_DESCRIPTION}, "e2e-quoted-app"),
        ({"app_description": NEUTRAL_DESCRIPTION, "cache_components": False}, "e2e-plain-app"),
    ],
    ids=["defaults", "awkward-description", "cache-components-off"],
)
def test_generated_app_installs_and_passes_the_validation_gate(
    template_repo: Path, tmp_path: Path, answers: dict, name: str
) -> None:
    project = tmp_path / name
    render(template_repo, project, app_name=name, **answers)
    _npm(project, "ci", "--no-audit", "--no-fund")
    _npm(project, "run", "check")
