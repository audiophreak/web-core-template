"""The feature registry (features.yml) and the configuration matrix it implies.

Maintainer and test tooling only: generation itself is plain Copier. The rules here must agree
with copier.yml; tests render every valid configuration and check that they do.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from pathlib import Path

import yaml

REGISTRY_PATH = Path(__file__).resolve().parents[1] / "features.yml"
BASE_KEY = "base"
NONE = "none"  # the default value of every choice feature: not selected


class RegistryError(ValueError):
    """features.yml is malformed or self-contradictory."""


@dataclass(frozen=True)
class Feature:
    id: str
    summary: str
    requires: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    npm: bool = False
    # A choice feature (for example a database provider) is one question with a closed set of values
    # besides the implicit default NONE. Boolean features leave this empty.
    choices: tuple[str, ...] = ()


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
            choices=tuple(entry.get("choices") or ()),
        )
    validate_registry(registry)
    return registry


def validate_registry(registry: Registry) -> None:
    for feature in registry.values():
        if not feature.summary:
            raise RegistryError(f"{feature.id}: a summary is required")
        for choice in feature.choices:
            if not choice.isidentifier() or choice != choice.lower() or choice == NONE:
                raise RegistryError(f"{feature.id}: choice {choice!r} must be a lowercase identifier other than {NONE!r}")
        if len(set(feature.choices)) != len(feature.choices):
            raise RegistryError(f"{feature.id}: duplicate choices")
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


def feature_of(token: str) -> str:
    """The feature a selection token belongs to: `forms` -> forms, `database=postgres` -> database."""
    return token.partition("=")[0]


def problems(selection: frozenset[str], registry: Registry) -> list[str]:
    """Why `selection` is not a supported configuration (empty list when it is).

    A selection holds one token per selected feature: the id of a boolean feature, or `id=value` for a
    choice feature. Rules (`requires`, `conflicts`) are between features, whichever value is chosen.
    """
    found = []
    selected = {feature_of(token) for token in selection}
    for token in sorted(selection):
        feature_id, _, value = token.partition("=")
        feature = registry.get(feature_id)
        if feature is None:
            found.append(f"unknown feature {feature_id!r}")
            continue
        if bool(feature.choices) != bool(value) or (value and value not in feature.choices):
            found.append(f"{token!r} is not a valid selection of {feature_id}")
            continue
        for required in feature.requires:
            if required not in selected:
                found.append(f"{feature_id} requires {required}")
        for other in feature.conflicts:
            if other in selected:
                found.append(f"{feature_id} conflicts with {other}")
    if len({feature_of(token) for token in selection}) != len(selection):
        found.append("a feature is selected more than once")
    return sorted(set(found))


def configurations(registry: Registry) -> list[frozenset[str]]:
    """Every valid selection of features, smallest first. The supported matrix."""
    ids = sorted(registry)
    options = [[None, *(f"{i}={c}" for c in registry[i].choices)] if registry[i].choices else [None, i] for i in ids]
    valid = []
    for chosen in product(*options):
        selection = frozenset(token for token in chosen if token)
        if not problems(selection, registry):
            valid.append(selection)
    return sorted(valid, key=lambda selection: (len(selection), sorted(selection)))


def lock_key(selection: frozenset[str], registry: Registry) -> str:
    """Names the lockfile for a configuration: only features that change npm dependencies count.

    A boolean feature contributes its id and a choice feature `id-value` (two providers may need
    different packages), sorted and joined with `+`; it must match `lock_key` in copier.yml.
    """
    parts = sorted(token.replace("=", "-") for token in selection if registry[feature_of(token)].npm)
    return "+".join(parts) if parts else BASE_KEY


def answers(selection: frozenset[str], registry: Registry) -> dict[str, bool | str]:
    """The Copier answers that select exactly `selection` (a choice feature not selected is `none`)."""
    chosen = dict(token.partition("=")[::2] for token in selection if "=" in token)
    return {
        feature_id: chosen.get(feature_id, NONE) if registry[feature_id].choices else feature_id in selection
        for feature_id in sorted(registry)
    }


def lock_keys(registry: Registry) -> dict[str, list[frozenset[str]]]:
    """Distinct lockfiles needed, each with the configurations that share it."""
    keys: dict[str, list[frozenset[str]]] = {}
    for selection in configurations(registry):
        keys.setdefault(lock_key(selection, registry), []).append(selection)
    return keys
