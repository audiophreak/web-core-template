"""tools/compare_release.py: the guard that a new release leaves default projects unchanged."""

from __future__ import annotations

import compare_release as c


def test_identical_trees_have_no_differences() -> None:
    assert c.compare({"a": b"1", "b": b"2"}, {"a": b"1", "b": b"2"}) == []


def test_additions_removals_and_changes_are_all_reported() -> None:
    differences = c.compare({"kept": b"1", "gone": b"1", "edited": b"1"}, {"kept": b"1", "new": b"1", "edited": b"2"})
    assert differences == [
        "only in the release: gone",
        "only in the working tree: new",
        "content differs: edited",
    ]


def test_the_answers_file_is_excluded_from_the_comparison(tmp_path) -> None:
    (tmp_path / ".copier-answers.yml").write_text("_commit: x")
    (tmp_path / "file.txt").write_text("y")
    assert sorted(c.tree(tmp_path)) == ["file.txt"]


def test_set_accepts_only_true_or_false() -> None:
    import pytest

    assert c.parse_set(["zod=true", "cache_components=false"]) == {"zod": True, "cache_components": False}
    with pytest.raises(SystemExit):
        c.parse_set(["zod=banana"])
