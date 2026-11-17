"""Repository content must not contain unscoped destructive commands.

Scans code the repository ships or runs (src, tools, tests, CI) and the shell snippets in its
documentation. This repository deletes nothing from the shell or from Python; scratch space is Python tempfile or pytest tmp_path.
Prose in AGENTS.md that names these commands in order to forbid them is not scanned: only code
files and fenced shell blocks are.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CODE_DIRS = ("tools", "tests", ".github", "template")
CODE_SUFFIXES = {".py", ".sh", ".yml", ".yaml", ".toml"}
ALLOWED = {"tests/test_no_destructive_commands.py"}  # this file names the patterns on purpose

SHELL_PATTERNS = {
    "recursive rm": re.compile(r"\brm\s+-[A-Za-z]*[rR]"),
    "find -delete": re.compile(r"\bfind\b[^\n|;]*\s-delete\b"),
    "git clean": re.compile(r"\bgit\s+clean\b"),
    "git reset --hard": re.compile(r"\bgit\s+reset\s+--hard\b"),
    "forced push": re.compile(r"\bgit\s+push\b[^\n]*(--force|\s-f\b)"),
    "recursive chmod/chown": re.compile(r"\bch(mod|own)\s+-[A-Za-z]*R"),
}
PYTHON_PATTERNS = {
    "rmtree": re.compile(r"\brmtree\s*\("),
    "os.removedirs": re.compile(r"\bos\.removedirs\s*\("),
}
FENCE = re.compile(r"```(?:bash|sh|zsh|shell|console)\n(.*?)```", re.DOTALL)


def findings(text: str, *, python: bool) -> list[str]:
    patterns = dict(SHELL_PATTERNS)
    if python:
        patterns.update(PYTHON_PATTERNS)
    return [name for name, pattern in patterns.items() if pattern.search(text)]


SKIPPED_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".next", "dist", "build"}


def repository_files() -> list[Path]:
    """Tracked and untracked-but-not-ignored files; without git (an archive), every file but caches."""
    result = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "--cached", "--others", "--exclude-standard"],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    if result.returncode == 0:
        return [ROOT / name for name in result.stdout.split("\n") if name]
    return [p for p in ROOT.rglob("*") if p.is_file() and not SKIPPED_DIRS & set(p.relative_to(ROOT).parts)]


def test_the_detector_flags_the_pattern_from_the_incident_and_its_relatives() -> None:
    for command in (
        "rm -rf /dev/null 2>/dev/null",
        'rm -rf "$D"',
        "rm -fr build",
        "rm -r somedir",
        "find . -name '*.tmp' -delete",
        "git clean -fdx",
        "git reset --hard HEAD~1",
        "git push --force origin main",
        "git push -f",
        "chmod -R 777 .",
    ):
        assert findings(command, python=False), command
    assert findings("shutil.rmtree(path)", python=True) == ["rmtree"]


def test_the_detector_ignores_harmless_text() -> None:
    for text in (
        "rm is a command", "git status", "git push origin main", "git restore file.txt",
        "uv run python tools/scratch.py clean WORKSPACE", "format the readme", "rmtree is a function",
    ):  # fmt: skip
        assert not findings(text, python=False), text


def test_code_files_contain_no_destructive_commands() -> None:
    scanned = 0
    for path in repository_files():
        relative = path.relative_to(ROOT).as_posix()
        if relative in ALLOWED or not relative.startswith(CODE_DIRS) or path.suffix not in CODE_SUFFIXES:
            continue
        scanned += 1
        found = findings(path.read_text(encoding="utf-8"), python=path.suffix == ".py")
        assert not found, f"{relative}: {found}"
    assert scanned > 10, "the scan found almost nothing; its file selection is wrong"


def test_documented_shell_snippets_contain_no_destructive_commands() -> None:
    blocks = 0
    for path in repository_files():
        if path.suffix != ".md":
            continue
        for block in FENCE.findall(path.read_text(encoding="utf-8")):
            blocks += 1
            found = findings(block, python=False)
            assert not found, f"{path.relative_to(ROOT).as_posix()}: {found}\n{block}"
    assert blocks > 5, "the scan found almost no shell snippets"
