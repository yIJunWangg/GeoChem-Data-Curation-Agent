"""Project management: create, load, list projects."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import yaml

from .database import Database
from .exceptions import ProjectAlreadyExistsError, ProjectNotFoundError
from .logging_config import get_logger
from .models import ProjectConfig

logger = get_logger("project")

PROJECT_DIRS = [
    "schema",
    "articles",
    "raw/articles",
    "raw/supplementary",
    "raw/figures",
    "parsed",
    "candidate_data",
    "review",
    "memory",
    "calculations",
    "headers",
    "logs",
    "output",
    "reports",
]

DEFAULT_WORKSPACE_ID = "DEFAULT_WORKSPACE"
ARTICLE_DIRS = [
    "source",
    "assets/tables",
    "assets/figures",
    "evidence",
    "candidates",
    "review",
    "standardized",
    "trace",
]


class ProjectManager:
    """Manage GeoChem projects."""

    def __init__(self, base_dir: str | Path | None = None):
        from .config import load_config
        config = load_config()
        self.base_dir = Path(base_dir).expanduser() if base_dir else Path(config.default_project_dir).expanduser()

    def create_project(
        self,
        project_name: str,
        project_id: str | None = None,
        description: str = "",
        research_field: str = "",
    ) -> Path:
        """Create a new project with directory structure and database."""
        if project_id is None:
            project_id = project_name.replace(" ", "_").upper()[:20]

        project_dir = self.base_dir / project_id
        if project_dir.exists():
            raise ProjectAlreadyExistsError(f"Project already exists: {project_dir}")

        now = datetime.now().isoformat()
        config = ProjectConfig(
            project_name=project_name,
            project_id=project_id,
            description=description,
            research_field=research_field,
        )

        project_dir.mkdir(parents=True, exist_ok=True)
        for d in PROJECT_DIRS:
            (project_dir / d).mkdir(parents=True, exist_ok=True)

        config_path = project_dir / "project.yaml"
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config.model_dump(), f, default_flow_style=False, allow_unicode=True, sort_keys=False)

        db = Database(project_dir / "geochem.db")
        db.initialize()

        db.execute(
            "INSERT INTO projects (project_id, project_name, description, research_field, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (project_id, project_name, description, research_field, now, now),
        )
        db.commit()
        db.close()

        logger.info(f"Created project: {project_name} ({project_id}) at {project_dir}")
        return project_dir

    def ensure_default_workspace(self) -> tuple[ProjectConfig, Path]:
        """Create or load the single UI workspace."""
        project_dir = self.base_dir / DEFAULT_WORKSPACE_ID
        if not project_dir.exists():
            self.create_project("GeoChem Workspace", DEFAULT_WORKSPACE_ID, "Default single-user workspace")
        return self.load_project(DEFAULT_WORKSPACE_ID)

    def load_project(self, project_id: str) -> tuple[ProjectConfig, Path]:
        """Load an existing project config and path."""
        project_dir = self.base_dir / project_id
        config_path = project_dir / "project.yaml"

        if not config_path.exists():
            raise ProjectNotFoundError(f"Project not found: {project_id}")

        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        config = ProjectConfig(**data)
        return config, project_dir

    def get_database(self, project_id: str) -> Database:
        """Get the database connection for a project."""
        project_dir = self.base_dir / project_id
        db = Database(project_dir / "geochem.db")
        db.initialize()
        return db

    def get_default_database(self) -> Database:
        """Get the database for the default workspace, creating it if needed."""
        config, _path = self.ensure_default_workspace()
        return self.get_database(config.project_id)

    def ensure_article_dir(
        self,
        project_dir: Path,
        title: str = "",
        doi: str | None = None,
        article_dir: str | None = None,
    ) -> str:
        """Ensure a stable per-article directory and return its project-relative path."""
        rel = article_dir or self.article_dir_name(title, doi)
        root = project_dir / "articles" / rel
        candidate = rel
        counter = 2
        while root.exists() and not article_dir:
            candidate = f"{rel}_{counter}"
            root = project_dir / "articles" / candidate
            counter += 1
        for sub in ARTICLE_DIRS:
            (root / sub).mkdir(parents=True, exist_ok=True)
        return f"articles/{candidate}"

    def article_dir_name(self, title: str = "", doi: str | None = None) -> str:
        """Build a concise article folder name from short title and DOI."""
        title_slug = self._slug(title, max_parts=5) or "untitled_article"
        doi_slug = self._slug(doi or "", max_parts=6)
        return f"{title_slug}_{doi_slug}" if doi_slug else title_slug

    def list_projects(self) -> list[dict]:
        """List all projects under the base directory."""
        projects = []
        if not self.base_dir.exists():
            return projects

        for d in sorted(self.base_dir.iterdir()):
            config_path = d / "project.yaml"
            if d.is_dir() and config_path.exists():
                with open(config_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                created = data.get("created_at", "")
                if hasattr(created, "isoformat"):
                    created = created.isoformat()
                projects.append({
                    "project_id": data.get("project_id", d.name),
                    "project_name": data.get("project_name", ""),
                    "path": str(d),
                    "created_at": str(created),
                })
        return projects

    def delete_project(self, project_id: str) -> None:
        """Delete a project directory."""
        import shutil
        project_dir = self.base_dir / project_id
        if not project_dir.exists():
            raise ProjectNotFoundError(f"Project not found: {project_id}")
        shutil.rmtree(project_dir)
        logger.info(f"Deleted project: {project_id}")

    def _slug(self, value: str, max_parts: int = 6) -> str:
        text = re.sub(r"[^A-Za-z0-9]+", "_", value or "").strip("_").lower()
        parts = [part for part in text.split("_") if part]
        return "_".join(parts[:max_parts])[:80]
