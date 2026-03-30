"""FastAPI application for the Sunbeam RCA web UI."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from sunbeam_rca.web.api import router

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(
    title="Sunbeam AI Inspector",
    description="Root-Cause Analysis for Sunbeam CI build failures",
    version="0.1.0",
)

app.include_router(router, prefix="/api")

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/favicon.ico")
async def favicon():
    return FileResponse(
        str(STATIC_DIR / "favicon.svg"), media_type="image/svg+xml"
    )
