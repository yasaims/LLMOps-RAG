from unittest.mock import MagicMock, patch

from app.vectorstore.base import ChunkRecord
from app.vectorstore.s3vectors_store import PUT_BATCH_SIZE, S3VectorsStore


def _chunk(content_hash: str, page_start: int | None = 10) -> ChunkRecord:
    return ChunkRecord(
        service="bedrock",
        doc="bedrock-ug",
        source_url="https://example.com/bedrock-ug.pdf",
        section="Overview",
        page_start=page_start,
        page_end=page_start,
        content="本文",
        content_hash=content_hash,
        embedding=[0.1, 0.2],
    )


def _store() -> tuple[S3VectorsStore, MagicMock]:
    with patch("app.vectorstore.s3vectors_store.boto3.client") as mock_client_factory:
        mock_client = MagicMock()
        mock_client_factory.return_value = mock_client
        store = S3VectorsStore()
    return store, mock_client


def test_upsert_chunks_splits_into_batches_and_marks_content_non_filterable():
    store, mock_client = _store()
    chunks = [_chunk(f"hash{i}") for i in range(PUT_BATCH_SIZE + 1)]

    inserted = store.upsert_chunks(chunks)

    assert inserted == len(chunks)
    assert mock_client.put_vectors.call_count == 2
    first_call_vectors = mock_client.put_vectors.call_args_list[0].kwargs["vectors"]
    assert len(first_call_vectors) == PUT_BATCH_SIZE
    vector = first_call_vectors[0]
    assert vector["key"] == "hash0"
    assert vector["data"] == {"float32": [0.1, 0.2]}
    assert vector["metadata"]["content"] == "本文"
    assert vector["metadata"]["service"] == "bedrock"


def test_upsert_chunks_uses_sentinel_for_missing_page():
    store, mock_client = _store()
    store.upsert_chunks([_chunk("hash0", page_start=None)])

    vector = mock_client.put_vectors.call_args.kwargs["vectors"][0]
    assert vector["metadata"]["page_start"] == -1


def test_search_converts_distance_to_score_and_restores_none_page():
    store, mock_client = _store()
    mock_client.query_vectors.return_value = {
        "vectors": [
            {
                "key": "hash0",
                "distance": 0.2,
                "metadata": {
                    "service": "bedrock",
                    "doc": "bedrock-ug",
                    "source_url": "https://example.com/x.pdf",
                    "section": "Overview",
                    "page_start": -1,
                    "content": "抜粋テキスト",
                },
            }
        ]
    }

    results = store.search([0.1, 0.2], top_k=5)

    assert len(results) == 1
    r = results[0]
    assert r.score == 0.8
    assert r.page_start is None
    assert r.content == "抜粋テキスト"
    mock_client.query_vectors.assert_called_once()
    assert mock_client.query_vectors.call_args.kwargs["topK"] == 5


def test_search_passes_metadata_filter_when_given():
    store, mock_client = _store()
    mock_client.query_vectors.return_value = {"vectors": []}

    store.search([0.1], top_k=3, metadata_filter={"service": {"$eq": "bedrock"}})

    assert mock_client.query_vectors.call_args.kwargs["filter"] == {"service": {"$eq": "bedrock"}}


def test_existing_hashes_paginates_and_filters_by_service_and_doc():
    store, mock_client = _store()
    mock_client.list_vectors.side_effect = [
        {
            "vectors": [
                {"key": "match1", "metadata": {"service": "bedrock", "doc": "bedrock-ug"}},
                {"key": "other-doc", "metadata": {"service": "bedrock", "doc": "other"}},
            ],
            "nextToken": "page2",
        },
        {
            "vectors": [
                {"key": "match2", "metadata": {"service": "bedrock", "doc": "bedrock-ug"}},
            ]
        },
    ]

    hashes = store.existing_hashes("bedrock", "bedrock-ug")

    assert hashes == {"match1", "match2"}
    assert mock_client.list_vectors.call_count == 2
    assert mock_client.list_vectors.call_args_list[1].kwargs["nextToken"] == "page2"
