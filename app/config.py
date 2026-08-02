from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[1] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: str = Field(alias="BOT_TOKEN")
    owner_id: int = Field(default=6577441312, alias="OWNER_ID")
    bazaar_chat_id: int = Field(default=-1003377593526, alias="BAZAAR_CHAT_ID")
    bazaar_url: str = Field(default="https://t.me/bgghtrhrwbehtvrw", alias="BAZAAR_URL")
    staff_chat_id: int = Field(default=-5466156820, alias="STAFF_CHAT_ID")
    timezone: str = Field(default="Europe/Moscow", alias="TIMEZONE")
    database_url: str = Field(
        default="sqlite+aiosqlite:///./limitads.sqlite3", alias="DATABASE_URL"
    )
    webhook_base_url: str = Field(default="", alias="WEBHOOK_BASE_URL")
    telegram_webhook_path: str = Field(
        default="/telegram/local-development", alias="TELEGRAM_WEBHOOK_PATH"
    )
    telegram_webhook_secret: str = Field(
        default="local-development-secret", alias="TELEGRAM_WEBHOOK_SECRET"
    )
    web_server_host: str = Field(default="127.0.0.1", alias="WEB_SERVER_HOST")
    web_server_port: int = Field(default=8092, alias="WEB_SERVER_PORT")

    @field_validator("telegram_webhook_path")
    @classmethod
    def normalize_webhook_path(cls, value: str) -> str:
        return value if value.startswith("/") else f"/{value}"

    @property
    def webhook_url(self) -> str:
        return f"{self.webhook_base_url.rstrip('/')}{self.telegram_webhook_path}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
