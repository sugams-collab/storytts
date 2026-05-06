"""
tts_engine.py - Dual-engine TTS wrapper (Piper + Coqui) with:
  • Hardware detection at startup
  • Per-language model selection
  • Automatic Coqui→Piper fallback on OOM
  • Chunk-level WAV generation
"""
from __future__ import annotations

import io
import os
import subprocess
import tempfile
import wave
from enum import Enum
from pathlib import Path
from typing import Optional

from app.config import (
    AUDIO_SAMPLE_RATE,
    COQUI_MIN_RAM_GB,
    COQUI_MIN_VRAM_GB,
    COQUI_MODELS,
    PIPER_MODELS,
)
from app.logger import get_logger

log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Engine enum
# ─────────────────────────────────────────────────────────────────────────────

class TTSEngine(str, Enum):
    AUTO  = "auto"
    PIPER = "piper"
    COQUI = "coqui"


# ─────────────────────────────────────────────────────────────────────────────
# Hardware detection
# ─────────────────────────────────────────────────────────────────────────────

def detect_capabilities() -> dict:
    """Return a dict with hardware info and recommended engine."""
    info: dict = {
        "cuda": False,
        "mps": False,
        "ram_gb": 0.0,
        "vram_gb": 0.0,
        "recommended_engine": TTSEngine.PIPER,
    }

    # RAM
    try:
        import psutil
        info["ram_gb"] = psutil.virtual_memory().total / 1e9
    except ImportError:
        pass

    # CUDA / MPS / VRAM
    try:
        import torch
        if torch.cuda.is_available():
            info["cuda"] = True
            props = torch.cuda.get_device_properties(0)
            info["vram_gb"] = props.total_memory / 1e9
        elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            info["mps"] = True
    except Exception:
        pass

    has_gpu  = info["cuda"] or info["mps"]
    good_ram = info["ram_gb"] >= COQUI_MIN_RAM_GB
    good_vram = info["vram_gb"] >= COQUI_MIN_VRAM_GB

    if has_gpu and good_vram:
        info["recommended_engine"] = TTSEngine.COQUI
    elif good_ram and not has_gpu:
        # CPU Coqui is viable on ≥ 4 GB RAM but slow; keep Piper as default
        info["recommended_engine"] = TTSEngine.PIPER
    else:
        info["recommended_engine"] = TTSEngine.PIPER

    log.info(
        "Hardware: RAM=%.1f GB | CUDA=%s | VRAM=%.1f GB | MPS=%s → recommended=%s",
        info["ram_gb"], info["cuda"], info["vram_gb"], info["mps"],
        info["recommended_engine"],
    )
    return info


# Singleton
_hw: Optional[dict] = None

def get_hw_info() -> dict:
    global _hw
    if _hw is None:
        _hw = detect_capabilities()
    return _hw


def resolve_engine(requested: TTSEngine) -> TTSEngine:
    """Translate AUTO to the hardware-recommended engine."""
    if requested == TTSEngine.AUTO:
        return get_hw_info()["recommended_engine"]
    return requested


# ─────────────────────────────────────────────────────────────────────────────
# Piper TTS
# ─────────────────────────────────────────────────────────────────────────────

