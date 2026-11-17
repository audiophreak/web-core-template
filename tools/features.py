"""The feature registry (features.yml) and the configuration matrix it implies.

Maintainer and test tooling only: generation itself is plain Copier. The rules here must agree
with copier.yml; tests render every valid configuration and check that they do.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

import yaml

REGISTRY_PATH = Path(__file__).resolve().parents[1] / "features.yml"
BASE_KEY = "base"


class RegistryError(ValueError):
    """features.yml is malformed or self-contradictory."""


@dataclass(frozen=True)
class Feature:
    id: str
    summary: str
    requires: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    npm: bool = False


Registry = dict[str, Feature]


def load_registry(path: Path = REGISTRY_PATH) -> Registry:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    registry: Registry = {}
    for feature_id, entry in (raw.get("features") or {}).items():
        entry = entry or {}
        registry[feature_id] = Feature(
            id=feature_id,
            summary=entry.get("summary", ""),
            requires=tuple(entry.get("requires") or ()),
            conflicts=tuple(entry.get("conflicts") or ()),
            npm=bool(entry.get("npm", False)),
        )
    validate_registry(registry)
    return registry


def validate_registry(registry: Registry) -> None:
    for feature in registry.values():
        if not feature.summary:
            raise RegistryError(f"{feature.id}: a summary is required")
        for other in (*feature.requires, *feature.conflicts):
            if other not in registry:
                raise RegistryError(f"{feature.id}: refers to unknown feature {other!r}")
            if other == feature.id:
                raise RegistryError(f"{feature.id}: refers to itself")
        both = set(feature.requires) & set(feature.conflicts)
        if both:
            raise RegistryError(f"{feature.id}: requires and conflicts with {sorted(both)}")
    # A cycle in `requires` would make every member unselectable.
    visiting: set[str] = set()
    done: set[str] = set()

    def visit(feature_id: str) -> None:
        if feature_id in done:
            return
        if feature_id in visiting:
            raise RegistryError(f"requires cycle through {feature_id!r}")
        visiting.add(feature_id)
        for required in registry[feature_id].requires:
            visit(required)
        visiting.discard(feature_id)
        done.add(feature_id)

    for feature_id in registry:
        visit(feature_id)


def problems(selection: frozenset[str], registry: Registry) -> list[str]:
    """Why `selection` is not a supported configuration (empty list when it is)."""
    found = []
    for feature_id in sorted(selection):
        feature = registry.get(feature_id)
        if feature is None:
            found.append(f"unknown feature {feature_id!r}")
            continue
        for required in feature.requires:
            if required not in selection:
                found.append(f"{feature_id} requires {required}")
        for other in feature.conflicts:
            if other in selection:
                found.append(f"{feature_id} conflicts with {other}")
    return sorted(set(found))


def configurations(registry: Registry) -> list[frozenset[str]]:
    """Every valid selection of features, smallest first. The supported matrix."""
    ids = sorted(registry)
    valid = []
    for size in range(len(ids) + 1):
        for chosen in combinations(ids, size):
            selection = frozenset(chosen)
            if not problems(selection, registry):
                valid.append(selection)
    return valid


def lock_key(selection: frozenset[str], registry: Registry) -> str:
    """Names the lockfile for a configuration: only features that change npm dependencies count."""
    npm_features = sorted(f for f in selection if registry[f].npm)
    return "+".join(npm_features) if npm_features else BASE_KEY


def answers(selection: frozenset[str], registry: Registry) -> dict[str, bool]:
    """The Copier answers that select exactly `selection`."""
    return {feature_id: feature_id in selection for feature_id in sorted(registry)}


def lock_keys(registry: Registry) -> dict[str, list[frozenset[str]]]:
    """Distinct lockfiles needed, each with the configurations that share it."""
    keys: dict[str, list[frozenset[str]]] = {}
    for selection in configurations(registry):
        keys.setdefault(lock_key(selection, registry), []).append(selection)
    return keys
