/**
 * app.js — CARSDR Station web UI
 *
 * - Polls /api/status every 500ms to update frequency display and signal bars
 * - Uses HLS.js for audio streaming (iOS Safari has native HLS, so HLS.js
 *   falls back gracefully — both paths are handled)
 * - Manages the frequency list and recordings via REST API calls
 */

'use strict';

const API = '';  // Same origin

// ── State ──────────────────────────────────────────────────────────────
let hls = null;
let audioPlaying = false;
let lastState = null;
let pollInterval = null;

// ── DOM refs ────────────────────────────────────────────────────────────
const freqDisplay  = document.getElementById('freq-display');
const stateBadge   = document.getElementById('state-badge');
const signalBars   = document.querySelectorAll('.bar');
const playBtn      = document.getElementById('play-btn');
const audioPlayer  = document.getElementById('audio-player');
const audioStatus  = document.getElementById('audio-status');
const recIndicator = document.getElementById('rec-indicator');
const freqList     = document.getElementById('freq-list');
const recList      = document.getElementById('recordings-list');
const noRec        = document.getElementById('no-recordings');

// ── Audio setup ─────────────────────────────────────────────────────────

function initHls() {
  const src = '/hls/stream.m3u8';

  if (typeof Hls !== 'undefined' && Hls.isSupported()) {
    // Use HLS.js (Android Chrome, desktop)
    if (hls) { hls.destroy(); }
    hls = new Hls({
      enableWorker: false,
      lowLatencyMode: false,
      backBufferLength: 8,
    });
    hls.loadSource(src);
    hls.attachMedia(audioPlayer);
    hls.on(Hls.Events.MANIFEST_PARSED, () => {
      if (audioPlaying) audioPlayer.play().catch(() => {});
    });
    hls.on(Hls.Events.ERROR, (event, data) => {
      if (data.fatal) {
        audioStatus.textContent = 'Stream error — retrying...';
        setTimeout(initHls, 3000);
      }
    });
  } else if (audioPlayer.canPlayType('application/vnd.apple.mpegurl')) {
    // Native HLS (iOS Safari)
    audioPlayer.src = src;
    if (audioPlaying) audioPlayer.play().catch(() => {});
  } else {
    audioStatus.textContent = 'HLS not supported in this browser';
  }
}

function togglePlay() {
  if (!audioPlaying) {
    audioPlaying = true;
    playBtn.textContent = '⏸ Pause Audio';
    audioStatus.textContent = 'Buffering... (~4s delay)';
    initHls();
    audioPlayer.play().catch(() => {
      // Autoplay blocked — user interaction already occurred so this shouldn't happen
      audioStatus.textContent = 'Tap Play again to start';
    });
    audioPlayer.onplaying = () => { audioStatus.textContent = 'Streaming live'; };
    audioPlayer.onwaiting = () => { audioStatus.textContent = 'Buffering...'; };
    audioPlayer.onpause   = () => { audioStatus.textContent = 'Paused'; };
  } else {
    audioPlaying = false;
    audioPlayer.pause();
    playBtn.textContent = '▶ Play Audio';
    audioStatus.textContent = 'Paused';
  }
}

function setVolume(val) {
  audioPlayer.volume = parseFloat(val);
}

// ── Status polling ──────────────────────────────────────────────────────

async function pollStatus() {
  try {
    const res = await fetch(`${API}/api/status`);
    if (!res.ok) return;
    const data = await res.json();
    updateDisplay(data);
  } catch (_) { /* server not yet ready */ }
}

