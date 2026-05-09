
from __future__ import annotations

import argparse
import json
import mimetypes
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import map_server as base


STATIC_DIR = Path(__file__).resolve().parent / "static"


def _send_static(handler: BaseHTTPRequestHandler, path: Path) -> None:
    if not path.exists() or not path.is_file():
        base.send(handler, {"status": "error", "message": "not found"}, 404)
        return
    body = path.read_bytes()
    content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    if path.suffix == ".js":
        content_type = "application/javascript; charset=utf-8"
    elif path.suffix == ".css":
        content_type = "text/css; charset=utf-8"
    elif path.suffix == ".html":
        content_type = "text/html; charset=utf-8"
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


_legacy_do_get = base.Handler.do_GET


def _compact_do_get(self: BaseHTTPRequestHandler) -> None:
    parsed = urllib.parse.urlparse(self.path)
    if parsed.path in {"/", "/map.html"}:
        _send_static(self, STATIC_DIR / "map.html")
    elif parsed.path == "/map.css":
        _send_static(self, STATIC_DIR / "map.css")
    elif parsed.path == "/map_viewer.js":
        _send_static(self, STATIC_DIR / "map_viewer.js")
    else:
        _legacy_do_get(self)


def _load_analysis_file(q: dict[str, list[str]]) -> dict[str, Any]:
    rel = q.get("file", [""])[0]
    if not rel:
        raise ValueError("missing file")
    path = base.safe_analysis_path(rel)
    if not path.exists():
        raise FileNotFoundError(str(path))
    if path.stat().st_size > base.ANALYSIS_MAX_BYTES:
        raise ValueError("analysis file too large")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return base.normalize_analysis(payload, source=f"analysis/{rel}")


base.Handler.do_GET = _compact_do_get
base.load_analysis_file = _load_analysis_file


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--canon-db", required=True)
    parser.add_argument("--bus-db", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--ttl-seconds", type=int, default=300)
    parser.add_argument("--default-limit", type=int, default=500)
    parser.add_argument("--analysis-dir")
    args = parser.parse_args()

    ttl = max(1, int(args.ttl_seconds))
    analysis_dir = Path(args.analysis_dir).resolve() if args.analysis_dir else Path("analysis").resolve()
    base.CFG.update({
        "canon_db": str(Path(args.canon_db).resolve()),
        "bus_db": str(Path(args.bus_db).resolve()),
        "run_id": args.run_id,
        "ttl_seconds": ttl,
        "default_limit": max(5, min(base.MAP_LIMIT_MAX, int(args.default_limit))),
        "analysis_dir": str(analysis_dir),
        "expires_at_epoch": time.time() + ttl,
    })

    server = ThreadingHTTPServer((args.host, int(args.port)), base.Handler)
    timer = base.threading.Timer(base.CFG["ttl_seconds"], server.shutdown)
    timer.daemon = True
    timer.start()
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
