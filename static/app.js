let currentFormat = 'video';
let cardData = [];
let pollIntervals = {};
let playlistIndices = [];

function setFormat(btn) {
  document.querySelectorAll('.pill').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  currentFormat = btn.dataset.format;
  cardData.forEach((card, idx) => {
    if (card) renderCard(idx);
  });
}

function parseUrls(text) {
  return [...new Set(text.split(/[\s,]+/).map(u => u.trim()).filter(u => u.startsWith('http')))];
}

function fmtDur(s) {
  if (!s) return '';
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${sec.toString().padStart(2, '0')}`;
}

function esc(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

function friendlyError(err) {
  if (!err) return 'Download failed';
  const lowerError = err.toLowerCase();
  const rules = [
    ['unsupported url', 'This URL is not supported'],
    ['video unavailable', 'Video is unavailable or private'],
    ['private video', 'This video is private'],
    ['http error 403', 'Access denied by the platform'],
    ['http error 404', 'Video not found'],
    ['copyright', 'Video blocked due to copyright'],
    ['geo', 'Video not available in your region'],
    ['timed out', 'Request timed out — try again'],
    ['network', 'Network error — check your connection'],
    ['cancelled', 'Download cancelled by user'],
  ];
  const matchedRule = rules.find(([searchText]) => lowerError.includes(searchText));
  if (matchedRule) return matchedRule[1];
  return err.length > 80 ? err.slice(0, 80) + '...' : err;
}

function needsYtDlpUpdate(err) {
  return err?.toLowerCase().includes('http error 403');
}

function fmtSize(bytes) {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

// Load existing jobs and files on page load
async function loadExistingJobs() {
  try {
    const [jobsRes, filesRes] = await Promise.all([
      fetch('/api/jobs'),
      fetch('/api/files')
    ]);
    
    const jobs = await jobsRes.json();
    const files = await filesRes.json();
    
    // Display files in downloads section
    renderDownloads(files);
    
    // Restore job cards for active or recently completed jobs
    const container = document.getElementById('cards');
    
    jobs.forEach(job => {
      if (job.status === 'queued' || job.status === 'downloading' || job.status === 'done' || job.status === 'error') {
        const idx = cardData.length;
        cardData.push({
          jobId: job.job_id,
          url: job.url,
          title: job.title || 'Untitled',
          status: job.status,
          position: job.position || null,
          thumbnail: '',
          uploader: '',
          duration: null,
          progress: job.progress || 0,
          totalSize: job.total_size,
          filename: job.filename,
          error: job.error,
          selectedFormatId: null,
          formats: []
        });
        renderCard(idx);
        
        if (job.status === 'downloading' || job.status === 'queued') {
          pollCard(idx);
        }
      }
    });
  } catch (err) {
    console.error('Failed to load existing jobs:', err);
  }
}

function renderDownloads(files) {
  const section = document.getElementById('downloads-section');
  const list = document.getElementById('downloads-list');
  
  if (!files || files.length === 0) {
    section.style.display = 'none';
    return;
  }
  
  section.style.display = 'block';
  list.innerHTML = files.map(f => `
    <div class="download-item">
      <span class="name" title="${esc(f.name)}">${esc(f.name)}</span>
      <span class="size">${fmtSize(f.size)}</span>
      <a class="download-file" href="${esc(f.download_url)}">Descargar</a>
    </div>
  `).join('');
}

async function refreshDownloads() {
  try {
    const res = await fetch('/api/files');
    const files = await res.json();
    renderDownloads(files);
  } catch (err) {
    console.error('Failed to refresh downloads:', err);
  }
}

document.getElementById('urls').addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); go(); }
});

async function go() {
  const urls = parseUrls(document.getElementById('urls').value);
  if (!urls.length) return;

  const btn = document.getElementById('goBtn');
  const container = document.getElementById('cards');
  btn.disabled = true;
  btn.textContent = 'Loading...';
  
  // Don't clear cards - append new ones
  
  for (let i = 0; i < urls.length; i++) {
    const url = urls[i];
    const existingIdx = cardData.findIndex(card => card?.url === url);
    if (existingIdx !== -1) {
      container.prepend(document.getElementById(`card-${existingIdx}`));
      continue;
    }
    const idx = cardData.length;
    cardData.push({ url, status: 'loading' });
    renderCard(idx);
    container.prepend(document.getElementById(`card-${idx}`));

    try {
      const res = await fetch('/api/info', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url }),
      });
      const data = await res.json();
      if (data.error) {
        cardData[idx] = { ...cardData[idx], status: 'info-error', error: data.error };
      } else if (data.type === 'playlist') {
        document.getElementById(`card-${idx}`)?.remove();
        cardData[idx] = null;
        addPlaylistCards(data, container);
      } else {
        cardData[idx] = {
          ...cardData[idx],
          status: 'ready',
          title: data.title || '',
          thumbnail: data.thumbnail || '',
          duration: data.duration,
          uploader: data.uploader || '',
          formats: data.formats || [],
          selectedFormatId: data.formats?.[0]?.id || null,
        };
      }
    } catch (err) {
      cardData[idx] = { ...cardData[idx], status: 'info-error', error: err.message };
    }
    if (cardData[idx]) renderCard(idx);
  }

  btn.disabled = false;
  btn.textContent = 'Fetch';
}

function addPlaylistCards(data, container) {
  const indices = [];
  (data.entries || []).forEach(entry => {
    const existing = cardData.findIndex(card => card && card.url === entry.url);
    if (existing !== -1) {
      indices.push(existing);
      container.prepend(document.getElementById(`card-${existing}`));
      return;
    }
    const idx = cardData.length;
    cardData.push({
      url: entry.url,
      status: 'ready',
      title: entry.title || '',
      uploader: entry.uploader || '',
      duration: entry.duration,
      thumbnail: '',
      formats: [],
      selectedFormatId: null,
      fromPlaylist: true,
    });
    renderCard(idx);
    container.prepend(document.getElementById(`card-${idx}`));
    indices.push(idx);
  });

  playlistIndices = indices;
  document.getElementById('playlist-title').textContent = data.title || 'Lista';
  const total = data.entry_count ?? (data.entries || []).length;
  const shown = (data.entries || []).length;
  document.getElementById('playlist-count').textContent = data.truncated
    ? `${shown} de ${total} vídeos (máximo alcanzado)`
    : `${total} vídeos`;
  document.getElementById('playlist-banner').style.display = 'flex';
}

async function downloadPlaylist() {
  const btn = document.getElementById('playlist-dl-all');
  btn.disabled = true;
  try {
    for (const idx of playlistIndices) {
      const card = cardData[idx];
      if (card && card.status === 'ready') {
        await dlCard(idx);
      }
    }
  } finally {
    btn.disabled = false;
  }
}

function renderCard(idx) {
  const c = cardData[idx];
  let el = document.getElementById(`card-${idx}`);
  if (!el) {
    el = document.createElement('div');
    el.id = `card-${idx}`;
    el.className = 'card';
    document.getElementById('cards').appendChild(el);
  }

  // Loading skeleton
  if (c.status === 'loading') {
    el.className = 'card';
    el.innerHTML = `
      <div class="card-thumb loading"></div>
      <div class="card-body">
        <div class="skeleton-line medium"></div>
        <div class="skeleton-line short"></div>
      </div>
    `;
    return;
  }

  // Error state
  if (c.status === 'info-error') {
    el.className = 'card card-error';
    el.innerHTML = `
      <div class="card-thumb">
        <div class="card-error-icon">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>
        </div>
      </div>
      <div class="card-body">
        <div class="card-title" style="color:var(--error)">Could not fetch video</div>
        <div class="card-error-msg">${esc(friendlyError(c.error || ''))}</div>
        <div class="card-error-url">${esc(c.url)}</div>
        <div class="card-actions"><button class="card-dl-btn remove" onclick="removeCard(${idx})">Eliminar</button></div>
      </div>
    `;
    return;
  }

  // Ready / downloading / done / error states
  el.className = 'card';
  const isAudio = currentFormat === 'audio';

  let thumbHtml;
  if (isAudio) {
    thumbHtml = `<div class="no-thumb" style="color:var(--accent)"><svg viewBox="0 0 24 24" fill="currentColor" stroke="none"><polygon points="5 3 19 12 5 21 5 3"/></svg></div>`;
  } else if (c.thumbnail) {
    thumbHtml = `<img src="${c.thumbnail}" alt="">`;
  } else {
    thumbHtml = `<div class="no-thumb"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="2" width="20" height="20" rx="2"/><circle cx="8" cy="8" r="1.5"/><path d="m21 15-5-5L5 21"/></svg></div>`;
  }

  let qualityChips = '';
  if (!isAudio && c.formats && c.formats.length > 1) {
    qualityChips = c.formats.map(f =>
      `<button class="q-chip${f.id === c.selectedFormatId ? ' active' : ''}" onclick="pickFormat(${idx}, '${f.id}')">${f.label}</button>`
    ).join('');
  }

  let actionHtml = '';
  const removeButton = c.status !== 'downloading'
    ? `<button class="card-dl-btn remove" onclick="removeCard(${idx})">Eliminar</button>`
    : '';
  if (c.status === 'ready') {
    actionHtml = `<button class="card-dl-btn" onclick="dlCard(${idx})">Download</button>${qualityChips}`;
  } else if (c.status === 'queued') {
    const pos = c.position ? ` · #${c.position}` : '';
    actionHtml = `
      <span class="card-status queued"><span class="dot"></span> En cola${pos}</span>
      <button class="card-dl-btn remove" onclick="removeCard(${idx})">Quitar de la cola</button>`;
  } else if (c.status === 'downloading') {
    const progress = c.progress || 0;
    const sizeInfo = c.totalSize ? ` ${c.totalSize}` : '';
    actionHtml = `
      <div style="flex:1;min-width:0;">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;min-width:0;">
          <span class="card-status downloading" title="Downloading${esc(sizeInfo)}"><span class="spin"></span> Downloading${sizeInfo}</span>
          <button class="card-dl-btn cancel" onclick="cancelCard(${idx})">Cancel</button>
        </div>
        <div class="progress-bar"><div class="progress-fill" style="width:${progress}%"></div></div>
        <div style="font-size:0.65rem;color:var(--muted);text-align:right;">${progress.toFixed(1)}%</div>
      </div>
    `;
  } else if (c.status === 'done') {
    actionHtml = `
      <span class="card-status done" title="${esc(c.filename || '')}">Saved: ${esc(c.filename || '')}</span>${removeButton}
    `;
  } else if (c.status === 'error') {
    const updateHint = needsYtDlpUpdate(c.error)
      ? '<span class="update-hint">YouTube blocked this version. Run <code>bash ./actualizar-reclip.sh</code> on the server, then retry.</span>'
      : '';
    actionHtml = `<button class="card-dl-btn" onclick="dlCard(${idx})">Retry</button>
      <span class="card-status error" title="${esc(c.error || 'Download failed')}">${esc(friendlyError(c.error || 'Download failed'))}</span>${updateHint}${removeButton}`;
  }

  el.innerHTML = `
    <div class="card-thumb">${thumbHtml}</div>
    <div class="card-body">
      <div class="card-title" title="${esc(c.title || 'Untitled')}">${esc(c.title || 'Untitled')}</div>
      <div class="card-meta" title="${esc(c.uploader)}${c.duration ? ' · ' + fmtDur(c.duration) : ''}">${esc(c.uploader)}${c.duration ? ' · ' + fmtDur(c.duration) : ''}</div>
      <div class="card-actions">${actionHtml}</div>
    </div>
  `;
}

