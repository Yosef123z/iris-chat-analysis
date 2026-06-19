"""Application configuration for the IRIS contract API."""

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings loaded from environment variables and optional .env file."""

    OPENAI_API_KEY: str = Field(
        "",
        description="Required for real KB indexing, customer chat, and chat-batch analysis runtime.",
    )
    GPT_CHAT_MODEL: str = "gpt-4o-mini"
    INTENT_MODEL: str = "gpt-4o-mini"
    ANALYSIS_MODEL: str = "gpt-4o-mini"

    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    APP_VERSION: str = "2.0.0"
    DEBUG: bool = False

    CORS_ALLOWED_ORIGINS: list[str] = Field(default_factory=list)
    AI_BACKEND_API_KEY: str = Field(
        "",
        description="Optional shared secret for backend-to-AI calls.",
    )

    OWNER_ANALYTICS_REPORT_DIR: str = "app/data/uploads"
    BUSINESS_KB_STORAGE_DIR: str = "storage/business_kb"
    SESSION_TTL_HOURS: int = Field(
        2,
        ge=1,
        description="Temporary in-memory chat/session expiry window.",
    )

    @field_validator("DEBUG", mode="before")
    @classmethod
    def _parse_debug_flag(cls, value):
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "t", "yes", "y", "on", "debug", "dev", "development"}:
                return True
            if normalized in {"0", "false", "f", "no", "n", "off", "release", "prod", "production"}:
                return False
        return value

    @field_validator("CORS_ALLOWED_ORIGINS", mode="before")
    @classmethod
    def _parse_cors_origins(cls, value):
        if value is None or value == "":
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
