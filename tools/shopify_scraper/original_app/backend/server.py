from __future__ import annotations

import json
import mimetypes
import os
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from scraper_manager import manager

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
HOST = "127.0.0.1"
PORT = 8765


def json_bytes(payload) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "Codexifyr/1.0"

    def log_message(self, fmt, *args):
        print("[web] " + (fmt % args))

    def _send_json(self, payload, status=200):
        raw = json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _body_json(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8") or "{}")

    def _serve_file(self, path: Path, download=False):
        if not path.exists() or not path.is_file():
            self.send_error(404)
            return
        data = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        if download:
            self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
        self.end_headers()
        self.wfile.write(data)


    def do_HEAD(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            file_path = FRONTEND / "index.html"
        elif path in ("/dashboard", "/dashboard.html"):
            file_path = FRONTEND / "dashboard.html"
        else:
            requested = (FRONTEND / path.lstrip("/")).resolve()
            try:
                requested.relative_to(FRONTEND.resolve())
            except ValueError:
                return self.send_error(403)
            file_path = requested
        if not file_path.exists():
            return self.send_error(404)
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(file_path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(file_path.stat().st_size))
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/status":
            return self._send_json({"ok": True, **manager.snapshot()})
        if path == "/api/download":
            csv_path = manager.output_dir / "shopify_products.csv"
            return self._serve_file(csv_path, download=True)
        if path == "/api/report":
            report = manager.output_dir / "scrape_report.json"
            return self._serve_file(report, download=True)

        if path in ("/", "/index.html"):
            file_path = FRONTEND / "index.html"
        elif path in ("/dashboard", "/dashboard.html"):
            file_path = FRONTEND / "dashboard.html"
        else:
            requested = (FRONTEND / path.lstrip("/")).resolve()
            try:
                requested.relative_to(FRONTEND.resolve())
            except ValueError:
                return self.send_error(403)
            file_path = requested
        self._serve_file(file_path)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            body = self._body_json()
            if path == "/api/start":
                result = manager.start(
                    url=body.get("url", ""),
                    delay=body.get("delay", 0.7),
                    retries=body.get("retries", 3),
                    timeout=body.get("timeout", 45000),
                    reset=bool(body.get("reset", True)),
                )
                return self._send_json({"ok": True, **result})
            if path == "/api/continue":
                return self._send_json({"ok": True, **manager.continue_after_captcha()})
            if path == "/api/stop":
                return self._send_json({"ok": True, **manager.stop()})
            if path == "/api/focus-browser":
                focused = manager.focus_browser()
                return self._send_json({"ok": focused, "focused": focused, "message": "Browser focused." if focused else "The scraper browser is already opened separately; bring its Chromium window to the front manually if Windows could not focus it."})
            return self._send_json({"ok": False, "error": "Unknown endpoint."}, 404)
        except (ValueError, RuntimeError) as exc:
            return self._send_json({"ok": False, "error": str(exc)}, 400)
        except Exception as exc:
            return self._send_json({"ok": False, "error": f"Server error: {exc}"}, 500)


def run_server(host=HOST, port=PORT):
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"[Codexifyr] Frontend + backend: http://{host}:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        manager.stop()
        httpd.server_close()


if __name__ == "__main__":
    run_server()
