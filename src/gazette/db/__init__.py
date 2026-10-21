"""Base `gazette` (PostgreSQL) : connexion, migrations, dépôts."""

from __future__ import annotations

import psycopg

from gazette.config import ConfigError, Settings


def connect(dsn: str) -> psycopg.Connection:
    """Connexion en autocommit : chaque opération des dépôts est une requête atomique."""
    return psycopg.connect(dsn, autocommit=True)


def database_url(settings: Settings) -> str:
    if not settings.database_url:
        raise ConfigError("GAZETTE_DATABASE_URL n'est pas défini (voir .env.example).")
    return settings.database_url
