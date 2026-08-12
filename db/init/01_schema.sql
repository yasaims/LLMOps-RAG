CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE documents (
    id           bigserial PRIMARY KEY,
    service      text NOT NULL,          -- 'bedrock'
    doc          text NOT NULL,          -- 'bedrock-ug'
    source_url   text NOT NULL,
    content_hash text NOT NULL,          -- PDF 全体のハッシュ (再取り込み判定)
    ingested_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (service, doc)
);

CREATE TABLE chunks (
    id           bigserial PRIMARY KEY,
    document_id  bigint NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    section      text,                   -- 見出しパス "Getting started > Prerequisites"
    page_start   int,
    page_end     int,
    content      text NOT NULL,
    content_hash text NOT NULL,
    embedding    vector(1536) NOT NULL,  -- cohere.embed-v4:0
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (document_id, content_hash)   -- 冪等な再取り込み
);

CREATE INDEX chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX chunks_document_id_idx ON chunks (document_id);
