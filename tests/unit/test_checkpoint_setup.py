from __future__ import annotations

import pytest

from geochem.core.checkpoint_setup import langgraph_connection_url


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("postgresql+psycopg://user:pass@db/geochem", "postgresql://user:pass@db/geochem"),
        ("postgresql+psycopg2://user:pass@db/geochem", "postgresql://user:pass@db/geochem"),
        ("postgresql://user:pass@db/geochem", "postgresql://user:pass@db/geochem"),
    ],
)
def test_langgraph_connection_url(source: str, expected: str):
    assert langgraph_connection_url(source) == expected


def test_langgraph_connection_url_rejects_sqlite():
    with pytest.raises(ValueError, match="PostgreSQL"):
        langgraph_connection_url("sqlite:///agent.sqlite")
