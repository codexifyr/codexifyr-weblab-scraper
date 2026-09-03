# Codexifyr — Website Product Scraper UI

## Normal Windows use

1. Double-click `START CODEXIFYR.bat`. On the first run it automatically creates a local `.venv`, installs the scraper requirements, and installs Playwright Chromium.
2. On later runs, the same file starts immediately. `INSTALL CODEXIFYR.bat` is also included if you ever want to reinstall dependencies manually.
3. Codexifyr starts at `http://127.0.0.1:8765/` and attempts to open that address specifically in Google Chrome.
4. Click **Go to Scraper Dashboard**.
5. Turn **SYSTEM ON**.
6. Enter one store URL, choose delay (for example `0.7`) and retries, and click **Start Scraping**.
7. The original `scraper.py` launches its visible Chromium browser. If a CAPTCHA appears, solve it there and press **Continue After CAPTCHA** in the dashboard.
8. **Stop Scraping** terminates the active scraper process; its checkpoint remains on disk.
9. When export completes, use **Download Shopify CSV**.

## Architecture

- `scraper.py` — original scraper engine; unchanged.
- `backend/scraper_manager.py` — launches and controls `scraper.py` as a subprocess.
- `backend/server.py` — local HTTP backend + frontend server (Python standard library only).
- `frontend/index.html` — minimal dark Codexifyr home page.
- `frontend/dashboard.html` — scraper dashboard.
- `run.py` — starts the local app and opens Chrome.

## Important browser note

The scraper's headed Chromium window is intentionally separate from the dashboard. The dashboard includes a best-effort **Open / Focus Browser** button on Windows, but it does not embed or mirror Chromium because doing that reliably would require changing the scraper/browser integration. This keeps the existing scraper functions and behavior intact.

## Responsible use

Use the scraper only for public product data on websites you are authorized to collect. It does not bypass CAPTCHA or access controls.
