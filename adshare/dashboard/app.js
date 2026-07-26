/* adshare service dashboard — polling + state machine.
 *
 * No build chain. Polls /status every 7 s while the tab is visible,
 * computes per-service smoothed state (3 consecutive misses → down,
 * 4 consecutive hits → ok, else degraded), and renders cards,
 * counters, sparkline, freshness table. Sends a Web Notification on
 * ok→degraded and degraded→down transitions.
 */

'use strict';

const POLL_INTERVAL_MS = 7000;
const RING_SIZE = 10;
const MISS_THRESHOLD = 3;   // consecutive misses → down
const HIT_THRESHOLD = 4;    // consecutive hits → ok

const SERVICE_ORDER = [
  'adshare-api',
  'amazingdata-realtime',
  'amazingdata-batch',
  'redis',
];

const STATUS_LABELS = {
  initialising: '初始化中',
  ok: '正常',
  degraded: '降级',
  down: '故障',
};

const PERIOD_LABELS = { day: '日线', week: '周线', month: '月线' };

const COUNTER_KEYS = [
  ['total_received', '已接收'],
  ['saved_to_redis', '已保存'],
  ['published', '已发布'],
  ['failed', '失败'],
];

// ---------------------------------------------------------------------
// State
// ---------------------------------------------------------------------

const state = {
  apiKey: localStorage.getItem('adshare_api_key') || '',
  pollsDone: 0,
  rings: Object.fromEntries(SERVICE_ORDER.map((n) => [n, []])),
  smoothed: Object.fromEntries(
    SERVICE_ORDER.map((n) => [n, { status: 'initialising', prev: null }]),
  ),
  lastPayload: null,
  pollTimer: null,
  audioEnabled: false,
};

// ---------------------------------------------------------------------
// DOM refs
// ---------------------------------------------------------------------

const els = {
  apiKey: document.getElementById('apiKey'),
  testBtn: document.getElementById('testBtn'),
  alertsBtn: document.getElementById('alertsBtn'),
  debugToggle: document.getElementById('debugToggle'),
  debugPane: document.getElementById('debugPane'),
  debugPre: document.getElementById('debugPre'),
  banner: document.getElementById('banner'),
  cards: document.getElementById('cards'),
  counters: document.getElementById('counters'),
  sparkLine: document.getElementById('sparkLine'),
  sparkMeta: document.getElementById('sparkMeta'),
  freshnessBody: document.getElementById('freshnessBody'),
  clock: document.getElementById('clock'),
};

// ---------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------

function showBanner(msg) {
  els.banner.textContent = msg;
  els.banner.style.display = msg ? 'block' : 'none';
}

function clearBanner() { showBanner(''); }

function fmtTs(epochSec) {
  if (!epochSec || typeof epochSec !== 'number') return '—';
  return new Date(epochSec * 1000).toLocaleTimeString();
}

function fmtIso(iso) {
  if (!iso) return '—';
  // ISO like "2026-07-26T11:24:05+08:00" — show as YYYY-MM-DD HH:MM:SS
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

function pushRing(name, hit) {
  const ring = state.rings[name];
  ring.push(hit ? 1 : 0);
  if (ring.length > RING_SIZE) ring.shift();
}

function smoothedStatus(name) {
  if (state.pollsDone < 2) return 'initialising';
  const ring = state.rings[name];
  if (!ring.length) return 'down';
  const last = ring[ring.length - 1];
  if (last === 0) {
    // current poll failed — count back through misses
    let misses = 0;
    for (let i = ring.length - 1; i >= 0 && ring[i] === 0; i--) misses++;
    if (misses >= MISS_THRESHOLD) return 'down';
  } else {
    // current poll succeeded — count back through hits
    let hits = 0;
    for (let i = ring.length - 1; i >= 0 && ring[i] === 1; i--) hits++;
    if (hits >= HIT_THRESHOLD) return 'ok';
  }
  return 'degraded';
}

// ---------------------------------------------------------------------
// Polling
// ---------------------------------------------------------------------

async function fetchStatus() {
  const ctrl = new AbortController();
  const timeout = setTimeout(() => ctrl.abort(), 10000);
  try {
    const resp = await fetch('/status', {
      headers: { 'X-API-Key': state.apiKey },
      signal: ctrl.signal,
      cache: 'no-store',
    });
    return resp;
  } finally {
    clearTimeout(timeout);
  }
}

async function poll() {
  let resp;
  let payload = null;
  let fetchError = null;
  try {
    resp = await fetchStatus();
  } catch (e) {
    fetchError = e;
  }

  if (fetchError || !resp || !resp.ok) {
    // API failed this round — mark adshare-api + redis as failing.
    pushRing('adshare-api', 0);
    pushRing('redis', 0);
    pushRing('amazingdata-realtime', 0);
    pushRing('amazingdata-batch', 0);
    state.pollsDone++;
    updateServiceStatuses();
    renderCards();
    showBanner(
      fetchError
        ? `网络错误：${fetchError.message || fetchError.name || '请求失败'}`
        : `API 返回 ${resp ? resp.status : '无响应'}`,
    );
    return;
  }

  try {
    payload = await resp.json();
  } catch (e) {
    showBanner('API 返回的不是 JSON');
    return;
  }

  clearBanner();
  state.pollsDone++;
  state.lastPayload = payload;

  // Per-service hit/miss
  for (const svc of payload.services) {
    const hit = svc.alive ? 1 : 0;
    pushRing(svc.name, hit);
  }
  updateServiceStatuses();
  renderCards();
  renderRealtime(payload.realtime_stats);
  renderFreshness(payload.data_freshness);
  renderDebug();
}

// Compute smoothed status from current rings; record transitions.
function updateServiceStatuses() {
  for (const name of SERVICE_ORDER) {
    const newStatus = smoothedStatus(name);
    const prev = state.smoothed[name];
    if (newStatus !== prev.status && prev.status !== 'initialising') {
      // Skip first transition out of initialising — too noisy.
      maybeNotify(name, prev.status, newStatus);
    }
    prev.prev = prev.status;
    prev.status = newStatus;
  }
}

// ---------------------------------------------------------------------
// Notifications
// ---------------------------------------------------------------------

function audioBeep() {
  if (!state.audioEnabled) return;
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.frequency.value = 880;
    osc.connect(gain);
    gain.connect(ctx.destination);
    gain.gain.setValueAtTime(0.0001, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.15, ctx.currentTime + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.25);
    osc.start();
    osc.stop(ctx.currentTime + 0.3);
  } catch (e) {
    /* AudioContext blocked — silent fallback. */
  }
}

