"""HTTP server: the browser app, the merged site data, and the downloaded reports.

Config comes from config/app.yaml (override with SOUTHBAY_WATER_CONFIG); a missing file is a
startup crash, never a silent default.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

REPO = Path(__file__).resolve().parents[2]


def load_config() -> dict:
    path = Path(os.environ.get("SOUTHBAY_WATER_CONFIG", REPO / "config" / "app.yaml"))
    if not path.exists():
        raise SystemExit(f"southbay-water: config not found at {path}")
    with open(path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    for key in ("data_dir", "web_dir", "reports_dir"):
        cfg[key] = (REPO / cfg[key]).resolve()
    return cfg


def create_app(cfg: dict | None = None) -> FastAPI:
    cfg = cfg or load_config()
    app = FastAPI(title="southbay-water", docs_url=None, redoc_url=None)
    data_dir: Path = cfg["data_dir"]
    web_dir: Path = cfg["web_dir"]
    reports_dir: Path = cfg["reports_dir"]
    app.state.cfg = cfg

    def _site() -> dict:
        path = data_dir / "site.json"
        if not path.exists():
            raise HTTPException(503, "site.json not built yet; run python3 -m src.pipeline.build_site")
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    @app.get("/api/health")
    def health() -> dict:
        site = _site()
        return {"ok": True, "generated": site["generated"], "counts": site["counts"]}

    @app.get("/api/site")
    def site() -> Response:
        path = data_dir / "site.json"
        if not path.exists():
            raise HTTPException(503, "site.json not built yet")
        return FileResponse(path, media_type="application/json", headers={"Cache-Control": "no-cache"})

    @app.get("/api/boundaries")
    def boundaries() -> Response:
        path = data_dir / "site.geojson"
        if not path.exists():
            raise HTTPException(503, "site.geojson not built yet")
        return FileResponse(path, media_type="application/geo+json", headers={"Cache-Control": "no-cache"})

    @app.get("/api/system/{pwsid}")
    def system(pwsid: str) -> JSONResponse:
        rec = _site()["systems"].get(pwsid.upper())
        if rec is None:
            raise HTTPException(404, f"unknown system {pwsid}")
        return JSONResponse(rec)

    @app.get("/reports/{pwsid}/{filename}")
    def report(pwsid: str, filename: str) -> Response:
        if "/" in pwsid or ".." in pwsid or "/" in filename or ".." in filename:
            raise HTTPException(404)
        path = (reports_dir / pwsid / filename).resolve()
        if reports_dir not in path.parents or not path.is_file():
            raise HTTPException(404, "report not found")
        media = {".pdf": "application/pdf", ".html": "text/html", ".htm": "text/html",
                 ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}.get(path.suffix.lower(), "application/octet-stream")
        return FileResponse(path, media_type=media, headers={"Content-Disposition": f'inline; filename="{filename}"'})

    @app.get("/")
    def index() -> Response:
        # cache-bust the app assets with their modification time so a reload always gets the current code
        html = (web_dir / "index.html").read_text(encoding="utf-8")
        for name in ("app.js", "styles.css"):
            stamp = int((web_dir / name).stat().st_mtime)
            html = html.replace(f"/static/{name}\"", f"/static/{name}?v={stamp}\"")
        return Response(html, media_type="text/html", headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=str(web_dir)), name="static")
    return app


app = create_app() if os.environ.get("SOUTHBAY_WATER_NO_AUTOAPP") is None else None


def main() -> None:
    import uvicorn
    cfg = load_config()
    uvicorn.run("src.server.app:app", host=cfg["host"], port=int(cfg["port"]), log_level="info")


if __name__ == "__main__":
    main()
