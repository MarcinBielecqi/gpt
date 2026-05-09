from __future__ import annotations

import argparse
import html
from pathlib import Path

from skills.shared import bus

DESCRIPTION = "Create presentation metadata and optional HTML from project_bus.sqlite artifacts."


def add_domain_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--view", choices=["summary", "map", "candidates", "all"], default="summary")


def run(args: argparse.Namespace, ctx) -> dict:
    artifacts = bus.list_artifacts(ctx.bus_db, run_id=ctx.run_id)
    if args.dry_run:
        return {"status": "ok", "counts": {"available_artifacts": len(artifacts)}, "artifacts": [], "warnings": []}
    summary = [{"type": a["artifact_type"], "key": a["artifact_key"], "producer": a["producer_skill"], "created_at": a["created_at"]} for a in artifacts]
    payload = {"view": args.view, "artifacts": summary, "count": len(summary)}
    published = []
    meta = bus.publish_artifact(ctx.bus_db, run_id=ctx.run_id, producer_skill=ctx.skill, artifact_type="report_metadata", artifact_key=args.view, payload=payload)
    published.append({"type": meta["type"], "key": meta["key"]})

    if args.view in {"summary", "all", "candidates"}:
        html_path = ctx.skill_dir / f"{args.view}.html"
        rows = "".join(f"<tr><td>{html.escape(x['type'])}</td><td>{html.escape(x['key'])}</td><td>{html.escape(x['producer'])}</td></tr>" for x in summary)
        html_path.write_text(f"<html><body><h1>{html.escape(ctx.run_id)}</h1><table>{rows}</table></body></html>", encoding="utf-8")
        meta2 = bus.publish_artifact(ctx.bus_db, run_id=ctx.run_id, producer_skill=ctx.skill, artifact_type="html_report", artifact_key=args.view, payload={"path": str(html_path), "view": args.view})
        published.append({"type": meta2["type"], "key": meta2["key"]})
    return {"status": "ok", "counts": {"available_artifacts": len(artifacts), "published": len(published)}, "artifacts": published, "warnings": []}