function maybeNotify(name, prev, curr) {
  const severity = { ok: 0, degraded: 1, down: 2 };
  if (severity[curr] > severity[prev]) {
    audioBeep();
    if (
      window.Notification &&
      Notification.permission === 'granted'
    ) {
      try {
        new Notification(`adshare：${name} → ${STATUS_LABELS[curr] || curr}`, {
          body: `状态从“${STATUS_LABELS[prev] || prev}”变为“${STATUS_LABELS[curr] || curr}”`,
          tag: `adshare-${name}`,
        });
      } catch (e) {
        /* Some browsers throw on Notification in insecure contexts. */
      }
    }
  }
}

els.alertsBtn.addEventListener('click', async () => {
  if (!window.Notification) {
    els.alertsBtn.textContent = '浏览器不支持通知';
    els.alertsBtn.disabled = true;
    return;
  }
  const perm = await Notification.requestPermission();
  if (perm === 'granted') {
    state.audioEnabled = true;
    els.alertsBtn.textContent = '告警已启用';
    els.alertsBtn.disabled = true;
  } else {
    els.alertsBtn.textContent = '通知权限被拒绝';
  }
});

// ---------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------

function renderCards() {
  const services = (state.lastPayload?.services || []).reduce(
    (m, s) => ((m[s.name] = s), m),
    {},
  );
  els.cards.innerHTML = '';
  for (const name of SERVICE_ORDER) {
    const svc = services[name] || {
      name,
      alive: false,
      age_sec: null,
      last_seen_at: null,
    };
    const card = document.createElement('div');
    card.className = 'card';
    const smoothed = state.smoothed[name].status;
    const displayStatus = STATUS_LABELS[smoothed] || smoothed;
    card.innerHTML = `
      <div class="card-name">${name}</div>
      <div class="card-status">
        <span class="pill ${smoothed}">${displayStatus}</span>
      </div>
      <div class="meta">
        <div class="kv"><span>年龄</span><span>${ageText(svc)}</span></div>
        <div class="kv"><span>最后心跳</span><span>${fmtTs(svc.last_seen_at)}</span></div>
        ${detailRow(svc)}
      </div>
    `;
    els.cards.appendChild(card);
  }
}

function ageText(svc) {
  if (svc.age_sec == null) return '—';
  return `${svc.age_sec.toFixed(1)} 秒`;
}

function detailRow(svc) {
  if (svc.name !== 'amazingdata-realtime' || !svc.payload) return '';
  const age = svc.payload.tick_age_sec;
  if (age == null) return '';
  return `<div class="kv"><span>行情延迟</span><span>${age.toFixed(1)} 秒</span></div>`;
}

function renderRealtime(stats) {
  const realtimeStats = (stats?.['amazingdata-realtime']?.heartbeat?.stats) || {};
  els.counters.innerHTML = '';
  for (const [key, label] of COUNTER_KEYS) {
    const value = realtimeStats[key];
    const div = document.createElement('div');
    div.className = 'counter';
    div.innerHTML = `
      <div class="counter-label">${label}</div>
      <div class="counter-value">${formatNum(value)}</div>
    `;
    els.counters.appendChild(div);
  }
  renderSparkline(stats?.['amazingdata-realtime']?.history || []);
}

function formatNum(n) {
  if (n == null) return '—';
  return Number(n).toLocaleString();
}

