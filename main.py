#!/usr/bin/env python3
from __future__ import annotations

import io
import json
import mimetypes
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlparse

os.environ.setdefault("CODEXIFYR_DATA_DIR", "/tmp/codexifyr-runtime")

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.server import (
    FRONT,
    MAN,
    RUNTIME,
    analyze_site_data,
    ensure_plugin_zip,
    ensure_shopify_json,
    validate_shopify_csv,
)

ROOT = Path(__file__).resolve().parent
SHOPIFY_SCRAPER = ROOT / "tools" / "shopify_scraper" / "scraper.py"

app = FastAPI(
    title="codexifyr-weblab-scraper",
    version="4.2.3-fastapi",
    description="Codexifyr WebLab FastAPI scraper API",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)


class Payload(BaseModel):
    model_config = {"extra": "allow"}


class ScrapeRequest(BaseModel):
    url: str


STATIC_ROUTES = {
    "/": "index.html",
    "/scraper": "scraper.html",
    "/repair": "repair.html",
    "/jobs": "jobs.html",
    "/validation": "validation.html",
    "/export": "export.html",
    "/plugin": "plugin.html",
    "/docs": "docs.html",
    "/shopify": "shopify.html",
    "/dashboard": "scraper.html",
}


def _safe_frontend_file(rel: str) -> Path:
    f = (FRONT / rel).resolve()
    front = FRONT.resolve()

    if f != front and front not in f.parents:
        raise HTTPException(404, "Not found")

    if not f.is_file():
        raise HTTPException(404, "Not found")

    return f


def _job(jid: str):
    try:
        return MAN.get(jid)
    except KeyError:
        raise HTTPException(404, "Job not found")


def _payload_dict(payload: Payload | None) -> dict:
    if payload is None:
        return {}

    return payload.model_dump(exclude_none=True)


@app.get("/api/health", tags=["System"])
def health():
    return {
        "ok": True,
        "service": "codexifyr-weblab-scraper",
        "fastapi": True,
        "chromium_mode": "container"
    }


@app.get("/api/system", tags=["System"])
def system():
    return {
        "app_version": "4.2.3 WebLab",
        "api_layer": "FastAPI",
        "service": "codexifyr-weblab-scraper",
        "plugin_version": "3.0.0",
        "python": sys.version.split()[0],
        "jobs": len(MAN.jobs),
        "runtime": str(RUNTIME),
        "plugin_ready": ensure_plugin_zip().exists(),
    }


@app.post("/api/scrape", tags=["Shopify Scraper"])
def scrape_json(payload: ScrapeRequest):
    source = payload.url.strip()

    if not source:
        raise HTTPException(400, "URL is required.")

    if not source.startswith(("http://", "https://")):
        source = "https://" + source

    parsed = urlparse(source)

    if not parsed.netloc:
        raise HTTPException(400, "Invalid URL.")

    if not SHOPIFY_SCRAPER.exists():
        raise HTTPException(500, "Bundled Shopify scraper is missing.")

    output_dir = Path(
        tempfile.mkdtemp(
            prefix="codexifyr_shopify_",
            dir="/tmp"
        )
    )

    cmd = [
        sys.executable,
        "-u",
        str(SHOPIFY_SCRAPER),
        "--url",
        source,
        "--output",
        str(output_dir),
        "--delay",
        "0.7",
        "--retries",
        "3",
        "--timeout",
        "45000",
        "--headless",
        "--reset",
    ]

    child_env = os.environ.copy()

    existing_pythonpath = child_env.get(
        "PYTHONPATH",
        ""
    )

    child_env["PYTHONPATH"] = os.pathsep.join(
        [p for p in sys.path if p]
        + (
            [existing_pythonpath]
            if existing_pythonpath
            else []
        )
    )

    try:
        result = subprocess.run(
            cmd,
            cwd=str(SHOPIFY_SCRAPER.parent),
            env=child_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=280,
        )

    except subprocess.TimeoutExpired:
        raise HTTPException(
            504,
            "Scraper exceeded execution time limit."
        )

    logs = result.stdout or ""

    if result.returncode != 0:
        raise HTTPException(
            500,
            detail={
                "message": "Scraper failed.",
                "return_code": result.returncode,
                "logs": logs[-8000:],
            },
        )

    csv_file = output_dir / "shopify_products.csv"
    json_file = output_dir / "shopify_products.json"

    if not csv_file.exists():
        raise HTTPException(
            500,
            detail={
                "message": "Scraper finished but CSV was not generated.",
                "logs": logs[-8000:],
            },
        )

    try:
        ensure_shopify_json(
            csv_file,
            json_file
        )

    except Exception as e:
        raise HTTPException(
            500,
            f"JSON generation failed: {e}"
        )

    if not json_file.exists():
        raise HTTPException(
            500,
            "JSON output was not created."
        )

    try:
        data = json.loads(
            json_file.read_text(
                encoding="utf-8"
            )
        )

    except Exception as e:
        raise HTTPException(
            500,
            f"Unable to read JSON output: {e}"
        )

    response = {
        "success": True,
        "source_url": source,
        "count": len(data),
        "data": data,
    }

    encoded = json.dumps(
        response,
        ensure_ascii=False
    ).encode("utf-8")

    if len(encoded) > 4_000_000:
        raise HTTPException(
            413,
            detail={
                "message": "Scrape completed but JSON response is too large for direct API return.",
                "count": len(data),
            },
        )

    return response


