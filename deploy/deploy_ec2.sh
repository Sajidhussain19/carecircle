#!/usr/bin/env bash
# Deploy CareCircle to a small EC2 instance behind CloudFront (HTTPS) - all from AWS CloudShell.
#   S3 (code bundle) -> EC2 t3.small (Amazon Linux 2023, systemd service, IAM role for Bedrock/DynamoDB/SNS)
#   -> CloudFront (HTTPS, caching disabled, streaming passthrough)
# Usage (repo root, after setup_aws.sh wrote .env):  bash deploy/deploy_ec2.sh
# Re-running updates the code on the existing instance.
set -euo pipefail
REGION="${AWS_REGION:-us-east-1}"; export AWS_REGION=$REGION AWS_PAGER=""
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
BUCKET="carecircle-deploy-$ACCOUNT"
NAME=carecircle
[ -f .env ] || { echo "Run deploy/setup_aws.sh first"; exit 1; }

echo "== Upload code bundle to s3://$BUCKET"
aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null || aws s3 mb "s3://$BUCKET" >/dev/null
rm -f /tmp/cc_bundle.tgz
tar czf /tmp/cc_bundle.tgz --exclude=.venv --exclude='*.db' --exclude=__pycache__ --exclude=.git \
  carecircle web requirements.txt .env
aws s3 cp /tmp/cc_bundle.tgz "s3://$BUCKET/bundle.tgz" >/dev/null

