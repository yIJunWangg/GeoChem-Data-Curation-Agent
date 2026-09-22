"""Database dialect helpers used by the SQLite/PostgreSQL compatibility layer."""

from __future__ import annotations

from functools import lru_cache
import hashlib
import os
import re
import threading
from typing import Any, Iterable


_ENGINE_LOCK = threading.Lock()


def normalize_postgres_url(url: str) -> str:
    """Use psycopg 3 explicitly when a plain PostgreSQL URL is supplied."""

    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url.removeprefix("postgresql://")
    return url


@lru_cache(maxsize=8)
def sqlalchemy_engine(url: str):
    """Return one process-wide pooled engine per database URL."""

    from sqlalchemy import create_engine

    normalized = normalize_postgres_url(url)
    pool_size = max(1, min(20, int(os.environ.get("GEOCHEM_DB_POOL_SIZE", "5"))))
    max_overflow = max(0, min(20, int(os.environ.get("GEOCHEM_DB_MAX_OVERFLOW", "5"))))
    pool_timeout = max(5, min(120, int(os.environ.get("GEOCHEM_DB_POOL_TIMEOUT", "30"))))
    with _ENGINE_LOCK:
        return create_engine(
            normalized,
            pool_pre_ping=True,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_timeout=pool_timeout,
            pool_recycle=1800,
            future=True,
        )


def qmark_to_pyformat(sql: str) -> str:
    """Convert DB-API qmark parameters without touching quoted question marks."""

    output: list[str] = []
    single = False
    double = False
    index = 0
    while index < len(sql):
        char = sql[index]
        if char == "'" and not double:
            if single and index + 1 < len(sql) and sql[index + 1] == "'":
                output.extend((char, char))
                index += 2
                continue
            single = not single
            output.append(char)
        elif char == '"' and not single:
            double = not double
            output.append(char)
        elif char == "?" and not single and not double:
            output.append("%s")
        else:
            output.append(char)
        index += 1
    return "".join(output)


