"""Simulated Alexa+ assistant.

It connects to the CareCircle server *as a real MCP client over Streamable HTTP* (exactly how Alexa+
consumes a brand's MCP server), discovers the tools, and lets a Bedrock model decide which to call.
Every reply passes the safety judge before it is spoken.
"""
from __future__ import annotations

import json
import logging
import re
import time
from contextlib import asynccontextmanager
from typing import Any

import anyio
import httpx2
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from . import config, llm, safety

log = logging.getLogger("carecircle.voice")

SYSTEM = """You are Alexa+, a warm voice assistant. You are talking with {name}, an elderly person who lives alone.
You have CareCircle tools (an MCP server) for medications, wellbeing, appointments and contacting family.
Rules: Always use tools for facts - never guess schedules. When they mention feelings, pain or symptoms,
call log_wellbeing with their exact words. If a tool result has say_to_user or emergency=true, follow it.
Never give medical advice or suggest changing medication. Replies are SPOKEN: 1-3 short sentences,
no lists, no markdown, use times like '8 PM'. Current local time: {time}."""


@asynccontextmanager
async def mcp_session():
    headers = {"Authorization": f"Bearer {config.API_TOKEN}"} if config.API_TOKEN else {}
    async with httpx2.AsyncClient(headers=headers, timeout=60) as http:
        async with streamable_http_client(config.MCP_URL, http_client=http) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


def _tool_result_payload(res) -> Any:
    if getattr(res, "structured_content", None):
        sc = res.structured_content
        return sc.get("result", sc) if isinstance(sc, dict) and list(sc) == ["result"] else sc
    texts = [c.text for c in res.content if getattr(c, "type", "") == "text"]
    joined = "\n".join(texts)
    try:
        return json.loads(joined)
    except Exception:
        return joined


async def call(session: ClientSession, name: str, args: dict, trace: list) -> Any:
    t0 = time.perf_counter()
    res = await session.call_tool(name, args)
    payload = _tool_result_payload(res)
    trace.append({"tool": name, "args": args, "result": payload, "error": bool(res.is_error),
                  "ms": int((time.perf_counter() - t0) * 1000)})
    return payload


# ---------------------------------------------------------------- Bedrock agent loop

async def _llm_turn(session: ClientSession, text: str, history: list[dict], trace: list) -> str:
    from datetime import datetime

    from .care import get_profile

    tools = (await session.list_tools()).tools
    tool_cfg = [{"toolSpec": {"name": t.name, "description": (t.description or "")[:1000],
                              "inputSchema": {"json": t.input_schema}}} for t in tools]
    system = SYSTEM.format(name=await anyio.to_thread.run_sync(lambda: get_profile().get("name", "there")),
                           time=datetime.now(config.TZ).strftime("%A %H:%M"))
    messages = [{"role": h["role"], "content": [{"text": h["text"]}]} for h in history[-8:] if h.get("text")]
    messages.append({"role": "user", "content": [{"text": text}]})

    for _ in range(6):
        resp = await anyio.to_thread.run_sync(
            lambda: llm.converse(messages, system, tools=tool_cfg, max_tokens=500, temperature=0.3))
        msg = resp["output"]["message"]
        messages.append(msg)
        uses = [c["toolUse"] for c in msg["content"] if "toolUse" in c]
        if not uses:
            return "".join(c.get("text", "") for c in msg["content"]).strip()
        results = []
        for u in uses:
            try:
                out = await call(session, u["name"], u.get("input") or {}, trace)
                results.append({"toolResult": {"toolUseId": u["toolUseId"], "content": [{"json": {"result": out}}]}})
            except Exception as e:
                results.append({"toolResult": {"toolUseId": u["toolUseId"], "status": "error",
                                               "content": [{"text": str(e)}]}})
        messages.append({"role": "user", "content": results})
    return "Sorry, I got a bit muddled there. Could you say that again?"


# ---------------------------------------------------------------- offline intent router (no cloud needed)

SYMPTOM_WORDS = ["dizzy", "headache", "tired", "nausea", "pain", "cough", "fever", "weak", "breathless",
                 "chest pain", "lonely", "sad", "anxious", "can't sleep", "fell", "confused", "swollen"]


def _hhmm(s: str) -> str:
    h, m = map(int, s.split(":"))
    return f"{(h % 12) or 12}{':%02d' % m if m else ''} {'PM' if h >= 12 else 'AM'}"


