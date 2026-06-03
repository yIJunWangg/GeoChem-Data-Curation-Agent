"""Local file import into a GeoChem project."""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

from ..core.database import Database
from ..core.exceptions import DuplicateFileError, FileImportError
from ..core.logging_config import get_logger
from ..core.models import ArticleStatus, IngestionResult, ResourceType
from ..core.project import ProjectManager

logger = get_logger("ingestion.file_importer")

EXTENSION_MAP: dict[str, ResourceType] = {
    ".pdf": ResourceType.MAIN_PDF,
    ".xlsx": ResourceType.SUPPLEMENTARY_EXCEL,
    ".xls": ResourceType.SUPPLEMENTARY_EXCEL,
    ".csv": ResourceType.SUPPLEMENTARY_CSV,
    ".docx": ResourceType.SUPPLEMENTARY_DOCX,
    ".doc": ResourceType.SUPPLEMENTARY_DOCX,
    ".zip": ResourceType.SUPPLEMENTARY_ZIP,
    ".png": ResourceType.FIGURE,
    ".jpg": ResourceType.FIGURE,
    ".jpeg": ResourceType.FIGURE,
    ".tiff": ResourceType.FIGURE,
    ".tif": ResourceType.FIGURE,
}

TARGET_DIRS: dict[ResourceType, str] = {
    ResourceType.FIGURE: "assets/figures",
}


