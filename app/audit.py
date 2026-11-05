"""Append-only, hash-chained audit log. Each entry's hash covers the previous hash, so edits are detectable."""
from __future__ import annotations

import hashlib
import json

from app.db import DB, now

GENESIS = "0" * 64


def _digest(prev: str, at: str, actor: str, actor_type: str, action: str, paper_id: str | None, payload: str) -> str:
    body = json.dumps([prev, at, actor, actor_type, action, paper_id, payload], separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


def record(db: DB, actor: str, action: str, paper_id: str | None = None, payload: dict | None = None,
           actor_type: str = "human") -> None:
    with db.lock:
        last = db.one("SELECT hash FROM audit ORDER BY seq DESC LIMIT 1")
        prev = last["hash"] if last else GENESIS
        at = now()
        body = json.dumps(payload or {}, sort_keys=True, default=str)
        h = _digest(prev, at, actor, actor_type, action, paper_id, body)
        db.execute("INSERT INTO audit (at, actor, actor_type, action, paper_id, payload, prev_hash, hash) "
                   "VALUES (?,?,?,?,?,?,?,?)", (at, actor, actor_type, action, paper_id, body, prev, h))


def verify_chain(db: DB) -> bool:
    prev = GENESIS
    for r in db.all("SELECT * FROM audit ORDER BY seq"):
        if r["prev_hash"] != prev or r["hash"] != _digest(prev, r["at"], r["actor"], r["actor_type"],
                                                          r["action"], r["paper_id"], r["payload"]):
            return False
        prev = r["hash"]
    return True


def entries(db: DB, paper_id: str | None = None) -> list[dict]:
    if paper_id:
        rows = db.all("SELECT * FROM audit WHERE paper_id=? ORDER BY seq", (paper_id,))
    else:
        rows = db.all("SELECT * FROM audit ORDER BY seq")
    for r in rows:
        r["payload"] = json.loads(r["payload"] or "{}")
    return rows
