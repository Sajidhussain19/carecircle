"""Core care domain logic, shared by MCP tools, the risk agent and the dashboard API."""
from __future__ import annotations

import difflib
import re
from datetime import date, datetime, timedelta

from . import config
from .store import get_store, iso, now

GRACE = timedelta(hours=2)  # a dose counts as missed 2h after its slot

# Phrases that always trigger an immediate urgent alert, no model in the loop.
RED_FLAGS = {
    r"chest (pain|hurts?|tight)": "chest pain",
    r"(can'?t|cannot|hard to|trouble) breath": "trouble breathing",
    r"short(ness)? of breath|breathless": "shortness of breath",
    r"\bfell\b(?! asleep)|\bfallen\b|had a fall|fall down": "a fall",
    r"slurr": "slurred speech",
    r"face (is )?droop": "facial drooping",
    r"\bnumb": "numbness",
    r"\bfainted\b|passed out|blacked out": "fainting",
    r"bleeding|vomit(ing)? blood|blood in": "bleeding",
    r"\bconfus": "confusion",
}


# ---------- profile ----------

def get_profile() -> dict:
    recs = get_store().list("profile")
    return recs[0] if recs else {"name": "Rose", "family": []}


# ---------- medications ----------

def _norm_time(t: str) -> str:
    t = t.strip().lower()
    named = {"morning": "08:00", "breakfast": "08:00", "noon": "13:00", "lunch": "13:00", "afternoon": "14:00",
             "evening": "18:00", "dinner": "20:00", "night": "21:00", "bedtime": "22:00"}
    if t in named:
        return named[t]
    m = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", t)
    if not m:
        raise ValueError(f"Unrecognised time '{t}'. Use HH:MM, e.g. 08:00")
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if ap == "pm" and h < 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    return f"{h:02d}:{mi:02d}"


def list_meds(active_only: bool = True) -> list[dict]:
    meds = get_store().list("med")
    meds = [m for m in meds if m.get("active", True) or not active_only]
    return sorted(meds, key=lambda m: m["name"].lower())


def add_med(name: str, dose: str, times: list[str], instructions: str = "", purpose: str = "",
            source: str = "manual") -> dict:
    times = sorted({_norm_time(t) for t in times}) or ["08:00"]
    existing = find_med(name, cutoff=0.9)
    rid = existing["id"] if existing else None
    return get_store().put("med", {"name": name.strip(), "dose": dose.strip(), "times": times,
                                   "instructions": instructions.strip(), "purpose": purpose.strip(),
                                   "active": True, "source": source}, rid=rid)


ALIASES = {"bp": "blood pressure", "sugar": "blood sugar", "diabetes": "blood sugar diabetes",
           "statin": "cholesterol", "heart": "blood pressure"}


def find_med(name: str, cutoff: float = 0.55) -> dict | None:
    meds = list_meds()
    if not meds:
        return None
    q = name.lower().strip()
    for short, full in ALIASES.items():
        q = re.sub(rf"\b{short}\b", full, q)
    for m in meds:  # substring match on name or purpose ("my BP tablet")
        if q in m["name"].lower() or m["name"].lower() in q:
            return m
    qwords = set(re.findall(r"[a-z]+", q))
    best, best_score = None, 0
    for m in meds:  # purpose match ("my blood pressure tablet"), best word overlap wins
        pwords = {w for w in re.findall(r"[a-z]+", m.get("purpose", "").lower()) if len(w) > 2}
        score = len(pwords & qwords) + (2 if m.get("purpose") and m["purpose"].lower() in q else 0)
        if score > best_score:
            best, best_score = m, score
    if best:
        return best
    names = {m["name"].lower(): m for m in meds}
    hit = difflib.get_close_matches(q, list(names), n=1, cutoff=cutoff)
    return names[hit[0]] if hit else None


def _logs_for(day: date) -> list[dict]:
    return [l for l in get_store().list("medlog", since=day.isoformat()) if l.get("date") == day.isoformat()]


