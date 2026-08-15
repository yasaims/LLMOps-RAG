from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    aws_region: str = "ap-northeast-1"
    bedrock_embed_model_id: str = "cohere.embed-v4:0"
    bedrock_embed_dim: int = 1536
    bedrock_chat_model_id: str = "jp.anthropic.claude-haiku-4-5-20251001-v1:0"
    bedrock_max_tokens: int = 1024
    database_url: str = "postgresql://rag:rag@localhost:5432/rag"
    rag_top_k: int = 5

    # ベクトルストア切り替え (Phase 1: pgvector ローカル/CI, Phase 2: s3vectors AWS)
    vector_store: str = "pgvector"
    s3_vectors_bucket: str = ""
    s3_vectors_index: str = "chunks"

    # Phase 2: 取り込み元 PDF の保管先 (出典・再現性の証跡)
    docs_bucket: str = ""

    log_level: str = "INFO"
    # カンマ区切りのオリジン一覧。空なら CORS ミドルウェアは有効化しない (Phase 4 のフロント用)
    cors_allow_origins: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
