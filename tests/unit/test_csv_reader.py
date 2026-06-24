"""Tests for CsvReader."""

from pathlib import Path

import pytest

from geochem.extractors.csv_reader import CsvReader

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def reader():
    return CsvReader()


def test_read_geochem_sample(reader):
    """Read the real geochem CSV fixture."""
    results = reader.read(FIXTURES / "geochem_sample.csv")

    assert len(results) == 1
    content = results[0]

    assert content.source_type == "csv"
    assert content.row_count == 4
    assert len(content.headers) == 6
    assert "SiO₂" in content.headers  # Unicode subscript preserved as-is


def test_markdown_format(reader):
    """Verify markdown table output."""
    results = reader.read(FIXTURES / "geochem_sample.csv")
    text = results[0].text

    assert "| " in text
    assert "---" in text


def test_empty_csv(tmp_path, reader):
    """Empty CSV returns no content."""
    path = tmp_path / "empty.csv"
    path.write_text("col1,col2\n")
    results = reader.read(path)
    assert results == []


def test_nonexistent_file(reader):
    """Nonexistent file raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        reader.read(Path("/nonexistent/file.csv"))
