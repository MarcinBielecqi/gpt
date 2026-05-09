#!/usr/bin/env python3
from __future__ import annotations

import functools
import http.server
import socket
import socketserver
import webbrowser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOST = "127.0.0.1"
START_PORT = 8765
VIEWER_PATH = "/maps/all_parcels.html"


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


def free_port(start: int) -> int:
    for port in range(start, start + 100):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind((HOST, port))
            except OSError:
                continue
            return port
    raise RuntimeError(f"No free port found from {start} to {start + 99}.")


def main() -> int:
    port = free_port(START_PORT)
    handler = functools.partial(QuietHandler, directory=str(ROOT))
    url = f"http://{HOST}:{port}{VIEWER_PATH}"
    with socketserver.TCPServer((HOST, port), handler) as server:
        print(f"All parcels viewer: {url}")
        print("Close this window or press Ctrl+C to stop the local server.")
        webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
