"""Browser-based authentication and content reading flow."""

from __future__ import annotations

import webbrowser
from typing import Callable

from ..core.logging_config import get_logger
from .doi_service import DOIService
from .web_reader import WebReader

logger = get_logger("ingestion.browser_service")


class BrowserService:
    """Orchestrate the DOI -> browser -> LLM reading flow.

    Flow:
    1. User provides DOI.
    2. DOIService resolves DOI to URL.
    3. webbrowser.open() opens the publisher page.
    4. User authenticates (institutional login, CAPTCHA, etc.).
    5. User returns to the app and confirms readiness.
    6. WebReader fetches the now-authenticated page content.
    7. Content is returned for downstream processing.
    """

    def __init__(
        self,
        doi_service: DOIService | None = None,
        web_reader: WebReader | None = None,
    ):
        self.doi_service = doi_service or DOIService()
        self.web_reader = web_reader or WebReader()

    def resolve_and_open(self, doi: str) -> str:
        """Resolve DOI and open in system browser.

        Returns the resolved URL.
        """
        url = self.doi_service.doi_to_url(doi)
        self.open_url(url)
        return url

    def open_url(self, url: str) -> None:
        """Open a URL in the system browser."""
        logger.info(f"Opening browser: {url}")
        webbrowser.open(url)

    def read_after_auth(self, url: str) -> str:
        """Read page content after user has authenticated.

        Returns extracted text content.
        """
        logger.info(f"Reading page after auth: {url}")
        try:
            return self.web_reader.fetch_page(url)
        except Exception as e:
            logger.warning(f"Failed to read page {url}: {e}")
            return ""

    def read_payload_after_auth(self, url: str) -> dict:
        """Read page plus discovered article assets after user confirmation."""
        logger.info(f"Reading page payload after auth: {url}")
        try:
            return self.web_reader.fetch_page_payload(url)
        except Exception as e:
            logger.warning(f"Failed to read page payload {url}: {e}")
            return {"text": "", "html": "", "pdf_url": "", "title": "", "content_type": ""}

    def full_doi_flow(
        self,
        doi: str,
        confirm_callback: Callable[[], bool] | None = None,
    ) -> str:
        """Execute the complete DOI-to-content flow.

        Args:
            doi: The DOI to resolve.
            confirm_callback: Callable that returns True when the user
                              confirms authentication. If None, uses input().

        Returns:
            Extracted page content as string, or empty string on failure.
        """
        # Step 1: Resolve and open
        url = self.resolve_and_open(doi)
        logger.info(f"Browser opened: {url}")

        # Step 2: Wait for user confirmation
        if confirm_callback is not None:
            confirmed = confirm_callback()
        else:
            try:
                input("Please authenticate in your browser, then press Enter here...")
                confirmed = True
            except (EOFError, KeyboardInterrupt):
                confirmed = False

        if not confirmed:
            logger.warning("User did not confirm browser authentication")
            return ""

        # Step 3: Read page content
        return self.read_after_auth(url)
