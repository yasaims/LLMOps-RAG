"""Bedrock 呼び出しの単一窓口。Phase 2 で Lambda にそのまま載せる想定。"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, Literal

import boto3
from botocore.exceptions import ClientError
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.config import get_settings


@lru_cache
def _client():
    settings = get_settings()
    return boto3.client("bedrock-runtime", region_name=settings.aws_region)


def _is_throttling(exc: BaseException) -> bool:
    if not isinstance(exc, ClientError):
        return False
    code = exc.response.get("Error", {}).get("Code")
    return code in {"ThrottlingException", "TooManyRequestsException"}


_retry_throttled = retry(
    retry=retry_if_exception(_is_throttling),
    wait=wait_exponential(multiplier=1, min=1, max=30),
    stop=stop_after_attempt(6),
    reraise=True,
)


@_retry_throttled
def _embed(
    texts: list[str], input_type: Literal["search_document", "search_query"]
) -> list[list[float]]:
    settings = get_settings()
    body = {
        "texts": texts,
        "input_type": input_type,
        "embedding_types": ["float"],
        "output_dimension": settings.bedrock_embed_dim,
    }
    resp = _client().invoke_model(
        modelId=settings.bedrock_embed_model_id,
        body=json.dumps(body),
        contentType="application/json",
        accept="application/json",
    )
    payload = json.loads(resp["body"].read())
    return payload["embeddings"]["float"]


def embed_documents(texts: list[str]) -> list[list[float]]:
    """チャンク (取り込み対象文書) を埋め込む。input_type=search_document。"""
    return _embed(texts, "search_document")


def embed_query(text: str) -> list[float]:
    """検索クエリを埋め込む。input_type=search_query (search_document とは別枠)。"""
    return _embed([text], "search_query")[0]


@_retry_throttled
def converse(
    system: str,
    messages: list[dict[str, Any]],
    max_tokens: int | None = None,
    model_id: str | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    resp = _client().converse(
        modelId=model_id or settings.bedrock_chat_model_id,
        system=[{"text": system}],
        messages=messages,
        inferenceConfig={"maxTokens": max_tokens or settings.bedrock_max_tokens},
    )
    output_message = resp["output"]["message"]
    text = "".join(block.get("text", "") for block in output_message["content"])
    return {"text": text, "usage": resp["usage"]}
