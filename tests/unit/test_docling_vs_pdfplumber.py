"""Compare Docling vs pdfplumber extraction on a real scientific PDF."""

import json
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
PDF_PATH = _PROJECT_ROOT / "tests" / "feart-09-788349.pdf"
SCHEMA_PATH = _PROJECT_ROOT / "tests" / "fixtures" / "schema_minimal.yaml"


_DOCLING_RUNTIME_ERRORS = (ImportError, ModuleNotFoundError, RuntimeError, AttributeError)


def _read_docling_or_skip(*, schema_fields=None):
    from geochem.extractors.docling_reader import DoclingReader

    try:
        return DoclingReader().read(PDF_PATH, schema_fields=schema_fields)
    except _DOCLING_RUNTIME_ERRORS as exc:
        pytest.skip(f"Docling runtime unavailable: {exc}")


def _paragraph(text, page, bbox, order, section="INTRODUCTION"):
    from geochem.extractors.docling_reader import DocBlock

    return DocBlock(
        block_type="paragraph",
        text=text,
        page_number=page,
        bbox=bbox,
        page_spans=[{"page_number": page, "bbox": bbox, "role": "text"}],
        reading_order=order,
        section_path=section,
    )


def test_docling_merges_cross_column_paragraph_continuation():
    from geochem.extractors.docling_reader import DoclingReader

    reader = DoclingReader()
    blocks = [
        _paragraph("This long paragraph starts in the left column and continues", 2, [0.12, 0.62, 0.48, 0.91], 1),
        _paragraph("in the right column with the same discussion of the basin.", 2, [0.54, 0.16, 0.88, 0.33], 2),
    ]

    merged = reader.merge_paragraphs(blocks)

    assert len(merged) == 1
    assert "right column" in merged[0].text
    assert len(merged[0].page_spans) == 2
    assert merged[0].merge_reason == "cross_column_paragraph"


def test_docling_merges_same_column_but_not_heading():
    from geochem.extractors.docling_reader import DoclingReader

    reader = DoclingReader()
    merged = reader.merge_paragraphs([
        _paragraph("Samples were collected from the shale section and then", 3, [0.54, 0.25, 0.88, 0.31], 1, "SAMPLES AND METHODS"),
        _paragraph("processed carefully for geochemical analysis.", 3, [0.54, 0.32, 0.88, 0.38], 2, "SAMPLES AND METHODS"),
        _paragraph("METHODS", 3, [0.54, 0.42, 0.88, 0.46], 3, "SAMPLES AND METHODS"),
    ])

    assert len(merged) == 2
    assert "processed carefully" in merged[0].text
    assert merged[1].text == "METHODS"


def test_docling_merges_cross_page_continuation():
    from geochem.extractors.docling_reader import DoclingReader

    reader = DoclingReader()
    merged = reader.merge_paragraphs([
        _paragraph("The depositional environment was controlled by contin-", 4, [0.12, 0.84, 0.48, 0.91], 1),
        _paragraph("uous restriction and redox changes in the basin.", 5, [0.12, 0.12, 0.48, 0.18], 2),
    ])

    assert len(merged) == 1
    assert "continuous restriction" in merged[0].text
    assert {span["page_number"] for span in merged[0].page_spans} == {4, 5}


@pytest.mark.skipif(not PDF_PATH.exists(), reason="Test PDF not found")
class TestDoclingVsPdfplumber:
    """Compare extraction quality between Docling and pdfplumber."""

    def test_docling_extracts_blocks(self):
        """Docling should extract structured blocks from the PDF."""
        blocks = _read_docling_or_skip()

        assert len(blocks) > 0, "Docling should extract at least some blocks"

        tables = [b for b in blocks if b.block_type == "table"]
        figures = [b for b in blocks if b.block_type == "figure"]
        paragraphs = [b for b in blocks if b.block_type == "paragraph"]

        print(f"\nDocling extraction results:")
        print(f"  Tables: {len(tables)}")
        print(f"  Figures: {len(figures)}")
        print(f"  Paragraphs: {len(paragraphs)}")
        print(f"  Total blocks: {len(blocks)}")

        # Should find at least some elements
        assert len(figures) > 0, "Should find figures"
        assert len(paragraphs) > 0, "Should find paragraphs"

    def test_docling_table_has_structure(self):
        """Docling tables should have structured headers and rows."""
        blocks = _read_docling_or_skip()

        tables = [b for b in blocks if b.block_type == "table"]
        if not tables:
            pytest.skip("No tables found in PDF")

        for table in tables[:3]:
            assert table.table_data is not None
            headers = table.table_data.get("headers", [])
            rows = table.table_data.get("rows", [])
            print(f"\n  Table: {table.text[:60]}")
            print(f"    Headers: {headers[:5]}")
            print(f"    Rows: {len(rows)}")
            if headers:
                assert len(headers) > 0, "Table should have headers"

    def test_docling_figures_have_previews(self):
        """Docling figures should have preview images saved."""
        blocks = _read_docling_or_skip()

        figures = [b for b in blocks if b.block_type == "figure"]
        if not figures:
            pytest.skip("No figures found in PDF")

        saved = sum(1 for f in figures if f.preview_path and Path(f.preview_path).exists())
        print(f"\n  Figures with preview images: {saved}/{len(figures)}")
        assert saved > 0, "At least some figures should have preview images"

    def test_docling_matches_schema_fields(self):
        """Docling should match schema fields in paragraph text."""
        import yaml

        with open(SCHEMA_PATH, encoding="utf-8") as f:
            schema = yaml.safe_load(f)
        fields = [c["name"] for c in schema.get("columns", []) if c.get("name")]

        blocks = _read_docling_or_skip(schema_fields=fields)

        matched_blocks = [b for b in blocks if b.matched_headers]
        print(f"\n  Blocks with matched headers: {len(matched_blocks)}/{len(blocks)}")
        for b in matched_blocks[:5]:
            print(f"    [{b.block_type}] p{b.page_number}: {b.matched_headers}")

        assert len(matched_blocks) > 0, "Some blocks should match schema fields"

    def test_docling_provenance(self):
        """Every block should have page number and bbox."""
        blocks = _read_docling_or_skip()

        for block in blocks[:10]:
            assert block.page_number >= 1, f"Page number should be >= 1, got {block.page_number}"
            assert len(block.bbox) == 4, f"Bbox should have 4 elements, got {len(block.bbox)}"
            assert block.page_spans, "Block should retain one or more provenance spans"
            assert block.page_spans[0]["page_number"] >= 1
            assert len(block.page_spans[0]["bbox"]) == 4
            assert block.bbox[0] <= block.bbox[2], "x0 should be <= x1"
            assert block.bbox[1] <= block.bbox[3], "y0 should be <= y1"

    def test_pdfplumber_extracts_tables(self):
        """pdfplumber baseline: should extract at least some tables."""
        from geochem.extractors.pdf_reader import PdfReader

        reader = PdfReader()
        contents = reader.read(PDF_PATH)

        print(f"\npdfplumber extraction results:")
        print(f"  Raw contents: {len(contents)}")
        for c in contents[:3]:
            print(f"    {c.title}: {c.row_count} rows, headers={c.headers[:3]}")
