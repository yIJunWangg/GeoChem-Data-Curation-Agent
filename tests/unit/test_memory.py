"""Tests for memory store."""

from pathlib import Path

import pytest

from geochem.core.memory import MemoryStore
from geochem.core.models import MappingRule, MappingType, ReviewStatus

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_load_rules():
    store = MemoryStore(FIXTURES / "golden_rules.yaml")
    store.load()
    assert store.get_rule_count() == 10


def test_query_by_source():
    store = MemoryStore(FIXTURES / "golden_rules.yaml")
    store.load()

    rules = store.query_by_source("Na2O")
    assert len(rules) == 2  # Na2O->Na2O and Na2O->Na

    targets = {r.target_field for r in rules}
    assert "Na2O" in targets
    assert "Na" in targets


def test_query_by_source_target():
    store = MemoryStore(FIXTURES / "golden_rules.yaml")
    store.load()

    rule = store.query_by_source_target("Na2O", "Na")
    assert rule is not None
    assert rule.formula == "Na = Na2O * 0.741857"


def test_add_and_remove_rule():
    store = MemoryStore()
    rule = MappingRule(
        rule_id="TEST_RULE",
        source_field="Test",
        target_field="Test2",
        mapping_type=MappingType.EXACT,
    )
    store.add_rule(rule)
    assert store.get_rule_count() == 1

    found = store.query_by_source("Test")
    assert len(found) == 1

    store.remove_rule("TEST_RULE")
    assert store.get_rule_count() == 0


def test_save_and_reload(tmp_path):
    rules_path = tmp_path / "rules.yaml"

    store = MemoryStore(rules_path)
    rule = MappingRule(
        rule_id="SAVE_TEST",
        source_field="X",
        target_field="Y",
        mapping_type=MappingType.ALIAS_NORMALIZATION,
    )
    store.add_rule(rule)
    store.save()

    # Reload
    store2 = MemoryStore(rules_path)
    store2.load()
    assert store2.get_rule_count() == 1
    found = store2.query_by_source_target("X", "Y")
    assert found is not None
