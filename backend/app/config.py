"""Application configuration loaded from environment / .env.

Env var names are documented in .env.example (the source of truth for
placeholders). A few legacy names from the first scaffold (ELEVENLABS_API_KEY,
TWILIO_FROM_NUMBER, ...) are still accepted as aliases so existing .env files
keep working.
"""
from __future__ import annotations

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _env(*names: str) -> AliasChoices:
    return AliasChoices(*names)


class Settings(BaseSettings):
    # Optional override. Empty uses the agent's deployed Main branch.
    eleven_immediate_call_branch_id: str = ""
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", populate_by_name=True
    )

    # --- General -----------------------------------------------------------
    # Postgres in prod. SQLite also works for local demos/tests
    # (e.g. DATABASE_URL=sqlite:///./guardian.db) because JSON/array
    # columns are portable (see app/database.py).
    database_url: str = "postgresql+psycopg://I767867@localhost:5432/guardian"
    photo_storage_dir: str = "./private-photos"
    photo_max_bytes: int = 8 * 1024 * 1024
    photo_max_pixels: int = 20_000_000
    photo_push_enabled: bool = False
    photo_credential_days: int = 90

    public_base_url: str = "http://localhost:8000"
    public_host: str = ""  # e.g. 203-0-113-10.sslip.io (Caddy, docs only)
    timezone: str = "Europe/Berlin"
    # When true, ElevenLabs/Twilio adapters log instead of calling out.
    # Adapters also fall back to mock if their credentials are missing.
    mock_telephony: bool = False

    # --- API protection ------------------------------------------------------
    # Required X-API-Key header on all non-webhook routes. Empty = auth
    # disabled (local dev only; a warning is logged at startup).
    guardian_api_key: str = ""
    # Exact origins allowed by CORS, comma-separated (e.g. the hosted
    # Flutter web URL). Localhost on any port is allowed via the regex for
    # Flutter web dev. Never "*".
    cors_origins: str = ""
    cors_origin_regex: str = r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"

    # --- Model server (Gemma 4 on the Google Generative Language API) ------
    gemma_api_key: str | None = Field(
        None, validation_alias=_env("GOOGLE_API_KEY", "GEMMA_API_KEY")
    )
    gemma_model: str = "gemma-4-31b-it"
    gemma_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    clinical_pipeline: str = "two_stage"  # two_stage | single
    model_timeout_s: float = 120.0
    plan_timeout_s: float = 8.0  # live-call path budget
    model_max_concurrent: int = 2  # max concurrent generations (semaphore)
    history_reports_n: int = 3  # last N call reports fed to the model

    @property
    def gemma_configured(self) -> bool:
        return bool(self.gemma_api_key)

    # --- Red-flag thresholds (placeholders, to be confirmed by a clinician) -
    redflag_bp_systolic_above: float = 180.0
    redflag_bp_diastolic_above: float = 120.0
    redflag_glucose_below: float = 54.0  # mg/dL

    # --- ElevenLabs ----------------------------------------------------------
    eleven_api_key: str | None = Field(
        None, validation_alias=_env("ELEVEN_API_KEY", "ELEVENLABS_API_KEY")
    )
    eleven_agent_id: str | None = Field(
        None, validation_alias=_env("ELEVEN_AGENT_ID", "ELEVENLABS_AGENT_ID")
    )
    eleven_agent_phone_number_id: str | None = Field(
        None,
        validation_alias=_env(
            "ELEVEN_AGENT_PHONE_NUMBER_ID", "ELEVENLABS_PHONE_NUMBER_ID"
        ),
    )
    eleven_webhook_secret: str | None = None  # post-call webhook HMAC secret
    eleven_webhook_secret_file: str | None = None
    eleven_tool_auth_token: str | None = None  # Bearer token sent by agent tools
    eleven_base_url: str = "https://api.elevenlabs.io"

    # --- Twilio --------------------------------------------------------------
    twilio_account_sid: str | None = None
    twilio_api_key_sid: str | None = None
    twilio_api_key_secret: str | None = None
    twilio_auth_token: str | None = None  # validates inbound webhook signatures
    twilio_number: str | None = Field(
        None, validation_alias=_env("TWILIO_NUMBER", "TWILIO_FROM_NUMBER")
    )

    # --- Scheduling ----------------------------------------------------------
    callback_default_minutes: int = 15  # use 1 for demos
    missed_call_retry_minutes: int = 60
    max_call_attempts: int = 3
    scheduler_enabled: bool = True
    # Empty = use DATABASE_URL (jobs persist across restarts).
    scheduler_jobstore_url: str = ""

    # --- Email (Gmail SMTP with an App Password) ---
    # Create at: Google Account -> Security -> 2-Step Verification -> App passwords
    gmail_address: str | None = None          # e.g. you@gmail.com
    gmail_app_password: str | None = None      # 16-char app password (no spaces)
    email_from: str = "guardian@example.com"
    doctor_alert_email: str | None = None

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def eleven_configured(self) -> bool:
        return bool(
            self.eleven_api_key
            and self.eleven_agent_id
            and self.eleven_agent_phone_number_id
        )

    @property
    def twilio_configured(self) -> bool:
        return bool(
            self.twilio_account_sid
            and self.twilio_api_key_sid
            and self.twilio_api_key_secret
            and self.twilio_number
        )


settings = Settings()
