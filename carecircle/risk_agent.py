"""Care Risk Agent - a LangGraph workflow that turns raw logs into a decision.

    gather -> detect --(red flag)--> escalate --------------------> act
                     --(patterns)--> reason (Bedrock) -> act
                     --(nothing)---> END

Hard safety rules (red flags) never depend on the model. The model is used for
judgement on softer patterns and for writing a clear, kind message to family.
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timedelta
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from . import care, llm
from .notify import send_family_alert
from .store import iso, now

log = logging.getLogger("carecircle.risk")


class RiskState(TypedDict, total=False):
    trigger: str
    at: datetime
    context: dict
    findings: list[dict]
    decision: dict
    alert: dict | None


def gather(state: RiskState) -> RiskState:
    at = state.get("at") or now()
    wb = care.wellbeing_history(7, at)
    ctx = {
        "elder": care.get_profile().get("name", "Rose"),
        "adherence_7d": care.adherence(7, at),
        "adherence_2d": care.adherence(2, at),
        "wellbeing_7d": [{"ts": w["ts"], "mood": w.get("mood"), "symptoms": w.get("symptoms", []),
                          "note": w.get("note", ""), "red_flags": w.get("red_flags", [])} for w in wb],
        "recent_alerts": [{"ts": a["ts"], "level": a["level"], "reason": a.get("reason", "")}
                          for a in care.recent_alerts(12, at)],
    }
    return {"at": at, "context": ctx}


def detect(state: RiskState) -> RiskState:
    ctx, at = state["context"], state["at"]
    f: list[dict] = []
    wb = ctx["wellbeing_7d"]

    if wb and wb[0]["red_flags"] and wb[0]["ts"] >= iso(at - timedelta(hours=1)):
        f.append({"type": "red_flag", "severity": 3,
                  "detail": f"Reported {', '.join(wb[0]['red_flags'])}: \"{wb[0]['note'] or ', '.join(wb[0]['symptoms'])}\""})

    counts = Counter(s for w in wb for s in w["symptoms"])
    for sym, n in counts.items():
        if n >= 2:
            f.append({"type": "recurring_symptom", "severity": 2 if n >= 3 else 1,
                      "detail": f"'{sym}' reported {n} times in the last 7 days"})

    missed2 = ctx["adherence_2d"]["missed"]
    if missed2:
        per_med = Counter(m["medication"] for m in missed2)
        for med, n in per_med.items():
            f.append({"type": "missed_doses", "severity": 2 if n >= 2 else 1,
                      "detail": f"{med}: {n} dose(s) missed in the last 48h"})

    moods = [w["mood"] for w in wb[:5] if isinstance(w.get("mood"), (int, float))]
    low = [m for m in moods if m <= 2]
    if len(low) >= 3:
        f.append({"type": "low_mood", "severity": 2, "detail": f"Low mood in {len(low)} of last {len(moods)} check-ins"})

    if not wb or wb[0]["ts"] < iso(at - timedelta(hours=26)):
        f.append({"type": "no_checkin", "severity": 1, "detail": "No wellbeing check-in in over 24 hours"})

    # Combination is what matters clinically: a symptom + missed meds of any kind.
    if any(x["type"] == "recurring_symptom" for x in f) and any(x["type"] == "missed_doses" for x in f):
        f.append({"type": "combined", "severity": 2,
                  "detail": "Recurring symptom coincides with missed medication"})
    return {"findings": f}


def route(state: RiskState) -> str:
    f = state["findings"]
    if any(x["type"] == "red_flag" for x in f):
        return "escalate"
    return "reason" if f else "end"


def escalate(state: RiskState) -> RiskState:
    ctx = state["context"]
    rf = next(x for x in state["findings"] if x["type"] == "red_flag")
    return {"decision": {
        "level": "urgent",
        "family_message": f"{ctx['elder']} {rf['detail'][0].lower()}{rf['detail'][1:]}. Please call right now. "
                          "If you can't reach her, contact emergency services.",
        "elder_message": "That could be serious. I've alerted your family right now. If it is getting worse, "
                         "please call emergency services immediately.",
        "reasoning": "Red-flag symptom - escalated by hard safety rule (no model involved).",
        "decided_by": "safety-rule",
    }}


REASON_SYSTEM = """You are the risk-assessment step of CareCircle, an assistant that helps adult children look after an
elderly parent living alone. You get findings detected from the parent's medication and wellbeing logs.
Decide ONE level:
- "none": nothing worth bothering family about.
- "nudge": gently remind the parent only (e.g. a single missed dose).
- "alert": notify the family - a pattern a caring son/daughter would want to know about today
  (e.g. a symptom repeating while medication is being missed, several days of low mood).
