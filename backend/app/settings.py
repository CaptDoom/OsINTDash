from __future__ import annotations

from functools import lru_cache
from typing import List, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Drishya 2.0 API"
    environment: str = Field(default="development", validation_alias="APP_ENV")
    debug: bool = False
    api_host: str = "0.0.0.0"
    api_port: int = 3001

    database_url: str = Field(
        default="postgresql+asyncpg://drishya_user:drishya_password@localhost:5432/drishya_db",
        validation_alias="DATABASE_URL",
    )
    redis_url: str = Field(default="redis://localhost:6379/0", validation_alias="REDIS_URL")
    sqlite_url: str = ""

    openai_api_key: Optional[str] = Field(default=None, validation_alias="OPENAI_API_KEY")
    google_api_key: Optional[str] = Field(default=None, validation_alias="GOOGLE_API_KEY")
    newsapi_key: Optional[str] = Field(default=None, validation_alias="NEWS_API_KEY")
    world_news_api_key: Optional[str] = Field(default=None, validation_alias="WORLD_NEWS_API_KEY")
    newsdata_api_key: Optional[str] = Field(default=None, validation_alias="NEWSDATA_API_KEY")
    finnhub_api_key: Optional[str] = Field(default=None, validation_alias="FINNHUB_API_KEY")
    gnews_api_key: Optional[str] = Field(default=None, validation_alias="GNEWS_API_KEY")
    currents_api_key: Optional[str] = Field(default=None, validation_alias="CURRENTS_API_KEY")
    thenews_api_key: Optional[str] = Field(default=None, validation_alias="THENEWS_API_KEY")
    mediastack_api_key: Optional[str] = Field(default=None, validation_alias="MEDIASTACK_API_KEY")
    newscatcher_api_key: Optional[str] = Field(default=None, validation_alias="NEWSCATCHER_API_KEY")
    bing_news_api_key: Optional[str] = Field(default=None, validation_alias="BING_NEWS_API_KEY")
    websub_hub_url: Optional[str] = Field(default=None, validation_alias="WEBSUB_HUB_URL")
    callback_host: Optional[str] = Field(default=None, validation_alias="CALLBACK_HOST")
    llm_provider: Optional[str] = Field(default=None, validation_alias="LLM_PROVIDER")
    llm_model: Optional[str] = Field(default=None, validation_alias="LLM_MODEL")
    ollama_base_url: Optional[str] = Field(default=None, validation_alias="OLLAMA_BASE_URL")
    scraper_api_key: Optional[str] = Field(default=None, validation_alias="SCRAPER_API_KEY")
    nominatim_user_agent: str = Field(default="drishya-news-pipeline/1.0", validation_alias="NOMINATIM_USER_AGENT")
    aws_access_key_id: Optional[str] = Field(default=None, validation_alias="AWS_ACCESS_KEY_ID")
    aws_secret_access_key: Optional[str] = Field(default=None, validation_alias="AWS_SECRET_ACCESS_KEY")
    s3_bucket_name: str = Field(default="drishya-uploads", validation_alias="S3_BUCKET_NAME")

    use_sqlite_fallback: bool = True
    enable_inline_job_processing: bool = True
    enable_redis_dedup: bool = True

    ingestion_batch_size: int = 50
    scrape_limit_per_country: int = 50
    request_retry_count: int = 4
    request_timeout_seconds: float = 12.0
    request_backoff_base_seconds: float = 0.8
    request_backoff_max_seconds: float = 12.0
    request_concurrency: int = 6
    no_new_article_window_minutes: int = 60
    medium_priority_refresh_divisor: int = 2
    low_priority_refresh_days: int = 7

    embedding_dimensions: int = 384
    archive_max_tokens: int = 100_000
    archive_cache_ttl_seconds: int = 3 * 60 * 60
    archive_summary_chunk_size: int = 5
    fusion_top_k: int = 5
    fusion_cache_ttl_seconds: int = 24 * 60 * 60

    critical_countries: List[str] = Field(default_factory=lambda: ["CN", "PK", "AF", "MM"])
    high_countries: List[str] = Field(default_factory=lambda: ["BD", "NP", "BT", "LK", "MV", "IN"])
    medium_countries: List[str] = Field(default_factory=lambda: ["US", "RU", "UA", "IR", "IL", "JP", "KR", "TW"])
    low_countries: List[str] = Field(default_factory=list)

    country_refresh_minutes_critical: int = 15
    country_refresh_minutes_high: int = 30
    country_refresh_minutes_medium: int = 60
    country_refresh_minutes_low: int = 24 * 60 * 7

    metrics_enabled: bool = True
    service_name: str = "drishya"
    worker_name: str = "drishya-worker"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    if not settings.sqlite_url:
        from pathlib import Path

        project_root = Path(__file__).resolve().parents[2]
        data_dir = project_root / "data"
        data_dir.mkdir(exist_ok=True)
        settings.sqlite_url = f"sqlite+aiosqlite:///{(data_dir / 'articles_v2.db').as_posix()}"
    return settings


settings = get_settings()
