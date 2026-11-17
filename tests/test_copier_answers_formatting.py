"""Copier's answers file is not Prettier's business (see template/.prettierignore).

With an untagged source commit Copier records `_commit` as an abbreviated hash. When that hash is
all digits, PyYAML single-quotes it to keep it a string and Prettier then rejects the file for its
quote style. The generated project excludes the root answers file from Prettier; nothing else.
These tests are deterministic: they use the numeric identifier `1234567` instead of waiting for a
real hash to be numeric. The end-to-end proof with Prettier itself is in test_e2e.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from conftest import NEUTRAL_DESCRIPTION, NUMERIC_COMMIT, render, with_numeric_commit

EXPECTED_IGNORED = {"package-lock.json", "/.copier-answers.yml"}


def _patterns(project: Path) -> set[str]:
    lines = (project / ".prettierignore").read_text(encoding="utf-8").splitlines()
    return {line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")}


def test_prettier_ignores_exactly_the_lockfile_and_the_root_answers_file(generated_app: Path) -> None:
    assert _patterns(generated_app) == EXPECTED_IGNORED  # anchored: no other YAML or source file is exempt


def test_the_ignore_does_not_touch_the_answers_file_itself(generated_app: Path, template_repo: Path, tmp_path: Path) -> None:
    other = tmp_path / "other"
    render(template_repo, other, app_name="other-app", zod=True)
    for project in (generated_app, other):
        answers = yaml.safe_load((project / ".copier-answers.yml").read_text(encoding="utf-8"))
        assert answers["_src_path"] and answers["_commit"]  # still recorded, so `copier update` works
        assert "zod" in answers and "cache_components" in answers  # feature selections still recorded


@pytest.mark.parametrize("zod", [False, True], ids=["default", "zod"])
def test_a_numeric_commit_is_valid_yaml_and_keeps_every_other_answer(template_repo: Path, tmp_path: Path, zod: bool) -> None:
    project = tmp_path / "app"
    render(template_repo, project, app_name="numeric-app", app_description=NEUTRAL_DESCRIPTION, zod=zod)
    before = yaml.safe_load((project / ".copier-answers.yml").read_text(encoding="utf-8"))
    text = with_numeric_commit(project)
    assert f"_commit: '{NUMERIC_COMMIT}'" in text  # the quoting that Prettier objects to is really present
    after = yaml.safe_load(text)
    assert after["_commit"] == NUMERIC_COMMIT and isinstance(after["_commit"], str)
    assert {k: v for k, v in after.items() if k != "_commit"} == {k: v for k, v in before.items() if k != "_commit"}
    assert after["zod"] is zod