function pickFormat(idx, formatId) {
  cardData[idx].selectedFormatId = formatId;
  renderCard(idx);
}

async function removeCard(idx) {
  const c = cardData[idx];
  if (!c) return;

  try {
    if (c.jobId) {
      if (pollIntervals[idx]) {
        clearInterval(pollIntervals[idx]);
        delete pollIntervals[idx];
      }
      const res = await fetch(`/api/jobs/${c.jobId}`, { method: 'DELETE' });
      if (!res.ok && res.status !== 404) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error || 'Could not remove download history');
      }
    }
    document.getElementById(`card-${idx}`)?.remove();
    cardData[idx] = null;
  } catch (err) {
    console.error('Failed to remove download history:', err);
    if (c.jobId && pollIntervals[idx] === undefined) {
      pollCard(idx);
    }
  }
}

async function dlCard(idx) {
  const c = cardData[idx];
  if (!c) return;
  c.status = 'queued';
  c.progress = 0;
  c.position = null;
  c.error = null;
  renderCard(idx);

  try {
    const res = await fetch('/api/download', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        url: c.url,
        format: currentFormat,
        format_id: c.selectedFormatId,
        title: c.title || '',
      }),
    });
    const data = await res.json();
    if (!res.ok || data.error) {
      throw new Error(data.error || 'Could not start download');
    }
    c.jobId = data.job_id;
    c.status = data.status || 'queued';
    c.position = data.position || null;
    renderCard(idx);
    pollCard(idx);
  } catch (err) {
    c.status = 'error';
    c.error = err.message;
    renderCard(idx);
  }
}

