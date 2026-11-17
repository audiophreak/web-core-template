#!/usr/bin/env python3
"""Maintainer tool: prove a configuration renders identically to a previous release.

    uv run python tools/compare_release.py v0.1.0            # default configuration (no features)
    uv run python tools/compare_release.py v0.1.0 --set zod=true

Renders the working tree and the given release tag with the same answers and compares the two
projects file by file. `.copier-answers.yml` is excluded (it legitimately records the new commit and
the new questions). Exit 0 when identical, 1 with the differences listed otherwise.

Release rule (docs/features.md): with every feature off, a new release must render byte-identically
to the previous one, so adding a feature never changes an existing project.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import copier
from copier import Settings

ROOT = Path(__file__).resolve().parents[1]
IGNORED = {".copier-answers.yml"}
GIT_ENV = {
    "GIT_AUTHOR_NAME": "compare",
    "GIT_AUTHOR_EMAIL": "compare@example.invalid",
    "GIT_COMMITTER_NAME": "compare",
    "GIT_COMMITTER_EMAIL": "compare@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}
NAME = "compare-app"


def snapshot(destination: Path) -> None:
    """The working tree's template inputs in a committed repo, so uncommitted work is rendered."""
    destination.mkdir(parents=True)
    shutil.copy2(ROOT / "copier.yml", destination / "copier.yml")
    shutil.copytree(ROOT / "template", destination / "template")
    if (ROOT / "locks").is_dir():
        shutil.copytree(ROOT / "locks", destination / "locks")
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "snapshot"]):
        subprocess.run(["git", "-c", "commit.gpgsign=false", "-C", str(destination), *args],
                       check=True, capture_output=True, env={**os.environ, **GIT_ENV})  # fmt: skip


def render(source: str, ref: str, destination: Path, answers: dict[str, object]) -> None:
    copier.run_copy(
        source,
        destination,
        data={"app_name": NAME, "app_description": "Release comparison", **answers},
        defaults=True,
        vcs_ref=ref,
        settings=Settings(),
        unsafe=False,
        quiet=True,
    )


def tree(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name not in IGNORED
    }


def compare(old: dict[str, bytes], new: dict[str, bytes]) -> list[str]:
    differences = [f"only in the release: {name}" for name in sorted(old.keys() - new.keys())]
    differences += [f"only in the working tree: {name}" for name in sorted(new.keys() - old.keys())]
    differences += [f"content differs: {name}" for name in sorted(old.keys() & new.keys()) if old[name] != new[name]]
    return differences


def parse_set(pairs: list[str]) -> dict[str, object]:
    answers: dict[str, object] = {}
    for pair in pairs:
        name, _, value = pair.partition("=")
        if value not in ("true", "false"):
            raise SystemExit(f"--set {pair!r}: use NAME=true or NAME=false")
        answers[name] = value == "true"
    return answers


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tag", help="the release tag to compare against, e.g. v0.1.0")
    parser.add_argument("--set", dest="options", action="append", default=[], metavar="NAME=true|false")
    args = parser.parse_args(argv)
    answers = parse_set(args.options)

    with tempfile.TemporaryDirectory(prefix="compare-release-") as tmp:
        work = Path(tmp)
        snapshot(work / "src")
        try:
            render(str(ROOT), args.tag, work / "old", answers)
        except Exception as exc:
            print(f"cannot render release {args.tag}: {exc}", file=sys.stderr)
            return 2
        render(str(work / "src"), "HEAD", work / "new", answers)
        differences = compare(tree(work / "old"), tree(work / "new"))
    if differences:
        print(f"NOT identical to {args.tag} ({len(differences)} difference(s)):")
        print("\n".join(f"  {d}" for d in differences))
        return 1
    print(f"identical to {args.tag} (every file except .copier-answers.yml)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
