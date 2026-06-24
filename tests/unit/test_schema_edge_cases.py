"""Edge case tests for schema matching."""

from pathlib import Path

import pytest

from geochem.core.schema_manager import SchemaManager, normalize_field_name

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def sm():
    s = SchemaManager()
    s.load_from_file(FIXTURES / "schema_minimal.yaml")
    return s


class TestUnicodeNormalization:
    def test_subscript_2(self):
        assert normalize_field_name("SiO₂") == "SiO2"

    def test_subscript_multiple(self):
        assert normalize_field_name("Al₂O₃") == "Al2O3"

    def test_subscript_3(self):
        assert normalize_field_name("Fe₂O₃") == "Fe2O3"

    def test_greek_alpha(self):
        assert normalize_field_name("α") == "alpha"

    def test_no_change_needed(self):
        assert normalize_field_name("SiO2") == "SiO2"

    def test_whitespace_normalization(self):
        assert normalize_field_name("  SiO2  ") == "SiO2"
        assert normalize_field_name("Si O2") == "Si O2"


class TestFieldMatching:
    def test_exact_match(self, sm):
        field, conf = sm.match_field("SiO2")
        assert field == "SiO2"
        assert conf == 1.0

    def test_subscript_match(self, sm):
        field, conf = sm.match_field("SiO₂")
        assert field == "SiO2"
        assert conf >= 0.9

    def test_alias_match_sample_no(self, sm):
        field, conf = sm.match_field("Sample No.")
        assert field == "Sample_ID"
        assert conf == 1.0

    def test_alias_match_sample_number(self, sm):
        field, conf = sm.match_field("Sample Number")
        assert field == "Sample_ID"

    def test_alias_match_chinese(self, sm):
        field, conf = sm.match_field("样品编号")
        assert field == "Sample_ID"

    def test_alias_chemical_index(self, sm):
        field, conf = sm.match_field("Chemical Index of Alteration")
        assert field == "CIA"

    def test_case_insensitive(self, sm):
        field, conf = sm.match_field("sio2")
        assert field == "SiO2"

    def test_case_mixed(self, sm):
        field, conf = sm.match_field("SIO2")
        assert field == "SiO2"

    def test_with_spaces(self, sm):
        field, conf = sm.match_field("SiO 2")
        # Should still match via cleaned comparison
        assert field is not None

    def test_unknown_field(self, sm):
        field, conf = sm.match_field("RandomUnknownField_xyz")
        assert field is None
        assert conf == 0.0

    def test_empty_string(self, sm):
        field, conf = sm.match_field("")
        assert field is None

    def test_short_string_no_match(self, sm):
        # "Si" is too short (2 chars) for partial matching
        field, conf = sm.match_field("Si")
        assert field is None

    def test_partial_match_medium_confidence(self, sm):
        # "SiO" is a substring of "SiO2" but shorter
        field, conf = sm.match_field("SiO")
        assert field == "SiO2"
        assert conf < 0.9


class TestSchemaStructure:
    def test_field_count(self, sm):
        assert len(sm.schema.columns) == 14

    def test_get_field_exists(self, sm):
        f = sm.get_field("SiO2")
        assert f is not None
        assert f.description == "Silicon dioxide concentration"

    def test_get_field_not_exists(self, sm):
        f = sm.get_field("NonExistent")
        assert f is None

    def test_get_all_field_names(self, sm):
        names = sm.get_all_field_names()
        assert "Sample_ID" in names
        assert "SiO2" in names
        assert "CIA" in names
        assert "DOI" in names

    def test_get_all_aliases(self, sm):
        aliases = sm.get_all_aliases()
        assert "Sample No." in aliases["Sample_ID"]
        assert "SiO₂" in aliases["SiO2"]
        assert "Chemical Index of Alteration" in aliases["CIA"]

    def test_schema_without_loading(self):
        empty_sm = SchemaManager()
        field, conf = empty_sm.match_field("anything")
        assert field is None
        assert conf == 0.0
        assert empty_sm.get_all_field_names() == []