@app.get("/api/jobs", tags=["Jobs"])
def jobs():
    return {
        "jobs": MAN.list()
    }


@app.get("/api/jobs/{jid}", tags=["Jobs"])
def job(jid: str):
    return _job(jid)


@app.post(
    "/api/jobs/scan",
    tags=["Website Scraper"]
)
def start_scan(payload: Payload):
    try:
        return {
            "job": MAN.start_scan(
                _payload_dict(payload)
            )
        }

    except Exception as e:
        raise HTTPException(
            400,
            str(e)
        )


@app.post(
    "/api/jobs/shopify",
    tags=["Shopify Scraper"]
)
def start_shopify(payload: Payload):
    try:
        return {
            "job": MAN.start_shopify(
                _payload_dict(payload)
            )
        }

    except Exception as e:
        raise HTTPException(
            400,
            str(e)
        )


@app.post(
    "/api/repair/upload",
    tags=["Repair"]
)
async def repair_upload(
    file: UploadFile = File(...)
):
    try:
        content = await file.read()
        name = file.filename or "site-data.json"

        if name.lower().endswith(".zip"):
            with zipfile.ZipFile(
                io.BytesIO(content)
            ) as z:

                names = z.namelist()

                cands = [
                    n
                    for n in names
                    if Path(n).name
                    == "corrected-site-data.json"
                ] or [
                    n
                    for n in names
                    if Path(n).name
                    == "site-data.json"
                ]

                if not cands:
                    raise ValueError(
                        "ZIP does not contain site-data.json or corrected-site-data.json."
                    )

                content = z.read(
                    cands[0]
                )

                name = "site-data.json"

        return {
            "job": MAN.create_repair_upload(
                name,
                content
            )
        }

    except Exception as e:
        raise HTTPException(
            400,
            str(e)
        )


@app.post(
    "/api/jobs/{jid}/start-repair",
    tags=["Repair"]
)
def start_repair(
    jid: str,
    payload: Payload | None = None
):
    try:
        return {
            "job": MAN.start_repair(
                jid,
                _payload_dict(payload)
            )
        }

    except KeyError:
        raise HTTPException(
            404,
            "Job not found"
        )

    except Exception as e:
        raise HTTPException(
            400,
            str(e)
        )


@app.post(
    "/api/jobs/{jid}/stop",
    tags=["Jobs"]
)
@app.post(
    "/api/jobs/{jid}/pause",
    tags=["Jobs"]
)
def stop_job(jid: str):
    try:
        MAN.stop(jid)

        return {
            "ok": True
        }

    except KeyError:
        raise HTTPException(
            404,
            "Job not found"
        )


@app.post(
    "/api/jobs/{jid}/focus",
    tags=["Jobs"]
)
def focus_job(jid: str):
    try:
        return {
            "ok": MAN.focus(jid)
        }

    except KeyError:
        raise HTTPException(
            404,
            "Job not found"
        )


@app.post(
    "/api/jobs/{jid}/continue-captcha",
    tags=["Jobs"]
)
def continue_captcha(jid: str):
    try:
        return {
            "ok": MAN.continue_captcha(jid)
        }

    except KeyError:
        raise HTTPException(
            404,
            "Job not found"
        )


@app.post(
    "/api/jobs/{jid}/resume",
    tags=["Jobs"]
)
def resume_job(jid: str):
    try:
        return {
            "job": MAN.resume(jid)
        }

    except KeyError:
        raise HTTPException(
            404,
            "Job not found"
        )

    except Exception as e:
        raise HTTPException(
            400,
            str(e)
        )


@app.post(
    "/api/jobs/{jid}/build",
    tags=["Export"]
)
def build_job(jid: str):
    try:
        MAN.build(jid)

        return {
            "ok": True
        }

    except KeyError:
        raise HTTPException(
            404,
            "Job not found"
        )

    except Exception as e:
        raise HTTPException(
            400,
            str(e)
        )


