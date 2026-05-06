# Story→Audiobook Converter

A production-ready, locally hosted FastAPI application that seamlessly scrapes multi-page web stories and converts them into audiobooks (MP3). 

This tool provides a beautiful frontend for users to submit URLs. Behind the scenes, it intelligently traverses pagination, extracts core text, detects language, and synthesizes audio using either lightweight (Piper) or high-fidelity (Coqui) Text-To-Speech engines. 

## Features

- **Automated Web Scraping:** Extracts clean text from story URLs and automatically follows pagination links.
- **Intelligent Text Processing:** Detects language automatically and chunks text into optimal sizes for TTS generation.
- **Dual TTS Engine Architecture:** 
  - **Piper TTS:** Lightweight and ultra-fast for CPU inference.
  - **Coqui TTS:** High-fidelity TTS for premium quality (supports GPU/CUDA inference).
- **Hardware-Aware Processing:** Detects available hardware (CPU, CUDA, MPS) to recommend the best TTS engine and optimize resource usage.
- **Interactive UI with Live Progress:** Real-time job status tracking and Server-Sent Events (SSE) live log streaming right in your browser.
- **Resilient Background Jobs:** In-memory queueing with asynchronous job management, ensuring the UI stays responsive.

## Tech Stack

- **Backend:** FastAPI, Python 3.9+
- **Frontend:** HTML5, JS, TailwindCSS/Vanilla CSS (Jinja2 Templates)
- **Scraping:** Trafilatura, BeautifulSoup4, HTTPX
- **Text & Audio Processing:** Langdetect, Pydub, FFMPEG
- **TTS Engines:** Piper TTS, Coqui TTS

## Prerequisites

1. **Python 3.9+**
2. **FFMPEG:** Required by `pydub` for merging and converting WAV files to MP3.
   - *Ubuntu/Debian:* `sudo apt install ffmpeg`
   - *macOS:* `brew install ffmpeg`
   - *Windows:* Download from [ffmpeg.org](https://ffmpeg.org/) and add to your PATH.

## Installation

1. **Clone the repository and enter the directory:**
   ```bash
   git clone <repository_url>
   cd tts
   ```

2. **Create a virtual environment (recommended):**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **(Optional) Install TTS Engines manually:**
   The application tries to use the installed TTS engines. To ensure they are available:
   - For Piper (CPU friendly): `pip install piper-tts`
   - For Coqui (High Fidelity): `pip install TTS` 
   *(Note: For GPU acceleration with Coqui, ensure you have the appropriate PyTorch build with CUDA installed).*

## Usage

1. **Start the FastAPI server:**
   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```
   *For hot-reloading during development, use the `--reload` flag.*

2. **Open your browser:**
   Navigate to [http://localhost:8000](http://localhost:8000).

3. **Generate an Audiobook:**
   - Enter a URL for a web story.
   - Select your preferred TTS Engine (or leave it as `Auto`).
   - Click "Generate".
   - Watch the live logs and download the final `full_story.mp3` once processing completes.

## Directory Structure

```text
├── app/
│   ├── main.py           # FastAPI entry point, routes, and background tasks
│   ├── config.py         # Configuration settings and paths
│   ├── scraper.py        # Web scraping and pagination logic
│   ├── text_processor.py # Text extraction, chunking, and language detection
│   ├── tts_engine.py     # TTS wrapper (Piper/Coqui) and hardware detection
│   ├── audio_utils.py    # Audio merging to MP3 using Pydub
│   └── logger.py         # SSE log streaming utilities
├── static/               # CSS, JS, and image assets
├── templates/            # HTML Jinja2 templates (index.html)
├── output/               # Generated MP3 audiobooks
├── logs/                 # Job log files
├── models/               # Cached TTS models (if applicable)
└── requirements.txt      # Python dependencies
```

## Troubleshooting

- **Audio Merge Fails:** Ensure `ffmpeg` is correctly installed and available in your system's PATH.
- **Scraping Issues:** If the application fails to extract text, the website might have strong anti-bot protections or highly irregular markup.
- **TTS Errors:** If Coqui or Piper fails, the system tries to fall back safely. Check the live server logs on the UI for specific synthesis errors.