function updateDisplay(data) {
  // Frequency
  const freq = data.current_freq;
  freqDisplay.textContent = freq
    ? `${freq.toFixed(4)} MHz`
    : '---.----- MHz';

  // State badge
  const state = data.state;
  if (state !== lastState) {
    lastState = state;
    stateBadge.className = 'badge';
    stateBadge.textContent = state;
    if (state === 'SCANNING') {
      stateBadge.classList.add('badge-scanning');
    } else if (state === 'LOCKED') {
      stateBadge.classList.add('badge-locked');
      stateBadge.textContent = `LOCKED: ${freq ? freq.toFixed(3) : ''} MHz`;
    } else if (state === 'MANUAL') {
      stateBadge.classList.add('badge-manual');
      stateBadge.textContent = `MANUAL: ${freq ? freq.toFixed(3) : ''} MHz`;
    } else {
      stateBadge.classList.add('badge-stopped');
    }
  } else if (state === 'LOCKED' && freq) {
    stateBadge.textContent = `LOCKED: ${freq.toFixed(3)} MHz`;
  } else if (state === 'MANUAL' && freq) {
    stateBadge.textContent = `MANUAL: ${freq.toFixed(3)} MHz`;
  }

  // Signal bars
  const level = data.signal_level || 0;
  const bars = 5;
  // Map level 0–32767 to 0–5 bars (log scale feels more natural for RF)
  const activeBars = level < 100 ? 0
    : level < 500  ? 1
    : level < 1500 ? 2
    : level < 4000 ? 3
    : level < 9000 ? 4 : 5;

  signalBars.forEach((bar, i) => {
    bar.classList.remove('active', 'mid');
    if (i < activeBars) {
      bar.classList.add(activeBars >= 4 ? 'active' : 'mid');
    }
  });

  // Recording indicator
  if (data.is_recording) {
    recIndicator.classList.remove('hidden');
  } else {
    recIndicator.classList.add('hidden');
  }

  // Highlight active frequency in list
  document.querySelectorAll('.freq-item').forEach(el => {
    el.classList.toggle('active-freq', parseFloat(el.dataset.freq) === freq);
  });
}

// ── Scanner controls ────────────────────────────────────────────────────

async function scannerAction(action) {
  try {
    await fetch(`${API}/api/scanner/${action}`, { method: 'POST' });
    pollStatus();
  } catch (e) { console.error(e); }
}

async function quickTune() {
  const val = document.getElementById('tune-input').value;
  if (!val) return;
  try {
    await fetch(`${API}/api/tune`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ freq_mhz: parseFloat(val) }),
    });
    pollStatus();
  } catch (e) { console.error(e); }
}

// ── Frequency list ──────────────────────────────────────────────────────

async function loadFrequencies() {
  try {
    const res = await fetch(`${API}/api/frequencies`);
    const freqs = await res.json();
    renderFrequencies(freqs);
  } catch (e) { console.error(e); }
}

function renderFrequencies(freqs) {
  freqList.innerHTML = '';
  freqs.forEach(f => {
    const li = document.createElement('li');
    li.className = 'freq-item';
    li.dataset.freq = f.freq_mhz;
    li.innerHTML = `
      <label class="toggle">
        <input type="checkbox" ${f.enabled ? 'checked' : ''}
               onchange="toggleFreq(${f.freq_mhz})">
        <span class="toggle-track"></span>
        <span class="toggle-thumb"></span>
      </label>
      <span class="freq-name">${escHtml(f.name)}</span>
      <span class="freq-mhz">${f.freq_mhz.toFixed(3)}</span>
      <button class="freq-tune-btn" onclick="tuneToFreq(${f.freq_mhz})">Tune</button>
      <button class="freq-del-btn" onclick="deleteFreq(${f.freq_mhz})" title="Remove">✕</button>
    `;
    freqList.appendChild(li);
  });
}

async function toggleFreq(freqMhz) {
  try {
    await fetch(`${API}/api/frequencies/${freqMhz}/toggle`, { method: 'POST' });
  } catch (e) { console.error(e); }
}

async function tuneToFreq(freqMhz) {
  try {
    await fetch(`${API}/api/tune`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ freq_mhz: freqMhz }),
    });
    pollStatus();
  } catch (e) { console.error(e); }
}

async function deleteFreq(freqMhz) {
  try {
    await fetch(`${API}/api/frequencies/${freqMhz}`, { method: 'DELETE' });
    loadFrequencies();
  } catch (e) { console.error(e); }
}

