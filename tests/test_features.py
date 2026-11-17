"""The feature registry (features.yml) and the configuration matrix it implies."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

import features as f
from conftest import REGISTRY, TEMPLATE_ROOT
from features import Feature, RegistryError


def registry(**definitions: dict) -> f.Registry:
    """A synthetic registry: registry(a={"requires": ["b"]}, b={})"""
    built = {
        name: Feature(
            id=name,
            summary=f"{name} feature",
            requires=tuple(spec.get("requires", ())),
            conflicts=tuple(spec.get("conflicts", ())),
            npm=spec.get("npm", False),
        )
        for name, spec in definitions.items()
    }
    f.validate_registry(built)
    return built


def sel(*names: str) -> frozenset[str]:
    return frozenset(names)


# ----------------------------------------------------------------------- the real registry


def test_the_real_registry_is_valid_and_lists_zod() -> None:
    assert sorted(REGISTRY) == ["zod"]
    assert REGISTRY["zod"].npm is True and REGISTRY["zod"].summary


def test_every_feature_is_a_strict_boolean_copier_question_that_defaults_to_off() -> None:
    """Strict form: see the note in copier.yml and tests/test_answer_types.py."""
    questions = yaml.safe_load((TEMPLATE_ROOT / "copier.yml").read_text())
    for feature_id in REGISTRY:
        question = questions[feature_id]
        assert question["type"] == "yaml" and question["choices"] == [False, True], feature_id
        assert f"{feature_id} is not boolean" in question["validator"], feature_id
        assert question["default"] is False, feature_id
        assert question.get("help"), f"{feature_id} needs help text"


def test_feature_rules_are_mirrored_by_validators_in_copier_yml() -> None:
    """copier.yml is what enforces the rules at generation time for any front-end."""
    questions = yaml.safe_load((TEMPLATE_ROOT / "copier.yml").read_text())
    for feature in REGISTRY.values():
        if feature.requires or feature.conflicts:
            validator = questions[feature.id].get("validator", "")
            for other in (*feature.requires, *feature.conflicts):
                assert other in validator, f"{feature.id}: validator does not mention {other}"


def test_the_supported_matrix_of_the_real_registry() -> None:
    assert [sorted(c) for c in f.configurations(REGISTRY)] == [[], ["zod"]]
    assert {k: [sorted(c) for c in v] for k, v in f.lock_keys(REGISTRY).items()} == {
        "base": [[]],
        "zod": [["zod"]],
    }


def test_the_lockfile_matrix_stays_small() -> None:
    """Each npm-affecting combination needs its own committed ~400 KB lockfile.

    If this fails, stop adding independent features and move to curated presets (see
    docs/features.md, "Limits of the lockfile matrix").
    """
    assert len(f.lock_keys(REGISTRY)) <= 16


# ----------------------------------------------------------------------- matrix logic


def test_independent_features_give_every_subset() -> None:
    reg = registry(a={}, b={})
    assert [sorted(c) for c in f.configurations(reg)] == [[], ["a"], ["b"], ["a", "b"]]


def test_requires_removes_selections_missing_the_prerequisite() -> None:
    reg = registry(forms={"requires": ["zod"]}, zod={})
    assert [sorted(c) for c in f.configurations(reg)] == [[], ["zod"], ["forms", "zod"]]
    assert f.problems(sel("forms"), reg) == ["forms requires zod"]


def test_requires_is_transitive_through_the_chain() -> None:
    reg = registry(a={"requires": ["b"]}, b={"requires": ["c"]}, c={})
    valid = [sorted(c) for c in f.configurations(reg)]
    assert ["a", "b", "c"] in valid and ["a", "b"] not in valid and ["a"] not in valid
    assert f.problems(sel("a", "b"), reg) == ["b requires c"]


def test_conflicts_remove_selections_containing_both() -> None:
    reg = registry(vercel={"conflicts": ["postgres"]}, postgres={})
    assert sel("vercel", "postgres") not in f.configurations(reg)
    assert f.problems(sel("vercel", "postgres"), reg) == ["vercel conflicts with postgres"]
    assert sel("vercel") in f.configurations(reg) and sel("postgres") in f.configurations(reg)


def test_problems_reports_unknown_features_and_is_sorted() -> None:
    reg = registry(a={"requires": ["b"]}, b={})
    assert f.problems(sel("nope"), reg) == ["unknown feature 'nope'"]
    assert f.problems(sel("a", "nope"), reg) == ["a requires b", "unknown feature 'nope'"]
    assert f.problems(sel(), reg) == []


def test_lock_key_names_only_npm_affecting_features() -> None:
    reg = registry(zod={"npm": True}, forms={"npm": True, "requires": ["zod"]}, docker={})
    assert f.lock_key(sel(), reg) == "base"
    assert f.lock_key(sel("docker"), reg) == "base"  # no npm change, so the base lock is shared
    assert f.lock_key(sel("zod", "docker"), reg) == "zod"
    assert f.lock_key(sel("forms", "zod"), reg) == "forms+zod"  # sorted, stable


def test_lock_keys_group_configurations_that_share_a_lock() -> None:
    reg = registry(zod={"npm": True}, docker={})
    keys = f.lock_keys(reg)
    assert sorted(keys) == ["base", "zod"]
    assert sorted(sorted(c) for c in keys["base"]) == [[], ["docker"]]


def test_answers_select_exactly_the_given_features() -> None:
    reg = registry(a={}, b={}, c={})
    assert f.answers(sel("b"), reg) == {"a": False, "b": True, "c": False}


# ----------------------------------------------------------------------- registry validation


@pytest.mark.parametrize(
    ("definitions", "message"),
    [
        ({"a": {"requires": ["ghost"]}}, "unknown feature"),
        ({"a": {"conflicts": ["ghost"]}}, "unknown feature"),
        ({"a": {"requires": ["a"]}}, "itself"),
        ({"a": {"requires": ["b"], "conflicts": ["b"]}, "b": {}}, "requires and conflicts"),
        ({"a": {"requires": ["b"]}, "b": {"requires": ["a"]}}, "cycle"),
        ({"a": {"requires": ["b"]}, "b": {"requires": ["c"]}, "c": {"requires": ["a"]}}, "cycle"),
    ],
)
def test_malformed_registries_are_rejected(definitions: dict, message: str) -> None:
    with pytest.raises(RegistryError, match=message):
        registry(**definitions)


def test_a_feature_without_a_summary_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "features.yml"
    path.write_text(textwrap.dedent("features:\n  a:\n    npm: true\n"))
    with pytest.raises(RegistryError, match="summary"):
        f.load_registry(path)
