"""Bedrock-powered insights: prescription photo -> medication schedule, and the weekly family summary."""
from __future__ import annotations

import base64
import logging
from datetime import datetime, timedelta

from . import care, llm
from .store import get_store, now

log = logging.getLogger("carecircle.insights")

RX_PROMPT = """This is a photo of a doctor's prescription or a medicine label for an elderly patient.
Extract every medication. Convert dosing frequency into concrete clock times (24h HH:MM) using:
once daily/OD -> 08:00; twice daily/BD/BID -> 08:00, 20:00; three times/TDS/TID -> 08:00, 14:00, 20:00;
at night/HS -> 21:00; before breakfast -> 07:30; after food -> keep the meal time.
Return JSON: {"patient": "<name or empty>", "doctor": "<name or empty>",
"medications": [{"name": "...", "dose": "e.g. 5 mg, 1 tablet", "times": ["08:00"],
"instructions": "e.g. after food", "purpose": "what it's for if written, else empty",
"confidence": 0.0-1.0}],
"warnings": ["anything illegible or ambiguous that a human must double-check"]}
Do not invent medications that are not visible."""


def parse_prescription(image_b64: str, media_type: str = "image/jpeg") -> dict:
    if not llm.enabled():
        raise RuntimeError("Prescription reading needs Amazon Bedrock (set AWS credentials).")
    fmt = media_type.split("/")[-1].replace("jpg", "jpeg")
    if fmt not in ("png", "jpeg", "gif", "webp"):
        raise ValueError(f"Unsupported image type {media_type}")
    if "," in image_b64[:100]:  # strip data: URL prefix
        image_b64 = image_b64.split(",", 1)[1]
    return llm.vision_json(base64.b64decode(image_b64), fmt, RX_PROMPT)


def import_prescription(image_b64: str, media_type: str = "image/jpeg", min_confidence: float = 0.6) -> dict:
    parsed = parse_prescription(image_b64, media_type)
    added, review = [], list(parsed.get("warnings", []))
    for m in parsed.get("medications", []):
        if float(m.get("confidence", 1)) < min_confidence:
            review.append(f"Low confidence reading '{m.get('name')}' - please confirm manually.")
            continue
        rec = care.add_med(m["name"], m.get("dose", ""), m.get("times") or ["08:00"],
                           m.get("instructions", ""), m.get("purpose", ""), source="prescription-photo")
        added.append({k: rec[k] for k in ("name", "dose", "times", "instructions")})
    return {"added": added, "needs_review": review, "doctor": parsed.get("doctor", "")}


SUMMARY_SYSTEM = """You write a short weekly update for adult children about their elderly parent, based ONLY on
the data given. Warm, plain English, no medical diagnosis, no medication advice. Structure (markdown):
**Overall** (1-2 sentences) / **Medications** (adherence %, which doses get missed and when - spot patterns
like 'evening doses') / **How they're feeling** (mood trend, recurring symptoms) / **Coming up** (appointments) /
**Suggested for this week** (2-3 concrete, kind actions for the family, e.g. 'mention the dizziness to Dr X on Tuesday').
Max 170 words."""


def weekly_summary(save: bool = True) -> dict:
    at = now()
    data = {
        "parent": care.get_profile().get("name", "Rose"),
        "adherence": care.adherence(7, at),
        "wellbeing": [{"day": w["ts"][:10], "mood": w.get("mood"), "symptoms": w.get("symptoms"),
                       "note": w.get("note")} for w in care.wellbeing_history(7, at)],
        "appointments": [{"title": a["title"], "when": a["when"], "location": a.get("location")}
                         for a in care.upcoming_appointments(14, at)],
        "alerts": [{"when": a["ts"], "level": a["level"], "message": a["message"]} for a in care.recent_alerts(24 * 7, at)],
    }
    text = None
    if llm.enabled():
        try:
            text = llm.complete(f"DATA: {data}", SUMMARY_SYSTEM, model=None, max_tokens=700)
        except Exception as e:
            log.warning("summary via Bedrock failed: %s", e)
    if not text:
        a = data["adherence"]
        rate = f"{int(a['rate'] * 100)}%" if a["rate"] is not None else "n/a"
        syms = sorted({s for w in data["wellbeing"] for s in (w["symptoms"] or [])})
        moods = [w["mood"] for w in data["wellbeing"] if w["mood"]]
        text = (f"**Overall** {data['parent']} logged {len(data['wellbeing'])} check-ins this week.\n\n"
                f"**Medications** {a['taken']}/{a['scheduled']} doses taken ({rate}); {len(a['missed'])} missed.\n\n"
                f"**How they're feeling** Average mood {round(sum(moods)/len(moods),1) if moods else 'n/a'}/5. "
                f"Symptoms mentioned: {', '.join(syms) or 'none'}.\n\n"
                f"**Coming up** " + ("; ".join(f"{x['title']} ({datetime.fromisoformat(x['when']).strftime('%a %d %b, %I:%M %p')})" for x in data['appointments']) or "nothing scheduled"))
    rec = {"text": text, "period_start": (at - timedelta(days=7)).date().isoformat(), "period_end": at.date().isoformat(),
           "generated_by": "bedrock" if llm.enabled() else "rules"}
    if save:
        rec = get_store().put("summary", rec)
    return rec
