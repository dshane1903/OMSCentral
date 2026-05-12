from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    postgres_db: str = "rag"
    postgres_user: str = "rag"
    postgres_password: str = "rag"
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    redis_host: str = "redis"
    redis_port: int = 6379
    rabbitmq_host: str = "rabbitmq"
    rabbitmq_port: int = 5672
    rabbitmq_scheme: str = "amqp"
    rabbitmq_user: str = "rag"
    rabbitmq_password: str = "rag"
    rabbitmq_retry_delay_ms: int = 30000
    rabbitmq_consumer_prefetch: int = 4
    document_storage_path: Path = Path("/data/documents")
    omscentral_base_url: str = "https://www.omscentral.com"
    omscentral_request_timeout_seconds: float = 30.0
    omscentral_user_agent: str = "omscs-course-intel/0.1"
    reddit_request_timeout_seconds: float = 30.0
    reddit_user_agent: str = "omscs-course-intel/0.1 (by /u/omscs-course-intel)"
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    llm_provider: str = "openai"
    openai_api_key: str = "replace-me"
    openai_embedding_model: str = "text-embedding-3-small"
    openai_chat_model: str = "gpt-4.1-mini"
    anthropic_api_key: str = "replace-me"
    anthropic_chat_model: str = "claude-3-5-sonnet-latest"
    anthropic_api_version: str = "2023-06-01"
    embedding_dimensions: int = 1536
    api_gateway_url: str = "http://api-gateway:8000"
    ingestion_service_url: str = "http://ingestion-service:8001"
    embedding_service_url: str = "http://embedding-service:8002"
    processing_service_url: str = "http://processing-service:8005"
    retrieval_service_url: str = "http://retrieval-service:8003"
    llm_service_url: str = "http://llm-service:8004"
    redis_cache_ttl_seconds: int = 300
    rate_limit_enabled: bool = True
    query_rate_limit_per_minute: int = 10
    query_rate_limit_per_day: int = 100
    admin_api_key: str = "replace-me"
    api_gateway_port: int = 8000
    ingestion_service_port: int = 8001
    embedding_service_port: int = 8002
    retrieval_service_port: int = 8003
    llm_service_port: int = 8004
    processing_service_port: int = 8005
    frontend_cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    frontend_cors_origin_regex: str = r"http://(localhost|127\.0\.0\.1):[0-9]+"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
