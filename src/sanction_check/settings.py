from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    postgres_user: str
    postgres_password: SecretStr
    postgres_db: str
    openrouter_api_key: SecretStr
    load_sources: list[str]
    web_search_allowed_domains: list[str]
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    postgres_host: str = "127.0.0.1"
    top_k: int = 10
    llm_model: str = "deepseek/deepseek-v4.1-flash-1017"
    hit_threshold: float = 0.99
    embedding_model: str = "qwen/qwen3-embedding-8b"
    vector_review_threshold: float = 0.80
    tavily_api_key: SecretStr | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
