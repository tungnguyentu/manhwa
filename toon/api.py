"""
FastAPI REST backend for the Toon translation pipeline.

Runs on port 7861 (Gradio stays on 7860).
Used by the Chrome extension to trigger processing and fetch translations.

Start with:
    uvicorn toon.api:app --port 7861 --reload
or:
    toon api
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import json

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from toon.config import get_settings
from toon import db as db_module

app = FastAPI(title="Toon API", version="1.0.0")

# Allow Chrome extensions and localhost to call the API
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"chrome-extension://.*|http://localhost.*|http://127\.0\.0\.1.*",
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# In-memory job status tracker: chapter_id → status
_jobs: dict[int, dict[str, Any]] = {}

# SSE event queues: chapter_id → list of subscriber queues
_sse_queues: dict[int, list[asyncio.Queue]] = {}


async def _push_event(chapter_id: int, event: dict) -> None:
    """Push an SSE event to all subscribers for a chapter."""
    for q in _sse_queues.get(chapter_id, []):
        await q.put(event)


def _init() -> Any:
    settings = get_settings()
    db_module.set_db_path(settings.data_dir / "toon.db")
    db_module.init_db()
    return settings


# ── Models ────────────────────────────────────────────────────────────────────

class ProcessRequest(BaseModel):
    url: str                        # Chapter page URL
    image_urls: list[str] = []      # Optional: image URLs already extracted from DOM
    series_slug: str = ""           # Optional: override auto-detected slug
    source_lang: str = "en"         # "en" or "ko"
    force_reextract: bool = False


class JobStatus(BaseModel):
    chapter_id: int
    status: str   # "queued" | "scraping" | "extracting" | "translating" | "done" | "error"
    message: str = ""
    translated: int = 0
    total: int = 0


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "toon-api"}


# ── Series ────────────────────────────────────────────────────────────────────

@app.get("/api/series")
def list_series() -> list[dict]:
    _init()
    with db_module.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, slug, title, source_language FROM series ORDER BY slug"
        ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/series/{slug}/chapters")
def list_chapters(slug: str) -> list[dict]:
    _init()
    series_id = db_module.get_series_id(slug)
    if not series_id:
        raise HTTPException(status_code=404, detail=f"Series '{slug}' not found")
    chapters = db_module.list_chapters(series_id)
    return chapters


@app.get("/api/profiles/{slug}")
def get_profiles(slug: str) -> list[dict]:
    _init()
    series_id = db_module.get_series_id(slug)
    if not series_id:
        raise HTTPException(status_code=404, detail=f"Series '{slug}' not found")
    return db_module.get_character_profiles(series_id)


# ── Translations ──────────────────────────────────────────────────────────────

@app.get("/api/chapter/{chapter_id}/translations")
def get_translations(chapter_id: int, target_lang: str = "vi") -> dict:
    """Return translated dialogues grouped by panel_index."""
    _init()
    rows = db_module.get_translations_for_chapter(chapter_id, target_lang)
    if not rows:
        raise HTTPException(status_code=404, detail="No translations found for this chapter")

    panels: dict[int, list[dict]] = {}
    for r in rows:
        idx = r["panel_index"]
        panels.setdefault(idx, []).append({
            "speaker": r["speaker"],
            "original": r["original_text"],
            "translated": r.get("translated_text") or "",
            "scene_description": r.get("scene_description") or "",
        })

    return {
        "chapter_id": chapter_id,
        "target_lang": target_lang,
        "panels": panels,
        "total_dialogues": len(rows),
        "translated_count": sum(1 for r in rows if r.get("translated_text")),
    }


@app.get("/api/chapter/{chapter_id}/stream")
async def stream_chapter(chapter_id: int) -> StreamingResponse:
    """
    Server-Sent Events stream for real-time pipeline progress.
    Connect with: new EventSource('/api/chapter/{id}/stream')
    Events: { stage, detail, translated?, total? }
    Closes when stage == "done" or "error".
    """
    q: asyncio.Queue = asyncio.Queue()
    _sse_queues.setdefault(chapter_id, []).append(q)

    # If already done, send that immediately
    job = _jobs.get(chapter_id)
    if job and job.get("status") in ("done", "error"):
        await q.put({**job, "stage": job["status"]})
        await q.put(None)  # sentinel

    async def generator():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=30)
                except asyncio.TimeoutError:
                    yield "event: ping\ndata: {}\n\n"
                    continue
                if event is None:
                    break
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("stage") in ("done", "error"):
                    break
        finally:
            subs = _sse_queues.get(chapter_id, [])
            if q in subs:
                subs.remove(q)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/chapter/{chapter_id}/status")
def get_job_status(chapter_id: int) -> dict:
    """Return processing job status for a chapter."""
    _init()
    if chapter_id in _jobs:
        return _jobs[chapter_id]

    # Check DB state
    with db_module.get_conn() as conn:
        row = conn.execute("SELECT * FROM chapters WHERE id=?", (chapter_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Chapter not found")

    row = dict(row)
    if row.get("translated_at"):
        rows = db_module.get_translations_for_chapter(chapter_id)
        return {
            "chapter_id": chapter_id,
            "status": "done",
            "translated": sum(1 for r in rows if r.get("translated_text")),
            "total": len(rows),
        }
    if row.get("extracted_at"):
        return {"chapter_id": chapter_id, "status": "extracted", "message": "Extraction done, not yet translated"}
    if row.get("scraped_at"):
        return {"chapter_id": chapter_id, "status": "scraped", "message": "Images downloaded, not yet extracted"}
    return {"chapter_id": chapter_id, "status": "pending", "message": "Not yet processed"}


# ── Learn (build style guide from Vietnamese chapters) ───────────────────────

class LearnRequest(BaseModel):
    url: str
    image_urls: list[str] = []
    series_slug: str = ""
    force_reextract: bool = False


@app.post("/api/learn")
async def learn_chapter(req: LearnRequest, background_tasks: BackgroundTasks) -> dict:
    """
    Trigger the learn pipeline (scrape → extract → profile → style guide) for a Vietnamese chapter.
    Returns immediately with chapter_id; poll /api/chapter/{id}/status for progress.
    """
    settings = _init()

    from toon.scraper.downloader import url_to_slug
    slug = req.series_slug or url_to_slug(req.url)

    import re
    m = re.search(r"chapter[-_]?(\d+)", req.url, re.IGNORECASE)
    chapter_num = int(m.group(1)) if m else 1

    series_id = db_module.upsert_series(slug, url_base=req.url, source_language="vi")
    chapter_id = db_module.upsert_chapter(series_id, chapter_num, req.url)

    # Check if already extracted (skip unless force)
    with db_module.get_conn() as conn:
        row = conn.execute("SELECT extracted_at FROM chapters WHERE id=?", (chapter_id,)).fetchone()
    if row and row["extracted_at"] and not req.force_reextract:
        return {
            "chapter_id": chapter_id,
            "status": "done",
            "message": "Already extracted. Use force_reextract=true to redo.",
        }

    _jobs[chapter_id] = {"chapter_id": chapter_id, "status": "queued", "message": "Queued", "translated": 0, "total": 0}
    background_tasks.add_task(
        _run_learn_pipeline, chapter_id, series_id, slug, chapter_num, req, settings
    )
    return {"chapter_id": chapter_id, "status": "queued", "message": "Learning started"}


async def _run_learn_pipeline(
    chapter_id: int,
    series_id: int,
    slug: str,
    chapter_num: int,
    req: LearnRequest,
    settings: Any,
) -> None:
    """Run scrape → extract → profile → style guide pipeline."""
    from toon.ai_client import AIClient

    client = AIClient(settings)

    async def _update(status: str, message: str, **extra) -> None:
        _jobs[chapter_id].update({"status": status, "message": message, **extra})
        await _push_event(chapter_id, {"stage": status, "message": message, **extra})

    try:
        await _update("scraping", "Downloading images…")

        if req.image_urls:
            from toon.scraper.downloader import scrape_chapter_with_urls
            await scrape_chapter_with_urls(slug, chapter_num, req.image_urls, req.url, settings)
        else:
            from toon.scraper.downloader import scrape_chapter
            await scrape_chapter(slug, chapter_num, req.url, settings)

        db_module.mark_chapter_scraped(chapter_id)
        await _update("extracting", "Running OCR…")

        from toon.extractor.vision import extract_chapter
        total = await extract_chapter(
            chapter_id, slug, chapter_num, client, settings, db_module,
            source_lang="vi", skip_attribution=True,
        )
        db_module.mark_chapter_extracted(chapter_id)

        await _update("profiling", "Building character profiles…", total=total)

        from toon.profiler.builder import build_profiles
        await build_profiles(series_id, client, db_module, rebuild=False)

        await _update("learning", "Analyzing translation style…")

        from toon.learner.style_learner import learn_style_from_series
        await learn_style_from_series(series_id, series_id, slug, client, db_module)

        await _update("done", "Style guide saved", total=total)
        await _push_event(chapter_id, None)

    except Exception as exc:
        await _update("error", str(exc))
        await _push_event(chapter_id, None)
        raise


# ── Process (trigger full pipeline) ──────────────────────────────────────────

@app.post("/api/process")
async def process_chapter(req: ProcessRequest, background_tasks: BackgroundTasks) -> dict:
    """
    Trigger the full pipeline for a chapter URL.
    Returns immediately with chapter_id; poll /api/chapter/{id}/status for progress.
    """
    settings = _init()

    from toon.scraper.downloader import url_to_slug
    slug = req.series_slug or url_to_slug(req.url)

    import re
    m = re.search(r"chapter[-_]?(\d+)", req.url, re.IGNORECASE)
    chapter_num = int(m.group(1)) if m else 1

    series_id = db_module.upsert_series(slug, url_base=req.url, source_language=req.source_lang)
    chapter_id = db_module.upsert_chapter(series_id, chapter_num, req.url)

    # Check if already translated (skip unless force)
    with db_module.get_conn() as conn:
        row = conn.execute("SELECT translated_at FROM chapters WHERE id=?", (chapter_id,)).fetchone()
    if row and row["translated_at"] and not req.force_reextract:
        rows = db_module.get_translations_for_chapter(chapter_id)
        return {
            "chapter_id": chapter_id,
            "status": "done",
            "message": "Already translated. Use force_reextract=true to redo.",
            "translated": sum(1 for r in rows if r.get("translated_text")),
            "total": len(rows),
        }

    _jobs[chapter_id] = {"chapter_id": chapter_id, "status": "queued", "message": "Queued", "translated": 0, "total": 0}
    background_tasks.add_task(
        _run_pipeline, chapter_id, series_id, slug, chapter_num, req, settings
    )
    return {"chapter_id": chapter_id, "status": "queued", "message": "Processing started"}


async def _run_pipeline(
    chapter_id: int,
    series_id: int,
    slug: str,
    chapter_num: int,
    req: ProcessRequest,
    settings: Any,
) -> None:
    """Run scrape → extract → translate pipeline as a background task."""
    from toon.ai_client import AIClient

    client = AIClient(settings)

    async def _update(status: str, message: str, **extra) -> None:
        _jobs[chapter_id].update({"status": status, "message": message, **extra})
        await _push_event(chapter_id, {"stage": status, "message": message, **extra})

    async def _ocr_progress(stage: str, detail: str) -> None:
        friendly = {
            "ocr_start": f"Running OCR on {detail}…",
            "ocr_panel": f"OCR {detail} panels done",
            "attribution_start": "Attributing speakers…",
            "attribution_panel": f"Attribution {detail} panels done",
        }.get(stage, detail)
        _jobs[chapter_id]["message"] = friendly
        await _push_event(chapter_id, {"stage": stage, "detail": detail, "message": friendly})

    try:
        # Step 1: Scrape
        await _update("scraping", "Downloading images…")

        if req.image_urls:
            from toon.scraper.downloader import scrape_chapter_with_urls
            await scrape_chapter_with_urls(slug, chapter_num, req.image_urls, req.url, settings)
        else:
            from toon.scraper.downloader import scrape_chapter
            await scrape_chapter(slug, chapter_num, req.url, settings)

        db_module.mark_chapter_scraped(chapter_id)

        # Step 2: Extract (parallel cloud OCR + speaker attribution)
        await _update("extracting", "Starting OCR…")

        from toon.extractor.vision import extract_chapter
        total = await extract_chapter(
            chapter_id, slug, chapter_num, client, settings, db_module,
            source_lang=req.source_lang,
            progress_cb=_ocr_progress,
        )
        db_module.mark_chapter_extracted(chapter_id)
        _jobs[chapter_id]["total"] = total

        # Step 3: Translate
        await _update("translating", "Translating with character voice profiles…", total=total)

        profiles = db_module.get_character_profiles(series_id)

        async def _translate_progress(done: int, total_d: int) -> None:
            msg = f"Translating… {done}/{total_d} dialogues"
            _jobs[chapter_id]["message"] = msg
            await _push_event(chapter_id, {"stage": "translating", "message": msg, "translated": done, "total": total_d})

        from toon.translator.engine import translate_chapter
        translated = await translate_chapter(
            chapter_id, client, profiles, db_module,
            source_lang=req.source_lang, series_id=series_id,
            progress_cb=_translate_progress,
        )
        db_module.mark_chapter_translated(chapter_id)

        await _update("done", "Complete", translated=translated, total=total)
        await _push_event(chapter_id, None)  # close SSE streams

    except Exception as exc:
        await _update("error", str(exc))
        await _push_event(chapter_id, None)
        raise
