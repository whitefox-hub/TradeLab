"""Runtime configuration. Plan.md §2.2."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ExecutionMode(StrEnum):
    """The four rungs of Plan.md §12.1."""

    BACKTEST = "backtest"
    SIMULATOR = "simulator"
    TESTNET = "testnet"
    LIVE = "live"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = Field(default="dev", alias="TRADELAB_ENV")
    archive_root: Path = Field(default=Path("data/parquet"), alias="TRADELAB_ARCHIVE_ROOT")

    postgres_user: str = Field(default="tradelab", alias="POSTGRES_USER")
    postgres_password: str = Field(default="change_me_locally", alias="POSTGRES_PASSWORD")
    postgres_db: str = Field(default="tradelab", alias="POSTGRES_DB")
    postgres_host: str = Field(default="localhost", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, alias="POSTGRES_PORT")

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


def get_settings() -> Settings:
    return Settings()