class PiperEngine:
    """
    Wraps the piper-tts Python package (new API: synthesize returns Iterable[AudioChunk])
    with a CLI fallback for older installs.
    """

    def synthesize_chunk(self, text: str, lang: str, output_wav: Path) -> None:
        model_cfg = PIPER_MODELS.get(lang) or PIPER_MODELS["en"]
        model_path = model_cfg["model"]
        config_path = model_cfg.get("config", "")

        if not Path(model_path).exists():
            raise FileNotFoundError(
                f"Piper model not found: {model_path}\n"
                "Download from https://huggingface.co/rhasspy/piper-voices"
            )

        try:
            self._synthesize_python(text, model_path, config_path, output_wav)
        except ImportError:
            self._synthesize_cli(text, model_path, config_path, output_wav)

    # ── Python package (piper-tts new API) ───────────────────────────────────
    def _synthesize_python(
        self, text: str, model_path: str, config_path: str, output_wav: Path
    ) -> None:
        from piper import PiperVoice  # type: ignore

        cfg_arg = config_path if (config_path and Path(config_path).exists()) else None
        voice = PiperVoice.load(model_path, config_path=cfg_arg)

        # New API: synthesize() → Iterable[AudioChunk]
        # Each AudioChunk carries sample_rate, sample_width, sample_channels + audio bytes
        chunks = list(voice.synthesize(text))

        if not chunks:
            raise RuntimeError("Piper returned no audio chunks.")

        first = chunks[0]
        sample_rate     = first.sample_rate
        sample_width    = first.sample_width      # bytes (2 = 16-bit)
        sample_channels = first.sample_channels   # 1 = mono

        with wave.open(str(output_wav), "wb") as wf:
            wf.setnchannels(sample_channels)
            wf.setsampwidth(sample_width)
            wf.setframerate(sample_rate)
            for chunk in chunks:
                wf.writeframes(chunk.audio_int16_bytes)

    # ── CLI binary fallback ───────────────────────────────────────────────────
    def _synthesize_cli(
        self, text: str, model_path: str, config_path: str, output_wav: Path
    ) -> None:
        cmd = ["piper", "--model", model_path, "--output_file", str(output_wav)]
        if config_path and Path(config_path).exists():
            cmd += ["--config", config_path]

        result = subprocess.run(
            cmd, input=text, capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Piper CLI error: {result.stderr[-300:]}")


# ─────────────────────────────────────────────────────────────────────────────
# Coqui TTS
# ─────────────────────────────────────────────────────────────────────────────

class CoquiEngine:
    """
    Wraps the TTS package (Coqui).
    Models are downloaded on first use to ~/.local/share/tts/.
    """

    _instances: dict[str, object] = {}

    def _get_tts(self, lang: str):
        if lang in self._instances:
            return self._instances[lang]

        from TTS.api import TTS  # type: ignore  # lazy import
        model_name = COQUI_MODELS.get(lang) or COQUI_MODELS["en"]

        hw = get_hw_info()
        use_gpu = hw["cuda"] or hw["mps"]

        log.info("Loading Coqui model '%s' (gpu=%s)…", model_name, use_gpu)
        tts = TTS(model_name=model_name, progress_bar=False, gpu=use_gpu)
        self._instances[lang] = tts
        return tts

    def synthesize_chunk(self, text: str, lang: str, output_wav: Path) -> None:
        tts = self._get_tts(lang)
        tts.tts_to_file(text=text, file_path=str(output_wav))


# ─────────────────────────────────────────────────────────────────────────────
# Unified synthesizer (with fallback)
# ─────────────────────────────────────────────────────────────────────────────

_piper = PiperEngine()
_coqui = CoquiEngine()


def synthesize_chunk(
    text: str,
    lang: str,
    output_wav: Path,
    engine: TTSEngine,
    job_logger=None,
) -> None:
    """
    Synthesize a single text chunk to a WAV file.
    Falls back to Piper if Coqui fails (e.g., OOM).
    """
    logger = job_logger or log
    actual_engine = resolve_engine(engine)

    def _run(eng: TTSEngine):
        if eng == TTSEngine.COQUI:
            _coqui.synthesize_chunk(text, lang, output_wav)
        else:
            _piper.synthesize_chunk(text, lang, output_wav)

    try:
        logger.debug("TTS [%s] chunk len=%d lang=%s", actual_engine, len(text), lang)
        _run(actual_engine)
    except MemoryError as e:
        if actual_engine == TTSEngine.COQUI:
            logger.warning("Coqui OOM – falling back to Piper for this chunk.")
            _run(TTSEngine.PIPER)
        else:
            raise
    except Exception as exc:
        if actual_engine == TTSEngine.COQUI:
            logger.warning("Coqui error (%s) – falling back to Piper.", exc)
            _run(TTSEngine.PIPER)
        else:
            raise


def synthesize_page(
    chunks: list[str],
    lang: str,
    page_wav: Path,
    engine: TTSEngine,
    job_logger=None,
) -> Path:
    """
    Synthesize all chunks for a single page, concatenating WAV data
    into one `page_wav` file.
    """
    import wave as wave_mod

    logger = job_logger or log
    tmp_dir = page_wav.parent / f"_tmp_{page_wav.stem}"
    tmp_dir.mkdir(exist_ok=True)

    chunk_wavs: list[Path] = []
    for i, chunk in enumerate(chunks):
        cw = tmp_dir / f"chunk_{i:04d}.wav"
        try:
            synthesize_chunk(chunk, lang, cw, engine, logger)
            if cw.exists() and cw.stat().st_size > 100:
                chunk_wavs.append(cw)
        except Exception as exc:
            logger.error("Chunk %d synthesis failed: %s", i, exc)

    if not chunk_wavs:
        raise RuntimeError("All chunks failed for this page.")

    # Concatenate WAV files at byte level (same sample rate / channels)
    _concat_wavs(chunk_wavs, page_wav)

    # Cleanup temp chunks
    for cw in chunk_wavs:
        cw.unlink(missing_ok=True)
    try:
        tmp_dir.rmdir()
    except OSError:
        pass

    logger.info("Page WAV written: %s", page_wav)
    return page_wav


def _concat_wavs(wavs: list[Path], output: Path) -> None:
    """Byte-level WAV concatenation – avoids re-encoding."""
    import wave as wave_mod

    with wave_mod.open(str(wavs[0]), "rb") as first:
        params = first.getparams()

    with wave_mod.open(str(output), "wb") as out_wf:
        out_wf.setparams(params)
        for wf_path in wavs:
            with wave_mod.open(str(wf_path), "rb") as in_wf:
                out_wf.writeframes(in_wf.readframes(in_wf.getnframes()))
