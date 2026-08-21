#!/bin/bash
set -euo pipefail

echo "================================================================="
echo "TripleTen Cloud Platform — Autonomous Incident Defense Smoke Test"
echo "================================================================="

FAILED=0

check_endpoint() {
    local name="$1"
    local url="$2"
    echo -n "Checking $name ($url)... "
    if curl -s -f -o /dev/null "$url"; then
        echo "OK"
    else
        echo "FAILED"
        FAILED=1
    fi
}

# 1. Container HTTP Health Endpoints
check_endpoint "incident-war-room" "http://localhost:3000"
check_endpoint "incident-agent-api healthz" "http://localhost:8000/healthz"
check_endpoint "grafana" "http://localhost:3001/api/health"
check_endpoint "prometheus" "http://localhost:9090/-/healthy"
check_endpoint "jaeger" "http://localhost:16686/"
check_endpoint "localstack" "http://localhost:4566/_localstack/health"

# 2. Redis check
echo -n "Checking redis ping... "
if docker exec redis redis-cli ping 2>/dev/null | grep -q "PONG"; then
    echo "OK"
else
    echo "FAILED"
    FAILED=1
fi

# 3. PostgreSQL Schema, Indexes & Seed Runbooks Check
echo -n "Checking postgres-vector connection, HNSW/GIN indexes & seed runbooks... "
PG_CHECK=$(docker exec postgres-vector psql -U postgres -d tripleten_db -t -A -F' ' -c "
    SELECT 
        (SELECT count(*) FROM knowledge_runbooks WHERE id IN ('RB-104', 'RB-208', 'RB-312', 'SEC-501') AND embedding IS NOT NULL),
        (SELECT count(*) FROM pg_indexes WHERE tablename = 'knowledge_runbooks' AND indexname = 'idx_knowledge_runbooks_embedding'),
        (SELECT count(*) FROM pg_indexes WHERE tablename = 'knowledge_runbooks' AND indexname = 'idx_knowledge_runbooks_content_fts'),
        (SELECT count(*) FROM information_schema.tables WHERE table_name = 'checkpoints');
" 2>/dev/null || echo "0 0 0 0")

RB_COUNT=$(echo "$PG_CHECK" | awk '{print $1}')
HNSW_COUNT=$(echo "$PG_CHECK" | awk '{print $2}')
GIN_COUNT=$(echo "$PG_CHECK" | awk '{print $3}')
CHK_COUNT=$(echo "$PG_CHECK" | awk '{print $4}')

if [ "${RB_COUNT:-0}" -ge 4 ] && [ "${HNSW_COUNT:-0}" -ge 1 ] && [ "${GIN_COUNT:-0}" -ge 1 ] && [ "${CHK_COUNT:-0}" -ge 1 ]; then
    echo "OK (4 runbooks, HNSW + GIN indexes, checkpointer verified)"
else
    echo "FAILED (runbooks: $RB_COUNT, hnsw: $HNSW_COUNT, gin: $GIN_COUNT, checkpointer: $CHK_COUNT)"
    FAILED=1
fi

# 4. LocalStack SQS Queues, Redrive Policies & S3 Bucket Check
#
# Values are pulled with --query rather than grepped out of formatted JSON. The CLI
# renders RedrivePolicy as an escaped JSON *string*, and matching that text has already
# produced two false failures against a correctly provisioned stack. --query returns the
# unescaped value, so the comparison does not depend on quoting or whitespace at all.
echo -n "Checking LocalStack SQS queues, DLQ redrive policies & S3 bucket... "
SQS_REASONS=""

QUEUES=$(docker exec localstack awslocal sqs list-queues --output text 2>/dev/null || echo "")
BUCKETS=$(docker exec localstack awslocal s3 ls 2>/dev/null || echo "")

for q in customer-jobs customer-dlq remediation-jobs remediation-dlq; do
    case "$QUEUES" in
        *"$q"*) ;;
        *) SQS_REASONS="$SQS_REASONS queue-missing:$q" ;;
    esac
done
case "$BUCKETS" in
    *tripleten-cloud-postmortems*) ;;
    *) SQS_REASONS="$SQS_REASONS bucket-missing:tripleten-cloud-postmortems" ;;
esac

check_source_queue() {
    # $1 = source queue name, $2 = expected DLQ name
    _url=$(docker exec localstack awslocal sqs get-queue-url --queue-name "$1" --output text 2>/dev/null | tr -d '\r' || echo "")
    if [ -z "$_url" ]; then
        SQS_REASONS="$SQS_REASONS url-unresolved:$1"
        return
    fi
    _vis=$(docker exec localstack awslocal sqs get-queue-attributes --queue-url "$_url" \
        --attribute-names VisibilityTimeout --query 'Attributes.VisibilityTimeout' \
        --output text 2>/dev/null | tr -d '\r' || echo "")
    _redrive=$(docker exec localstack awslocal sqs get-queue-attributes --queue-url "$_url" \
        --attribute-names RedrivePolicy --query 'Attributes.RedrivePolicy' \
        --output text 2>/dev/null | tr -d '\r' || echo "")

    [ "$_vis" = "30" ] || SQS_REASONS="$SQS_REASONS $1:VisibilityTimeout=[$_vis]want30"
    case "$_redrive" in
        *"$2"*) ;;
        *) SQS_REASONS="$SQS_REASONS $1:RedrivePolicy=[$_redrive]want$2" ;;
    esac
    case "$_redrive" in
        *'"maxReceiveCount":3'*|*'"maxReceiveCount": 3'*) ;;
        *) SQS_REASONS="$SQS_REASONS $1:maxReceiveCount=[$_redrive]want3" ;;
    esac
}

