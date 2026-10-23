"""Demo household: Rose, 74, lives alone. Her son and daughter live in other cities.

The last week contains a realistic, slightly worrying pattern for the demo:
evening doses get missed and she has mentioned dizziness twice.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from . import care, config
from .store import get_store, iso, now

MEDS = [
    ("Amlodipine", "5 mg, 1 tablet", ["08:00"], "after breakfast", "blood pressure"),
    ("Metformin", "500 mg, 1 tablet", ["08:00", "20:00"], "with food", "blood sugar (diabetes)"),
    ("Atorvastatin", "10 mg, 1 tablet", ["21:00"], "at night", "cholesterol"),
]

# (days_ago, med, slot) doses that were NOT taken
MISSED = {(0, "Amlodipine", "08:00"), (1, "Metformin", "20:00"), (1, "Amlodipine", "08:00"), (3, "Metformin", "20:00"),
          (4, "Atorvastatin", "21:00"), (5, "Metformin", "20:00")}

WELLBEING = [
    (6, "10:15", 4, [], "Feeling good, walked to the park with Mrs. Pereira."),
    (5, "09:40", 4, [], "Fine. A bit tired after the market."),
    (3, "08:30", 3, ["dizzy"], "A little dizzy when I stood up this morning."),
    (2, "11:00", 3, [], "Okay. Watched a film, missed the kids."),
    (1, "09:05", 2, ["dizzy", "tired"], "Dizzy again this morning, had to sit down for a while."),
]


def _at(days_ago: int, hhmm: str) -> datetime:
    d = (now() - timedelta(days=days_ago)).date()
    return datetime.combine(d, datetime.strptime(hhmm, "%H:%M").time(), tzinfo=config.TZ)


def seed() -> dict:
    s = get_store()
    s.clear()
    s.put("profile", {"name": "Rose", "age": 74, "lives": "alone",
                      "family": [{"name": "Sajid", "relation": "son"}, {"name": "Ayesha", "relation": "daughter"}]},
          rid="profile")
    meds = {name: care.add_med(name, dose, times, instr, purpose) for name, dose, times, instr, purpose in MEDS}

    current = now()
    for days_ago in range(6, -1, -1):
        for name, m in meds.items():
            for slot in m["times"]:
                at = _at(days_ago, slot) + timedelta(minutes=12)
                if at > current - timedelta(minutes=30) or (days_ago, name, slot) in MISSED:
                    continue
                s.put("medlog", {"med_id": m["id"], "med_name": name, "slot": slot,
                                 "date": at.date().isoformat(), "taken": True, "note": ""}, ts=iso(at))

    for days_ago, hhmm, mood, syms, note in WELLBEING:
        care.log_wellbeing(mood, syms, note, at=_at(days_ago, hhmm))

    care.add_appointment("Cardiology follow-up with Dr. Mehta", iso(_at(-3, "10:30")), "City Heart Clinic, Room 4")
    care.add_appointment("Fasting blood test", iso(_at(-8, "08:00")), "Apollo Diagnostics")
    return {"ok": True, "seeded": "Rose (74), 3 medications, 7 days of history"}


if __name__ == "__main__":
    print(seed())
