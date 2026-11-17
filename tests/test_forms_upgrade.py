"""Forms and `copier update`: enabling it later, keeping it, and being refused without Zod.

Uses the same published-release stand-in as tests/test_upgrade.py. Projects are made to look as if
generated before the `forms` question existed (no `forms` answer recorded).
"""

from __future__ import annotations

import json
from pathlib import Path

from conftest import TEMPLATE_ROOT, render, run_git
from test_upgrade import USER_PAGE_EDIT, _adopt, _answers, _lock, _release, _update, published  # noqa: F401

SCHEMA_DIR = "src/lib/{% if zod %}schemas{% endif %}"
FORMS_FILE_TEMPLATE = SCHEMA_DIR + "/{% if forms %}example-form.ts{% endif %}"
PRODUCTION = {"@hookform/resolvers": "5.9.2", "react-hook-form": "7.91.0"}
OWN_SCHEMA = 'import { z } from "zod";\n\nexport const contactSchema = z.object({ phone: z.string() });\n'


def _as_generated_before_forms(project: Path) -> None:
    path = project / ".copier-answers.yml"
    path.write_text("".join(line for line in path.read_text().splitlines(keepends=True) if not line.startswith("forms:")))
    assert "forms" not in _answers(project)


def _commit_own_work(project: Path) -> Path:
    own = project / "src/lib/schemas/contact.ts"
    own.write_text(OWN_SCHEMA)
    run_git(project, "add", "-A")
    run_git(project, "commit", "-q", "-m", "feat: contact schema")
    return own


def _release_020(repo: Path, remote: Path, edit=None) -> None:
    def change(template: Path) -> None:
        (template / "CONTRIBUTING.md.jinja").write_text("# Contributing to {{ app_name }}\n")
        if edit:
            edit(template)

    _release(repo, remote, "v0.2.0", change)


def _no_markers(project: Path) -> None:
    for name in ("package.json", "package-lock.json", "README.md", "AGENTS.md", "src/lib/schemas/index.ts"):
        assert "<<<<<<<" not in (project / name).read_text(), name


def test_forms_can_be_enabled_on_an_existing_zod_project_without_losing_user_work(published, tmp_path: Path) -> None:  # noqa: F811
    repo, remote = published
    project = tmp_path / "app"
    render(str(remote), project, ref="v0.1.0", app_name="upgrade-app", zod=True)
    _as_generated_before_forms(project)
    _adopt(project)
    own = _commit_own_work(project)
    before_deps = json.loads((project / "package.json").read_text())["dependencies"]
    _release_020(repo, remote)

    result = _update(project, "--vcs-ref", "v0.2.0", "--data", "forms=true")

    assert result.returncode == 0, result.stderr
    answers = _answers(project)
    assert (answers["zod"], answers["forms"], answers["_commit"]) == (True, True, "v0.2.0")
    for name in ("src/components/forms/example-form.tsx", "src/app/forms-example/page.tsx", "src/lib/schemas/example-form.ts"):
        assert (project / name).is_file(), name
    package = json.loads((project / "package.json").read_text())
    assert {k: package["dependencies"][k] for k in PRODUCTION} == PRODUCTION
    assert {k: v for k, v in package["dependencies"].items() if k not in PRODUCTION} == before_deps  # nothing else moved
    lock = _lock(project)
    assert lock["packages"][""]["dependencies"] == package["dependencies"]
    assert lock["packages"][""]["devDependencies"] == package["devDependencies"]
    expected = (TEMPLATE_ROOT / "locks" / "forms+zod.json").read_text().replace("{{ app_name | to_json }}", '"upgrade-app"')
    assert (project / "package-lock.json").read_text() == expected  # exactly the committed matrix lock
    _no_markers(project)
    assert own.read_text() == OWN_SCHEMA  # the user's own schema is untouched
    assert USER_PAGE_EDIT in (project / "src/app/page.tsx").read_text()
    assert "Local note kept across upgrades." in (project / "README.md").read_text()
    assert "exampleFormSchema" in (project / "src/lib/schemas/index.ts").read_text()


def test_a_forms_project_keeps_its_selection_and_user_edits_through_an_upgrade(published, tmp_path: Path) -> None:  # noqa: F811
    repo, remote = published
    project = tmp_path / "app"
    render(str(remote), project, ref="v0.1.0", app_name="keep-app", zod=True, forms=True, cache_components=False)
    _adopt(project)
    own = _commit_own_work(project)
    component = project / "src/components/forms/example-form.tsx"
    component.write_text(component.read_text() + "\n// kept: my own note\n")
    run_git(project, "add", "-A")
    run_git(project, "commit", "-q", "-m", "chore: note")

    def edit(template: Path) -> None:
        schema = repo / "template" / FORMS_FILE_TEMPLATE
        schema.write_text(schema.read_text().replace("Use 80 characters or fewer", "Use up to 80 characters"))

    _release_020(repo, remote, edit)
    result = _update(project, "--vcs-ref", "v0.2.0")

    assert result.returncode == 0, result.stderr
    answers = _answers(project)
    assert (answers["zod"], answers["forms"], answers["cache_components"], answers["_commit"]) == (True, True, False, "v0.2.0")
    assert "Use up to 80 characters" in (project / "src/lib/schemas/example-form.ts").read_text()  # template change
    assert "// kept: my own note" in component.read_text()  # user edit
    assert own.read_text() == OWN_SCHEMA and USER_PAGE_EDIT in (project / "src/app/page.tsx").read_text()
    assert {k: json.loads((project / "package.json").read_text())["dependencies"][k] for k in PRODUCTION} == PRODUCTION
    _no_markers(project)


def test_a_zod_project_without_forms_stays_without_it(published, tmp_path: Path) -> None:  # noqa: F811
    repo, remote = published
    project = tmp_path / "app"
    render(str(remote), project, ref="v0.1.0", app_name="plain-app", zod=True)
    _as_generated_before_forms(project)
    _adopt(project)
    _release_020(repo, remote)

    assert _update(project, "--vcs-ref", "v0.2.0").returncode == 0
    answers = _answers(project)
    assert (answers["zod"], answers["forms"]) == (True, False)
    assert not (project / "src/components/forms").exists() and not (project / "src/app/forms-example").exists()
    assert not set(PRODUCTION) & set(json.loads((project / "package.json").read_text())["dependencies"])


def test_enabling_forms_on_a_project_without_zod_is_refused_and_changes_nothing(published, tmp_path: Path) -> None:  # noqa: F811
    repo, remote = published
    project = tmp_path / "app"
    render(str(remote), project, ref="v0.1.0", app_name="nozod-app")
    _as_generated_before_forms(project)
    _adopt(project)
    _release_020(repo, remote)
    head = run_git(project, "rev-parse", "HEAD")
    answers = _answers(project)

    result = _update(project, "--vcs-ref", "v0.2.0", "--data", "forms=true")

    assert result.returncode != 0
    assert "forms requires zod" in result.stderr + result.stdout
    assert run_git(project, "status", "--porcelain") == ""  # nothing was written
    assert run_git(project, "rev-parse", "HEAD") == head and _answers(project) == answers
    assert not (project / "src/components/forms").exists()