def day_doses(day: date, at: datetime | None = None) -> list[dict]:
    """Every scheduled dose for a day with status: taken / skipped / missed / due / upcoming."""
    at = at or now()
    logs = _logs_for(day)
    out = []
    for m in list_meds():
        for slot in m["times"]:
            log = next((l for l in logs if l["med_id"] == m["id"] and l["slot"] == slot), None)
            slot_dt = datetime.combine(day, datetime.strptime(slot, "%H:%M").time(), tzinfo=config.TZ)
            if log:
                status = "taken" if log["taken"] else "skipped"
            elif at > slot_dt + GRACE:
                status = "missed"
            elif at >= slot_dt - timedelta(hours=1):
                status = "due"
            else:
                status = "upcoming"
            out.append({"med_id": m["id"], "medication": m["name"], "dose": m["dose"], "slot": slot,
                        "instructions": m.get("instructions", ""), "status": status,
                        "logged_at": log["ts"] if log else None})
    return sorted(out, key=lambda d: d["slot"])


def log_dose(med_name: str, taken: bool = True, note: str = "", at: datetime | None = None) -> dict:
    at = at or now()
    med = find_med(med_name)
    if not med:
        known = ", ".join(m["name"] for m in list_meds()) or "none yet"
        return {"ok": False, "message": f"I couldn't find a medication called '{med_name}'. Known: {known}."}
    doses = [d for d in day_doses(at.date(), at) if d["med_id"] == med["id"]]
    open_doses = [d for d in doses if d["status"] not in ("taken", "skipped")]
    if not open_doses:
        return {"ok": True, "already_done": True, "medication": med["name"],
                "message": f"All of today's {med['name']} doses are already logged."}

    def dist(d):
        s = datetime.combine(at.date(), datetime.strptime(d["slot"], "%H:%M").time(), tzinfo=config.TZ)
        return abs((at - s).total_seconds())

    dose = min(open_doses, key=dist)
    get_store().put("medlog", {"med_id": med["id"], "med_name": med["name"], "slot": dose["slot"],
                               "date": at.date().isoformat(), "taken": taken, "note": note}, ts=iso(at))
    remaining = [d for d in day_doses(at.date(), at) if d["status"] in ("due", "upcoming")]
    nxt = remaining[0] if remaining else None
    return {"ok": True, "medication": med["name"], "dose": med["dose"], "slot": dose["slot"], "taken": taken,
            "next_dose": f"{nxt['medication']} at {nxt['slot']}" if nxt else None}


def adherence(days: int = 7, at: datetime | None = None) -> dict:
    at = at or now()
    taken = total = 0
    missed: list[dict] = []
    for i in range(days):
        day = (at - timedelta(days=i)).date()
        for d in day_doses(day, at):
            if d["status"] in ("upcoming", "due"):
                continue
            total += 1
            if d["status"] == "taken":
                taken += 1
            else:
                missed.append({"date": day.isoformat(), "medication": d["medication"], "slot": d["slot"]})
    return {"days": days, "taken": taken, "scheduled": total,
            "rate": round(taken / total, 2) if total else None, "missed": missed}


# ---------- wellbeing ----------

def red_flags(text: str) -> list[str]:
    t = text.lower()
    return sorted({label for pat, label in RED_FLAGS.items() if re.search(pat, t)})


def log_wellbeing(mood: int | None, symptoms: list[str], note: str = "", at: datetime | None = None) -> dict:
    at = at or now()
    symptoms = [s.strip().lower() for s in symptoms if s.strip()]
    flags = red_flags(" ".join(symptoms) + " " + note)
    rec = get_store().put("wellbeing", {"mood": mood, "symptoms": symptoms, "note": note, "red_flags": flags},
                          ts=iso(at))
    return rec


def wellbeing_history(days: int = 7, at: datetime | None = None) -> list[dict]:
    at = at or now()
    return get_store().list("wellbeing", since=iso(at - timedelta(days=days)))


# ---------- appointments ----------

def add_appointment(title: str, when: str, location: str = "", notes: str = "") -> dict:
    dt = datetime.fromisoformat(when)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=config.TZ)
    return get_store().put("appt", {"title": title, "when": iso(dt), "location": location, "notes": notes},
                           ts=iso(dt))


def upcoming_appointments(days_ahead: int = 14, at: datetime | None = None) -> list[dict]:
    at = at or now()
    end = at + timedelta(days=days_ahead)
    appts = [a for a in get_store().list("appt") if iso(at) <= a["when"] <= iso(end)]
    return sorted(appts, key=lambda a: a["when"])


# ---------- alerts ----------

def recent_alerts(hours: int = 24 * 7, at: datetime | None = None) -> list[dict]:
    at = at or now()
    return get_store().list("alert", since=iso(at - timedelta(hours=hours)))
