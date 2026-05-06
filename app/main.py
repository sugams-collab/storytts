"""
main.py - FastAPI entry point: API routes, background job management, SSE.
"""
from __future__ import annotations

import asyncio
import json
import re
import uuid
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.requests import Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, HttpUrl

from app.audio_utils import merge_wavs_to_mp3
from app.config import OUTPUT_DIR
from app.logger import (
    cleanup_job,
    get_job_logger,
    iter_job_logs,
    mark_job_done,
)
from app.scraper import iter_pages
from app.text_processor import detect_language, extract_text, split_into_chunks
from app.tts_engine import TTSEngine, get_hw_info, synthesize_page

# ── App init ──────────────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).resolve().parent.parent
TEMPLATES  = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(title="Story→Audiobook", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.mount("/output", StaticFiles(directory=str(OUTPUT_DIR)), name="output")

# ── In-memory job store ───────────────────────────────────────────────────────
# job_id → {status, page, total, engine, lang, error, output_path}
_jobs: dict[str, dict] = {}


# ─────────────────────────────────────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────────────────────────────────────

class GenerateRequest(BaseModel):
    url: str
    tts_engine: TTSEngine = TTSEngine.AUTO


# ─────────────────────────────────────────────────────────────────────────────
# Frontend
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    hw = get_hw_info()
    # Use keyword-arg API (Starlette ≥0.28) to avoid unhashable dict cache key bug
    return TEMPLATES.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "recommended_engine": hw["recommended_engine"].value,  # plain str
            "has_cuda": hw["cuda"],
            "has_mps": hw["mps"],
            "ram_gb": round(hw["ram_gb"], 1),
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# API routes
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/hw")
async def hardware_info():
    """Return detected hardware capabilities."""
    return get_hw_info()


@app.post("/api/generate")
async def generate(req: GenerateRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {
        "status": "Queued",
        "page": 0,
        "total": 0,
        "engine": req.tts_engine,
        "lang": "en",
        "error": None,
        "output_path": None,
        "url": str(req.url),
    }
    background_tasks.add_task(_run_job, job_id, str(req.url), req.tts_engine)
    return {"job_id": job_id}


@app.get("/api/status/{job_id}")
async def status(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


@app.get("/api/logs/{job_id}")
async def log_stream(job_id: str):
    """Server-Sent Events stream of live job logs."""
    if job_id not in _jobs:
        raise HTTPException(404, "Job not found")

    async def event_generator():
        async for line in iter_job_logs(job_id):
            yield f"data: {json.dumps(line)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/download/{job_id}")
async def download(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job["status"] != "Done":
        raise HTTPException(400, f"Job not complete (status={job['status']})")
    mp3_path = job.get("output_path")
    if not mp3_path or not Path(mp3_path).exists():
        raise HTTPException(404, "Output file not found")
    return FileResponse(
        mp3_path,
        media_type="audio/mpeg",
        filename=Path(mp3_path).name,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Background job
# ─────────────────────────────────────────────────────────────────────────────

def _safe_name(url: str) -> str:
    """Convert URL to a safe directory name."""
    name = re.sub(r"https?://", "", url)
    name = re.sub(r"[^\w\-]", "_", name)
    return name[:60]


async def _run_job(job_id: str, url: str, engine: TTSEngine):
    job = _jobs[job_id]
    logger = get_job_logger(job_id)

    try:
        job["status"] = "Scraping"
        logger.info("=== Job %s started | URL: %s | Engine: %s ===", job_id, url, engine)

        story_dir = OUTPUT_DIR / f"{_safe_name(url)}_{job_id[:8]}"
        story_dir.mkdir(parents=True, exist_ok=True)

        # ── Phase 1: Scrape pages ────────────────────────────────────────────
        pages: list[tuple[int, str]] = []   # (page_num, text)
        async for page_num, page_url, soup in iter_pages(url, job_logger=logger):
            html = str(soup)
            text = extract_text(html, page_url)
            if not text.strip():
                logger.warning("Page %d: no text extracted – skipping.", page_num)
                continue
            pages.append((page_num, text))
            job["total"] = page_num  # will be updated as we go

        if not pages:
            raise RuntimeError("No text could be extracted from any page.")

        total_pages = len(pages)
        job["total"] = total_pages
        logger.info("Scraping done. %d pages with text.", total_pages)

        # Detect language from first page
        lang = detect_language(pages[0][1])
        job["lang"] = lang
        logger.info("Detected language: %s", lang)

        # ── Phase 2: TTS per page ────────────────────────────────────────────
        job["status"] = "Processing"
        wav_files: list[Path] = []

        for page_num, text in pages:
            job["page"] = page_num
            logger.info("TTS processing page %d/%d …", page_num, total_pages)

            chunks = split_into_chunks(text)
            page_wav = story_dir / f"page_{page_num:04d}.wav"

            try:
                # Run blocking TTS in a thread pool to keep event loop free
                await asyncio.get_event_loop().run_in_executor(
                    None,
                    synthesize_page,
                    chunks,
                    lang,
                    page_wav,
                    engine,
                    logger,
                )
                wav_files.append(page_wav)
            except Exception as exc:
                logger.error("Page %d TTS failed: %s – skipping.", page_num, exc)

        if not wav_files:
            raise RuntimeError("TTS produced no audio for any page.")

        # ── Phase 3: Merge ───────────────────────────────────────────────────
        job["status"] = "Merging"
        logger.info("Merging %d WAV files…", len(wav_files))
        output_mp3 = story_dir / "full_story.mp3"

        await asyncio.get_event_loop().run_in_executor(
            None, merge_wavs_to_mp3, wav_files, output_mp3
        )

        job["output_path"] = str(output_mp3)
        job["status"] = "Done"
        logger.info("=== Job %s DONE → %s ===", job_id, output_mp3)

    except Exception as exc:
        job["status"] = "Error"
        job["error"] = str(exc)
        logger.error("Job %s failed: %s", job_id, exc, exc_info=True)

    finally:
        mark_job_done(job_id)
