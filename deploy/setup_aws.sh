#!/usr/bin/env bash
# One-time AWS setup for CareCircle: DynamoDB table, SNS family-alert topic, Bedrock model check, .env file.
# Usage (from the repo root, e.g. in AWS CloudShell):  bash deploy/setup_aws.sh you@example.com
set -euo pipefail
EMAIL="${1:?usage: setup_aws.sh <family-alert-email>}"
REGION="${AWS_REGION:-us-east-1}"
TABLE=carecircle
export AWS_REGION=$REGION AWS_PAGER=""

echo "== DynamoDB table '$TABLE'"
if ! aws dynamodb describe-table --table-name $TABLE >/dev/null 2>&1; then
  aws dynamodb create-table --table-name $TABLE \
    --attribute-definitions AttributeName=pk,AttributeType=S AttributeName=sk,AttributeType=S \
    --key-schema AttributeName=pk,KeyType=HASH AttributeName=sk,KeyType=RANGE \
    --billing-mode PAY_PER_REQUEST >/dev/null
  aws dynamodb wait table-exists --table-name $TABLE
fi
echo "   ok"

echo "== SNS topic for family alerts"
TOPIC=$(aws sns create-topic --name carecircle-family-alerts --query TopicArn --output text)
if ! aws sns list-subscriptions-by-topic --topic-arn "$TOPIC" --query "Subscriptions[].Endpoint" --output text | grep -q "$EMAIL"; then
  aws sns subscribe --topic-arn "$TOPIC" --protocol email --notification-endpoint "$EMAIL" >/dev/null
  echo "   Confirmation email sent to $EMAIL - click the link in it!"
fi
echo "   $TOPIC"

echo "== Bedrock models"
python3 deploy/check_bedrock.py > /tmp/cc_models.env
cat /tmp/cc_models.env

cat > .env <<EOF
CARECIRCLE_LLM=bedrock
AWS_REGION=$REGION
CARECIRCLE_STORAGE=dynamodb
CARECIRCLE_DYNAMO_TABLE=$TABLE
CARECIRCLE_SNS_TOPIC_ARN=$TOPIC
CARECIRCLE_TZ=Asia/Kolkata
$(cat /tmp/cc_models.env)
EOF
echo "== wrote .env"; cat .env
