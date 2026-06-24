"""Tests for V1 curation engines."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from geochem.core.database import Database
from geochem.core.memory import MemoryStore
from geochem.core.schema_manager import SchemaManager
from geochem.core.header_descriptions import load_header_descriptions
from geochem.curation import (
    AuditPackageBuilder,
    CostReporter,
    HeaderNormalizer,
    LearningEngine,
    MappingEngine,
    ReviewManager,
    RuleApplicationEngine,
    StandardizationPipeline,
    TargetHeaderBuilder,
    TeachingManager,
    TraceService,
    UnitConversionEngine,
    classify_field,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"
NOW = datetime.now().isoformat()


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "test.db")
    database.initialize()
    database.execute(
        "INSERT INTO projects (project_id, project_name, created_at, updated_at) VALUES (?, ?, ?, ?)",
        ("PRJ_001", "Test", NOW, NOW),
    )
    database.execute(
        "INSERT INTO articles (article_id, project_id, title, doi, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        ("ART_001", "PRJ_001", "Test Paper", "10.1234/test", "imported", NOW),
    )
    database.execute(
        "INSERT INTO resources (resource_id, article_id, resource_type, file_name, created_at) VALUES (?, ?, ?, ?, ?)",
        ("RES_001", "ART_001", "supplementary_excel", "data.xlsx", NOW),
    )
    database.execute(
        """INSERT INTO candidate_tables
        (table_id, article_id, resource_id, source_type, sheet_name, row_count, col_count, extract_method, confidence, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("TBL_001", "ART_001", "RES_001", "excel", "Sheet1", 1, 4, "llm", 0.9, NOW),
    )
    database.commit()
    yield database
    database.close()


def _schema(path="schema_ordovician.yaml"):
    sm = SchemaManager()
    sm.load_from_file(FIXTURES / path)
    return sm


def _insert_column(db, column_id, raw_name, unit=None):
    db.execute(
        """INSERT INTO candidate_columns
        (column_id, table_id, raw_name, unit_candidate, dtype, sample_values)
        VALUES (?, ?, ?, ?, ?, ?)""",
        (column_id, "TBL_001", raw_name, unit, "float", json.dumps([1.0])),
    )
    db.commit()


def test_header_normalizer_special_headers():
    normalizer = HeaderNormalizer()

    assert normalizer.normalize("δ15Nbulk\n‰").clean == "d15Nbulk"
    assert normalizer.normalize("δ15Nbulk\n‰").unit == "‰"
    assert normalizer.normalize("Li\nppm").clean == "Li"
    assert normalizer.normalize("Age（min）").normalized_key == "Age_min"
    assert normalizer.normalize("Depth/m").clean == "Depth"
    assert normalizer.normalize("Depth/m").unit == "m"
    assert normalizer.normalize("Fepy/Fehr").normalized_key == "Fepy_Fehr"
    assert normalizer.normalize("C/Nmol").normalized_key == "C_N_mol"
    assert normalizer.normalize("Dry density\ng/cm3").normalized_key == "DryDensity"
    assert normalizer.normalize("Dry density\ng/cm3").unit == "g/cm3"


def test_target_header_builder_preserves_display_units():
    builder = TargetHeaderBuilder(_schema())
    headers = builder.build(
        ["δ15Nbulk\n‰", "Li\nppm", "Al2O3(wt%)", "Fepy/Fehr", "Dry density\ng/cm3"],
        Path(__file__).parent.parent / "奥陶纪地化数据_headers.json",
    )
    by_field = {h.canonical_field: h for h in headers}

    assert by_field["δ15Nbulk"].display_header == "δ15Nbulk ‰"
    assert by_field["δ15Nbulk"].target_unit == "‰"
    assert by_field["Li"].display_header == "Li ppm"
    assert by_field["Li"].target_unit == "ppm"
    assert by_field["Al2O3"].display_header == "Al2O3(wt%)"
    assert by_field["Al2O3"].target_unit == "wt%"
    assert by_field["Fepy_Fehr"].field_group == "iron_speciation"
    assert by_field["DryDensity"].display_header == "Dry density g/cm3"


