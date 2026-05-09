#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
FETCH_SCRIPT = ROOT_DIR / "skills" / "polish-parcel-wfs" / "scripts" / "fetch_polish_parcel_wfs.py"


# Dolnoslaskie powiaty (ziemskie + miasta na prawach powiatu).
DOLNOSLASKIE_COUNTIES: list[tuple[str, str]] = [
    ("0201", "boleslawiecki"),
    ("0202", "dzierzoniowski"),
    ("0203", "glogowski"),
    ("0204", "gorowski"),
    ("0205", "jaworski"),
    ("0206", "karkonoski"),
    ("0207", "kamiennogorski"),
    ("0208", "klodzki"),
    ("0209", "legnicki"),
    ("0210", "lubanski"),
    ("0211", "lubinski"),
    ("0212", "lwowecki"),
    ("0213", "milicki"),
    ("0214", "olesnicki"),
    ("0215", "olawski"),
    ("0216", "polkowicki"),
    ("0217", "strzelinski"),
    ("0218", "sredzki"),
    ("0219", "swidnicki"),
    ("0220", "trzebnicki"),
    ("0221", "walbrzyski"),
    ("0222", "wolowski"),
    ("0223", "wroclawski"),
    ("0224", "zabkowicki"),
    ("0225", "zgorzelecki"),
    ("0226", "zlotoryjski"),
    ("0261", "jelenia_gora_city"),
    ("0262", "legnica_city"),
    ("0264", "wroclaw_city"),
    ("0265", "walbrzych_city"),
]


# W praktyce mamy potwierdzony stabilny endpoint WebEWID dla 0221.
ENDPOINT_OVERRIDES: dict[str, str] = {
    "0221": "https://walbrzyski-wms.webewid.pl/iip/ows",
}


@dataclass
class ProbeResult:
    ok: bool
    endpoint: str
    sample_parsed: int
    note: str


@dataclass
class FetchPageResult:
    ok: bool
    fetched: int
    inserted_or_updated: int
    skipped_unchanged: int
    errors: int
    error_examples: list[str]
    raw: dict


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def geoportal_endpoint(county_code: str) -> str:
    return f"https://mapy.geoportal.gov.pl/wss/ext/PowiatoweBazyEwidencjiGruntow/{county_code}"


def run_fetch_script(args: list[str], timeout_s: int) -> tuple[int, str, str]:
    cmd = [sys.executable, str(FETCH_SCRIPT)] + args
    completed = subprocess.run(
        cmd,
        cwd=str(ROOT_DIR),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_s,
    )
    return completed.returncode, completed.stdout, completed.stderr


def parse_last_json(stdout_text: str) -> dict | None:
    lines = [line.strip() for line in stdout_text.splitlines() if line.strip()]
    for line in reversed(lines):
        if not line.startswith("{"):
            continue
        try:
            data = json.loads(line)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            continue
    return None


def probe_endpoint(endpoint: str, county_code: str, typename: str, timeout_s: int) -> ProbeResult:
    rc, out, err = run_fetch_script(
        [
            "--endpoint-url",
            endpoint,
            "--county-code",
            county_code,
            "--mode",
            "probe",
            "--typename",
            typename,
            "--count",
            "1",
            "--max-pages",
            "1",
            "--timeout",
            str(timeout_s),
        ],
        timeout_s=timeout_s + 10,
    )
    payload = parse_last_json(out)
    if payload is None:
        note = (err or out).strip().replace("\n", " ")[:220]
        return ProbeResult(ok=False, endpoint=endpoint, sample_parsed=0, note=f"no-json rc={rc} {note}")
    sample = payload.get("sample", {}) if isinstance(payload, dict) else {}
    parsed = int(sample.get("sample_parsed") or 0)
    sample_error = str(sample.get("sample_error") or "").strip()
    ok = rc == 0 and parsed > 0 and not sample_error
    if ok:
        return ProbeResult(ok=True, endpoint=endpoint, sample_parsed=parsed, note="ok")
    if sample_error:
        return ProbeResult(ok=False, endpoint=endpoint, sample_parsed=parsed, note=sample_error[:220])
    return ProbeResult(ok=False, endpoint=endpoint, sample_parsed=parsed, note=f"probe rc={rc} parsed={parsed}")


