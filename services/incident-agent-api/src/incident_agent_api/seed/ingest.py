"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/src/incident_agent_api/seed/ingest.py
Component:          Knowledge Base Ingestion & Vector Embedding
Purpose:            Creates the pgvector schema and idempotently upserts the runbook corpus.
Interacts With:     postgres-vector (:5432)

Curriculum Project:  Project 2 — Hybrid RAG & Retrieval Architecture
Skills:             Vector Search, HNSW Cosine Indexing, FTS Ingestion, Deterministic Embeddings
Tools:              PostgreSQL 16, pgvector, SQLAlchemy, Python 3.11
"""

import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from incident_agent_api.retrieval.embeddings import EMBEDDING_VERSION, embed, to_pgvector_literal
from incident_agent_api.seed.runbooks import SEED_RUNBOOKS


async def seed_knowledge_base(database_url: str) -> int:
    """Seeds the PostgreSQL database with the 4 canonical emergency runbooks and vector embeddings."""
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS knowledge_runbooks (
                    id VARCHAR(32) PRIMARY KEY,
                    title VARCHAR(255) NOT NULL,
                    version VARCHAR(32) NOT NULL,
                    category VARCHAR(64) NOT NULL,
                    tags TEXT[] NOT NULL DEFAULT '{}',
                    author VARCHAR(255) NOT NULL,
                    summary TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                    embedding vector(384),
                    content_fts tsvector GENERATED ALWAYS AS (
                        to_tsvector('english', coalesce(title, '') || ' ' || coalesce(summary, '') || ' ' || coalesce(content, ''))
                    ) STORED
                );
            """))
            await conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_knowledge_runbooks_embedding 
                ON knowledge_runbooks USING hnsw (embedding vector_cosine_ops);
            """))
            await conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_knowledge_runbooks_content_fts 
                ON knowledge_runbooks USING gin (content_fts);
            """))

            # No checkpointer DDL here. AsyncPostgresSaver.setup() creates its own tables
            # during startup, including the checkpoint_migrations table this file used to
            # omit while claiming to provide the schema. See agent/checkpointer.py.

            # Current means "seeded AND embedded by the version of the embedding function this
            # build ships". Counting rows alone is not enough: changing the embedding scheme
            # leaves four perfectly good-looking rows holding vectors from the previous one,
            # while queries are embedded with the new one. Cosine distance across two schemes is
            # meaningless, and the failure is silent — retrieval still returns a confident
            # ordering, just the wrong one.
            current_res = await conn.execute(
                text(
                    """
                    SELECT count(*) FROM knowledge_runbooks
                    WHERE embedding IS NOT NULL
                      AND metadata_json ->> 'embedding_version' = :version
                    """
                ),
                {"version": EMBEDDING_VERSION},
            )
            count = current_res.scalar() or 0

            if count < len(SEED_RUNBOOKS):
                for doc in SEED_RUNBOOKS:
                    full_text = f"{doc['title']} {doc['summary']} {doc['content']}"
                    # The same function the retriever embeds queries with. Two different
                    # embedding functions would make every cosine distance meaningless while
                    # still returning a plausible-looking ordering.
                    embedding = embed(full_text)
                    await conn.execute(
                        text("""
                            INSERT INTO knowledge_runbooks (id, title, version, category, tags, author, summary, content, metadata_json, embedding)
                            VALUES (:id, :title, :version, :category, :tags, :author, :summary, :content, :metadata_json, (:embedding)::text::vector)
                            ON CONFLICT (id) DO UPDATE SET
                                title = EXCLUDED.title,
                                version = EXCLUDED.version,
                                category = EXCLUDED.category,
                                tags = EXCLUDED.tags,
                                author = EXCLUDED.author,
                                summary = EXCLUDED.summary,
                                content = EXCLUDED.content,
                                metadata_json = EXCLUDED.metadata_json,
                                embedding = (:embedding)::text::vector;
                        """),
                        {
                            "id": doc["id"],
                            "title": doc["title"],
                            "version": doc["version"],
                            "category": doc["category"],
                            "tags": doc["tags"],
                            "author": doc["author"],
                            "summary": doc["summary"],
                            "content": doc["content"],
                            "metadata_json": json.dumps(
                                {
                                    "scenario_target": doc["category"],
                                    "embedding_version": EMBEDDING_VERSION,
                                }
                            ),
                            "embedding": to_pgvector_literal(embedding),
                        }
                    )
                current_res = await conn.execute(
                    text(
                        """
                        SELECT count(*) FROM knowledge_runbooks
                        WHERE embedding IS NOT NULL
                          AND metadata_json ->> 'embedding_version' = :version
                        """
                    ),
                    {"version": EMBEDDING_VERSION},
                )
                count = current_res.scalar() or 0

        return count
    finally:
        await engine.dispose()
