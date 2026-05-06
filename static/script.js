/* ─── script.js ─── Story→Audiobook frontend logic ─────────────────────────
   Handles: form submit, SSE log streaming, polling, progress bar, download
*/

const form           = document.getElementById('generate-form');
const urlInput       = document.getElementById('url-input');
const btnGenerate    = document.getElementById('btn-generate');
const btnText        = document.getElementById('btn-text');
const progressSec    = document.getElementById('progress-section');
const downloadSec    = document.getElementById('download-section');
const errorBox       = document.getElementById('error-box');
const statusPill     = document.getElementById('status-pill');
const pageCounter    = document.getElementById('page-counter');
const progressFill   = document.getElementById('progress-fill');
const progressPct    = document.getElementById('progress-pct');
const terminal       = document.getElementById('terminal');
const downloadLink   = document.getElementById('download-link');
const downloadName   = document.getElementById('download-name');

let currentJobId = null;
let sseSource    = null;
let pollTimer    = null;
let lastPage     = 0;
let lastTotal    = 0;

// ── Helpers ──────────────────────────────────────────────────────────────────

function appendLog(raw) {
  const line = document.createElement('span');
  line.className = 'log-line';

  const lower = raw.toLowerCase();
  if (lower.includes('[error]') || lower.includes('error'))        line.classList.add('error');
  else if (lower.includes('[warning]') || lower.includes('warn'))  line.classList.add('warn');
  else if (lower.includes('done') || lower.includes('complete'))   line.classList.add('success');
  else                                                             line.classList.add('info');

  line.textContent = raw;
  terminal.appendChild(line);
  terminal.scrollTop = terminal.scrollHeight;
}

function setProgress(page, total) {
  if (total <= 0) return;
  const pct = Math.min(100, Math.round((page / total) * 100));
  progressFill.style.width = pct + '%';
  progressPct.textContent  = pct + '%';
  pageCounter.textContent  = `Page ${page} / ${total}`;
}

function setStatus(text, cls = '') {
  statusPill.textContent = text;
  statusPill.className   = 'status-pill' + (cls ? ' ' + cls : '');
}

function showError(msg) {
  errorBox.textContent = '⚠ ' + msg;
  errorBox.style.display = 'block';
}

function resetUI() {
  progressFill.style.width = '0%';
  progressPct.textContent  = '0%';
  pageCounter.textContent  = 'Page 0 / ?';
  terminal.innerHTML       = '';
  errorBox.style.display   = 'none';
  downloadSec.style.display = 'none';
}

// ── SSE log stream ────────────────────────────────────────────────────────────

function startSSE(jobId) {
  if (sseSource) { sseSource.close(); sseSource = null; }

  sseSource = new EventSource(`/api/logs/${jobId}`);

  sseSource.onmessage = (e) => {
    const data = e.data.trim();
    if (data === '[DONE]') {
      sseSource.close();
      sseSource = null;
      return;
    }
    try { appendLog(JSON.parse(data)); } catch { appendLog(data); }
  };

  sseSource.onerror = () => {
    sseSource.close();
    sseSource = null;
  };
}

// ── Status polling ────────────────────────────────────────────────────────────

async function pollStatus(jobId) {
  try {
    const res  = await fetch(`/api/status/${jobId}`);
    const data = await res.json();

    const { status, page, total, error, output_path } = data;

    if (total > 0) setProgress(page, total);

    switch (status) {
      case 'Queued':
        setStatus('⏳ Queued');
        break;
      case 'Scraping':
        setStatus('🔍 Scraping');
        break;
      case 'Processing':
        setStatus('🎙️ Generating Audio');
        break;
      case 'Merging':
        setStatus('🎵 Merging Audio', 'merge');
        progressFill.style.width = '99%';
        progressPct.textContent  = '99%';
        break;
      case 'Done':
        clearInterval(pollTimer);
        pollTimer = null;
        setStatus('✅ Done', 'done');
        progressFill.style.width = '100%';
        progressPct.textContent  = '100%';
        if (total > 0) pageCounter.textContent = `Page ${total} / ${total}`;
        showDownload(jobId);
        setBtnIdle();
        return;
      case 'Error':
        clearInterval(pollTimer);
        pollTimer = null;
        setStatus('❌ Error', 'error');
        showError(error || 'An unknown error occurred.');
        setBtnIdle();
        return;
    }
  } catch (err) {
    console.error('Poll error:', err);
  }
}

// ── Download reveal ───────────────────────────────────────────────────────────

function showDownload(jobId) {
  downloadLink.href = `/api/download/${jobId}`;
  downloadLink.download = 'audiobook.mp3';
  downloadName.textContent = `Job ID: ${jobId.slice(0, 8)}… ready`;
  downloadSec.style.display = 'block';
}

// ── Button state helpers ──────────────────────────────────────────────────────

function setBtnLoading() {
  btnGenerate.disabled = true;
  btnText.innerHTML = '<span class="btn-spinner"></span>Starting…';
}

function setBtnIdle() {
  btnGenerate.disabled = false;
  btnText.innerHTML = '🎧 Generate Audiobook';
}

// ── Form submit ───────────────────────────────────────────────────────────────

form.addEventListener('submit', async (e) => {
  e.preventDefault();

  const url = urlInput.value.trim();
  if (!url) { showError('Please enter a URL.'); return; }

  // Cleanup previous job
  if (sseSource) { sseSource.close(); sseSource = null; }
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }

  resetUI();
  setBtnLoading();
  progressSec.style.display = 'block';
  setStatus('⏳ Queued');

  const engine = document.querySelector('input[name="engine"]:checked')?.value || 'auto';

  try {
    const res  = await fetch('/api/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, tts_engine: engine }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Server error ${res.status}`);
    }

    const { job_id } = await res.json();
    currentJobId = job_id;

    appendLog(`Job started: ${job_id}`);
    startSSE(job_id);

    // Poll every 2 seconds
    pollTimer = setInterval(() => pollStatus(job_id), 2000);
    pollStatus(job_id);   // immediate first poll

  } catch (err) {
    showError(err.message);
    setBtnIdle();
  }
});
