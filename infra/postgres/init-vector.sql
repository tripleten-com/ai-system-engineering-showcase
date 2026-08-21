-- TripleTen Cloud Platform — Autonomous Incident Defense
-- Database Initialization: Vector Extension & Knowledge Base
CREATE EXTENSION IF NOT EXISTS vector;

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

CREATE INDEX IF NOT EXISTS idx_knowledge_runbooks_embedding 
ON knowledge_runbooks USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS idx_knowledge_runbooks_content_fts 
ON knowledge_runbooks USING gin (content_fts);

-- LangGraph checkpointer tables are deliberately NOT created here.
--
-- They used to be, as hand-written DDL approximating the library's layout closely enough that
-- /healthz passed. That approximation was already missing `checkpoint_migrations`, the table
-- LangGraph uses to version the rest, so the schema would have diverged silently on the first
-- dependency bump. `AsyncPostgresSaver.setup()` runs during API startup and owns them — see
-- services/incident-agent-api/src/incident_agent_api/agent/checkpointer.py.
