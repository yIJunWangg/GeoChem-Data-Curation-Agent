"""Tests for ResultPersistence."""

import json
from datetime import datetime

import pytest

from geochem.core.database import Database
from geochem.extractors.result_persistence import ResultPersistence

NOW = datetime.now().isoformat()


@pytest.fixture
def db(tmp_path):
    db = Database(tmp_path / "test.db")
    db.initialize()
    db.execute(
        "INSERT INTO projects (project_id, project_name, created_at, updated_at) VALUES (?, ?, ?, ?)",
        ("PRJ_001", "Test", NOW, NOW),
    )
    db.execute(
        "INSERT INTO articles (article_id, project_id, title, status, created_at) VALUES (?, ?, ?, ?, ?)",
        ("ART_001", "PRJ_001", "Test Paper", "imported", NOW),
    )
    db.execute(
        "INSERT INTO resources (resource_id, article_id, resource_type, file_name, created_at) VALUES (?, ?, ?, ?, ?)",
        ("RES_001", "ART_001", "supplementary_excel", "data.xlsx", NOW),
    )
    db.commit()
    yield db
    db.close()


@pytest.fixture
def persistence():
    return ResultPersistence()


def test_save_creates_tables_and_rows(db, persistence):
    """Save creates candidate_tables, columns, and rows."""
    rows = [
        {"SampleID": "S1", "SiO2": 71.08, "Al2O3": 14.5},
        {"SampleID": "S2", "SiO2": 68.5, "Al2O3": 15.2},
    ]
    headers = ["SampleID", "SiO2", "Al2O3"]
    matched = {"SampleID": "SampleID", "SiO2": "SiO2", "Al2O3": "Al2O3"}

    table_id = persistence.save(
        db, "ART_001", "RES_001", "markdown text",
        rows, headers, matched, source_type="excel",
    )

    assert table_id.startswith("TBL_")

    # Verify candidate_table
    tbl = db.fetch_one("SELECT * FROM candidate_tables WHERE table_id = ?", (table_id,))
    assert tbl is not None
    assert tbl["article_id"] == "ART_001"
    assert tbl["row_count"] == 2
    assert tbl["col_count"] == 3
    assert tbl["extract_method"] == "llm"

    # Verify columns
    cols = db.fetch_all("SELECT * FROM candidate_columns WHERE table_id = ?", (table_id,))
    assert len(cols) == 3

    # Verify rows
    db_rows = db.fetch_all("SELECT * FROM candidate_rows WHERE table_id = ?", (table_id,))
    assert len(db_rows) == 2
    first_row = json.loads(db_rows[0]["raw_data"])
    assert first_row["SampleID"] == "S1"


def test_column_sample_values(db, persistence):
    """Sample values are stored in candidate_columns."""
    rows = [
        {"SampleID": "S1", "SiO2": 71.08},
        {"SampleID": "S2", "SiO2": 68.5},
        {"SampleID": "S3", "SiO2": 70.0},
    ]

    table_id = persistence.save(
        db, "ART_001", "RES_001", "", rows, ["SampleID", "SiO2"],
        {"SampleID": "SampleID", "SiO2": "SiO2"},
    )

    col = db.fetch_one(
        "SELECT * FROM candidate_columns WHERE table_id = ? AND raw_name = 'SiO2'",
        (table_id,),
    )
    samples = json.loads(col["sample_values"])
    assert 71.08 in samples
    assert col["dtype"] == "float"


def test_row_raw_data_is_json(db, persistence):
    """Row data is stored as valid JSON."""
    rows = [{"SampleID": "S1", "SiO2": 71.08, "note": "test value"}]

    table_id = persistence.save(
        db, "ART_001", "RES_001", "", rows, ["SampleID", "SiO2", "note"], {},
    )

    row = db.fetch_one("SELECT * FROM candidate_rows WHERE table_id = ?", (table_id,))
    data = json.loads(row["raw_data"])
    assert data["SampleID"] == "S1"
    assert data["note"] == "test value"


def test_sequential_id_generation(db, persistence):
    """IDs are generated sequentially."""
    rows = [{"SampleID": "S1"}]

    t1 = persistence.save(db, "ART_001", "RES_001", "", rows, ["SampleID"], {})
    t2 = persistence.save(db, "ART_001", "RES_001", "", rows, ["SampleID"], {})

    assert t1 == "TBL_001"
    assert t2 == "TBL_002"


def test_dtype_inference(db, persistence):
    """Dtype is inferred correctly from sample values."""
    rows = [
        {"name": "S1", "value": "71.08", "note": "siliceous"},
        {"name": "S2", "value": "68.5", "note": "calcareous"},
    ]

    persistence.save(
        db, "ART_001", "RES_001", "", rows, ["name", "value", "note"], {},
    )

    name_col = db.fetch_one(
        "SELECT * FROM candidate_columns WHERE raw_name = 'name'", ()
    )
    value_col = db.fetch_one(
        "SELECT * FROM candidate_columns WHERE raw_name = 'value'", ()
    )
    note_col = db.fetch_one(
        "SELECT * FROM candidate_columns WHERE raw_name = 'note'", ()
    )

    assert name_col["dtype"] == "string"
    assert value_col["dtype"] == "float"
    assert note_col["dtype"] == "string"
