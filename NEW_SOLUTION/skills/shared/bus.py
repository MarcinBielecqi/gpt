from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .db import connect, ensure_bus_db


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(payload_json: str) -> str:
    return hashlib.sha256(payload_json.encode("utf-8")).hexdigest()


def touch_run(bus_db: str | Path, *, run_id: str, status: str, profile: str, payload: dict | None = None) -> None:
    ensure_bus_db(bus_db)
    payload_json = _json(payload or {})
    con = connect(bus_db)
    con.execute(
        """
        INSERT INTO bus_runs(run_id, status, profile, payload_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))
        ON CONFLICT(run_id) DO UPDATE SET
            status=excluded.status,
            profile=excluded.profile,
            payload_json=excluded.payload_json,
            updated_at=datetime('now')
        """,
        (run_id, status, profile, payload_json),
    )
    con.commit()
    con.close()


def set_skill_status(
    bus_db: str | Path,
    *,
    run_id: str,
    skill: str,
    status: str,
    code: str | None = None,
    message: str | None = None,
    counts: dict | None = None,
) -> None:
    con = connect(bus_db)
    con.execute(
        """
        INSERT INTO bus_skill_status(run_id, skill, status, code, message, counts_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(run_id, skill) DO UPDATE SET
            status=excluded.status,
            code=excluded.code,
            message=excluded.message,
            counts_json=excluded.counts_json,
            updated_at=datetime('now')
        """,
        (run_id, skill, status, code, message, _json(counts or {})),
    )
    con.commit()
    con.close()


def publish_artifact(
    bus_db: str | Path,
    *,
    run_id: str,
    producer_skill: str,
    artifact_type: str,
    artifact_key: str = "default",
    payload: dict | list | None = None,
    schema_version: int = 1,
) -> dict:
    """Publish a bus artifact as a JSON string.

    The bus stores the latest value for a logical artifact selector:
    (run_id, producer_skill, artifact_type, artifact_key). Re-publishing the same
    selector updates the payload instead of creating duplicate rows.
    """
    payload_json = _json(payload or {})
    content_hash = _hash(payload_json)
    con = connect(bus_db)
    cols = {row["name"] for row in con.execute("PRAGMA table_info(bus_artifacts)").fetchall()}
    if "updated_at" not in cols:
        con.execute("ALTER TABLE bus_artifacts ADD COLUMN updated_at TEXT")
        con.execute("UPDATE bus_artifacts SET updated_at = COALESCE(created_at, datetime('now')) WHERE updated_at IS NULL")

    row = con.execute(
        """
        SELECT id FROM bus_artifacts
        WHERE run_id = ? AND producer_skill = ? AND artifact_type = ? AND artifact_key = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (run_id, producer_skill, artifact_type, artifact_key),
    ).fetchone()
    if row is None:
        con.execute(
            """
            INSERT INTO bus_artifacts(
                run_id, producer_skill, artifact_type, artifact_key,
                payload_json, schema_version, content_hash, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
            """,
            (run_id, producer_skill, artifact_type, artifact_key, payload_json, schema_version, content_hash),
        )
        row = con.execute("SELECT last_insert_rowid() AS id").fetchone()
    else:
        con.execute(
            """
            UPDATE bus_artifacts
            SET payload_json = ?, schema_version = ?, content_hash = ?, updated_at = datetime('now')
            WHERE id = ?
            """,
            (payload_json, schema_version, content_hash, row["id"]),
        )
    con.commit()
    con.close()
    return {
        "id": row["id"],
        "type": artifact_type,
        "key": artifact_key,
        "schema_version": schema_version,
        "content_hash": content_hash,
    }


def parse_selector(selector: str | None, default_type: str | None = None) -> tuple[str, str]:
    if selector:
        if ":" in selector:
            artifact_type, artifact_key = selector.split(":", 1)
            return artifact_type.strip(), artifact_key.strip() or "default"
        if default_type:
            return default_type, selector.strip() or "default"
        return selector.strip(), "default"
    if not default_type:
        raise ValueError("missing artifact selector")
    return default_type, "default"


def get_latest_artifact(
    bus_db: str | Path,
    *,
    run_id: str,
    artifact_type: str,
    artifact_key: str = "default",
) -> dict | None:
    con = connect(bus_db)
    row = con.execute(
        """
        SELECT id, run_id, producer_skill, artifact_type, artifact_key, payload_json,
               schema_version, content_hash, created_at, updated_at
        FROM bus_artifacts
        WHERE run_id = ? AND artifact_type = ? AND artifact_key = ?
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        (run_id, artifact_type, artifact_key),
    ).fetchone()
    con.close()
    if row is None:
        return None
    item = dict(row)
    item["payload"] = json.loads(item.pop("payload_json"))
    return item


def list_artifacts(bus_db: str | Path, *, run_id: str) -> list[dict]:
    con = connect(bus_db)
    rows = con.execute(
        """
        SELECT id, run_id, producer_skill, artifact_type, artifact_key, payload_json,
               schema_version, content_hash, created_at, updated_at
        FROM bus_artifacts
        WHERE run_id = ?
        ORDER BY id
        """,
        (run_id,),
    ).fetchall()
    con.close()
    out = []
    for row in rows:
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json"))
        out.append(item)
    return out
