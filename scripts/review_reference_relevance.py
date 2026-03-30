#!/usr/bin/env python3
"""
Visual review tool for comparing transcription lyrics against reference lyrics.

Fetches production job data from Firestore/GCS (same as analyze_reference_relevance.py),
then starts a local HTTP server with a side-by-side review interface.

Usage:
    python scripts/review_reference_relevance.py
    python scripts/review_reference_relevance.py --limit 50
    python scripts/review_reference_relevance.py --port 8765

Requires:
    - google-cloud-firestore
    - google-cloud-storage
    - GOOGLE_APPLICATION_CREDENTIALS or gcloud auth application-default login
"""
import argparse
import json
import logging
import socket
import sys
import threading
import webbrowser
from collections import defaultdict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

from google.cloud import firestore, storage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

PROJECT = "nomadkaraoke"
GCS_BUCKET = "karaoke-gen-storage-nomadkaraoke"
OUTPUT_FILE = Path(__file__).parent / "relevance_review_results.json"

# ---------------------------------------------------------------------------
# Firestore / GCS helpers (same as analyze_reference_relevance.py)
# ---------------------------------------------------------------------------


def fetch_candidate_jobs(db: firestore.Client, limit: int) -> list[dict]:
    logger.info("Querying Firestore for completed jobs with correction data...")
    query = (
        db.collection("jobs")
        .where("status", "==", "complete")
        .order_by("created_at", direction=firestore.Query.DESCENDING)
        .limit(limit * 3)
    )
    jobs = []
    for doc in query.stream():
        data = doc.to_dict()
        data["_job_id"] = doc.id
        corrections_url = _get_corrections_url(data)
        if corrections_url:
            data["_corrections_url"] = corrections_url
            jobs.append(data)
    logger.info(f"Found {len(jobs)} jobs with corrections URL")
    return jobs


def _get_corrections_url(job_data: dict) -> Optional[str]:
    file_urls = job_data.get("file_urls", {})
    lyrics = file_urls.get("lyrics", {})
    return lyrics.get("corrections")


def download_corrections(gcs_client: storage.Client, gcs_path: str) -> Optional[dict]:
    if gcs_path.startswith("gs://"):
        parts = gcs_path[5:].split("/", 1)
        bucket_name = parts[0]
        blob_path = parts[1] if len(parts) > 1 else ""
    else:
        bucket_name = GCS_BUCKET
        blob_path = gcs_path

    try:
        bucket = gcs_client.bucket(bucket_name)
        blob = bucket.blob(blob_path)
        content = blob.download_as_bytes()
        return json.loads(content)
    except Exception as e:
        logger.warning(f"Failed to download {gcs_path}: {e}")
        return None


# ---------------------------------------------------------------------------
# Data extraction for review
# ---------------------------------------------------------------------------


