"""Tests for DOIService."""

from unittest.mock import MagicMock, patch

import pytest

from geochem.core.exceptions import DOIResolutionError, NetworkError
from geochem.ingestion.doi_service import ArticleMetadata, DOIService


@pytest.fixture
def service():
    return DOIService()


# --- normalize_doi ---

def test_normalize_doi_bare(service):
    assert service.normalize_doi("10.1234/test") == "10.1234/test"


def test_normalize_doi_url(service):
    assert service.normalize_doi("https://doi.org/10.1234/test") == "10.1234/test"


def test_normalize_doi_dx_url(service):
    assert service.normalize_doi("https://dx.doi.org/10.1234/test") == "10.1234/test"


def test_normalize_doi_prefix(service):
    assert service.normalize_doi("doi:10.1234/test") == "10.1234/test"


def test_normalize_doi_prefix_upper(service):
    assert service.normalize_doi("DOI: 10.1234/test") == "10.1234/test"


def test_normalize_doi_whitespace(service):
    assert service.normalize_doi("  10.1234/test  ") == "10.1234/test"


def test_doi_to_url(service):
    assert service.doi_to_url("10.1234/test") == "https://doi.org/10.1234/test"


def test_doi_to_url_with_prefix(service):
    assert service.doi_to_url("https://doi.org/10.1234/test") == "https://doi.org/10.1234/test"


# --- resolve (mocked) ---

CROSSREF_RESPONSE = {
    "message": {
        "title": ["A Test Paper on Geochemistry"],
        "author": [
            {"family": "Smith", "given": "John", "affiliation": []},
            {"family": "Doe", "given": "Jane", "affiliation": []},
        ],
        "published-print": {"date-parts": [[2024, 3, 15]]},
        "container-title": ["Nature Geoscience"],
        "URL": "https://doi.org/10.1234/test",
        "abstract": "<p>Test abstract</p>",
        "publisher": "Springer Nature",
        "ISSN": ["1752-0894"],
    }
}


@patch("geochem.ingestion.doi_service.requests.Session.get")
def test_resolve_success(mock_get, service):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = CROSSREF_RESPONSE
    mock_get.return_value = mock_resp

    meta = service.resolve("10.1234/test")

    assert meta.doi == "10.1234/test"
    assert meta.title == "A Test Paper on Geochemistry"
    assert meta.authors == ["Smith, John", "Doe, Jane"]
    assert meta.year == 2024
    assert meta.journal == "Nature Geoscience"
    assert meta.publisher == "Springer Nature"


@patch("geochem.ingestion.doi_service.requests.Session.get")
def test_resolve_not_found(mock_get, service):
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_get.return_value = mock_resp

    with pytest.raises(DOIResolutionError, match="not found"):
        service.resolve("10.9999/nonexistent")


@patch("geochem.ingestion.doi_service.requests.Session.get")
def test_resolve_network_error(mock_get, service):
    import requests
    mock_get.side_effect = requests.Timeout("Connection timed out")

    with pytest.raises(NetworkError, match="timeout"):
        service.resolve("10.1234/test")


@patch("geochem.ingestion.doi_service.requests.Session.get")
def test_parse_authors_no_given(mock_get, service):
    resp = {
        "message": {
            "title": ["Test"],
            "author": [{"family": "Chen"}],
            "published-online": {"date-parts": [[2023]]},
        }
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = resp
    mock_get.return_value = mock_resp

    meta = service.resolve("10.1234/test")
    assert meta.authors == ["Chen"]
    assert meta.year == 2023


@patch("geochem.ingestion.doi_service.requests.Session.get")
def test_parse_missing_optional_fields(mock_get, service):
    resp = {"message": {"title": []}}
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = resp
    mock_get.return_value = mock_resp

    meta = service.resolve("10.1234/test")
    assert meta.title == ""
    assert meta.authors == []
    assert meta.year is None
    assert meta.journal == ""