check_source_queue customer-jobs customer-dlq
check_source_queue remediation-jobs remediation-dlq

if [ -z "$SQS_REASONS" ]; then
    echo "OK (queues, DLQs, 30s visibility & maxReceiveCount=3 verified)"
else
    # Print what was actually observed. A bare "mismatch" costs a whole CI cycle to diagnose.
    echo "FAILED —$SQS_REASONS"
    FAILED=1
fi

# 5. Worker heartbeat check in Redis
echo -n "Checking remediation-worker heartbeat... "
HB=$(docker exec redis redis-cli get "worker:heartbeat" 2>/dev/null || echo "")
if [ -n "$HB" ] && echo "$HB" | grep -q "healthy"; then
    echo "OK"
else
    echo "FAILED"
    FAILED=1
fi

# 6. Prometheus scrape target & pre-provisioned Grafana dashboards
#
# Grafana answering /api/health above proves only that the process is alive. It would answer
# identically with zero dashboards and an unresolvable datasource, which is exactly what a
# broken provisioning change looks like. The dashboard roster is derived from Grafana rather
# than listed here, and the scrape target is checked for health=up, not merely for existence.
echo -n "Checking prometheus scrape target & provisioned Grafana dashboards... "
OBS_REASONS=""

TARGETS=$(curl -s "http://localhost:9090/api/v1/targets?state=active" 2>/dev/null || echo "")
case "$TARGETS" in
    *'"health":"up"'*) ;;
    *) OBS_REASONS="$OBS_REASONS scrape-target-not-up" ;;
esac
case "$TARGETS" in
    *'incident-agent-api:8000/metrics'*) ;;
    *) OBS_REASONS="$OBS_REASONS scrape-target-missing:incident-agent-api" ;;
esac

# The roster is derived, never listed. Grafana is asked which tripleten dashboards it loaded
# and the count is compared against the committed JSON files, so a sixth dashboard is checked
# the moment it lands. A hardcoded list would keep passing while silently covering five.
DASH_DIR="$(dirname "$0")/../infra/grafana/provisioning/dashboards"
EXPECTED=$(find "$DASH_DIR" -maxdepth 1 -name '*.json' 2>/dev/null | wc -l | tr -d '[:space:]')
SEARCH=$(curl -s -f -u admin:admin "http://localhost:3001/api/search?tag=tripleten&type=dash-db" 2>/dev/null || echo "")
FOUND_UIDS=$(echo "$SEARCH" | grep -oE '"uid":"[^"]+"' | cut -d'"' -f4 | sort -u)
FOUND=$(printf '%s' "$FOUND_UIDS" | grep -c . || true)

if [ "${EXPECTED:-0}" -eq 0 ]; then
    OBS_REASONS="$OBS_REASONS no-dashboard-json-found:$DASH_DIR"
elif [ "$FOUND" != "$EXPECTED" ]; then
    OBS_REASONS="$OBS_REASONS dashboard-count=[$FOUND]want$EXPECTED"
fi

for uid in $FOUND_UIDS; do
    DASH=$(curl -s -f -u admin:admin "http://localhost:3001/api/dashboards/uid/$uid" 2>/dev/null || echo "")
    case "$DASH" in
        *'"provisioned":true'*) ;;
        *) OBS_REASONS="$OBS_REASONS dashboard-not-provisioned:$uid" ;;
    esac
done

DATASOURCES="tripleten-prometheus tripleten-jaeger"
DS_COUNT=0
for ds in $DATASOURCES; do
    DS_COUNT=$((DS_COUNT + 1))
    if ! curl -s -f -u admin:admin -o /dev/null "http://localhost:3001/api/datasources/uid/$ds" 2>/dev/null; then
        OBS_REASONS="$OBS_REASONS datasource-missing:$ds"
    fi
done

if [ -z "$OBS_REASONS" ]; then
    echo "OK (target up, $FOUND dashboards provisioned, $DS_COUNT datasources resolved)"
else
    echo "FAILED —$OBS_REASONS"
    FAILED=1
fi

echo "================================================================="
if [ "$FAILED" -eq 0 ]; then
    echo "ALL SMOKE CHECKS PASSED (< 15s)"
    exit 0
else
    echo "SMOKE CHECKS FAILED"
    exit 1
fi
