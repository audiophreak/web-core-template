"""The REST API and `copier update`: enabling it later, keeping it, and being refused without Zod.

Same published-release stand-in as tests/test_upgrade.py. Projects are made to look as if generated
before the `rest_api` question existed (no such answer recorded), and carry committed user work:
their own API route, their own schema, an edited page and a README note.
"""

from __future__ import annotations

import json
from pathlib import Path

from conftest import TEMPLATE_ROOT, render, run_git
from test_upgrade import USER_PAGE_EDIT, _adopt, _answers, _lock, _release, _update, published  # noqa: F401

API = "rest_api"
V1_TEMPLATE_DIR = "src/lib/{% if zod %}schemas{% endif %}/{% if rest_api %}v1{% endif %}"
OWN_ROUTE = 'export function GET() {\n  return Response.json({ status: "ok" });\n}\n'
OWN_SCHEMA = 'import { z } from "zod";\n\nexport const contactSchema = z.object({ phone: z.string() });\n'
NEW_FILES = (
    "src/app/api/v1/app-info/route.ts",
    "src/app/api/v1/examples/validate/route.ts",
    "src/app/api/v1/[...path]/route.ts",
    "src/lib/schemas/v1/problem.ts",
    "src/server/api/responses.ts",
    "src/server/boundary.test.ts",
    "docs/api-v1.md",
)


def _as_generated_before_the_api(project: Path) -> None:
    path = project / ".copier-answers.yml"
    path.write_text("".join(line for line in path.read_text().splitlines(keepends=True) if not line.startswith(f"{API}:")))
    assert API not in _answers(project)


def _commit_own_work(project: Path) -> dict[str, Path]:
    own = {"route": project / "src/app/api/health/route.ts", "schema": project / "src/lib/schemas/contact.ts"}
    own["route"].parent.mkdir(parents=True)
    own["route"].write_text(OWN_ROUTE)
    own["schema"].write_text(OWN_SCHEMA)
    run_git(project, "add", "-A")
    run_git(project, "commit", "-q", "-m", "feat: health route and contact schema")
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


def _user_work_is_intact(project: Path, own: dict[str, Path]) -> None:
    assert own["route"].read_text() == OWN_ROUTE and own["schema"].read_text() == OWN_SCHEMA
    assert USER_PAGE_EDIT in (project / "src/app/page.tsx").read_text()
    assert "Local note kept across upgrades." in (project / "README.md").read_text()


def test_the_api_can_be_enabled_on_an_existing_zod_project_without_losing_user_work(published, tmp_path: Path) -> None:  # noqa: F811
    repo, remote = published
    project = tmp_path / "app"
    render(str(remote), project, ref="v0.1.0", app_name="upgrade-app", zod=True)
    _as_generated_before_the_api(project)
    _adopt(project)
    own = _commit_own_work(project)
    manifest, lock = (project / "package.json").read_bytes(), (project / "package-lock.json").read_bytes()
    _release_020(repo, remote)

    result = _update(project, "--vcs-ref", "v0.2.0", "--data", f"{API}=true")

    assert result.returncode == 0, result.stderr
    answers = _answers(project)
    assert (answers["zod"], answers[API], answers["forms"], answers["_commit"]) == (True, True, False, "v0.2.0")
    for name in NEW_FILES:
        assert (project / name).is_file(), name
    assert "export * as v1" in (project / "src/lib/schemas/index.ts").read_text()
    assert (project / "package.json").read_bytes() == manifest  # no dependency was added...
    assert (project / "package-lock.json").read_bytes() == lock  # ...and the lockfile is the same one
    _no_markers(project)
    _user_work_is_intact(project, own)
    assert (project / "src/app/api/health/route.ts").parent.parent == project / "src/app/api"  # next to v1, not inside


def test_enabling_the_api_leaves_an_existing_forms_selection_exactly_as_it_was(published, tmp_path: Path) -> None:  # noqa: F811
    repo, remote = published
    project = tmp_path / "app"
    render(str(remote), project, ref="v0.1.0", app_name="forms-app", zod=True, forms=True, cache_components=False)
    _as_generated_before_the_api(project)
    _adopt(project)
    own = _commit_own_work(project)
    forms_before = {p.relative_to(project): p.read_bytes() for p in (project / "src/components/forms").rglob("*") if p.is_file()}
    forms_before[Path("src/app/forms-example/page.tsx")] = (project / "src/app/forms-example/page.tsx").read_bytes()
    manifest = (project / "package.json").read_bytes()
    _release_020(repo, remote)

    result = _update(project, "--vcs-ref", "v0.2.0", "--data", f"{API}=true")

    assert result.returncode == 0, result.stderr
    answers = _answers(project)
    assert (answers["zod"], answers["forms"], answers[API], answers["cache_components"]) == (True, True, True, False)
    for relative, content in forms_before.items():
        assert (project / relative).read_bytes() == content, relative
    assert (project / "package.json").read_bytes() == manifest
    _no_markers(project)
    _user_work_is_intact(project, own)


