from pydantic_settings import BaseSettings
from pydantic import field_validator
from typing import List
import os


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str
    SYNC_DATABASE_URL: str

    # Supabase
    SUPABASE_URL: str
    SUPABASE_ANON_KEY: str = ""
    SUPABASE_SERVICE_KEY: str = ""
    SUPABASE_JWT_SECRET: str = ""

    # JWT
    JWT_ALGORITHM: str = "ES256"
    SUPABASE_JWKS_URL: str = ""

    @property
    def jwks_url(self) -> str:
        return self.SUPABASE_JWKS_URL or f"{self.SUPABASE_URL}/auth/v1/.well-known/jwks.json"

    # CORS
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:5173"

    # Email (Resend) — optional; graceful degradation if missing
    RESEND_API_KEY: str = ""
    EMAIL_FROM: str = "noreply@reliantanchor.dev"
    EMAIL_FROM_NAME: str = "Reliant Anchor Operations"

    # WhatsApp / Twilio — optional; graceful degradation if missing
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_WHATSAPP_FROM: str = "whatsapp:+14155238886"  # Twilio sandbox default

    # App settings
    FLASK_ENV: str = "development"
    AUTO_ESCALATION_TIMEOUT_HOURS: int = 4
    MAX_UPLOAD_SIZE_MB: int = 10

    @property
    def cors_origins_list(self) -> List[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",")]

    @property
    def is_development(self) -> bool:
        return self.FLASK_ENV == "development"

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
