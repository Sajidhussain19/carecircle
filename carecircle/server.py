"""CareCircle MCP server - Streamable HTTP, built for Alexa+.

Run:  python -m carecircle.server      (MCP endpoint: http://127.0.0.1:8000/mcp)
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse

from . import care, config, insights, llm
from .notify import send_family_alert
from .risk_agent import run_risk_check
from .store import now

log = logging.getLogger("carecircle")
WEB = Path(__file__).resolve().parent.parent / "web"

INSTRUCTIONS = """CareCircle helps an elderly person who lives alone manage medications, appointments and
daily wellbeing by voice, and keeps their family informed.
Guidance for the assistant:
- Start a daily conversation with get_today_plan.
- When the user says they took (or skipped) a medicine, call log_medication. They may name it loosely
  ("my BP tablet", "the white pill") - pass their words; the server fuzzy-matches.
- When the user mentions how they feel, ANY symptom, pain, a fall or low mood, call log_wellbeing with
  their own words in `note`. Its result says whether family was alerted - tell the user kindly.
- Never give medical advice, diagnoses or dose changes. Defer to their doctor or pharmacist.
- Keep spoken replies to 1-3 short, warm sentences."""

mcp = MCPServer(
    name="carecircle",
    title="CareCircle",
    description="Voice-first medication, wellbeing and family-alert companion for elderly people living alone.",
    instructions=INSTRUCTIONS,
    version="1.0.0",
)

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
NOTIFY = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)


# ------------------------------------------------------------------ tools

@mcp.tool(title="Today's plan", annotations=READ)
def get_today_plan() -> dict[str, Any]:
    """Today's medication doses (with taken/missed/due/upcoming status) and today's appointments.
    Use at the start of a conversation or when asked 'what do I need to do today?'."""
    at = now()
    appts = [a for a in care.upcoming_appointments(1, at) if a["when"][:10] == at.date().isoformat()]
    doses = care.day_doses(at.date(), at)
    return {"date": at.strftime("%A %d %B"), "time": at.strftime("%H:%M"),
            "name": care.get_profile().get("name"), "doses": doses, "appointments": appts,
            "due_now": [d for d in doses if d["status"] == "due"],
            "missed_today": [d for d in doses if d["status"] == "missed"]}


@mcp.tool(title="Log a medication dose", annotations=WRITE)
def log_medication(medication: str, taken: bool = True, note: str = "") -> dict[str, Any]:
    """Record that the user took (taken=true) or skipped (taken=false) a medication dose.
    `medication` can be the drug name or a loose description such as 'blood pressure tablet'."""
    return care.log_dose(medication, taken, note)


@mcp.tool(title="Medication schedule", annotations=READ)
def get_medication_schedule() -> dict[str, Any]:
    """All active medications with dose, daily times, instructions and purpose, plus 7-day adherence."""
    return {"medications": [{k: m.get(k) for k in ("name", "dose", "times", "instructions", "purpose")}
                            for m in care.list_meds()],
            "adherence_7d": care.adherence(7)}


@mcp.tool(title="Add a medication", annotations=WRITE)
def add_medication(name: str, dose: str, times: list[str], instructions: str = "", purpose: str = "") -> dict[str, Any]:
    """Add (or update) a medication. `times` are daily times like ['08:00','20:00'] or words like
    ['morning','night']. Only use when the user or a caregiver explicitly provides the prescription details."""
    m = care.add_med(name, dose, times, instructions, purpose)
    return {"ok": True, "medication": {k: m[k] for k in ("name", "dose", "times", "instructions")}}


@mcp.tool(title="Log wellbeing / symptoms", annotations=NOTIFY)
def log_wellbeing(note: str, mood: int | None = None, symptoms: list[str] | None = None) -> dict[str, Any]:
    """Record how the user is feeling. `note` = their own words (always pass it). `mood` 1 (very low) - 5 (great)
    if you can infer it. `symptoms` = short lowercase terms, e.g. ['dizzy','headache'].
    Runs the Care Risk Agent: red-flag symptoms (chest pain, a fall, trouble breathing...) alert family
    immediately; recurring patterns (e.g. dizziness + missed doses) may also notify family.
    The result includes `say_to_user` guidance and whether family was alerted."""
    rec = care.log_wellbeing(mood, symptoms or [], note)
    risk = run_risk_check(trigger="wellbeing check-in")
    d = risk["decision"]
    return {"logged": True, "red_flags": rec["red_flags"], "risk_level": risk["level"],
            "family_alerted": risk["family_alerted"], "say_to_user": d.get("elder_message", ""),
            "emergency": bool(rec["red_flags"]),
            "findings": [f["detail"] for f in risk["findings"]]}


@mcp.tool(title="Upcoming appointments", annotations=READ)
def get_appointments(days_ahead: int = 14) -> dict[str, Any]:
    """Upcoming appointments (doctor visits, tests, etc.) within `days_ahead` days."""
    return {"appointments": care.upcoming_appointments(days_ahead)}


@mcp.tool(title="Add an appointment", annotations=WRITE)
def add_appointment(title: str, when: str, location: str = "", notes: str = "") -> dict[str, Any]:
    """Add an appointment. `when` is ISO-8601 local time, e.g. '2026-10-27T10:30'."""
    a = care.add_appointment(title, when, location, notes)
    return {"ok": True, "appointment": a}


@mcp.tool(title="Alert family", annotations=NOTIFY)
def alert_family(message: str, urgency: Literal["info", "alert", "urgent"] = "alert") -> dict[str, Any]:
    """Send a message to the user's family (SMS/email + family dashboard). Use when the user asks you to
    tell/call/inform their family, or needs help. Use 'urgent' only for emergencies."""
    rec = send_family_alert(message, urgency, reason="Requested by user via voice", source="user")
    return {"sent": True, "level": rec["level"], "channels": ["dashboard", *rec.get("delivered", [])]}


@mcp.tool(title="Family: how are they doing?", annotations=READ)
def family_status() -> dict[str, Any]:
    """For FAMILY members asking 'How is Mum doing?': today's doses, 7-day adherence, latest check-ins,
    recent alerts and next appointment."""
    at = now()
    wb = care.wellbeing_history(3, at)
    appts = care.upcoming_appointments(30, at)
    return {"name": care.get_profile().get("name"), "today": care.day_doses(at.date(), at),
            "adherence_7d": care.adherence(7, at),
            "latest_checkins": [{"ts": w["ts"], "mood": w.get("mood"), "note": w.get("note")} for w in wb[:3]],
            "recent_alerts": [{"ts": a["ts"], "level": a["level"], "message": a["message"]}
                              for a in care.recent_alerts(48, at)],
            "next_appointment": appts[0] if appts else None}


@mcp.tool(title="Weekly family summary", annotations=READ)
def weekly_summary() -> dict[str, Any]:
    """AI-written weekly update for the family: adherence patterns, mood/symptom trends, upcoming
    appointments and suggested actions."""
    return insights.weekly_summary()


@mcp.tool(title="Import prescription photo", annotations=WRITE)
def import_prescription(image_base64: str, media_type: str = "image/jpeg") -> dict[str, Any]:
    """Read a photo of a prescription or medicine label (Amazon Bedrock vision) and add the medications
    to the schedule. Returns what was added and anything that needs human review."""
    return insights.import_prescription(image_base64, media_type)


# ------------------------------------------------------------------ web + REST (dashboard, simulator)

@mcp.custom_route("/", methods=["GET"])
async def root(_: Request):
    return RedirectResponse("/alexa")


@mcp.custom_route("/alexa", methods=["GET"])
async def alexa_page(_: Request):
    return FileResponse(WEB / "alexa.html")


@mcp.custom_route("/dashboard", methods=["GET"])
async def dashboard_page(_: Request):
    return FileResponse(WEB / "dashboard.html")


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request):
    return JSONResponse({"ok": True, "llm": llm.provider(), "storage": config.STORAGE})


@mcp.custom_route("/api/state", methods=["GET"])
async def api_state(_: Request):
    import anyio

    def build():
        at = now()
        days = []
        for i in range(6, -1, -1):
            from datetime import timedelta
            d = (at - timedelta(days=i)).date()
            doses = care.day_doses(d, at)
            days.append({"date": d.isoformat(), "taken": sum(x["status"] == "taken" for x in doses),
                         "missed": sum(x["status"] in ("missed", "skipped") for x in doses),
                         "open": sum(x["status"] in ("due", "upcoming") for x in doses)})
        from .store import get_store
        summaries = get_store().list("summary", limit=1)
        return {"profile": care.get_profile(), "now": at.isoformat(), "today": care.day_doses(at.date(), at),
                "adherence_7d": care.adherence(7, at), "days": days,
                "wellbeing": care.wellbeing_history(7, at), "appointments": care.upcoming_appointments(30, at),
                "alerts": care.recent_alerts(24 * 7, at), "meds": care.list_meds(),
                "summary": summaries[0] if summaries else None,
                "llm": llm.provider()}

    return JSONResponse(await anyio.to_thread.run_sync(build))


@mcp.custom_route("/api/chat", methods=["POST"])
async def api_chat(request: Request):
    from .voice_agent import respond

    body = await request.json()
    return JSONResponse(await respond(body.get("text", ""), body.get("history", [])))


@mcp.custom_route("/api/risk-check", methods=["POST"])
async def api_risk(_: Request):
    import anyio

    return JSONResponse(await anyio.to_thread.run_sync(lambda: run_risk_check(trigger="manual")))


@mcp.custom_route("/api/summary", methods=["POST"])
async def api_summary(_: Request):
    import anyio

    return JSONResponse(await anyio.to_thread.run_sync(insights.weekly_summary))


@mcp.custom_route("/api/prescription", methods=["POST"])
async def api_prescription(request: Request):
    import anyio

    body = await request.json()
    try:
        res = await anyio.to_thread.run_sync(
            lambda: insights.import_prescription(body["image_base64"], body.get("media_type", "image/jpeg")))
        return JSONResponse(res)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@mcp.custom_route("/api/reset-demo", methods=["POST"])
async def api_reset(_: Request):
    import anyio

    from .seed import seed

    return JSONResponse(await anyio.to_thread.run_sync(seed))


# ------------------------------------------------------------------ app

def build_app():
    from mcp.server.transport_security import TransportSecuritySettings

    app = mcp.streamable_http_app(
        streamable_http_path="/mcp",
        host=config.HOST,
        stateless_http=config.STATELESS,
        json_response=config.STATELESS,
        # Hosted behind a load balancer/proxy we can't predict the Host header; DNS-rebinding
        # protection only matters for localhost servers.
        transport_security=None if config.HOST in ("127.0.0.1", "localhost") else
        TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    if config.API_TOKEN:
        return _BearerGuard(app, config.API_TOKEN)
    return app


class _BearerGuard:
    """Require `Authorization: Bearer <CARECIRCLE_API_TOKEN>` on /mcp when a token is configured."""

    def __init__(self, app, token: str):
        self.app, self.expected = app, f"Bearer {token}".encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith("/mcp"):
            auth = dict(scope.get("headers") or []).get(b"authorization", b"")
            if auth != self.expected:
                resp = JSONResponse({"error": "unauthorized"}, status_code=401)
                return await resp(scope, receive, send)
        return await self.app(scope, receive, send)


def main():
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    from .store import get_store

    if not get_store().list("med", limit=1):
        from .seed import seed

        seed()
        log.info("Seeded demo household")
    log.info("CareCircle MCP: http://%s:%s/mcp | LLM: %s | storage: %s", config.HOST, config.PORT,
             llm.provider(), config.STORAGE)
    uvicorn.run(build_app(), host=config.HOST, port=config.PORT, log_level="info")


if __name__ == "__main__":
    main()
