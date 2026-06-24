"""Tests for WebReader."""

from unittest.mock import MagicMock, patch

import pytest

from geochem.core.exceptions import NetworkError, WebExtractionError
from geochem.ingestion.web_reader import WebReader


@pytest.fixture
def reader():
    return WebReader()


# --- _extract_text ---

def test_extract_text_basic_html(reader):
    html = "<html><body><p>Hello World</p><p>Second paragraph</p></body></html>"
    text = reader._extract_text(html)
    assert "Hello World" in text
    assert "Second paragraph" in text


def test_extract_text_strips_scripts(reader):
    html = """
    <html><body>
        <script>alert('hi')</script>
        <style>.red { color: red; }</style>
        <p>Visible content</p>
    </body></html>
    """
    text = reader._extract_text(html)
    assert "Visible content" in text
    assert "alert" not in text
    assert "color: red" not in text


def test_extract_text_strips_nav_footer(reader):
    html = """
    <html><body>
        <nav><a href="/">Home</a></nav>
        <main><p>Main article content here.</p></main>
        <footer>Copyright 2024</footer>
    </body></html>
    """
    text = reader._extract_text(html)
    assert "Main article content" in text
    # nav/footer content should be removed
    assert "Home" not in text
    assert "Copyright" not in text


def test_extract_text_empty_page(reader):
    html = "<html><body></body></html>"
    text = reader._extract_text(html)
    assert text.strip() == ""


def test_extract_text_article_tag(reader):
    html = """
    <html><body>
        <nav>Sidebar</nav>
        <article>
            <h1>Title</h1>
            <p>This is the main article text.</p>
        </article>
        <footer>Footer stuff</footer>
    </body></html>
    """
    text = reader._extract_text(html)
    assert "Title" in text
    assert "main article text" in text


# --- fetch_page (mocked) ---

@patch("geochem.ingestion.web_reader.requests.Session.get")
def test_fetch_page_success(mock_get, reader):
    html = "<html><body><p>Extracted content</p></body></html>"
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = html
    mock_get.return_value = mock_resp

    text = reader.fetch_page("https://example.com")
    assert "Extracted content" in text


@patch("geochem.ingestion.web_reader.requests.Session.get")
def test_fetch_page_network_error(mock_get, reader):
    import requests
    mock_get.side_effect = requests.Timeout("timeout")

    with pytest.raises(NetworkError, match="Timeout"):
        reader.fetch_page("https://example.com")


@patch("geochem.ingestion.web_reader.requests.Session.get")
def test_fetch_page_http_error(mock_get, reader):
    mock_resp = MagicMock()
    mock_resp.status_code = 403
    mock_get.return_value = mock_resp

    with pytest.raises(WebExtractionError, match="403"):
        reader.fetch_page("https://example.com")


@patch("geochem.ingestion.web_reader.requests.Session.get")
def test_fetch_page_payload_discovers_iframe_pdf(mock_get, reader):
    html = """
    <html><head><title>Viewer</title></head><body>
      <aside>plugin words should not become the selected asset</aside>
      <iframe src="/downloads/paper.pdf"></iframe>
    </body></html>
    """
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = html
    mock_resp.headers = {"content-type": "text/html"}
    mock_get.return_value = mock_resp

    payload = reader.fetch_page_payload("https://example.com/viewer")

    assert payload["pdf_url"] == "https://example.com/downloads/paper.pdf"
    assert payload["title"] == "Viewer"
