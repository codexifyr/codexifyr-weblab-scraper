# codexifyr-weblab-scraper — FastAPI deployment

This package adds a deployment layer only. The existing WebLab backend and Shopify scraper core are preserved.

## Local FastAPI test

Install `requirements-deploy.txt`, install Chromium with Playwright, then run:

`uvicorn main:app --host 127.0.0.1 --port 8877`

WebLab UI: `http://127.0.0.1:8877/`
FastAPI Swagger: `http://127.0.0.1:8877/api/docs`
Health check: `http://127.0.0.1:8877/api/health`

## Render

1. Push this folder to a GitHub repository named `codexifyr-weblab-scraper`.
2. In Render choose **New > Blueprint** and connect the repository. Render reads `render.yaml` and builds the included Dockerfile.
3. Wait for the Docker build and Playwright Chromium installation to complete.
4. Open the Render URL. Test `/api/health`, then `/api/docs`.

## Hosted-browser note

A cloud container has no normal visible desktop browser. Use headless Chromium for hosted scraping. Manual CAPTCHA solving that requires a visible local browser remains a desktop/local workflow; no CAPTCHA bypass is included.

## Storage note

Render free instances use ephemeral local storage. Jobs/files can disappear after a restart/redeploy. Production use should attach persistent storage or external object storage/database.
