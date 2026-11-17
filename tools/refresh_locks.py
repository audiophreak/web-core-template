#!/usr/bin/env python3
"""Maintainer tool: regenerate (or verify) locks/<key>.json for every supported configuration.

    uv run python tools/refresh_locks.py            # write missing or changed lockfiles
    uv run python tools/refresh_locks.py --check    # change nothing; exit 1 if any would change

Needs npm and network access. It is not run by generation and not by the default test suite.

How each lockfile is produced (see docs/features.md):

1. The configuration is rendered with Copier from a snapshot of the working tree, giving the
   exact package.json a generated project would get. package.json.jinja is the only place the
   dependency versions are written.
2. npm starts from the BASE lockfile (locks/base.json) and only adds what the extra dependencies
   need (`npm install --package-lock-only --ignore-scripts`). `--before` makes npm ignore any
   release newer than the cutoff (default: two weeks ago), keeping the supply-chain age rule
   for new transitive packages.
3. The result must be purely additive: every package in the base lock must appear unchanged
   (same version, source and integrity). Otherwise the tool stops without writing.
4. The project name is turned back into the template placeholder and the file is written.

locks/base.json itself is the reference. It is changed by the "refresh the dependency pins"
procedure in docs/maintaining.md, after which this tool refreshes every other lockfile.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import copier
from copier import Settings

import features

ROOT = Path(__file__).resolve().parents[1]
LOCKS = ROOT / "locks"
PLACEHOLDER = "lock-template"
TEMPLATE_NAME = "{{ app_name | to_json }}"
GIT_ENV = {
    "GIT_AUTHOR_NAME": "locks",
    "GIT_AUTHOR_EMAIL": "locks@example.invalid",
    "GIT_COMMITTER_NAME": "locks",
    "GIT_COMMITTER_EMAIL": "locks@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}


class ToolError(RuntimeError):
    pass


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        env={**os.environ, **GIT_ENV},
    )


def snapshot(destination: Path, extra_locks: dict[str, str]) -> None:
    """Copy the working tree's template inputs into a committed repo (Copier renders git trees)."""
    destination.mkdir(parents=True)
    shutil.copy2(ROOT / "copier.yml", destination / "copier.yml")
    shutil.copytree(ROOT / "template", destination / "template")
    shutil.copytree(LOCKS, destination / "locks")
    for name, text in extra_locks.items():
        (destination / "locks" / name).write_text(text, encoding="utf-8")
    _git(destination, "init", "-q", "-b", "main")
    _git(destination, "add", "-A")
    _git(destination, "commit", "-q", "-m", "snapshot")


def to_placeholder_name(template_text: str) -> str:
    return template_text.replace(TEMPLATE_NAME, json.dumps(PLACEHOLDER))


def to_template(lock_text: str) -> str:
    """Replace the two project-name fields (root and packages[""]) with the Jinja placeholder."""
    lines = lock_text.split("\n")
    hits = [i for i, line in enumerate(lines) if line.strip() == f'"name": "{PLACEHOLDER}",']
    if len(hits) != 2:
        raise ToolError(f"expected exactly two name fields in the lockfile, found {len(hits)}")
    for i in hits:
        lines[i] = lines[i].replace(f'"{PLACEHOLDER}"', TEMPLATE_NAME)
    return "\n".join(lines)


def check_additive(base: dict, new: dict, key: str) -> None:
    changed = []
    for path, entry in base["packages"].items():
        if path == "":
            continue
        other = new["packages"].get(path)
        if other is None:
            changed.append(f"{path}: removed")
            continue
        for field in ("version", "resolved", "integrity"):
            if entry.get(field) != other.get(field):
                changed.append(f"{path}: {field} changed")
    if changed:
        shown = "\n  ".join(changed[:10])
        raise ToolError(
            f"lock {key!r} is not additive over the base lock ({len(changed)} change(s)):\n  {shown}"
        )


def build_lock(key: str, selection: frozenset[str], registry, before: str) -> str:
    base_text = (LOCKS / "base.json").read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="refresh-locks-") as tmp:
        work = Path(tmp)
        # Until the lock exists the template cannot render its include, so lend it the base lock.
        snapshot(work / "src", {f"{key}.json": base_text})
        project = work / "project"
        copier.run_copy(
            str(work / "src"),
            project,
            data={"app_name": PLACEHOLDER, "app_description": "Lock template", **features.answers(selection, registry)},
            defaults=True,
            vcs_ref="HEAD",
            settings=Settings(),
            unsafe=False,
            quiet=True,
        )
        starting = json.loads(to_placeholder_name(base_text))
        current = json.loads((project / "package-lock.json").read_text(encoding="utf-8"))
        if current != starting:
            raise ToolError("the starting lockfile is not the base lock; the template is inconsistent")
        subprocess.run(
            ["npm", "install", "--package-lock-only", "--ignore-scripts", "--no-audit", "--no-fund",
             f"--before={before}"],
            cwd=project,
            check=True,
            capture_output=True,
            text=True,
        )  # fmt: skip
        result_text = (project / "package-lock.json").read_text(encoding="utf-8")
        check_additive(starting, json.loads(result_text), key)
        return to_template(result_text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="change nothing; exit 1 if any lockfile would change")
    cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=14)).date().isoformat()
    parser.add_argument("--before", default=cutoff, help=f"ignore npm releases after this date (default: {cutoff})")
    args = parser.parse_args(argv)

    registry = features.load_registry()
    if not (LOCKS / "base.json").is_file():
        print("locks/base.json is missing; it is the reference lockfile", file=sys.stderr)
        return 1
    dirty = False
    for key, configurations in features.lock_keys(registry).items():
        if key == features.BASE_KEY:
            continue
        selection = configurations[0]
        try:
            text = build_lock(key, selection, registry, args.before)
        except (ToolError, subprocess.CalledProcessError) as exc:
            detail = exc.stderr if isinstance(exc, subprocess.CalledProcessError) else exc
            print(f"{key}: FAILED: {str(detail).strip()[-800:]}", file=sys.stderr)
            return 1
        target = LOCKS / f"{key}.json"
        existing = target.read_text(encoding="utf-8") if target.exists() else None
        if existing == text:
            print(f"{key}: up to date")
            continue
        dirty = True
        if args.check:
            print(f"{key}: {'missing' if existing is None else 'differs'} (run without --check)")
        else:
            target.write_text(text, encoding="utf-8")
            print(f"{key}: {'created' if existing is None else 'updated'} (before {args.before})")
    return 1 if (dirty and args.check) else 0


if __name__ == "__main__":
    sys.exit(main())
