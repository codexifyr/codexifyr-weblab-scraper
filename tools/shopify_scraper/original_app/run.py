from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent
URL = "http://127.0.0.1:8765/"


def open_chrome(url: str) -> bool:
    if os.name == "nt":
        candidates = [
            Path(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
        ]
        for chrome in candidates:
            if chrome.exists():
                subprocess.Popen([str(chrome), url])
                return True
        try:
            subprocess.Popen(["cmd", "/c", "start", "chrome", url], shell=False)
            return True
        except Exception:
            pass
    try:
        import webbrowser
        return bool(webbrowser.open(url))
    except Exception:
        return False


def launch_when_ready():
    for _ in range(60):
        try:
            with urlopen(URL, timeout=0.4) as response:
                if response.status == 200:
                    open_chrome(URL)
                    return
        except Exception:
            time.sleep(0.2)


def main():
    print("=" * 58)
    print("       CODEXIFYR - WEBSITE PRODUCT SCRAPER")
    print("=" * 58)
    print("[+] Starting local backend and frontend...")
    threading.Thread(target=launch_when_ready, daemon=True).start()
    sys.path.insert(0, str(ROOT / "backend"))
    from server import run_server
    print("[+] Chrome will open automatically.")
    print("[+] Press CTRL+C here to stop Codexifyr.")
    run_server()


if __name__ == "__main__":
    main()
