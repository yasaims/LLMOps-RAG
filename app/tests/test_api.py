from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.rag.generate import GenerateResult, Source
from app.vectorstore import SearchResult


@pytest.fixture
def client():
    fake_store = MagicMock()
    with patch("app.api.main.get_store", return_value=fake_store):
        with TestClient(app) as c:
            yield c, fake_store


def test_healthz_ok(client):
    c, fake_store = client
    fake_store.ping.return_value = True
    resp = c.get("/healthz")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/json; charset=utf-8"
    body = resp.json()
    assert body["status"] == "ok"
    assert body["vector_store"]


def test_healthz_db_down(client):
    c, fake_store = client
    fake_store.ping.return_value = False
    resp = c.get("/healthz")

    assert resp.status_code == 200
    assert resp.json()["status"] == "error"


def test_query_returns_answer_with_numbered_sources(client):
    c, _ = client
    fake_chunks = [
        SearchResult(
            id=1,
            section="A > B",
            content="content 1",
            page_start=10,
            service="bedrock",
            doc="bedrock-ug",
            source_url="https://example.com/x.pdf",
            score=0.9,
        ),
        SearchResult(
            id=2,
            section="C",
            content="content 2",
            page_start=20,
            service="bedrock",
            doc="bedrock-ug",
            source_url="https://example.com/y.pdf",
            score=0.8,
        ),
    ]
    fake_result = GenerateResult(
        answer="回答本文 [1][2]",
        sources=[
            Source(1, "bedrock", "bedrock-ug", "A > B", 10, "https://example.com/x.pdf", 0.9),
            Source(2, "bedrock", "bedrock-ug", "C", 20, "https://example.com/y.pdf", 0.8),
        ],
        usage={"inputTokens": 10, "outputTokens": 5},
    )
    with (
        patch("app.api.main.retrieve", return_value=fake_chunks) as mock_retrieve,
        patch("app.api.main.generate_answer", return_value=fake_result) as mock_generate,
    ):
        resp = c.post("/query", json={"question": "質問文"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] == "回答本文 [1][2]"
    assert [s["index"] for s in body["sources"]] == [1, 2]
    assert body["usage"]["inputTokens"] == 10
    assert body["latency_ms"] >= 0
    mock_retrieve.assert_called_once()
    mock_generate.assert_called_once()


def test_query_with_no_matching_chunks_returns_no_sources(client):
    c, _ = client
    empty_result = GenerateResult(
        answer="提供されたドキュメントには記載がありません。", sources=[], usage={}
    )
    with (
        patch("app.api.main.retrieve", return_value=[]),
        patch("app.api.main.generate_answer", return_value=empty_result),
    ):
        resp = c.post("/query", json={"question": "無関係の質問"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["sources"] == []
    assert "記載がありません" in body["answer"]


def test_query_rejects_empty_question(client):
    c, _ = client
    resp = c.post("/query", json={"question": ""})
    assert resp.status_code == 422
