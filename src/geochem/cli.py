"""CLI entry point for GeoChem."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .core.config import load_config, save_config
from .core.exceptions import DuplicateFileError, FileImportError
from .core.logging_config import setup_logging
from .core.models import ResourceType
from .core.project import ProjectManager
from .core.schema_manager import SchemaManager
from .core.memory import MemoryStore
from .core.header_descriptions import load_header_descriptions
from .curation.header_normalizer import HeaderNormalizer
from .curation.target_headers import TargetHeaderBuilder
from .curation import (
    AuditPackageBuilder,
    CostReporter,
    LearningEngine,
    MappingEngine,
    ReviewManager,
    RuleApplicationEngine,
    StandardizationPipeline,
    TeachingManager,
    TraceService,
)
from .ingestion.browser_service import BrowserService
from .ingestion.doi_service import DOIService
from .ingestion.file_importer import FileImporter
from .providers.llm_client import LLMClient

app = typer.Typer(
    name="geochem",
    help="GeoChem Data Curation Agent - Schema-driven geochemical literature data extraction",
    no_args_is_help=True,
)
console = Console()


# --- Project commands ---

@app.command()
def new_project(
    name: str = typer.Argument(..., help="Project name"),
    project_id: str = typer.Option(None, "--id", help="Project ID (auto-generated if not set)"),
    description: str = typer.Option("", "--desc", help="Project description"),
    research_field: str = typer.Option("", "--field", help="Research field"),
):
    """Create a new project."""
    pm = ProjectManager()
    try:
        path = pm.create_project(name, project_id, description, research_field)
        console.print(f"[green]Created project:[/green] {name}")
        console.print(f"  Path: {path}")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@app.command(name="list-projects")
def list_projects():
    """List all projects."""
    pm = ProjectManager()
    projects = pm.list_projects()

    if not projects:
        console.print("[yellow]No projects found.[/yellow]")
        return

    table = Table(title="Projects")
    table.add_column("ID", style="cyan")
    table.add_column("Name", style="green")
    table.add_column("Path", style="dim")
    table.add_column("Created", style="dim")

    for p in projects:
        table.add_row(p["project_id"], p["project_name"], p["path"], p["created_at"])

    console.print(table)


@app.command()
def status(
    project: str = typer.Option(..., "--project", "-p", help="Project ID"),
):
    """Show project status."""
    pm = ProjectManager()
    try:
        config, path = pm.load_project(project)
        db = pm.get_database(project)

        article_count = db.fetch_one("SELECT COUNT(*) as cnt FROM articles")["cnt"]
        resource_count = db.fetch_one("SELECT COUNT(*) as cnt FROM resources")["cnt"]
        review_count = db.fetch_one("SELECT COUNT(*) as cnt FROM review_items WHERE status='pending'")["cnt"]
        rule_count = db.fetch_one("SELECT COUNT(*) as cnt FROM mapping_rules")["cnt"]
        llm_count = db.fetch_one("SELECT COUNT(*) as cnt FROM llm_calls")["cnt"]

        table = Table(title=f"Project: {config.project_name}")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green")
        table.add_row("Project ID", config.project_id)
        table.add_row("Path", str(path))
        table.add_row("Articles", str(article_count))
        table.add_row("Resources", str(resource_count))
        table.add_row("Pending Reviews", str(review_count))
        table.add_row("Mapping Rules", str(rule_count))
        table.add_row("LLM Calls", str(llm_count))

        console.print(table)
        db.close()
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


# --- Schema commands ---

@app.command()
def import_schema(
    project: str = typer.Option(..., "--project", "-p", help="Project ID"),
    schema_file: Path = typer.Option(..., "--file", "-f", help="Schema YAML file path"),
):
    """Import a schema file into a project."""
    import shutil
    pm = ProjectManager()
    try:
        config, path = pm.load_project(project)
        dest = path / "schema" / "geochem_schema.yaml"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(schema_file, dest)

        # Validate the schema
        sm = SchemaManager()
        sm.load_from_file(dest)
        field_names = sm.get_all_field_names()

        console.print(f"[green]Schema imported:[/green] {len(field_names)} fields")
        for name in field_names:
            console.print(f"  - {name}")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


# --- LLM commands ---

llm_app = typer.Typer(help="LLM provider management")
app.add_typer(llm_app, name="llm")


@llm_app.command(name="list")
def llm_list():
    """List all configured LLM providers and models."""
    config = load_config()
    table = Table(title="LLM Providers & Models")
    table.add_column("Provider", style="cyan")
    table.add_column("Model", style="green")
    table.add_column("Display Name")
    table.add_column("Max Tokens", justify="right")
    table.add_column("Vision", justify="center")

    for prov in config.providers:
        for model in prov.models:
            vision = "Y" if model.supports_vision else ""
            table.add_row(
                prov.name,
                model.name,
                model.display_name,
                str(model.max_tokens),
                vision,
            )
        if not prov.models:
            table.add_row(prov.name, "(no models configured)", "", "", "")

    console.print(table)


@llm_app.command(name="check")
def llm_check():
    """Check connectivity of all LLM providers."""
    config = load_config()

    # Need to initialize logging and create a temporary LLM client
    setup_logging(config.log_dir, config.log_level)
    client = LLMClient(config)

    results = client.check_providers()

    table = Table(title="LLM Provider Status")
    table.add_column("Provider", style="cyan")
    table.add_column("Status")
    table.add_column("Available Models", style="dim")

    for r in results:
        status = r["status"]
        if status == "connected":
            status_str = "[green]Connected[/green]"
        elif status == "auth_failed":
            status_str = "[red]Auth Failed[/red]"
        elif status == "not_configured":
            status_str = "[yellow]Not Configured[/yellow]"
        else:
            status_str = f"[red]{status}[/red]"

        models_str = ", ".join(r.get("models", [])[:5])
        if len(r.get("models", [])) > 5:
            models_str += f" (+{len(r['models']) - 5} more)"

        table.add_row(r["name"], status_str, models_str)

    console.print(table)


@llm_app.command(name="test")
def llm_test(
    task: str = typer.Argument("_default", help="Task name to test"),
    prompt: str = typer.Option("Say hello in one word.", "--prompt", "-p"),
    provider: str = typer.Option(None, "--provider"),
    model: str = typer.Option(None, "--model"),
):
    """Test an LLM provider with a simple prompt."""
    config = load_config()
    setup_logging(config.log_dir, config.log_level)
    client = LLMClient(config)

    console.print(f"Testing task=[cyan]{task}[/cyan]...")

    try:
        response = client.chat(
            messages=[{"role": "user", "content": prompt}],
            task_name=task,
            provider_override=provider,
            model_override=model,
            use_cache=False,
        )
        console.print(f"[green]Success![/green]")
        console.print(f"  Provider: {response.provider}")
        console.print(f"  Model: {response.model}")
        console.print(f"  Tokens: {response.input_tokens} in / {response.output_tokens} out")
        console.print(f"  Latency: {response.latency_ms}ms")
        console.print(f"  Response: {response.content[:200]}")
    except Exception as e:
        console.print(f"[red]Failed:[/red] {e}")
        raise typer.Exit(1)


@llm_app.command(name="cost")
def llm_cost(
    project: str = typer.Option(..., "--project", "-p", help="Project ID"),
    group_by: str = typer.Option("model", "--group-by", help="model, task, agent, article, or provider"),
):
    """Show LLM cost report for a project."""
    pm = ProjectManager()
    try:
        db = pm.get_database(project)

        rows = CostReporter().report(db, project, group_by)

        if not rows:
            console.print("[yellow]No LLM calls recorded for this project.[/yellow]")
            return

        table = Table(title=f"LLM Cost Report: {project}")
        label_fields = [f for f in rows[0].keys() if f not in {"calls", "input_tokens", "output_tokens", "total_tokens", "estimated_cost"}]
        for field in label_fields:
            table.add_column(field, style="cyan")
        table.add_column("Calls", justify="right")
        table.add_column("Input Tokens", justify="right")
        table.add_column("Output Tokens", justify="right")
        table.add_column("Total Tokens", justify="right")
        table.add_column("Est. Cost ($)", justify="right")

        total_cost = 0.0
        for row in rows:
            cost = row["estimated_cost"] or 0.0
            total_cost += cost
            table.add_row(
                *[str(row.get(field) or "") for field in label_fields],
                str(row["calls"]),
                f"{row['input_tokens'] or 0:,}",
                f"{row['output_tokens'] or 0:,}",
                f"{row['total_tokens'] or 0:,}",
                f"${cost:.4f}",
            )

        console.print(table)
        console.print(f"\n[bold]Total estimated cost:[/bold] ${total_cost:.4f}")
        db.close()
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


# --- Ingestion commands ---

@app.command(name="import-file")
def import_file_cmd(
    project: str = typer.Option(..., "--project", "-p", help="Project ID"),
    file: Path = typer.Option(..., "--file", "-f", help="File to import"),
    article: str = typer.Option(None, "--article", "-a", help="Attach to existing article ID"),
    force_type: str = typer.Option(None, "--type", "-t", help="Override resource type"),
):
    """Import a local file (PDF/Excel/CSV/DOCX/ZIP) into a project."""
    pm = ProjectManager()
    try:
        config, project_dir = pm.load_project(project)
        db = pm.get_database(project)

        importer = FileImporter()
        type_override = ResourceType(force_type) if force_type else None

        result = importer.import_file(
            db=db,
            project_dir=project_dir,
            file_path=file,
            article_id=article,
            resource_type_override=type_override,
        )

        console.print(f"[green]Imported:[/green] {result.file_name}")
        console.print(f"  Article: {result.article_id}")
        console.print(f"  Resource: {result.resource_id}")
        console.print(f"  Type: {result.resource_type.value if result.resource_type else 'unknown'}")
        console.print(f"  Hash: {result.file_hash}")
        console.print(f"  Size: {result.file_size:,} bytes")

        db.close()
    except DuplicateFileError as e:
        console.print(f"[yellow]Duplicate:[/yellow] {e}")
    except FileImportError as e:
        console.print(f"[red]Import error:[/red] {e}")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@app.command(name="import-doi")
def import_doi_cmd(
    project: str = typer.Option(..., "--project", "-p", help="Project ID"),
    doi: str = typer.Argument(..., help="DOI to import (e.g., 10.1038/s41598-022-17105-2)"),
    open_browser: bool = typer.Option(True, "--browser/--no-browser", help="Open browser for auth"),
):
    """Import an article by DOI: resolve metadata, optionally open browser for full-text."""
    pm = ProjectManager()
    try:
        config, project_dir = pm.load_project(project)
        db = pm.get_database(project)
        project_id = config.project_id

        doi_service = DOIService()
        importer = FileImporter()

        # Step 1: Resolve DOI metadata
        console.print(f"Resolving DOI: [cyan]{doi}[/cyan]")
        metadata = doi_service.resolve(doi)
        console.print(f"  Title: {metadata.title}")
        authors_str = ", ".join(metadata.authors[:3])
        if len(metadata.authors) > 3:
            authors_str += f" (+{len(metadata.authors) - 3} more)"
        console.print(f"  Authors: {authors_str}")
        console.print(f"  Year: {metadata.year}")
        console.print(f"  Journal: {metadata.journal}")

        # Step 2: Create article record
        article_id = importer.create_article_from_metadata(db, project_id, metadata)
        console.print(f"[green]Article created:[/green] {article_id}")

        # Step 3: Optionally open browser
        if open_browser:
            url = doi_service.doi_to_url(doi)
            console.print(f"Opening browser: {url}")
            browser_svc = BrowserService(doi_service=doi_service)
            browser_svc.open_url(url)
            console.print("[yellow]Please authenticate in your browser, then press Enter here...[/yellow]")
            input()

            content = browser_svc.read_after_auth(url)
            if content:
                console.print(f"[green]Page content extracted:[/green] {len(content)} chars")
                importer.create_web_resource(db, article_id, url, content, project_dir)
            else:
                console.print("[yellow]Could not extract content. You can import downloaded files later.[/yellow]")

        console.print(f"\n[green]Done.[/green] Article {article_id} is ready for extraction.")
        db.close()
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


# --- Extraction commands ---

@app.command()
def extract(
    project: str = typer.Option(..., "--project", "-p", help="Project ID"),
    resource: str = typer.Option(..., "--resource", "-r", help="Resource ID to extract from"),
    schema_file: Path = typer.Option(None, "--schema", "-s", help="Schema YAML file (uses project schema if not set)"),
    header_descriptions: Path = typer.Option(None, "--header-descriptions", help="JSON file with user header descriptions"),
    llm_map: bool = typer.Option(False, "--llm-map", help="Use LLM during extraction; default saves full candidate rows first"),
    provider: str = typer.Option(None, "--provider", help="Override LLM provider"),
    model: str = typer.Option(None, "--model", help="Override LLM model"),
):
    """Extract structured data from an imported resource using LLM."""
    from .extractors import CsvReader, ExcelReader, LLMExtractor, PdfReader, ResultPersistence

    pm = ProjectManager()
    try:
        config, project_dir = pm.load_project(project)
        db = pm.get_database(project)
        project_id = config.project_id

        # Get resource info
        resource_row = db.fetch_one(
            "SELECT r.*, a.title, a.article_id FROM resources r "
            "JOIN articles a ON r.article_id = a.article_id "
            "WHERE r.resource_id = ?",
            (resource,),
        )
        if not resource_row:
            console.print(f"[red]Resource not found:[/red] {resource}")
            raise typer.Exit(1)

        article_id = resource_row["article_id"]
        resource_type = resource_row["resource_type"]
        file_name = resource_row["file_name"]
        local_path = resource_row["local_path"]

        console.print(f"Extracting from: [cyan]{file_name}[/cyan] ({resource_type})")
        console.print(f"  Article: {article_id} — {resource_row['title']}")

        # Resolve file path
        file_path = project_dir / local_path
        if not file_path.exists():
            # Try absolute path
            file_path = Path(local_path)
        if not file_path.exists():
            console.print(f"[red]File not found:[/red] {local_path}")
            raise typer.Exit(1)

        # Load schema
        sm = SchemaManager()
        if schema_file:
            sm.load_from_file(schema_file)
        else:
            default_schema = project_dir / "schema" / "geochem_schema.yaml"
            if default_schema.exists():
                sm.load_from_file(default_schema)
            else:
                console.print("[red]No schema found. Use --schema or import-schema first.[/red]")
                raise typer.Exit(1)

        field_count = len(sm.get_all_field_names())
        console.print(f"  Schema: {field_count} fields loaded")

        # Select reader
        reader_map = {
            "supplementary_excel": ExcelReader,
            "main_pdf": PdfReader,
            "supplementary_pdf": PdfReader,
            "supplementary_csv": CsvReader,
            "html_page": CsvReader,
        }
        reader_cls = reader_map.get(resource_type, ExcelReader)
        reader = reader_cls()

        # Read file
        console.print(f"  Reading file with {reader_cls.__name__}...")
        contents = reader.read(file_path)
        if not contents:
            console.print("[yellow]No extractable content found.[/yellow]")
            raise typer.Exit(1)

        persistence = ResultPersistence()
        extractor = None
        if llm_map:
            app_config = load_config()
            setup_logging(app_config.log_dir, app_config.log_level)
            llm_client = LLMClient(app_config, db=db)
            descriptions = load_header_descriptions(header_descriptions) if header_descriptions else {}
            extractor = LLMExtractor(llm_client, sm, header_descriptions=descriptions)

        # Extract from each content block
        total_rows = 0
        total_tables = 0

        for content in contents:
            label = content.sheet_name or content.title or "data"
            console.print(f"\n  Extracting: [cyan]{label}[/cyan] ({content.row_count} rows, {len(content.headers)} columns)")
            if extractor:
                article_metadata = _article_metadata_for_llm(resource_row, content)
                result = extractor.extract(
                    content,
                    article_metadata=article_metadata,
                    project_id=project_id,
                    article_id=article_id,
                    resource_id=resource,
                )
                rows = result.rows
                matched_fields = result.matched_fields
                confidence = result.confidence
                status = result.status
                error = result.error
                extract_method = "llm"
            else:
                display_headers = content.raw_headers or content.headers
                rows = []
                for raw_row in content.raw_rows:
                    row = {}
                    for i, header in enumerate(display_headers):
                        if header is None or str(header).strip() == "":
                            continue
                        row[str(header)] = raw_row[i] if i < len(raw_row) else None
                    rows.append(row)
                matched_fields = {}
                confidence = 1.0
                status = "success" if rows else "error"
                error = None if rows else "No rows extracted"
                extract_method = "reader"

            if status == "success" and rows:
                table_id = persistence.save(
                    db, article_id, resource,
                    content.text, rows,
                    content.raw_headers or content.headers, matched_fields,
                    header_units=content.header_units,
                    source_type=content.source_type,
                    sheet_name=content.sheet_name,
                    page_number=content.page_number,
                    confidence=confidence,
                    extract_method=extract_method,
                )
                console.print(f"    [green]OK[/green] — {len(rows)} rows, confidence={confidence:.1%}, table_id={table_id}")
                total_rows += len(rows)
                total_tables += 1
            else:
                console.print(f"    [red]Failed:[/red] {error or 'no data extracted'}")

        console.print(f"\n[bold green]Done.[/bold green] {total_tables} table(s), {total_rows} rows extracted.")
        db.close()

    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


def _article_metadata_for_llm(resource_row, content) -> dict:
    """Build row-free article metadata for LLM mapping prompts."""
    metadata = {"title": resource_row["title"], "doi": resource_row["doi"] if "doi" in resource_row.keys() else ""}
    # Only article-level metadata columns are sampled. Numeric/geochemical row values are never sent.
    allowed = {
        "Title": "title",
        "DOI": "doi",
        "Reference": "reference",
        "FirstAuthor": "first_author",
        "Year": "year",
        "DataSource": "data_source",
    }
    if content.raw_rows:
        first_row = content.raw_rows[0]
        parts = []
        for i, header in enumerate(content.headers):
            if header not in allowed or i >= len(first_row):
                continue
            value = first_row[i]
            if value is None or str(value).strip() == "":
                continue
            key = allowed[header]
            metadata[key] = str(value)
            parts.append(f"{key}: {value}")
        if parts:
            metadata["context"] = "\n".join(parts)
    return metadata


@app.command(name="map")
def map_cmd(
    project: str = typer.Option(..., "--project", "-p", help="Project ID"),
    table: str = typer.Option(..., "--table", "-t", help="Candidate table ID"),
    no_llm: bool = typer.Option(False, "--no-llm", help="Disable LLM fallback"),
    grouped: bool = typer.Option(False, "--grouped", help="Use grouped LLM fallback by field type"),
    header_descriptions: Path = typer.Option(None, "--header-descriptions", help="JSON file with user header descriptions"),
    provider: str = typer.Option(None, "--provider", help="Override LLM provider"),
    model: str = typer.Option(None, "--model", help="Override LLM model"),
):
    """Create field mapping suggestions for a candidate table."""
    pm = ProjectManager()
    try:
        config, project_dir = pm.load_project(project)
        db = pm.get_database(project)

        schema_path = project_dir / "schema" / "geochem_schema.yaml"
        if not schema_path.exists():
            console.print("[red]No project schema found. Use import-schema first.[/red]")
            raise typer.Exit(1)
        sm = SchemaManager()
        sm.load_from_file(schema_path)

        memory_path = project_dir / "memory" / "mapping_rules.yaml"
        memory = MemoryStore(memory_path)
        memory.load()

        llm_client = None
        if not no_llm:
            app_config = load_config()
            setup_logging(app_config.log_dir, app_config.log_level)
            llm_client = LLMClient(app_config, db=db)

        engine = MappingEngine(
            sm,
            memory_store=memory,
            llm_client=llm_client,
            provider_override=provider,
            model_override=model,
        )
        suggestions = engine.map_table(
            db,
            table,
            project_id=project,
            use_llm=not no_llm,
            grouped=grouped,
            header_descriptions_path=str(header_descriptions) if header_descriptions else None,
        )
        review_count = ReviewManager().create_for_table(db, table)

        console.print(f"[green]Mapped:[/green] {len(suggestions)} field(s)")
        console.print(f"  Review items created: {review_count}")
        db.close()
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


teach_app = typer.Typer(help="User teaching and extraction-rule learning")
app.add_typer(teach_app, name="teach")


@teach_app.command(name="add")
def teach_add(
    project: str = typer.Option(..., "--project", "-p", help="Project ID"),
    article: str = typer.Option(..., "--article", "-a", help="Article ID"),
    table: str = typer.Option(None, "--table", "-t", help="Candidate table ID"),
    sample_id: str = typer.Option(None, "--sample-id", help="Sample ID for row matching"),
    target_header: str = typer.Option("", "--target-header", help="User-facing target header"),
    target_field: str = typer.Option(..., "--target-field", help="Canonical target field"),
    unit: str = typer.Option(None, "--unit", help="Target/source unit for the taught value"),
    value: str = typer.Option(..., "--value", help="Taught value"),
    source: str = typer.Option("", "--source", help="appendix, table, text, etc."),
    evidence: str = typer.Option("", "--evidence", help="Textual evidence or location"),
    notes: str = typer.Option("", "--notes", help="Optional notes"),
):
    """Save a user teaching event and current-value patch."""
    pm = ProjectManager()
    try:
        db = pm.get_database(project)
        result = TeachingManager().add_event(
            db=db,
            project_id=project,
            article_id=article,
            table_id=table,
            sample_id=sample_id,
            target_header=target_header,
            target_field=target_field,
            unit=unit,
            value=value,
            source_type=source,
            evidence=evidence,
            notes=notes,
        )
        console.print(f"[green]Teaching saved:[/green] {result['event_id']}")
        console.print(f"  Patch: {result['patch_id']}")
        db.close()
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@teach_app.command(name="list")
def teach_list(
    project: str = typer.Option(..., "--project", "-p", help="Project ID"),
    article: str = typer.Option(None, "--article", "-a", help="Article ID"),
):
    """List teaching events."""
    pm = ProjectManager()
    try:
        db = pm.get_database(project)
        rows = TeachingManager().list_events(db, project, article)
        if not rows:
            console.print("[yellow]No teaching events found.[/yellow]")
            return
        table = Table(title=f"Teaching Events: {project}")
        table.add_column("ID", style="cyan")
        table.add_column("Article")
        table.add_column("Sample")
        table.add_column("Target", style="green")
        table.add_column("Value")
        table.add_column("Source")
        table.add_column("Evidence")
        for row in rows:
            table.add_row(
                row["event_id"],
                row["article_id"],
                row["sample_id"] or "",
                row["target_header"] or row["target_field"],
                row["value"] or "",
                row["source_type"] or "",
                (row["evidence"] or "")[:80],
            )
        console.print(table)
        db.close()
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@teach_app.command(name="learn")
def teach_learn(
    project: str = typer.Option(..., "--project", "-p", help="Project ID"),
    article: str = typer.Option(..., "--article", "-a", help="Article ID"),
    provider: str = typer.Option(None, "--provider", help="Override LLM provider"),
    model: str = typer.Option(None, "--model", help="Override LLM model"),
    no_llm: bool = typer.Option(False, "--no-llm", help="Use local grouping instead of LLM"),
):
    """Learn reusable extraction rules from teaching events."""
    pm = ProjectManager()
    try:
        config, project_dir = pm.load_project(project)
        db = pm.get_database(project)
        llm_client = None
        if not no_llm:
            app_config = load_config()
            setup_logging(app_config.log_dir, app_config.log_level)
            llm_client = LLMClient(app_config, db=db)
        engine = LearningEngine(llm_client=llm_client, provider=provider, model=model)
        rules = engine.learn(
            db=db,
            project_id=project,
            article_id=article,
            memory_path=project_dir / "memory" / "extraction_rules.yaml",
        )
        console.print(f"[green]Learned rules:[/green] {len(rules)}")
        for rule in rules:
            console.print(f"  {rule['rule_id']}: {rule['target_field']} ({rule['rule_type']})")
        db.close()
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@teach_app.command(name="apply")
def teach_apply(
    project: str = typer.Option(..., "--project", "-p", help="Project ID"),
    table: str = typer.Option(..., "--table", "-t", help="Candidate table ID"),
):
    """Apply confirmed learned extraction rules to create record patches."""
    pm = ProjectManager()
    try:
        db = pm.get_database(project)
        count = RuleApplicationEngine().apply(db, project, table)
        console.print(f"[green]Patches created:[/green] {count}")
        db.close()
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@teach_app.command(name="explain")
def teach_explain(
    project: str = typer.Option(..., "--project", "-p", help="Project ID"),
    patch: str = typer.Option(..., "--patch", help="Patch ID"),
):
    """Explain a patch using its evidence and learned rule."""
    pm = ProjectManager()
    try:
        db = pm.get_database(project)
        info = TeachingManager().explain_patch(db, patch)
        console.print_json(json.dumps(info, ensure_ascii=False, default=str))
        db.close()
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


review_app = typer.Typer(help="Human review queue")
app.add_typer(review_app, name="review")


@review_app.command(name="list")
def review_list(
    project: str = typer.Option(..., "--project", "-p", help="Project ID"),
    status_filter: str = typer.Option("pending", "--status", help="Review status"),
):
    """List review items."""
    pm = ProjectManager()
    try:
        db = pm.get_database(project)
        rows = db.fetch_all(
            """SELECT * FROM review_items WHERE status = ?
            ORDER BY CASE risk_level WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, created_at""",
            (status_filter,),
        )
        if not rows:
            console.print(f"[yellow]No {status_filter} review items.[/yellow]")
            return
        table = Table(title=f"Review Items: {project}")
        table.add_column("ID", style="cyan")
        table.add_column("Risk")
        table.add_column("Source")
        table.add_column("Unit")
        table.add_column("Suggestion", style="green")
        table.add_column("Confidence", justify="right")
        for row in rows:
            table.add_row(
                row["review_id"],
                row["risk_level"],
                row["original_field"],
                row["original_unit"] or "",
                row["ai_suggestion"],
                f"{row['confidence']:.2f}",
            )
        console.print(table)
        db.close()
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@review_app.command(name="decide")
def review_decide(
    project: str = typer.Option(..., "--project", "-p", help="Project ID"),
    review: str = typer.Option(..., "--review", "-r", help="Review item ID"),
    action: str = typer.Option(..., "--action", "-a", help="accept, reject, edit, or defer"),
    target_field: str = typer.Option(None, "--target-field"),
    target_unit: str = typer.Option(None, "--target-unit"),
    formula: str = typer.Option(None, "--formula"),
    save_rule: bool = typer.Option(False, "--save-rule/--no-save-rule"),
    notes: str = typer.Option("", "--notes"),
):
    """Record a human review decision."""
    pm = ProjectManager()
    try:
        config, project_dir = pm.load_project(project)
        db = pm.get_database(project)
        result = ReviewManager().decide(
            db=db,
            review_id=review,
            action=action,
            target_field=target_field,
            target_unit=target_unit,
            formula=formula,
            save_as_rule=save_rule,
            memory_path=project_dir / "memory" / "mapping_rules.yaml",
            notes=notes,
        )
        console.print(f"[green]Decision saved:[/green] {result['decision_id']}")
        if result.get("rule_id"):
            console.print(f"  Rule saved: {result['rule_id']}")
        db.close()
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@app.command()
def standardize(
    project: str = typer.Option(..., "--project", "-p", help="Project ID"),
    table: str = typer.Option(..., "--table", "-t", help="Candidate table ID"),
):
    """Create standardized records from approved mappings."""
    pm = ProjectManager()
    try:
        config, project_dir = pm.load_project(project)
        db = pm.get_database(project)
        count = StandardizationPipeline().standardize_table(db, project_dir, table)
        console.print(f"[green]Standardized:[/green] {count} record(s)")
        db.close()
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


trace_app = typer.Typer(help="Row-level trace reports")
app.add_typer(trace_app, name="trace")


@trace_app.command(name="row")
def trace_row(
    project: str = typer.Option(..., "--project", "-p", help="Project ID"),
    record: str = typer.Option(..., "--record", "-r", help="Standardized record ID"),
):
    """Explain one standardized row."""
    pm = ProjectManager()
    try:
        db = pm.get_database(project)
        info = TraceService().trace_row(db, record)
        console.print_json(json.dumps(info, ensure_ascii=False, default=str))
        db.close()
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@trace_app.command(name="table")
def trace_table(
    project: str = typer.Option(..., "--project", "-p", help="Project ID"),
    table: str = typer.Option(..., "--table", "-t", help="Candidate table ID"),
):
    """Summarize row-level trace for a table."""
    pm = ProjectManager()
    try:
        db = pm.get_database(project)
        rows = TraceService().trace_table(db, table)
        if not rows:
            console.print("[yellow]No standardized records found.[/yellow]")
            return
        output = Table(title=f"Trace: {table}")
        output.add_column("Record", style="cyan")
        output.add_column("Source Row", justify="right")
        output.add_column("Grade")
        output.add_column("Patches", justify="right")
        output.add_column("Reviews", justify="right")
        output.add_column("Calcs", justify="right")
        for row in rows:
            output.add_row(
                row["record_id"],
                str(row["source_row"] or ""),
                row["quality_grade"],
                str(row["patch_count"]),
                str(row["review_count"]),
                str(row["calculation_count"]),
            )
        console.print(output)
        db.close()
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@app.command()
def export(
    project: str = typer.Option(..., "--project", "-p", help="Project ID"),
    table: str = typer.Option(None, "--table", "-t", help="Table ID (exports all if not set)"),
    output: Path = typer.Option(None, "--output", "-o", help="Output file path (default: project_dir/export/)"),
    format: str = typer.Option("csv", "--format", "-f", help="Output format: csv or xlsx"),
    candidates: bool = typer.Option(False, "--candidates", help="Export candidate rows instead of standardized records"),
    headers_json: Path = typer.Option(None, "--headers-json", help="User header description JSON; export these columns in order"),
    package: bool = typer.Option(False, "--package", help="Create an audit package alongside the data export"),
):
    """Export standardized data to CSV or Excel."""
    import csv
    import json as json_mod

    pm = ProjectManager()
    try:
        config, project_dir = pm.load_project(project)
        db = pm.get_database(project)

        if not candidates:
            data_file = _export_standardized(db, project_dir, table, output, format, headers_json)
            if package:
                package_dir = AuditPackageBuilder().create(
                    db=db,
                    project_id=project,
                    project_dir=project_dir,
                    table_id=table,
                    data_file=data_file,
                    export_format=format,
                )
                console.print(f"[green]Audit package:[/green] {package_dir}")
            else:
                _record_export_job(db, project, table, data_file, format)
            db.close()
            return

        # Get candidate tables to export
        if table:
            tables = db.fetch_all("SELECT * FROM candidate_tables WHERE table_id = ?", (table,))
        else:
            tables = db.fetch_all("SELECT * FROM candidate_tables ORDER BY table_id")

        if not tables:
            console.print("[yellow]No tables found to export.[/yellow]")
            raise typer.Exit(1)

        # Output directory
        if output:
            out_path = Path(output)
        else:
            out_path = project_dir / "export"
        out_path.mkdir(parents=True, exist_ok=True)

        for tbl in tables:
            table_id = tbl["table_id"]
            rows = db.fetch_all("SELECT * FROM candidate_rows WHERE table_id = ? ORDER BY row_index", (table_id,))

            if not rows:
                console.print(f"[yellow]Table {table_id} has no rows.[/yellow]")
                continue

            # Parse all rows and collect all field names
            parsed_rows = []
            all_fields = set()
            for r in rows:
                data = json_mod.loads(r["raw_data"])
                parsed_rows.append(data)
                all_fields.update(k for k, v in data.items() if v is not None)

            # Sort fields: SampleID first, then alphabetically
            fields = sorted(all_fields, key=lambda x: (x != "SampleID", x))

            # Write CSV
            if format == "csv":
                file_path = out_path / f"{table_id}.csv"
                with open(file_path, "w", newline="", encoding="utf-8-sig") as f:
                    writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
                    writer.writeheader()
                    for row in parsed_rows:
                        writer.writerow({k: row.get(k, "") for k in fields})
                console.print(f"[green]Exported:[/green] {file_path} ({len(parsed_rows)} rows, {len(fields)} columns)")

            elif format == "xlsx":
                try:
                    import openpyxl
                    wb = openpyxl.Workbook()
                    ws = wb.active
                    ws.title = table_id
                    ws.append(fields)
                    for row in parsed_rows:
                        ws.append([row.get(k, "") for k in fields])
                    file_path = out_path / f"{table_id}.xlsx"
                    wb.save(str(file_path))
                    wb.close()
                    console.print(f"[green]Exported:[/green] {file_path} ({len(parsed_rows)} rows, {len(fields)} columns)")
                except ImportError:
                    console.print("[red]openpyxl not installed. Use --format csv or install openpyxl.[/red]")
                    raise typer.Exit(1)

        db.close()
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


def _export_standardized(db, project_dir: Path, table_id: str | None, output: Path | None, format: str, headers_json: Path | None = None) -> Path:
    import csv
    import json as json_mod

    if table_id:
        records = db.fetch_all("SELECT * FROM standardized_records WHERE table_id = ? ORDER BY record_id", (table_id,))
    else:
        records = db.fetch_all("SELECT * FROM standardized_records ORDER BY record_id")
    if not records:
        console.print("[yellow]No standardized records found to export.[/yellow]")
        raise typer.Exit(1)

    out_path = Path(output) if output else project_dir / "output"
    out_path.mkdir(parents=True, exist_ok=True)

    parsed = []
    fields = set()
    requested_headers = _requested_export_headers(db, table_id, headers_json) if headers_json else []
    output_map = _build_output_header_map(db, requested_headers, table_id) if requested_headers else {}
    governance = [
        "Source_File", "Source_Table", "Source_Row", "Original_Field", "Original_Unit",
        "Original_Value", "Mapping_Rule_ID", "Calculation_ID", "Review_Status",
        "Confidence", "Processed_At", "Quality_Grade",
    ]
    for record in records:
        data = json_mod.loads(record["data"])
        output_data = _project_to_user_headers(data, requested_headers, output_map) if requested_headers else data
        original_fields = json_mod.loads(record["original_fields"])
        original_units = json_mod.loads(record["original_units"])
        original_values = json_mod.loads(record["original_values"])
        calculation_ids = json_mod.loads(record["calculation_ids"])
        review_statuses = json_mod.loads(record["review_statuses"])
        confidence_scores = json_mod.loads(record["confidence_scores"])
        fields.update(output_data.keys())
        row = dict(output_data)
        row.update({
            "Source_File": record["source_file"],
            "Source_Table": record["source_table"],
            "Source_Row": record["source_row"],
            "Original_Field": json_mod.dumps(original_fields, ensure_ascii=False),
            "Original_Unit": json_mod.dumps(original_units, ensure_ascii=False),
            "Original_Value": json_mod.dumps(original_values, ensure_ascii=False),
            "Mapping_Rule_ID": record["mapping_rule_ids"],
            "Calculation_ID": json_mod.dumps(calculation_ids, ensure_ascii=False),
            "Review_Status": json_mod.dumps(review_statuses, ensure_ascii=False),
            "Confidence": json_mod.dumps(confidence_scores, ensure_ascii=False),
            "Processed_At": record["processed_at"],
            "Quality_Grade": record["quality_grade"],
        })
        parsed.append(row)

    fieldnames = requested_headers + governance if requested_headers else sorted(fields) + governance
    file_path = out_path / f"standardized_data.{format}"
    if format == "csv":
        with open(file_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(parsed)
    elif format == "xlsx":
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "standardized_data"
        ws.append(fieldnames)
        for row in parsed:
            ws.append([row.get(k, "") for k in fieldnames])
        wb.save(file_path)
        wb.close()
    else:
        raise ValueError("format must be csv or xlsx")
    console.print(f"[green]Exported:[/green] {file_path} ({len(parsed)} records)")
    return file_path


def _requested_export_headers(db, table_id: str | None, headers_json: Path | None) -> list[str]:
    if table_id:
        columns = db.fetch_all(
            "SELECT raw_name FROM candidate_columns WHERE table_id = ? ORDER BY column_id",
            (table_id,),
        )
        raw_headers = [col["raw_name"] for col in columns if col["raw_name"]]
        if raw_headers:
            return [h.display_header for h in TargetHeaderBuilder().build(raw_headers, headers_json)]
    return list(load_header_descriptions(headers_json).keys()) if headers_json else []


def _record_export_job(db, project_id: str, table_id: str | None, data_file: Path, format: str) -> None:
    row = db.fetch_one(
        "SELECT MAX(CAST(SUBSTR(job_id, 5) AS INTEGER)) as max_id FROM export_jobs WHERE job_id LIKE 'EXP_%'"
    )
    max_id = row["max_id"] if row and row["max_id"] else 0
    job_id = f"EXP_{max_id + 1:03d}"
    record_count = 0
    article_id = None
    if table_id:
        count = db.fetch_one("SELECT COUNT(*) as cnt FROM standardized_records WHERE table_id = ?", (table_id,))
        record_count = count["cnt"] if count else 0
        table = db.fetch_one("SELECT article_id FROM candidate_tables WHERE table_id = ?", (table_id,))
        article_id = table["article_id"] if table else None
    db.execute(
        """INSERT INTO export_jobs
        (job_id, project_id, article_id, table_id, export_format, output_path, record_count, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (job_id, project_id, article_id, table_id, format, str(data_file), record_count, "success", datetime.now().isoformat()),
    )
    db.commit()


