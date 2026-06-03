"""DOI metadata resolution via CrossRef API."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import requests

from ..core.exceptions import DOIResolutionError, NetworkError
from ..core.logging_config import get_logger

logger = get_logger("ingestion.doi_service")

CROSSREF_API = "https://api.crossref.org/works/{doi}"
REQUEST_TIMEOUT = 15


@dataclass
class ArticleMetadata:
    """Metadata resolved from a DOI."""
    doi: str
    title: str = ""
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    journal: str = ""
    url: str = ""
    abstract: str = ""
    publisher: str = ""
    issn: str = ""


class DOIService:
    """Resolve DOIs to article metadata using CrossRef."""

    def __init__(self):
        self._session: requests.Session | None = None

    def _get_session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update({
                "User-Agent": "GeoChemAgent/0.1 (mailto:geochem-agent@example.com)",
            })
        return self._session

    def normalize_doi(self, doi: str) -> str:
        """Normalize a DOI string to bare form.

        Handles: 'https://doi.org/10.1234/test', 'doi:10.1234/test',
                 'DOI: 10.1234/test', '10.1234/test'
        """
        doi = doi.strip()
        # Remove URL prefix
        doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi, flags=re.IGNORECASE)
        # Remove 'doi:' prefix
        doi = re.sub(r"^doi:\s*", "", doi, flags=re.IGNORECASE)
        return doi.strip()

    def doi_to_url(self, doi: str) -> str:
        """Convert a bare DOI to its canonical https://doi.org/ URL."""
        return f"https://doi.org/{self.normalize_doi(doi)}"

    def resolve(self, doi: str) -> ArticleMetadata:
        """Resolve a DOI to article metadata via CrossRef.

        Raises:
            DOIResolutionError: DOI not found or invalid.
            NetworkError: Cannot reach CrossRef API.
        """
        bare_doi = self.normalize_doi(doi)
        logger.info(f"Resolving DOI: {bare_doi}")

        data = self._fetch_crossref(bare_doi)
        return self._parse_metadata(data, bare_doi)

    def _fetch_crossref(self, doi: str) -> dict:
        """Raw CrossRef API call. Returns the 'message' dict."""
        url = CROSSREF_API.format(doi=doi)
        session = self._get_session()

        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT)
        except requests.Timeout:
            raise NetworkError(f"CrossRef API timeout for DOI: {doi}")
        except requests.ConnectionError as e:
            raise NetworkError(f"Cannot connect to CrossRef: {e}")

        if resp.status_code == 404:
            raise DOIResolutionError(f"DOI not found: {doi}")
        if resp.status_code != 200:
            raise DOIResolutionError(f"CrossRef API error {resp.status_code} for DOI: {doi}")

        try:
            body = resp.json()
            return body.get("message", body)
        except Exception as e:
            raise DOIResolutionError(f"Invalid response from CrossRef: {e}")

    def _parse_metadata(self, data: dict, doi: str) -> ArticleMetadata:
        """Parse CrossRef response into ArticleMetadata."""
        # Title
        title_list = data.get("title", [])
        title = title_list[0] if title_list else ""

        # Authors
        authors = []
        for author in data.get("author", []):
            family = author.get("family", "")
            given = author.get("given", "")
            if family and given:
                authors.append(f"{family}, {given}")
            elif family:
                authors.append(family)
            elif given:
                authors.append(given)

        # Year from published-print or published-online or created
        year = None
        for date_field in ("published-print", "published-online", "created"):
            date_info = data.get(date_field, {}).get("date-parts", [[]])
            if date_info and date_info[0] and date_info[0][0]:
                year = date_info[0][0]
                break

        # Journal from container-title
        container = data.get("container-title", [])
        journal = container[0] if container else ""

        # URL
        url = data.get("URL", "")

        # Abstract
        abstract = data.get("abstract", "")

        # Publisher
        publisher = data.get("publisher", "")

        # ISSN
        issn_list = data.get("ISSN", [])
        issn = issn_list[0] if issn_list else ""

        return ArticleMetadata(
            doi=doi,
            title=title,
            authors=authors,
            year=year,
            journal=journal,
            url=url,
            abstract=abstract,
            publisher=publisher,
            issn=issn,
        )
