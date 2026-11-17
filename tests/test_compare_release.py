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


def test_an_allowed_change_is_accepted_only_for_that_exact_file() -> None:
    differences = ["content differs: .prettierignore"]
    assert c.split_allowed(differences, [".prettierignore"]) == ([], differences)
    unaccounted, accepted = c.split_allowed(["content differs: .prettierignore", "content differs: src/app/page.tsx"], [".prettierignore"])
    assert unaccounted == ["content differs: src/app/page.tsx"] and accepted == ["content differs: .prettierignore"]


def test_an_allowance_does_not_cover_added_or_removed_files() -> None:
    unaccounted, _ = c.split_allowed(["only in the working tree: .prettierignore", "only in the release: .prettierignore"], [".prettierignore"])
    assert len(unaccounted) == 3  # both file-presence differences plus the missing expected content change


def test_an_allowance_for_a_file_that_did_not_change_fails() -> None:
    assert c.split_allowed([], [".prettierignore"]) == (["expected change not present: .prettierignore"], [])


def test_the_accepted_change_is_shown_as_a_line_difference() -> None:
    shown = c.line_changes(b"package-lock.json\n", b"package-lock.json\n/.copier-answers.yml\n", ".prettierignore")
    assert [line for line in shown if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))] == ["+/.copier-answers.yml"]
