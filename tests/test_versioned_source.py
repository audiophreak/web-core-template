"""Generation from versioned git sources: tags, branches, and a bare repo standing in for a remote."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from conftest import render, run_git, snapshot_repo


@pytest.fixture()
def released_repo(tmp_path: Path) -> Path:
    """v0.1.0 (.nvmrc=26), v0.2.0 (.nvmrc=26.0) and one later, untagged commit (.nvmrc=26.1)."""
    repo = snapshot_repo(tmp_path / "web-core-template")
    run_git(repo, "tag", "v0.1.0")
    for version, tag in (("26.0", "v0.2.0"), ("26.1", None)):
        (repo / "template" / ".nvmrc").write_text(f"{version}\n")
        run_git(repo, "commit", "-q", "-am", f"nvmrc {version}")
        if tag:
            run_git(repo, "tag", tag)
    return repo


def _nvmrc(project: Path) -> str:
    return (project / ".nvmrc").read_text().strip()


def _answers(project: Path) -> dict:
    return yaml.safe_load((project / ".copier-answers.yml").read_text())


def test_each_tag_renders_exactly_that_release(released_repo: Path, tmp_path: Path) -> None:
    old, new = tmp_path / "old", tmp_path / "new"
    render(released_repo, old, ref="v0.1.0")
    render(released_repo, new, ref="v0.2.0")
    assert (_nvmrc(old), _answers(old)["_commit"]) == ("26", "v0.1.0")
    assert (_nvmrc(new), _answers(new)["_commit"]) == ("26.0", "v0.2.0")


def test_without_a_ref_the_latest_tag_is_used_not_unreleased_commits(
    released_repo: Path, tmp_path: Path
) -> None:
    project = tmp_path / "latest"
    render(released_repo, project, ref=None)
    assert _nvmrc(project) == "26.0"
    assert _answers(project)["_commit"] == "v0.2.0"


def test_head_renders_unreleased_work_for_template_development(
    released_repo: Path, tmp_path: Path
) -> None:
    project = tmp_path / "head"
    render(released_repo, project, ref="HEAD")
    assert _nvmrc(project) == "26.1"


def test_generation_from_a_remote_records_that_origin(
    released_repo: Path, tmp_path: Path
) -> None:
    remote = tmp_path / "published" / "web-core-template.git"
    remote.parent.mkdir()
    run_git(tmp_path, "clone", "-q", "--bare", str(released_repo), str(remote))
    project = tmp_path / "from-remote"
    render(str(remote), project, ref="v0.1.0")
    answers = _answers(project)
    assert answers["_src_path"] == str(remote)  # recorded exactly as given: a portable origin
    assert answers["_commit"] == "v0.1.0"
    assert _nvmrc(project) == "26"


def test_unknown_ref_fails_cleanly_and_creates_nothing(
    released_repo: Path, tmp_path: Path
) -> None:
    with pytest.raises(Exception):  # Copier surfaces the failed git checkout
        render(released_repo, tmp_path / "nope", ref="v9.9.9")
    assert not (tmp_path / "nope").exists()


def test_unavailable_template_fails_and_creates_nothing(tmp_path: Path) -> None:
    with pytest.raises(Exception):
        render(tmp_path / "does-not-exist.git", tmp_path / "out", ref="v0.1.0")
    assert not (tmp_path / "out").exists()


def test_annotated_release_tags_work_like_lightweight_ones(tmp_path: Path) -> None:
    repo = snapshot_repo(tmp_path / "web-core-template")
    run_git(repo, "tag", "-a", "v0.1.0", "-m", "web-core-template v0.1.0")
    remote = tmp_path / "published" / "web-core-template.git"
    remote.parent.mkdir()
    run_git(tmp_path, "clone", "-q", "--bare", str(repo), str(remote))
    for ref in (None, "v0.1.0"):  # latest tag, and the tag by name
        project = tmp_path / f"annotated-{ref}"
        render(str(remote), project, ref=ref)
        assert _answers(project)["_commit"] == "v0.1.0"