def fetch_one_page(
    endpoint: str,
    county_code: str,
    typename: str,
    page_size: int,
    startindex: int,
    timeout_s: int,
    db_path: Path,
    canon_db_path: Path,
) -> FetchPageResult:
    rc, out, err = run_fetch_script(
        [
            "--endpoint-url",
            endpoint,
            "--county-code",
            county_code,
            "--mode",
            "fetch",
            "--typename",
            typename,
            "--count",
            str(page_size),
            "--startindex",
            str(startindex),
            "--max-pages",
            "1",
            "--timeout",
            str(timeout_s),
            "--db-path",
            str(db_path),
            "--canon-db-path",
            str(canon_db_path),
        ],
        timeout_s=timeout_s + 20,
    )
    payload = parse_last_json(out)
    if payload is None:
        snippet = (err or out).strip().replace("\n", " ")[:220]
        return FetchPageResult(
            ok=False,
            fetched=0,
            inserted_or_updated=0,
            skipped_unchanged=0,
            errors=1,
            error_examples=[f"no-json rc={rc} {snippet}"],
            raw={},
        )
    fetched = int(payload.get("fetched_features") or 0)
    inserted_or_updated = int(payload.get("inserted_or_updated") or 0)
    skipped_unchanged = int(payload.get("skipped_unchanged") or 0)
    errors = int(payload.get("errors") or 0)
    error_examples = payload.get("error_examples") or []
    ok = rc == 0 and errors == 0
    return FetchPageResult(
        ok=ok,
        fetched=fetched,
        inserted_or_updated=inserted_or_updated,
        skipped_unchanged=skipped_unchanged,
        errors=errors,
        error_examples=[str(x) for x in error_examples][:3],
        raw=payload,
    )


def render_bar(done: int, total: int, width: int = 26) -> str:
    if total <= 0:
        return "[" + ("-" * width) + "]"
    ratio = min(1.0, max(0.0, done / total))
    filled = int(round(ratio * width))
    return "[" + ("#" * filled) + ("-" * (width - filled)) + "]"


def short_endpoint(value: str, max_len: int = 58) -> str:
    if len(value) <= max_len:
        return value
    return value[: max_len - 3] + "..."


def print_status(
    counties_done: int,
    counties_total: int,
    county_code: str,
    county_name: str,
    phase: str,
    page: int,
    records: int,
    endpoint: str,
) -> None:
    bar = render_bar(counties_done, counties_total)
    msg = (
        f"\r{bar} {counties_done}/{counties_total} "
        f"| {county_code} {county_name:<18} "
        f"| {phase:<18} "
        f"| pages={page:<5} records={records:<8} "
        f"| {short_endpoint(endpoint)}"
    )
    print(msg, end="", flush=True)


def finalize_status_line() -> None:
    print("", flush=True)


def choose_counties(county_codes_arg: str | None) -> list[tuple[str, str]]:
    if not county_codes_arg:
        return DOLNOSLASKIE_COUNTIES.copy()
    wanted = {code.strip() for code in county_codes_arg.split(",") if code.strip()}
    out: list[tuple[str, str]] = []
    known = {code: name for code, name in DOLNOSLASKIE_COUNTIES}
    for code in sorted(wanted):
        out.append((code, known.get(code, "custom")))
    return out