Never diagnose and never suggest changing medication. Be calm, specific and kind; cite the evidence.
Return JSON: {"level": "...", "family_message": "<=2 sentences for the family>",
"elder_message": "<=1 short sentence spoken to the parent>", "reasoning": "<1 sentence>"}"""


def _offline_reason(state: RiskState) -> dict:
    f, ctx = state["findings"], state["context"]
    score = sum(x["severity"] for x in f)
    level = "alert" if score >= 4 or any(x["type"] == "combined" for x in f) else ("nudge" if score >= 1 else "none")
    ev = "; ".join(x["detail"] for x in f)
    return {"level": level,
            "family_message": f"Heads-up about {ctx['elder']}: {ev}. It may be worth a call today.",
            "elder_message": "Please remember to take your medicines on time - I've let your family know how you're doing."
            if level == "alert" else "Just a gentle reminder to take your medicines on time.",
            "reasoning": f"Rule-based score {score}."}


def reason(state: RiskState) -> RiskState:
    decision = None
    if llm.enabled():
        try:
            prompt = (f"Parent: {state['context']['elder']}\nTrigger: {state.get('trigger','check')}\n"
                      f"Findings: {state['findings']}\n"
                      f"7-day adherence: {state['context']['adherence_7d']['rate']}\n"
                      f"Recent wellbeing: {state['context']['wellbeing_7d'][:6]}")
            decision = llm.complete_json(prompt, REASON_SYSTEM)
            decision["decided_by"] = llm.provider()
        except Exception as e:
            log.warning("Bedrock reasoning failed, using rules: %s", e)
    if not decision or decision.get("level") not in ("none", "nudge", "alert"):
        decision = {**_offline_reason(state), "decided_by": "rules"}
    return {"decision": decision}


def act(state: RiskState) -> RiskState:
    d, ctx = state["decision"], state["context"]
    if d["level"] in ("none", "nudge"):
        return {"alert": None}
    # De-duplicate: don't page the family twice for the same thing within 12h (urgent always goes out).
    if d["level"] != "urgent" and any(a["level"] in ("alert", "urgent") for a in ctx["recent_alerts"]):
        d["suppressed"] = "family already alerted in the last 12h"
        return {"alert": None, "decision": d}
    alert = send_family_alert(d["family_message"], d["level"], d.get("reasoning", ""), source="risk-agent",
                              evidence=[x["detail"] for x in state["findings"]])
    return {"alert": alert}


def _build():
    g = StateGraph(RiskState)
    for name, fn in [("gather", gather), ("detect", detect), ("escalate", escalate), ("reason", reason), ("act", act)]:
        g.add_node(name, fn)
    g.set_entry_point("gather")
    g.add_edge("gather", "detect")
    g.add_conditional_edges("detect", route, {"escalate": "escalate", "reason": "reason", "end": END})
    g.add_edge("escalate", "act")
    g.add_edge("reason", "act")
    g.add_edge("act", END)
    return g.compile()


GRAPH = _build()


def run_risk_check(trigger: str = "scheduled", at: datetime | None = None) -> dict[str, Any]:
    out = GRAPH.invoke({"trigger": trigger, "at": at or now()})
    d = out.get("decision") or {"level": "none", "reasoning": "No concerning patterns.", "decided_by": "rules"}
    return {"level": d["level"], "decision": d, "findings": out.get("findings", []),
            "family_alerted": bool(out.get("alert"))}
