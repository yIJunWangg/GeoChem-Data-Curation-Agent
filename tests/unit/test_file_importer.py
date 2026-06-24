"""Tests for FileImporter."""

import json
from datetime import datetime
from pathlib import Path

import pytest

from geochem.core.database import Database
from geochem.core.exceptions import DuplicateFileError, FileImportError
from geochem.core.models import ResourceType
from geochem.ingestion.file_importer import FileImporter

NOW = datetime.now().isoformat()


@pytest.fixture
def db(tmp_path):
    db = Database(tmp_path / "test.db")
    db.initialize()
    db.execute(
        "INSERT INTO projects (project_id, project_name, created_at, updated_at) VALUES (?, ?, ?, ?)",
        ("PRJ_001", "Test Project", NOW, NOW),
    )
    db.commit()
    yield db
    db.close()


@pytest.fixture
def project_dir(tmp_path):
    d = tmp_path / "project"
    for sub in ("raw/articles", "raw/supplementary", "raw/figures"):
        (d / sub).mkdir(parents=True)
    return d


@pytest.fixture
def importer():
    return FileImporter()


def _make_file(tmp_path: Path, name: str, content: bytes = b"test content") -> Path:
    f = tmp_path / name
    f.write_bytes(content)
    return f


def test_import_pdf_creates_article_and_resource(db, project_dir, importer, tmp_path):
    pdf = _make_file(tmp_path, "paper.pdf")
    result = importer.import_file(db, project_dir, pdf)

    assert result.article_id.startswith("ART_")
    assert result.resource_id.startswith("RES_")
    assert result.resource_type == ResourceType.MAIN_PDF
    assert result.file_name == "paper.pdf"
    assert result.file_hash
    assert result.file_size > 0
    assert not result.is_duplicate

    # Verify DB records
    article = db.fetch_one("SELECT * FROM articles WHERE article_id = ?", (result.article_id,))
    assert article is not None
    assert article["project_id"] == "PRJ_001"

    resource = db.fetch_one("SELECT * FROM resources WHERE resource_id = ?", (result.resource_id,))
    assert resource is not None
    assert resource["resource_type"] == "main_pdf"


def test_import_excel_creates_resource(db, project_dir, importer, tmp_path):
    xlsx = _make_file(tmp_path, "data.xlsx")
    result = importer.import_file(db, project_dir, xlsx)

    assert result.resource_type == ResourceType.SUPPLEMENTARY_EXCEL
    assert result.local_path.startswith("articles/")
    assert "/source/" in result.local_path


def test_import_csv_creates_resource(db, project_dir, importer, tmp_path):
    csv = _make_file(tmp_path, "data.csv")
    result = importer.import_file(db, project_dir, csv)

    assert result.resource_type == ResourceType.SUPPLEMENTARY_CSV


def test_duplicate_detection_raises(db, project_dir, importer, tmp_path):
    f = _make_file(tmp_path, "dup.pdf", b"same content")
    importer.import_file(db, project_dir, f)

    f2 = _make_file(tmp_path, "dup_copy.pdf", b"same content")
    with pytest.raises(DuplicateFileError):
        importer.import_file(db, project_dir, f2)


def test_import_to_existing_article(db, project_dir, importer, tmp_path):
    # Create an article first
    db.execute(
        "INSERT INTO articles (article_id, project_id, title, status, created_at) VALUES (?, ?, ?, ?, ?)",
        ("ART_050", "PRJ_001", "Existing", "imported", NOW),
    )
    db.commit()

    f = _make_file(tmp_path, "supp.xlsx")
    result = importer.import_file(db, project_dir, f, article_id="ART_050")

    assert result.article_id == "ART_050"
    resource = db.fetch_one("SELECT * FROM resources WHERE resource_id = ?", (result.resource_id,))
    assert resource["article_id"] == "ART_050"
    assert resource["local_path"].startswith("articles/")


def test_file_not_found_raises(importer, db, project_dir):
    with pytest.raises(FileImportError, match="not found"):
        importer.import_file(db, project_dir, "/nonexistent/file.pdf")


