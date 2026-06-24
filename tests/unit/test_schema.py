"""Tests for schema management."""

from pathlib import Path

import pytest

from geochem.core.schema_manager import SchemaManager, normalize_field_name

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_load_schema():
    sm = SchemaManager()
    schema = sm.load_from_file(FIXTURES / "schema_minimal.yaml")
    assert len(schema.columns) == 14
    assert schema.get_field("SiO2") is not None


def test_match_exact():
    sm = SchemaManager()
    sm.load_from_file(FIXTURES / "schema_minimal.yaml")

    field, conf = sm.match_field("SiO2")
    assert field == "SiO2"
    assert conf == 1.0


def test_match_alias():
    sm = SchemaManager()
    sm.load_from_file(FIXTURES / "schema_minimal.yaml")

    field, conf = sm.match_field("Sample No.")
    assert field == "Sample_ID"
    assert conf == 1.0


def test_match_unicode_subscript():
    sm = SchemaManager()
    sm.load_from_file(FIXTURES / "schema_minimal.yaml")

    field, conf = sm.match_field("SiO₂")
    assert field == "SiO2"
    assert conf >= 0.9


def test_match_case_insensitive():
    sm = SchemaManager()
    sm.load_from_file(FIXTURES / "schema_minimal.yaml")

    field, conf = sm.match_field("sio2")
    assert field == "SiO2"


def test_match_no_result():
    sm = SchemaManager()
    sm.load_from_file(FIXTURES / "schema_minimal.yaml")

    field, conf = sm.match_field("CompletelyUnknownField")
    assert field is None
    assert conf == 0.0


def test_normalize_unicode():
    assert normalize_field_name("SiO₂") == "SiO2"
    assert normalize_field_name("Na₂O") == "Na2O"
    assert normalize_field_name("Al₂O₃") == "Al2O3"


def test_get_all_field_names():
    sm = SchemaManager()
    sm.load_from_file(FIXTURES / "schema_minimal.yaml")
    names = sm.get_all_field_names()
    assert "Sample_ID" in names
    assert "SiO2" in names
    assert "CIA" in names
