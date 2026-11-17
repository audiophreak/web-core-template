"""Controlled failure probe for the CI evidence workflow. Never merge."""

import pytest


def test_deliberate_probe_failure() -> None:
    assert 1 + 1 == 3, "deliberate probe failure: CI must go red and still upload evidence"


@pytest.mark.skip(reason="deliberate probe skip: must appear with this reason in junit.xml")
def test_deliberate_probe_skip() -> None:
    raise AssertionError("never runs")