def test_header_description_loader_tolerates_unescaped_quotes():
    descriptions = load_header_descriptions(Path(__file__).parent.parent / "奥陶纪地化数据_headers.json")

    assert len(descriptions) == 156
    assert descriptions["Material"] == '测试物质如 "全岩"、"干酪根"、"碳酸盐岩"'


def test_classify_field_groups_cover_key_types():
    assert classify_field("SampleID") == "basic_info"
    assert classify_field("Latitude") == "location"
    assert classify_field("Age_min") == "stratigraphy_age"
    assert classify_field("Lithology") == "sample_context"
    assert classify_field("δ13Corg") == "isotope_organic"
    assert classify_field("Al2O3") == "major_elements"
    assert classify_field("Li") == "trace_elements"
    assert classify_field("REE_total") == "ree"
    assert classify_field("Fepy_Fehr") == "iron_speciation"
    assert classify_field("CIA") == "weathering_indices"


def test_mapping_engine_normalization_before_llm(db):
    _insert_column(db, "COL_001", "δ15Nbulk\n‰")
    _insert_column(db, "COL_002", "Age（min）")
    _insert_column(db, "COL_003", "Fepy/Fehr")

    engine = MappingEngine(_schema(), llm_client=None)
    suggestions = engine.map_table(db, "TBL_001", project_id="PRJ_001", use_llm=False)
    by_source = {s["source_field"]: s["target_field"] for s in suggestions}

    assert by_source["δ15Nbulk\n‰"] == "δ15Nbulk"
    assert by_source["Age（min）"] == "Age_min"
    assert by_source["Fepy/Fehr"] == "Fepy_Fehr"


def test_mapping_engine_uses_memory_before_llm(db, tmp_path):
    _insert_column(db, "COL_001", "CustomNa")
    memory_path = tmp_path / "mapping_rules.yaml"
    memory_path.write_text(
        """rules:
- rule_id: RULE_CUSTOM_NA
  source_field: CustomNa
  target_field: Na
  source_unit: wt%
  target_unit: wt%
  mapping_type: alias_mapping
  review_status: confirmed
  scope: project
  version: 1
  created_at: "2026-01-01T00:00:00"
  created_by: user
""",
        encoding="utf-8",
    )
    memory = MemoryStore(memory_path)
    memory.load()

    suggestions = MappingEngine(_schema(), memory_store=memory).map_table(db, "TBL_001", use_llm=False)

    assert suggestions[0]["target_field"] == "Na"
    assert suggestions[0]["confidence"] == pytest.approx(0.98)


