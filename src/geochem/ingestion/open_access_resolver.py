"""Public-only DOI and PDF acquisition for the conversational agent.

This resolver deliberately stops at open endpoints. It never attempts to
circumvent a publisher login, CAPTCHA, institutional proxy, or paywall.
"""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any
from uuid import uuid4

import requests

from ..core.project import ProjectManager
from .doi_service import ArticleMetadata, DOIService
from .file_importer import FileImporter


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class OpenAccessResolver:
    """Resolve a DOI through Crossref, Unpaywall, then public publisher links."""

    USER_AGENT = "GeoChem Data Curation Agent/1.0 (public-pdf-resolver)"

    def __init__(self, project_manager: ProjectManager | None = None):
        self.pm = project_manager or ProjectManager()
        self.dois = DOIService()
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.USER_AGENT, "Accept": "application/pdf,text/html;q=0.9,*/*;q=0.1"})

    def search(self, project_id: str, source: str) -> dict[str, Any]:
        """Persist candidates without creating an article until user confirmation."""
        source = source.strip()
        if not source:
            raise ValueError("请输入 DOI 或公开 URL。")
        metadata, doi = self._metadata(source)
        candidates: list[dict[str, Any]] = []
        if source.lower().split("?", 1)[0].endswith(".pdf"):
            candidates.append(self._candidate(metadata, source, source, "direct_pdf", "public", "用户提供的 PDF URL，等待确认下载。"))
        else:
            pdf = self._unpaywall_pdf(doi) if doi else ""
            if pdf:
                candidates.append(self._candidate(metadata, metadata.url or self.dois.doi_to_url(doi), pdf, "unpaywall", "public", "Unpaywall 提供的开放 PDF。"))
            publisher_url = metadata.url or (self.dois.doi_to_url(doi) if doi else source)
            publisher_pdf, message = self._publisher_pdf(publisher_url)
            if publisher_pdf and publisher_pdf not in {item["pdf_url"] for item in candidates}:
                candidates.append(self._candidate(metadata, publisher_url, publisher_pdf, "publisher", "public", "出版社页面中发现的公开 PDF。"))
            if not candidates:
                candidates.append(self._candidate(metadata, publisher_url, "", "crossref", "unavailable", message or "未找到可公开下载的 PDF，请手动上传文件。"))
        stored = [self._store_candidate(project_id, source, item) for item in candidates]
        return {"input": source, "doi": doi, "candidates": stored, "upload_required": not any(item["access_status"] == "public" for item in stored)}

    def confirm(self, project_id: str, source_id: str) -> dict[str, Any]:
        """Download one already-reviewed public PDF and create its article/resource."""
        db = self.pm.get_database(project_id)
        try:
            row = db.fetch_one("SELECT * FROM article_source_candidates WHERE project_id=? AND source_id=?", (project_id, source_id))
            if not row:
                raise ValueError("公开来源候选不存在。")
            candidate = dict(row)
            if candidate["access_status"] != "public" or not candidate["pdf_url"]:
                return {"status": "needs_upload", "message": candidate["validation_message"] or "该来源没有可公开下载的 PDF，请上传 PDF。"}
            existing = db.fetch_one("SELECT article_id FROM articles WHERE project_id=? AND doi=? ORDER BY created_at DESC LIMIT 1", (project_id, candidate["doi"])) if candidate["doi"] else None
            if existing:
                db.execute("UPDATE article_source_candidates SET user_confirmed=1, updated_at=? WHERE source_id=?", (_now(), source_id))
                db.commit()
                return {"status": "ready", "article_id": existing["article_id"], "message": "该 DOI 已存在于当前工作区。"}
            metadata_data = self._json(candidate["metadata_json"], {})
            metadata = ArticleMetadata(
                doi=candidate["doi"], title=candidate["title"], authors=self._json(candidate["authors_json"], []),
                year=candidate["year"], journal=candidate["journal"], url=candidate["source_url"],
                abstract=metadata_data.get("abstract", ""), publisher=metadata_data.get("publisher", ""), issn=metadata_data.get("issn", ""),
            )
            _project, project_dir = self.pm.load_project(project_id)
            article_id = FileImporter().create_article_from_metadata(db, project_id, metadata)
            pdf = self._download_public_pdf(candidate["pdf_url"])
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
                handle.write(pdf)
                path = Path(handle.name)
            try:
                result = FileImporter().import_file(db, project_dir, path, article_id=article_id)
            finally:
                path.unlink(missing_ok=True)
            db.execute("UPDATE article_source_candidates SET user_confirmed=1, updated_at=? WHERE source_id=?", (_now(), source_id))
            db.commit()
            return {"status": "ready", "article_id": article_id, "resource_id": result.resource_id, "file_name": result.file_name, "pdf_url": candidate["pdf_url"]}
        except requests.RequestException as exc:
            return {"status": "needs_upload", "message": f"公开 PDF 下载失败：{exc}。请手动上传 PDF。"}
        finally:
            db.close()

    def _metadata(self, source: str) -> tuple[ArticleMetadata, str]:
        doi = self.dois.normalize_doi(source)
        if re.match(r"^10\.\d{4,9}/", doi, re.I):
            try:
                return self.dois.resolve(doi), doi
            except Exception as exc:
                return ArticleMetadata(doi=doi, title=doi, url=self.dois.doi_to_url(doi)), doi
        return ArticleMetadata(doi="", title=Path(source.split("?", 1)[0]).stem or source, url=source), ""

    def _unpaywall_pdf(self, doi: str) -> str:
        email = os.getenv("UNPAYWALL_EMAIL", "").strip()
        if not doi or not email:
            return ""
        try:
            response = self.session.get(f"https://api.unpaywall.org/v2/{doi}", params={"email": email}, timeout=15)
            if response.status_code != 200:
                return ""
            data = response.json()
            location = data.get("best_oa_location") or {}
            return str(location.get("url_for_pdf") or "")
        except (requests.RequestException, ValueError):
            return ""

    def _publisher_pdf(self, url: str) -> tuple[str, str]:
        try:
            response = self.session.get(url, timeout=15, allow_redirects=True)
            if response.status_code in {401, 403}:
                return "", f"出版社页面返回 {response.status_code}，可能需要登录；请手动上传 PDF。"
            if response.status_code != 200:
                return "", f"出版社页面无法访问（HTTP {response.status_code}）。"
            if response.content.startswith(b"%PDF"):
                return response.url, ""
            html = response.text[:2_000_000]
            patterns = [
                r'<meta[^>]+name=["\']citation_pdf_url["\'][^>]+content=["\']([^"\']+)',
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']citation_pdf_url["\']',
                r'href=["\']([^"\']+\.pdf(?:\?[^"\']*)?)["\']',
            ]
            for pattern in patterns:
                match = re.search(pattern, html, re.I)
                if match:
                    return requests.compat.urljoin(response.url, match.group(1).replace("&amp;", "&")), ""
            return "", "出版社页面没有公开 PDF 链接；请手动上传 PDF。"
        except requests.RequestException as exc:
            return "", f"无法访问公开来源：{exc}。请手动上传 PDF。"

    def _download_public_pdf(self, url: str) -> bytes:
        response = self.session.get(url, timeout=30, allow_redirects=True)
        if response.status_code in {401, 403}:
            raise requests.RequestException(f"HTTP {response.status_code}，需要访问授权")
        response.raise_for_status()
        payload = response.content
        content_type = response.headers.get("content-type", "").lower()
        if not payload.startswith(b"%PDF") and "application/pdf" not in content_type:
            raise requests.RequestException("响应不是 PDF 文件")
        return payload

    @staticmethod
    def _candidate(metadata: ArticleMetadata, source_url: str, pdf_url: str, kind: str, access: str, message: str) -> dict[str, Any]:
        return {"doi": metadata.doi, "title": metadata.title, "authors": metadata.authors, "year": metadata.year, "journal": metadata.journal, "source_url": source_url, "pdf_url": pdf_url, "source_kind": kind, "access_status": access, "validation_message": message, "metadata": {"abstract": metadata.abstract, "publisher": metadata.publisher, "issn": metadata.issn}}

    def _store_candidate(self, project_id: str, input_value: str, candidate: dict[str, Any]) -> dict[str, Any]:
        db = self.pm.get_database(project_id)
        try:
            source_id = f"SRC_{uuid4().hex[:16].upper()}"
            now = _now()
            db.execute(
                """INSERT INTO article_source_candidates
                   (source_id, project_id, input_value, doi, title, authors_json, year, journal, source_url, pdf_url, source_kind, access_status, validation_message, metadata_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (source_id, project_id, input_value, candidate["doi"], candidate["title"], json.dumps(candidate["authors"], ensure_ascii=False), candidate["year"], candidate["journal"], candidate["source_url"], candidate["pdf_url"], candidate["source_kind"], candidate["access_status"], candidate["validation_message"], json.dumps(candidate["metadata"], ensure_ascii=False), now, now),
            )
            db.commit()
            return {"source_id": source_id, **candidate}
        finally:
            db.close()

    @staticmethod
    def _json(raw: str, fallback: Any) -> Any:
        try:
            return json.loads(raw or "")
        except (TypeError, json.JSONDecodeError):
            return fallback