def candidate_endpoints(county_code: str, extra_templates: list[str]) -> list[str]:
    values: list[str] = []
    if county_code in ENDPOINT_OVERRIDES:
        values.append(ENDPOINT_OVERRIDES[county_code])
    values.append(geoportal_endpoint(county_code))
    for tpl in extra_templates:
        values.append(tpl.replace("{code}", county_code))
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def save_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def append_jsonl(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Automatyczny dump parceli WFS dla powiatow. "
            "Domyslnie leci po wszystkich powiatach Dolnoslaskiego."
        )
    )
    parser.add_argument("--county-codes", help="Opcjonalnie: lista kodow powiatow CSV, np. 0208,0221,0224")
    parser.add_argument("--typename", default="ms:dzialki")
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--probe-timeout", type=int, default=20)
    parser.add_argument("--fetch-timeout", type=int, default=90)
    parser.add_argument("--max-pages-per-county", type=int, default=0, help="0 = bez limitu")
    parser.add_argument("--sleep-between-pages", type=float, default=0.0)
    parser.add_argument("--analysis-id", default="WORKBENCH")
    parser.add_argument("--db-path", default=str(ROOT_DIR / "data" / "analysis_workspace.sqlite"))
    parser.add_argument("--canon-db-path", default=str(ROOT_DIR / "data" / "canon_workspace.sqlite"))
    parser.add_argument(
        "--extra-endpoint-template",
        action="append",
        default=[],
        help="Dodatkowy template endpointu, np. https://{code}-wms.webewid.pl/iip/ows",
    )
    args = parser.parse_args()

    counties = choose_counties(args.county_codes)
    counties_total = len(counties)
    results_dir = ROOT_DIR / "results" / f"analysis_{args.analysis_id}"
    run_id = datetime.now().strftime("WFS_BULK_%Y%m%d_%H%M%S")
    summary_path = results_dir / f"wfs_bulk_summary_{run_id}.json"
    events_path = results_dir / f"wfs_bulk_events_{run_id}.jsonl"

    global_summary: dict = {
        "run_id": run_id,
        "started_at": now_utc_iso(),
        "counties_total": counties_total,
        "page_size": args.page_size,
        "probe_timeout": args.probe_timeout,
        "fetch_timeout": args.fetch_timeout,
        "counties": [],
    }

    counties_done = 0
    for county_code, county_name in counties:
        county_event = {
            "time": now_utc_iso(),
            "county_code": county_code,
            "county_name": county_name,
            "stage": "start_county",
        }
        append_jsonl(events_path, county_event)

        endpoints = candidate_endpoints(county_code, args.extra_endpoint_template)
        working_endpoint = None
        probe_note = "no-endpoint"

        for endpoint in endpoints:
            print_status(
                counties_done,
                counties_total,
                county_code,
                county_name,
                "probe",
                0,
                0,
                endpoint,
            )
            probe = probe_endpoint(endpoint, county_code, args.typename, args.probe_timeout)
            append_jsonl(
                events_path,
                {
                    "time": now_utc_iso(),
                    "county_code": county_code,
                    "county_name": county_name,
                    "stage": "probe",
                    "endpoint": endpoint,
                    "ok": probe.ok,
                    "sample_parsed": probe.sample_parsed,
                    "note": probe.note,
                },
            )
            if probe.ok:
                working_endpoint = endpoint
                probe_note = probe.note
                break
            probe_note = probe.note

        if working_endpoint is None:
            counties_done += 1
            print_status(
                counties_done,
                counties_total,
                county_code,
                county_name,
                "skip:no_endpoint",
                0,
                0,
                endpoints[0] if endpoints else "-",
            )
            finalize_status_line()
            global_summary["counties"].append(
                {
                    "county_code": county_code,
                    "county_name": county_name,
                    "status": "skipped_no_endpoint",
                    "probe_note": probe_note,
                    "endpoints_tried": endpoints,
                    "pages": 0,
                    "fetched_records": 0,
                    "inserted_or_updated": 0,
                    "skipped_unchanged": 0,
                    "errors": 1,
                }
            )
            continue

        page = 0
        startindex = 0
        fetched_total = 0
        upsert_total = 0
        unchanged_total = 0
        errors_total = 0
        final_status = "ok"
        stop_reason = "completed_short_page"

        while True:
            page += 1
            print_status(
                counties_done,
                counties_total,
                county_code,
                county_name,
                "fetch",
                page,
                fetched_total,
                working_endpoint,
            )
            page_result = fetch_one_page(
                endpoint=working_endpoint,
                county_code=county_code,
                typename=args.typename,
                page_size=args.page_size,
                startindex=startindex,
                timeout_s=args.fetch_timeout,
                db_path=Path(args.db_path),
                canon_db_path=Path(args.canon_db_path),
            )
            fetched_total += page_result.fetched
            upsert_total += page_result.inserted_or_updated
            unchanged_total += page_result.skipped_unchanged
            errors_total += page_result.errors

            append_jsonl(
                events_path,
                {
                    "time": now_utc_iso(),
                    "county_code": county_code,
                    "county_name": county_name,
                    "stage": "fetch_page",
                    "page": page,
                    "startindex": startindex,
                    "fetched": page_result.fetched,
                    "inserted_or_updated": page_result.inserted_or_updated,
                    "skipped_unchanged": page_result.skipped_unchanged,
                    "errors": page_result.errors,
                    "error_examples": page_result.error_examples,
                },
            )

            if not page_result.ok:
                final_status = "error"
                stop_reason = "fetch_error"
                break
            if page_result.fetched < args.page_size:
                stop_reason = "short_page"
                break
            if args.max_pages_per_county > 0 and page >= args.max_pages_per_county:
                stop_reason = "max_pages_per_county"
                break

            startindex += args.page_size
            if args.sleep_between_pages > 0:
                time.sleep(args.sleep_between_pages)

        counties_done += 1
        print_status(
            counties_done,
            counties_total,
            county_code,
            county_name,
            f"done:{final_status}",
            page,
            fetched_total,
            working_endpoint,
        )
        finalize_status_line()

        global_summary["counties"].append(
            {
                "county_code": county_code,
                "county_name": county_name,
                "status": final_status,
                "stop_reason": stop_reason,
                "probe_note": probe_note,
                "endpoint": working_endpoint,
                "pages": page,
                "fetched_records": fetched_total,
                "inserted_or_updated": upsert_total,
                "skipped_unchanged": unchanged_total,
                "errors": errors_total,
            }
        )

    global_summary["finished_at"] = now_utc_iso()
    global_summary["ok_counties"] = sum(1 for row in global_summary["counties"] if row["status"] == "ok")
    global_summary["error_or_skipped_counties"] = counties_total - global_summary["ok_counties"]
    global_summary["records_fetched_total"] = sum(int(row["fetched_records"]) for row in global_summary["counties"])
    global_summary["inserted_or_updated_total"] = sum(int(row["inserted_or_updated"]) for row in global_summary["counties"])
    global_summary["skipped_unchanged_total"] = sum(int(row["skipped_unchanged"]) for row in global_summary["counties"])
    global_summary["errors_total"] = sum(int(row["errors"]) for row in global_summary["counties"])

    save_json(summary_path, global_summary)
    print(
        json.dumps(
            {
                "run_id": run_id,
                "summary": str(summary_path),
                "events": str(events_path),
                "counties_total": counties_total,
                "ok_counties": global_summary["ok_counties"],
                "error_or_skipped_counties": global_summary["error_or_skipped_counties"],
                "records_fetched_total": global_summary["records_fetched_total"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if global_summary["error_or_skipped_counties"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
