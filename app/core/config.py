from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # MongoDB Config
    MONGO_URL: str
    MONGO_DB_NAME: str = "cleaning_one"

    # JWT Config
    SECRET_KEY: str
    REFRESH_SECRET_KEY: str
    ADMIN_CREATION_SECRET: str = "super_admin_secret_key_123"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 120
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    # Email / SMTP Config
    SMTP_HOST: Optional[str] = None
    SMTP_PORT: Optional[int] = None
    SMTP_USER: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    FROM_EMAIL: Optional[str] = None

    # AWS Config
    AWS_ACCESS_KEY_ID: Optional[str] = None
    AWS_SECRET_ACCESS_KEY: Optional[str] = None
    AWS_REGION: Optional[str] = None
    AWS_S3_BUCKET_NAME: Optional[str] = None

    # OneSignal Config
    ONESIGNAL_APP_ID: Optional[str] = None
    ONESIGNAL_REST_API_KEY: Optional[str] = None

    # FAQ
    FAQ_SHEET_LINK: Optional[str] = None

    # Server Config
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8080
    CLIENT_PORT: int = 8082
    WORKER_PORT: int = 8083
    MANAGER_PORT: int = 8084
    ADMIN_PORT: int = 8085

    # Timezone Config
    DEFAULT_TIMEZONE: str = "Europe/Amsterdam"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

settings = Settings()