async function addFrequency() {
  const name = document.getElementById('new-freq-name').value.trim();
  const mhz  = document.getElementById('new-freq-mhz').value;
  if (!name || !mhz) return;
  try {
    await fetch(`${API}/api/frequencies`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, freq_mhz: parseFloat(mhz) }),
    });
    document.getElementById('new-freq-name').value = '';
    document.getElementById('new-freq-mhz').value  = '';
    loadFrequencies();
  } catch (e) { console.error(e); }
}

// ── Recordings ──────────────────────────────────────────────────────────

async function loadRecordings() {
  try {
    const res  = await fetch(`${API}/api/recordings`);
    const recs = await res.json();
    renderRecordings(recs);
  } catch (e) { console.error(e); }
}

function renderRecordings(recs) {
  recList.innerHTML = '';
  if (recs.length === 0) {
    noRec.classList.remove('hidden');
    return;
  }
  noRec.classList.add('hidden');
  recs.forEach(r => {
    const li = document.createElement('li');
    li.className = 'rec-item';
    li.innerHTML = `
      <span class="rec-freq">${escHtml(r.freq)}</span>
      <span class="rec-time">${escHtml(r.timestamp)}</span>
      <span class="rec-size">${r.size_kb} KB</span>
      <button class="rec-play-btn" onclick="playRecording('${escHtml(r.filename)}')">▶</button>
      <a href="${API}/api/recordings/${encodeURIComponent(r.filename)}" download
         class="rec-play-btn">⬇</a>
    `;
    recList.appendChild(li);
  });
}

function playRecording(filename) {
  // Pause the live stream and play the recording
  if (audioPlaying) {
    audioPlayer.pause();
    audioPlaying = false;
    playBtn.textContent = '▶ Play Audio';
  }
  if (hls) { hls.destroy(); hls = null; }
  audioPlayer.src = `${API}/api/recordings/${encodeURIComponent(filename)}`;
  audioPlayer.play().catch(() => {});
  audioStatus.textContent = `Playing: ${filename}`;
}

// ── RadioReference Import ────────────────────────────────────────────────

let _rrAllEntries = [];   // full parsed result from preview
let _rrVisible   = [];    // currently filtered entries

const RAILROAD_TAGS = new Set([
  'railroad', 'railways', 'rail', 'transportation',
  'railroad ops', 'railroad dispatch',
]);

function isRailroadEntry(e) {
  const tag  = (e.tag  || '').toLowerCase();
  const desc = (e.description || '').toLowerCase();
  return RAILROAD_TAGS.has(tag) || tag.includes('railroad') || tag.includes('rail')
    || desc.includes('railroad') || desc.includes('railway');
}

function openRRImport() {
  document.getElementById('rr-modal').classList.remove('hidden');
  document.getElementById('rr-url-input').focus();
}

function closeRRModal(event) {
  if (!event || event.target === document.getElementById('rr-modal')) {
    document.getElementById('rr-modal').classList.add('hidden');
    _rrReset();
  }
}

function _rrReset() {
  _rrAllEntries = [];
  _rrVisible = [];
  document.getElementById('rr-results').classList.add('hidden');
  document.getElementById('rr-filter-row').classList.add('hidden');
  document.getElementById('rr-status').textContent = '';
  document.getElementById('rr-tbody').innerHTML = '';
  document.getElementById('rr-url-input').value = '';
}

async function rrPreview() {
  const url = document.getElementById('rr-url-input').value.trim();
  if (!url) return;
  const btn = document.getElementById('rr-preview-btn');
  const status = document.getElementById('rr-status');

  btn.disabled = true;
  btn.textContent = 'Loading…';
  status.textContent = 'Fetching RadioReference page…';
  document.getElementById('rr-results').classList.add('hidden');
  document.getElementById('rr-filter-row').classList.add('hidden');

  try {
    const res = await fetch(`${API}/api/import/rr/preview`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    });
    const data = await res.json();
    if (!res.ok) { status.textContent = `Error: ${data.error}`; return; }

    _rrAllEntries = data.entries || [];

    if (_rrAllEntries.length === 0) {
      status.textContent = 'No frequency entries found on that page.';
      return;
    }

    // Populate tag filter
    const tagSel = document.getElementById('rr-tag-filter');
    tagSel.innerHTML = '<option value="">All tags</option>';
    (data.tags || []).forEach(t => {
      const opt = document.createElement('option');
      opt.value = t; opt.textContent = t;
      tagSel.appendChild(opt);
    });

    // If there are railroad entries, default to showing them
    if (data.railroad_count > 0) {
      document.getElementById('rr-rr-only').checked = true;
    }

    document.getElementById('rr-filter-row').classList.remove('hidden');
    document.getElementById('rr-results').classList.remove('hidden');
    status.textContent = '';
    rrApplyFilter();

  } catch (e) {
    status.textContent = 'Network error — check Pi has internet access.';
  } finally {
    btn.disabled = false;
    btn.textContent = 'Preview';
  }
}

