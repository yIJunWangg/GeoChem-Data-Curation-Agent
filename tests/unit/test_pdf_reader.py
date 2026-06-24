"""Tests for PdfReader."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from geochem.extractors.pdf_reader import PdfReader


@pytest.fixture
def reader():
    return PdfReader()


def test_nonexistent_file(reader):
    with pytest.raises(FileNotFoundError):
        reader.read(Path("/nonexistent/file.pdf"))


def test_extract_tables(reader, tmp_path):
    """Mock pdfplumber to test table extraction."""
    pdf_path = tmp_path / "test.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")

    # Create mock pdfplumber module
    mock_pdfplumber = MagicMock()
    mock_page = MagicMock()
    mock_page.extract_tables.return_value = [
        [["SampleID", "SiO2", "Al2O3"], ["S1", "71.08", "14.5"], ["S2", "68.5", "15.2"]],
    ]
    mock_pdf = MagicMock()
    mock_pdf.pages = [mock_page]
    mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
    mock_pdf.__exit__ = MagicMock(return_value=False)
    mock_pdfplumber.open.return_value = mock_pdf

    # Inject mock into sys.modules before the method imports it
    sys.modules["pdfplumber"] = mock_pdfplumber
    try:
        results = reader.read(pdf_path)
    finally:
        del sys.modules["pdfplumber"]

    assert len(results) == 1
    assert results[0].source_type == "pdf"
    assert results[0].row_count == 2
    assert "SampleID" in results[0].headers


def test_no_tables_falls_back_to_text(reader, tmp_path):
    """When no tables found, fall back to text extraction."""
    pdf_path = tmp_path / "test.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")

    # Mock pdfplumber (no tables)
    mock_pdfplumber = MagicMock()
    mock_page = MagicMock()
    mock_page.extract_tables.return_value = []
    mock_pdf = MagicMock()
    mock_pdf.pages = [mock_page]
    mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
    mock_pdf.__exit__ = MagicMock(return_value=False)
    mock_pdfplumber.open.return_value = mock_pdf

    # Mock fitz (text fallback)
    mock_fitz = MagicMock()
    mock_doc = MagicMock()
    mock_doc.__len__ = MagicMock(return_value=1)
    mock_doc_page = MagicMock()
    mock_doc_page.get_text.return_value = "This is a long enough text for extraction " * 5
    mock_doc.__getitem__ = MagicMock(return_value=mock_doc_page)
    mock_fitz.open.return_value = mock_doc

    sys.modules["pdfplumber"] = mock_pdfplumber
    sys.modules["fitz"] = mock_fitz
    try:
        results = reader.read(pdf_path)
    finally:
        sys.modules.pop("pdfplumber", None)
        sys.modules.pop("fitz", None)

    assert len(results) == 1
    assert results[0].source_type == "pdf"
    assert "text" in results[0].metadata.get("extraction_method", "")
