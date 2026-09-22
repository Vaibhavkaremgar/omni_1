from functools import lru_cache
from decimal import Decimal
from secrets import token_urlsafe
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = Field(default="Voice Calling SaaS API", validation_alias="APP_NAME")
    environment: str = Field(default="development", validation_alias="ENVIRONMENT")
    debug: bool = Field(default=False, validation_alias="DEBUG")
    database_url: str = Field(default="sqlite:///./backend.db", validation_alias="DATABASE_URL")
    backend_cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ],
        validation_alias="BACKEND_CORS_ORIGINS",
    )
    database_echo: bool = Field(default=False, validation_alias="DATABASE_ECHO")
    # A configured secret keeps sessions valid across restarts. The random fallback is
    # intentionally development-only and invalidates sessions when the process restarts.
    auth_secret_key: str = Field(default="", validation_alias="AUTH_SECRET_KEY")
    auth_access_token_expire_minutes: int = Field(default=1440, validation_alias="AUTH_ACCESS_TOKEN_EXPIRE_MINUTES")
    bootstrap_admin_email: str | None = Field(default=None, validation_alias="BOOTSTRAP_ADMIN_EMAIL")
    bootstrap_admin_password: str | None = Field(default=None, validation_alias="BOOTSTRAP_ADMIN_PASSWORD")
    llm_provider: str | None = Field(default=None, validation_alias="LLM_PROVIDER")
    llm_api_key: str | None = Field(default=None, validation_alias="LLM_API_KEY")
    llm_model: str | None = Field(default=None, validation_alias="LLM_MODEL")
    llm_base_url: str | None = Field(default=None, validation_alias="LLM_BASE_URL")
    llm_timeout_seconds: float = Field(default=45.0, validation_alias="LLM_TIMEOUT_SECONDS")
    # Groq-specific aliases — mapped to the generic LLM fields when present
    groq_api_key: str | None = Field(default=None, validation_alias="GROQ_API_KEY")
    groq_api_key_2: str | None = Field(default=None, validation_alias="GROQ_API_KEY_2")
    groq_model: str | None = Field(default=None, validation_alias="GROQ_MODEL")
    groq_base_url: str | None = Field(default=None, validation_alias="GROQ_BASE_URL")
    groq_base_url_2: str | None = Field(default=None, validation_alias="GROQ_BASE_URL_2")
    groq_model_2: str | None = Field(default=None, validation_alias="GROQ_MODEL_2")
    gemini_api_key: str | None = Field(default=None, validation_alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-2.5-flash-lite", validation_alias="GEMINI_MODEL")
    gemini_base_url: str = Field(default="https://generativelanguage.googleapis.com/v1beta", validation_alias="GEMINI_BASE_URL")
    gemini_research_timeout_seconds: float = Field(default=30.0, validation_alias="GEMINI_RESEARCH_TIMEOUT_SECONDS")

    @property
    def effective_llm_provider(self) -> str | None:
        if self.llm_provider:
            return self.llm_provider
        if self.gemini_api_key:
            return "gemini"
        if self.groq_api_key:
            return "groq"
        return None

    @property
    def effective_llm_api_key(self) -> str | None:
        if self.llm_provider and self.llm_provider.casefold() == "groq":
            return self.groq_api_key
        if self.llm_provider and self.llm_provider.casefold() in {"gemini", "google", "google-gemini"}:
            return self.gemini_api_key
        return self.gemini_api_key or self.llm_api_key or self.groq_api_key

    @property
    def effective_llm_model(self) -> str | None:
        if self.llm_provider and self.llm_provider.casefold() == "groq":
            return self.groq_model
        if self.llm_provider and self.llm_provider.casefold() in {"gemini", "google", "google-gemini"}:
            return self.gemini_model
        if self.gemini_api_key:
            return self.gemini_model
        return self.llm_model or self.groq_model

    @property
    def effective_llm_base_url(self) -> str | None:
        if self.llm_provider and self.llm_provider.casefold() == "groq":
            return self.groq_base_url
        if self.llm_provider and self.llm_provider.casefold() in {"gemini", "google", "google-gemini"}:
            return self.gemini_base_url
        if self.gemini_api_key:
            return self.gemini_base_url
        return self.llm_base_url or self.groq_base_url
    omnidimension_api_key: str | None = Field(default=None, validation_alias="OMNIDIMENSION_API_KEY")
    omnidimension_base_url: str = Field(
        default="https://backend.omnidim.io/api/v1",
        validation_alias="OMNIDIMENSION_BASE_URL",
    )
    omnidimension_timeout_seconds: float = Field(
        default=15.0,
        validation_alias="OMNIDIMENSION_TIMEOUT_SECONDS",
    )
    live_speech_silence_timeout_ms: int = Field(default=500, validation_alias="LIVE_SPEECH_SILENCE_TIMEOUT_MS", ge=150, le=2000)
    omnidimension_voice_catalog_json: str = Field(default="", validation_alias="OMNIDIMENSION_VOICE_CATALOG_JSON")
    phone_number_monthly_price_inr: Decimal = Field(
        default=Decimal("650.00"), validation_alias="PHONE_NUMBER_MONTHLY_PRICE_INR", ge=0
    )
    call_price_inr: Decimal = Field(default=Decimal("8.00"), validation_alias="CALL_PRICE_INR")
    minimum_call_balance_inr: Decimal = Field(
        default=Decimal("8.00"), validation_alias="MINIMUM_CALL_BALANCE_INR"
    )
    razorpay_key_id: str | None = Field(default=None, validation_alias="RAZORPAY_KEY_ID")
    razorpay_key_secret: str | None = Field(default=None, validation_alias="RAZORPAY_KEY_SECRET")
    razorpay_webhook_secret: str | None = Field(default=None, validation_alias="RAZORPAY_WEBHOOK_SECRET")
    razorpay_minimum_top_up_inr: Decimal = Field(default=Decimal("10.00"), validation_alias="RAZORPAY_MINIMUM_TOP_UP_INR")
    razorpay_maximum_top_up_inr: Decimal = Field(default=Decimal("100000.00"), validation_alias="RAZORPAY_MAXIMUM_TOP_UP_INR")
    # OAuth client credentials — backend only, never returned through API responses
    backend_public_url: str = Field(default="http://localhost:8000", validation_alias="BACKEND_PUBLIC_URL")
    frontend_url: str = Field(default="http://localhost:5173", validation_alias="FRONTEND_URL")
    hubspot_oauth_client_id: str | None = Field(default=None, validation_alias="HUBSPOT_OAUTH_CLIENT_ID")
    hubspot_oauth_client_secret: str | None = Field(default=None, validation_alias="HUBSPOT_OAUTH_CLIENT_SECRET")
    hubspot_oauth_redirect_uri: str | None = Field(default=None, validation_alias="HUBSPOT_OAUTH_REDIRECT_URI")
    salesforce_oauth_client_id: str | None = Field(default=None, validation_alias="SALESFORCE_OAUTH_CLIENT_ID")
    salesforce_oauth_client_secret: str | None = Field(default=None, validation_alias="SALESFORCE_OAUTH_CLIENT_SECRET")
    salesforce_oauth_redirect_uri: str | None = Field(default=None, validation_alias="SALESFORCE_OAUTH_REDIRECT_URI")
    google_calendar_oauth_client_id: str | None = Field(default=None, validation_alias="GOOGLE_CALENDAR_OAUTH_CLIENT_ID")
    google_calendar_oauth_client_secret: str | None = Field(default=None, validation_alias="GOOGLE_CALENDAR_OAUTH_CLIENT_SECRET")
    google_calendar_oauth_redirect_uri: str | None = Field(default=None, validation_alias="GOOGLE_CALENDAR_OAUTH_REDIRECT_URI")
    slack_oauth_client_id: str | None = Field(default=None, validation_alias="SLACK_OAUTH_CLIENT_ID")
    slack_oauth_client_secret: str | None = Field(default=None, validation_alias="SLACK_OAUTH_CLIENT_SECRET")
    slack_oauth_redirect_uri: str | None = Field(default=None, validation_alias="SLACK_OAUTH_REDIRECT_URI")
    ghl_oauth_client_id: str | None = Field(default=None, validation_alias="GHL_OAUTH_CLIENT_ID")
    ghl_oauth_client_secret: str | None = Field(default=None, validation_alias="GHL_OAUTH_CLIENT_SECRET")
    ghl_oauth_redirect_uri: str | None = Field(default=None, validation_alias="GHL_OAUTH_REDIRECT_URI")

    # Resolve the backend env file from this module, not the process cwd. The
    # API is commonly launched from the repository root, while `.env` lives in
    # backend/.env. Explicit process environment variables still take priority.
    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).resolve().parents[2] / ".env"),
        enable_decoding=False,
        extra="ignore",
    )

    def model_post_init(self, __context) -> None:
        if self.environment.lower() in {"production", "prod"} and not self.auth_secret_key:
            raise ValueError("AUTH_SECRET_KEY must be configured in production")
        if not self.auth_secret_key:
            self.auth_secret_key = token_urlsafe(32)

    @field_validator("debug", mode="before")
    @classmethod
    def parse_debug(cls, value: bool | str) -> bool:
        if isinstance(value, bool):
            return value
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        return False

    @field_validator("backend_cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, list):
            return value
        return [item.strip() for item in value.split(",") if item.strip()]

    @field_validator("llm_timeout_seconds", mode="before")
    @classmethod
    def parse_timeout(cls, value: float | str) -> float:
        if isinstance(value, (int, float)):
            return float(value)
        try:
            return float(value)
        except (TypeError, ValueError):
            return 45.0

    @field_validator("omnidimension_timeout_seconds", mode="before")
    @classmethod
    def parse_omnidimension_timeout(cls, value: float | str) -> float:
        if isinstance(value, (int, float)):
            return float(value)
        try:
            return float(value)
        except (TypeError, ValueError):
            return 15.0

    @field_validator(
        "hubspot_oauth_client_id", "hubspot_oauth_client_secret", "hubspot_oauth_redirect_uri",
        "salesforce_oauth_client_id", "salesforce_oauth_client_secret", "salesforce_oauth_redirect_uri",
        "google_calendar_oauth_client_id", "google_calendar_oauth_client_secret", "google_calendar_oauth_redirect_uri",
        "slack_oauth_client_id", "slack_oauth_client_secret", "slack_oauth_redirect_uri",
        "ghl_oauth_client_id", "ghl_oauth_client_secret", "ghl_oauth_redirect_uri",
        mode="before",
    )
    @classmethod
    def empty_str_to_none(cls, value: str | None) -> str | None:
        """Treat blank env values (e.g. KEY=) identically to absent ones."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    def validate_production_secrets(self) -> None:
        if self.environment.lower() in {"production", "prod"} and not self.auth_secret_key:
            raise ValueError("AUTH_SECRET_KEY must be configured in production")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
