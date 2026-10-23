"""Runtime configuration, read from environment variables (see .env.example)."""
from __future__ import annotations

import os
from zoneinfo import ZoneInfo


def _load_dotenv(path: str = ".env") -> None:
    """Minimal .env loader (KEY=VALUE lines); real environment variables win."""
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


AWS_REGION = _env("AWS_REGION", "us-east-1")

# Storage: "sqlite" (local, zero setup) or "dynamodb" (AWS)
STORAGE = _env("CARECIRCLE_STORAGE", "sqlite").lower()
SQLITE_PATH = _env("CARECIRCLE_SQLITE_PATH", "carecircle.db")
DYNAMO_TABLE = _env("CARECIRCLE_DYNAMO_TABLE", "carecircle")

# LLM: "bedrock", "offline", or "auto" (Bedrock if AWS credentials are present)
LLM_MODE = _env("CARECIRCLE_LLM", "auto").lower()
# Fast model for the voice agent / judge, stronger model for vision + summaries.
FAST_MODEL = _env("CARECIRCLE_FAST_MODEL", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
SMART_MODEL = _env("CARECIRCLE_SMART_MODEL", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")

# Alternative LLM: any OpenAI-compatible API (OpenAI, or Gemini via its OpenAI endpoint).
# Used when CARECIRCLE_LLM=openai, or in auto mode when OPENAI_API_KEY is set.
OPENAI_API_KEY = _env("OPENAI_API_KEY")
OPENAI_BASE_URL = _env("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_MODEL = _env("CARECIRCLE_OPENAI_MODEL", "gpt-4o-mini")

# Alerts: SNS topic the family is subscribed to (SMS / email). Empty = dashboard only.
SNS_TOPIC_ARN = _env("CARECIRCLE_SNS_TOPIC_ARN")

# Household (single-household demo deployment)
USER_ID = _env("CARECIRCLE_USER_ID", "rose")
TZ = ZoneInfo(_env("CARECIRCLE_TZ", "Asia/Kolkata"))

# Server
HOST = _env("HOST", "127.0.0.1")
PORT = int(_env("PORT", "8000"))
# URL the built-in voice agent uses to reach the MCP server (itself, by default)
MCP_URL = _env("CARECIRCLE_MCP_URL", f"http://127.0.0.1:{PORT}/mcp")
# Stateless JSON mode (for serverless hosts like AWS Lambda / AgentCore Runtime)
STATELESS = _env("CARECIRCLE_STATELESS", "0") in ("1", "true", "yes")
# Optional bearer token protecting /mcp when deployed publicly
API_TOKEN = _env("CARECIRCLE_API_TOKEN")
