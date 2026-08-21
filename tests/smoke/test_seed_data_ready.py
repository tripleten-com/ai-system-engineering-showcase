"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             tests/smoke/test_seed_data_ready.py
Component:          Knowledge Base & Cloud Resource Smoke Test
Purpose:            Validates that pgvector runbooks are seeded and LocalStack resources exist.
Interacts With:     postgres-vector (:5432), localstack (:4566)

Curriculum Project:  Project 2 — Hybrid RAG & Retrieval Architecture
Skills:             Vector Search, HNSW Cosine Indexing, FTS Ingestion, LocalStack SQS/S3
Tools:              Pytest, SQLAlchemy, Boto3, Python 3.11
"""

# Hard imports on purpose. These ship in the workspace dev group, so a missing
# one is a broken environment, not a reason to silently pass an empty run.
import boto3
import pytest
import sqlalchemy as sa

create_engine = sa.create_engine
text = sa.text


@pytest.mark.smoke
def test_postgres_pgvector_seed_and_schema():
    """Validates that PostgreSQL has pgvector enabled, HNSW/GIN indexes, 4 runbooks, and checkpointer."""
    engine = create_engine("postgresql://postgres:postgres@localhost:5432/tripleten_db")
    with engine.connect() as conn:
        # 1. Check extension
        ext_res = conn.execute(text("SELECT extname FROM pg_extension WHERE extname = 'vector';")).scalar()
        assert ext_res == "vector", "pgvector extension not enabled in PostgreSQL"

        # 2. Check runbook IDs and embeddings
        expected_ids = {"RB-104", "RB-208", "RB-312", "SEC-501"}
        rows = conn.execute(
            text("SELECT id, embedding IS NOT NULL as has_embedding FROM knowledge_runbooks;")
        ).fetchall()
        found_ids = {row[0] for row in rows}
        assert expected_ids.issubset(found_ids), f"Missing runbooks. Expected: {expected_ids}, found: {found_ids}"
        for row in rows:
            if row[0] in expected_ids:
                assert row[1] is True, f"Runbook {row[0]} missing vector embedding"

        # 3. Check HNSW and GIN indexes
        idx_res = conn.execute(text("""
            SELECT indexname FROM pg_indexes 
            WHERE tablename = 'knowledge_runbooks';
        """)).fetchall()
        indexes = {row[0] for row in idx_res}
        assert "idx_knowledge_runbooks_embedding" in indexes, "HNSW embedding index missing"
        assert "idx_knowledge_runbooks_content_fts" in indexes, "GIN content_fts index missing"

        # 4. Check checkpointer table
        chk_res = conn.execute(text("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = 'checkpoints'
            );
        """)).scalar()
        assert chk_res is True, "LangGraph checkpoints table missing"

    engine.dispose()


@pytest.mark.smoke
def test_localstack_queues_and_buckets():
    """Validates presence of required SQS queues, redrive policies, visibility timeout, and S3 bucket."""
    sqs = boto3.client(
        "sqs",
        endpoint_url="http://localhost:4566",
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    s3 = boto3.client(
        "s3",
        endpoint_url="http://localhost:4566",
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )

    queues = sqs.list_queues().get("QueueUrls", [])
    queue_map = {q.split("/")[-1]: q for q in queues}

    for req_q in ["customer-jobs", "customer-dlq", "remediation-jobs", "remediation-dlq"]:
        assert req_q in queue_map, f"Queue {req_q} missing in LocalStack"

    # Validate source queues RedrivePolicy and VisibilityTimeout (30s)
    for src_q, dlq_name in [("customer-jobs", "customer-dlq"), ("remediation-jobs", "remediation-dlq")]:
        attrs = sqs.get_queue_attributes(
            QueueUrl=queue_map[src_q],
            AttributeNames=["VisibilityTimeout", "RedrivePolicy"],
        ).get("Attributes", {})

        assert attrs.get("VisibilityTimeout") == "30", f"VisibilityTimeout on {src_q} must be 30s"
        assert "RedrivePolicy" in attrs, f"RedrivePolicy missing on {src_q}"
        assert dlq_name in attrs["RedrivePolicy"], f"RedrivePolicy on {src_q} does not point to {dlq_name}"
        assert '"maxReceiveCount":3' in attrs["RedrivePolicy"] or '"maxReceiveCount": 3' in attrs["RedrivePolicy"]

    buckets = [b["Name"] for b in s3.list_buckets().get("Buckets", [])]
    assert "tripleten-cloud-postmortems" in buckets, "Bucket tripleten-cloud-postmortems missing in LocalStack"
