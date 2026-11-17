"""Boolean answers are strict: invalid values are rejected before anything is written.

Copier casts a `type: bool` answer before validators run, so `--data zod=banana` used to enable the
feature silently. Boolean questions are therefore `type: yaml` + `choices: [false, true]` + an
`is boolean` validator (see the note in copier.yml). These tests pin that behaviour for every
boolean question, through the Python API and through the real `copier` command line.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from conftest import GIT_TEST_ENV, REGISTRY, TEMPLATE_ROOT, render

COPIER_YML = TEMPLATE_ROOT / "copier.yml"
QUESTIONS = yaml.safe_load(COPIER_YML.read_text())
BOOLEAN_QUESTIONS = sorted(name for name, q in QUESTIONS.items() if not name.startswith("_") and isinstance(q, dict) and isinstance(q.get("default"), bool))

VALID = [
    (True, True), (False, False),
    ("true", True), ("false", False), ("True", True), ("False", False), ("TRUE", True), ("FALSE", False),
    ("yes", True), ("no", False), ("on", True), ("off", False),
    ("Yes", True), ("NO", False), ("On", True), ("OFF", False),
]  # fmt: skip
INVALID = ["banana", "1", "0", "2", "", " ", "maybe", "tru", "truee", "null", "~", "y", "n", "tRuE", "yEs", 1, 0, 2, None]


def recorded(project: Path, name: str):
    return yaml.safe_load((project / ".copier-answers.yml").read_text())[name]


def test_the_template_has_boolean_questions_to_protect() -> None:
    assert {"cache_components", "zod"} <= set(BOOLEAN_QUESTIONS)
    assert set(REGISTRY) <= set(BOOLEAN_QUESTIONS), "every feature must be a boolean question"


@pytest.mark.parametrize("name", BOOLEAN_QUESTIONS)
def test_every_boolean_question_uses_the_strict_form(name: str) -> None:
    question = QUESTIONS[name]
    assert question["type"] == "yaml", f"{name}: `type: bool` is coerced before validation"
    assert question["choices"] == [False, True]
    assert f"{name} is not boolean" in question["validator"]
    assert question.get("help")


@pytest.mark.parametrize("name", BOOLEAN_QUESTIONS)
@pytest.mark.parametrize(("given", "expected"), VALID, ids=[repr(v[0]) for v in VALID])
def test_valid_booleans_are_accepted_and_recorded_as_real_booleans(
    template_repo: Path, tmp_path: Path, name: str, given: object, expected: bool
) -> None:
    project = tmp_path / "ok"
    render(template_repo, project, **{name: given})
    assert recorded(project, name) is expected


@pytest.mark.parametrize("name", BOOLEAN_QUESTIONS)
@pytest.mark.parametrize("given", INVALID, ids=[repr(v) for v in INVALID])
def test_invalid_booleans_are_rejected_before_anything_is_written(
    template_repo: Path, tmp_path: Path, name: str, given: object
) -> None:
    with pytest.raises(Exception, match=name):
        render(template_repo, tmp_path / "no", **{name: given})
    assert not (tmp_path / "no").exists()


def _copier(template_repo: Path, destination: Path, *data: str) -> subprocess.CompletedProcess[str]:
    arguments = [sys.executable, "-m", "copier", "copy", "--defaults", "--quiet", "--vcs-ref", "HEAD",
                 "--data", "app_name=cli-app", str(template_repo), str(destination)]  # fmt: skip
    for item in data:
        arguments[arguments.index("--data"):arguments.index("--data")] = ["--data", item]
    return subprocess.run(arguments, capture_output=True, text=True, env={**os.environ, **GIT_TEST_ENV}, check=False)


@pytest.mark.parametrize("name", BOOLEAN_QUESTIONS)
@pytest.mark.parametrize("given", ["banana", "1", "0", "maybe", "2"])
def test_the_real_copier_command_line_rejects_invalid_booleans(
    template_repo: Path, tmp_path: Path, name: str, given: str
) -> None:
    result = _copier(template_repo, tmp_path / "cli", f"{name}={given}")
    assert result.returncode != 0
    assert name in result.stderr
    assert not (tmp_path / "cli").exists()


@pytest.mark.parametrize("name", BOOLEAN_QUESTIONS)
@pytest.mark.parametrize(("given", "expected"), [("true", True), ("false", False), ("yes", True), ("no", False)])
def test_the_real_copier_command_line_accepts_valid_booleans(
    template_repo: Path, tmp_path: Path, name: str, given: str, expected: bool
) -> None:
    result = _copier(template_repo, tmp_path / "cli", f"{name}={given}")
    assert result.returncode == 0, result.stderr
    assert recorded(tmp_path / "cli", name) is expected


def test_an_omitted_boolean_takes_its_default(template_repo: Path, tmp_path: Path) -> None:
    result = _copier(template_repo, tmp_path / "defaults")
    assert result.returncode == 0, result.stderr
    for name in BOOLEAN_QUESTIONS:
        assert recorded(tmp_path / "defaults", name) is QUESTIONS[name]["default"]


# ------------------------------------------------------------------ projects made before strictness


def _legacy(copier_yml: str) -> str:
    """The same questions written the old, lenient way (`type: bool`)."""
    for name in BOOLEAN_QUESTIONS:
        copier_yml, count = re.subn(
            r"  type: yaml  # a strict boolean[^\n]*\n(  default: (?:true|false)\n)  choices: \[false, true\]\n"
            rf"  validator: >-\n    \{{% if {name} is not boolean %\}}Answer true or false\.\{{% endif %\}}\n",
            r"  type: bool\n\1",
            copier_yml,
        )
        assert count == 1, f"could not build the legacy form of {name}"
    return copier_yml


def test_the_legacy_form_helper_really_produces_lenient_questions() -> None:
    legacy = yaml.safe_load(_legacy(COPIER_YML.read_text()))
    for name in BOOLEAN_QUESTIONS:
        assert legacy[name]["type"] == "bool" and "choices" not in legacy[name]
