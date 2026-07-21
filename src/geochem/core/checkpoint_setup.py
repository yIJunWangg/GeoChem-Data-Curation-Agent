"""Initialize the PostgreSQL LangGraph checkpoint schema before Web startup."""

from __future__ import annotations

from .runtime import load_runtime_settings


def langgraph_connection_url(database_url: str) -> str:
    """Return the psycopg connection URL expected by PostgresSaver."""

    value = database_url.strip()
    for prefix in ("postgresql+psycopg://", "postgresql+psycopg2://"):
        if value.startswith(prefix):
            return "postgresql://" + value.removeprefix(prefix)
    if value.startswith("postgresql://"):
        return value
    raise ValueError("LangGraph server checkpoints require a PostgreSQL database URL")


def setup_checkpoint_database(database_url: str) -> None:
    """Create or upgrade LangGraph's checkpoint tables idempotently."""

    from langgraph.checkpoint.postgres import PostgresSaver

    context = PostgresSaver.from_conn_string(langgraph_connection_url(database_url))
    with context as saver:
        saver.setup()


def main() -> None:
    settings = load_runtime_settings()
    setup_checkpoint_database(settings.effective_checkpoint_database_url)
    print("LangGraph PostgreSQL checkpoint schema is ready.")


if __name__ == "__main__":
    main()