def test_a_project_with_the_api_keeps_it_and_its_user_edits_through_an_upgrade(published, tmp_path: Path) -> None:  # noqa: F811
    repo, remote = published
    project = tmp_path / "app"
    render(str(remote), project, ref="v0.1.0", app_name="keep-app", zod=True, **{API: True})
    _adopt(project)
    own = _commit_own_work(project)
    responses = project / "src/server/api/responses.ts"
    responses.write_text(responses.read_text() + "\n// kept: my own note\n")
    run_git(project, "add", "-A")
    run_git(project, "commit", "-q", "-m", "chore: note")

    def edit(template: Path) -> None:
        schema = repo / "template" / V1_TEMPLATE_DIR / "app-info.ts"
        schema.write_text(schema.read_text().replace("max(200)", "max(300)"))

    _release_020(repo, remote, edit)
    result = _update(project, "--vcs-ref", "v0.2.0")

    assert result.returncode == 0, result.stderr
    answers = _answers(project)
    assert (answers["zod"], answers[API], answers["_commit"]) == (True, True, "v0.2.0")
    assert "max(300)" in (project / "src/lib/schemas/v1/app-info.ts").read_text()  # the template's change
    assert "// kept: my own note" in responses.read_text()  # the user's edit
    _no_markers(project)
    _user_work_is_intact(project, own)


def test_a_project_without_the_api_stays_without_it(published, tmp_path: Path) -> None:  # noqa: F811
    repo, remote = published
    project = tmp_path / "app"
    render(str(remote), project, ref="v0.1.0", app_name="plain-app", zod=True, forms=True)
    _as_generated_before_the_api(project)
    _adopt(project)
    _release_020(repo, remote)

    assert _update(project, "--vcs-ref", "v0.2.0").returncode == 0
    answers = _answers(project)
    assert (answers["zod"], answers["forms"], answers[API]) == (True, True, False)
    assert not (project / "src/app/api").exists() and not (project / "src/server").exists() and not (project / "docs").exists()


def test_enabling_the_api_on_a_project_without_zod_is_refused_and_changes_nothing(published, tmp_path: Path) -> None:  # noqa: F811
    repo, remote = published
    project = tmp_path / "app"
    render(str(remote), project, ref="v0.1.0", app_name="nozod-app")
    _as_generated_before_the_api(project)
    _adopt(project)
    _release_020(repo, remote)
    head, answers = run_git(project, "rev-parse", "HEAD"), _answers(project)

    result = _update(project, "--vcs-ref", "v0.2.0", "--data", f"{API}=true")

    assert result.returncode != 0
    assert "rest_api requires zod" in result.stderr + result.stdout
    assert run_git(project, "status", "--porcelain") == ""  # nothing was written
    assert run_git(project, "rev-parse", "HEAD") == head and _answers(project) == answers
    assert not (project / "src/app/api").exists()


def test_an_invalid_answer_through_an_update_fails_safely(published, tmp_path: Path) -> None:  # noqa: F811
    repo, remote = published
    project = tmp_path / "app"
    render(str(remote), project, ref="v0.1.0", app_name="bad-app", zod=True)
    _as_generated_before_the_api(project)
    _adopt(project)
    _release_020(repo, remote)
    head, answers = run_git(project, "rev-parse", "HEAD"), _answers(project)

    for bad in ("banana", "1", "maybe", "tRuE"):
        result = _update(project, "--vcs-ref", "v0.2.0", "--data", f"{API}={bad}")
        assert result.returncode != 0, bad
        assert run_git(project, "status", "--porcelain") == "", bad
        assert run_git(project, "rev-parse", "HEAD") == head and _answers(project) == answers, bad


def test_enabling_the_api_with_a_matching_lock_does_not_touch_the_lockfile_bytes(published, tmp_path: Path) -> None:  # noqa: F811
    repo, remote = published
    project = tmp_path / "app"
    render(str(remote), project, ref="v0.1.0", app_name="lock-app", zod=True, forms=True)
    _as_generated_before_the_api(project)
    _adopt(project)
    _release_020(repo, remote)

    assert _update(project, "--vcs-ref", "v0.2.0", "--data", f"{API}=true").returncode == 0
    expected = (TEMPLATE_ROOT / "locks" / "forms+zod.json").read_text().replace("{{ app_name | to_json }}", '"lock-app"')
    assert (project / "package-lock.json").read_text() == expected  # still exactly the committed matrix lock
    assert _lock(project)["packages"][""]["dependencies"] == json.loads((project / "package.json").read_text())["dependencies"]
