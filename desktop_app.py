#!/usr/bin/env python3
"""Codexifyr WebLab desktop shell.

Runs the existing local backend in a background thread and displays the frontend
inside a native Windows WebView2 window. Scraper Chromium windows remain separate
so CAPTCHA/manual interactions continue to work normally.
"""
import os
import socket
import sys
import runpy
import threading
import time
from pathlib import Path

APP_NAME = "Codexifyr WebLab"
PORT = int(os.environ.get("CODEXIFYR_PORT", "8877"))
HOST = "127.0.0.1"
URL = f"http://{HOST}:{PORT}/"


def default_data_dir() -> Path:
    override = os.environ.get("CODEXIFYR_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "Codexifyr WebLab" / "Data"
    return Path.home() / "Codexifyr" / "Data"


def wait_for_port(timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection((HOST, PORT), timeout=0.35):
                return True
        except OSError:
            time.sleep(0.15)
    return False


def run_server():
    from backend.server import serve
    serve(HOST, PORT)


def run_shopify_worker():
    """PyInstaller worker mode: execute the bundled, unchanged scraper.py."""
    base=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent))
    script=base/'tools'/'shopify_scraper'/'scraper.py'
    if not script.exists():
        script=Path(__file__).resolve().parent/'tools'/'shopify_scraper'/'scraper.py'
    args=sys.argv[sys.argv.index('--shopify-worker')+1:]
    sys.argv=[str(script)]+args
    runpy.run_path(str(script),run_name='__main__')


def main():
    data_dir = default_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ["CODEXIFYR_DATA_DIR"] = str(data_dir)

    threading.Thread(target=run_server, daemon=True, name="codexifyr-backend").start()
    if not wait_for_port():
        raise RuntimeError("Codexifyr backend did not start in time.")

    import webview
    # pywebview disables HTTP attachment downloads by default. Every WebLab
    # download button points at a local /api/download endpoint that responds
    # with Content-Disposition: attachment, so downloads must be explicitly
    # enabled before webview.start(). On Windows/WebView2 this opens the native
    # Save As dialog and preserves the filename supplied by the backend.
    webview.settings['ALLOW_DOWNLOADS'] = True
    window = webview.create_window(
        APP_NAME,
        URL,
        width=1480,
        height=920,
        min_size=(1024, 680),
        resizable=True,
        text_select=True,
        confirm_close=False,
    )
    webview.start(debug=False, private_mode=False)


if __name__ == "__main__":
    if '--shopify-worker' in sys.argv:run_shopify_worker()
    else:main()
