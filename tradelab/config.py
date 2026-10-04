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
    live_db: str = Field(default="tradelab_live", alias="TRADELAB_LIVE_DB")
    app_book: str = Field(default="sim", alias="TRADELAB_APP_BOOK", pattern="^(sim|demo|live)$")

    def _url(self, database: str) -> str:
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{database}"
        )

    @property
    def database_url(self) -> str:
        """The laboratory: research, the simulator and Binance Demo."""
        return self._url(self.postgres_db)

    @property
    def live_database_url(self) -> str:
        """Real money only, in a database of its own, so nothing the laboratory runs or tests
        can write to its records."""
        if self.live_db == self.postgres_db:
            raise ValueError(f"the live database cannot be the laboratory's ({self.postgres_db})")
        return self._url(self.live_db)

    @property
    def app_database_url(self) -> str:
        """The database the operator app reads and writes: the live one when it serves the
        real book, so its buttons reach that book, and never both."""
        return self.live_database_url if self.app_book == "live" else self.database_url


def get_settings() -> Settings:
    return Settings()
