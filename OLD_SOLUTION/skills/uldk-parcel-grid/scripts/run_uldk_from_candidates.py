#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import TextIO

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import probe_uldk_parcels as l2
from skills.shared.parcel_db import DEFAULT_ANALYSIS_DB_PATH, DEFAULT_CANON_DB_PATH, connect_workspace


DEFAULT_DB_PATH = DEFAULT_ANALYSIS_DB_PATH


def candidate_rows(
    connection: sqlite3.Connection,
    run_id: str,
    limit: int,
    exclude_categories: set[str] | None = None,
) -> list[dict]:
    exclude_categories = exclude_categories or set()
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        """
        SELECT cell_id, category, tag_key, tag_value, score, point_count,
               bbox_min_lat, bbox_min_lon, bbox_max_lat, bbox_max_lon
        FROM helper_osm_hotspot_mesh_cells
        WHERE run_id = ?
        ORDER BY score DESC, cell_id
        """,
        (run_id,),
    ).fetchall()
    candidates = []
    seen_bboxes = set()
    for row in rows:
        if row["category"] in exclude_categories:
            continue
        bbox = (row["bbox_min_lat"], row["bbox_min_lon"], row["bbox_max_lat"], row["bbox_max_lon"])
        if bbox in seen_bboxes:
            continue
        seen_bboxes.add(bbox)
        candidates.append(
            {
                "candidate_index": len(candidates) + 1,
                "cell_id": row["cell_id"],
                "category": row["category"],
                "tag_key": row["tag_key"],
                "tag_value": row["tag_value"],
                "score": row["score"],
                "point_count": row["point_count"],
                "bbox_min_lat": row["bbox_min_lat"],
                "bbox_min_lon": row["bbox_min_lon"],
                "bbox_max_lat": row["bbox_max_lat"],
                "bbox_max_lon": row["bbox_max_lon"],
            }
        )
        if len(candidates) >= limit:
            break
    return candidates


def candidate_bbox(candidate: dict) -> tuple[float, float, float, float]:
    return (
        candidate["bbox_min_lon"],
        candidate["bbox_min_lat"],
        candidate["bbox_max_lon"],
        candidate["bbox_max_lat"],
    )


def candidate_bbox_text(candidate: dict) -> str:
    min_lon, min_lat, max_lon, max_lat = candidate_bbox(candidate)
    return f"{min_lon},{min_lat},{max_lon},{max_lat}"


