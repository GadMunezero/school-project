"""Application settings.

All configuration comes from the environment. Nothing here has a production-safe default:
`SECRET_KEY` must be supplied, and `validate_for_production()` refuses to start a production
process with development placeholders still in place.
"""

from __future__ import annotations

import functools
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "test", "staging", "production"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- runtime -----------------------------------------------------------
    environment: Environment = Field(default="development", alias="TRADELOOM_ENV")
    debug: bool = Field(default=False, alias="DEBUG")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_format: Literal["json", "console"] = Field(default="json", alias="LOG_FORMAT")

    # --- urls --------------------------------------------------------------
    backend_url: str = Field(default="http://localhost:8000", alias="BACKEND_URL")
    frontend_url: str = Field(default="http://localhost:3000", alias="FRONTEND_URL")
    cors_origins_raw: str = Field(
        default="http://localhost:3000,http://localhost:8080", alias="CORS_ORIGINS"
    )

    # --- cookies / sessions ------------------------------------------------
    secret_key: str = Field(
        default="development-only-insecure-secret-key-change-me-0123456789abcdef",
        alias="SECRET_KEY",
    )
    session_cookie_name: str = Field(default="tl_session", alias="SESSION_COOKIE_NAME")
    csrf_cookie_name: str = Field(default="tl_csrf", alias="CSRF_COOKIE_NAME")
    cookie_domain: str | None = Field(default=None, alias="COOKIE_DOMAIN")
    cookie_secure: bool = Field(default=False, alias="COOKIE_SECURE")
    session_ttl_seconds: int = Field(default=1_209_600, alias="SESSION_TTL_SECONDS")
    session_idle_timeout_seconds: int = Field(default=86_400, alias="SESSION_IDLE_TIMEOUT_SECONDS")
    session_rotate_after_seconds: int = Field(default=3_600, alias="SESSION_ROTATE_AFTER_SECONDS")
    password_reset_ttl_seconds: int = Field(default=3_600, alias="PASSWORD_RESET_TTL_SECONDS")
    email_verify_ttl_seconds: int = Field(default=86_400, alias="EMAIL_VERIFY_TTL_SECONDS")

    argon2_time_cost: int = Field(default=3, alias="ARGON2_TIME_COST")
    argon2_memory_cost_kib: int = Field(default=65_536, alias="ARGON2_MEMORY_COST_KIB")
    argon2_parallelism: int = Field(default=2, alias="ARGON2_PARALLELISM")

    # --- database ----------------------------------------------------------
    database_url: str = Field(
        default="postgresql+asyncpg://tradeloom:tradeloom@localhost:5432/tradeloom",
        alias="DATABASE_URL",
    )
    database_pool_size: int = Field(default=10, alias="DATABASE_POOL_SIZE")
    database_max_overflow: int = Field(default=20, alias="DATABASE_MAX_OVERFLOW")
    database_echo: bool = Field(default=False, alias="DATABASE_ECHO")

    # --- redis / celery ----------------------------------------------------
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    celery_broker_url: str = Field(default="redis://localhost:6379/1", alias="CELERY_BROKER_URL")
    celery_result_backend: str = Field(
        default="redis://localhost:6379/2", alias="CELERY_RESULT_BACKEND"
    )
    celery_task_time_limit: int = Field(default=1800, alias="CELERY_TASK_TIME_LIMIT")
    celery_task_soft_time_limit: int = Field(default=1680, alias="CELERY_TASK_SOFT_TIME_LIMIT")
    celery_task_always_eager: bool = Field(default=False, alias="CELERY_TASK_ALWAYS_EAGER")

    # --- signup ------------------------------------------------------------
    #: ``open`` lets anyone register; ``invite`` requires a code an administrator issued.
    #:
    #: The default is ``open`` because that is what the code has always done, and flipping it
    #: silently would lock people out of running deployments. ``.env.example`` ships ``invite``,
    #: so a fresh install starts closed — which is the right way round for a beta.
    signup_mode: str = Field(default="open", alias="SIGNUP_MODE")

    @property
    def signup_is_invite_only(self) -> bool:
        return self.signup_mode.strip().lower() == "invite"

    # --- rate limiting -----------------------------------------------------
    rate_limit_enabled: bool = Field(default=True, alias="RATE_LIMIT_ENABLED")
    rate_limit_default: str = Field(default="200/minute", alias="RATE_LIMIT_DEFAULT")
    rate_limit_login: str = Field(default="10/minute", alias="RATE_LIMIT_LOGIN")
    rate_limit_signup: str = Field(default="5/hour", alias="RATE_LIMIT_SIGNUP")
    login_lockout_threshold: int = Field(default=8, alias="LOGIN_LOCKOUT_THRESHOLD")
    login_lockout_seconds: int = Field(default=900, alias="LOGIN_LOCKOUT_SECONDS")

    # --- email -------------------------------------------------------------
    email_enabled: bool = Field(default=True, alias="EMAIL_ENABLED")
    smtp_host: str = Field(default="localhost", alias="SMTP_HOST")
    smtp_port: int = Field(default=1025, alias="SMTP_PORT")
    smtp_user: str | None = Field(default=None, alias="SMTP_USER")
    smtp_password: str | None = Field(default=None, alias="SMTP_PASSWORD")
    smtp_tls: bool = Field(default=False, alias="SMTP_TLS")
    smtp_from_email: str = Field(default="no-reply@tradeloom.local", alias="SMTP_FROM_EMAIL")
    smtp_from_name: str = Field(default="Tradeloom", alias="SMTP_FROM_NAME")

    # --- oauth -------------------------------------------------------------
    oauth_google_client_id: str | None = Field(default=None, alias="OAUTH_GOOGLE_CLIENT_ID")
    oauth_google_client_secret: str | None = Field(default=None, alias="OAUTH_GOOGLE_CLIENT_SECRET")
    oauth_github_client_id: str | None = Field(default=None, alias="OAUTH_GITHUB_CLIENT_ID")
    oauth_github_client_secret: str | None = Field(default=None, alias="OAUTH_GITHUB_CLIENT_SECRET")
    oauth_redirect_base_url: str = Field(
        default="http://localhost:8000/api/v1/auth/oauth", alias="OAUTH_REDIRECT_BASE_URL"
    )

    # --- object storage ----------------------------------------------------
    s3_endpoint_url: str | None = Field(default=None, alias="S3_ENDPOINT_URL")
    s3_region: str = Field(default="us-east-1", alias="S3_REGION")
    s3_bucket: str = Field(default="tradeloom-uploads", alias="S3_BUCKET")
    s3_access_key_id: str | None = Field(default=None, alias="S3_ACCESS_KEY_ID")
    s3_secret_access_key: str | None = Field(default=None, alias="S3_SECRET_ACCESS_KEY")
    s3_force_path_style: bool = Field(default=True, alias="S3_FORCE_PATH_STYLE")
    s3_public_base_url: str | None = Field(default=None, alias="S3_PUBLIC_BASE_URL")
    s3_signed_url_ttl_seconds: int = Field(default=900, alias="S3_SIGNED_URL_TTL_SECONDS")
    upload_max_bytes: int = Field(default=10 * 1024 * 1024, alias="UPLOAD_MAX_BYTES")
    upload_allowed_mime_raw: str = Field(
        default="image/png,image/jpeg,image/webp,image/gif,text/csv,application/json",
        alias="UPLOAD_ALLOWED_MIME",
    )

    # --- billing -----------------------------------------------------------
    stripe_enabled: bool = Field(default=False, alias="STRIPE_ENABLED")
    stripe_secret_key: str | None = Field(default=None, alias="STRIPE_SECRET_KEY")
    stripe_publishable_key: str | None = Field(default=None, alias="STRIPE_PUBLISHABLE_KEY")
    stripe_webhook_secret: str | None = Field(default=None, alias="STRIPE_WEBHOOK_SECRET")
    stripe_price_pro_monthly: str | None = Field(default=None, alias="STRIPE_PRICE_PRO_MONTHLY")
    stripe_price_pro_yearly: str | None = Field(default=None, alias="STRIPE_PRICE_PRO_YEARLY")
    stripe_price_enterprise_monthly: str | None = Field(
        default=None, alias="STRIPE_PRICE_ENTERPRISE_MONTHLY"
    )
    billing_success_url: str = Field(
        default="http://localhost:3000/billing?checkout=success", alias="BILLING_SUCCESS_URL"
    )
    billing_cancel_url: str = Field(
        default="http://localhost:3000/billing?checkout=cancelled", alias="BILLING_CANCEL_URL"
    )

    # --- market data -------------------------------------------------------
    market_data_default_source: str = Field(default="seed", alias="MARKET_DATA_DEFAULT_SOURCE")

    # --- demo seed ---------------------------------------------------------
    demo_user_email: str = Field(default="demo@example.com", alias="DEMO_USER_EMAIL")
    demo_user_password: str = Field(default="DemoTrader!2024", alias="DEMO_USER_PASSWORD")
    seed_random_seed: int = Field(default=20_240_517, alias="SEED_RANDOM_SEED")

    @field_validator("log_level")
    @classmethod
    def _upper_log_level(cls, value: str) -> str:
        return value.upper()

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins_raw.split(",") if origin.strip()]

    @property
    def upload_allowed_mime(self) -> set[str]:
        return {m.strip().lower() for m in self.upload_allowed_mime_raw.split(",") if m.strip()}

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def is_test(self) -> bool:
        return self.environment == "test"

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def oauth_providers(self) -> list[str]:
        providers: list[str] = []
        if self.oauth_google_client_id and self.oauth_google_client_secret:
            providers.append("google")
        if self.oauth_github_client_id and self.oauth_github_client_secret:
            providers.append("github")
        return providers

    def validate_for_production(self) -> list[str]:
        """Return a list of blocking misconfigurations. Empty list means safe to boot."""
        problems: list[str] = []
        if not self.is_production:
            return problems
        if "development-only" in self.secret_key or len(self.secret_key) < 48:
            problems.append("SECRET_KEY must be a unique random value of at least 48 characters")
        if not self.cookie_secure:
            problems.append("COOKIE_SECURE must be true in production")
        if self.debug:
            problems.append("DEBUG must be false in production")
        if self.is_sqlite:
            problems.append("DATABASE_URL must point at PostgreSQL in production")
        if self.stripe_enabled and not self.stripe_webhook_secret:
            problems.append("STRIPE_WEBHOOK_SECRET is required when STRIPE_ENABLED is true")
        if not self.s3_access_key_id or not self.s3_secret_access_key:
            problems.append("S3 credentials are required in production")
        return problems


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """Used by tests that manipulate the environment."""
    get_settings.cache_clear()
