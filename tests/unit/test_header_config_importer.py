"""Tests for header-only CSV/XLSX imports used by the Web application."""

from openpyxl import Workbook

from geochem.curation.header_config_importer import HeaderConfigImporter


def test_reads_header_only_csv(tmp_path):
    source = tmp_path / "headers.csv"
    source.write_text("SampleID,Li ppm,Al2O3(wt%)\n", encoding="utf-8")

    headers, samples = HeaderConfigImporter().read_headers(source)

    assert headers == ["SampleID", "Li ppm", "Al2O3(wt%)"]
    assert samples == []


def test_reads_excel_headers_and_samples(tmp_path):
    source = tmp_path / "headers.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["SampleID", "Age（min）", "Al2O3(wt%)", "Li\nppm", "δ15Nbulk\n‰"])
    sheet.append(["S1", 450, 12.3, 42.1, 3.2])
    workbook.save(source)

    headers, samples = HeaderConfigImporter().read_headers(source)

    assert headers == ["SampleID", "Age（min）", "Al2O3(wt%)", "Li\nppm", "δ15Nbulk\n‰"]
    assert samples == [["S1", 450, 12.3, 42.1, 3.2]]
