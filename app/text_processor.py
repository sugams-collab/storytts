"""
text_processor.py - Extract, clean, detect language, and chunk page text.
Uses trafilatura for main-content extraction, langdetect for language ID.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional

import trafilatura
from bs4 import BeautifulSoup
from langdetect import detect, LangDetectException

from app.config import CHUNK_MAX_CHARS
from app.logger import get_logger

log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Main content extraction
# ─────────────────────────────────────────────────────────────────────────────

def extract_text(html: str, url: str = "") -> str:
    """
    Extract the main story text from raw HTML.
    Strategy: trafilatura first (best accuracy), fallback to BS4 heuristics.
    """
    # trafilatura
    text = trafilatura.extract(
        html,
        url=url,
        include_comments=False,
        include_tables=False,
        no_fallback=False,
        favor_recall=True,
    )
    if text and len(text.strip()) > 100:
        return _clean(text)

    # BS4 fallback – grab all <p> tags inside likely story containers
    soup = BeautifulSoup(html, "html.parser")
    # Remove noise
    for tag in soup(["script", "style", "nav", "header", "footer",
                     "aside", "form", "noscript", "iframe"]):
        tag.decompose()

    # Priority: article / main / div[class~=content|story|chapter|post]
    containers = (
        soup.find("article")
        or soup.find("main")
        or soup.find(attrs={"class": re.compile(r"(content|story|chapter|post|text)", re.I)})
        or soup.body
    )
    if containers:
        paragraphs = containers.find_all("p")
        text = "\n".join(p.get_text(separator=" ") for p in paragraphs)
    else:
        text = soup.get_text(separator="\n")

    return _clean(text)


# ─────────────────────────────────────────────────────────────────────────────
# Text Cleaning
# ─────────────────────────────────────────────────────────────────────────────

_WHITESPACE_RE  = re.compile(r"[ \t]+")
_BLANK_LINE_RE  = re.compile(r"\n{3,}")
_HTML_ENT_RE    = re.compile(r"&[a-z]+;|&#\d+;|&#x[0-9a-f]+;", re.I)
_BRACKET_RE     = re.compile(r"\[.*?\]|\{.*?\}")   # [ad] {sponsored}


def _clean(text: str) -> str:
    """Normalize & clean extracted text."""
    # Unicode normalization
    text = unicodedata.normalize("NFKC", text)
    # Remove HTML entities missed by BS4
    text = _HTML_ENT_RE.sub(" ", text)
    # Remove bracketed noise
    text = _BRACKET_RE.sub("", text)
    # Collapse horizontal whitespace
    text = _WHITESPACE_RE.sub(" ", text)
    # Normalize line-breaks
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _BLANK_LINE_RE.sub("\n\n", text)
    return text.strip()


# ─────────────────────────────────────────────────────────────────────────────
# Language Detection
# ─────────────────────────────────────────────────────────────────────────────

def detect_language(text: str) -> str:
    """
    Returns 'hi' for Hindi, 'en' for English, defaults to 'en' on error.
    Samples the first 1000 chars for speed.
    """
    sample = text[:1000]
    try:
        lang = detect(sample)
        if lang in ("hi", "mr", "ne"):   # Devanagari-script languages
            return "hi"
        return "en"
    except LangDetectException:
        log.warning("Language detection failed; defaulting to 'en'.")
        return "en"


# ─────────────────────────────────────────────────────────────────────────────
# TTS Chunking
# ─────────────────────────────────────────────────────────────────────────────

# Sentence boundary: split after . ! ? followed by whitespace or end
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?।])\s+")


def split_into_chunks(text: str, max_chars: int = CHUNK_MAX_CHARS) -> list[str]:
    """
    Split text into TTS-safe chunks, respecting sentence boundaries.
    Chunks are <= max_chars characters.
    """
    sentences = _SENT_SPLIT_RE.split(text)
    chunks: list[str] = []
    current = ""

    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        # If a single sentence is already too long, sub-split by comma / word
        if len(sent) > max_chars:
            sub_chunks = _force_split(sent, max_chars)
            for sc in sub_chunks:
                if current:
                    chunks.append(current)
                    current = ""
                chunks.append(sc)
            continue

        if current and len(current) + 1 + len(sent) > max_chars:
            chunks.append(current)
            current = sent
        else:
            current = (current + " " + sent).strip() if current else sent

    if current:
        chunks.append(current)

    return [c for c in chunks if c.strip()]


def _force_split(text: str, max_chars: int) -> list[str]:
    """Hard-split oversized text by word boundaries."""
    words = text.split()
    parts: list[str] = []
    buf = ""
    for w in words:
        if buf and len(buf) + 1 + len(w) > max_chars:
            parts.append(buf)
            buf = w
        else:
            buf = (buf + " " + w).strip() if buf else w
    if buf:
        parts.append(buf)
    return parts
