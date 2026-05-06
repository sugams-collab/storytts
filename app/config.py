"""
config.py - Global configuration and path settings
"""
import os
from pathlib import Path

# ── Base Directories ──────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "output"
LOGS_DIR   = BASE_DIR / "logs"
MODELS_DIR = BASE_DIR / "models"          # Piper model files live here

# ── Auto-create required directories ─────────────────────────────────────────
for _d in (OUTPUT_DIR, LOGS_DIR, MODELS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ── Scraping ──────────────────────────────────────────────────────────────────
MAX_PAGES           = 100
REQUEST_TIMEOUT     = 30          # seconds
REQUEST_RETRIES     = 3
REQUEST_RETRY_WAIT  = 2           # seconds (exponential back-off base)
USER_AGENT          = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# ── Text Processing ───────────────────────────────────────────────────────────
CHUNK_MAX_CHARS = 600             # max chars per TTS chunk (sentence-aware)

# ── Piper TTS Model Paths ─────────────────────────────────────────────────────
# Place downloaded .onnx + .json files in models/
PIPER_MODELS = {
    "en": {
        "model": str(MODELS_DIR / "en_US-lessac-medium.onnx"),
        "config": str(MODELS_DIR / "en_US-lessac-medium.onnx.json"),
    },
    "hi": {
        "model": str(MODELS_DIR / "hi_IN-rohan-medium.onnx"),
        "config": str(MODELS_DIR / "hi_IN-rohan-medium.onnx.json"),
    },
}

# ── Coqui TTS Model Names ─────────────────────────────────────────────────────
COQUI_MODELS = {
    "en": "tts_models/en/ljspeech/tacotron2-DDC",
    "hi": "tts_models/hi/cv/vits",
}

# ── Audio ─────────────────────────────────────────────────────────────────────
AUDIO_SAMPLE_RATE   = 22050
MERGED_MP3_BITRATE  = "128k"

# ── Hardware thresholds for Coqui auto-selection ──────────────────────────────
COQUI_MIN_RAM_GB    = 4           # system RAM >= this → Coqui eligible
COQUI_MIN_VRAM_GB   = 2           # GPU VRAM >= this  → Coqui strongly preferred