function renderSparkline(history) {
  const counts = history
    .map((h) => Number(h.total_received) || 0)
    .filter((n) => Number.isFinite(n));
  if (counts.length < 2) {
    els.sparkLine.setAttribute('points', '');
    els.sparkMeta.textContent = '正在收集数据…';
    return;
  }
  const max = Math.max(...counts);
  const min = Math.min(...counts);
  const range = max - min || 1;
  const W = 600;
  const H = 64;
  const pad = 4;
  const step = (W - pad * 2) / (counts.length - 1);
  const pts = counts
    .map((v, i) => {
      const x = pad + i * step;
      const y = H - pad - ((v - min) / range) * (H - pad * 2);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');
  els.sparkLine.setAttribute('points', pts);
  els.sparkMeta.textContent =
    `${counts.length} 个样本 · 最小 ${min.toLocaleString()} · ` +
    `最大 ${max.toLocaleString()} · 最新 ${counts[counts.length - 1].toLocaleString()}`;
}

function renderFreshness(freshness) {
  const rows = freshness?.rows || [];
  const periodOrder = ['day', 'week', 'month'];
  els.freshnessBody.innerHTML = '';
  for (const period of periodOrder) {
    const row = rows.find((r) => r.period === period) || { period, missing: true };
    const tr = document.createElement('tr');
    if (row.missing) {
      tr.className = 'missing';
      tr.innerHTML = `
        <td>${PERIOD_LABELS[period]}</td>
        <td colspan="5">暂无数据 — 尚未同步</td>
      `;
    } else {
      const inProgress = row.is_in_progress;
      const completeTag = inProgress
        ? `<span class="pill degraded">同步中</span>`
        : '';
      const runStatus = row.last_run_status || 'unknown';
      const runStatusLabel = runStatus === 'ok' ? '正常' :
        runStatus === 'down' ? '失败' : '未知';
      tr.innerHTML = `
        <td>${PERIOD_LABELS[period]}</td>
        <td>${row.latest_trade_date || '—'}</td>
        <td>${row.latest_complete_date || '—'} ${completeTag}</td>
        <td class="num">${formatNum(row.code_count)}</td>
        <td>${fmtIso(row.last_run_at)}</td>
        <td>
          <span class="pill ${runStatus === 'ok' ? 'ok' : 'down'}">
            ${runStatusLabel}
          </span>
        </td>
      `;
    }
    els.freshnessBody.appendChild(tr);
  }
}

function renderDebug() {
  if (els.debugPane.classList.contains('hidden')) return;
  els.debugPre.textContent = JSON.stringify(state.lastPayload, null, 2);
}

// ---------------------------------------------------------------------
// Controls
// ---------------------------------------------------------------------

els.apiKey.value = state.apiKey ? '•'.repeat(8) : '';

els.apiKey.addEventListener('focus', () => {
  els.apiKey.value = state.apiKey;
  els.apiKey.type = 'text';
});
els.apiKey.addEventListener('input', () => {
  state.apiKey = els.apiKey.value.trim();
});
els.apiKey.addEventListener('blur', () => {
  state.apiKey = els.apiKey.value.trim();
  localStorage.setItem('adshare_api_key', state.apiKey);
  els.apiKey.value = state.apiKey ? '•'.repeat(8) : '';
  els.apiKey.type = 'password';
  // Auto-start polling once a key is provided so users don't have
  // to click Test after pasting.
  if (state.apiKey) startPolling();
});

els.testBtn.addEventListener('click', async () => {
  // state.apiKey is current — the `input` event listener keeps it
  // in sync, and blur fires before click so the bullets-display
  // never reaches the request.
  els.testBtn.disabled = true;
  els.testBtn.textContent = '测试中…';
  try {
    const resp = await fetchStatus();
    if (resp.ok) {
      els.testBtn.textContent = '通过 ✓';
      els.alertsBtn.disabled = false;
    } else if (resp.status === 401) {
      els.testBtn.textContent = '密钥错误（401）';
    } else {
      els.testBtn.textContent = `错误 ${resp.status}`;
    }
  } catch (e) {
    els.testBtn.textContent = '网络错误';
  } finally {
    setTimeout(() => {
      els.testBtn.disabled = false;
      els.testBtn.textContent = '测试';
    }, 1500);
  }
});

els.debugToggle.addEventListener('click', () => {
  els.debugPane.classList.toggle('hidden');
  renderDebug();
});

if (window.Notification && Notification.permission === 'granted') {
  state.audioEnabled = true;
  els.alertsBtn.textContent = '告警已启用';
  els.alertsBtn.disabled = true;
}

// ---------------------------------------------------------------------
// Poll loop + visibility + clock
// ---------------------------------------------------------------------

function startPolling() {
  if (state.pollTimer) clearInterval(state.pollTimer);
  poll();
  state.pollTimer = setInterval(poll, POLL_INTERVAL_MS);
}

document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible' && state.apiKey) {
    if (!state.pollTimer) startPolling();
  }
});

setInterval(() => {
  els.clock.textContent = new Date().toLocaleTimeString();
}, 1000);

// Auto-start if we already have a key.
if (state.apiKey) {
  els.alertsBtn.disabled = false;
  startPolling();
}