function rrApplyFilter() {
  const tagFilter  = document.getElementById('rr-tag-filter').value.toLowerCase();
  const rrOnly     = document.getElementById('rr-rr-only').checked;

  _rrVisible = _rrAllEntries.filter(e => {
    if (tagFilter && (e.tag || '').toLowerCase() !== tagFilter) return false;
    if (rrOnly && !isRailroadEntry(e)) return false;
    return true;
  });

  rrRenderTable(_rrVisible);
  document.getElementById('rr-count').textContent = `${_rrVisible.length} shown`;
  _rrUpdateSelectedCount();
}

function rrRenderTable(entries) {
  const tbody = document.getElementById('rr-tbody');
  tbody.innerHTML = '';
  entries.forEach((e, i) => {
    const isRR = isRailroadEntry(e);
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td><input type="checkbox" class="rr-row-cb" data-idx="${i}" checked></td>
      <td>${e.freq_mhz.toFixed(4)}</td>
      <td>${escHtml(e.name)}</td>
      <td>${escHtml(e.mode)}</td>
      <td><span class="rr-tag-pill ${isRR ? 'railroad' : ''}">${escHtml(e.tag)}</span></td>
    `;
    tr.querySelector('.rr-row-cb').addEventListener('change', _rrUpdateSelectedCount);
    tbody.appendChild(tr);
  });
  document.getElementById('rr-select-all').checked = true;
  _rrUpdateSelectedCount();
}

function rrToggleAll(checked) {
  document.querySelectorAll('.rr-row-cb').forEach(cb => { cb.checked = checked; });
  _rrUpdateSelectedCount();
}

function _rrUpdateSelectedCount() {
  const n = document.querySelectorAll('.rr-row-cb:checked').length;
  document.getElementById('rr-selected-count').textContent = `${n} selected`;
  document.getElementById('rr-import-btn').disabled = n === 0;
}

async function rrImportSelected() {
  const checkboxes = document.querySelectorAll('.rr-row-cb:checked');
  const selected = Array.from(checkboxes).map(cb => _rrVisible[parseInt(cb.dataset.idx)]);
  if (!selected.length) return;

  const btn = document.getElementById('rr-import-btn');
  btn.disabled = true;
  btn.textContent = 'Importing…';

  try {
    const res = await fetch(`${API}/api/import/rr/confirm`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ entries: selected }),
    });
    const data = await res.json();
    if (!res.ok) {
      document.getElementById('rr-status').textContent = `Error: ${data.error}`;
      return;
    }
    document.getElementById('rr-status').textContent =
      `✓ Added ${data.added} frequencies${data.skipped ? `, ${data.skipped} already existed` : ''}.`;
    loadFrequencies();
    setTimeout(() => closeRRModal(), 2000);
  } catch (e) {
    document.getElementById('rr-status').textContent = 'Import failed.';
  } finally {
    btn.disabled = false;
    btn.textContent = 'Import Selected';
  }
}

// ── Utility ─────────────────────────────────────────────────────────────

function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ── Init ────────────────────────────────────────────────────────────────

function init() {
  loadFrequencies();
  loadRecordings();
  pollStatus();
  // Poll status every 500ms
  pollInterval = setInterval(pollStatus, 500);
  // Refresh recordings every 10s
  setInterval(loadRecordings, 10000);
}

document.addEventListener('DOMContentLoaded', init);
