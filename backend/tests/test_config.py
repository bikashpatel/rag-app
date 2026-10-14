"""Settings that are derived rather than read as-is (app.config)."""

import pytest
from sqlalchemy import make_url

from app.config import Settings


@pytest.mark.parametrize(
    "password", ["correct horse battery staple", "a+b", "x y+z", "p@ss:w/d%#?", "x%2By", "naïve pass"]
)
def test_postgres_url_keeps_every_character_of_the_credentials(password: str) -> None:
    configured = Settings(  # type: ignore[call-arg]
        database_url=None, postgres_user="rag user", postgres_password=password, postgres_db="rag db"
    )

    url = make_url(configured.sqlalchemy_url)

    assert (url.username, url.password, url.database) == ("rag user", password, "rag db")
    assert (url.drivername, url.host, url.port) == ("postgresql+asyncpg", "postgres", 5432)


def test_database_url_wins_over_the_postgres_settings() -> None:
    configured = Settings(database_url="sqlite+aiosqlite:///x.db", postgres_password="a b")  # type: ignore[call-arg]

    assert configured.sqlalchemy_url == "sqlite+aiosqlite:///x.db"
