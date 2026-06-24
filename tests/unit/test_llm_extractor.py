"""Tests for LLMExtractor (header-only mapping + programmatic extraction)."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from geochem.core.models import LLMResponse
from geochem.core.schema_manager import SchemaManager
from geochem.extractors.base_reader import RawContent
from geochem.extractors.llm_extractor import LLMExtractor

FIXTURES = Path(__file__).parent.parent / "fixtures"

# Schema field names from schema_minimal.yaml
SID = "Sample_ID"  # The schema uses Sample_ID, not SampleID


@pytest.fixture
def schema_manager():
    sm = SchemaManager()
    sm.load_from_file(FIXTURES / "schema_minimal.yaml")
    return sm


@pytest.fixture
def mock_llm_client():
    return MagicMock()


@pytest.fixture
def extractor(mock_llm_client, schema_manager):
    return LLMExtractor(mock_llm_client, schema_manager)


def _make_content(headers=None, raw_rows=None, text=""):
    h = headers or ["SampleID", "SiO2", "Al2O3"]
    rows = raw_rows or [["S1", 71.08, 14.5]]
    return RawContent(
        text=text or "markdown",
        headers=h,
        row_count=len(rows),
        source_type="excel",
        raw_rows=rows,
    )


def _make_mapping_response(mapping):
    """Create mock LLM response. mapping format: {schema_field: table_header}."""
    return LLMResponse(
        content=json.dumps(mapping),
        model="test-model",
        provider="test",
        input_tokens=100,
        output_tokens=100,
    )


def test_mapping_extraction(extractor, mock_llm_client):
    """LLM returns mapping dict, program extracts rows."""
    mapping = {SID: "SampleID", "SiO2": "SiO2", "Al2O3": "Al2O3"}
    mock_llm_client.chat.return_value = _make_mapping_response(mapping)

    content = _make_content()
    result = extractor.extract(content, article_id="ART_001", resource_id="RES_001")

    assert result.status == "success"
    assert result.row_count == 1
    assert result.rows[0][SID] == "S1"
    assert result.rows[0]["SiO2"] == 71.08
    assert result.rows[0]["Al2O3"] == 14.5


def test_mapping_with_null_fields(extractor, mock_llm_client):
    """Schema fields with no matching header are excluded from extracted rows."""
    mapping = {SID: "SampleID", "SiO2": "SiO2", "Al2O3": None}
    mock_llm_client.chat.return_value = _make_mapping_response(mapping)

    content = _make_content()
    result = extractor.extract(content, article_id="ART_001", resource_id="RES_001")

    assert result.status == "success"
    assert "SiO2" in result.rows[0]
    assert "Al2O3" not in result.rows[0]


def test_mapping_with_renamed_fields(extractor, mock_llm_client):
    """LLM maps Unicode header to schema field name."""
    mapping = {SID: "SampleID", "SiO2": "SiO2", "Al2O3": "Al₂O₃"}
    mock_llm_client.chat.return_value = _make_mapping_response(mapping)

    content = _make_content(headers=["SampleID", "SiO2", "Al₂O₃"])
    result = extractor.extract(content, article_id="ART_001", resource_id="RES_001")

    assert result.status == "success"
    assert "Al2O3" in result.rows[0]


def test_multiple_rows_extraction(extractor, mock_llm_client):
    """Program extracts multiple rows from raw_rows."""
    mapping = {SID: "SampleID", "SiO2": "SiO2", "Al2O3": "Al2O3"}
    mock_llm_client.chat.return_value = _make_mapping_response(mapping)

    content = _make_content(
        raw_rows=[
            ["S1", 71.08, 14.5],
            ["S2", 68.5, 15.2],
            ["S3", 65.3, 16.1],
        ]
    )
    result = extractor.extract(content, article_id="ART_001", resource_id="RES_001")

    assert result.row_count == 3
    assert result.rows[0][SID] == "S1"
    assert result.rows[1][SID] == "S2"
    assert result.rows[2]["SiO2"] == 65.3


def test_rows_without_sampleid_skipped(extractor, mock_llm_client):
    """Rows with empty SampleID are skipped."""
    mapping = {SID: "SampleID", "SiO2": "SiO2"}
    mock_llm_client.chat.return_value = _make_mapping_response(mapping)

    content = _make_content(
        headers=["SampleID", "SiO2"],
        raw_rows=[
            ["S1", 71.08],
            ["", 68.5],       # No SampleID — skipped
            ["S3", 65.3],
            [None, 60.0],     # None SampleID — skipped
        ]
    )
    result = extractor.extract(content, article_id="ART_001", resource_id="RES_001")

    assert result.row_count == 2
    assert result.rows[0][SID] == "S1"
    assert result.rows[1][SID] == "S3"


def test_json_with_code_fences(extractor, mock_llm_client):
    """LLM response wrapped in ```json blocks is parsed correctly."""
    mapping = {SID: "SampleID", "SiO2": "SiO2", "Al2O3": "Al2O3"}
    response = LLMResponse(
        content=f'```json\n{json.dumps(mapping)}\n```',
        model="test", provider="test",
    )
    mock_llm_client.chat.return_value = response

    content = _make_content()
    result = extractor.extract(content, article_id="ART_001", resource_id="RES_001")

    assert result.status == "success"


def test_json_with_trailing_commas(extractor, mock_llm_client):
    """LLM response with trailing commas is repaired."""
    response = LLMResponse(
        content=json.dumps({SID: "SampleID", "SiO2": "SiO2", "Al2O3": "Al2O3"}).replace("}", ",}"),
        model="test", provider="test",
    )
    mock_llm_client.chat.return_value = response

    content = _make_content()
    result = extractor.extract(content, article_id="ART_001", resource_id="RES_001")

    assert result.status == "success"


def test_json_with_commentary(extractor, mock_llm_client):
    """LLM response with commentary before/after JSON is parsed."""
    mapping = {SID: "SampleID", "SiO2": "SiO2", "Al2O3": "Al2O3"}
    response = LLMResponse(
        content=f'Here is the mapping:\n{json.dumps(mapping)}\n\nNote: some fields unmapped.',
        model="test", provider="test",
    )
    mock_llm_client.chat.return_value = response

    content = _make_content()
    result = extractor.extract(content, article_id="ART_001", resource_id="RES_001")

    assert result.status == "success"


def test_empty_response_returns_error(extractor, mock_llm_client):
    """Empty LLM response results in error status."""
    mock_llm_client.chat.return_value = LLMResponse(content="", model="test", provider="test")

    content = _make_content()
    result = extractor.extract(content, article_id="ART_001", resource_id="RES_001")

    assert result.status == "error"
    assert result.row_count == 0


def test_no_schema_fields_returns_error(extractor, mock_llm_client, schema_manager):
    """Empty schema returns error."""
    sm = SchemaManager()
    ext = LLMExtractor(mock_llm_client, sm)

    content = _make_content()
    result = ext.extract(content, article_id="ART_001", resource_id="RES_001")

    assert result.status == "error"
    assert "No schema fields" in result.error


def test_confidence_computation(extractor):
    """Confidence is computed based on fill rate."""
    rows = [
        {SID: "S1", "SiO2": 71.08, "Al2O3": 14.5},
        {SID: "S2", "SiO2": 68.5, "Al2O3": None},
    ]
    fields = [SID, "SiO2", "Al2O3"]

    conf = extractor._compute_confidence(rows, fields)
    assert 0.0 < conf < 1.0
    # 5 out of 6 cells filled, 2/2 SampleID present
    # (5/6 * 0.6) + (2/2 * 0.4) = 0.5 + 0.4 = 0.9
    assert abs(conf - 0.9) < 0.01


def test_confidence_empty_rows(extractor):
    """Empty rows give 0 confidence."""
    assert extractor._compute_confidence([], [SID]) == 0.0


def test_build_messages_no_row_data(extractor):
    """Prompt contains headers and schema fields, but NO row data."""
    content = _make_content(
        headers=["SampleID", "SiO2"],
        raw_rows=[["S1", 71.08]],
    )
    messages = extractor._build_messages(content, [SID, "SiO2"], {"title": "Test"})

    user_msg = messages[1]["content"]
    # Should contain headers and schema fields
    assert "SampleID" in user_msg
    assert "SiO2" in user_msg
    # Should NOT contain row data
    assert "71.08" not in user_msg
    assert "S1" not in user_msg


def test_build_messages_includes_units(extractor):
    """Prompt includes header units."""
    content = RawContent(
        text="markdown",
        headers=["SampleID", "SiO2"],
        row_count=1,
        source_type="excel",
        header_units={"SiO2": "wt%"},
        raw_rows=[["S1", 71.08]],
    )
    messages = extractor._build_messages(content, [SID, "SiO2"], None)
    user_msg = messages[1]["content"]

    assert "wt%" in user_msg


def test_matched_fields_from_mapping(extractor, mock_llm_client):
    """matched_fields in result reflects the LLM mapping."""
    mapping = {SID: "SampleID", "SiO2": "SiO2", "Al2O3": None}
    mock_llm_client.chat.return_value = _make_mapping_response(mapping)

    content = _make_content()
    result = extractor.extract(content, article_id="ART_001", resource_id="RES_001")

    # matched_fields should have the mapped headers
    assert "SampleID" in result.matched_fields
    assert "SiO2" in result.matched_fields
    # Al2O3 mapped to None → should not be in matched_fields
    assert "Al2O3" not in result.matched_fields


def test_no_raw_rows_returns_error(extractor, mock_llm_client):
    """Content without raw_rows returns error (no data to extract)."""
    mapping = {SID: "SampleID", "SiO2": "SiO2"}
    mock_llm_client.chat.return_value = _make_mapping_response(mapping)

    content = RawContent(
        text="markdown",
        headers=["SampleID", "SiO2"],
        row_count=1,
        source_type="excel",
        raw_rows=[],  # No raw data
    )
    result = extractor.extract(content, article_id="ART_001", resource_id="RES_001")

    assert result.status == "error"
