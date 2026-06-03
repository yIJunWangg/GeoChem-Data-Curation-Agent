"""Lightweight workflow orchestration for CLI and UI callers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import requests

from .core.config import load_config
from .core.memory import MemoryStore
from .core.models import ResourceType
from .core.project import ProjectManager
from .core.schema_manager import SchemaManager
from .curation import (
    AuditPackageBuilder,
    CostReporter,
    MappingEngine,
    ReviewManager,
    RuleApplicationEngine,
    StandardizationPipeline,
    TeachingManager,
    TraceService,
)
from .extractors import CsvReader, ExcelReader, PdfReader, ResultPersistence
from .ingestion.browser_service import BrowserService
from .ingestion.doi_service import ArticleMetadata, DOIService
from .ingestion.file_importer import FileImporter
from .providers.llm_client import LLMClient


@dataclass
class WorkflowEvent:
    """A UI-friendly workflow event."""

    event_type: str
    message: str
    progress: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


class EventBus:
    """Small in-process pub/sub used by the desktop UI."""

    def __init__(self):
        self._subscribers: list[Callable[[WorkflowEvent], None]] = []

    def subscribe(self, callback: Callable[[WorkflowEvent], None]) -> None:
        self._subscribers.append(callback)

    def emit(self, event_type: str, message: str, progress: float = 0.0, **details) -> WorkflowEvent:
        event = WorkflowEvent(event_type=event_type, message=message, progress=progress, details=details)
        for callback in list(self._subscribers):
            callback(event)
        return event


class WorkflowRunner:
    """Deterministic workflow wrapper around existing curation services."""

    def __init__(self, project_manager: ProjectManager | None = None, event_bus: EventBus | None = None):
        self.pm = project_manager or ProjectManager()
        self.events = event_bus or EventBus()

    def extract_resource(self, project_id: str, resource_id: str) -> dict[str, Any]:
        """Read a resource and save full candidate tables without LLM row extraction."""
        config, project_dir = self.pm.load_project(project_id)
        db = self.pm.get_database(project_id)
        try:
            resource = db.fetch_one(
                "SELECT r.*, a.article_id FROM resources r JOIN articles a ON r.article_id = a.article_id WHERE r.resource_id = ?",
                (resource_id,),
            )
            if not resource:
                raise ValueError(f"Resource not found: {resource_id}")
            file_path = project_dir / resource["local_path"] if resource["local_path"] else Path(resource["file_name"])
            if not file_path.exists():
                file_path = Path(resource["local_path"] or "")
            if not file_path.exists():
                raise FileNotFoundError(resource["local_path"] or resource["file_name"])
            reader_cls = {
                "supplementary_excel": ExcelReader,
                "supplementary_csv": CsvReader,
                "main_pdf": PdfReader,
                "supplementary_pdf": PdfReader,
                "html_page": CsvReader,
            }.get(resource["resource_type"], ExcelReader)
            self.events.emit("extract", f"Reading {resource['file_name']}", 0.2, resource_id=resource_id)
            contents = reader_cls().read(file_path)
            persistence = ResultPersistence()
            table_ids = []
            for content in contents:
                display_headers = content.raw_headers or content.headers
                rows = []
                for raw_row in content.raw_rows:
                    row = {}
                    for i, header in enumerate(display_headers):
                        if header is not None and str(header).strip():
                            row[str(header)] = raw_row[i] if i < len(raw_row) else None
                    rows.append(row)
                if not rows:
                    continue
                table_id = persistence.save(
                    db,
                    resource["article_id"],
                    resource_id,
                    content.text,
                    rows,
                    display_headers,
                    {},
                    header_units=content.header_units,
                    source_type=content.source_type,
                    sheet_name=content.sheet_name,
                    page_number=content.page_number,
                    confidence=1.0,
                    extract_method="reader",
                )
                table_ids.append(table_id)
            self.events.emit("extract", f"Extracted {len(table_ids)} table(s)", 1.0, tables=table_ids)
            return {"table_ids": table_ids}
        finally:
            db.close()

    def import_files(
        self,
        project_id: str,
        file_paths: list[str | Path],
        article_id: str | None = None,
        resource_type: str | None = None,
    ) -> dict[str, Any]:
        """Import local PDF/Excel/CSV/ZIP/DOCX files into the project."""
        _config, project_dir = self.pm.load_project(project_id)
        db = self.pm.get_database(project_id)
        try:
            importer = FileImporter()
            override = ResourceType(resource_type) if resource_type else None
            results = []
            for path in file_paths:
                self.events.emit("import", f"Importing {Path(path).name}", 0.25, file=str(path))
                result = importer.import_file(
                    db,
                    project_dir,
                    path,
                    article_id=article_id,
                    resource_type_override=override,
                )
                results.append({
                    "article_id": result.article_id,
                    "resource_id": result.resource_id,
                    "resource_type": result.resource_type.value if result.resource_type else "",
                    "file_name": result.file_name,
                    "status": result.status,
                })
            self.events.emit("import", f"Imported {len(results)} file(s)", 1.0, count=len(results))
            return {"resources": results, "count": len(results)}
        finally:
            db.close()

    def open_source_in_browser(self, project_id: str, source: str) -> dict[str, Any]:
        """Open a DOI or URL in the system browser and register an article shell."""
        source = (source or "").strip()
        if not source:
            raise ValueError("DOI or URL is required")
        _config, _project_dir = self.pm.load_project(project_id)
        db = self.pm.get_database(project_id)
        try:
            self.events.emit("import", "Resolving DOI/URL metadata", 0.08, source=source)
            doi_service = DOIService()
            browser = BrowserService(doi_service=doi_service)
            if source.startswith(("http://", "https://")) and "doi.org/" not in source:
                url = source
                metadata = ArticleMetadata(doi="", title=source, url=url)
            else:
                try:
                    metadata = doi_service.resolve(source)
                    self.events.emit("import", "CrossRef metadata resolved", 0.25, doi=metadata.doi, title=metadata.title)
                except Exception:
                    doi = doi_service.normalize_doi(source)
                    metadata = ArticleMetadata(doi=doi, title=doi, url=doi_service.doi_to_url(doi))
                    self.events.emit("import", "CrossRef lookup unavailable; using DOI URL directly", 0.25, doi=doi)
                url = metadata.url or doi_service.doi_to_url(metadata.doi)
            self.events.emit("import", "Creating article record", 0.4, url=url)
            article_id = FileImporter().create_article_from_metadata(db, project_id, metadata)
            self.events.emit("import", "Opening publisher page in browser", 0.55, article_id=article_id, url=url)
            browser.open_url(url)
            self.events.emit("import", "Browser opened for article access confirmation", 0.6, article_id=article_id, url=url)
            return {
                "article_id": article_id,
                "url": url,
                "doi": metadata.doi,
                "title": metadata.title,
                "authors": ", ".join(metadata.authors),
                "year": metadata.year,
                "journal": metadata.journal,
                "status": "等待浏览器确认",
            }
        finally:
            db.close()

    def confirm_browser_access(self, project_id: str, article_id: str, url: str) -> dict[str, Any]:
        """Save a user-confirmed article URL, using fetched text when available.

        Browser cookies from the user's system browser are not available to the
        requests-based reader. For open-access pages this may still fetch enough
        text; for publisher pages that block scripted reads, we keep a confirmed
        evidence resource so a later LLM/browser-reading task can continue.
        """
        if not article_id or not url:
            raise ValueError("Missing article_id or URL for browser confirmation")
        _config, project_dir = self.pm.load_project(project_id)
        db = self.pm.get_database(project_id)
        try:
            self.events.emit("import", "User confirmed browser access; starting journal-page read", 0.1, article_id=article_id, url=url)
            self.events.emit("import", "Trying to discover the real article PDF/body asset", 0.28, article_id=article_id, url=url)
            browser = BrowserService()
            payload = browser.read_payload_after_auth(url)
            pdf_url = payload.get("pdf_url", "")
            content = payload.get("text", "")
            fetched = bool(content and len(content.strip()) >= 200)
            if pdf_url:
                self.events.emit("import", "Article PDF candidate found; downloading PDF asset", 0.52, pdf_url=pdf_url)
                pdf_resource = self._download_and_import_pdf(db, project_dir, article_id, pdf_url)
                if pdf_resource:
                    self._discover_evidence_from_pdf(db, project_id, article_id, pdf_resource["resource_id"], project_dir / pdf_resource["local_path"])
                    self.events.emit(
                        "import",
                        f"Saved article PDF as {pdf_resource['resource_id']}; next step is PDF table/figure extraction",
                        1.0,
                        resource_id=pdf_resource["resource_id"],
                    )
                    return {
                        "article_id": article_id,
                        "resource_id": pdf_resource["resource_id"],
                        "content_length": pdf_resource.get("file_size", 0),
                        "fetched_content": True,
                        "status": "pending",
                        "resource_type": ResourceType.MAIN_PDF.value,
                        "pdf_url": pdf_url,
                    }
                self.events.emit("import", "PDF candidate could not be downloaded; falling back to confirmed URL handoff", 0.62, pdf_url=pdf_url)
            if not fetched:
                self.events.emit(
                    "import",
                    "Direct reader did not capture reliable article text; preparing LLM/browser-reader handoff",
                    0.55,
                    article_id=article_id,
                    url=url,
                )
                content = (
                    "<html><body>\n"
                    "<h1>GeoChem confirmed article access</h1>\n"
                    f"<p>Article URL: {url}</p>\n"
                    "<p>User confirmed that the article page is accessible in the browser. "
                    "Scripted fetching did not return readable content, so downstream LLM/browser "
                    "reading should use this URL and the user's confirmed access state.</p>\n"
                    "</body></html>\n"
                )
            else:
                self.events.emit("import", "Article body text captured for AI reading", 0.6, article_id=article_id, chars=len(content))
            self.events.emit("import", "Saving confirmed page evidence resource", 0.78, article_id=article_id)
            resource_id = FileImporter().create_web_resource(db, article_id, url, content, project_dir)
            db.execute(
                "UPDATE resources SET status = ? WHERE resource_id = ?",
                ("pending" if fetched else "auth_confirmed", resource_id),
            )
            db.commit()
            if fetched:
                self._discover_evidence_from_text(db, project_id, article_id, resource_id, content)
            if fetched:
                self.events.emit("import", f"Saved readable article page as {resource_id}; AI can proceed to extraction", 1.0, resource_id=resource_id)
            else:
                self.events.emit("import", f"Saved LLM/browser-reader entrypoint as {resource_id}; waiting for extraction task", 1.0, resource_id=resource_id)
            return {
                "article_id": article_id,
                "resource_id": resource_id,
                "content_length": len(content),
                "fetched_content": fetched,
                "status": "pending" if fetched else "auth_confirmed",
            }
        finally:
            db.close()

    def _download_and_import_pdf(self, db, project_dir: Path, article_id: str, pdf_url: str) -> dict[str, Any] | None:
        """Download a discovered PDF URL and import it as the main article resource."""
        try:
            response = requests.get(
                pdf_url,
                timeout=30,
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
                    "Accept": "application/pdf,*/*",
                },
            )
            if response.status_code != 200:
                return None
            content_type = response.headers.get("content-type", "").lower()
            if "pdf" not in content_type and not response.content.startswith(b"%PDF"):
                return None
            target = project_dir / ".cache" / "downloads" / f"{article_id}_article.pdf"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(response.content)
            result = FileImporter().import_file(
                db,
                project_dir,
                target,
                article_id=article_id,
                resource_type_override=ResourceType.MAIN_PDF,
            )
            db.execute("UPDATE resources SET source_url = ? WHERE resource_id = ?", (pdf_url, result.resource_id))
            db.commit()
            return {
                "resource_id": result.resource_id,
                "file_name": result.file_name,
                "local_path": result.local_path,
                "file_size": result.file_size,
            }
        except Exception:
            return None

    def _discover_evidence_from_text(self, db, project_id: str, article_id: str, resource_id: str, text: str) -> int:
        """Create first-pass paragraph/table/figure evidence from readable text."""
        created = 0
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for line in lines:
            lowered = line.lower()
            evidence_type = ""
            if lowered.startswith("table ") or " table " in lowered[:80]:
                evidence_type = "table_caption"
            elif lowered.startswith("figure ") or lowered.startswith("fig. ") or " figure " in lowered[:80]:
                evidence_type = "figure_caption"
            elif any(term in lowered for term in ("geochem", "sample", "supplementary", "data", "ppm", "wt%")):
                evidence_type = "paragraph"
            if not evidence_type:
                continue
            self._insert_evidence(db, project_id, article_id, resource_id, evidence_type, line[:1200])
            created += 1
            if created >= 40:
                break
        return created

    def _discover_evidence_from_pdf(self, db, project_id: str, article_id: str, resource_id: str, pdf_path: Path) -> int:
        """Extract rough captions/paragraph evidence from a PDF."""
        try:
            import fitz
        except Exception:
            return 0
        created = 0
        try:
            doc = fitz.open(str(pdf_path))
            for page_index, page in enumerate(doc, start=1):
                text = page.get_text("text") or ""
                for line in text.splitlines():
                    cleaned = " ".join(line.split())
                    lowered = cleaned.lower()
                    if not cleaned:
                        continue
                    evidence_type = ""
                    if lowered.startswith("table "):
                        evidence_type = "table_caption"
                    elif lowered.startswith(("figure ", "fig. ")):
                        evidence_type = "figure_caption"
                    elif any(term in lowered for term in ("geochem", "sample", "supplementary", "ppm", "wt%")):
                        evidence_type = "paragraph"
                    if evidence_type:
                        self._insert_evidence(db, project_id, article_id, resource_id, evidence_type, cleaned[:1200], f"page {page_index}")
                        created += 1
                        if created >= 60:
                            doc.close()
                            return created
            doc.close()
        except Exception:
            return created
        return created

    def _insert_evidence(
        self,
        db,
        project_id: str,
        article_id: str,
        resource_id: str,
        evidence_type: str,
        evidence_text: str,
        page_or_section: str = "",
    ) -> None:
        row = db.fetch_one(
            "SELECT MAX(CAST(SUBSTR(evidence_id, 5) AS INTEGER)) as max_id FROM article_evidence WHERE evidence_id LIKE 'EVD_%'"
        )
        evidence_id = f"EVD_{(row['max_id'] or 0) + 1:06d}"
        db.execute(
            """INSERT INTO article_evidence
               (evidence_id, project_id, article_id, resource_id, evidence_type,
                evidence_text, page_or_section, confidence, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                evidence_id,
                project_id,
                article_id,
                resource_id,
                evidence_type,
                evidence_text,
                page_or_section,
                0.55,
                "candidate",
                datetime.now().isoformat(),
            ),
        )
        db.commit()

    def map_table(
        self,
        project_id: str,
        table_id: str,
        grouped: bool = True,
        use_llm: bool = False,
        header_descriptions: str | None = None,
        provider: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        config, project_dir = self.pm.load_project(project_id)
        db = self.pm.get_database(project_id)
        try:
            sm = SchemaManager()
            sm.load_from_file(project_dir / "schema" / "geochem_schema.yaml")
            memory = MemoryStore(project_dir / "memory" / "mapping_rules.yaml")
            memory.load()
            llm_client = LLMClient(load_config(), db=db) if use_llm else None
            self.events.emit("map", f"Mapping {table_id}", 0.35, table_id=table_id)
            suggestions = MappingEngine(
                sm,
                memory_store=memory,
                llm_client=llm_client,
                provider_override=provider,
                model_override=model,
            ).map_table(
                db,
                table_id,
                project_id=project_id,
                use_llm=use_llm,
                grouped=grouped,
                header_descriptions_path=header_descriptions,
            )
            reviews = ReviewManager().create_for_table(db, table_id)
            self.events.emit("map", f"Mapped {len(suggestions)} field(s)", 1.0, reviews=reviews)
            return {"mapped": len(suggestions), "review_items": reviews}
        finally:
            db.close()

    def standardize(self, project_id: str, table_id: str) -> dict[str, Any]:
        config, project_dir = self.pm.load_project(project_id)
        db = self.pm.get_database(project_id)
        try:
            self.events.emit("standardize", f"Standardizing {table_id}", 0.5, table_id=table_id)
            count = StandardizationPipeline().standardize_table(db, project_dir, table_id)
            self.events.emit("standardize", f"Standardized {count} record(s)", 1.0, records=count)
            return {"records": count}
        finally:
            db.close()

    def teach_value(self, project_id: str, article_id: str, **kwargs) -> dict[str, str]:
        db = self.pm.get_database(project_id)
        try:
            result = TeachingManager().add_event(db=db, project_id=project_id, article_id=article_id, **kwargs)
            self.events.emit("teach", f"Teaching saved {result['event_id']}", 1.0, **result)
            return result
        finally:
            db.close()

    def apply_teaching(self, project_id: str, table_id: str) -> dict[str, Any]:
        db = self.pm.get_database(project_id)
        try:
            count = RuleApplicationEngine().apply(db, project_id, table_id)
            self.events.emit("teach", f"Applied {count} patch(es)", 1.0, patches=count)
            return {"patches": count}
        finally:
            db.close()

    def export_audit_package(self, project_id: str, table_id: str, data_file: Path, export_format: str = "csv") -> dict[str, Any]:
        config, project_dir = self.pm.load_project(project_id)
        db = self.pm.get_database(project_id)
        try:
            package_dir = AuditPackageBuilder().create(db, project_id, project_dir, table_id, data_file, export_format)
            self.events.emit("export", f"Audit package created", 1.0, package=str(package_dir))
            return {"package_dir": str(package_dir)}
        finally:
            db.close()

    def trace_table(self, project_id: str, table_id: str) -> list[dict[str, Any]]:
        db = self.pm.get_database(project_id)
        try:
            return TraceService().trace_table(db, table_id)
        finally:
            db.close()

    def cost_report(self, project_id: str, group_by: str = "model") -> list[dict[str, Any]]:
        db = self.pm.get_database(project_id)
        try:
            return CostReporter().report(db, project_id, group_by)
        finally:
            db.close()


class TaskService:
    """Synchronous task facade for UI buttons and tests.

    The PySide layer can run these methods in a QThread later; keeping this class
    UI-free makes the business flow easy to test.
    """

    def __init__(self, runner: WorkflowRunner | None = None):
        self.runner = runner or WorkflowRunner()

    def run(self, action: str, **kwargs) -> Any:
        if not hasattr(self.runner, action):
            raise ValueError(f"Unknown workflow action: {action}")
        method = getattr(self.runner, action)
        return method(**kwargs)