def translate_postgres_sql(sql: str) -> str:
    """Translate the small SQLite query subset used by GeoChem."""

    translated = qmark_to_pyformat(sql)
    if re.search(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", translated, flags=re.IGNORECASE):
        translated = re.sub(
            r"\bINSERT\s+OR\s+IGNORE\s+INTO\b",
            "INSERT INTO",
            translated,
            count=1,
            flags=re.IGNORECASE,
        ).rstrip().rstrip(";")
        translated += " ON CONFLICT DO NOTHING"
    return translated


def _split_sql_script(script: str) -> list[str]:
    """Split the schema script on semicolons outside quoted values."""

    statements: list[str] = []
    buffer: list[str] = []
    single = False
    double = False
    for char in script:
        if char == "'" and not double:
            single = not single
        elif char == '"' and not single:
            double = not double
        if char == ";" and not single and not double:
            value = "".join(buffer).strip()
            if value:
                statements.append(value)
            buffer = []
        else:
            buffer.append(char)
    value = "".join(buffer).strip()
    if value:
        statements.append(value)
    return statements


def _constraint_name(table: str, columns: str, reference: str) -> str:
    raw = re.sub(r"[^a-zA-Z0-9_]+", "_", f"fk_{table}_{columns}_{reference}").lower()
    if len(raw) <= 58:
        return raw
    digest = hashlib.sha1(raw.encode()).hexdigest()[:8]
    return f"{raw[:49]}_{digest}"


def postgres_schema_statements(schema_sql: str) -> list[str]:
    """Convert GeoChem's canonical SQLite DDL into idempotent PostgreSQL DDL.

    The canonical schema remains in one place. Foreign keys are added after all
    tables exist so SQLite's permissive declaration order does not leak into the
    PostgreSQL bootstrap.
    """

    statements: list[str] = []
    foreign_keys: list[tuple[str, str, str, str]] = []
    for raw in _split_sql_script(schema_sql):
        virtual_match = re.search(
            r"CREATE VIRTUAL TABLE IF NOT EXISTS (retrieval_fts|retrieval_chunk_fts)",
            raw,
            flags=re.IGNORECASE,
        )
        if virtual_match:
            virtual_table = virtual_match.group(1)
            id_column = "document_id" if virtual_table == "retrieval_fts" else "chunk_id"
            type_column = "document_type" if virtual_table == "retrieval_fts" else "element_type"
            statements.append(
                f"""CREATE TABLE IF NOT EXISTS {virtual_table} (
                    {id_column} TEXT PRIMARY KEY,
                    content TEXT NOT NULL DEFAULT '',
                    project_id TEXT NOT NULL DEFAULT '',
                    article_id TEXT NOT NULL DEFAULT '',
                    {type_column} TEXT NOT NULL DEFAULT ''
                )"""
            )
            statements.append(
                f"CREATE INDEX IF NOT EXISTS idx_{virtual_table}_scope "
                f"ON {virtual_table}(project_id, article_id, {type_column})"
            )
            statements.append(
                f"CREATE INDEX IF NOT EXISTS idx_{virtual_table}_content "
                f"ON {virtual_table} USING GIN (to_tsvector('simple', content))"
            )
            continue

        table_match = re.search(
            r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+([A-Za-z_][A-Za-z0-9_]*)",
            raw,
            flags=re.IGNORECASE,
        )
        if table_match:
            table = table_match.group(1)
            lines: list[str] = []
            for line in raw.splitlines():
                fk_match = re.search(
                    r"FOREIGN\s+KEY\s*\(([^)]+)\)\s+REFERENCES\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]+)\)",
                    line,
                    flags=re.IGNORECASE,
                )
                if fk_match:
                    foreign_keys.append(
                        (table, fk_match.group(1).strip(), fk_match.group(2).strip(), fk_match.group(3).strip())
                    )
                    continue
                lines.append(line)
            converted = "\n".join(lines)
            converted = re.sub(r",\s*\)$", "\n)", converted.strip(), flags=re.DOTALL)
            converted = re.sub(
                r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT",
                "BIGSERIAL PRIMARY KEY",
                converted,
                flags=re.IGNORECASE,
            )
            statements.append(converted)
            continue

        if re.search(r"CREATE\s+INDEX", raw, flags=re.IGNORECASE):
            statements.append(raw)

    for table, columns, reference_table, reference_columns in foreign_keys:
        name = _constraint_name(table, columns, reference_table)
        statements.append(
            f"""DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = '{name}') THEN
                    ALTER TABLE {table} ADD CONSTRAINT {name}
                    FOREIGN KEY ({columns}) REFERENCES {reference_table} ({reference_columns});
                END IF;
            END
            $$"""
        )
    return statements


class PostgresConnection:
    """Small SQLAlchemy wrapper matching the existing Database API."""

    dialect = "postgresql"

    def __init__(self, database_url: str):
        self.database_url = database_url
        self.engine = sqlalchemy_engine(database_url)
        self.connection = None

    def connect(self):
        if self.connection is None:
            self.connection = self.engine.connect()
        return self.connection

    def execute(self, sql: str, params: tuple[Any, ...] = ()):
        return self.connect().exec_driver_sql(translate_postgres_sql(sql), params)

    def executemany(self, sql: str, params_list: Iterable[tuple[Any, ...]]):
        return self.connect().exec_driver_sql(translate_postgres_sql(sql), list(params_list))

    def fetch_one(self, sql: str, params: tuple[Any, ...] = ()):
        return self.execute(sql, params).mappings().first()

    def fetch_all(self, sql: str, params: tuple[Any, ...] = ()):
        return list(self.execute(sql, params).mappings().all())

    def commit(self) -> None:
        self.connect().commit()

    def rollback(self) -> None:
        self.connect().rollback()

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None