echo "== IAM role + instance profile"
if ! aws iam get-role --role-name $NAME-ec2 >/dev/null 2>&1; then
  aws iam create-role --role-name $NAME-ec2 --assume-role-policy-document \
    '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
  aws iam put-role-policy --role-name $NAME-ec2 --policy-name $NAME-app --policy-document "{
    \"Version\":\"2012-10-17\",\"Statement\":[
     {\"Effect\":\"Allow\",\"Action\":[\"bedrock:InvokeModel\",\"bedrock:InvokeModelWithResponseStream\",\"bedrock:Converse\",\"bedrock:ConverseStream\"],\"Resource\":\"*\"},
     {\"Effect\":\"Allow\",\"Action\":[\"dynamodb:GetItem\",\"dynamodb:PutItem\",\"dynamodb:DeleteItem\",\"dynamodb:Query\",\"dynamodb:UpdateItem\"],\"Resource\":\"arn:aws:dynamodb:$REGION:$ACCOUNT:table/carecircle\"},
     {\"Effect\":\"Allow\",\"Action\":\"sns:Publish\",\"Resource\":\"arn:aws:sns:$REGION:$ACCOUNT:carecircle-family-alerts\"},
     {\"Effect\":\"Allow\",\"Action\":\"s3:GetObject\",\"Resource\":\"arn:aws:s3:::$BUCKET/*\"},
     {\"Effect\":\"Allow\",\"Action\":[\"aws-marketplace:ViewSubscriptions\",\"aws-marketplace:Subscribe\"],\"Resource\":\"*\"}]}"
  aws iam attach-role-policy --role-name $NAME-ec2 --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
  aws iam create-instance-profile --instance-profile-name $NAME-ec2 >/dev/null
  aws iam add-role-to-instance-profile --instance-profile-name $NAME-ec2 --role-name $NAME-ec2
  sleep 10
fi

USERDATA=$(cat <<EOF
#!/bin/bash
set -e
dnf install -y python3.12 python3.12-pip tar >/dev/null
mkdir -p /opt/carecircle && cd /opt/carecircle
cat > /opt/carecircle/update.sh <<'UPD'
#!/bin/bash
cd /opt/carecircle && aws s3 cp s3://$BUCKET/bundle.tgz /tmp/b.tgz && tar xzf /tmp/b.tgz
[ -d .venv ] || python3.12 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
systemctl restart carecircle
UPD
chmod +x update.sh
cat > /etc/systemd/system/carecircle.service <<'SVC'
[Unit]
Description=CareCircle MCP server
After=network-online.target
[Service]
WorkingDirectory=/opt/carecircle
Environment=HOST=0.0.0.0 PORT=8000 AWS_REGION=$REGION
ExecStart=/opt/carecircle/.venv/bin/python -m carecircle.server
Restart=always
[Install]
WantedBy=multi-user.target
SVC
systemctl daemon-reload && systemctl enable carecircle
/opt/carecircle/update.sh
EOF
)

IID=$(aws ec2 describe-instances --filters Name=tag:Name,Values=$NAME Name=instance-state-name,Values=running,pending \
      --query "Reservations[0].Instances[0].InstanceId" --output text)
if [ "$IID" = "None" ]; then
  echo "== Security group"
  VPC=$(aws ec2 describe-vpcs --filters Name=is-default,Values=true --query "Vpcs[0].VpcId" --output text)
  SG=$(aws ec2 describe-security-groups --filters Name=group-name,Values=$NAME Name=vpc-id,Values=$VPC \
       --query "SecurityGroups[0].GroupId" --output text)
  if [ "$SG" = "None" ]; then
    SG=$(aws ec2 create-security-group --group-name $NAME --description "CareCircle MCP" --vpc-id $VPC --query GroupId --output text)
    # Only CloudFront can reach the origin
    PL=$(aws ec2 describe-managed-prefix-lists --filters Name=prefix-list-name,Values=com.amazonaws.global.cloudfront.origin-facing \
         --query "PrefixLists[0].PrefixListId" --output text)
    aws ec2 authorize-security-group-ingress --group-id $SG \
      --ip-permissions "IpProtocol=tcp,FromPort=8000,ToPort=8000,PrefixListIds=[{PrefixListId=$PL}]" >/dev/null
  fi
  echo "== Launch EC2"
  AMI=$(aws ssm get-parameter --name /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 --query Parameter.Value --output text)
  IID=$(aws ec2 run-instances --image-id $AMI --instance-type t3.small --security-group-ids $SG \
        --iam-instance-profile Name=$NAME-ec2 --user-data "$USERDATA" \
        --metadata-options HttpTokens=required \
        --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$NAME}]" \
        --query "Instances[0].InstanceId" --output text)
  aws ec2 wait instance-running --instance-ids $IID
else
  echo "== Updating code on $IID"
  aws ssm send-command --instance-ids $IID --document-name AWS-RunShellScript \
    --parameters commands=/opt/carecircle/update.sh >/dev/null 2>&1 || \
    echo "   (SSM not available - reboot instance or re-run user data to update)"
fi
DNS=$(aws ec2 describe-instances --instance-ids $IID --query "Reservations[0].Instances[0].PublicDnsName" --output text)
echo "   instance $IID  origin $DNS"

echo "== CloudFront (HTTPS)"
CF=$(aws cloudfront list-distributions --query "DistributionList.Items[?Comment=='$NAME'].DomainName | [0]" --output text 2>/dev/null || echo None)
if [ "$CF" = "None" ] || [ -z "$CF" ]; then
  cat > /tmp/cf.json <<EOF
{"CallerReference":"$NAME-$(date +%s)","Comment":"$NAME","Enabled":true,
 "Origins":{"Quantity":1,"Items":[{"Id":"ec2","DomainName":"$DNS",
   "CustomOriginConfig":{"HTTPPort":8000,"HTTPSPort":443,"OriginProtocolPolicy":"http-only","OriginReadTimeout":60}}]},
 "DefaultCacheBehavior":{"TargetOriginId":"ec2","ViewerProtocolPolicy":"redirect-to-https",
   "AllowedMethods":{"Quantity":7,"Items":["GET","HEAD","OPTIONS","PUT","POST","PATCH","DELETE"],
     "CachedMethods":{"Quantity":2,"Items":["GET","HEAD"]}},
   "CachePolicyId":"4135ea2d-6df8-44a3-9df3-4b5a84be39ad",
   "OriginRequestPolicyId":"b689b0a8-53d0-40ab-baf2-68738e2966ac","Compress":false},
 "PriceClass":"PriceClass_200"}
EOF
  CF=$(aws cloudfront create-distribution --distribution-config file:///tmp/cf.json --query Distribution.DomainName --output text)
  echo "   CloudFront is deploying (5-10 min)."
fi
echo
echo "CareCircle (when ready):"
echo "  Alexa+ simulator : https://$CF/alexa"
echo "  Family dashboard : https://$CF/dashboard"
echo "  MCP endpoint     : https://$CF/mcp"