def run_layer2(
    connection: sqlite3.Connection,
    run_id: str,
    expected_commune: str | None,
    max_candidates: int,
    grid_size_m: float,
    max_requests_per_candidate: int,
    exclude_categories: set[str] | None = None,
    strict_commune: bool = False,
    progress_every: int = 0,
    progress_stream: TextIO | None = None,
    min_parcel_area_m2: float = 0.0,
    max_parcel_area_m2: float = 0.0,
    max_bbox_area_m2: float = 0.0,
    max_bbox_aspect_ratio: float = 0.0,
    stop_after_first_hit: bool = False,
    target_linked_parcels: int = 0,
) -> dict:
    l2.ensure_layer2_tables(connection)
    connection.execute("DELETE FROM helper_layer2_run_parcels WHERE run_id = ?", (run_id,))
    connection.commit()

    candidates = candidate_rows(connection, run_id, max_candidates, exclude_categories)
    filtered_commune = expected_commune if strict_commune else None
    tested = empty = out_of_scope = error_candidates = total_requests = total_inserted = 0
    total_rejected = total_skipped_rejected_polygon = 0
    rejected_reasons: dict[str, int] = {}
    selected_candidate = None
    linked_parcel_ids: list[str] = []
    first_error_examples: list[str] = []

    def emit_progress(event: str, candidate: dict | None = None, extra: dict | None = None) -> None:
        if not progress_stream:
            return
        payload = {
            "stage": "layer2",
            "event": event,
            "run_id": run_id,
            "tested_candidates": tested,
            "candidate_limit": max_candidates,
            "requests": total_requests,
            "linked_parcels": len(linked_parcel_ids),
            "rejected": total_rejected,
            "skipped_rejected_polygon": total_skipped_rejected_polygon,
        }
        if candidate:
            payload.update(
                {
                    "candidate_index": candidate["candidate_index"],
                    "cell_id": candidate["cell_id"],
                    "category": candidate["category"],
                    "score": candidate["score"],
                }
            )
        if extra:
            payload.update(extra)
        print("PROGRESS " + json.dumps(payload, ensure_ascii=True, sort_keys=True), file=progress_stream, flush=True)

    emit_progress("start")
    for candidate in candidates:
        tested += 1
        bbox = candidate_bbox(candidate)
        emit_progress("candidate_start", candidate)
        smoke = l2.run_probe(
            connection,
            bbox,
            grid_size_m,
            1,
            False,
            filtered_commune,
            min_parcel_area_m2=min_parcel_area_m2,
            max_parcel_area_m2=max_parcel_area_m2,
            max_bbox_area_m2=max_bbox_area_m2,
            max_bbox_aspect_ratio=max_bbox_aspect_ratio,
        )
        total_requests += smoke["requests"]
        total_inserted += smoke["inserted"]
        total_rejected += smoke.get("rejected", 0)
        total_skipped_rejected_polygon += smoke.get("skipped_rejected_polygon", 0)
        for reason, count in smoke.get("rejected_reasons", {}).items():
            rejected_reasons[reason] = rejected_reasons.get(reason, 0) + count
        first_error_examples.extend(smoke.get("error_examples", [])[: max(0, 3 - len(first_error_examples))])

        if smoke["errors"] and not (smoke["empty"] or smoke["out_of_scope"] or smoke["found_parcel_ids"]):
            error_candidates += 1
            emit_progress("candidate_error", candidate, {"errors": smoke["errors"]})
            continue
        if smoke["out_of_scope"]:
            out_of_scope += 1
            emit_progress("candidate_out_of_scope", candidate, {"out_of_scope": smoke["out_of_scope"]})
            continue
        if smoke["empty"] and not smoke["found_parcel_ids"]:
            empty += 1
            emit_progress("candidate_empty", candidate)
            continue

        summary = smoke
        if max_requests_per_candidate > 1:
            summary = l2.run_probe(
                connection,
                bbox,
                grid_size_m,
                max_requests_per_candidate,
                False,
                filtered_commune,
                progress_label=f"{run_id}:candidate_{candidate['candidate_index']}",
                progress_every=progress_every,
                progress_stream=progress_stream,
                min_parcel_area_m2=min_parcel_area_m2,
                max_parcel_area_m2=max_parcel_area_m2,
                max_bbox_area_m2=max_bbox_area_m2,
                max_bbox_aspect_ratio=max_bbox_aspect_ratio,
            )
            total_requests += max(0, summary["requests"] - smoke["requests"])
            total_inserted += summary["inserted"]
            total_rejected += summary.get("rejected", 0)
            total_skipped_rejected_polygon += summary.get("skipped_rejected_polygon", 0)
            for reason, count in summary.get("rejected_reasons", {}).items():
                rejected_reasons[reason] = rejected_reasons.get(reason, 0) + count
            first_error_examples.extend(summary.get("error_examples", [])[: max(0, 3 - len(first_error_examples))])

        for parcel_id in summary.get("found_parcel_ids", []):
            l2.link_layer2_run_parcel(
                connection,
                run_id,
                parcel_id,
                candidate["candidate_index"],
                candidate_bbox_text(candidate),
                expected_commune,
            )
            if parcel_id not in linked_parcel_ids:
                linked_parcel_ids.append(parcel_id)
        if linked_parcel_ids:
            selected_candidate = selected_candidate or candidate["candidate_index"]
            emit_progress("candidate_selected", candidate, {"linked_parcels": len(linked_parcel_ids)})
            if stop_after_first_hit:
                break
            if target_linked_parcels and len(linked_parcel_ids) >= target_linked_parcels:
                break

    status = "ok" if linked_parcel_ids else ("no_in_scope_parcels" if strict_commune else "no_parcels_found")
    if error_candidates and not linked_parcel_ids and not (empty or out_of_scope):
        status = "error"
    emit_progress("done", extra={"status": status})
    return {
        "run_id": run_id,
        "status": status,
        "tested_candidates": tested,
        "candidate_limit": max_candidates,
        "selected_candidate": selected_candidate,
        "linked_parcels": len(linked_parcel_ids),
        "inserted_parcels": total_inserted,
        "empty_candidates": empty,
        "out_of_scope_candidates": out_of_scope,
        "error_candidates": error_candidates,
        "requests": total_requests,
        "rejected_parcels": total_rejected,
        "rejected_reasons": rejected_reasons,
        "skipped_rejected_polygon_points": total_skipped_rejected_polygon,
        "min_parcel_area_m2": min_parcel_area_m2,
        "max_parcel_area_m2": max_parcel_area_m2,
        "max_bbox_area_m2": max_bbox_area_m2,
        "max_bbox_aspect_ratio": max_bbox_aspect_ratio,
        "stop_after_first_hit": stop_after_first_hit,
        "target_linked_parcels": target_linked_parcels,
        "expected_commune": expected_commune,
        "strict_commune": strict_commune,
        "filtered_commune": filtered_commune,
        "error_examples": first_error_examples[:3],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe ULDK parcels from SQLite candidate bboxes and print one compact summary.")
    parser.add_argument("--run-id", required=True, help="Run id used to read candidates and rebuild parcel links.")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--canon-db-path", default=str(DEFAULT_CANON_DB_PATH))
    parser.add_argument("--expected-commune")
    parser.add_argument("--strict-commune", action="store_true", help="Use --expected-commune as a hard filter.")
    parser.add_argument("--max-candidates", type=int, default=20)
    parser.add_argument("--grid-size-m", type=float, default=35.0)
    parser.add_argument("--max-requests-per-candidate", type=int, default=50)
    parser.add_argument("--exclude-categories", default="", help="Comma-separated categories to skip.")
    parser.add_argument("--progress-every", type=int, default=25, help="Emit compact progress every N grid points; 0 disables inner probe progress.")
    parser.add_argument("--min-parcel-area-m2", type=float, default=0.0, help="Cheap parcel gate: link only parcels at or above this area.")
    parser.add_argument("--max-parcel-area-m2", type=float, default=0.0, help="Cheap parcel gate: reject and skip polygons above this area.")
    parser.add_argument("--max-bbox-area-m2", type=float, default=0.0, help="Cheap parcel gate for huge bbox envelopes.")
    parser.add_argument("--max-bbox-aspect-ratio", type=float, default=0.0, help="Cheap parcel gate for long strips.")
    parser.add_argument("--stop-after-first-hit", action="store_true", help="Legacy narrow mode: stop after the first candidate with linked parcels.")
    parser.add_argument("--target-linked-parcels", type=int, default=0, help="Stop after this many linked parcels; 0 means process all candidates.")
    parser.add_argument("--analysis-id", help="Shared output folder id for multi-run analyses.")
    parser.add_argument("--summary-output")
    args = parser.parse_args()

    exclude_categories = {item.strip() for item in args.exclude_categories.split(",") if item.strip()}
    connection = connect_workspace(args.db_path, args.canon_db_path)
    try:
        summary = run_layer2(
            connection,
            args.run_id,
            args.expected_commune,
            args.max_candidates,
            args.grid_size_m,
            args.max_requests_per_candidate,
            exclude_categories,
            args.strict_commune,
            args.progress_every,
            sys.stderr,
            args.min_parcel_area_m2,
            args.max_parcel_area_m2,
            args.max_bbox_area_m2,
            args.max_bbox_aspect_ratio,
            args.stop_after_first_hit,
            args.target_linked_parcels,
        )
    finally:
        connection.close()

    if args.summary_output:
        output = Path(args.summary_output)
    else:
        analysis_id = args.analysis_id or args.run_id
        summary_name = "layer2_summary.json" if analysis_id == args.run_id else f"layer2_{args.run_id}_summary.json"
        output = Path("results") / f"analysis_{analysis_id}" / summary_name
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), **summary}, ensure_ascii=True, sort_keys=True))
    return 1 if summary["status"] == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
