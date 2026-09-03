from __future__ import annotations

import json
import os
import re
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
SCRAPER = ROOT / "scraper.py"
OUTPUT_ROOT = ROOT / "output"


class ScraperManager:
    """Controls the existing scraper.py as a child process without modifying it."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.process: subprocess.Popen[str] | None = None
        self.logs: deque[str] = deque(maxlen=700)
        self.status_name = "idle"
        self.started_at: float | None = None
        self.finished_at: float | None = None
        self.last_error = ""
        self.current_url = ""
        self.store_url = ""
        self.output_dir = OUTPUT_ROOT / "current"
        self.return_code: int | None = None
        self._reader: threading.Thread | None = None

    def _python_executable(self) -> str:
        if os.name == "nt":
            candidates = [
                ROOT / "full_scraper" / ".venv" / "Scripts" / "python.exe",
                ROOT / ".venv" / "Scripts" / "python.exe",
            ]
            for candidate in candidates:
                if candidate.exists():
                    return str(candidate)
        return sys.executable

    def _append(self, line: str) -> None:
        text = line.rstrip("\r\n")
        if not text:
            return
        with self.lock:
            self.logs.append(text)
            urls = re.findall(r"https?://[^\s)'\"]+", text)
            if urls:
                self.current_url = urls[-1].rstrip(".,]")
            low = text.lower()
            if "captcha / browser challenge detected" in low or "press enter after it is cleared" in low:
                self.status_name = "paused_captcha"
            if text.startswith("CSV:"):
                self.status_name = "completed"

    def _reader_loop(self, proc: subprocess.Popen[str]) -> None:
        try:
            assert proc.stdout is not None
            for line in iter(proc.stdout.readline, ""):
                if not line:
                    break
                self._append(line)
        finally:
            code = proc.wait()
            with self.lock:
                self.return_code = code
                self.finished_at = time.time()
                if self.status_name == "stopping":
                    self.status_name = "stopped"
                elif code == 0:
                    self.status_name = "completed"
                elif self.status_name != "stopped":
                    self.status_name = "error"
                    self.last_error = f"Scraper exited with code {code}. Check the log for details."

    def start(self, *, url: str, delay: float, retries: int, timeout: int = 45000, reset: bool = True) -> dict:
        url = (url or "").strip()
        if not re.match(r"^https?://", url, re.I):
            url = "https://" + url
        parsed = urlparse(url)
        if not parsed.netloc or any(ch.isspace() for ch in parsed.netloc) or "." not in parsed.netloc:
            raise ValueError("Enter a valid website URL, for example https://example.com.")
        delay = min(max(float(delay), 0.0), 60.0)
        retries = min(max(int(retries), 1), 20)
        timeout = min(max(int(timeout), 5000), 180000)

        with self.lock:
            if self.process and self.process.poll() is None:
                raise RuntimeError("A scrape is already running. Stop it before starting another site.")

            OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
            self.output_dir = OUTPUT_ROOT / "current"
            self.output_dir.mkdir(parents=True, exist_ok=True)
            if reset:
                for stale in ("shopify_products.csv", "scrape_report.json", "collections_meta.csv"):
                    try:
                        (self.output_dir / stale).unlink(missing_ok=True)
                    except Exception:
                        pass
            self.logs.clear()
            self.last_error = ""
            self.current_url = url
            self.store_url = url
            self.status_name = "starting"
            self.started_at = time.time()
            self.finished_at = None
            self.return_code = None

            cmd = [
                self._python_executable(), "-u", str(SCRAPER),
                "--url", url,
                "--output", str(self.output_dir),
                "--delay", str(delay),
                "--retries", str(retries),
                "--timeout", str(timeout),
            ]
            if reset:
                cmd.append("--reset")

            creationflags = 0
            startupinfo = None
            if os.name == "nt":
                creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

            self.process = subprocess.Popen(
                cmd,
                cwd=str(ROOT),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                creationflags=creationflags,
                startupinfo=startupinfo,
            )
            self.status_name = "running"
            self._reader = threading.Thread(target=self._reader_loop, args=(self.process,), daemon=True)
            self._reader.start()
            self._append(f"[Codexifyr] Started scraper for {url}")
            return self.snapshot()

    def continue_after_captcha(self) -> dict:
        with self.lock:
            if not self.process or self.process.poll() is not None:
                raise RuntimeError("No active scraper process.")
            if not self.process.stdin:
                raise RuntimeError("Scraper input channel is unavailable.")
            self.process.stdin.write("\n")
            self.process.stdin.flush()
            self.status_name = "running"
            self._append("[Codexifyr] Continue signal sent after manual CAPTCHA handling.")
            return self.snapshot()

    def stop(self) -> dict:
        with self.lock:
            proc = self.process
            if not proc or proc.poll() is not None:
                self.status_name = "stopped" if self.status_name not in ("completed", "error") else self.status_name
                return self.snapshot()
            self.status_name = "stopping"
            self._append("[Codexifyr] Stop requested. Checkpoint data will remain for resume/export.")

        try:
            if os.name == "nt":
                proc.send_signal(getattr(signal, "CTRL_BREAK_EVENT", signal.SIGTERM))
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.terminate()
            else:
                proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        except Exception as exc:
            with self.lock:
                self.last_error = str(exc)
        return self.snapshot()

    def focus_browser(self) -> bool:
        """Best-effort Windows focus for the headed Chromium window opened by scraper.py."""
        if os.name != "nt":
            return False
        script = r'''$ws = New-Object -ComObject WScript.Shell
$names = @('Chromium','Chrome')
foreach ($n in $names) { if ($ws.AppActivate($n)) { exit 0 } }
exit 1'''
        try:
            result = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, timeout=4)
            return result.returncode == 0
        except Exception:
            return False

    def _checkpoint_stats(self) -> dict:
        stats = {"products": 0, "variants": 0, "images": 0, "collections_done": 0, "collections_total": 0}
        db_path = self.output_dir / "checkpoint.sqlite"
        if not db_path.exists():
            return stats
        try:
            con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=0.25)
            cur = con.cursor()
            rows = cur.execute("SELECT data FROM products").fetchall()
            stats["products"] = len(rows)
            for (raw,) in rows:
                try:
                    p = json.loads(raw)
                    stats["variants"] += len(p.get("variants") or [])
                    stats["images"] += len(p.get("images") or [])
                except Exception:
                    pass
            row = cur.execute("SELECT COUNT(*), SUM(CASE WHEN done=1 THEN 1 ELSE 0 END) FROM categories").fetchone()
            if row:
                stats["collections_total"] = int(row[0] or 0)
                stats["collections_done"] = int(row[1] or 0)
            con.close()
        except Exception:
            pass
        return stats

    def snapshot(self) -> dict:
        with self.lock:
            running = bool(self.process and self.process.poll() is None)
            started_at = self.started_at
            elapsed = 0
            if started_at:
                elapsed = int((self.finished_at or time.time()) - started_at)
            csv_path = self.output_dir / "shopify_products.csv"
            data = {
                "status": self.status_name,
                "running": running,
                "store_url": self.store_url,
                "current_url": self.current_url,
                "elapsed_seconds": max(elapsed, 0),
                "last_error": self.last_error,
                "return_code": self.return_code,
                "csv_ready": csv_path.exists(),
                "csv_name": csv_path.name if csv_path.exists() else "",
                "logs": list(self.logs)[-180:],
            }
        data.update(self._checkpoint_stats())
        return data


manager = ScraperManager()
