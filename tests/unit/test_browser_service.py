"""Tests for BrowserService."""

from unittest.mock import MagicMock, patch

import pytest

from geochem.ingestion.browser_service import BrowserService
from geochem.ingestion.doi_service import DOIService
from geochem.ingestion.web_reader import WebReader


@pytest.fixture
def doi_service():
    svc = DOIService()
    return svc


@pytest.fixture
def web_reader():
    return WebReader()


@pytest.fixture
def browser_svc(doi_service, web_reader):
    return BrowserService(doi_service=doi_service, web_reader=web_reader)


def test_open_url(browser_svc):
    with patch("geochem.ingestion.browser_service.webbrowser.open") as mock_open:
        browser_svc.open_url("https://example.com")
        mock_open.assert_called_once_with("https://example.com")


def test_resolve_and_open(browser_svc):
    with patch("geochem.ingestion.browser_service.webbrowser.open") as mock_open:
        url = browser_svc.resolve_and_open("10.1234/test")
        assert url == "https://doi.org/10.1234/test"
        mock_open.assert_called_once_with("https://doi.org/10.1234/test")


def test_read_after_auth_success(browser_svc):
    browser_svc.web_reader = MagicMock()
    browser_svc.web_reader.fetch_page.return_value = "Page content here"

    content = browser_svc.read_after_auth("https://example.com")
    assert content == "Page content here"
    browser_svc.web_reader.fetch_page.assert_called_once_with("https://example.com")


def test_read_after_auth_failure_returns_empty(browser_svc):
    browser_svc.web_reader = MagicMock()
    browser_svc.web_reader.fetch_page.side_effect = Exception("network error")

    content = browser_svc.read_after_auth("https://example.com")
    assert content == ""


def test_read_payload_after_auth_returns_pdf_candidate(browser_svc):
    browser_svc.web_reader = MagicMock()
    browser_svc.web_reader.fetch_page_payload.return_value = {
        "text": "article",
        "html": "<html></html>",
        "pdf_url": "https://example.com/paper.pdf",
    }

    payload = browser_svc.read_payload_after_auth("https://example.com")

    assert payload["pdf_url"] == "https://example.com/paper.pdf"
    browser_svc.web_reader.fetch_page_payload.assert_called_once_with("https://example.com")


def test_full_doi_flow_with_callback(browser_svc):
    browser_svc.doi_service = MagicMock()
    browser_svc.doi_service.doi_to_url.return_value = "https://doi.org/10.1234/test"
    browser_svc.web_reader = MagicMock()
    browser_svc.web_reader.fetch_page.return_value = "Article content"

    with patch("geochem.ingestion.browser_service.webbrowser.open"):
        callback = MagicMock(return_value=True)
        content = browser_svc.full_doi_flow("10.1234/test", confirm_callback=callback)

    assert content == "Article content"
    callback.assert_called_once()


def test_full_doi_flow_callback_declined(browser_svc):
    browser_svc.doi_service = MagicMock()
    browser_svc.doi_service.doi_to_url.return_value = "https://doi.org/10.1234/test"

    with patch("geochem.ingestion.browser_service.webbrowser.open"):
        callback = MagicMock(return_value=False)
        content = browser_svc.full_doi_flow("10.1234/test", confirm_callback=callback)

    assert content == ""
