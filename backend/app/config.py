"""Application configuration loaded from environment / .env."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database (Postgres). Defaults to the local `guardian` db.
    database_url: str = "postgresql+psycopg://I767867@localhost:5432/guardian"

    # MedGemma via Ollama (text + vision). HF_TOKEN only needed for the
    # Transformers fallback, not the Ollama path.
    medgemma_url: str = "http://localhost:11434"
    medgemma_model: str = "medgemma:4b"

    # External integrations (optional for local dev; adapters no-op if unset).
    elevenlabs_api_key: str | None = None
    elevenlabs_agent_id: str | None = None
    elevenlabs_phone_number_id: str | None = None
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_from_number: str | None = None
    email_api_key: str | None = None
    email_from: str = "guardian@example.com"

    public_base_url: str = "http://localhost:8000"


settings = Settings()
