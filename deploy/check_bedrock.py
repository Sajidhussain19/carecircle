"""Find working Claude models on Bedrock for this account; print .env lines.

Tries the account's Anthropic inference profiles (newest first) with a tiny Converse call.
"""
import os
import sys

import boto3

region = os.environ.get("AWS_REGION", "us-east-1")
bedrock = boto3.client("bedrock", region_name=region)
runtime = boto3.client("bedrock-runtime", region_name=region)

ids = []
try:
    for p in bedrock.list_inference_profiles(maxResults=1000)["inferenceProfileSummaries"]:
        pid = p["inferenceProfileId"]
        if "anthropic.claude" in pid and pid.startswith(("us.", "global.")):
            ids.append(pid)
except Exception as e:  # pragma: no cover
    print(f"# could not list inference profiles: {e}", file=sys.stderr)


def works(mid):
    try:
        runtime.converse(modelId=mid, messages=[{"role": "user", "content": [{"text": "Say ok"}]}],
                         inferenceConfig={"maxTokens": 5})
        return True
    except Exception as e:
        print(f"#  {mid}: {type(e).__name__}: {str(e)[:120]}", file=sys.stderr)
        return False


ids = sorted(set(ids), key=lambda s: (s.split("-v")[0][-8:], s), reverse=True)  # newest date suffix first
haiku = [i for i in ids if "haiku" in i]
smart = [i for i in ids if "sonnet" in i] + [i for i in ids if "opus" in i]
fast = next((m for m in haiku if works(m)), None)
best = next((m for m in smart if works(m)), None)
if not fast and not best:
    sys.exit("No Claude model works yet - check Bedrock model access / the Anthropic use-case form.")
print(f"CARECIRCLE_FAST_MODEL={fast or best}")
print(f"CARECIRCLE_SMART_MODEL={best or fast}")