def test_hash_consistency(db, project_dir, importer, tmp_path):
    f = _make_file(tmp_path, "stable.pdf", b"consistent content")
    r1 = importer.import_file(db, project_dir, f)
    assert r1.file_hash

    # Same file should give same hash (tested via duplicate detection)
    f2 = _make_file(tmp_path, "stable2.pdf", b"consistent content")
    with pytest.raises(DuplicateFileError):
        importer.import_file(db, project_dir, f2)


def test_batch_import_partial_failure(db, project_dir, importer, tmp_path):
    good = _make_file(tmp_path, "good.pdf", b"pdf content")
    bad = tmp_path / "missing.pdf"  # doesn't exist
    good2 = _make_file(tmp_path, "good2.xlsx", b"xlsx content")

    results = importer.import_batch(db, project_dir, [good, bad, good2])

    assert len(results) == 3
    assert results[0].status == "success"
    assert results[1].status == "error"
    assert results[2].status == "success"


def test_resource_type_override(db, project_dir, importer, tmp_path):
    f = _make_file(tmp_path, "weird.xyz")
    result = importer.import_file(
        db, project_dir, f, resource_type_override=ResourceType.SUPPLEMENTARY_EXCEL
    )
    assert result.resource_type == ResourceType.SUPPLEMENTARY_EXCEL


def test_name_collision_handling(db, project_dir, importer, tmp_path):
    # Use different filenames to avoid overwriting, then import both to same target dir
    f1 = _make_file(tmp_path, "collision.pdf", b"content_a")
    # Create a second file with same name in a subdirectory to avoid overwriting
    sub = tmp_path / "sub"
    sub.mkdir()
    f2 = _make_file(sub, "collision.pdf", b"content_b")

    r1 = importer.import_file(db, project_dir, f1)
    r2 = importer.import_file(db, project_dir, f2)

    assert r1.local_path != r2.local_path
    assert "collision" in r1.local_path
    assert "collision_2" in r2.local_path


def test_sequential_id_generation(db, project_dir, importer, tmp_path):
    f1 = _make_file(tmp_path, "a.pdf", b"content_a")
    f2 = _make_file(tmp_path, "b.pdf", b"content_b")

    r1 = importer.import_file(db, project_dir, f1)
    r2 = importer.import_file(db, project_dir, f2)

    assert r1.article_id == "ART_001"
    assert r2.article_id == "ART_002"
    assert r1.resource_id == "RES_001"
    assert r2.resource_id == "RES_002"


def test_create_article_from_metadata(db, importer):
    from geochem.ingestion.doi_service import ArticleMetadata

    metadata = ArticleMetadata(
        doi="10.1234/test",
        title="Test Paper",
        authors=["Smith, J.", "Doe, J."],
        year=2024,
        journal="Nature",
    )

    article_id = importer.create_article_from_metadata(db, "PRJ_001", metadata)

    row = db.fetch_one("SELECT * FROM articles WHERE article_id = ?", (article_id,))
    assert row is not None
    assert row["title"] == "Test Paper"
    assert row["year"] == 2024
    assert row["doi"] == "10.1234/test"
    assert row["article_dir"].startswith("articles/test_paper_10_1234_test")
    authors = json.loads(row["authors"])
    assert authors == ["Smith, J.", "Doe, J."]


def test_create_web_resource(db, project_dir, importer):
    # First create an article
    db.execute(
        "INSERT INTO articles (article_id, project_id, title, status, created_at) VALUES (?, ?, ?, ?, ?)",
        ("ART_001", "PRJ_001", "Test", "imported", NOW),
    )
    db.commit()

    content = "<html><body><p>Hello World</p></body></html>"
    resource_id = importer.create_web_resource(
        db, "ART_001", "https://example.com", content, project_dir
    )

    assert resource_id.startswith("RES_")
    resource = db.fetch_one("SELECT * FROM resources WHERE resource_id = ?", (resource_id,))
    assert resource["resource_type"] == "html_page"
    assert resource["source_url"] == "https://example.com"

    # Verify file was written
    file_path = Path(resource["local_path"])
    assert not file_path.is_absolute()
    assert (project_dir / file_path).exists()
    assert (project_dir / file_path).read_text() == content


def test_unsupported_extension_gets_other_type(db, project_dir, importer, tmp_path):
    f = _make_file(tmp_path, "data.xyz")
    result = importer.import_file(db, project_dir, f)
    assert result.resource_type == ResourceType.OTHER
