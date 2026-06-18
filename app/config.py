"""Application configuration.

Settings are read from the environment (and the local ``.env`` file when
present). The same ``POSTGRES_*`` variables that drive Docker Compose drive the
API, so there is a single source of truth for credentials.

Defaults target host-based development (localhost on the published port). Inside
Docker Compose the ``api`` service overrides ``POSTGRES_HOST``/``POSTGRES_PORT``
to reach Postgres over the container network.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "AgentOps Core Ingestion API"

    postgres_user: str = "agentops"
    postgres_password: str = "change-me"
    postgres_db: str = "agentops"
    postgres_host: str = "localhost"
    # Defaults to the host-published port from .env.example so tests run
    # against the running container without extra configuration.
    postgres_port: int = 54325

    @property
    def database_url(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
