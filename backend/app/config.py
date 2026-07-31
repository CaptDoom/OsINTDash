import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://drishya_user:drishya_password@localhost:5432/drishya_db"
    REDIS_URL: str = "redis://localhost:6379/0"
    
    NEWSAPI_KEY: Optional[str] = None
    GNEWS_API_KEY: Optional[str] = None
    OPENAI_API_KEY: Optional[str] = None
    GOOGLE_API_KEY: Optional[str] = None # For Gemini API
    
    AWS_ACCESS_KEY_ID: Optional[str] = None
    AWS_SECRET_ACCESS_KEY: Optional[str] = None
    S3_BUCKET_NAME: str = "drishya-uploads"
    
    # Fallback to local SQLite and in-memory broker if services are offline
    USE_SQLITE_FALLBACK: bool = True
    SQLITE_URL: str = ""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()

# Calculate absolute path for SQLite fallback
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
data_dir = os.path.join(project_root, "data")
os.makedirs(data_dir, exist_ok=True)

db_path = os.path.join(data_dir, "articles_v2.db").replace("\\", "/")
settings.SQLITE_URL = f"sqlite+aiosqlite:///{db_path}"
