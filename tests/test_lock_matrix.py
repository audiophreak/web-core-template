"""One committed lockfile per distinct npm dependency set (locks/<key>.json)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import features as f
from conftest import CONFIGURATIONS, REGISTRY, TEMPLATE_ROOT

LOCKS = TEMPLATE_ROOT / "locks"
KEYS = sorted(f.lock_keys(REGISTRY))
NAME = "{{ app_name | to_json }}"


def load_lock(key: str) -> dict:
    text = (LOCKS / f"{key}.json").read_text(encoding="utf-8")
    assert text.count(NAME) == 2, "the project name must be templated in exactly two places"
    return json.loads(text.replace(NAME, '"sample-app"'))


def install_scripts(lock: dict) -> set[str]:
    return {p for p, e in lock["packages"].items() if e.get("hasInstallScript")}


@pytest.mark.parametrize("key", KEYS)
def test_every_lock_key_has_a_lockfile(key: str) -> None:
    assert (LOCKS / f"{key}.json").is_file()


def test_there_are_no_orphan_lockfiles() -> None:
    assert sorted(p.name for p in LOCKS.iterdir()) == [f"{k}.json" for k in KEYS]


def test_every_configuration_receives_the_lockfile_the_registry_names(config_app) -> None:
    """Checks that the lock_key rule in copier.yml agrees with tools/features.py."""
    selection, project = config_app
    generated = json.loads((project / "package-lock.json").read_text())
    assert generated == load_lock(f.lock_key(selection, REGISTRY))


def test_the_lock_shim_renders_exactly_the_lock_file_bytes(config_app) -> None:
    selection, project = config_app
    expected = (LOCKS / f"{f.lock_key(selection, REGISTRY)}.json").read_text()
    assert (project / "package-lock.json").read_text() == expected.replace(NAME, '"sample-app"')


@pytest.mark.parametrize("key", [k for k in KEYS if k != f.BASE_KEY])
def test_feature_locks_are_additive_over_the_base_lock(key: str) -> None:
    base, lock = load_lock(f.BASE_KEY), load_lock(key)
    for path, entry in base["packages"].items():
        if path == "":
            continue
        other = lock["packages"].get(path)
        assert other is not None, f"{path} disappeared from lock {key}"
        for field in ("version", "resolved", "integrity"):
            assert other.get(field) == entry.get(field), f"{path}: {field} differs in lock {key}"


@pytest.mark.parametrize("key", KEYS)
def test_locks_only_use_the_npm_registry_and_carry_integrity_hashes(key: str) -> None:
    packages = load_lock(key)["packages"]
    for path, entry in packages.items():
        if path == "":
            continue
        if entry.get("inBundle"):
            # Shipped inside its parent's tarball, so the parent's hash covers it.
            parent = path.rsplit("/node_modules/", 1)[0]
            assert "integrity" in packages[parent], f"{path}: bundled under an unverified parent"
            continue
        assert entry["resolved"].startswith("https://registry.npmjs.org/"), path
        assert re.fullmatch(r"sha512-[A-Za-z0-9+/]+=*", entry["integrity"]), path


@pytest.mark.parametrize("key", [k for k in KEYS if k != f.BASE_KEY])
def test_a_feature_must_not_introduce_install_scripts(key: str) -> None:
    """Install scripts run code at `npm ci`; a new one needs an explicit, reviewed decision."""
    assert install_scripts(load_lock(key)) == install_scripts(load_lock(f.BASE_KEY))


@pytest.mark.parametrize("selection", CONFIGURATIONS, ids=lambda s: "+".join(sorted(s)) or "default")
def test_feature_dependencies_appear_only_when_selected(selection: frozenset, template_repo: Path, tmp_path: Path) -> None:
    from conftest import render

    project = tmp_path / "p"
    render(template_repo, project, **f.answers(selection, REGISTRY))
    deps = set(json.loads((project / "package.json").read_text())["dependencies"])
    assert ("zod" in deps) == ("zod" in selection)
