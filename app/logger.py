"""
logger.py - Structured logging + per-job SSE log queue
"""
import logging
import asyncio
import sys
from collections import defaultdict
from pathlib import Path

from app.config import LOGS_DIR

# ── File handler shared by all loggers ────────────────────────────────────────
_LOG_FILE = LOGS_DIR / "app.log"

_file_handler = logging.FileHandler(_LOG_FILE, encoding="utf-8")
_file_handler.setFormatter(
    logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
)

_stream_handler = logging.StreamHandler(sys.stdout)
_stream_handler.setFormatter(
    logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
    )
)

# ── Per-job in-memory log queues (for SSE streaming) ─────────────────────────
# job_id → asyncio.Queue of log strings
_job_queues: dict[str, asyncio.Queue] = defaultdict(lambda: asyncio.Queue(maxsize=500))
_job_done:   dict[str, bool]          = {}


def get_logger(name: str) -> logging.Logger:
    """Return a named logger writing to file + stdout."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.addHandler(_file_handler)
        logger.addHandler(_stream_handler)
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
    return logger


class JobLogHandler(logging.Handler):
    """Pushes log records into the job-specific asyncio queue."""

    def __init__(self, job_id: str):
        super().__init__()
        self.job_id = job_id
        self.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                                            datefmt="%H:%M:%S"))

    def emit(self, record: logging.LogRecord):
        msg = self.format(record)
        q = _job_queues[self.job_id]
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            pass  # drop if queue is full


def get_job_logger(job_id: str, name: str = "job") -> logging.Logger:
    """Return a logger that writes to the file AND the SSE queue for this job."""
    logger = logging.getLogger(f"{name}.{job_id}")
    if not logger.handlers:
        logger.addHandler(_file_handler)
        logger.addHandler(_stream_handler)
        logger.addHandler(JobLogHandler(job_id))
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
    return logger


async def iter_job_logs(job_id: str):
    """Async generator yielding log lines for SSE. Ends when job is marked done."""
    q = _job_queues[job_id]
    while True:
        if not q.empty():
            yield await q.get()
        elif _job_done.get(job_id):
            break
        else:
            await asyncio.sleep(0.1)


def mark_job_done(job_id: str):
    _job_done[job_id] = True


def cleanup_job(job_id: str):
    _job_queues.pop(job_id, None)
    _job_done.pop(job_id, None)