def extract_review_pairs(
    job_id: str,
    artist: str,
    title: str,
    corrections: dict,
) -> list[dict]:
    """
    Extract all (job, source) pairs with full segment text and anchor word IDs.
    Returns list of pair dicts ready to be serialized into the HTML page.
    """
    reference_lyrics: dict = corrections.get("reference_lyrics", {})
    anchor_sequences: list = corrections.get("anchor_sequences", [])
    original_segments: list = corrections.get("original_segments", [])

    # Build anchored word IDs per source
    anchored_ids: dict[str, set] = defaultdict(set)
    for anchor in anchor_sequences:
        ref_word_ids: dict = anchor.get("reference_word_ids", {})
        for source, word_ids in ref_word_ids.items():
            anchored_ids[source].update(word_ids)

    # Build anchored transcription word IDs (from transcription side)
    anchored_transcription_ids: set = set()
    for anchor in anchor_sequences:
        trans_word_ids = anchor.get("transcription_word_ids", [])
        anchored_transcription_ids.update(trans_word_ids)

    # Transcription segments — collect words with anchor flag
    trans_segments = []
    for seg in original_segments:
        words = []
        for w in seg.get("words", []):
            words.append({
                "text": w.get("text", ""),
                "id": w.get("id", ""),
                "anchored": w.get("id", "") in anchored_transcription_ids,
            })
        trans_segments.append({
            "text": seg.get("text", ""),
            "words": words,
        })

    # Relevance scores
    total_words_per_source: dict[str, int] = {}
    for source, ref_data in reference_lyrics.items():
        if not ref_data:
            continue
        total = sum(len(s.get("words", [])) for s in ref_data.get("segments", []))
        total_words_per_source[source] = total

    pairs = []
    for source, ref_data in reference_lyrics.items():
        if not ref_data:
            continue

        total_words = total_words_per_source.get(source, 0)
        anchored_count = len(anchored_ids.get(source, set()))
        score = round(anchored_count / total_words, 4) if total_words > 0 else 0.0
        anchored_set = anchored_ids.get(source, set())

        # Reference segments with per-word anchor flag
        ref_segments = []
        for seg in ref_data.get("segments", []):
            words = []
            for w in seg.get("words", []):
                words.append({
                    "text": w.get("text", ""),
                    "id": w.get("id", ""),
                    "anchored": w.get("id", "") in anchored_set,
                })
            ref_segments.append({
                "text": seg.get("text", ""),
                "words": words,
            })

        # Source metadata
        meta = ref_data.get("metadata", {})
        source_track = meta.get("track_name", "")
        source_artist = meta.get("artist_names", "")

        pairs.append({
            "job_id": job_id,
            "source": source,
            "artist": artist,
            "title": title,
            "relevance": score,
            "total_words": total_words,
            "anchored_words": anchored_count,
            "source_track": source_track,
            "source_artist": source_artist,
            "trans_segments": trans_segments,
            "ref_segments": ref_segments,
        })

    return pairs


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Reference Lyrics Review</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --bg: #0f1117;
    --surface: #1a1d27;
    --surface2: #22263a;
    --border: #2e3354;
    --text: #e2e8f0;
    --text-muted: #8892b0;
    --accent: #64ffda;
    --green: #22c55e;
    --green-dim: #166534;
    --red: #ef4444;
    --red-dim: #7f1d1d;
    --orange: #f97316;
    --orange-dim: #7c2d12;
    --highlight: rgba(100, 255, 218, 0.25);
    --highlight-border: rgba(100, 255, 218, 0.6);
  }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
  }

  /* ── Top bar ── */
  #topbar {
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    padding: 10px 20px;
    display: flex;
    align-items: center;
    gap: 16px;
    flex-wrap: wrap;
    position: sticky;
    top: 0;
    z-index: 100;
  }

  #topbar h1 {
    font-size: 1rem;
    font-weight: 700;
    color: var(--accent);
    white-space: nowrap;
  }

  #progress-text {
    font-size: 0.875rem;
    color: var(--text-muted);
    white-space: nowrap;
  }

  #progress-bar-wrap {
    flex: 1;
    min-width: 80px;
    height: 6px;
    background: var(--surface2);
    border-radius: 3px;
    overflow: hidden;
  }

  #progress-bar {
    height: 100%;
    background: var(--accent);
    border-radius: 3px;
    transition: width 0.3s ease;
  }

  #jump-select {
    background: var(--surface2);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 4px 8px;
    font-size: 0.8rem;
    max-width: 220px;
  }

  #save-btn {
    background: var(--surface2);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 6px 14px;
    font-size: 0.85rem;
    cursor: pointer;
    white-space: nowrap;
    transition: background 0.2s;
  }
  #save-btn:hover { background: var(--border); }

  #keyboard-hint {
    font-size: 0.75rem;
    color: var(--text-muted);
    white-space: nowrap;
  }

  /* ── Main card ── */
  #main {
    flex: 1;
    padding: 20px;
    display: flex;
    flex-direction: column;
    gap: 16px;
    max-width: 1600px;
    width: 100%;
    margin: 0 auto;
  }

  #card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    overflow: hidden;
  }

  /* ── Card header ── */
  #card-header {
    padding: 16px 20px;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    gap: 16px;
    flex-wrap: wrap;
  }

  #job-title {
    font-size: 1.15rem;
    font-weight: 700;
  }

  #job-meta {
    font-size: 0.8rem;
    color: var(--text-muted);
  }

  #score-badge {
    margin-left: auto;
    padding: 4px 12px;
    border-radius: 999px;
    font-size: 0.9rem;
    font-weight: 700;
    white-space: nowrap;
  }
  .score-red    { background: var(--red-dim);    color: #fca5a5; }
  .score-orange { background: var(--orange-dim); color: #fdba74; }
  .score-green  { background: var(--green-dim);  color: #86efac; }

  #source-meta {
    font-size: 0.8rem;
    color: var(--text-muted);
  }

  #verdict-badge {
    padding: 3px 10px;
    border-radius: 999px;
    font-size: 0.78rem;
    font-weight: 600;
  }
  .vb-correct   { background: var(--green-dim); color: #86efac; }
  .vb-wrong     { background: var(--red-dim);   color: #fca5a5; }

  /* ── Lyrics columns ── */
  #lyrics-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    min-height: 320px;
  }

  .lyrics-panel {
    padding: 16px 20px;
    overflow-y: auto;
    max-height: 420px;
  }

  .lyrics-panel:first-child {
    border-right: 1px solid var(--border);
  }

  .panel-label {
    font-size: 0.7rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--text-muted);
    margin-bottom: 10px;
  }

  .lyrics-text {
    font-family: 'SF Mono', 'Fira Code', 'Courier New', monospace;
    font-size: 0.82rem;
    line-height: 1.7;
    white-space: pre-wrap;
    word-break: break-word;
  }

  .seg-line { display: block; }

  .word {
    display: inline;
    border-radius: 2px;
    padding: 0 1px;
  }
  .word.anchored {
    background: var(--highlight);
    border-bottom: 1px solid var(--highlight-border);
    color: var(--accent);
  }

  /* ── Actions ── */
  #actions {
    padding: 14px 20px;
    border-top: 1px solid var(--border);
    display: flex;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
  }

  .verdict-btn {
    padding: 9px 22px;
    border-radius: 8px;
    border: 2px solid transparent;
    font-size: 0.9rem;
    font-weight: 700;
    cursor: pointer;
    transition: all 0.15s;
  }

  #btn-correct {
    background: var(--green-dim);
    color: #86efac;
    border-color: var(--green);
  }
  #btn-correct:hover, #btn-correct.active {
    background: var(--green);
    color: #052e16;
  }

  #btn-wrong {
    background: var(--red-dim);
    color: #fca5a5;
    border-color: var(--red);
  }
  #btn-wrong:hover, #btn-wrong.active {
    background: var(--red);
    color: #450a0a;
  }

  #comment-input {
    flex: 1;
    min-width: 180px;
    background: var(--surface2);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 8px 12px;
    font-size: 0.85rem;
  }
  #comment-input::placeholder { color: var(--text-muted); }

  /* ── Nav footer ── */
  #nav-footer {
    display: flex;
    align-items: center;
    gap: 12px;
    justify-content: space-between;
  }

  .nav-btn {
    background: var(--surface2);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 8px 20px;
    font-size: 0.9rem;
    cursor: pointer;
    transition: background 0.15s;
  }
  .nav-btn:hover { background: var(--border); }
  .nav-btn:disabled { opacity: 0.35; cursor: default; }

  #status-msg {
    font-size: 0.82rem;
    color: var(--text-muted);
    flex: 1;
    text-align: center;
  }
  #status-msg.success { color: var(--green); }
  #status-msg.error   { color: var(--red); }

  /* ── All-done screen ── */
  #done-screen {
    display: none;
    text-align: center;
    padding: 60px 20px;
    flex-direction: column;
    align-items: center;
    gap: 16px;
  }
  #done-screen h2 { font-size: 1.5rem; color: var(--accent); }
  #done-screen p  { color: var(--text-muted); }

  /* ── Overview table (hidden by default) ── */
  #overview-toggle {
    background: none;
    border: 1px solid var(--border);
    color: var(--text-muted);
    border-radius: 6px;
    padding: 4px 10px;
    font-size: 0.78rem;
    cursor: pointer;
  }
  #overview-toggle:hover { color: var(--text); }

  #overview-panel {
    display: none;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 12px 16px;
    max-height: 240px;
    overflow-y: auto;
  }

  #overview-panel table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.78rem;
  }
  #overview-panel th {
    text-align: left;
    padding: 4px 8px;
    color: var(--text-muted);
    border-bottom: 1px solid var(--border);
  }
  #overview-panel td {
    padding: 4px 8px;
    border-bottom: 1px solid var(--border);
  }
  #overview-panel tr:hover td { background: var(--surface2); cursor: pointer; }
  .ov-correct { color: #86efac; }
  .ov-wrong   { color: #fca5a5; }
  .ov-pending { color: var(--text-muted); }
</style>
</head>
<body>

<div id="topbar">
  <h1>Reference Lyrics Review</h1>
  <span id="progress-text">0 / 0 reviewed</span>
  <div id="progress-bar-wrap"><div id="progress-bar" style="width:0%"></div></div>
  <select id="jump-select" title="Jump to pair"></select>
  <button id="overview-toggle" onclick="toggleOverview()">Overview</button>
  <button id="save-btn" onclick="saveAll()">Save &amp; Exit</button>
  <span id="keyboard-hint">← Wrong &nbsp;|&nbsp; → Correct &nbsp;|&nbsp; Enter = Next</span>
</div>

<div id="main">
  <div id="card">
    <div id="card-header">
      <div>
        <div id="job-title"></div>
        <div id="job-meta"></div>
      </div>
      <div id="source-meta"></div>
      <div id="score-badge"></div>
      <div id="verdict-badge" style="display:none"></div>
    </div>

    <div id="lyrics-grid">
      <div class="lyrics-panel">
        <div class="panel-label">Transcription</div>
        <div id="trans-lyrics" class="lyrics-text"></div>
      </div>
      <div class="lyrics-panel">
        <div class="panel-label">Reference (<span id="ref-source-label"></span>)</div>
        <div id="ref-lyrics" class="lyrics-text"></div>
      </div>
    </div>

    <div id="actions">
      <button id="btn-correct" class="verdict-btn" onclick="submitVerdict('correct_song')">&#10003; Correct Song</button>
      <button id="btn-wrong"   class="verdict-btn" onclick="submitVerdict('wrong_song')">&#10007; Wrong Song</button>
      <input  id="comment-input" type="text" placeholder="Optional comment..." />
    </div>
  </div>

  <div id="nav-footer">
    <button class="nav-btn" id="btn-prev" onclick="navigate(-1)">&#8592; Prev</button>
    <span id="status-msg"></span>
    <button class="nav-btn" id="btn-next" onclick="navigate(1)">Next &#8594;</button>
  </div>

  <div>
    <button id="overview-toggle2" class="nav-btn" onclick="toggleOverview()" style="font-size:0.78rem;padding:5px 12px">Show All Pairs</button>
  </div>

  <div id="overview-panel">
    <table>
      <thead><tr><th>#</th><th>Job</th><th>Source</th><th>Score</th><th>Verdict</th></tr></thead>
      <tbody id="overview-tbody"></tbody>
    </table>
  </div>

  <div id="done-screen">
    <h2>All pairs reviewed!</h2>
    <p id="done-stats"></p>
    <button class="nav-btn" onclick="saveAll()">Save Results &amp; Exit</button>
  </div>
</div>

<script>
// ── Data embedded by server ──────────────────────────────────────────────
const PAIRS = __PAIRS_JSON__;
// ────────────────────────────────────────────────────────────────────────

// State
let currentIdx = 0;
const reviews = {};   // key: `${job_id}::${source}` → {verdict, comment}

// Restore from localStorage if available
try {
  const saved = localStorage.getItem('rlr_reviews');
  if (saved) Object.assign(reviews, JSON.parse(saved));
} catch(e) {}

function pairKey(p) { return `${p.job_id}::${p.source}`; }

function updateProgress() {
  const total = PAIRS.length;
  const done  = Object.keys(reviews).length;
  document.getElementById('progress-text').textContent = `${done} / ${total} reviewed`;
  document.getElementById('progress-bar').style.width = `${total ? (done/total)*100 : 0}%`;
}

function renderPair(idx) {
  const p = PAIRS[idx];
  if (!p) return;

  // Header
  document.getElementById('job-title').textContent = `${p.artist} – ${p.title}`;
  document.getElementById('job-meta').textContent  = `Job: ${p.job_id}`;

  let srcMeta = p.source;
  if (p.source_track || p.source_artist) {
    srcMeta += ` — found: "${p.source_track}" by ${p.source_artist}`;
  }
  document.getElementById('source-meta').textContent = srcMeta;
  document.getElementById('ref-source-label').textContent = p.source;

  // Score badge
  const pct = (p.relevance * 100).toFixed(1);
  const badge = document.getElementById('score-badge');
  badge.textContent = `${pct}% word match`;
  badge.className = 'score-badge ' + (
    p.relevance < 0.20 ? 'score-red' :
    p.relevance < 0.50 ? 'score-orange' :
    'score-green'
  );

  // Existing verdict badge
  const vb  = document.getElementById('verdict-badge');
  const rev = reviews[pairKey(p)];
  if (rev) {
    vb.style.display = '';
    vb.textContent   = rev.verdict === 'correct_song' ? '✓ Correct Song' : '✗ Wrong Song';
    vb.className     = 'verdict-badge ' + (rev.verdict === 'correct_song' ? 'vb-correct' : 'vb-wrong');
    document.getElementById('comment-input').value = rev.comment || '';
  } else {
    vb.style.display = 'none';
    document.getElementById('comment-input').value = '';
  }

  // Highlight active verdict buttons
  document.getElementById('btn-correct').classList.toggle('active', rev?.verdict === 'correct_song');
  document.getElementById('btn-wrong').classList.toggle('active',   rev?.verdict === 'wrong_song');

  // Lyrics
  document.getElementById('trans-lyrics').innerHTML = renderSegments(p.trans_segments);
  document.getElementById('ref-lyrics').innerHTML   = renderSegments(p.ref_segments);

  // Nav buttons
  document.getElementById('btn-prev').disabled = (idx === 0);
  document.getElementById('btn-next').disabled = (idx === PAIRS.length - 1);

  // Jump select
  document.getElementById('jump-select').value = String(idx);

  // Update status
  document.getElementById('status-msg').textContent = `Pair ${idx + 1} of ${PAIRS.length}`;
  document.getElementById('status-msg').className = '';
}

function renderSegments(segments) {
  if (!segments || !segments.length) return '<em style="color:var(--text-muted)">No data</em>';

  return segments.map(seg => {
    if (!seg.words || !seg.words.length) {
      // Fallback: plain text
      return `<span class="seg-line">${escHtml(seg.text || '')}\n</span>`;
    }
    const wordsHtml = seg.words.map(w => {
      const cls = w.anchored ? 'word anchored' : 'word';
      return `<span class="${cls}">${escHtml(w.text)}</span>`;
    }).join(' ');
    return `<span class="seg-line">${wordsHtml}\n</span>`;
  }).join('');
}

function escHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function navigate(delta) {
  const next = currentIdx + delta;
  if (next < 0 || next >= PAIRS.length) return;
  currentIdx = next;
  renderPair(currentIdx);
  updateProgress();
  refreshOverview();
}

function jumpTo(idx) {
  currentIdx = Number(idx);
  renderPair(currentIdx);
  updateProgress();
  refreshOverview();
}

function submitVerdict(verdict) {
  const p   = PAIRS[currentIdx];
  const key = pairKey(p);
  const comment = document.getElementById('comment-input').value.trim();
  reviews[key] = { verdict, comment, job_id: p.job_id, source: p.source };

  // Persist to localStorage
  try { localStorage.setItem('rlr_reviews', JSON.stringify(reviews)); } catch(e) {}

  // Send to server (auto-save)
  sendToServer(p, verdict, comment);

  // Update UI
  renderPair(currentIdx);
  updateProgress();
  refreshOverview();

  // Auto-advance to next unreviewed
  const nextUnreviewed = findNextUnreviewed(currentIdx + 1);
  if (nextUnreviewed !== -1) {
    setTimeout(() => {
      currentIdx = nextUnreviewed;
      renderPair(currentIdx);
      refreshOverview();
    }, 180);
  } else {
    // All done?
    const allReviewed = PAIRS.every(pp => reviews[pairKey(pp)]);
    if (allReviewed) {
      setTimeout(showDoneScreen, 250);
    }
  }
}

function findNextUnreviewed(startFrom) {
  for (let i = startFrom; i < PAIRS.length; i++) {
    if (!reviews[pairKey(PAIRS[i])]) return i;
  }
  // Wrap around
  for (let i = 0; i < startFrom; i++) {
    if (!reviews[pairKey(PAIRS[i])]) return i;
  }
  return -1;
}

function sendToServer(p, verdict, comment) {
  const payload = {
    job_id: p.job_id,
    source: p.source,
    artist: p.artist,
    title: p.title,
    relevance: p.relevance,
    verdict,
    comment,
  };
  fetch('/api/review', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  .then(r => r.json())
  .then(d => {
    const msg = document.getElementById('status-msg');
    msg.textContent = d.saved ? 'Auto-saved.' : 'Server error saving.';
    msg.className = d.saved ? 'success' : 'error';
    setTimeout(() => { msg.textContent = `Pair ${currentIdx + 1} of ${PAIRS.length}`; msg.className=''; }, 1800);
  })
  .catch(() => {});
}

function saveAll() {
  fetch('/api/save', { method: 'POST' })
    .then(r => r.json())
    .then(d => {
      const msg = document.getElementById('status-msg');
      msg.textContent = d.ok ? `Saved ${d.count} reviews to ${d.path}` : 'Save failed: ' + d.error;
      msg.className = d.ok ? 'success' : 'error';
      if (d.ok) setTimeout(() => window.close(), 1500);
    })
    .catch(e => {
      document.getElementById('status-msg').textContent = 'Save error: ' + e;
      document.getElementById('status-msg').className = 'error';
    });
}

function showDoneScreen() {
  document.getElementById('card').style.display    = 'none';
  document.getElementById('nav-footer').style.display = 'none';
  const screen = document.getElementById('done-screen');
  screen.style.display = 'flex';
  const correct = Object.values(reviews).filter(r => r.verdict === 'correct_song').length;
  const wrong   = Object.values(reviews).filter(r => r.verdict === 'wrong_song').length;
  document.getElementById('done-stats').textContent =
    `${correct} correct song / ${wrong} wrong song out of ${PAIRS.length} pairs`;
  saveAll();
}

// ── Overview ────────────────────────────────────────────────────────────

function toggleOverview() {
  const panel = document.getElementById('overview-panel');
  panel.style.display = panel.style.display === 'block' ? 'none' : 'block';
  if (panel.style.display === 'block') refreshOverview();
}

function refreshOverview() {
  const tbody = document.getElementById('overview-tbody');
  tbody.innerHTML = PAIRS.map((p, i) => {
    const rev = reviews[pairKey(p)];
    const verdictHtml = rev
      ? `<span class="${rev.verdict === 'correct_song' ? 'ov-correct' : 'ov-wrong'}">${rev.verdict === 'correct_song' ? '✓' : '✗'}</span>`
      : '<span class="ov-pending">—</span>';
    const pct = (p.relevance * 100).toFixed(1) + '%';
    const current = i === currentIdx ? 'style="background:var(--surface2)"' : '';
    return `<tr ${current} onclick="jumpTo(${i})">
      <td>${i+1}</td>
      <td>${escHtml(p.artist)} – ${escHtml(p.title)}</td>
      <td>${escHtml(p.source)}</td>
      <td>${pct}</td>
      <td>${verdictHtml}</td>
    </tr>`;
  }).join('');
}

// ── Jump select ─────────────────────────────────────────────────────────

function buildJumpSelect() {
  const sel = document.getElementById('jump-select');
  sel.innerHTML = PAIRS.map((p, i) =>
    `<option value="${i}">${i+1}. ${p.artist} – ${p.title} (${p.source})</option>`
  ).join('');
  sel.addEventListener('change', e => jumpTo(e.target.value));
}

// ── Keyboard shortcuts ───────────────────────────────────────────────────

document.addEventListener('keydown', e => {
  // Don't fire when user is typing in comment box
  if (document.activeElement === document.getElementById('comment-input')) {
    if (e.key === 'Enter') { navigate(1); e.preventDefault(); }
    return;
  }
  if (e.key === 'ArrowLeft')  { submitVerdict('wrong_song');   e.preventDefault(); }
  if (e.key === 'ArrowRight') { submitVerdict('correct_song'); e.preventDefault(); }
  if (e.key === 'Enter')      { navigate(1);                   e.preventDefault(); }
  if (e.key === 'ArrowUp')    { navigate(-1);                  e.preventDefault(); }
  if (e.key === 'ArrowDown')  { navigate(1);                   e.preventDefault(); }
});

// ── Init ─────────────────────────────────────────────────────────────────

buildJumpSelect();
updateProgress();
if (PAIRS.length > 0) {
  // Start from first unreviewed
  const firstUnreviewed = findNextUnreviewed(0);
  currentIdx = firstUnreviewed === -1 ? 0 : firstUnreviewed;
  renderPair(currentIdx);
} else {
  document.getElementById('status-msg').textContent = 'No pairs to review.';
}
</script>
</body>
</html>
"""


def build_html(pairs: list[dict]) -> str:
    pairs_json = json.dumps(pairs, ensure_ascii=False)
    return HTML_TEMPLATE.replace("__PAIRS_JSON__", pairs_json)


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------


class ReviewHandler(BaseHTTPRequestHandler):
    """Serves the review UI and handles API endpoints."""

    # Class-level state shared across requests
    html_content: bytes = b""
    reviews: dict = {}  # key → review dict
    output_path: Path = OUTPUT_FILE
    total_pairs: int = 0

    def log_message(self, format, *args):  # noqa: A002
        # Suppress per-request logs for cleaner terminal output
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            self._send_response(200, "text/html; charset=utf-8", self.html_content)
        else:
            self._send_response(404, "text/plain", b"Not found")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b"{}"

        if path == "/api/review":
            self._handle_review(body)
        elif path == "/api/save":
            self._handle_save()
        else:
            self._send_response(404, "application/json", json.dumps({"error": "not found"}).encode())

    def _handle_review(self, body: bytes):
        try:
            data = json.loads(body)
            job_id = data.get("job_id", "")
            source = data.get("source", "")
            verdict = data.get("verdict", "")
            if not job_id or not source or verdict not in ("correct_song", "wrong_song"):
                raise ValueError("Invalid payload")

            key = f"{job_id}::{source}"
            ReviewHandler.reviews[key] = {
                "job_id": job_id,
                "source": source,
                "artist": data.get("artist", ""),
                "title": data.get("title", ""),
                "relevance": data.get("relevance", 0),
                "verdict": verdict,
                "comment": data.get("comment", ""),
            }

            # Auto-save after each review
            self._write_results()

            resp = json.dumps({"saved": True, "total": len(ReviewHandler.reviews)})
            self._send_response(200, "application/json", resp.encode())
        except Exception as e:
            resp = json.dumps({"saved": False, "error": str(e)})
            self._send_response(400, "application/json", resp.encode())

    def _handle_save(self):
        try:
            path = self._write_results()
            resp = json.dumps({
                "ok": True,
                "count": len(ReviewHandler.reviews),
                "path": str(path),
            })
            self._send_response(200, "application/json", resp.encode())
        except Exception as e:
            resp = json.dumps({"ok": False, "error": str(e)})
            self._send_response(500, "application/json", resp.encode())

    def _write_results(self) -> Path:
        path = ReviewHandler.output_path
        _flush_reviews(ReviewHandler.reviews, path, ReviewHandler.total_pairs)
        return path

    def _send_response(self, code: int, content_type: str, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


def find_free_port(preferred: int) -> int:
    """Try preferred port; fall back to any free port."""
    for port in [preferred, 0]:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
        except OSError:
            continue
    raise RuntimeError("Could not find a free port")


# ---------------------------------------------------------------------------
# Shared save helper (used by handler and shutdown)
# ---------------------------------------------------------------------------


def _flush_reviews(reviews: dict, output_path: Path, total_pairs: int) -> None:
    reviews_list = sorted(
        reviews.values(),
        key=lambda r: (r.get("artist", ""), r.get("title", ""), r.get("source", "")),
    )
    output = {
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "total_pairs": total_pairs,
        "reviewed": len(reviews_list),
        "reviews": reviews_list,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)
    logger.info(f"Saved {len(reviews_list)} reviews to {output_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Visual review tool for reference lyrics relevance"
    )
    parser.add_argument("--limit", type=int, default=30,
                        help="Target number of jobs to load (default: 30)")
    parser.add_argument("--port", type=int, default=8765,
                        help="HTTP server port (default: 8765)")
    parser.add_argument("--no-mixed-bias", action="store_true",
                        help="Disable bias toward jobs with mixed results")
    parser.add_argument("--output", type=str, default=str(OUTPUT_FILE),
                        help=f"Output file path (default: {OUTPUT_FILE})")
    args = parser.parse_args()

    # ── Fetch data ────────────────────────────────────────────────────────
    db = firestore.Client(project=PROJECT)
    gcs_client = storage.Client(project=PROJECT)

    candidate_jobs = fetch_candidate_jobs(db, limit=args.limit)
    if not candidate_jobs:
        logger.error("No candidate jobs found. Check Firestore access.")
        sys.exit(1)

    job_results = []
    skipped = 0

    for job in candidate_jobs:
        job_id = job["_job_id"]
        corrections_url = job["_corrections_url"]
        artist = job.get("artist", job.get("state_data", {}).get("artist", "Unknown"))
        title = job.get("title", job.get("state_data", {}).get("title", "Unknown"))

        logger.info(f"Processing {job_id}: {artist} – {title}")

        corrections = download_corrections(gcs_client, corrections_url)
        if not corrections:
            skipped += 1
            continue

        pairs = extract_review_pairs(job_id, artist, title, corrections)
        if not pairs:
            skipped += 1
            continue

        job_results.append({
            "job_id": job_id,
            "artist": artist,
            "title": title,
            "pairs": pairs,
        })

    if skipped:
        logger.info(f"Skipped {skipped} jobs (download failure or no reference sources)")

    if not job_results:
        logger.error("No usable jobs found.")
        sys.exit(1)

    # ── Apply mixed-bias selection (same logic as analyze script) ────────
    if not args.no_mixed_bias:
        def is_mixed(result):
            scores = [p["relevance"] for p in result["pairs"]]
            return len(scores) >= 2 and min(scores) < 0.5

        mixed     = [r for r in job_results if is_mixed(r)]
        non_mixed = [r for r in job_results if not is_mixed(r)]
        target_mixed     = min(len(mixed),     max(args.limit // 2, len(mixed)))
        target_non_mixed = min(len(non_mixed), args.limit - target_mixed)
        job_results = mixed[:target_mixed] + non_mixed[:target_non_mixed]
        logger.info(
            f"After mixed-bias selection: {len(job_results)} jobs "
            f"({target_mixed} mixed, {target_non_mixed} non-mixed)"
        )
    else:
        job_results = job_results[:args.limit]

    # ── Flatten to pairs, sorted by relevance ascending ──────────────────
    all_pairs = []
    for result in job_results:
        all_pairs.extend(result["pairs"])

    all_pairs.sort(key=lambda p: p["relevance"])

    logger.info(f"Total review pairs: {len(all_pairs)}")

    # ── Pre-load existing reviews from output file ────────────────────────
    output_path = Path(args.output)
    existing_reviews = {}
    if output_path.exists():
        try:
            with open(output_path) as f:
                saved_data = json.load(f)
            for rev in saved_data.get("reviews", []):
                key = f"{rev['job_id']}::{rev['source']}"
                existing_reviews[key] = rev
            logger.info(f"Loaded {len(existing_reviews)} existing reviews from {output_path}")
        except Exception as e:
            logger.warning(f"Could not load existing reviews: {e}")

    # ── Start HTTP server ─────────────────────────────────────────────────
    html_bytes = build_html(all_pairs).encode("utf-8")

    ReviewHandler.html_content = html_bytes
    ReviewHandler.reviews      = existing_reviews
    ReviewHandler.output_path  = output_path
    ReviewHandler.total_pairs  = len(all_pairs)

    port = find_free_port(args.port)
    server = HTTPServer(("127.0.0.1", port), ReviewHandler)

    url = f"http://localhost:{port}/"
    print()
    print("=" * 60)
    print(f"  Review tool ready: {url}")
    print(f"  {len(all_pairs)} pairs to review")
    print(f"  Results will be saved to: {output_path}")
    print("=" * 60)
    print()
    print("  Keyboard shortcuts:")
    print("    ← Arrow  = Wrong Song")
    print("    → Arrow  = Correct Song")
    print("    Enter    = Next pair")
    print()
    print("  Press Ctrl+C to stop the server")
    print()

    # Open browser after a short delay
    def open_browser():
        import time
        time.sleep(0.5)
        webbrowser.open(url)

    threading.Thread(target=open_browser, daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
        server.server_close()

        # Final save on exit
        if ReviewHandler.reviews:
            _flush_reviews(ReviewHandler.reviews, output_path, len(all_pairs))


if __name__ == "__main__":
    main()
