"""
audio_utils.py - Merge per-page WAV files into a single MP3 audiobook.
Primary: pydub (wraps ffmpeg).  Fallback: direct ffmpeg subprocess call.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from app.config import MERGED_MP3_BITRATE
from app.logger import get_logger

log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def merge_wavs_to_mp3(wav_files: list[Path], output_mp3: Path) -> Path:
    """
    Merge a list of WAV files (in order) into a single MP3.
    Returns the path to the created MP3.
    Raises RuntimeError if no WAV files given or all methods fail.
    """
    if not wav_files:
        raise RuntimeError("No WAV files provided for merging.")

    # Filter to files that actually exist and have content
    valid = [f for f in wav_files if f.exists() and f.stat().st_size > 100]
    if not valid:
        raise RuntimeError("All provided WAV files are missing or empty.")

    log.info("Merging %d WAV file(s) → %s", len(valid), output_mp3)

    try:
        _merge_pydub(valid, output_mp3)
    except Exception as exc:
        log.warning("pydub merge failed (%s); trying ffmpeg subprocess.", exc)
        try:
            _merge_ffmpeg(valid, output_mp3)
        except Exception as exc2:
            raise RuntimeError(f"Both merge strategies failed: {exc2}") from exc2

    log.info("Merge complete: %s (%.1f MB)", output_mp3, output_mp3.stat().st_size / 1e6)
    return output_mp3


# ─────────────────────────────────────────────────────────────────────────────
# Internal strategies
# ─────────────────────────────────────────────────────────────────────────────

def _merge_pydub(wav_files: list[Path], output_mp3: Path) -> None:
    from pydub import AudioSegment  # lazy import – optional dep

    combined = AudioSegment.empty()
    for wf in wav_files:
        seg = AudioSegment.from_wav(str(wf))
        combined += seg

    combined.export(
        str(output_mp3),
        format="mp3",
        bitrate=MERGED_MP3_BITRATE,
        tags={"album": "Audiobook", "genre": "Audiobook"},
    )


def _merge_ffmpeg(wav_files: list[Path], output_mp3: Path) -> None:
    """Use ffmpeg concat demuxer to merge without re-encoding each chunk."""
    # Write a concat list file next to the output
    list_file = output_mp3.with_suffix(".txt")
    try:
        with list_file.open("w", encoding="utf-8") as fh:
            for wf in wav_files:
                fh.write(f"file '{wf.resolve()}'\n")

        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(list_file),
            "-b:a", MERGED_MP3_BITRATE,
            str(output_mp3),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            raise RuntimeError(result.stderr[-500:])
    finally:
        if list_file.exists():
            list_file.unlink()
