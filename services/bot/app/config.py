"""Configuration for Telegram Bot service."""
from __future__ import annotations

import os
from pydantic_settings import BaseSettings, SettingsConfigDict


class BotSettings(BaseSettings):
    telegram_bot_token: str = ""
    backend_url: str = "http://localhost:8010"
    web_app_url: str = "http://localhost:5173/miniapp"
    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = BotSettings()