@app.get(
    "/api/job-data",
    tags=["Jobs"]
)
def job_data(
    job: str = Query(...),
    kind: str = Query("auto")
):
    try:
        jd = MAN.job_dir(job)
        j = _job(job)

        candidates = []

        if (
            kind == "corrected"
            or (
                kind == "auto"
                and j["type"] == "repair"
            )
        ):
            candidates.append(
                jd
                / "corrected-site-data.json"
            )

        candidates += [
            jd / "site-data.json",
            jd / "original-site-data.json",
        ]

        f = next(
            (
                x
                for x in candidates
                if x.exists()
            ),
            None
        )

        if not f:
            return {
                "ready": False
            }

        d = json.loads(
            f.read_text(
                encoding="utf-8"
            )
        )

        return {
            "ready": True,
            "source_url": d.get(
                "source_url",
                ""
            ),
            "platform": d.get(
                "platform",
                "Unknown"
            ),
            "branding": d.get(
                "branding",
                {}
            ),
            "menus": d.get(
                "menus",
                []
            ),
            "categories": d.get(
                "categories",
                []
            ),
            "blog_categories": d.get(
                "blog_categories",
                []
            ),
            "tags": d.get(
                "tags",
                []
            ),
            "products": d.get(
                "products",
                []
            )[:500],
            "pages": d.get(
                "pages",
                []
            )[:500],
            "media": d.get(
                "media",
                []
            )[:500],
            "counts": {
                "products": len(
                    d.get(
                        "products",
                        []
                    )
                ),
                "pages": len(
                    d.get(
                        "pages",
                        []
                    )
                ),
                "media": len(
                    d.get(
                        "media",
                        []
                    )
                ),
            },
        }

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            500,
            str(e)
        )


@app.get(
    "/api/analyze",
    tags=["Validation"]
)
def analyze(
    job: str = Query(...),
    validator: str = Query("")
):
    try:
        jd = MAN.job_dir(job)
        j = _job(job)

        if (
            validator == "shopify"
            or j.get("type")
            == "shopify"
        ):
            return validate_shopify_csv(
                jd
                / "shopify_products.csv"
            )

        candidates = [
            jd
            / "corrected-site-data.json",
            jd
            / "site-data.json",
            jd
            / "original-site-data.json",
        ]

        f = next(
            (
                x
                for x in candidates
                if x.exists()
            ),
            None
        )

        if not f:
            raise RuntimeError(
                "No site-data file found for this job."
            )

        d = json.loads(
            f.read_text(
                encoding="utf-8"
            )
        )

        out = analyze_site_data(d)
        out["validator"] = "wordpress"

        return out

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            400,
            str(e)
        )


@app.get(
    "/api/download",
    tags=["Export"]
)
def download(
    job: str = Query(...),
    type: str = Query("package")
):
    try:
        jd = MAN.job_dir(job)
        _job(job)

        mp = {
            "package": (
                "codexifyr-wordpress-migration.zip",
                "codexifyr-wordpress-migration.zip",
            ),
            "site": (
                "site-data.json",
                "site-data.json",
            ),
            "original": (
                "original-site-data.json",
                "original-site-data.json",
            ),
            "corrected": (
                "corrected-site-data.json",
                "corrected-site-data.json",
            ),
            "report": (
                "migration-report.json",
                "migration-report.json",
            ),
            "repair-report": (
                "repair-report.json",
                "repair-report.json",
            ),
            "shopify-csv": (
                "shopify_products.csv",
                "shopify_products.csv",
            ),
            "shopify-json": (
                "shopify_products.json",
                "shopify_products.json",
            ),
            "shopify-report": (
                "scrape_report.json",
                "scrape_report.json",
            ),
        }

        if type == "shopify-json":
            ensure_shopify_json(
                jd
                / "shopify_products.csv",
                jd
                / "shopify_products.json",
            )

        fn, dn = mp.get(
            type,
            mp["package"]
        )

        f = jd / fn

        if not f.is_file():
            raise HTTPException(
                404,
                "Download is not ready"
            )

        return FileResponse(
            f,
            filename=dn,
            media_type=(
                mimetypes.guess_type(
                    str(f)
                )[0]
                or "application/octet-stream"
            ),
        )

    except HTTPException:
        raise

    except Exception:
        raise HTTPException(
            404,
            "Download is not ready"
        )


@app.get(
    "/api/plugin/download",
    tags=["Export"]
)
def plugin_download():
    f = ensure_plugin_zip()

    if not f.is_file():
        raise HTTPException(
            404,
            "Plugin ZIP is not ready"
        )

    return FileResponse(
        f,
        filename="codexifyr-migrator-importer.zip",
        media_type="application/zip",
    )


@app.get(
    "/api",
    tags=["System"]
)
def api_root():
    return {
        "service": "codexifyr-weblab-scraper",
        "status": "online",
        "health": "/api/health",
        "scrape_endpoint": "/api/scrape",
        "swagger": "/api/docs",
        "openapi": "/api/openapi.json",
    }


@app.get(
    "/{full_path:path}",
    include_in_schema=False
)
def frontend(full_path: str):
    path = "/" + full_path

    rel = STATIC_ROUTES.get(
        path,
        full_path or "index.html"
    )

    f = _safe_frontend_file(rel)

    return FileResponse(
        f,
        media_type=(
            mimetypes.guess_type(
                str(f)
            )[0]
            or "application/octet-stream"
        ),
    )