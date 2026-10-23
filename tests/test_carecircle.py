import os
import tempfile

os.environ["CARECIRCLE_LLM"] = "offline"
os.environ["CARECIRCLE_STORAGE"] = "sqlite"
os.environ["CARECIRCLE_SQLITE_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")

import anyio  # noqa: E402
import pytest  # noqa: E402

from carecircle import care, safety  # noqa: E402
from carecircle.risk_agent import run_risk_check  # noqa: E402
from carecircle.seed import seed  # noqa: E402


@pytest.fixture(autouse=True)
def fresh():
    seed()


def test_fuzzy_med_match():
    assert care.find_med("my blood pressure tablet")["name"] == "Amlodipine"
    assert care.find_med("metformin")["name"] == "Metformin"
    assert care.find_med("aspirin") is None


def test_log_dose_marks_taken():
    r = care.log_dose("blood pressure tablet")
    assert r["ok"] and r["medication"] == "Amlodipine"


def test_red_flags():
    assert care.red_flags("I fell in the bathroom") == ["a fall"]
    assert care.red_flags("I fell asleep watching TV") == []
    assert care.red_flags("my blood pressure is fine") == []
    assert "chest pain" in care.red_flags("my chest hurts")


def test_red_flag_escalates_without_model():
    care.log_wellbeing(2, ["pain"], "I fell and can't get up")
    r = run_risk_check("test")
    assert r["level"] == "urgent" and r["family_alerted"]
    assert r["decision"]["decided_by"] == "safety-rule"


def test_dizziness_plus_missed_meds_alerts_family():
    care.log_wellbeing(2, ["dizzy"], "dizzy again")
    r = run_risk_check("test")
    assert r["level"] == "alert" and r["family_alerted"]
    assert any(f["type"] == "combined" for f in r["findings"])


def test_alert_dedup_within_12h():
    care.log_wellbeing(2, ["dizzy"], "dizzy again")
    assert run_risk_check("a")["family_alerted"]
    assert not run_risk_check("b")["family_alerted"]


def test_safety_blocks_dose_advice():
    out = safety.review("can I take two metformin?", "Sure, just double your dose tonight.")
    assert out["verdict"]["rewritten"] and "doctor" in out["reply"]


def test_safety_adds_emergency_line():
    out = safety.review("I have chest pain", "I've noted that.")
    assert "emergency" in out["reply"].lower()


def test_mcp_tools_in_process():
    from mcp import Client

    from carecircle.server import mcp

    async def go():
        async with Client(mcp) as c:
            names = {t.name for t in (await c.list_tools()).tools}
            assert {"get_today_plan", "log_medication", "log_wellbeing", "alert_family",
                    "weekly_summary", "import_prescription", "family_status"} <= names
            res = await c.call_tool("log_wellbeing", {"note": "I feel dizzy again", "symptoms": ["dizzy"], "mood": 2})
            assert not res.is_error
            assert res.structured_content["family_alerted"] is True

    anyio.run(go)