class FileImporter:
    """Import local files into a GeoChem project."""

    def import_file(
        self,
        db: Database,
        project_dir: Path,
        file_path: str | Path,
        article_id: str | None = None,
        resource_type_override: ResourceType | None = None,
    ) -> IngestionResult:
        """Import a single file into the project.

        Args:
            db: Open database connection.
            project_dir: Root directory of the project.
            file_path: Absolute path to the source file.
            article_id: If provided, attach resource to existing article.
                        If None, a new article is created.
            resource_type_override: Force a specific resource type.

        Returns:
            IngestionResult with import details.

        Raises:
            FileImportError: File does not exist or is unreadable.
            DuplicateFileError: File with same hash already imported.
        """
        source = Path(file_path).resolve()
        self._validate_file(source)

        file_hash = self._compute_hash(source)
        file_size = source.stat().st_size
        file_name = source.name

        # Check for duplicate
        existing = self._check_duplicate(db, file_hash)
        if existing:
            project_id = self._get_project_id(db)
            self._log_event(db, project_id, existing["article_id"], "duplicate_file",
                            f"Duplicate of {existing['file_name']}: {file_name}")
            raise DuplicateFileError(
                f"File already imported as {existing['resource_id']} "
                f"({existing['file_name']}), hash: {file_hash}"
            )

        resource_type = resource_type_override or self._detect_resource_type(source)
        project_id = self._get_project_id(db)

        # Create article if needed
        if article_id is None:
            article_id = self._create_article(db, project_id, source)

        # Copy file into the article-scoped folder.
        target_dir = self._get_target_dir(db, article_id, resource_type, project_dir)
        local_path = self._copy_file(source, target_dir)
        rel_path = self._relative_to_project(project_dir, local_path)

        # Create resource record
        resource_id = self._create_resource(
            db, article_id, resource_type, file_name, rel_path, file_hash, file_size
        )

        self._log_event(db, project_id, article_id, "file_imported",
                        f"Imported {file_name} as {resource_type.value}")

        logger.info(f"Imported {file_name} -> {resource_id} (article {article_id})")

        return IngestionResult(
            article_id=article_id,
            resource_id=resource_id,
            resource_type=resource_type,
            file_name=file_name,
            local_path=rel_path,
            file_hash=file_hash,
            file_size=file_size,
            is_duplicate=False,
        )

    def import_batch(
        self,
        db: Database,
        project_dir: Path,
        file_paths: list[str | Path],
        article_id: str | None = None,
    ) -> list[IngestionResult]:
        """Import multiple files, each as a separate resource."""
        results = []
        for fp in file_paths:
            try:
                result = self.import_file(db, project_dir, fp, article_id)
                results.append(result)
            except Exception as e:
                logger.warning(f"Failed to import {fp}: {e}")
                results.append(IngestionResult(
                    article_id="",
                    file_name=str(fp),
                    status="error",
                    error=str(e),
                ))
        return results

    def create_article_from_metadata(
        self, db: Database, project_id: str, metadata
    ) -> str:
        """Create an Article record from DOI metadata.

        Args:
            db: Database connection.
            project_id: Project ID.
            metadata: ArticleMetadata dataclass from doi_service.

        Returns:
            The new article_id.
        """
        article_id = self._generate_id(db, "ART", "articles", "article_id")
        now = datetime.now().isoformat()

        db.execute(
            """INSERT INTO articles
            (article_id, project_id, title, authors, year, doi, url, journal, article_dir, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                article_id,
                project_id,
                metadata.title,
                json.dumps(metadata.authors, ensure_ascii=False),
                metadata.year,
                metadata.doi,
                metadata.url,
                metadata.journal,
                ProjectManager().ensure_article_dir(project_dir=self._project_dir_from_db(db), title=metadata.title, doi=metadata.doi),
                ArticleStatus.IMPORTED.value,
                now,
            ),
        )
        db.commit()
        logger.info(f"Created article {article_id} from DOI {metadata.doi}")
        return article_id

    def create_web_resource(
        self,
        db: Database,
        article_id: str,
        url: str,
        content: str,
        project_dir: Path,
    ) -> str:
        """Save web page content as an HTML resource.

        Returns the new resource_id.
        """
        resource_id = self._generate_id(db, "RES", "resources", "resource_id")
        now = datetime.now().isoformat()

        # Save content to the article folder.
        target_dir = self._get_article_root(db, article_id, project_dir) / "source"
        target_dir.mkdir(parents=True, exist_ok=True)
        file_path = target_dir / "confirmed_page.html"
        file_path.write_text(content, encoding="utf-8")

        file_hash = self._compute_hash(file_path)
        file_size = file_path.stat().st_size

        db.execute(
            """INSERT INTO resources
            (resource_id, article_id, resource_type, file_name, local_path,
             source_url, file_hash, file_size, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                resource_id,
                article_id,
                ResourceType.HTML_PAGE.value,
                file_path.name,
                self._relative_to_project(project_dir, file_path),
                url,
                file_hash,
                file_size,
                "pending",
                now,
            ),
        )
        db.commit()
        logger.info(f"Created web resource {resource_id} for article {article_id}")
        return resource_id

    # --- Private helpers ---

    def _validate_file(self, file_path: Path) -> None:
        if not file_path.exists():
            raise FileImportError(f"File not found: {file_path}")
        if not file_path.is_file():
            raise FileImportError(f"Not a regular file: {file_path}")

    def _compute_hash(self, file_path: Path) -> str:
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

    def _detect_resource_type(self, file_path: Path) -> ResourceType:
        ext = file_path.suffix.lower()
        return EXTENSION_MAP.get(ext, ResourceType.OTHER)

    def _get_target_dir(self, db: Database, article_id: str, resource_type: ResourceType, project_dir: Path) -> Path:
        article_root = self._get_article_root(db, article_id, project_dir)
        sub = TARGET_DIRS.get(resource_type, "source")
        target = article_root / sub
        target.mkdir(parents=True, exist_ok=True)
        return target

    def _get_article_root(self, db: Database, article_id: str, project_dir: Path) -> Path:
        row = db.fetch_one("SELECT title, doi, article_dir FROM articles WHERE article_id = ?", (article_id,))
        if not row:
            rel = ProjectManager().ensure_article_dir(project_dir, title=article_id)
        else:
            rel = row["article_dir"] or ProjectManager().ensure_article_dir(project_dir, row["title"], row["doi"])
            if not row["article_dir"]:
                db.execute("UPDATE articles SET article_dir = ? WHERE article_id = ?", (rel, article_id))
                db.commit()
        root = project_dir / rel
        for sub in ("source", "assets/tables", "assets/figures", "evidence", "candidates", "review", "standardized", "trace"):
            (root / sub).mkdir(parents=True, exist_ok=True)
        return root

    def _copy_file(self, source: Path, target_dir: Path) -> Path:
        dest = target_dir / source.name
        if dest.exists():
            stem = source.stem
            suffix = source.suffix
            counter = 2
            while dest.exists():
                dest = target_dir / f"{stem}_{counter}{suffix}"
                counter += 1
        shutil.copy2(source, dest)
        return dest

    def _check_duplicate(self, db: Database, file_hash: str) -> dict | None:
        row = db.fetch_one(
            "SELECT resource_id, article_id, file_name FROM resources WHERE file_hash = ?",
            (file_hash,),
        )
        if row:
            return dict(row)
        return None

    def _get_project_id(self, db: Database) -> str:
        row = db.fetch_one("SELECT project_id FROM projects LIMIT 1")
        return row["project_id"] if row else ""

    def _create_article(self, db: Database, project_id: str, file_path: Path) -> str:
        article_id = self._generate_id(db, "ART", "articles", "article_id")
        now = datetime.now().isoformat()

        title = ""
        authors = "[]"
        year = None

        # Try to extract metadata from PDF
        if file_path.suffix.lower() == ".pdf":
            try:
                import fitz  # PyMuPDF
                doc = fitz.open(str(file_path))
                meta = doc.metadata
                if meta:
                    title = meta.get("title", "") or ""
                    author = meta.get("author", "") or ""
                    if author:
                        authors = json.dumps([a.strip() for a in author.split(",") if a.strip()])
                doc.close()
            except Exception as e:
                logger.debug(f"Could not extract PDF metadata: {e}")

        if not title:
            title = file_path.stem

        db.execute(
            """INSERT INTO articles
            (article_id, project_id, title, authors, year, article_dir, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                article_id,
                project_id,
                title,
                authors,
                year,
                ProjectManager().ensure_article_dir(project_dir=self._project_dir_from_db(db), title=title),
                ArticleStatus.IMPORTED.value,
                now,
            ),
        )
        db.commit()
        return article_id

    def _project_dir_from_db(self, db: Database) -> Path:
        return db.db_path.parent

    def _relative_to_project(self, project_dir: Path, path: Path) -> str:
        try:
            return str(path.relative_to(project_dir))
        except ValueError:
            return str(path)

    def _create_resource(
        self,
        db: Database,
        article_id: str,
        resource_type: ResourceType,
        file_name: str,
        local_path: str,
        file_hash: str,
        file_size: int,
    ) -> str:
        resource_id = self._generate_id(db, "RES", "resources", "resource_id")
        now = datetime.now().isoformat()

        db.execute(
            """INSERT INTO resources
            (resource_id, article_id, resource_type, file_name, local_path,
             file_hash, file_size, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (resource_id, article_id, resource_type.value, file_name, local_path,
             file_hash, file_size, "pending", now),
        )
        db.commit()
        return resource_id

    def _log_event(
        self, db: Database, project_id: str, article_id: str,
        event_type: str, message: str,
    ) -> None:
        event_id = self._generate_id(db, "EVT", "processing_events", "event_id")
        now = datetime.now().isoformat()
        try:
            db.execute(
                """INSERT INTO processing_events
                (event_id, project_id, article_id, event_type, agent_name, message, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (event_id, project_id, article_id, event_type, "ingestion", message, now),
            )
            db.commit()
        except Exception as e:
            logger.debug(f"Failed to log event: {e}")

    def _generate_id(self, db: Database, prefix: str, table: str, column: str) -> str:
        try:
            row = db.fetch_one(
                f"SELECT MAX(CAST(SUBSTR({column}, {len(prefix) + 2}) AS INTEGER)) as max_id "
                f"FROM {table} WHERE {column} LIKE ?",
                (f"{prefix}_%",),
            )
            max_id = row["max_id"] if row and row["max_id"] else 0
        except Exception:
            max_id = 0
        return f"{prefix}_{max_id + 1:03d}"
