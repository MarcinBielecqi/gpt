from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path
from typing import Callable

from . import bus
from .db import connect
from .skill_context import build_context, ensure_context, SkillContext

VALID_STATUSES = {
    "ok", "partial", "empty", "skipped", "no_input", "invalid_input",
    "schema_error", "external_error", "timeout", "error",
}


class SkillError(Exception):
    def __init__(self, code: str, message: str, *, status: str = "error", recoverable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.recoverable = recoverable


def add_standard_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--canon-db", required=True)
    parser.add_argument("--profile", required=True, choices=["quick", "normal", "deep"])
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--input-summary")
    parser.add_argument("--input-artifact")


def emit_progress(ctx: SkillContext, *, stage: str, event: str = "tick", **fields) -> None:
    payload = {"v": 1, "skill": ctx.skill, "run_id": ctx.run_id, "stage": stage, "event": event}
    payload.update({k: v for k, v in fields.items() if v is not None})
    print("PROGRESS " + json.dumps(payload, ensure_ascii=True, sort_keys=True), file=sys.stderr, flush=True)


def emit_error(payload: dict) -> None:
    print("ERROR " + json.dumps(payload, ensure_ascii=True, sort_keys=True), file=sys.stderr, flush=True)


def _record_error(ctx: SkillContext, *, code: str, message: str, detail: dict | None = None) -> None:
    con = connect(ctx.local_db)
    con.execute(
        "INSERT INTO skill_errors(code, message, detail_json, created_at) VALUES (?, ?, ?, datetime('now'))",
        (code, message, json.dumps(detail or {}, ensure_ascii=False, sort_keys=True)),
    )
    con.commit()
    con.close()




def _set_local_state(ctx: SkillContext, *, key: str, value: dict) -> None:
    con = connect(ctx.local_db)
    con.execute(
        """
        INSERT INTO skill_state(key, value_json, updated_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(key) DO UPDATE SET
            value_json=excluded.value_json,
            updated_at=datetime('now')
        """,
        (key, json.dumps(value, ensure_ascii=False, sort_keys=True)),
    )
    con.commit()
    con.close()


def _put_local_cache(ctx: SkillContext, *, cache_key: str, payload: dict) -> None:
    con = connect(ctx.local_db)
    con.execute(
        """
        INSERT INTO skill_cache(cache_key, payload_json, created_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(cache_key) DO UPDATE SET
            payload_json=excluded.payload_json,
            created_at=datetime('now')
        """,
        (cache_key, json.dumps(payload, ensure_ascii=False, sort_keys=True)),
    )
    con.commit()
    con.close()

def _finalize(ctx: SkillContext, result: dict) -> dict:
    result.setdefault("v", 1)
    result.setdefault("skill", ctx.skill)
    result.setdefault("run_id", ctx.run_id)
    result.setdefault("status", "ok")
    result.setdefault("counts", {})
    result.setdefault("artifacts", [])
    result.setdefault("warnings", [])
    if result["status"] not in VALID_STATUSES:
        raise SkillError("INVALID_STATUS", f"invalid status: {result['status']}", status="error")

    _set_local_state(ctx, key="last_result", value=result)
    _put_local_cache(ctx, cache_key="last_result", payload={"status": result.get("status"), "counts": result.get("counts", {}), "artifacts": result.get("artifacts", [])})

    summary_artifact = bus.publish_artifact(
        ctx.bus_db,
        run_id=ctx.run_id,
        producer_skill=ctx.skill,
        artifact_type="skill_summary",
        artifact_key=ctx.skill,
        payload=result,
    )
    if not any(a.get("type") == "skill_summary" and a.get("key") == ctx.skill for a in result["artifacts"]):
        result["artifacts"].append({"type": summary_artifact["type"], "key": summary_artifact["key"]})

    bus.set_skill_status(
        ctx.bus_db,
        run_id=ctx.run_id,
        skill=ctx.skill,
        status=result["status"],
        code=result.get("code"),
        message=result.get("message"),
        counts=result.get("counts", {}),
    )
    _set_local_state(ctx, key="run", value={"status": result["status"], "run_id": ctx.run_id, "skill": ctx.skill, "profile": ctx.profile_name})
    bus.touch_run(ctx.bus_db, run_id=ctx.run_id, status="running", profile=ctx.profile_name)
    return result


def run_skill(*, skill: str, build_parser: Callable[[], argparse.ArgumentParser], run: Callable[[argparse.Namespace, SkillContext], dict], schema_sql: str | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args()
    ctx = build_context(skill=skill, run_id=args.run_id, workspace=args.workspace, canon_db=args.canon_db, profile=args.profile)

    try:
        ensure_context(ctx, schema_sql=schema_sql)
        _set_local_state(ctx, key="run", value={"status": "running", "run_id": ctx.run_id, "skill": ctx.skill, "profile": ctx.profile_name})
        _put_local_cache(ctx, cache_key="run_args", payload={"dry_run": bool(args.dry_run), "profile": ctx.profile_name})
        bus.touch_run(ctx.bus_db, run_id=ctx.run_id, status="running", profile=ctx.profile_name)
        bus.set_skill_status(ctx.bus_db, run_id=ctx.run_id, skill=ctx.skill, status="running", counts={})
        emit_progress(ctx, stage="start", event="start", dry_run=bool(args.dry_run))
        result = run(args, ctx)
        result = _finalize(ctx, result)
        emit_progress(ctx, stage="done", event="done", status=result["status"])
        print(json.dumps(result, ensure_ascii=True, sort_keys=True), flush=True)
        return 0 if result["status"] not in {"invalid_input", "schema_error", "external_error", "timeout", "error"} else 1
    except SkillError as exc:
        _record_error(ctx, code=exc.code, message=exc.message, detail={"recoverable": exc.recoverable})
        result = {
            "v": 1,
            "skill": ctx.skill,
            "run_id": ctx.run_id,
            "status": exc.status,
            "code": exc.code,
            "message": exc.message,
            "recoverable": exc.recoverable,
            "counts": {},
            "artifacts": [],
            "warnings": [],
        }
        try:
            result = _finalize(ctx, result)
        except Exception:
            pass
        emit_error(result)
        print(json.dumps(result, ensure_ascii=True, sort_keys=True), flush=True)
        return 1
    except Exception as exc:
        detail = {"traceback": traceback.format_exc(limit=20)}
        _record_error(ctx, code="INTERNAL_ERROR", message=str(exc), detail=detail)
        result = {
            "v": 1,
            "skill": ctx.skill,
            "run_id": ctx.run_id,
            "status": "error",
            "code": "INTERNAL_ERROR",
            "message": str(exc)[:300],
            "counts": {},
            "artifacts": [],
            "warnings": [],
        }
        try:
            result = _finalize(ctx, result)
        except Exception:
            pass
        emit_error(result)
        print(json.dumps(result, ensure_ascii=True, sort_keys=True), flush=True)
        return 1
