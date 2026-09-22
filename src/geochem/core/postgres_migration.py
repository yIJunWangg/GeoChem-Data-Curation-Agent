"""One-time migration of a local GeoChem SQLite workspace to PostgreSQL."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sqlite3
from typing import Any

from .database import Database, SCHEMA_SQL


_TABLE_PATTERN = re.compile(
    r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)


def schema_table_order() -> list[str]:
    """Return canonical application tables in foreign-key-friendly order."""

    tables = list(dict.fromkeys(_TABLE_PATTERN.findall(SCHEMA_SQL)))
    # SQLite declares these as FTS virtual tables; PostgreSQL materializes them
    # as ordinary tables with tsvector GIN indexes.
    for table, parent in (
        ("retrieval_fts", "retrieval_documents"),
        ("retrieval_chunk_fts", "retrieval_chunks"),
    ):
        if table not in tables:
            anchor = tables.index(parent) + 1
            tables.insert(anchor, table)
    return tables


def sqlite_tables(connection: sqlite3.Connection) -> set[str]:
    canonical = set(schema_table_order())
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
        ).fetchall()
        if str(row[0]) in canonical
    }


def _source_columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')]


def _target_columns(database: Database, table: str) -> list[str]:
    return [
        str(row["column_name"])
        for row in database.fetch_all(
            """SELECT column_name FROM information_schema.columns
               WHERE table_schema = current_schema() AND table_name = ?
               ORDER BY ordinal_position""",
            (table,),
        )
    ]


def _assert_empty_target(database: Database, tables: list[str]) -> None:
    occupied: list[str] = []
    for table in tables:
        try:
            row = database.fetch_one(f'SELECT COUNT(*) AS n FROM "{table}"')
        except Exception:
            continue
        if row and int(row["n"] or 0) > 0:
            occupied.append(table)
    if occupied:
        raise ValueError(
            "Target PostgreSQL database is not empty. Refusing to overwrite: "
            + ", ".join(occupied[:12])
        )


def migrate_sqlite_to_postgres(
    sqlite_path: str | Path,
    database_url: str,
    *,
    require_empty: bool = True,
    batch_size: int = 500,
) -> dict[str, Any]:
    """Copy one local workspace database into a freshly migrated PostgreSQL DB."""

    source_path = Path(sqlite_path).expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"SQLite database not found: {source_path}")
    if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        raise ValueError("database_url must point to PostgreSQL")

    source = sqlite3.connect(str(source_path))
    source.row_factory = sqlite3.Row
    target = Database(source_path, database_url)
    target.initialize()
    tables = schema_table_order()
    try:
        available = sqlite_tables(source)
        if require_empty:
            _assert_empty_target(target, tables)
        copied: dict[str, int] = {}
        for table in tables:
            if table not in available:
                continue
            source_columns = _source_columns(source, table)
            target_column_set = set(_target_columns(target, table))
            columns = [column for column in source_columns if column in target_column_set]
            if not columns:
                continue
            quoted = ", ".join(f'"{column}"' for column in columns)
            placeholders = ", ".join("?" for _ in columns)
            cursor = source.execute(f'SELECT {quoted} FROM "{table}"')
            count = 0
            while True:
                rows = cursor.fetchmany(batch_size)
                if not rows:
                    break
                target.executemany(
                    f'INSERT INTO "{table}" ({quoted}) VALUES ({placeholders})',
                    [tuple(row[column] for column in columns) for row in rows],
                )
                count += len(rows)
            copied[table] = count

        for table in ("workflow_task_events", "agent_run_events"):
            if table not in copied:
                continue
            target.execute(
                f"""SELECT setval(
                       pg_get_serial_sequence('{table}', 'event_id'),
                       COALESCE((SELECT MAX(event_id) FROM {table}), 1),
                       EXISTS(SELECT 1 FROM {table})
                   )"""
            )
        target.commit()
        return {
            "source": str(source_path),
            "tables": copied,
            "rows": sum(copied.values()),
        }
    except Exception:
        target.rollback()
        raise
    finally:
        source.close()
        target.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate one GeoChem workspace SQLite database to PostgreSQL"
    )
    parser.add_argument("sqlite_path", help="Path to the workspace geochem.db")
    parser.add_argument(
        "--database-url",
        required=True,
        help="PostgreSQL URL, preferably supplied from GEOCHEM_DATABASE_URL",
    )
    parser.add_argument("--allow-nonempty", action="store_true")
    args = parser.parse_args()
    result = migrate_sqlite_to_postgres(
        args.sqlite_path,
        args.database_url,
        require_empty=not args.allow_nonempty,
    )
    print(f"Migrated {result['rows']} rows from {result['source']}")


if __name__ == "__main__":
    main()
