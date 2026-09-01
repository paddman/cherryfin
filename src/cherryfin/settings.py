from decimal import Decimal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from CHERRYFIN_* environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="CHERRYFIN_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = "development"
    log_level: str = "INFO"

    llm_base_url: str = "http://127.0.0.1:1234/v1"
    llm_api_key: str = "local"
    llm_model: str = ""
    llm_timeout_seconds: float = Field(default=90.0, gt=0, le=600)

    execution_enabled: bool = False
    max_transaction_notional: Decimal = Field(default=Decimal("0"), ge=0)
    default_currency: str = Field(default="THB", min_length=3, max_length=3)

    max_market_data_age_minutes: int = Field(default=30, ge=1)
    max_news_age_hours: int = Field(default=24, ge=1)
