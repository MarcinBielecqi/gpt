from __future__ import annotations

import argparse
import html
import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from skills.shared import bus

DESCRIPTION = "Create result presentation metadata and serve a temporary localhost parcel map."


def add_domain_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--view", choices=["summary", "map", "candidates", "all"], default="summary")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0, help="0 selects a free local port.")
    parser.add_argument("--ttl-seconds", type=int, default=300, help="Local map server lifetime. Default: 300s = 5min.")
    parser.add_argument("--map-limit", type=int, default=500, help="Default parcel render limit.")
    parser.add_argument("--analysis-dir", help="Directory with analysis JSON files. Default: <canon-db parent>/analysis, usually data/analysis.")
    parser.add_argument("--serve-timeout-seconds", type=float, default=15.0)
    parser.add_argument("--no-serve", action="store_true", help="Publish map metadata without starting localhost.")


def _free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return int(s.getsockname()[1])


def _absolute(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def _read_http_error(exc: urllib.error.HTTPError) -> str:
    try:
        return exc.read().decode("utf-8", errors="replace")[:1000]
    except Exception:
        return str(exc)


def _wait_endpoint(url: str, timeout_s: float) -> dict[str, Any] | None:
    deadline = time.time() + max(0.5, timeout_s)
    last: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.5) as r:
                data = r.read().decode("utf-8", errors="replace")
                if r.status == 200:
                    return json.loads(data) if data else None
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"{url} returned HTTP {exc.code}: {_read_http_error(exc)}") from exc
        except Exception as exc:
            last = exc
        time.sleep(0.2)
    raise RuntimeError(f"{url} did not become ready: {last}")


def _start_map(args: argparse.Namespace, ctx) -> dict[str, Any]:
    ttl = max(1, int(args.ttl_seconds))
    limit = max(5, int(args.map_limit))
    host = args.host
    port = int(args.port) if int(args.port) > 0 else _free_port(host)
    server_py = Path(__file__).resolve().parent / "map_server.py"
    canon_db = _absolute(ctx.canon_db)
    bus_db = _absolute(ctx.bus_db)
    analysis_dir = _absolute(args.analysis_dir) if args.analysis_dir else (canon_db.parent / "analysis").resolve()
    if not server_py.exists():
        raise RuntimeError(f"map server script not found: {server_py}")
    if not canon_db.exists():
        raise RuntimeError(f"canon db not found: {canon_db}")
    cmd = [sys.executable, str(server_py), "--canon-db", str(canon_db), "--bus-db", str(bus_db), "--run-id", ctx.run_id, "--host", host, "--port", str(port), "--ttl-seconds", str(ttl), "--default-limit", str(limit), "--analysis-dir", str(analysis_dir)]
    proc = subprocess.Popen(cmd, cwd=str(ctx.skill_dir), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, text=True)
    ts = int(time.time())
    public_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    base_url = f"http://{public_host}:{port}"
    health_url = f"{base_url}/api/health"
    manifest_url = f"{base_url}/api/manifest"
    try:
        _wait_endpoint(health_url, float(args.serve_timeout_seconds))
        manifest = _wait_endpoint(manifest_url, float(args.serve_timeout_seconds))
    except Exception:
        proc.terminate()
        try:
            _out, err = proc.communicate(timeout=2)
        except Exception:
            err = ""
        details = f"\nserver stderr: {err.strip()}" if err else ""
        raise RuntimeError(f"local map server failed to start correctly{details}") from None
    return {"local_url": f"{base_url}/map.html?run_id={ctx.run_id}&ts={ts}", "health_url": health_url, "manifest_url": manifest_url, "host": public_host, "bind_host": host, "port": port, "pid": proc.pid, "ttl_seconds": ttl, "expires_at_epoch": time.time() + ttl, "server": "result-presentation/src/map_server.py", "canon_db": str(canon_db), "bus_db": str(bus_db), "analysis_dir": str(analysis_dir), "parcel_count": int((manifest or {}).get("parcel_count") or 0), "rcn_count": int((manifest or {}).get("rcn_count") or 0)}


def _html_report(ctx, view: str, summary: list[dict[str, Any]]) -> dict[str, str]:
    rows = "".join(f"<tr><td>{html.escape(x['type'])}</td><td>{html.escape(x['key'])}</td><td>{html.escape(x['producer'])}</td></tr>" for x in summary)
    path = ctx.skill_dir / f"{view}.html"
    path.write_text("<!doctype html><html><head><meta charset='utf-8'><title>Result presentation</title></head>" f"<body><h1>{html.escape(ctx.run_id)}</h1><table>{rows}</table></body></html>", encoding="utf-8")
    return {"path": str(path), "view": view}


def run(args: argparse.Namespace, ctx) -> dict[str, Any]:
    artifacts = bus.list_artifacts(ctx.bus_db, run_id=ctx.run_id)
    if args.dry_run:
        return {"status": "ok", "counts": {"available_artifacts": len(artifacts)}, "artifacts": [], "warnings": []}
    summary = [{"type": a["artifact_type"], "key": a["artifact_key"], "producer": a["producer_skill"], "created_at": a["created_at"]} for a in artifacts]
    published: list[dict[str, str]] = []
    meta_payload: dict[str, Any] = {"view": args.view, "artifacts": summary, "count": len(summary)}
    if args.view in {"map", "all"}:
        if args.no_serve:
            meta_payload["map_server"] = {"enabled": False}
        else:
            map_meta = _start_map(args, ctx)
            meta_payload["map_server"] = map_meta
            item = bus.publish_artifact(ctx.bus_db, run_id=ctx.run_id, producer_skill=ctx.skill, artifact_type="local_map_server", artifact_key="default", payload=map_meta)
            published.append({"type": item["type"], "key": item["key"]})
    item = bus.publish_artifact(ctx.bus_db, run_id=ctx.run_id, producer_skill=ctx.skill, artifact_type="report_metadata", artifact_key=args.view, payload=meta_payload)
    published.append({"type": item["type"], "key": item["key"]})
    if args.view in {"summary", "candidates", "all"}:
        report = _html_report(ctx, args.view, summary)
        item = bus.publish_artifact(ctx.bus_db, run_id=ctx.run_id, producer_skill=ctx.skill, artifact_type="html_report", artifact_key=args.view, payload=report)
        published.append({"type": item["type"], "key": item["key"]})
    result: dict[str, Any] = {"status": "ok", "counts": {"available_artifacts": len(artifacts), "published": len(published)}, "artifacts": published, "warnings": []}
    if meta_payload.get("map_server", {}).get("local_url"):
        result["map_server"] = meta_payload["map_server"]
        result["message"] = f"Local map: {meta_payload['map_server']['local_url']} (expires in {meta_payload['map_server']['ttl_seconds']}s)"
    return result
