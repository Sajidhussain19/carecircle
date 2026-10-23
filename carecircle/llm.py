"""Amazon Bedrock wrapper (Converse API) with a deterministic offline fallback.

Offline mode lets the whole project run and be tested with zero cloud setup;
every caller supplies its own rule-based fallback.
"""
from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from typing import Any

from . import config

log = logging.getLogger("carecircle.llm")


@lru_cache(maxsize=1)
def _client():
    import boto3

    return boto3.client("bedrock-runtime", region_name=config.AWS_REGION)


@lru_cache(maxsize=1)
def provider() -> str:
    """'bedrock', 'openai' (any OpenAI-compatible API, e.g. Gemini) or 'offline'."""
    mode = config.LLM_MODE
    if mode in ("offline", "bedrock", "openai"):
        return mode
    if config.OPENAI_API_KEY:
        return "openai"
    try:  # auto: use Bedrock if AWS credentials resolve
        import boto3

        return "bedrock" if boto3.Session().get_credentials() is not None else "offline"
    except Exception:
        return "offline"


def enabled() -> bool:
    return provider() != "offline"


def converse(
    messages: list[dict],
    system: str = "",
    tools: list[dict] | None = None,
    model: str | None = None,
    max_tokens: int = 1024,
    temperature: float = 0.2,
) -> dict:
    """Bedrock Converse-shaped call, routed to the configured provider."""
    if provider() == "openai":
        return _openai_converse(messages, system, tools, max_tokens, temperature)
    return _bedrock_converse(messages, system, tools, model, max_tokens, temperature)


def _openai_converse(messages, system, tools, max_tokens, temperature) -> dict:
    """Translate a Converse request to an OpenAI-compatible /chat/completions call and back."""
    import base64
    import urllib.request

    out: list[dict] = [{"role": "system", "content": system}] if system else []
    for m in messages:
        parts, calls = [], []
        for c in m["content"]:
            if "text" in c:
                parts.append({"type": "text", "text": c["text"]})
            elif "image" in c:
                b64 = base64.b64encode(c["image"]["source"]["bytes"]).decode()
                parts.append({"type": "image_url",
                              "image_url": {"url": f"data:image/{c['image']['format']};base64,{b64}"}})
            elif "toolUse" in c:
                u = c["toolUse"]
                calls.append({"id": u["toolUseId"], "type": "function",
                              "function": {"name": u["name"], "arguments": json.dumps(u.get("input") or {})}})
            elif "toolResult" in c:
                r = c["toolResult"]
                body = r["content"][0]
                out.append({"role": "tool", "tool_call_id": r["toolUseId"],
                            "content": json.dumps(body["json"]) if "json" in body else body.get("text", "")})
        if m["role"] == "assistant":
            msg: dict[str, Any] = {"role": "assistant", "content": "".join(p["text"] for p in parts if p["type"] == "text") or None}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
        elif parts:
            out.append({"role": "user", "content": parts})
    req: dict[str, Any] = {"model": config.OPENAI_MODEL, "messages": out, "max_tokens": max_tokens,
                           "temperature": temperature}
    if tools:
        req["tools"] = [{"type": "function", "function": {"name": t["toolSpec"]["name"],
                                                          "description": t["toolSpec"]["description"],
                                                          "parameters": t["toolSpec"]["inputSchema"]["json"]}}
                        for t in tools]
    http = urllib.request.Request(
        config.OPENAI_BASE_URL.rstrip("/") + "/chat/completions", data=json.dumps(req).encode(),
        headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}", "Content-Type": "application/json"})
    with urllib.request.urlopen(http, timeout=60) as r:
        choice = json.loads(r.read())["choices"][0]["message"]
    content: list[dict] = []
    if choice.get("content"):
        content.append({"text": choice["content"]})
    for tc in choice.get("tool_calls") or []:
        content.append({"toolUse": {"toolUseId": tc["id"], "name": tc["function"]["name"],
                                    "input": json.loads(tc["function"].get("arguments") or "{}")}})
    return {"output": {"message": {"role": "assistant", "content": content}}}


def _bedrock_converse(
    messages: list[dict],
    system: str = "",
    tools: list[dict] | None = None,
    model: str | None = None,
    max_tokens: int = 1024,
    temperature: float = 0.2,
) -> dict:
    """Raw Bedrock Converse call. Returns the full response dict."""
    kwargs: dict[str, Any] = {
        "modelId": model or config.FAST_MODEL,
        "messages": messages,
        "inferenceConfig": {"maxTokens": max_tokens, "temperature": temperature},
    }
    if system:
        kwargs["system"] = [{"text": system}]
    if tools:
        kwargs["toolConfig"] = {"tools": tools}
    return _client().converse(**kwargs)


def _text_of(resp: dict) -> str:
    parts = resp["output"]["message"]["content"]
    return "".join(p.get("text", "") for p in parts).strip()


def complete(prompt: str, system: str = "", model: str | None = None, max_tokens: int = 1024) -> str:
    resp = converse([{"role": "user", "content": [{"text": prompt}]}], system, model=model, max_tokens=max_tokens)
    return _text_of(resp)


def extract_json(text: str) -> Any:
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1)
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    if start < 0:
        raise ValueError(f"No JSON in model output: {text[:200]}")
    return json.loads(text[start : max(text.rfind("}"), text.rfind("]")) + 1])


def complete_json(prompt: str, system: str = "", model: str | None = None, max_tokens: int = 1500) -> Any:
    return extract_json(complete(prompt, system + "\nRespond with JSON only.", model, max_tokens))


def vision_json(image: bytes, fmt: str, prompt: str, model: str | None = None) -> Any:
    """Send an image (png/jpeg/webp/gif) plus instructions; parse JSON back."""
    msg = {
        "role": "user",
        "content": [{"image": {"format": fmt, "source": {"bytes": image}}}, {"text": prompt}],
    }
    resp = converse([msg], "You extract structured data from images. Respond with JSON only.",
                    model=model or config.SMART_MODEL, max_tokens=2000, temperature=0)
    return extract_json(_text_of(resp))
