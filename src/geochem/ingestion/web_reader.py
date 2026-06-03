"""Web page content extraction for LLM consumption."""

from __future__ import annotations

import requests
from bs4 import BeautifulSoup, Tag
from urllib.parse import urljoin

from ..core.exceptions import NetworkError, WebExtractionError
from ..core.logging_config import get_logger

logger = get_logger("ingestion.web_reader")

REQUEST_TIMEOUT = 30


class WebReader:
    """Fetch and extract readable text from a web page."""

    def __init__(self):
        self._session: requests.Session | None = None

    def _get_session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update({
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/120.0.0.0 Safari/537.36",
            })
        return self._session

    def fetch_page(self, url: str) -> str:
        """Fetch a URL and return extracted readable text.

        Raises:
            NetworkError: Cannot reach the URL.
            WebExtractionError: Content could not be extracted.
        """
        session = self._get_session()

        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT)
        except requests.Timeout:
            raise NetworkError(f"Timeout fetching: {url}")
        except requests.ConnectionError as e:
            raise NetworkError(f"Cannot connect to {url}: {e}")

        if resp.status_code != 200:
            raise WebExtractionError(f"HTTP {resp.status_code} for {url}")

        html = resp.text
        if not html or len(html) < 50:
            raise WebExtractionError(f"Empty or minimal content from {url}")

        text = self._extract_text(html, url)
        if not text or len(text) < 20:
            logger.warning(f"Very little text extracted from {url} ({len(text)} chars)")

        return text

    def fetch_page_payload(self, url: str) -> dict:
        """Fetch a URL and return text plus discovered article assets.

        Journal and PDF-viewer pages often wrap the real article PDF in an
        iframe/embed/object. Returning the discovered PDF URL lets the workflow
        save the actual article asset instead of sidebar or plugin text.
        """
        session = self._get_session()
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT)
        except requests.Timeout:
            raise NetworkError(f"Timeout fetching: {url}")
        except requests.ConnectionError as e:
            raise NetworkError(f"Cannot connect to {url}: {e}")

        if resp.status_code != 200:
            raise WebExtractionError(f"HTTP {resp.status_code} for {url}")

        content_type = resp.headers.get("content-type", "").lower()
        if "application/pdf" in content_type or url.lower().split("?", 1)[0].endswith(".pdf"):
            return {
                "text": "",
                "html": "",
                "pdf_url": url,
                "title": "",
                "content_type": "pdf",
            }

        html = resp.text
        if not html or len(html) < 50:
            raise WebExtractionError(f"Empty or minimal content from {url}")

        soup = BeautifulSoup(html, "html.parser")
        pdf_url = self._discover_pdf_url(soup, url)
        title = ""
        if soup.title and soup.title.string:
            title = soup.title.string.strip()
        text = self._extract_text(html, url)
        return {
            "text": text,
            "html": html,
            "pdf_url": pdf_url,
            "title": title,
            "content_type": content_type,
        }

    def _extract_text(self, html: str, url: str = "") -> str:
        """Extract readable text from raw HTML."""
        soup = BeautifulSoup(html, "html.parser")

        # Remove boilerplate tags
        for tag_name in ("script", "style", "nav", "footer", "header", "aside", "noscript"):
            for tag in soup.find_all(tag_name):
                tag.decompose()

        # Try to find main content area
        main_content = None
        for selector in ("article", "main", '[role="main"]'):
            main_content = soup.select_one(selector)
            if main_content:
                break

        # Fallback: find largest div with substantial text
        if not main_content:
            main_content = self._find_largest_text_div(soup)

        if not main_content:
            main_content = soup.body or soup

        # Convert to text with paragraph separation
        text = self._element_to_text(main_content)
        return text.strip()

    def _discover_pdf_url(self, soup: BeautifulSoup, base_url: str) -> str:
        """Find the most likely article PDF URL in a publisher/PDF-viewer page."""
        candidates = []
        for selector, attr in (
            ("iframe[src]", "src"),
            ("embed[src]", "src"),
            ("object[data]", "data"),
            ("a[href]", "href"),
            ("meta[name='citation_pdf_url'][content]", "content"),
            ("meta[property='og:url'][content]", "content"),
        ):
            for tag in soup.select(selector):
                value = tag.get(attr)
                if not value:
                    continue
                absolute = urljoin(base_url, value)
                lowered = absolute.lower()
                score = 0
                if ".pdf" in lowered or "/pdf" in lowered or "download" in lowered:
                    score += 5
                if tag.name in {"iframe", "embed", "object"}:
                    score += 3
                if "sci-hub" in lowered and ".pdf" in lowered:
                    score += 2
                if score:
                    candidates.append((score, absolute))
        candidates.sort(reverse=True)
        return candidates[0][1] if candidates else ""

    def _find_largest_text_div(self, soup: BeautifulSoup) -> Tag | None:
        """Find the div with the most text content."""
        best_div = None
        best_len = 0

        for div in soup.find_all("div"):
            text = div.get_text(strip=True)
            if len(text) > best_len:
                best_len = len(text)
                best_div = div

        return best_div if best_len > 100 else None

    def _element_to_text(self, element: Tag) -> str:
        """Convert an HTML element to readable plain text."""
        lines = []
        for child in element.descendants:
            if isinstance(child, str):
                text = child.strip()
                if text:
                    lines.append(text)
            elif isinstance(child, Tag) and child.name in ("p", "br", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li"):
                lines.append("")  # blank line for separation

        # Join and clean up multiple blank lines
        raw = "\n".join(lines)
        cleaned = []
        prev_blank = False
        for line in raw.split("\n"):
            is_blank = not line.strip()
            if is_blank and prev_blank:
                continue
            cleaned.append(line)
            prev_blank = is_blank

        return "\n".join(cleaned)