def test_review_manager_decide_and_save_rule(db, tmp_path):
    db.execute(
        """INSERT INTO field_mappings
        (mapping_id, article_id, table_id, source_field, target_field, source_unit,
         target_unit, mapping_type, confidence, risk_level, requires_review, reason, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("MAP_001", "ART_001", "TBL_001", "Na2O", "Na", "wt%", "wt%", "oxide_to_element", 0.8, "high", 1, "test", NOW),
    )
    db.commit()
    manager = ReviewManager()
    assert manager.create_for_table(db, "TBL_001") == 1
    result = manager.decide(
        db,
        "REV_001",
        "accept",
        save_as_rule=True,
        memory_path=tmp_path / "mapping_rules.yaml",
    )

    assert result["review_status"] == "confirmed"
    assert result["rule_id"] == "RULE_001"
    assert (tmp_path / "mapping_rules.yaml").exists()
    assert (tmp_path / "mapping_memory.md").exists()


def test_unit_conversion_engine_supported_conversions():
    converter = UnitConversionEngine()

    value, calc = converter.convert(10000, "Li", "Li", "ppm", "wt%")
    assert value == pytest.approx(1.0)
    assert calc["formula"] == "wt% = ppm / 10000"

    value, calc = converter.convert(2.87, "Na2O", "Na", "wt%", "wt%")
    assert value == pytest.approx(2.12912959)
    assert calc["formula"] == "Na = Na2O * 0.741857"


def test_standardization_conservative_review_policy(db, tmp_path):
    db.execute(
        """INSERT INTO candidate_rows (row_id, table_id, row_index, raw_data)
        VALUES (?, ?, ?, ?)""",
        ("ROW_001", "TBL_001", 0, json.dumps({"SampleID": "S1", "Na2O": 2.87})),
    )
    db.execute(
        """INSERT INTO field_mappings
        (mapping_id, article_id, table_id, source_field, target_field, source_unit,
         target_unit, mapping_type, confidence, risk_level, requires_review, reason, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("MAP_001", "ART_001", "TBL_001", "Na2O", "Na", "wt%", "wt%", "oxide_to_element", 0.8, "high", 1, "test", NOW),
    )
    db.commit()

    pipeline = StandardizationPipeline()
    assert pipeline.standardize_table(db, tmp_path, "TBL_001") == 0

    manager = ReviewManager()
    manager.create_for_table(db, "TBL_001")
    manager.decide(db, "REV_001", "accept")

    assert pipeline.standardize_table(db, tmp_path, "TBL_001") == 1
    record = db.fetch_one("SELECT * FROM standardized_records")
    data = json.loads(record["data"])
    assert data["Na"] == pytest.approx(2.12912959)
    assert record["quality_grade"] == "C"
    assert db.fetch_one("SELECT * FROM calculation_records") is not None


def test_teaching_add_saves_event_and_patch_without_mutating_candidate_rows(db):
    db.execute(
        """INSERT INTO candidate_rows (row_id, table_id, row_index, raw_data)
        VALUES (?, ?, ?, ?)""",
        ("ROW_001", "TBL_001", 0, json.dumps({"SampleID": "S1", "Li": ""})),
    )
    db.commit()

    result = TeachingManager().add_event(
        db=db,
        project_id="PRJ_001",
        article_id="ART_001",
        table_id="TBL_001",
        sample_id="S1",
        target_header="Li ppm",
        target_field="Li",
        unit="ppm",
        value="42.1",
        source_type="appendix",
        evidence="Supplementary Table S1 column Li(ppm), row S1",
    )

    assert result["event_id"] == "TEACH_001"
    assert result["patch_id"] == "PATCH_001"
    raw = json.loads(db.fetch_one("SELECT raw_data FROM candidate_rows WHERE row_id = ?", ("ROW_001",))["raw_data"])
    assert raw["Li"] == ""
    assert db.fetch_one("SELECT * FROM teaching_events WHERE event_id = ?", ("TEACH_001",)) is not None
    assert db.fetch_one("SELECT * FROM record_patches WHERE patch_id = ?", ("PATCH_001",)) is not None


def test_learning_engine_local_rules_and_memory_sync(db, tmp_path):
    TeachingManager().add_event(
        db=db,
        project_id="PRJ_001",
        article_id="ART_001",
        target_header="Li ppm",
        target_field="Li",
        unit="ppm",
        value="42.1",
        source_type="appendix",
        evidence="Supplementary Table S1 uses Li(ppm)",
    )

    rules = LearningEngine().learn(db, "PRJ_001", "ART_001", tmp_path / "extraction_rules.yaml")

    assert rules[0]["rule_id"] == "LRULE_001"
    assert rules[0]["rule_type"] == "field_location"
    assert (tmp_path / "extraction_rules.yaml").exists()
    assert (tmp_path / "extraction_memory.md").exists()


def test_low_confidence_learned_rule_requires_review(db):
    rule = LearningEngine()._save_rule(db, "PRJ_001", "ART_001", {
        "target_field": "Li",
        "target_header": "Li ppm",
        "target_unit": "ppm",
        "rule_type": "field_location",
        "source_type": "appendix",
        "pattern": "Maybe Li appears in appendix notes",
        "confidence": 0.6,
        "risk_level": "medium",
    })
    db.commit()

    assert rule["review_status"] == "pending"
    review = db.fetch_one("SELECT * FROM review_items WHERE item_type = ?", ("learned_extraction_rule",))
    assert review["ai_suggestion"] == "LRULE_001"
    ReviewManager().decide(db, review["review_id"], "accept")
    stored = db.fetch_one("SELECT * FROM learned_extraction_rules WHERE rule_id = ?", ("LRULE_001",))
    assert stored["review_status"] == "confirmed"


def test_teach_apply_and_standardize_merge_patch_value(db, tmp_path):
    db.execute(
        """INSERT INTO candidate_rows (row_id, table_id, row_index, raw_data)
        VALUES (?, ?, ?, ?)""",
        ("ROW_001", "TBL_001", 0, json.dumps({"SampleID": "S1", "Li": ""})),
    )
    db.commit()
    manager = TeachingManager()
    manager.add_event(
        db=db,
        project_id="PRJ_001",
        article_id="ART_001",
        table_id="TBL_001",
        sample_id="S1",
        target_header="Li ppm",
        target_field="Li",
        unit="ppm",
        value="42.1",
        source_type="appendix",
        evidence="Supplementary Table S1 column Li(ppm), row S1",
    )
    LearningEngine().learn(db, "PRJ_001", "ART_001", tmp_path / "extraction_rules.yaml")

    assert RuleApplicationEngine().apply(db, "PRJ_001", "TBL_001") == 0
    assert StandardizationPipeline().standardize_table(db, tmp_path, "TBL_001") == 1
    record = db.fetch_one("SELECT * FROM standardized_records")
    data = json.loads(record["data"])
    assert data["Li"] == "42.1"
    assert record["quality_grade"] == "B"


def test_trace_and_cost_reporters(db, tmp_path):
    db.execute(
        """INSERT INTO candidate_rows (row_id, table_id, row_index, raw_data)
        VALUES (?, ?, ?, ?)""",
        ("ROW_001", "TBL_001", 0, json.dumps({"SampleID": "S1"})),
    )
    db.execute(
        """INSERT INTO standardized_records
        (record_id, article_id, table_id, row_id, data, source_file, source_table,
         source_row, original_fields, original_units, original_values, mapped_fields,
         mapped_units, mapping_rule_ids, calculation_ids, review_statuses,
         confidence_scores, quality_grade, processed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "STD_001", "ART_001", "TBL_001", "ROW_001", json.dumps({"SampleID": "S1"}),
            "data.xlsx", "Sheet1", 0, "{}", "{}", "{}", "{}", "{}", "{}", "{}",
            json.dumps({"SampleID": "auto"}), json.dumps({"SampleID": 1.0}), "A", NOW,
        ),
    )
    db.execute(
        """INSERT INTO llm_calls
        (call_id, project_id, article_id, agent_name, skill_name, model_provider,
         model_name, input_tokens, output_tokens, total_tokens, estimated_cost,
         started_at, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("LLM_000001", "PRJ_001", "ART_001", "Teaching Agent", "rule_learning",
         "xiaomi", "mimo-v2.5-pro", 100, 50, 150, 0.001, NOW, "success"),
    )
    db.commit()

    trace = TraceService().trace_table(db, "TBL_001")
    assert trace[0]["record_id"] == "STD_001"
    costs = CostReporter().report(db, "PRJ_001", "task")
    assert costs[0]["skill_name"] == "rule_learning"
    CostReporter().write_files(db, "PRJ_001", tmp_path)
    assert (tmp_path / "llm_cost_report.csv").exists()


def test_audit_package_records_export_job(db, tmp_path):
    data_file = tmp_path / "standardized_data.csv"
    data_file.write_text("SampleID\nS1\n", encoding="utf-8")
    db.execute(
        """INSERT INTO standardized_records
        (record_id, article_id, table_id, row_id, data, source_row, original_fields,
         original_units, original_values, mapped_fields, mapped_units, mapping_rule_ids,
         calculation_ids, review_statuses, confidence_scores, quality_grade, processed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "STD_001", "ART_001", "TBL_001", "ROW_001", json.dumps({"SampleID": "S1"}),
            0, "{}", "{}", "{}", "{}", "{}", "{}", "{}", "{}", "{}", "A", NOW,
        ),
    )
    db.commit()

    package_dir = AuditPackageBuilder().create(db, "PRJ_001", tmp_path, "TBL_001", data_file, "csv")

    assert (package_dir / "standardized_data.csv").exists()
    assert (package_dir / "trace_rows.csv").exists()
    assert db.fetch_one("SELECT * FROM export_jobs WHERE job_id = ?", ("EXP_001",))["status"] == "success"
