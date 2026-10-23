"""Tiny record store with two backends: SQLite (local) and DynamoDB (AWS).

Every record has: user_id, kind, ts (ISO-8601, sortable), id, and a JSON payload.
Kinds used: profile, med, medlog, wellbeing, appt, alert, summary.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol

from . import config


def now() -> datetime:
    return datetime.now(config.TZ)


def iso(dt: datetime) -> str:
    return dt.astimezone(config.TZ).isoformat(timespec="seconds")


class Store(Protocol):
    def put(self, kind: str, data: dict, ts: str | None = None, rid: str | None = None) -> dict: ...
    def list(self, kind: str, since: str | None = None, limit: int = 500) -> list[dict]: ...
    def get(self, kind: str, rid: str) -> dict | None: ...
    def delete(self, kind: str, rid: str) -> None: ...
    def clear(self) -> None: ...


def _record(kind: str, data: dict, ts: str | None, rid: str | None) -> dict:
    return {"id": rid or uuid.uuid4().hex[:12], "kind": kind, "ts": ts or iso(now()), **data}


class SQLiteStore:
    def __init__(self, path: str, user_id: str):
        self.user_id = user_id
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS records (user_id TEXT, kind TEXT, id TEXT, ts TEXT, data TEXT,"
            " PRIMARY KEY (user_id, kind, id))"
        )
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_ts ON records(user_id, kind, ts)")
        self._db.commit()

    def put(self, kind, data, ts=None, rid=None):
        rec = _record(kind, data, ts, rid)
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO records VALUES (?,?,?,?,?)",
                (self.user_id, kind, rec["id"], rec["ts"], json.dumps(rec)),
            )
            self._db.commit()
        return rec

    def list(self, kind, since=None, limit=500):
        q = "SELECT data FROM records WHERE user_id=? AND kind=?"
        args: list[Any] = [self.user_id, kind]
        if since:
            q += " AND ts>=?"
            args.append(since)
        q += " ORDER BY ts DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._db.execute(q, args).fetchall()
        return [json.loads(r[0]) for r in rows]

    def get(self, kind, rid):
        with self._lock:
            row = self._db.execute(
                "SELECT data FROM records WHERE user_id=? AND kind=? AND id=?", (self.user_id, kind, rid)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def delete(self, kind, rid):
        with self._lock:
            self._db.execute("DELETE FROM records WHERE user_id=? AND kind=? AND id=?", (self.user_id, kind, rid))
            self._db.commit()

    def clear(self):
        with self._lock:
            self._db.execute("DELETE FROM records WHERE user_id=?", (self.user_id,))
            self._db.commit()


def _from_dynamo(v):
    if isinstance(v, Decimal):
        return int(v) if v == int(v) else float(v)
    if isinstance(v, list):
        return [_from_dynamo(x) for x in v]
    if isinstance(v, dict):
        return {k: _from_dynamo(x) for k, x in v.items()}
    return v


class DynamoStore:
    """Single-table design. pk = "<user>#<kind>", sk = "<ts>#<id>"."""

    def __init__(self, table: str, user_id: str, region: str):
        import boto3

        self.user_id = user_id
        self.table = boto3.resource("dynamodb", region_name=region).Table(table)

    def _pk(self, kind):
        return f"{self.user_id}#{kind}"

    def put(self, kind, data, ts=None, rid=None):
        rec = _record(kind, data, ts, rid)
        old = self.get(kind, rec["id"]) if rid else None
        if old and old["ts"] != rec["ts"]:
            self.delete(kind, rec["id"])
        item = json.loads(json.dumps(rec), parse_float=Decimal)
        item.update(pk=self._pk(kind), sk=f"{rec['ts']}#{rec['id']}")
        self.table.put_item(Item=item)
        return rec

    def list(self, kind, since=None, limit=500):
        from boto3.dynamodb.conditions import Key

        cond = Key("pk").eq(self._pk(kind))
        if since:
            cond = cond & Key("sk").gte(since)
        resp = self.table.query(KeyConditionExpression=cond, ScanIndexForward=False, Limit=limit)
        out = []
        for it in resp.get("Items", []):
            it.pop("pk", None)
            it.pop("sk", None)
            out.append(_from_dynamo(it))
        return out

    def get(self, kind, rid):
        return next((r for r in self.list(kind) if r["id"] == rid), None)

    def delete(self, kind, rid):
        rec = self.get(kind, rid)
        if rec:
            self.table.delete_item(Key={"pk": self._pk(kind), "sk": f"{rec['ts']}#{rid}"})

    def clear(self):
        for kind in ("profile", "med", "medlog", "wellbeing", "appt", "alert", "summary"):
            for r in self.list(kind):
                self.delete(kind, r["id"])


_store: Store | None = None


def get_store() -> Store:
    global _store
    if _store is None:
        if config.STORAGE == "dynamodb":
            _store = DynamoStore(config.DYNAMO_TABLE, config.USER_ID, config.AWS_REGION)
        else:
            _store = SQLiteStore(config.SQLITE_PATH, config.USER_ID)
    return _store
