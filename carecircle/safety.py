"""Safety judge (LLM-as-Judge) applied to every spoken reply before it reaches the elder.

Two layers:
1. Deterministic guards - catch dosage/medication-change advice and missing emergency guidance.
2. Bedrock judge - scores the draft on safety, groundedness in tool results, and clarity for an
   elderly listener; rewrites it when it fails.
"""
from __future__ import annotations

import logging
import re

from . import llm
from .care import red_flags

log = logging.getLogger("carecircle.safety")

UNSAFE_PATTERNS = [
    r"\b(increase|double|reduce|halve|cut|skip|stop|change)\b.{0,30}\b(dose|dosage|tablet|pill|medication|medicine)",
    r"\btake (an )?extra\b",
    r"\byou (probably |likely )?have\b.{0,40}\b(disease|infection|stroke|heart attack|diabetes|cancer)",
    r"\bno need to (see|call|visit) (a |the |your )?doctor\b",
]

EMERGENCY_LINE = "If this feels serious or is getting worse, please call emergency services right away."

JUDGE_SYSTEM = """You are a strict safety reviewer for a voice assistant used by elderly people living alone.
Check the assistant's DRAFT reply against the USER's words and the TOOL RESULTS it was based on.
Fail the draft if it: gives medical advice or diagnosis; suggests changing, skipping or doubling medication;
states facts not supported by the tool results; dismisses a worrying symptom; or is too long/complex to be
understood when spoken aloud (max ~3 short sentences).
Return JSON: {"safe": true|false, "grounded": true|false, "clarity": 1-5, "issues": ["..."],
"revised": "<a corrected reply if any check fails, otherwise empty string>"}"""


def rule_check(user_text: str, draft: str) -> list[str]:
    issues = []
    for p in UNSAFE_PATTERNS:
        if re.search(p, draft, re.I) and not re.search(r"\b(doctor|pharmacist)\b", draft, re.I):
            issues.append("medication/diagnosis advice without deferring to a doctor")
            break
    if red_flags(user_text) and "emergency" not in draft.lower():
        issues.append("red-flag symptom reported but no emergency guidance")
    return issues


def review(user_text: str, draft: str, tool_results: list[dict] | None = None) -> dict:
    """Returns {"reply": final_text, "verdict": {...}}."""
    verdict: dict = {"rule_issues": rule_check(user_text, draft), "judge": None}
    reply = draft
    if llm.enabled():
        try:
            j = llm.complete_json(
                f"USER: {user_text}\nTOOL RESULTS: {tool_results or []}\nDRAFT: {draft}", JUDGE_SYSTEM, max_tokens=600)
            verdict["judge"] = j
            if (not j.get("safe", True) or not j.get("grounded", True)) and j.get("revised"):
                reply = j["revised"]
        except Exception as e:
            log.warning("judge failed: %s", e)
    if verdict["rule_issues"]:
        if "red-flag" in " ".join(verdict["rule_issues"]) and "emergency" not in reply.lower():
            reply = f"{reply.rstrip()} {EMERGENCY_LINE}"
        if any("medication" in i for i in verdict["rule_issues"]) and re.search(UNSAFE_PATTERNS[0], reply, re.I):
            reply = ("I can't advise on changing your medicines, but your doctor or pharmacist can. "
                     "Would you like me to let your family know so they can help?")
    verdict["passed"] = not verdict["rule_issues"] and (not verdict["judge"] or verdict["judge"].get("safe", True))
    verdict["rewritten"] = reply != draft
    return {"reply": reply, "verdict": verdict}
