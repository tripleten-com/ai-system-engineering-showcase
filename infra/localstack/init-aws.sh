#!/bin/bash
set -euo pipefail

echo "=== Initializing LocalStack AWS Resources ==="

# 1. Pre-create S3 bucket
echo "Creating S3 bucket: tripleten-cloud-postmortems..."
awslocal s3 mb s3://tripleten-cloud-postmortems || true

# 2. Create Dead-Letter Queues (DLQs) FIRST
echo "Creating customer-dlq..."
awslocal sqs create-queue --queue-name customer-dlq

echo "Creating remediation-dlq..."
awslocal sqs create-queue --queue-name remediation-dlq

# 3. Retrieve DLQ URLs and ARNs
CUSTOMER_DLQ_URL=$(awslocal sqs get-queue-url --queue-name customer-dlq --query "QueueUrl" --output text)
CUSTOMER_DLQ_ARN=$(awslocal sqs get-queue-attributes --queue-url "${CUSTOMER_DLQ_URL}" --attribute-names QueueArn --query "Attributes.QueueArn" --output text)

REMEDIATION_DLQ_URL=$(awslocal sqs get-queue-url --queue-name remediation-dlq --query "QueueUrl" --output text)
REMEDIATION_DLQ_ARN=$(awslocal sqs get-queue-attributes --queue-url "${REMEDIATION_DLQ_URL}" --attribute-names QueueArn --query "Attributes.QueueArn" --output text)

echo "Customer DLQ ARN: ${CUSTOMER_DLQ_ARN}"
echo "Remediation DLQ ARN: ${REMEDIATION_DLQ_ARN}"

# 4. Create Source Queues with Redrive Policy (maxReceiveCount=3) and VisibilityTimeout=30
echo "Creating customer-jobs with VisibilityTimeout=30 and RedrivePolicy..."
awslocal sqs create-queue \
  --queue-name customer-jobs \
  --attributes "{\"VisibilityTimeout\": \"30\", \"RedrivePolicy\": \"{\\\"deadLetterTargetArn\\\":\\\"${CUSTOMER_DLQ_ARN}\\\",\\\"maxReceiveCount\\\":3}\"}"

echo "Creating remediation-jobs with VisibilityTimeout=30 and RedrivePolicy..."
awslocal sqs create-queue \
  --queue-name remediation-jobs \
  --attributes "{\"VisibilityTimeout\": \"30\", \"RedrivePolicy\": \"{\\\"deadLetterTargetArn\\\":\\\"${REMEDIATION_DLQ_ARN}\\\",\\\"maxReceiveCount\\\":3}\"}"

echo "=== LocalStack AWS Initialization Complete ==="
