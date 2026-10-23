"""Family alerts: always stored (dashboard feed); also published to Amazon SNS when configured."""
from __future__ import annotations

import logging

from . import config
from .care import get_profile
from .store import get_store

log = logging.getLogger("carecircle.notify")

LEVELS = ("info", "nudge", "alert", "urgent")


def send_family_alert(message: str, level: str = "alert", reason: str = "", source: str = "agent",
                      evidence: list[str] | None = None) -> dict:
    level = level if level in LEVELS else "alert"
    name = get_profile().get("name", "your parent")
    rec = get_store().put("alert", {"level": level, "message": message, "reason": reason, "source": source,
                                    "evidence": evidence or [], "delivered": []})
    if config.SNS_TOPIC_ARN and level in ("alert", "urgent"):
        try:
            import boto3

            subject = f"[CareCircle] {'URGENT: ' if level == 'urgent' else ''}Update about {name}"[:100]
            boto3.client("sns", region_name=config.AWS_REGION).publish(
                TopicArn=config.SNS_TOPIC_ARN, Subject=subject, Message=f"{message}\n\n{reason}".strip())
            rec["delivered"] = ["sns"]
            get_store().put("alert", {k: v for k, v in rec.items() if k not in ("id", "kind", "ts")},
                            ts=rec["ts"], rid=rec["id"])
        except Exception as e:  # never let notification failures break the voice flow
            log.warning("SNS publish failed: %s", e)
    return rec