def _build_output_header_map(db, requested_headers: list[str], table_id: str | None = None) -> dict[str, str]:
    """Map canonical standardized field names back to user-requested headers."""
    normalizer = HeaderNormalizer()
    if table_id:
        mappings = db.fetch_all(
            "SELECT DISTINCT source_field, target_field FROM field_mappings WHERE table_id = ?",
            (table_id,),
        )
    else:
        mappings = db.fetch_all("SELECT DISTINCT source_field, target_field FROM field_mappings")
    result = {}
    for header in requested_headers:
        normalized_header = normalizer.normalize(header)
        for mapping in mappings:
            source_norm = normalizer.normalize(mapping["source_field"])
            if (
                mapping["source_field"] == header
                or mapping["source_field"] == normalized_header.clean
                or source_norm.clean == normalized_header.clean
                or source_norm.normalized_key == normalized_header.normalized_key
            ):
                result[mapping["target_field"]] = header
            elif mapping["target_field"] == header or mapping["target_field"] == normalized_header.normalized_key:
                result[mapping["target_field"]] = header
    return result


def _project_to_user_headers(data: dict, requested_headers: list[str], output_map: dict[str, str]) -> dict:
    row = {header: "" for header in requested_headers}
    for key, value in data.items():
        header = output_map.get(key, key if key in row else None)
        if header in row:
            row[header] = value
    return row


def main():
    app()


if __name__ == "__main__":
    main()