function pollCard(idx) {
  const c = cardData[idx];
  if (!c || !c.jobId) return;
  if (pollIntervals[idx]) clearInterval(pollIntervals[idx]);

  const tick = async () => {
    try {
      const res = await fetch(`/api/status/${c.jobId}`);
      if (res.status === 404) {
        clearInterval(pollIntervals[idx]);
        delete pollIntervals[idx];
        document.getElementById(`card-${idx}`)?.remove();
        cardData[idx] = null;
        return;
      }
      const data = await res.json();

      const prevStatus = c.status;
      const prevPosition = c.position;
      if (data.status) c.status = data.status;
      if (data.progress !== undefined) c.progress = data.progress;
      if (data.total_size) c.totalSize = data.total_size;
      c.position = data.position || null;

      if (c.status === 'downloading') {
        renderCard(idx);
      } else if (c.status === 'queued' && (prevStatus !== 'queued' || prevPosition !== c.position)) {
        renderCard(idx);
      }

      if (data.status === 'done') {
        clearInterval(pollIntervals[idx]);
        delete pollIntervals[idx];
        c.filename = data.filename;
        renderCard(idx);
        refreshDownloads();
      } else if (data.status === 'error') {
        clearInterval(pollIntervals[idx]);
        delete pollIntervals[idx];
        c.error = data.error;
        renderCard(idx);
      } else if (data.status === 'cancelled') {
        clearInterval(pollIntervals[idx]);
        delete pollIntervals[idx];
        c.status = 'error';
        c.error = data.error || 'Download cancelled';
        renderCard(idx);
      }
    } catch {
      clearInterval(pollIntervals[idx]);
      delete pollIntervals[idx];
      c.status = 'error';
      c.error = 'Lost connection to server';
      renderCard(idx);
    }
  };

  tick();
  pollIntervals[idx] = setInterval(tick, 1000);
}

async function cancelCard(idx) {
  const c = cardData[idx];
  if (!c.jobId) return;
  
  try {
    await fetch(`/api/cancel/${c.jobId}`, { method: 'POST' });
  } catch (err) {
    console.error('Failed to cancel:', err);
  }
  
  if (pollIntervals[idx]) {
    clearInterval(pollIntervals[idx]);
    delete pollIntervals[idx];
  }
  c.status = 'error';
  c.error = 'Download cancelled';
  renderCard(idx);
}

// Load existing jobs when page loads
loadExistingJobs();
