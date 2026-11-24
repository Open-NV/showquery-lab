"""Runtime settings, read from SHOWQUERY_* environment variables (or a .env file)."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SHOWQUERY_", env_file=".env", extra="ignore")

    mongo_uri: str = "mongodb://127.0.0.1:27017"
    mongo_db: str = "netstate"
    mongo_collection: str = "show_commands"
    mongo_max_time_ms: int = 15_000

    # Path of the osqueryd extensions socket the API connects to.
    osquery_socket: str = ".run/osquery.sock"
    osquery_timeout_s: float = 5.0

    # SQLite cannot push LIMIT into a virtual table, so an unfiltered query would read the
    # whole collection. Each generate() call refuses to return more rows than this and tells
    # the user to add a filter instead of silently truncating (which would give wrong answers).
    max_scan_rows: int = 250_000

    # Rows returned to API callers.
    api_max_result_rows: int = 5_000
    log_level: str = "info"
    # Built UI to serve from the API; defaults to <repo>/web/dist.
    web_dist: str | None = None
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