async def _offline_turn(session: ClientSession, text: str, trace: list) -> str:
    t = text.lower()
    if re.search(r"\b(tell|call|message|text|inform) (my )?(son|daughter|family|kids|children)\b", t):
        await call(session, "alert_family", {"message": f"Message from Rose: \"{text}\"", "urgency": "alert"}, trace)
        return "Okay, I've sent that to your family. They'll see it right away."
    if re.search(r"\b(how is|how's) (mum|mom|mother|dad|father|rose)", t):
        s = await call(session, "family_status", {}, trace)
        a = s["adherence_7d"]
        return f"{s['name']} has taken {a['taken']} of {a['scheduled']} doses this week. " + (
            f"Latest check-in: {s['latest_checkins'][0]['note']}" if s["latest_checkins"] else "")
    if re.search(r"\b(can|should) i (take|double|skip|stop)\b|\b(double|extra|two)\b.*\b(dose|tablet|pill|metformin|\w+pine)", t):
        return ("I can't advise on changing your medicines - please check with your doctor or pharmacist. "
                "Would you like me to ask your family to call them for you?")
    if re.search(r"\b(skip|skipped|didn't take|did not take|forgot|missed)\b", t):
        r = await call(session, "log_medication", {"medication": text, "taken": False}, trace)
        return f"Noted that you skipped your {r['medication']}." if r.get("ok") else r["message"]
    if re.search(r"\b(took|taken|had|take)\b.*\b(tablet|pill|medicine|meds|dose|\w+pine|\w+formin|\w+statin)\b", t) \
            or re.search(r"\btook my\b", t):
        r = await call(session, "log_medication", {"medication": text, "taken": True}, trace)
        if not r.get("ok"):
            return r["message"]
        if r.get("already_done"):
            return r["message"]
        nxt = f" Your next one is {r['next_dose'].split(' at ')[0]} at {_hhmm(r['next_dose'].split(' at ')[1])}." if r.get("next_dose") else ""
        return f"Great, I've logged your {r['medication']}.{nxt}"
    syms = [w for w in SYMPTOM_WORDS if w in t]
    if syms or re.search(r"\b(feel|feeling|felt|hurt|unwell|fell)\b", t):
        mood = 2 if syms or re.search(r"\b(bad|awful|unwell|not good|low)\b", t) else 4
        r = await call(session, "log_wellbeing", {"note": text, "mood": mood, "symptoms": syms}, trace)
        if r["emergency"]:
            return ("I'm worried about that. I've alerted your family right now. If it's getting worse, "
                    "please call emergency services immediately.")
        if r["family_alerted"]:
            return "I'm sorry you're not feeling well. I've noted it, and since this has happened a few times I've let your family know."
        return "Thank you for telling me, I've noted how you're feeling."
    if re.search(r"\b(appointment|doctor|visit|clinic)\b", t):
        r = await call(session, "get_appointments", {"days_ahead": 14}, trace)
        if not r["appointments"]:
            return "You don't have any appointments in the next two weeks."
        a = r["appointments"][0]
        return f"Your next appointment is {a['title']} on {a['when'][:10]} at {_hhmm(a['when'][11:16])}."
    if "summary" in t or "week" in t:
        r = await call(session, "weekly_summary", {}, trace)
        return re.sub(r"\*\*", "", r["text"]).split("\n\n")[0]
    r = await call(session, "get_today_plan", {}, trace)
    left = [d for d in r["doses"] if d["status"] in ("due", "upcoming")]
    msg = f"Today you have {len(r['doses'])} doses; {len(left)} still to take."
    if left:
        msg += f" Next is {left[0]['medication']} at {_hhmm(left[0]['slot'])}."
    return msg


# ---------------------------------------------------------------- entry point

async def respond(text: str, history: list[dict] | None = None) -> dict:
    text = (text or "").strip()
    if not text:
        return {"reply": "Sorry, I didn't catch that.", "trace": [], "safety": None, "mode": "none"}
    trace: list[dict] = []
    mode = llm.provider()
    t0 = time.perf_counter()
    async with mcp_session() as session:
        if mode != "offline":
            try:
                draft = await _llm_turn(session, text, history or [], trace)
            except Exception as e:
                log.warning("Bedrock agent failed (%s) - falling back to offline router", e)
                mode, draft = "offline", await _offline_turn(session, text, trace)
        else:
            draft = await _offline_turn(session, text, trace)
    reviewed = await anyio.to_thread.run_sync(lambda: safety.review(text, draft, [x["result"] for x in trace]))
    return {"reply": reviewed["reply"], "draft": draft, "trace": trace, "safety": reviewed["verdict"],
            "mode": mode, "ms": int((time.perf_counter() - t0) * 1000)}
