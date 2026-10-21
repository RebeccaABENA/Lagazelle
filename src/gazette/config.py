"""Configuration lue depuis l'environnement (et un éventuel fichier .env)."""

from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from gazette import __version__

PROJECT_URL = "https://github.com/RebeccaABENA/Lagazelle"


class ConfigError(RuntimeError):
    """Configuration manquante ou invalide."""


class Settings(BaseModel):
    data_dir: Path = Path("data")
    sources_file: Path = Path("sources.csv")
    timezone: str = "Europe/Paris"
    contact_email: str = ""
    http_timeout: float = Field(20.0, gt=0)
    # Pause minimale (secondes) entre deux requêtes vers un même domaine.
    domain_delay: float = Field(2.0, ge=0)
    # Nombre total d'essais par requête (1 essai + 2 relances au plus).
    max_attempts: int = Field(3, ge=1, le=3)
    database_url: str | None = None

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def state_dir(self) -> Path:
        return self.data_dir / "state"

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def user_agent(self) -> str:
        if not self.contact_email or "@" not in self.contact_email:
            raise ConfigError(
                "GAZETTE_CONTACT_EMAIL doit contenir un e-mail de contact (voir .env.example)."
            )
        return f"LaGazette/{__version__} (+{PROJECT_URL}; contact: {self.contact_email})"

    def now(self) -> datetime:
        """Heure courante, avec fuseau, à la seconde près."""
        return datetime.now(self.tz).replace(microsecond=0)

    def today(self) -> date:
        """Jour de collecte courant, en heure de Paris (ou du fuseau configuré)."""
        return self.now().date()


# Variable d'environnement -> champ de Settings
_ENV_MAP = {
    "GAZETTE_DATA_DIR": "data_dir",
    "GAZETTE_SOURCES_FILE": "sources_file",
    "GAZETTE_TIMEZONE": "timezone",
    "GAZETTE_CONTACT_EMAIL": "contact_email",
    "GAZETTE_HTTP_TIMEOUT": "http_timeout",
    "GAZETTE_DOMAIN_DELAY": "domain_delay",
    "GAZETTE_MAX_ATTEMPTS": "max_attempts",
    "GAZETTE_DATABASE_URL": "database_url",
}


def load_settings(env_file: str | Path | None = ".env") -> Settings:
    """Construit les réglages. Les variables déjà définies priment sur le fichier .env."""
    if env_file is not None:
        load_dotenv(env_file, override=False)
    values = {
        field: os.environ[var] for var, field in _ENV_MAP.items() if os.environ.get(var, "") != ""
    }
    return Settings(**values)
