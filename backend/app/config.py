"""Application configuration loaded from environment / .env.

Env var names follow ELEVENLABS_TWILIO_SETUP.md so the telephony work and
this scaffold share one config surface.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- General ---
    public_base_url: str = "http://localhost:8000"
    # When true, telephony adapters log instead of calling ElevenLabs/Twilio,
    # so the app is fully testable/demoable offline.
    mock_telephony: bool = True
    database_url: str = "postgresql+psycopg://I767867@localhost:5432/guardian"

    # --- MedGemma via Ollama (clinical brain) ---
    medgemma_url: str = "http://localhost:11434"
    medgemma_model: str = "medgemma:4b"

    # --- ElevenLabs ---
    eleven_api_key: str | None = None
    eleven_agent_id: str | None = None
    eleven_agent_phone_number_id: str | None = None  # imported Twilio number
    eleven_webhook_secret: str | None = None          # post-call webhook HMAC
    eleven_tool_auth_token: str | None = None          # agent tool shared secret
    eleven_base_url: str = "https://api.elevenlabs.io"

    # --- Twilio ---
    twilio_account_sid: str | None = None
    twilio_api_key_sid: str | None = None
    twilio_api_key_secret: str | None = None
    twilio_auth_token: str | None = None               # validates inbound webhooks
    twilio_number: str | None = None

    # --- Scheduling ---
    callback_default_minutes: int = 15                 # use 1 for demos

    # --- Email (SendGrid/Resend) ---
    email_api_key: str | None = None
    email_from: str = "guardian@example.com"


settings = Settings()
